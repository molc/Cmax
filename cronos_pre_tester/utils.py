import os
import logging
import datetime
from pathlib import Path
from PyQt5.QtCore import QObject, pyqtSignal


class LogManager:
    """
    日志管理器。
    - 同时向文件和控制台输出
    - 文件按日期自动分目录
    - UI 通过 signal 接收日志行
    """

    def __init__(self, log_dir: str = "logs"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)
        self._today = datetime.date.today()
        self._setup_logger()

    def _setup_logger(self):
        today_str = self._today.strftime("%Y-%m-%d")
        log_file = self.log_dir / f"cronos_{today_str}.log"

        # 创建 logger
        self.logger = logging.getLogger("cronos_pre")
        self.logger.setLevel(logging.DEBUG)
        self.logger.handlers.clear()

        # 文件 Handler
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")
        )

        # 控制台 Handler（收集所有日志，通过 emit 转发给 UI）
        ch = _UICaptureHandler()
        ch.setLevel(logging.DEBUG)
        ch.emit_signal.connect(self._on_log_record)

        self.logger.addHandler(fh)
        self.logger.addHandler(ch)

    def _check_rotate(self):
        """检查是否需要按日期切换日志文件。"""
        today = datetime.date.today()
        if today != self._today:
            self._today = today
            self._setup_logger()

    # --- 公开日志 API ---

    def debug(self, msg: str):
        self._check_rotate()
        self.logger.debug(msg)

    def info(self, msg: str):
        self._check_rotate()
        self.logger.info(msg)

    def warning(self, msg: str):
        self._check_rotate()
        self.logger.warning(msg)

    def error(self, msg: str):
        self._check_rotate()
        self.logger.error(msg)

    def raw(self, level: str, msg: str):
        """直接按指定级别写日志（不经过 logger 过滤）。"""
        self._check_rotate()
        getattr(self.logger, level.lower())(msg)

    # --- UI 回调 ---
    emit = None  # 由外部设置: lambda level, msg: ...

    def _on_log_record(self, level: str, msg: str):
        if self.emit:
            self.emit(level, msg)


class _UICaptureHandler(QObject, logging.Handler):
    """将日志记录转发为 Qt 信号，传递给 UI 线程。"""

    emit_signal = pyqtSignal(str, str)  # (level, message)

    def __init__(self):
        QObject.__init__(self)
        logging.Handler.__init__(self)

    def emit(self, record: logging.LogRecord):
        self.emit_signal.emit(record.levelname, record.getMessage())
