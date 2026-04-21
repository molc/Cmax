import socket
import threading
from typing import Optional
from .message import ProtocolMessage


class ProtocolSocket:
    """TCP 套接字封装，负责连接的建立、发送和接收。

    所有 send/recv 操作均通过锁保护，可在多线程环境下安全使用。
    """

    def __init__(self, host: str, port: int, timeout: float = 5.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: Optional[socket.socket] = None
        self._lock = threading.Lock()

    def connect(self) -> None:
        """连接到设备。"""
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.settimeout(self.timeout)
        self._sock.connect((self.host, self.port))

    def close(self) -> None:
        """关闭连接。"""
        with self._lock:
            if self._sock:
                try:
                    self._sock.close()
                except Exception:
                    pass
                self._sock = None

    def send_message(self, msg: ProtocolMessage) -> None:
        """发送一条协议消息（线程安全）。"""
        with self._lock:
            if not self._sock:
                raise ConnectionError("未连接到设备")
            self._sock.sendall(msg.to_bytes())

    def recv_message(self) -> tuple[Optional[ProtocolMessage], Optional[str]]:
        """接收一条协议消息（线程安全）。返回 (message, error)。"""
        with self._lock:
            if not self._sock:
                return None, "未连接到设备"
            return ProtocolMessage.from_socket(self._sock)

    def recv_until_response(
        self, response_proto: int, max_skips: int = 10, _diag: bool = False
    ) -> tuple[Optional[ProtocolMessage], Optional[str]]:
        """
        接收消息，直到收到期望的响应协议号。
        跳过中间的推送通知（status=0 或 body 以 <Notify 开头）。
        最多跳过 max_skips 条消息，防止死循环。
        _diag: 若为 True，写入诊断日志到 diag.log。
        """
        import sys, os, threading
        diag_file = os.path.join(os.path.dirname(__file__), "..", "diag.log")
        def diag(msg):
            if _diag:
                with open(diag_file, "a", encoding="utf-8") as f:
                    tid = threading.get_ident()
                    f.write(f"[{tid}] {msg}\n")
        with self._lock:
            if not self._sock:
                return None, "未连接到设备"
            diag(f"recv_until({response_proto}) start")
            max_iter = max_skips if not _diag else 200
            for i in range(max_iter):
                msg, err = ProtocolMessage.from_socket(self._sock)
                if err:
                    diag(f"recv error: {err}")
                    return None, err
                diag(f"recv msg proto={msg.header.protocol_num} status={msg.header.status_code} len={len(msg.xml_body)}")
                if msg.header.protocol_num == response_proto:
                    diag(f"MATCH! returning response")
                    return msg, None
                diag(f"skip [{i}] (not {response_proto})")
            diag(f"timeout: got {max_iter} unexpected msgs")
            return None, f"收到 {max_iter} 条非期望消息，未找到响应 {response_proto}"

    @property
    def is_connected(self) -> bool:
        with self._lock:
            return self._sock is not None

    def __str__(self) -> str:
        status = f"connected to {self.host}:{self.port}" if self.is_connected else "disconnected"
        return f"ProtocolSocket({status})"

