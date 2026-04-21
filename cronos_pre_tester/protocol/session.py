from dataclasses import dataclass, field
from typing import Optional
import threading


@dataclass
class Session:
    user_id: Optional[int] = None
    a_token: Optional[str] = None
    a_token_expires: int = 0  # 分钟
    role: Optional[int] = None
    machine: str = ""
    user_id_str: str = ""  # 用于 XML 模板，如 "1"
    serial: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def next_serial(self) -> int:
        with self._lock:
            self.serial += 1
            return self.serial

    @property
    def is_authenticated(self) -> bool:
        return self.user_id is not None and self.a_token is not None

    def user_id_xml(self) -> str:
        """生成带 atoken 属性的 UserID 标签。"""
        uid = self.user_id or 0
        if self.a_token:
            return f'<UserID atoken="{self.a_token}">{uid}</UserID>'
        return f"<UserID>{uid}</UserID>"

    def build_auth_request_xml(self, body_content: str) -> str:
        """构建已授权的请求 XML。"""
        return (
            '<?xml version="1.0" encoding="utf-8"?>'
            f"<Message>"
            f"{self.user_id_xml()}"
            f"{body_content}"
            f"</Message>"
        )

    def logout_xml(self) -> str:
        """构建退出登录的 XML。"""
        uid = self.user_id_str or str(self.user_id or "")
        return (
            '<?xml version="1.0" encoding="utf-8"?>'
            f"<Message>"
            f"<UserID>{uid}</UserID>"
            f"</Message>"
        )

    def heartbeat_xml(self) -> str:
        """构建心跳请求的 XML。"""
        return self.build_auth_request_xml("")

    def __str__(self) -> str:
        if self.is_authenticated:
            return f"Session(user={self.user_id}, role={self.role}, token={self.a_token[:8]}...)"
        return "Session(not authenticated)"
