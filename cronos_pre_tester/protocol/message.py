import socket
from dataclasses import dataclass
from typing import Optional
from .header import ProtocolHeader, HEADER_SIZE


def recv_exact(sock: socket.socket, n: int) -> Optional[bytes]:
    """Read exactly n bytes from socket. Returns None if connection closed."""
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            return None
        data += chunk
    return data


@dataclass
class ProtocolMessage:
    header: ProtocolHeader
    xml_body: str

    def to_bytes(self) -> bytes:
        body_bytes = self.xml_body.encode("utf-8")
        return self.header.pack_with_body(body_bytes) + body_bytes

    @classmethod
    def from_socket(cls, sock: socket.socket) -> tuple[Optional["ProtocolMessage"], Optional[str]]:
        """Receive exactly one message from a connected socket."""
        # 1. Read header
        header_data = recv_exact(sock, HEADER_SIZE)
        if header_data is None:
            return None, "连接已断开（读取头部时）"
        try:
            header = ProtocolHeader.unpack(header_data)
        except ValueError as e:
            return None, f"协议头解析失败: {e}"

        if header.marker != 0xFFFF:
            return None, f"无效的协议标记 0x{header.marker:04X}，期望 0xFFFF"

        # 2. Read body
        body = ""
        if header.body_length > 0:
            body_data = recv_exact(sock, header.body_length)
            if body_data is None:
                return None, "连接已断开（读取正文时）"
            body = body_data.decode("utf-8", errors="replace")

        return cls(header, body), None

    def __str__(self) -> str:
        preview = self.xml_body[:200].replace("\n", " ")
        return f"ProtocolMessage({self.header}, body='{preview}...')"
