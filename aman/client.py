# python3 client.py 127.0.0.1 8080 ./sample.txt
import struct
import socket
import time
import sys
import asyncio
import random
import os

MAGIC = 0xC461
VERSION = 1
MAX_DATA_SIZE = 4096

# commands
HELLO   = 0
DATA    = 1
ALIVE   = 2
GOODBYE = 3

HEADER_FMT = "!H B B I I Q Q"
HEADER_SIZE = struct.calcsize(HEADER_FMT)

# states as per the FSA
START       = 0
HELLO_WAIT  = 1
READY       = 2
READY_TIMER = 3
CLOSING     = 4
CLOSED      = 5

STATE = START # current state

INPUT_FILE_NAME = None
INPUT_STREAM = sys.stdin

# server address => 127.0.0.1:8080
SERVER = "127.0.0.1"
PORT = 8080

SOCK = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
SOCK.setblocking(False)
SERVER_ADDRESS = (SERVER, PORT)
SESSION_ID = 1234

TIMEOUT = 10.0
SEQUENCE_NUMBER = 0
STOP_READING = False
DONE_READY_TIMER = True
CLOSE_CLIENT = False
CLIENT_LOGICAL_CLOCK = 0

TOTAL_LATENCY = 0
NUMBER_OF_SERVER_MESSAGE = 0

event = asyncio.Event()

def now_s():
    """returns current time in micro seconds"""
    return int(time.time())

def now_ms():
    """returns current time in micro seconds"""
    return time.time_ns() // 1000


def build_header(magic, version, command, seq, SESSION_ID,
                 logical_clock, timestamp):
    return struct.pack(
        HEADER_FMT,
        magic,
        version,
        command,
        seq,
        SESSION_ID,
        logical_clock,
        timestamp
    )


def build_packet(command, payload: bytes = b""):
    header = build_header(
        MAGIC,
        VERSION,
        command,
        SEQUENCE_NUMBER,
        SESSION_ID,
        CLIENT_LOGICAL_CLOCK,
        now_ms()
    )
    return header + payload

def parse_packet(data: bytes):
    header = struct.unpack(HEADER_FMT, data[:HEADER_SIZE])
    payload = data[HEADER_SIZE:]
    header_dict = {
        "magic": header[0],
        "version": header[1],
        "command": header[2],
        "sequence_number": header[3],
        "SESSION_ID": header[4],
        "logical_clock": header[5],
        "timestamp": header[6],
    }
    return header_dict, payload

def add_latency(server_timestamp):
    global TOTAL_LATENCY, NUMBER_OF_SERVER_MESSAGE
    TOTAL_LATENCY += now_ms() - server_timestamp
    NUMBER_OF_SERVER_MESSAGE += 1

def send_message(pkt):
    global SEQUENCE_NUMBER, CLIENT_LOGICAL_CLOCK
    SEQUENCE_NUMBER += 1
    CLIENT_LOGICAL_CLOCK += 1
    SOCK.sendto(pkt, SERVER_ADDRESS)
    return

def is_hello(hdr):
    if hdr["command"] == HELLO:
        return True
    return False

def is_goodbye(hdr):
    if hdr["command"] == GOODBYE:
        return True
    return False

def is_alive(hdr):
    if hdr["command"] == ALIVE:
        return True
    return False

def send_hello():
    pkt = build_packet(HELLO)
    send_message(pkt)

def send_goodbye():
    pkt = build_packet(GOODBYE)
    send_message(pkt)

def send_data(msg):
    payload = msg.encode("utf-8")
    pkt = build_packet(DATA, payload)
    send_message(pkt)
    return

def update_logical_clock(server_logical_clock):
    global CLIENT_LOGICAL_CLOCK
    CLIENT_LOGICAL_CLOCK = max(server_logical_clock, CLIENT_LOGICAL_CLOCK) + 1
    return

async def run_start():
    global STATE, CLIENT_LOGICAL_CLOCK
    send_hello()    # sending initial HELLO packet
    STATE = HELLO_WAIT
    CLIENT_LOGICAL_CLOCK += 1
    return

async def run_hello_wait():
    global STATE, CLIENT_LOGICAL_CLOCK
    try:
        loop = asyncio.get_running_loop()
        data = await asyncio.wait_for(loop.sock_recv(SOCK, MAX_DATA_SIZE), TIMEOUT)
        hdr, payload = parse_packet(data)
        add_latency(hdr["timestamp"])
        update_logical_clock(hdr["logical_clock"])
        if is_hello(hdr):
            STATE = READY
        elif is_goodbye(hdr):
            STATE = CLOSED
        else:
            STATE = CLOSED
    except asyncio.TimeoutError:
        send_goodbye()
        STATE = CLOSING
        CLIENT_LOGICAL_CLOCK += 1
    return

async def read_input_and_send():
    global STATE, STOP_READING, CLIENT_LOGICAL_CLOCK
    STOP_READING = False
    loop = asyncio.get_running_loop()
    while not STOP_READING:
        event.clear()
        line = await loop.run_in_executor(None, INPUT_STREAM.readline)
        CLIENT_LOGICAL_CLOCK += 1
        if line == "":  # EOF
            STATE = CLOSING
            STOP_READING = True
            event.set()
            break
        if INPUT_FILE_NAME is None:
            if line.strip() == "q":
                STATE = CLOSING
                STOP_READING = True
                event.set()
                break
        send_data(line)
        event.set()


async def read_socket_ready_timer():
    # in the "READY TIMER" state, it asyncronously wait for ALIVE message from socket.
    loop = asyncio.get_running_loop()
    data = await loop.sock_recv(SOCK, MAX_DATA_SIZE)
    return data

async def run_ready_timer():
    global STATE, STOP_READING, CLIENT_LOGICAL_CLOCK
    tasks = [
        asyncio.create_task(read_input_and_send()),
        asyncio.create_task(read_socket_ready_timer())
    ]
    done, pending = await asyncio.wait(tasks, timeout=TIMEOUT, return_when=asyncio.FIRST_COMPLETED)

    if not done:
        # no tasks finished before timeout
        print("TIMEOUT reached at 'READY TIME' state")
        send_goodbye()
        STATE = CLOSING
        STOP_READING = True
        CLIENT_LOGICAL_CLOCK += 1
        return

    done_task = list(done)[0]
    if done_task is tasks[1]:  # socket task finished
        STOP_READING = True
        result = done_task.result()
        hdr, payload = parse_packet(result)
        add_latency(hdr["timestamp"])
        update_logical_clock(hdr["logical_clock"])
        if is_alive(hdr):
            STATE = READY
        else:
            STATE = CLOSED
    else:  # file/stdin read finished
        if STATE == CLOSING:
            send_goodbye()
        else:
            STATE = CLOSED

async def read_socket_ready():
    # in the "READY" state, it asyncronously looks for ALIVE message from socket, till it receives something else.
    loop = asyncio.get_running_loop()
    while True:
        data = await loop.sock_recv(SOCK, MAX_DATA_SIZE)
        hdr, payload = parse_packet(data)
        add_latency(hdr["timestamp"])
        update_logical_clock(hdr["logical_clock"])
        if not is_alive(hdr):
            break
    return

async def wait_for_one_input():
    global STATE
    
    await event.wait()

    if STATE==CLOSING or STATE ==CLOSED:
        return
    loop = asyncio.get_running_loop()
    line = await loop.run_in_executor(None, INPUT_STREAM.readline)
    if line == "":  # EOF
        STATE = CLOSING
        return
    if INPUT_FILE_NAME is None:
        if line.strip() == "q":
            STATE = CLOSING
            return
    send_data(line)

async def run_ready():
    global STATE, CLIENT_LOGICAL_CLOCK

    tasks = [
        asyncio.create_task(wait_for_one_input()),
        asyncio.create_task(read_socket_ready())
    ]

    # whichever finishes first
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)

    if STATE == CLOSING :
        send_goodbye()
        return
    elif STATE == CLOSED:
        return
    
    done_task = list(done)[0]
    if done_task is tasks[1]:
        # socket task finished
        STATE = CLOSED
    else:
        # file/stdin read finished
        CLIENT_LOGICAL_CLOCK += 1
        if STATE == CLOSING:
            send_goodbye()
        else:
            STATE = READY_TIMER

async def keep_looping_for_alive():
    loop = asyncio.get_running_loop()
    while True:
        data = await loop.sock_recv(SOCK, MAX_DATA_SIZE)
        hdr, payload = parse_packet(data)
        add_latency(hdr["timestamp"])
        update_logical_clock(hdr["logical_clock"])
        if is_goodbye(hdr):
            return "goodbye", hdr, payload
        elif not is_alive(hdr):
            return "other", hdr, payload
        # continue if packet received is ALIVE

async def run_closing():
    global STATE, CLIENT_LOGICAL_CLOCK
    try:
        msg_type, hdr, payload = await asyncio.wait_for(keep_looping_for_alive(), TIMEOUT)
        add_latency(hdr["timestamp"])
        update_logical_clock(hdr["logical_clock"])
        if msg_type == "goodbye":
            STATE = CLOSED
        else:
            # received unexpected non-ALIVE message
            STATE = CLOSED
    except asyncio.TimeoutError:
        print("Timeout reached at 'closing' state")
        STATE = CLOSED
        CLIENT_LOGICAL_CLOCK += 1

def print_one_way_latency():
    global TOTAL_LATENCY, NUMBER_OF_SERVER_MESSAGE
    print("Average one way latency[server to client]: ", TOTAL_LATENCY/NUMBER_OF_SERVER_MESSAGE, " micro seconds")
    return

async def run_closed():
    global SOCK
    SOCK.close()
    print_one_way_latency()
    print("terminating client...")
    os._exit(0)
    return


async def take_action():
    global STATE, INPUT_FILE_NAME, INPUT_STREAM, SERVER, PORT, SERVER_ADDRESS, SESSION_ID

    while True:
        if STATE == START:
            await run_start()
        elif STATE == HELLO_WAIT:
            await run_hello_wait()
        elif STATE == READY:
            await run_ready()
        elif STATE == READY_TIMER:
            await run_ready_timer()
        elif STATE == CLOSING:
            await run_closing()
        elif STATE == CLOSED:
            await run_closed()
            break
    return

def main():
    global STATE, INPUT_FILE_NAME, INPUT_STREAM, SERVER, PORT, SERVER_ADDRESS, SESSION_ID

    arg_len = len(sys.argv)

    if not (arg_len==3 or arg_len==4):
        print("INVALID USAGE!!!")
        print("usage: ./client <hostname> <portnum> [filename]")
        SOCK.close()
        sys.exit(1)

    SERVER = sys.argv[1]
    PORT = int(sys.argv[2])
    
    if( arg_len ==  4):
        INPUT_FILE_NAME = sys.argv[3]
        INPUT_STREAM = open(INPUT_FILE_NAME, "r")

    SERVER_ADDRESS = (SERVER, PORT)
    SESSION_ID = random.randint(0, 2**32 - 1)

    event.set()
    asyncio.run(take_action())


if __name__ == "__main__":
    main()
