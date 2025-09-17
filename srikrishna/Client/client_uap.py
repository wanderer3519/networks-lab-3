import socket, struct, sys, threading, queue, time, random

class Client:
    MAGIC = 0xC461
    VERSION = 1
    CMD_HELLO, CMD_DATA, CMD_ALIVE, CMD_GOODBYE = 0, 1, 2, 3
    HDR_FMT = "!H B B I I Q d"
    HDR_SIZE = struct.calcsize(HDR_FMT)

    def __init__(self, host, port):
        self.session_id = random.getrandbits(32)
        self.seq = 0
        self.clock = 0
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(0.5)  # non-blocking receive with small timeout
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
            if line == "q":
                self.send_q.put(None)  # signal EOF if 'q' is typed
                break
            self.bump_clock_event()  # bump on stdin input
            self.send_q.put(line)
        # Only put None once at EOF if not already sent
        if not self.send_q.qsize() or self.send_q.queue[-1] is not None:
            self.send_q.put(None)

            self.send_q.put(line)
        self.bump_clock_event()
        self.send_q.put(None)  # EOF

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

            elif cmd == self.CMD_ALIVE:
                # Server acknowledged our DATA
                self.bump_clock_recv(pkt["clock"])
                self.awaiting_alive = False

                # latency measurement
                latency = time.time() - pkt["ts"]
                print(f"[latency] s→c {latency*1000:.2f} ms", file=sys.stderr)

            elif cmd == self.CMD_GOODBYE:
                # Server closed session → client closes immediately
                self.bump_clock_recv(pkt["clock"])
                print("Server closed session.")
                self.running = False

            else:
                # Unexpected command = protocol error → close
                print(f"Unexpected command {cmd}, closing session.")
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

        # wait briefly for HELLO
        time.sleep(0.5)

        # Step 2: DATA loop
        while self.running:
            if not self.send_q.empty() and not self.awaiting_alive:
                item = self.send_q.get()
                if item is None:
                    print("eof")
                    break

                self.bump_clock_event()
                payload = (item + "\n").encode()
                self.sock.sendto(
                    self.pack_header(
                        self.CMD_DATA, self.seq, self.session_id, self.clock, payload
                    ),
                    (self.SERVER_HOST, self.SERVER_PORT),
                )
                self.seq += 1
                self.awaiting_alive = True
                self.last_send_time = time.time()

            # timeout check (Ready Timer → Closing)
            if self.awaiting_alive and self.last_send_time:
                if time.time() - self.last_send_time > self.TIMEOUT:
                    print("Timeout waiting for ALIVE, closing.")
                    break

            time.sleep(0.01)  # small sleep to avoid busy loop

        # Step 3: GOODBYE
        self.bump_clock_event()
        self.sock.sendto(
            self.pack_header(self.CMD_GOODBYE, self.seq, self.session_id, self.clock),
            (self.SERVER_HOST, self.SERVER_PORT),
        )
        self.seq += 1
        time.sleep(0.5)  # allow server reply
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
