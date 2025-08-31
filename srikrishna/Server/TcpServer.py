import socket

class TcpServer:
    def __init__(self, host, portnum):
        self.host = host
        self.port = portnum
        pass

    def start(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((self.host, self.port))
            s.listen()

            print("Started server, listening")

            conn, addr = s.accept()
            print(f"Connection from {addr} accepted!")

            while True:
                data = conn.recv(1024).decode()
                if not data:
                    break
                
                print(f"Received data: {data}")
                data = data.upper()
                print(f"Sending: {data}\n")
                conn.send(data.encode('utf-8'))
            
            print("Server shutting down")



def Main():
    host = "127.0.0.1"
    port = 5000

    s = TcpServer(host, port)
    s.start()


if __name__ == "__main__":
    Main()