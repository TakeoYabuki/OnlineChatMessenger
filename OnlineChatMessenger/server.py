import socket
import time
import threading
import os

TCP_PORT = 9000
UDP_PORT = 9001

CLIENT_TIMEOUT = 300
MAX_FAILURE = 3

rooms = {}

# rooms[room] = {
#   "host_token": str,
#   "tokens": {token: (ip, udp_port) },
#   "username": {token: username},
#   "last_seen": {token: timestamp }
#    "failure": {token: count} 
# }

STATE_REQUEST = 0
STATE_RESPONSE = 1
STATE_COMPLETE = 2

#===================
# TCP: recv_exact
#===================
def recv_exact(conn, size):
    buf = b""
    while len(buf) < size:
        received = conn.recv(size - len(buf))
        if not received:
            raise ConnectionError("connection closed")
        buf += received
    return buf

#===================
# TCP: room control
#===================

def tcp_server():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("0.0.0.0", TCP_PORT))
    sock.listen()
    print("TCP server listening")

    while True:
        conn, addr = sock.accept()
        try:
            # ===== State 0: request =====
            header = recv_exact(conn, 32)
            room_size = header[0]
            operation = header[1]
            state = header[2]
            payload_size = int.from_bytes(header[3:32], "big")

            if state != STATE_REQUEST:
                conn.close()
                continue

            body= recv_exact(conn, room_size + payload_size)
            room = body[:room_size].decode("utf-8")
            payload = body[room_size:]

            username = payload[:-2].decode("utf-8")
            udp_port = int.from_bytes(payload[-2:], "big")

            # ===== validate =====
            if operation == 1 and room in rooms:
                error_msg = b"ROOM_EXISTS"

                conn.sendall(
                    room_size.to_bytes(1, "big") +
                    operation.to_bytes(1, "big") +
                    STATE_RESPONSE.to_bytes(1, "big") +
                    len(error_msg).to_bytes(29) +
                    error_msg
                )
                conn.close()
                continue

            if operation == 2 and room not in rooms:
                error_msg = b"ROOM_NOT_FOUND"

                conn.sendall(
                    room_size.to_bytes(1, "big") +
                    operation.to_bytes(1, "big") +
                    STATE_RESPONSE.to_bytes(1, "big") +
                    len(error_msg).to_bytes(29, "big") +
                    error_msg
                )
                conn.close()
                continue

            # ===== State 1: response (OK) =====
            ok = b"OK"

            conn.sendall(
                room_size.to_bytes(1, "big") +
                operation.to_bytes(1, "big") +
                STATE_RESPONSE.to_bytes(1, "big") +
                len(ok).to_bytes(29, "big") +
                ok
            )

            # ===== State 2: complete =====
            token = os.urandom(8).hex()

            if operation == 1:  # create
                rooms[room] = {
                    "host_token": token,
                    "tokens": {token: (addr[0], udp_port)},
                    "username": {token: username},
                    "last_seen": {token: time.time()},
                    "failure": {token: 0}
                }
            else:  # join
                rooms[room]["tokens"][token] = (addr[0], udp_port)
                rooms[room]["username"][token] = username
                rooms[room]["last_seen"][token] = time.time()
                rooms[room]["failure"][token] = 0

            conn.sendall(
                room_size.to_bytes(1, "big") +
                operation.to_bytes(1, "big") +
                STATE_COMPLETE.to_bytes(1, "big") +
                len(token.encode()).to_bytes(29, "big") +
                token.encode()
            )
        except Exception as e:
            print("TCP error:", e)
        
        finally:
            conn.close()

#===================
# UDP: chat relay
#===================

def udp_server():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", UDP_PORT))
    print("UDP server listening")

    while True:
        data, addr = sock.recvfrom(4096)

        if len(data) < 2:    #異常パケット検出
            continue

        room_size = data[0]
        token_size = data[1]

        if len(data) < 2 + room_size + token_size:
            continue

        room = data[2:2+room_size].decode("utf-8")
        token = data[2+room_size:2+room_size+token_size].decode("utf-8")
        message = data[2+room_size+token_size:]

        if room not in rooms:
            continue

        room_info = rooms[room]

        # token 正当性
        if token not in room_info["tokens"]:
            continue

        # 失敗回数チェック
        if room_info["tokens"][token][0] != addr[0]:
            room_info["failure"][token] += 1

            if room_info["failure"][token] >= MAX_FAILURE:
                print(f"[FAILURE DROP] {token}")
            
                if token == room_info["host_token"]:
                    print(f"FAILURE DROP: {token}")

                    for _, client_addr in room_info["tokens"].items():
                        sock.sendto(b"Room closed", client_addr)

                    del rooms[room]

                else:
                    for k in ("tokens", "username", "last_seen", "failure"):
                        del room_info[k][token]
            continue

        # 正常通信 → failure リセット
        room_info["failure"][token] = 0
        room_info["last_seen"][token] = time.time()

        # ===== LEAVE 処理 =====
        if message == b"__LEAVE__":
            
            username = room_info["username"][token]

            if token == room_info["host_token"]:
                print(f"[CLOSE] room {room}")

                for _, client_addr in room_info["tokens"].items():
                    sock.sendto(b"Room closed", client_addr)

                del rooms[room]
            else:
                for k in ("tokens", "username", "last_seen", "failure"):
                    del room_info[k][token]

                leave_msg = f"{username} left the room".encode("utf-8")
                for t, client_addr in room_info["tokens"].items():
                    sock.sendto(leave_msg, client_addr)
            continue

        # =========================================

        # 通常チャット
        sender = room_info["username"][token]
        sentMessage = f"{sender}: ".encode("utf-8") + message

        for t, client_addr in room_info["tokens"].items():
            if t != token:
                sock.sendto(sentMessage, client_addr)

#=====================
# Room cleanup / close
#=====================

def cleanup_rooms():
    while True:
        now = time.time()

        for room in list(rooms.keys()):
            room_info = rooms[room]

            for token in list(room_info["last_seen"].keys()):
                if now - room_info["last_seen"][token] > CLIENT_TIMEOUT:
                    print(f"[CLIENT_TIMEOUT] token removed {token}")

                    if token == room_info["host_token"]:
                        print(f"Close room {room}")
                        del rooms[room]
                        break
                    else:
                        for k in("tokens", "username", "last_seen", "failure"):
                            del room_info[k][token]
        time.sleep(5)

#===================
# Start server
#===================

threading.Thread(target=tcp_server, daemon=True).start()
threading.Thread(target=cleanup_rooms, daemon=True).start()
udp_server()
