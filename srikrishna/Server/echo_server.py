# echo server TCP

import socket

HOST = "127.0.0.1" # localhost
PORT = 65432

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
    s.bind((HOST, PORT))
    s.listen()
    conn, addr = s.accept()

    with conn:
        print(f"Connected by {addr}")
        while True:
            data = conn.recv(1024) # reads upto 1kb of data from client
            if not data:
                break
            conn.sendall(data)
