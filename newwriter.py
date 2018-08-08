import numpy

from write.diskarray import DiskArray
from write.sink import Sink
from write.cursor import Cursor
from write.headkey import HeadKey
from write.header import Header
from write.begin_key import Begin_Key
from write.directoryinfo import DirectoryInfo
from write.streamerkey import StreamerKey
from write.allstreamer import AllStreamers

from write.TObjString.tobjstring import TObjString
from write.TObjString.key import Key as StringKey
from write.TObjString.junkkey import JunkKey
from write.TObjString.streamers import TObjStringStreamers

class NewWriter(object):

    def __init__(self, filename):
        self.file = DiskArray(filename, shape=(0,), dtype=numpy.uint8)
        filename = filename[(filename.rfind("/") + 1):]
        self.bytename = filename.encode("utf-8")

        self.sink = Sink(self.file)
        self.cursor = Cursor(0)

        self.streamers = []
        self.objend = 0
        self.expander = 500
        self.expanderpow = 2

        #Header Bytes
        fCompress = 0  # Constant for now
        self.header = Header(self.bytename, fCompress)
        self.sink.set_header(self.cursor, self.header)

        #Key
        self.cursor = Cursor(self.header.fBEGIN)
        pointcheck = self.cursor.index
        fName = self.bytename
        key = Begin_Key(fName)
        self.sink.set_key(self.cursor, key)
        key.fKeylen = self.cursor.index - pointcheck
        key.fObjlen = key.fNbytes - key.fKeylen
        self.sink.set_key(Cursor(pointcheck), key)

        # Junk
        self.sink.set_strings(self.cursor, fName)

        #DirectoryInfo
        self.directory_pointcheck = self.cursor.index
        fNbytesKeys = 0
        fNbytesName = self.header.fNbytesName
        self.directory = DirectoryInfo(fNbytesKeys, fNbytesName, 0)
        self.sink.set_directoryinfo(self.cursor, self.directory)

        #header.fSeekInfo points to begin of StreamerKey
        self.header.fSeekInfo = self.cursor.index

        #Streamer Key
        pointcheck = self.cursor.index
        key = StreamerKey(self.cursor, 0)
        self.sink.set_key(self.cursor, key)
        key.fKeylen = self.cursor.index - pointcheck
        key.fNbytes = key.fKeylen + key.fObjlen
        self.sink.set_key(Cursor(pointcheck), key)

        self.header.fNbytesInfo = key.fNbytes
        self.sink.set_header(Cursor(0), self.header)

        #Allocate space for streamers
        streamerstart = self.cursor.index
        self.file.resize(self.cursor.index + self.expander)
        streamers = AllStreamers(self.sink, self.cursor, size = 1)
        streamers.write()
        self.streamerend = self.cursor
        self.streamerlimit = self.cursor.index + self.expander

        #Starting after space allocated for streamers
        self.cursor = Cursor(streamerstart + self.expander)

        #directory.fSeekKeys points to Header Key
        self.directory.fSeekKeys = self.cursor.index
        self.sink.set_directoryinfo(Cursor(self.directory_pointcheck), self.directory)

        # Allocate space for keys
        self.keystart = self.cursor.index
        self.file.resize(self.cursor.index + self.expander)
        self.keyend = self.cursor
        self.keylimit = self.keystart + self.expander

        #Head Key
        self.head_key_pointcheck = self.cursor.index
        fNbytes = self.directory.fNbytesKeys
        fSeekKey = self.directory.fSeekKeys
        fName = self.bytename
        self.head_key = HeadKey(fNbytes, fSeekKey, fName)
        self.sink.set_key(self.cursor, self.head_key)
        self.head_key_end = self.cursor.index

        #Numbers of Keys
        self.nkeys = 0
        packer = ">i"
        self.sink.set_numbers(self.cursor, packer, self.nkeys)

        self.keyend = self.cursor

        self.header.fSeekFree = self.cursor.index
        self.header.fEND = self.header.fSeekFree + self.expander
        self.sink.set_header(Cursor(0), self.header)


    def __setitem__(self, keyname, item):

        #item = TObjString("Hello World")

        self.cursor = Cursor(self.header.fEND)

        if type(item) is TObjString:

            #Place TObjString
            pointcheck = self.cursor.index
            junkkey = JunkKey(keyname.encode("utf-8"))
            self.sink.set_key(self.cursor, junkkey)
            junkkey.fKeylen = self.cursor.index - pointcheck
            junkkey.fNbytes = junkkey.fKeylen + junkkey.fObjlen
            self.sink.set_key(Cursor(pointcheck), junkkey)

            if type(item.string) is str:
                item.string = item.string.encode("utf-8")

            self.sink.set_object(self.cursor, item)

            #Place Key
            key = StringKey(keyname.encode("utf-8"), pointcheck)

            #Check for Key re-allocation
            if self.keylimit - self.keyend.index < 200:
                self.file.resize(self.header.fEND + (self.expander ** self.expanderpow))
                self.file[self.header.fEND:self.header.fEND + self.expander] = self.file[self.directory.fSeekKeys:self.directory.fSeekKeys + self.expander]
                self.keyend = Cursor(self.header.fEND + self.keyend.index - self.directory.fSeekKeys)
                self.directory.fSeekKeys = self.header.fEND
                self.keylimit = self.header.fEND + (self.expander ** self.expanderpow)
                self.header.fEND = self.keylimit
                self.header.fSeekFree = self.keylimit
                self.sink.set_directoryinfo(Cursor(self.directory_pointcheck), self.directory)

            pointcheck = self.keyend.index
            self.sink.set_key(self.keyend, key)
            key.fKeylen = self.keyend.index - pointcheck
            key.fNbytes = key.fKeylen + key.fObjlen
            self.sink.set_key(Cursor(pointcheck), key)

            #Place Streamers
            if "TObjString" not in self.streamers:
                self.streamers.append("TObjString")

                tobjstring = TObjStringStreamers(self.sink, self.streamerend)

                # Check for streamer reallocation
                if self.streamerlimit - self.streamerend.index < 500:
                    self.file[self.header.fEND:self.header.fEND + self.expander] = self.file[self.header.fSeekInfo:self.header.fSeekInfo + self.expander]
                    self.streamerend = Cursor(self.header.fEND + self.streamerend.index - self.header.fSeekInfo)
                    self.header.fSeekInfo = self.header.fEND
                    self.streamerlimit = self.header.fEND + (self.expander ** self.expanderpow)
                    self.header.fEND = self.streamerlimit
                    self.header.fSeekFree = self.streamerlimit
                    tobjstring = TObjStringStreamers(self.sink, self.streamerend)

                tobjstring.write()

        #Update Number of Keys
        self.nkeys += 1
        packer = ">i"
        self.sink.set_numbers(Cursor(self.head_key_end), packer, self.nkeys)

        #Update DirectoryInfo
        self.directory.fNbytesKeys = self.header.fEND - self.keyend.index
        self.sink.set_directoryinfo(Cursor(self.directory_pointcheck), self.directory)

        #Update Head Key
        self.head_key.fNbytes = self.directory.fNbytesKeys
        self.head_key.fKeylen = self.head_key_end - self.head_key_pointcheck
        self.head_key.fObjlen = self.head_key.fNbytes - self.head_key.fKeylen
        self.sink.set_key(Cursor(self.head_key_pointcheck), self.head_key)

        #Updating header bytes
        if self.cursor.index > self.header.fEND:
            self.header.fSeekFree = self.cursor.index
            self.header.fEND = self.cursor.index

        self.sink.set_header(Cursor(0), self.header)

        self.file.flush()
