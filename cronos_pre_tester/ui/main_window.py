from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QMessageBox, QLabel, QTextEdit, QSplitter,
    QPushButton, QFrame, QTabWidget
)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QFont

from protocol import ProtocolSocket, ProtocolHeader, ProtocolMessage, Session
from .connection_bar import ConnectionBar
from .status_log_panel import StatusLogPanel
from .wall_panel import WallPanel
from login_service import LoginWorker
from utils import LogManager
from config import HEARTBEAT_INTERVAL_MS, SOCKET_TIMEOUT_SEC


class MainWindow(QMainWindow):
    """主窗口：顶部连接栏 + 日志面板（第一期仅登录测试）。"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("CronosPre Device Tester - 拼接测试")
        self.resize(900, 650)

        # 核心组件
        self.socket: ProtocolSocket = None
        self.session: Session = None
        self.login_worker: LoginWorker = None
        self.wall_panel: WallPanel = None
        self._hb_timer = QTimer()          # 心跳定时器
        self._hb_timer.timeout.connect(self._do_heartbeat)

        # 日志
        self.log_mgr = LogManager("logs")
        self.log_mgr.emit = lambda level, msg: self._on_log_emit(level, msg)

        # UI
        self._setup_ui()

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setSpacing(0)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # 顶部连接栏
        self.conn_bar = ConnectionBar()
        self.conn_bar.sig_connect.connect(self._on_connect)
        self.conn_bar.sig_disconnect.connect(self._on_disconnect_requested)
        main_layout.addWidget(self.conn_bar)

        # 分隔线
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("QFrame { border: 0; background-color: #3C3C3C; max-height: 1px; }")
        main_layout.addWidget(sep)

        # 欢迎/状态区
        self.status_area = QLabel(
            "请输入设备信息并点击「连接」按钮。\n"
            "登录成功后可进行心跳和设备信息查询测试。"
        )
        self.status_area.setAlignment(Qt.AlignCenter)
        self.status_area.setStyleSheet(
            "QLabel { color: #666; font-size: 14px; padding: 40px; background-color: #FAFAFA; }"
        )
        self.status_area.setFixedHeight(120)
        main_layout.addWidget(self.status_area)

        # 响应查看区
        resp_label = QLabel("设备响应")
        resp_label.setStyleSheet("font-weight: bold; padding: 4px 8px; color: #444; background: #F0F0F0;")
        main_layout.addWidget(resp_label)

        self.resp_text = QTextEdit()
        self.resp_text.setReadOnly(True)
        self.resp_text.setFont(QFont("Consolas", 9))
        self.resp_text.setStyleSheet(
            "QTextEdit { background-color: #1E1E1E; color: #D4D4D4; "
            "border: none; padding: 8px; }"
        )

        # 拼接测试标签页
        self.tab_widget = QTabWidget()
        self.tab_widget.addTab(self.resp_text, "响应查看")
        self.tab_widget.setStyleSheet(
            "QTabWidget::pane { border: 0; } "
            "QTabBar::tab { padding: 4px 12px; }"
        )
        main_layout.addWidget(self.tab_widget, 1)

        # 分隔线
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        sep2.setStyleSheet("QFrame { border: 0; background-color: #3C3C3C; max-height: 1px; }")
        main_layout.addWidget(sep2)

        # 底部日志面板
        self.log_panel = StatusLogPanel()
        self.log_panel.setFixedHeight(180)
        main_layout.addWidget(self.log_panel)

    def _on_log_emit(self, level: str, msg: str):
        """接收日志管理器转发的日志，写入 UI 日志面板。"""
        self.log_panel.append(level, msg)

    # ==== 连接 / 断开 ====

    def _on_connect(self, host, port, username, password):
        self.log_mgr.info(f"尝试连接到 {host}:{port}，用户: {username}")
        self.conn_bar.set_connecting()

        self.login_worker = LoginWorker(host, port, username, password)
        self.login_worker.sig_stage.connect(self._on_login_stage)
        self.login_worker.sig_success.connect(self._on_login_success)
        self.login_worker.sig_failure.connect(self._on_login_failure)
        self.login_worker.start()

    def _on_login_stage(self, msg: str):
        """接收登录各阶段消息，写入日志和状态区。"""
        self.log_mgr.info(msg)
        self.status_area.setText(msg)

    def _on_login_success(self, session: Session, sock: ProtocolSocket):
        """登录成功。"""
        self.socket = sock
        self.session = session
        self.conn_bar.set_connected(True)
        token_short = (session.a_token[:16] + "...") if session.a_token else "(无)"
        self.status_area.setText(
            f'<div style="color: green; font-size: 15px; font-weight: bold;">'
            f'登录成功！</div>'
            f'<div style="color: #555;">'
            f'用户ID: {session.user_id} &nbsp;|&nbsp; '
            f'角色: {session.role} &nbsp;|&nbsp; '
            f'设备: {session.machine or "（待查询）"}<br/>'
            f'AToken: {token_short} (有效期 {session.a_token_expires} 分钟)'
            f'</div>'
        )
        self.status_area.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.status_area.setStyleSheet(
            "QLabel { color: #333; padding: 15px; background-color: #F0FFF0; "
            "border-bottom: 1px solid #C8E6C9; }"
        )
        self.log_mgr.info(f"登录成功: UserID={session.user_id}, Role={session.role}, AToken={session.a_token[:16] if session.a_token else 'None'}...")
        self._start_heartbeat()
        # 添加拼接测试标签页
        self.wall_panel = WallPanel(session, sock, self.log_mgr)
        self.tab_widget.addTab(self.wall_panel, "拼接测试")
        self.tab_widget.setCurrentWidget(self.wall_panel)

    def _on_login_failure(self, msg: str):
        """登录失败。"""
        self.log_mgr.error(f"登录失败: {msg}")
        self.conn_bar.set_connected(False, msg)
        self.status_area.setText(
            f'<div style="color: red; font-size: 14px; font-weight: bold;">登录失败</div>'
            f'<div style="color: #555;">{msg}</div>'
        )
        self.status_area.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.status_area.setStyleSheet(
            "QLabel { color: #333; padding: 15px; background-color: #FFF0F0; "
            "border-bottom: 1px solid #FFCDD2; }"
        )

    def _on_disconnect_requested(self):
        """用户点击取消或断开按钮。"""
        if self.login_worker and self.login_worker.isRunning():
            self.log_mgr.warning("取消登录...")
            self.login_worker.terminate()
            self.login_worker = None
        self._cleanup_connection()

    def _cleanup_connection(self):
        """清理连接资源。"""
        self._stop_heartbeat()
        if self.socket:
            try:
                if self.session and self.session.is_authenticated:
                    # 发送退出请求
                    logout_xml = (
                        '<?xml version="1.0" encoding="utf-8"?>'
                        '<Message><UserID>{uid}</UserID></Message>'
                    ).format(uid=self.session.user_id_str)
                    msg = ProtocolMessage(ProtocolHeader(protocol_num=80002), logout_xml)
                    self.socket.send_message(msg)
                    self.log_mgr.debug("已发送退出请求")
            except Exception as e:
                self.log_mgr.warning(f"退出时出错: {e}")
            finally:
                try:
                    self.socket.close()
                except Exception:
                    pass
                self.socket = None
        self.session = None
        self.conn_bar.set_disconnected()
        # 移除拼接测试标签页（不调用 deleteLater，
        # 让 Qt 在 MainWindow 销毁时自然清理 wall_panel，
        # 避免 deleteLater 提前触发 destroyed 信号导致状态混乱）
        if self.wall_panel is not None:
            idx = self.tab_widget.indexOf(self.wall_panel)
            if idx >= 0:
                self.tab_widget.removeTab(idx)
            self.wall_panel = None
        self.tab_widget.setCurrentIndex(0)
        self.status_area.setText(
            "已断开连接。\n请输入设备信息并点击「连接」按钮。"
        )
        self.status_area.setAlignment(Qt.AlignCenter)
        self.status_area.setStyleSheet(
            "QLabel { color: #666; font-size: 14px; padding: 40px; background-color: #FAFAFA; }"
        )
        self.resp_text.clear()
        self.log_mgr.warning("连接已断开")

    # ==== 心跳 ====

    def _start_heartbeat(self):
        self._hb_timer.start(HEARTBEAT_INTERVAL_MS)
        self.log_mgr.debug(f"心跳定时器已启动（间隔 {HEARTBEAT_INTERVAL_MS // 1000}s）")

    def _stop_heartbeat(self):
        if self._hb_timer.isActive():
            self._hb_timer.stop()
            self.log_mgr.debug("心跳定时器已停止")

    def _do_heartbeat(self):
        """发送心跳并接收响应。"""
        if not self.socket or not self.socket.is_connected:
            self._stop_heartbeat()
            self.log_mgr.warning("心跳检测到连接已断开")
            self._cleanup_connection()
            return

        try:
            serial = self.session.next_serial()
            xml = self.session.heartbeat_xml()
            msg = ProtocolMessage(ProtocolHeader(protocol_num=80004, serial=serial), xml)
            self.socket.send_message(msg)

            resp, err = self.socket.recv_until_response(80005)
            if err:
                self.log_mgr.warning(f"心跳响应错误: {err}")
                return

            body_preview = (resp.xml_body[:80] + "...") if resp.xml_body else "(空)"
            self.log_mgr.debug(f"心跳响应: status={resp.header.status_code}, body={body_preview}")
            if resp.xml_body:
                self._display_response("心跳", resp.xml_body)

        except Exception as e:
            self.log_mgr.error(f"心跳异常: {e}")
            self._stop_heartbeat()
            self._cleanup_connection()

    def _display_response(self, title: str, xml_body: str):
        """在响应查看器中格式化显示 XML。"""
        import xml.etree.ElementTree as ET
        try:
            root = ET.fromstring(xml_body)
            import xml.dom.minidom
            pretty = xml.dom.minidom.parseString(
                ET.tostring(root, encoding="unicode")
            ).toprettyxml(indent="  ")
            # 去掉第一行 xml 声明
            lines = pretty.split("\n", 1)
            if len(lines) > 1:
                pretty = lines[1]
        except Exception:
            pretty = xml_body

        self.resp_text.append(f"=== {title} ===\n{pretty}\n")

    # ==== 窗口关闭 ====

    def closeEvent(self, event):
        # 停止心跳
        self._stop_heartbeat()

        # 停止日志回调，防止关闭后 log handler 仍向已销毁的 UI 发信号
        self.log_mgr.emit = None

        # 通知 wall_panel 停止并关闭 socket（unblock 所有阻塞的 worker），
        # 不等待 worker 线程退出——它们在 socket 关闭后自然退出，
        # Qt 的 deleteLater 机制会在之后清理。
        try:
            if self.wall_panel is not None:
                self.wall_panel._main_closing = True
                self.wall_panel._wait_all_workers()
        except Exception:
            pass

        self._cleanup_connection()
        event.accept()
