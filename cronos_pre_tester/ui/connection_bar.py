from PyQt5.QtWidgets import (
    QWidget, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QMessageBox, QApplication
)
from PyQt5.QtCore import pyqtSignal
from PyQt5.QtGui import QFont
import config


class ConnectionBar(QWidget):
    """顶部连接栏：IP、端口、账号、密码、连接按钮。"""

    sig_connect = pyqtSignal(str, int, str, str)   # host, port, username, password
    sig_disconnect = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(50)
        self._connected = False
        self._setup_ui()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 5)
        layout.setSpacing(12)

        font_bold = QFont()
        font_bold.setBold(True)

        # IP
        layout.addWidget(QLabel("IP:"))
        self.ip_edit = QLineEdit()
        self.ip_edit.setText(config.DEFAULT_HOST)
        self.ip_edit.setFixedWidth(150)
        layout.addWidget(self.ip_edit)

        # Port
        layout.addWidget(QLabel("端口:"))
        self.port_edit = QLineEdit()
        self.port_edit.setText(str(config.DEFAULT_PORT))
        self.port_edit.setFixedWidth(80)
        layout.addWidget(self.port_edit)

        # Username
        layout.addWidget(QLabel("账号:"))
        self.user_edit = QLineEdit()
        self.user_edit.setText(config.DEFAULT_USERNAME)
        self.user_edit.setFixedWidth(100)
        layout.addWidget(self.user_edit)

        # Password
        layout.addWidget(QLabel("密码:"))
        self.pwd_edit = QLineEdit()
        self.pwd_edit.setText(config.DEFAULT_PASSWORD)
        self.pwd_edit.setFixedWidth(100)
        self.pwd_edit.setEchoMode(QLineEdit.Password)
        layout.addWidget(self.pwd_edit)

        layout.addSpacing(10)

        # Connect / Disconnect button
        self.btn_connect = QPushButton("连接")
        self.btn_connect.setFont(font_bold)
        self.btn_connect.setFixedWidth(90)
        self.btn_connect.clicked.connect(self._on_connect_clicked)
        layout.addWidget(self.btn_connect)

        # Cancel button (visible only during connecting)
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setFont(font_bold)
        self.btn_cancel.setFixedWidth(70)
        self.btn_cancel.setVisible(False)
        self.btn_cancel.clicked.connect(self._on_cancel_clicked)
        layout.addWidget(self.btn_cancel)

        layout.addStretch()

        # Status label
        self.status_label = QLabel("未连接")
        self.status_label.setStyleSheet("color: gray; font-weight: bold;")
        layout.addWidget(self.status_label)

    def _on_connect_clicked(self):
        if self._connected:
            self.sig_disconnect.emit()
        else:
            host = self.ip_edit.text().strip()
            try:
                port = int(self.port_edit.text().strip())
            except ValueError:
                QMessageBox.warning(self, "输入错误", "端口号必须是整数")
                return
            username = self.user_edit.text().strip()
            password = self.pwd_edit.text()
            self.sig_connect.emit(host, port, username, password)

    def _on_cancel_clicked(self):
        self.sig_disconnect.emit()

    def set_connecting(self):
        """显示正在连接状态。"""
        self.btn_connect.setVisible(False)
        self.btn_cancel.setVisible(True)
        self._set_inputs_enabled(False)
        self.status_label.setText("正在连接...")
        self.status_label.setStyleSheet("color: orange; font-weight: bold;")
        QApplication.processEvents()

    def _set_inputs_enabled(self, enabled: bool):
        self.ip_edit.setEnabled(enabled)
        self.port_edit.setEnabled(enabled)
        self.user_edit.setEnabled(enabled)
        self.pwd_edit.setEnabled(enabled)

    def set_connected(self, success: bool, msg: str = ""):
        """设置连接成功或失败后的 UI 状态。"""
        self.btn_connect.setVisible(True)
        self.btn_cancel.setVisible(False)
        self._set_inputs_enabled(not success)

        if success:
            self._connected = True
            self.btn_connect.setText("断开")
            self.status_label.setText("已连接")
            self.status_label.setStyleSheet("color: green; font-weight: bold;")
        else:
            self._connected = False
            self.status_label.setText(f"连接失败: {msg}")
            self.status_label.setStyleSheet("color: red; font-weight: bold;")
            QApplication.processEvents()

    def set_disconnected(self):
        """设置断开连接后的 UI 状态。"""
        self._connected = False
        self.btn_connect.setVisible(True)
        self.btn_connect.setText("连接")
        self.btn_cancel.setVisible(False)
        self._set_inputs_enabled(True)
        self.status_label.setText("未连接")
        self.status_label.setStyleSheet("color: gray; font-weight: bold;")
