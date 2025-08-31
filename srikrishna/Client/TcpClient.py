import socket

class TcpClient:
    def __init__(self, host, portnum):
        self.host = host
        self.port = portnum
    
    def start(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect((self.host, self.port))
            print("Connected to server: ")
            message = input("(q to quit) --> ")

            while message != "q":
                s.send(message.encode('utf-8'))
                data = s.recv(1024).decode('utf-8')
                print(f"Recieved: {data}")
                message = input("(q to quit) --> ")

            print("Closing Client...")        

def Main():
    host = "localhost"
    port = 5000

    c = TcpClient(host, port)

    c.start()


if __name__ == "__main__":
    Main()