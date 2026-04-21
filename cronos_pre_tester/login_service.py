from PyQt5.QtCore import QObject, QThread, pyqtSignal
import xml.etree.ElementTree as ET
from protocol import (
    ProtocolSocket,
    ProtocolHeader,
    ProtocolMessage,
    Session,
    build_login_step1_xml,
    build_login_step2_xml,
)
from config import SOCKET_TIMEOUT_SEC


class LoginWorker(QThread):
    """
    在后台线程中执行登录流程。
    两步认证：发无认证请求 -> 收到401(nonce) -> 计算digest -> 重发 -> 收到200
    """

    # 登录过程中分阶段通知 UI
    sig_stage     = pyqtSignal(str)   # 当前阶段描述
    sig_success   = pyqtSignal(Session, ProtocolSocket)   # 成功：session + socket
    sig_failure   = pyqtSignal(str)    # 失败：错误信息

    def __init__(self, host, port, username, password, parent=None):
        super().__init__(parent)
        self.host = host
        self.port = port
        self.username = username
        self.password = password

    def run(self):
        sock = None
        try:
            self.sig_stage.emit("正在连接到设备...")
            sock = ProtocolSocket(self.host, self.port, timeout=SOCKET_TIMEOUT_SEC)
            sock.connect()
            self.sig_stage.emit("连接成功，发送登录请求（第一步）...")

            # ==== 登录第一步：发送无 Authorization 的请求 ====
            xml_step1 = build_login_step1_xml(self.username)
            msg_step1 = ProtocolMessage(
                ProtocolHeader(protocol_num=80000),
                xml_step1,
            )
            sock.send_message(msg_step1)
            self.sig_stage.emit("等待服务器认证挑战...")

            resp1, err1 = sock.recv_until_response(80001)
            if err1:
                self.sig_failure.emit(f"接收响应失败: {err1}")
                return
            if resp1.header.status_code != 401:
                self.sig_failure.emit(
                    f"登录第一步响应状态码异常: {resp1.header.status_code}，期望 401"
                )
                return

            # 解析 401 响应中的 nonce
            auth_data = self._parse_auth_challenge(resp1.xml_body)
            self.sig_stage.emit("收到认证挑战，正在计算响应...")

            # ==== 登录第二步：发送带 Digest Authorization 的请求 ====
            uri = f"{self.host}:{self.port}"
            xml_step2 = build_login_step2_xml(
                username=self.username,
                realm=auth_data["realm"],
                nonce=auth_data["nonce"],
                uri=uri,
                password=self.password,
            )
            msg_step2 = ProtocolMessage(
                ProtocolHeader(protocol_num=80000),
                xml_step2,
            )
            sock.send_message(msg_step2)
            self.sig_stage.emit("发送认证响应，等待登录结果...")

            resp2, err2 = sock.recv_until_response(80001)
            if err2:
                self.sig_failure.emit(f"接收登录响应失败: {err2}")
                return

            if resp2.header.status_code != 200:
                # 尝试从响应中提取错误信息
                err_msg = self._parse_error_message(resp2.xml_body)
                self.sig_failure.emit(f"登录失败 (状态码 {resp2.header.status_code}): {err_msg}")
                return

            # ==== 解析登录成功响应 ====
            session = self._parse_login_response(resp2.xml_body)
            self.sig_stage.emit(f"登录成功！用户ID={session.user_id}，角色={session.role}")
            self.sig_success.emit(session, sock)

        except Exception as e:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass
            self.sig_failure.emit(f"异常错误: {e}")

    def _parse_auth_challenge(self, xml_body: str) -> dict:
        """从 401 响应中解析 realm / nonce / uri。"""
        try:
            root = ET.fromstring(xml_body)
            # 可能在 <WWW-Authenticate> 下
            auth_elem = root.find(".//WWW-Authenticate")
            if auth_elem is None:
                auth_elem = root.find(".//Authorization")
            if auth_elem is None:
                auth_elem = root  # 直接在根元素下

            realm = "Athena2"
            nonce = ""
            uri = ""

            r = auth_elem.find("realm")
            if r is not None and r.text:
                realm = r.text.strip()
            n = auth_elem.find("nonce")
            if n is not None and n.text:
                nonce = n.text.strip()
            u = auth_elem.find("uri")
            if u is not None and u.text:
                uri = u.text.strip()

            return {"realm": realm, "nonce": nonce, "uri": uri}
        except Exception:
            return {"realm": "Athena2", "nonce": "", "uri": ""}

    def _parse_login_response(self, xml_body: str) -> Session:
        """从 200 响应中解析 Session。"""
        try:
            root = ET.fromstring(xml_body)
        except ET.ParseError:
            raise ValueError(f"无法解析登录响应 XML: {xml_body[:200]}")
        session = Session()

        uid_elem = root.find(".//UserID")
        if uid_elem is not None and uid_elem.text:
            session.user_id = int(uid_elem.text.strip())
            session.user_id_str = uid_elem.text.strip()

        atoken_elem = root.find(".//AToken")
        if atoken_elem is not None:
            session.a_token = atoken_elem.text.strip() if atoken_elem.text else ""
            if atoken_elem.get("expires"):
                try:
                    session.a_token_expires = int(atoken_elem.get("expires"))
                except ValueError:
                    session.a_token_expires = 0

        role_elem = root.find(".//Role")
        if role_elem is not None and role_elem.text:
            session.role = int(role_elem.text.strip())

        machine_elem = root.find(".//Machine")
        if machine_elem is not None and machine_elem.text:
            session.machine = machine_elem.text.strip()

        return session

    def _parse_error_message(self, xml_body: str) -> str:
        """从错误响应中提取简短描述。"""
        try:
            root = ET.fromstring(xml_body)
            # 有些响应包含 <Error> 或文本内容
            for tag in ["Error", "error", "Reason"]:
                elem = root.find(f".//{tag}")
                if elem is not None and elem.text:
                    return elem.text.strip()
            return xml_body[:100].replace("\n", " ").strip()
        except Exception:
            return xml_body[:100].replace("\n", " ").strip()
