#!/usr/bin/env python

# Copyright 2017 DIANA-HEP
# 
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# 
#     http://www.apache.org/licenses/LICENSE-2.0
# 
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import sys
import glob
import json
import os.path
from collections import namedtuple
from collections import OrderedDict
try:
    from urlparse import urlparse
except ImportError:
    from urllib.parse import urlparse

import numpy

import uproot
import uproot.tree

class BasketData(object):
    """Holds some information about baskets for making a decision about where to stop growing a partition.

        * `path` local name or remote URL to the file.
        * `branchname` name of the branch to which this basket belongs.
        * `dtype` Numpy array type that this branch will be read out into; note that `dtype.itemsize` gives the item size in bytes.
        * `itemdims` tuple of fixed-width dimensions for the array (not counted in `dtype.itemsize`).
        * `entrystart` first entry number in this basket.
        * `entryend` last entry number in this basket plus one. (`numentries` is `entryend - entrystart`.)
        * `numbytes` size of this basket in bytes.
    """
    def __init__(self, path, branchname, dtype, itemdims, entrystart, entryend, numbytes):
        self.path = path
        self.branchname = branchname
        self.dtype = dtype
        self.itemdims = itemdims
        self.entrystart = entrystart
        self.entryend = entryend
        self.numbytes = numbytes

    @property
    def numentries(self):
        return self.entryend - self.entrystart

    def __repr__(self):
        return "BasketData({0}, {1}, {2}, {3}, {4}, {5}, {6})".format(repr(self.path), repr(self.branchname), self.dtype, self.itemdims, self.entrystart, self.entryend, self.numbytes)

class Range(object):
    """Represents an entry range in a file; part of a partition.

        * `path` local name or remote URL to the file.
        * `entrystart` the first entry in this range.
        * `entryend` the last entry in this range plus one. (`numentries` is `entryend - entrystart`.)
    """
    def __init__(self, path, entrystart, entryend):
        self.path = path
        self.entrystart = entrystart
        self.entryend = entryend

    @property
    def numentries(self):
        return self.entryend - self.entrystart

    def __repr__(self):
        return "Range({0}, {1}, {2})".format(repr(self.path), self.entrystart, self.entryend)

    def toJson(self):
        return {"path": self.path, "entrystart": self.entrystart, "entryend": self.entryend}

    @staticmethod
    def fromJson(obj):
        return Range(obj["path"], obj["entrystart"], obj["entryend"])

class Partition(object):
    """Represents a section of data (possibly crossing file boundaries) to be loaded as contiguous arrays.

        * `index` enumerates this partition; must be contiguous from zero (inclusive) to the PartitionSet's `numpartitions` (exclusive).
        * `ranges` entry ranges within separate files (a partition that doesn't cross file boundaries has exactly one range).
        * `numentries` is the number of entries (calculated from ranges).
    """
    def __init__(self, index, *ranges):
        self.index = index
        self.ranges = ranges

    @property
    def numentries(self):
        return sum(x.numentries for x in self.ranges)

    def __repr__(self):
        return "Partition({0}, {1})".format(self.index, ", ".join(map(repr, self.ranges)))

    def toJson(self):
        return {"index": self.index, "ranges": [x.toJson() for x in self.ranges]}

    @staticmethod
    def fromJson(obj):
        return Partition(obj["index"], *[Range.fromJson(x) for x in obj["ranges"]])

class PartitionSet(object):
    """Represents a way to partition a set of files into 

        * `treepath` is where to find the TTree in each ROOT file.
        * `branchdtypes` is a *dict* from branch name to Numpy array `dtype`.
        * `branchcounters` is a dict from branch name to counter branch name for those branches that have variable width per entry.
        * `numpartitions` is the number of partitions as a cross-check on `len(partitions)`, as well as a quick way to get this information in JSON form.
        * `numentries` is the number of entries as a cross-check on `sum(x.numentries for x in partitions)`, as well as a quick way to get this information in JSON form.
        * `partitions` is a list of Partition objects.

    Use the `PartitionSet.fill` method to create a `PartitionSet` from a set of ROOT files and configurable constraints.

    Use the `iterator` function (in this module) to iterate over data described by a `PartitionSet`.
    """
    def __init__(self, treepath, branchdtypes, branchcounters, numpartitions, numentries, *partitions):
        if not isinstance(branchdtypes, dict):
            raise TypeError("branchdtypes must be a dict for PartitionSet constructor")
        assert numpartitions == len(partitions)
        assert [x.index for x in partitions] == list(range(numpartitions))
        assert numentries == sum(x.numentries for x in partitions)

        lastpath = None
        for partition in partitions:
            for filerange in partition.ranges:
                if lastpath != filerange.path:
                    assert filerange.entrystart == 0
                else:
                    assert filerange.entrystart == last
                lastpath = filerange.path
                last = filerange.entryend

        self.treepath = treepath
        self.branchdtypes = branchdtypes
        self.branchcounters = branchcounters
        self.numpartitions = numpartitions
        self.numentries = numentries
        self.partitions = partitions

    def __repr__(self):
        return "<PartitionSet {0}>".format(repr(self.treepath))

    def toJson(self):
        return {"treepath": self.treepath,
                "branchdtypes": dict((b, str(d)) for b, d in self.branchdtypes.items()),
                "branchcounters": self.branchcounters,
                "numpartitions": self.numpartitions,
                "numentries": self.numentries,
                "partitions": [p.toJson() for p in self.partitions]}

    @staticmethod
    def fromJson(obj):
        return PartitionSet(obj["treepath"],
                            dict((b, numpy.dtype(d)) for b, d in obj["branchdtypes"]),
                            obj["branchcounters"],
                            obj["numpartitions"],
                            obj["numentries"],
                            [Partition.fromJson(p) for p in obj["partitions"]])

    def toJsonString(self):
        return json.dumps(self.toJson())

    @staticmethod
    def fromJsonString(obj):
        return PartitionSet.fromJson(json.loads(obj))

    @staticmethod
    def fill(path, treepath, branchdtypes=lambda branch: getattr(branch, "dtype", None), by=lambda choices: min(choices, key=lambda x: x.numentries), under=lambda baskets: sum(x.numbytes for x in baskets) < 10*1024**2, memmap=True, debug=False):
        """Iterate over a set of ROOT files (reading only headers) to optimize a partitioning of the data.

        Returns:

            * a `PartitionSet` that can be saved as JSON and used to read data in optimally sized chunks.

        Arguments:

            * `path` *(required)*

              If a single string, the name of the file, possibly a URL for XRootD.
              If an iterable, a set of names or URLs.
              Local files can be glob patterns (`mydata*.root`).
              After expansion, paths will be traversed in *sorted* order. This is to ensure that entry numbers for the same file set have the same meaning from run to run.

            * `treepath` *(required)*

              A string describing the path through TDirectories (using '/' and ';' conventions) to the TTree of interest. Must be the same in all files.

            * `branchdtypes` (same as in `TTree.iterator`)

              If a single string, the string names the only branch to load.
              If an iterable of strings, all of these are loaded (in the specified order).
              If a dict of `{name: dtype}`, load the specified branch names and cast them into a given `dtype` (such as conversion to little endian).
              If a function from branch names to `dtype` or `None`, load the branches into the given `dtypes` and don't load the branches mapped to `None`.

            * `by` criterion by which data are partitioned; this function chooses from a set of options, passed as an argument.

              Default selects the option with the fewest entries.

                  def by(choices):
                      return min(choices, key=lambda x: x.numentries)

            * `under` criterion that stops growth of a partition for one branch; this function should return `True` or `False`, given a list of `BasketData`.

              Default stops growth before the number of bytes exceeds 10 MB.

                  def under(baskets):
                      return sum(x.numbytes for x in baskets) < 10*1024**2

            * `memmap` (same as in `uproot.open`)

              If `True`, load local files as memory maps. If `False`, load normally.
              The advantage of memory maps is that parallel reads only require one file handle, and random access (of which there is a *lot* in ROOT) is more efficient.
              The advantage of normal files is that memory maps sometimes load more data from disk than intended, which might (?) be a performance issue for slow disks.

            * `debug` if `debug` is `True`, this function prints out each `Partition` as it is created.            
        """
        if hasattr(path, "decode"):
            path = path.decode("ascii")

        def explode(x):
            parsed = urlparse(x)
            if parsed.scheme == "file" or parsed.scheme == "":
                return sorted(glob.glob(os.path.expanduser(parsed.netloc + parsed.path)))
            else:
                return [x]

        if (sys.version_info[0] <= 2 and isinstance(path, unicode)) or \
           (sys.version_info[0] > 2 and isinstance(path, str)):
            paths = explode(path)
        else:
            paths = [y for x in path for y in explode(x)]

        trees = {}
        trees[0] = uproot.open(paths[0], memmap=memmap)[treepath]
        toget = dict((b.name, d) for b, d in uproot.tree.TTree._normalizeselection(branchdtypes, trees[0].allbranches))
        counters = dict((countee, counter.branch) for countee, counter in trees[0].counter.items())
        def tree(i):
            if i not in trees:
                trees[i] = uproot.open(paths[i], memmap=memmap)[treepath]

                newtoget = dict((b.name, d) for b, d in uproot.tree.TTree._normalizeselection(branchdtypes, trees[i].allbranches))
                for key in set(toget.keys()).union(set(newtoget.keys())):
                    if key not in newtoget:
                        raise ValueError("branch {0} cannot be found in {1}, but it was in {2}".format(repr(key), repr(paths[i]), repr(paths[i - 1])))
                    if key not in toget:
                        del newtoget[key]
                    elif newtoget[key] != toget[key]:
                        raise ValueError("branch {0} is a {1} in {2}, but it was a {3} in {4}".format(repr(key), newtoget[key], repr(paths[i]), toget[key], repr(paths[i - 1])))

                newcounters = dict((countee, counter.branch) for countee, counter in trees[i].counter.items())
                for key in set(counters.keys()).union(set(newcounters.keys())):
                    if key not in newcounters:
                        raise ValueError("branch {0} doesn't have a counter in {1}, but it was counted by {2} in {3}".format(repr(key), repr(paths[i]), repr(counters[key]), repr(paths[i - 1])))
                    if key not in counters:
                        del newcounters[key]
                    elif newcounters[key] != counters[key]:
                        raise ValueError("branch {0} is counted by {1} in {2}, but it is counted by {3} in {4}".format(repr(key), repr(newcounters[key]), repr(paths[i]), repr(counters[key]), repr(paths[i - 1])))

            return trees[i]

        partitions = []
        partitioni = 0
        while len(partitions) == 0 or partitions[-1].ranges[-1].path != paths[-1] or partitions[-1].ranges[-1].entryend < tree(len(paths) - 1).numentries:
            possiblenext = []
            for branchname, dtype in toget.items():
                # start this branch where the global partitioning process left off
                if len(partitions) == 0:
                    pathi = 0
                    basketi = 0
                    entryi = 0
                    branch = tree(pathi)[branchname]
                else:
                    pathi = partitions[-1].ranges[-1]._pathi
                    entryi = partitions[-1].ranges[-1].entryend
                    branch = tree(pathi)[branchname]
                    for basketi in range(branch.numbaskets):
                        if basketi + 1 == branch.numbaskets or branch.basketstart(basketi + 1) > entryi:
                            break

                # accumulate until the constraint is satisfied
                basketdata = []
                while True:
                    if basketi >= branch.numbaskets:
                        pathi += 1
                        basketi = 0
                        if pathi >= len(paths):
                            break
                        else:
                            basket = tree(pathi)[branchname]

                    basketdata.append(BasketData(paths[pathi],
                                                 branchname,
                                                 dtype,
                                                 branch.itemdims,
                                                 branch.basketstart(basketi),
                                                 branch.basketstart(basketi) + branch.basketentries(basketi),
                                                 branch.basketbytes(basketi)))
                    basketdata[-1]._pathi = pathi

                    for basketdatum in basketdata:
                        assert basketdatum.entrystart != basketdatum.entryend

                    if not under(basketdata):
                        basketdata.pop()
                        break
                    else:
                        basketi += 1

                if len(basketdata) == 0:
                    raise ValueError("branch {0} starting at entry {1} in file {2} cannot satisfy the constraint".format(repr(branchname), entryi, repr(paths[pathi])))

                # create a possible partition
                ranges = []
                for basketdatum in basketdata:
                    if len(ranges) == 0 or ranges[-1]._pathi != basketdatum._pathi:
                        ranges.append(Range(basketdatum.path, basketdatum.entrystart, basketdatum.entryend))
                        ranges[-1]._pathi = basketdatum._pathi
                    else:
                        ranges[-1].entryend = basketdatum.entryend

                if len(partitions) != 0:
                    if partitions[-1].ranges[-1]._pathi == ranges[0]._pathi:
                        ranges[0].entrystart = partitions[-1].ranges[-1].entryend
                    else:
                        ranges[0].entrystart = 0

                possiblenext.append(Partition(partitioni, *filter(lambda r: r.entrystart != r.entryend, ranges)))

            partitions.append(by(possiblenext))
            if debug:
                print(partitions[-1])

            for todrop in set(pathi for pathi in trees if pathi < partitions[-1].ranges[0]._pathi):
                del trees[todrop]

            partitioni += 1

        return PartitionSet(treepath, toget, counters, len(partitions), sum(x.numentries for x in partitions), *partitions)

def iterator(partitionset, memmap=True, executor=None, outputtype=dict):
    """Iterates over a collection of files, yielding arrays for each partition in a given `PartitionSet` (even across the gap between files).

    Arguments:

        * a `PartitionSet` declaring the file and tree paths from which to get data as well as the entries to use as boundaries.

        * `memmap` (same as in `uproot.open`)

          If `True`, load local files as memory maps. If `False`, load normally.
          The advantage of memory maps is that parallel reads only require one file handle, and random access (of which there is a *lot* in ROOT) is more efficient.
          The advantage of normal files is that memory maps sometimes load more data from disk than intended, which might (?) be a performance issue for slow disks.

        * `executor` (same as in `TTree.iterator`)

          A `concurrent.futures.Executor` that would be used to parallelize the basket loading/decompression.
          If `None`, the process is serial.

        * `outputtype` (same as in `TTree.iterator`)

          Constructor for the objects to yield in the iterator. Good choices include `dict`, `tuple`, `namedtuple`, `list`.
    """
    if outputtype == namedtuple:
        outputtype = namedtuple("Arrays", [branch.name.decode("ascii") for branch, dtype, cache in partitionset.branchdtypes])

    treedata = {}
    def complete(nextpartition):
        for filerange in partitionset.partitions[nextpartition].ranges:
            if filerange.path not in treedata or not any(entrystart == filerange.entrystart and entryend == filerange.entryend for entrystart, entryend, arrays in treedata[filerange.path]):
                return False
        return True

    def output(nextpartition):
        arraylists = dict((x, []) for x in partitionset.branchdtypes)
        for filerange in partitionset.partitions[nextpartition].ranges:
            for used, (entrystart, entryend, arrays) in enumerate(treedata[filerange.path]):
                if filerange.entrystart == entrystart and filerange.entryend == entryend:
                    for name, array in arrays.items():
                        arraylists[name].append(array)
                    break
            treedata[filerange.path] = treedata[filerange.path][used + 1:]
            if len(treedata[filerange.path]) == 0:
                del treedata[filerange.path]

        outarrays = {}
        for name, arraylist in arraylists.items():
            if len(arraylist) == 0:
                outarrays[name] = numpy.array([], dtype=partitionset.branchdtypes[name])
            elif len(arraylist) == 1:
                outarrays[name] = arraylist[0]
            else:
                outarrays[name] = numpy.concatenate(arraylist)

        if outputtype == dict or outputtype == OrderedDict:
            return outarrays
        elif issubclass(outputtype, dict):
            return outputtype(outarrays.items())
        elif outputtype == tuple or outputtype == list:
            return outputtype(outarrays.values())
        else:
            return outputtype(*outarrays.values())
        
    oldpath = None
    nextpartition = 0
    for partition in partitionset.partitions:
        for filerange in partition.ranges:
            if oldpath != filerange.path:
                if oldpath is not None:
                    treedata[oldpath] = list(tree.iterator(entries, partitionset.branchdtypes, executor=executor, outputtype=OrderedDict, reportentries=True))
                tree = uproot.open(filerange.path, memmap=memmap)[partitionset.treepath]
                entries = []
            entries.append((filerange.entrystart, filerange.entryend))
            oldpath = filerange.path

        while nextpartition < len(partitionset.partitions):
            if not complete(nextpartition):
                break
            else:
                yield output(nextpartition)
                nextpartition += 1

    if oldpath is not None:
        treedata[oldpath] = list(tree.iterator(entries, partitionset.branchdtypes, executor=executor, outputtype=OrderedDict, reportentries=True))

    while nextpartition < len(partitionset.partitions):
        if not complete(nextpartition):
            break
        else:
            yield output(nextpartition)
            nextpartition += 1
