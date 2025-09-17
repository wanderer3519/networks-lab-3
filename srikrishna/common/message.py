import struct
import time

class UAPMessage:
    MAGIC = 0xC461
    VERSION = 1
    CMD_HELLO, CMD_DATA, CMD_ALIVE, CMD_GOODBYE = range(4)

    HEADER_FORMAT = "!H B B I I I d"
    HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

    def __init__(self, command, seq, session_id, clock, timestamp=None, payload=b""):
        self.command = command
        self.seq = seq
        self.session_id = session_id
        self.clock = clock
        self.timestamp = timestamp or time.time()
        self.payload = payload

    def pack(self):
        header = struct.pack(
            self.HEADER_FORMAT,
            self.MAGIC, self.VERSION, self.command,
            self.seq, self.session_id, self.clock, self.timestamp
        )
        return header + self.payload

    @classmethod
    def unpack(cls, packet):
        if len(packet) < cls.HEADER_SIZE:
            return None
        magic, version, command, seq, sid, clock, ts = struct.unpack(
            cls.HEADER_FORMAT, packet[:cls.HEADER_SIZE]
        )
        if magic != cls.MAGIC or version != cls.VERSION:
            return None
        return cls(command, seq, sid, clock, ts, packet[cls.HEADER_SIZE:])
