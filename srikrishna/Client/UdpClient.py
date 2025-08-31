import socket


msgFromClient = "Hello UDP Server"
bytesToSend = str.encode(msgFromClient)
serverAddressPort = ("127.0.0.1", 20001)
bufferSize = 1024


with socket.socket(family=socket.AF_INET, type=socket.SOCK_DGRAM) as s:
    s.sendto(bytesToSend, serverAddressPort)
    msgFromServer = s.recvfrom(bufferSize)
    print(f"Message from Server {msgFromServer[0]}")
    
