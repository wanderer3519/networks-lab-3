'''
    Asynchronous UADP server handling multiple clients with session management,
    Modified to be more susceptible to packet loss
'''

import asyncio, struct, time, sys, socket


class ServerProtocol:
    MAGIC = 0xC461
    VERSION = 1
    CMD_HELLO, CMD_DATA, CMD_ALIVE, CMD_GOODBYE = 0, 1, 2, 3
    HDR_FMT = "!H B B I I Q d"
    HDR_SIZE = struct.calcsize(HDR_FMT)
    INACTIVITY_LIMIT = 5  # seconds

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
        
        # Set smaller socket buffers to encourage drops
        sock = transport.get_extra_info('socket')
        if sock:
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8192)
            except:
                pass
                
        print(f"Waiting on port {transport.get_extra_info('sockname')[1]}...")
        asyncio.create_task(self.check_timeouts())

    def datagram_received(self, data, addr):
        pkt = self.unpack_header(data)
        if pkt is None:
            print("Received malformed packet, ignoring.")
            return
            
        self.server_clock = max(self.server_clock, pkt["clock"]) + 1

        sid = pkt["session_id"]
        cmd = pkt["command"]
        cseq = pkt["seq"]

        if sid not in self.sessions:
            if cmd != self.CMD_HELLO:
                print(f"Unknown session 0x{sid:08x}, no hello, ignoring packet.")
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
        # Update last_seen on every valid packet
        sess["last_seen"] = time.time()

        if cmd == self.CMD_DATA:
            exp = sess["expected"]
            
            if cseq == exp:
                # in-order packet
                try:
                    line = pkt["payload"].decode(errors="replace").rstrip("\n")
                except Exception as e:
                    line = str(pkt["payload"])
                print(f"0x{sid:08x} [{cseq}] {line}")
                sess["expected"] += 1
                
                # Send ALIVE response
                hdr = self.pack_header(
                    self.CMD_ALIVE, self.server_seq, sid, self.server_clock
                )
                self.transport.sendto(hdr, addr)
                self.server_seq += 1

            elif cseq == exp - 1:
                # duplicate packet
                print(f"0x{sid:08x} [{cseq}] Duplicate packet") 
                # Still send ALIVE for duplicate
                hdr = self.pack_header(
                    self.CMD_ALIVE, self.server_seq, sid, self.server_clock
                )
                self.transport.sendto(hdr, addr)
                self.server_seq += 1

            elif cseq > exp:
                # gap (lost packets) - this is what we want to see!
                for missing in range(exp, cseq):
                    print(f"0x{sid:08x} [{missing}] Lost packet!")
                    
                try:
                    line = pkt["payload"].decode(errors="replace").rstrip("\n")
                except Exception as e:
                    line = str(pkt["payload"])
                print(f"0x{sid:08x} [{cseq}] {line}")
                sess["expected"] = cseq + 1
                
                # Send ALIVE response
                hdr = self.pack_header(
                    self.CMD_ALIVE, self.server_seq, sid, self.server_clock
                )
                self.transport.sendto(hdr, addr)
                self.server_seq += 1

            else:
                # protocol error → close session (old packet)
                print(f"0x{sid:08x} Protocol error (old packet {cseq} < expected {exp})")
                hdr = self.pack_header(
                    self.CMD_GOODBYE, self.server_seq, sid, self.server_clock
                )
                self.transport.sendto(hdr, addr)
                self.server_seq += 1
                del self.sessions[sid]

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