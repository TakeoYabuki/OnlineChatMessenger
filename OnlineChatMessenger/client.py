import socket
import threading
import sys
import os

TCP_SERVER = ("127.0.0.1", 9000)
UDP_SERVER = ("127.0.0.1", 9001)

CLIENT_UDP_PORT = 10000 + (os.getpid() % 1000)

STATE_REQUEST = 0
STATE_RESPONSE = 1
STATE_COMPLETE = 2

#===================
# recv_exact
#===================
def recv_exact(sock, size):
    buf = b""
    while len(buf) < size:
        received = sock.recv(size - len(buf))
        if not received:
            raise ConnectionError("Connection closed")
        buf += received
    return buf

#===================
# TCP handshake
#===================
def tcp_handshake(room, username, operation):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    try:
        sock.connect(TCP_SERVER)
    except OSError:
        print("TCP SERVER unreachable")
        return None

    room_bytes = room.encode("utf-8")
    user_bytes = username.encode("utf-8")
    udp_port_bytes = CLIENT_UDP_PORT.to_bytes(2, "big")
    payload = user_bytes + udp_port_bytes

    # ===== State 0 =====
 

    header = (
        len(room_bytes).to_bytes(1, "big") +
        operation.to_bytes(1, "big") +
        STATE_REQUEST.to_bytes(1, "big") +
        len(payload).to_bytes(29, "big")
    )

    sock.sendall(header + room_bytes + payload)

    # ===== State 1 =====
    header = recv_exact(sock, 32)
    if header[2] != STATE_RESPONSE:
        print("Invalid response")
        sock.close()
        return None

    payload_len = int.from_bytes(header[3:32], "big")
    resp = recv_exact(sock, payload_len)

    if resp != b"OK":
        print("Server rejected:", resp.decode(errors ="replace"))
        sock.close()
        return None

    # ===== State 2 =====
    header = recv_exact(sock, 32)
    if header[2] != STATE_COMPLETE:
        print("Invalid complete response")
        sock.close()
        return None

    token_len = int.from_bytes(header[3:32], "big")
    token = recv_exact(sock, token_len).decode()

    sock.close()
    return token
#=====================
# UDP receive thread
#=====================
def udp_receive(sock):
    while True:
        data, _ = sock.recvfrom(4096)

        if data == b"Room closed":
            print("Room closed by host")
            os._exit(0)

        print(data.decode("utf-8", errors = "replace"))

#===================
# UDP chat loop
#===================
def udp_chat(room, token, udp_port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    try:
        sock.bind(("", udp_port))
    except OSError:
        print("UDP port bind failed")
        sys.exit(1)

    room_bytes = room.encode("utf-8")
    token_bytes = token.encode("utf-8")

    threading.Thread(target=udp_receive, args=(sock,), daemon=True).start()

    try:
        while True:
            msg = input("> ")
            msg_bytes = msg.encode("utf-8")

            if not msg:
                continue

            MAX_UDP = 4096
            overhead = 2 + len(room_bytes) + len(token_bytes)

            if overhead + len(msg_bytes) > MAX_UDP:
                print("Message too long")
                continue

            packet = (
                len(room_bytes).to_bytes(1, "big") +
                len(token_bytes).to_bytes(1, "big") +
                room_bytes +
                token_bytes +
                msg_bytes
            )

            sock.sendto(packet, UDP_SERVER)

    except (EOFError,KeyboardInterrupt) as e:
        leave_packet = (
            len(room_bytes).to_bytes(1, "big") +
            len(token_bytes).to_bytes(1, "big") +
            room_bytes +
            token_bytes +
            b"__LEAVE__"
        )
        sock.sendto(leave_packet, UDP_SERVER)
        
        if isinstance(e, EOFError):
            print("Input closed. Exiting.")
        else:
            print("\nleft room.")

        os._exit(0)
# ===================
# Main
# ===================
room = input("Room name: ")
username = input("Username: ")

username_bytes = username.encode("utf-8")

if len(username_bytes) > 255:
    print("Username too long( max 255 bytes in UTF-8")
    sys.exit(1)

mode = int(input("1=create, 2=join: "))


token = tcp_handshake(room, username, mode)

if token is None:
    print("Join failed. Exiting.")
    sys.exit(1)

print("Joined room.  Start chatting.")
udp_chat(room, token, CLIENT_UDP_PORT)
