"""
CronosPre Device Tester
需要 Python 3.8+ 和 PyQt5
启动方式: python main.py
或直接双击 run.bat
"""
import sys
import os
import traceback
import faulthandler

# 开启 faulthandler，捕获 C 级崩溃（写入文件，不依赖 stderr）
crash_dir = os.path.dirname(os.path.abspath(__file__))
fault_file = os.path.join(crash_dir, "faulthandler.log")
crash_file = os.path.join(crash_dir, "crash.log")
qt_msg_file = os.path.join(crash_dir, "qt_messages.log")

# 先清空旧日志，标明本次启动
for f in [fault_file, crash_file, qt_msg_file]:
    try:
        open(f, "w", encoding="utf-8").close()
    except Exception:
        pass

_fault_file = open(fault_file, "a", encoding="utf-8")
faulthandler.enable(file=_fault_file, all_threads=True)

# Qt 消息处理器：将 Qt 警告/致命信息写入日志文件
from PyQt5.QtCore import qInstallMessageHandler
_qt_log_file = open(os.path.join(crash_dir, "qt_messages.log"), "w", encoding="utf-8")

def _qt_msg_handler(msg_type, context, msg):
    import datetime
    ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
    type_map = {0: "DEBUG", 1: "WARNING", 2: "CRITICAL", 3: "FATAL", 4: "SYSTEM"}
    t = type_map.get(int(msg_type), str(msg_type))
    _qt_log_file.write(f"[{ts}] [{t}] {msg}\n")
    _qt_log_file.flush()
qInstallMessageHandler(_qt_msg_handler)

from PyQt5.QtWidgets import QApplication
from ui.main_window import MainWindow


def _close_log_files():
    for f in [_fault_file, _qt_log_file]:
        try:
            f.close()
        except Exception:
            pass

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("CronosPre Device Tester")
    app.setStyle("Fusion")  # 现代风格

    # 全局异常捕获，写入 crash.log
    def on_exc(exc_type, exc_val, exc_tb):
        lines = traceback.format_exception(exc_type, exc_val, exc_tb)
        try:
            with open(crash_file, "w", encoding="utf-8") as f:
                f.write(f"{exc_type.__name__}: {exc_val}\n")
                f.writelines(lines)
        except Exception:
            pass
        sys.stderr.flush()
        sys.__excepthook__(exc_type, exc_val, exc_tb)

    sys.excepthook = on_exc

    window = MainWindow()
    window.show()

    ret = app.exec_()
    _close_log_files()
    sys.exit(ret)


if __name__ == "__main__":
    main()
