'''
    Asynchronous UADP server handling multiple clients with session management,
'''

import asyncio, struct, time, sys


class ServerProtocol:
    MAGIC = 0xC461
    VERSION = 1
    CMD_HELLO, CMD_DATA, CMD_ALIVE, CMD_GOODBYE = 0, 1, 2, 3
    HDR_FMT = "!H B B I I Q d"
    HDR_SIZE = struct.calcsize(HDR_FMT)
    INACTIVITY_LIMIT = 10  # seconds

    def __init__(self):
        self.sessions = {}
        self.server_seq = 0
        self.server_clock = 0

    def pack_header(self, command, seq, session_id, clock):
        ts = time.time()
        return struct.pack(
            self.HDR_FMT, self.MAGIC, self.VERSION, command, seq, session_id, clock, ts
        )

    def unpack_header(self, data):
        if len(data) < self.HDR_SIZE:
            return None
        magic, version, cmd, seq, sid, clock, ts = struct.unpack(
            self.HDR_FMT, data[: self.HDR_SIZE]
        )
        if magic != self.MAGIC or version != self.VERSION:
            return None
        payload = data[self.HDR_SIZE :]
        return dict(
            command=cmd, seq=seq, session_id=sid, clock=clock, ts=ts, payload=payload
        )

    def connection_made(self, transport):
        self.transport = transport
        print(f"Waiting on port {transport.get_extra_info('sockname')[1]} (asyncio)...")
        asyncio.create_task(self.check_timeouts())

    def datagram_received(self, data, addr):
        pkt = self.unpack_header(data)
        if pkt is None:
            return

        sid = pkt["session_id"]
        cmd = pkt["command"]
        cseq = pkt["seq"]

        if sid not in self.sessions:
            if cmd != self.CMD_HELLO:
                return
            self.sessions[sid] = {"expected": 0, "addr": addr, "last_seen": time.time()}
            print(f"0x{sid:08x} [{cseq}] Session created")
            hdr = self.pack_header(
                self.CMD_HELLO, self.server_seq, sid, self.server_clock
            )
            self.transport.sendto(hdr, addr)
            self.server_seq += 1
            return

        sess = self.sessions[sid]

        if cmd == self.CMD_DATA:
            exp = sess["expected"]
            if cseq == exp:
                # in-order packet
                line = pkt["payload"].decode(errors="replace").rstrip("\n")
                print(f"0x{sid:08x} [{cseq}] {line}")
                sess["expected"] += 1
                hdr = self.pack_header(
                    self.CMD_ALIVE, self.server_seq, sid, self.server_clock
                )
                self.transport.sendto(hdr, addr)
                self.server_seq += 1

            elif cseq == exp - 1:
                # duplicate
                print("Duplicate packet!")

            elif cseq < exp - 1:
                # protocol error → close session
                print(f"0x{sid:08x} Protocol error (old packet {cseq} < expected {exp})")
                hdr = self.pack_header(
                    self.CMD_GOODBYE, self.server_seq, sid, self.server_clock
                )
                self.transport.sendto(hdr, addr)
                self.server_seq += 1
                del self.sessions[sid]

            elif cseq > exp:
                # gap (lost packets)
                for missing in range(exp, cseq):
                    print("Lost packet!")
                line = pkt["payload"].decode(errors="replace").rstrip("\n")
                print(f"0x{sid:08x} [{cseq}] {line}")
                sess["expected"] = cseq + 1
                hdr = self.pack_header(
                    self.CMD_ALIVE, self.server_seq, sid, self.server_clock
                )
                self.transport.sendto(hdr, addr)
                self.server_seq += 1

        elif cmd == self.CMD_GOODBYE:
            print(f"0x{sid:08x} [{cseq}] GOODBYE from client.")
            print(f"0x{sid:08x} Session closed")
            hdr = self.pack_header(
                self.CMD_GOODBYE, self.server_seq, sid, self.server_clock
            )
            self.transport.sendto(hdr, addr)
            self.server_seq += 1
            del self.sessions[sid]
        
        else:
            # Unexpected command → protocol error
            print(f"0x{sid:08x} Protocol error (unexpected cmd {cmd})")
            hdr = self.pack_header(
                self.CMD_GOODBYE, self.server_seq, sid, self.server_clock
            )
            self.transport.sendto(hdr, addr)
            self.server_seq += 1
            del self.sessions[sid]

    async def check_timeouts(self):
        while True:
            await asyncio.sleep(1)
            now = time.time()
            expired = [
                sid
                for sid, sess in self.sessions.items()
                if now - sess["last_seen"] > self.INACTIVITY_LIMIT
            ]
            for sid in expired:
                sess = self.sessions[sid]
                print(f"0x{sid:08x} Session closed (timeout)")
                hdr = self.pack_header(
                    self.CMD_GOODBYE, self.server_seq, sid, self.server_clock
                )
                self.transport.sendto(hdr, sess["addr"])
                self.server_seq += 1
                del self.sessions[sid]
    
    def connection_lost(self, exc):
        print("Server connection closed.")


async def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <portnum>", file=sys.stderr)
        sys.exit(1)

    server_host = "0.0.0.0"
    server_port = int(sys.argv[1])

    loop = asyncio.get_running_loop()
    print("Starting server...")
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: ServerProtocol(), local_addr=(server_host, server_port)
    )
    try:
        await asyncio.Future()  # run forever
    except KeyboardInterrupt:
        print("\nServer shutting down...")
    finally:
        transport.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nServer shutting down gracefully.")
