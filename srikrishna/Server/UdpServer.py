import socket

localIP = "127.0.0.1"
localPort = 20001
bufferSize = 1024


msgFromServer = "Hello UDP Client"
bytesToSend = str.encode(msgFromServer)


with socket.socket(family=socket.AF_INET, type=socket.SOCK_DGRAM) as s:
    s.bind((localIP, localPort))
    print("UDP server up and listening")

    while True:
        bytesAddressPair = s.recvfrom(bufferSize)
        if not bytesAddressPair:
            break

        message = bytesAddressPair[0]
        address = bytesAddressPair[1]

        print(f"Message from Client:{message}")
        print(f"Client IP Address:{address}")

        s.sendto(bytesToSend, address)
