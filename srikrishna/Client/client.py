import socket
import struct
import sys
import threading
import queue
import time
import random

class Client:
    MAGIC = 0xC461
    VERSION = 1
    CMD_HELLO, CMD_DATA, CMD_ALIVE, CMD_GOODBYE = 0, 1, 2, 3
    HDR_FMT = "!H B B I I Q d"
    HDR_SIZE = struct.calcsize(HDR_FMT)
    MAX_PAYLOAD = 1400  # safe UDP payload size

    def __init__(self, host, port):
        self.session_id = random.getrandbits(32)
        self.seq = 0
        self.clock = 0
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        
        # Smaller socket buffers to encourage drops
        try:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 8192)
        except:
            pass
            
        self.sock.settimeout(0.1)  # Much smaller timeout for faster operation
        self.send_q = queue.Queue()
        self.SERVER_HOST = host
        self.SERVER_PORT = port

        self.running = True
        self.awaiting_alive = False
        self.last_send_time = None
        self.TIMEOUT = 2.0  # seconds

    # ---------------- Clock helpers ---------------- #
    def bump_clock_event(self):
        self.clock += 1
        return self.clock

    def bump_clock_recv(self, rcvd_clock):
        self.clock = max(self.clock, rcvd_clock) + 1
        return self.clock

    # ---------------- Header helpers ---------------- #
    def pack_header(self, command, seq, session_id, clock, payload=b""):
        ts = time.time()
        hdr = struct.pack(
            self.HDR_FMT, self.MAGIC, self.VERSION, command, seq, session_id, clock, ts
        )
        return hdr + payload

    def unpack_header(self, data):
        if len(data) < self.HDR_SIZE:
            return None
        magic, version, cmd, seq, sid, rcv_clock, sent_ts = struct.unpack(
            self.HDR_FMT, data[: self.HDR_SIZE]
        )
        if magic != self.MAGIC or version != self.VERSION:
            return None
        payload = data[self.HDR_SIZE :]
        return dict(
            cmd=cmd, seq=seq, sid=sid, clock=rcv_clock, ts=sent_ts, payload=payload
        )

    # ---------------- Reader thread ---------------- #
    def reader(self):
        for line in sys.stdin:
            line = line.rstrip("\n")
            self.bump_clock_event()
            if line == "q":
                self.send_q.put(None)  # signal EOF if 'q' is typed
                break

            # Send entire lines as single packets for faster transmission
            self.send_q.put(line.encode())
            
        # Only put None once at EOF if not already sent
        if not self.send_q.qsize() or self.send_q.queue[-1] is not None:
            self.bump_clock_event()
            self.send_q.put(None)

    # ---------------- Receiver thread ---------------- #
    def receiver(self):
        while self.running:
            try:
                data, _ = self.sock.recvfrom(4096)
            except socket.timeout:
                # just loop back; timeout check is handled in run()
                continue

            pkt = self.unpack_header(data)
            if not pkt:
                continue

            cmd = pkt["cmd"]

            if cmd == self.CMD_HELLO:
                # Server acknowledged our HELLO
                self.bump_clock_recv(pkt["clock"])
                self.hello_ack = True
                print("Session established.", file=sys.stderr)

            elif cmd == self.CMD_ALIVE:
                # Server acknowledged our DATA
                self.bump_clock_recv(pkt["clock"])
                self.awaiting_alive = False
                self.last_send_time = None  # cancel timer

                # latency measurement
                latency = time.time() - pkt["ts"]
                print(f"[latency] s→c {latency*1000:.2f} ms", file=sys.stderr)

            elif cmd == self.CMD_GOODBYE:
                # Server closed session → client closes immediately
                self.bump_clock_recv(pkt["clock"])
                print("Server closed session.", file=sys.stderr)
                self.running = False

            else:
                # Unexpected command = protocol error → close
                print(f"Unexpected command {cmd}, closing session.", file=sys.stderr)
                self.running = False

    # ---------------- Main run ---------------- #
    def run(self):
        # start reader + receiver
        threading.Thread(target=self.reader, daemon=True).start()
        threading.Thread(target=self.receiver, daemon=True).start()

        # Step 1: HELLO
        self.bump_clock_event()
        self.sock.sendto(
            self.pack_header(self.CMD_HELLO, self.seq, self.session_id, self.clock),
            (self.SERVER_HOST, self.SERVER_PORT),
        )
        self.seq += 1

        # Wait until HELLO ack is received
        while self.running and not hasattr(self, "hello_ack"):
            time.sleep(0.001)  # Much smaller sleep

        # Step 2: DATA loop - Send packets as fast as possible
        pending_packets = []
        
        while self.running:
            # Collect multiple packets before sending
            batch_size = 0
            while not self.send_q.empty() and batch_size < 10:  # Send in small batches
                item = self.send_q.get()
                if item is None:
                    print("eof", file=sys.stderr)
                    self.bump_clock_event()
                    self.sock.sendto(
                        self.pack_header(self.CMD_GOODBYE, self.seq, self.session_id, self.clock),
                        (self.SERVER_HOST, self.SERVER_PORT),
                    )
                    self.seq += 1
                    self.running = False
                    break  # go to Closing state

                pending_packets.append(item)
                batch_size += 1

            # Send all pending packets rapidly
            for payload in pending_packets:
                self.bump_clock_event()
                self.sock.sendto(
                    self.pack_header(
                        self.CMD_DATA, self.seq, self.session_id, self.clock, payload
                    ),
                    (self.SERVER_HOST, self.SERVER_PORT),
                )
                self.seq += 1
                # No sleep here - send as fast as possible!
            
            pending_packets.clear()
            
            if not self.running:
                break
                
            # Very small sleep only when no data to send
            time.sleep(0.0001)

        # Step 3: closing
        closing_start = time.time()
        while time.time() - closing_start < self.TIMEOUT and self.running:
            time.sleep(0.05)  # wait for server GOODBYE or ALIVE
        # then close
        self.running = False
        self.sock.close()


def main():
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <server_host> <server_port>", file=sys.stderr)
        sys.exit(1)
    server_host = sys.argv[1]
    server_port = int(sys.argv[2])
    client = Client(server_host, server_port)
    client.run()


if __name__ == "__main__":
    main()