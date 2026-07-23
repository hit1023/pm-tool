import socket


def send_magic_packet(mac_address: str, broadcast_ip: str = "255.255.255.255", port: int = 9) -> None:
    """指定MACアドレス宛のWOLマジックパケットをUDPブロードキャストで送信する。"""
    mac_bytes = bytes.fromhex(mac_address.replace(":", "").replace("-", "").strip())
    if len(mac_bytes) != 6:
        raise ValueError(f"MACアドレスの形式が不正です: {mac_address}")

    packet = b"\xff" * 6 + mac_bytes * 16

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(packet, (broadcast_ip, port))
    finally:
        sock.close()
