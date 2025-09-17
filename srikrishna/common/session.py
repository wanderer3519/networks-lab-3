class Session:
    def __init__(self, sid, addr):
        self.sid = sid
        self.addr = addr
        self.last_seq = -1
        self.clock = 0

    def update_clock(self, msg):
        self.clock = max(self.clock, msg.clock) + 1
