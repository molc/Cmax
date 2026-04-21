from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QTextEdit, QPushButton, QHBoxLayout
)
from PyQt5.QtGui import QTextCursor, QFont, QColor
from PyQt5.QtCore import Qt
from datetime import datetime


class StatusLogPanel(QWidget):
    """底部日志面板：显示滚动日志，支持按级别着色。"""

    COLOR_MAP = {
        "DEBUG":   "#888888",   # 灰色
        "INFO":    "#1E90FF",   # 蓝色
        "WARNING": "#FF8C00",   # 橙色
        "ERROR":   "#FF2222",   # 红色
        "CRITICAL":"#8B0000",  # 深红
    }
    DEFAULT_COLOR = "#000000"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 3, 5, 3)
        layout.setSpacing(3)

        # 工具栏
        toolbar = QHBoxLayout()
        self.btn_clear = QPushButton("清空日志")
        self.btn_clear.setFixedWidth(80)
        self.btn_clear.clicked.connect(self._on_clear)
        toolbar.addWidget(self.btn_clear)
        toolbar.addStretch()

        # 标题
        from PyQt5.QtWidgets import QLabel
        title = QLabel("操作日志")
        title.setStyleSheet("font-weight: bold; color: #555;")
        toolbar.addWidget(title)
        toolbar.addStretch()

        layout.addLayout(toolbar)

        # 日志文本框
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 9))
        self.log_text.setStyleSheet(
            "QTextEdit { background-color: #1E1E1E; color: #D4D4D4; "
            "border: 1px solid #3C3C3C; padding: 4px; }"
        )
        layout.addWidget(self.log_text)

    def append(self, level: str, msg: str, timestamp: bool = True):
        """
        追加一条日志。
        level: DEBUG/INFO/WARNING/ERROR
        msg: 日志内容
        timestamp: 是否显示时间戳
        """
        color = self.COLOR_MAP.get(level.upper(), self.DEFAULT_COLOR)

        prefix = ""
        if timestamp:
            ts = datetime.now().strftime("%H:%M:%S")
            prefix = f'<span style="color:#888;">[{ts}]</span> '

        level_tag = f'<span style="color:{color}; font-weight:bold;">[{level}]</span> '
        html = f"{prefix}{level_tag} {msg}"

        # 追加到末尾
        self.log_text.append(html)
        # 自动滚动到底部
        self.log_text.moveCursor(QTextCursor.End)

    def _on_clear(self):
        self.log_text.clear()
