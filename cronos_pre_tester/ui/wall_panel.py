"""
拼接墙测试面板。
步进开窗：多屏组同时开窗，固定位置，关旧开新，信号源依次循环。
"""
import itertools
import random as _random_module
import xml.etree.ElementTree as ET

# 为每个使用 random 的线程创建独立实例，避免全局 random 锁竞争
_rnd = _random_module.Random()
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QGroupBox, QMessageBox, QSpinBox, QCheckBox,
    QTextEdit, QTabWidget
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
from protocol import ProtocolSocket, ProtocolHeader, ProtocolMessage
import config
from plugins.wall_plugin import (
    SignalSourcePlugin, ScreenGroupPlugin,
    OpenWindowPlugin, CloseWindowPlugin,
    QueryWindowsPlugin, CloseAllWindowsPlugin,
    BkSetPlugin, BkListPlugin,
    OsdMoveQueryPlugin, OsdMoveSetPlugin,
)


class WallWorker(QThread):
    """后台线程执行协议请求。"""
    sig_done = pyqtSignal(dict)
    sig_error = pyqtSignal(str)

    def __init__(self, session, socket, proto, request_xml, diag=False):
        super().__init__()
        self.session = session
        self.socket = socket
        self.proto = proto
        self.request_xml = request_xml
        self.diag = diag

    def run(self):
        try:
            serial = self.session.next_serial()
            msg = ProtocolMessage(
                ProtocolHeader(protocol_num=self.proto, serial=serial),
                self.request_xml
            )
            self.socket.send_message(msg)
            resp, err = self.socket.recv_until_response(
                self.proto + 1, _diag=self.diag
            )
            if err:
                self.sig_error.emit(err)
                return
            # 诊断：打印原始 header 字段
            import os, threading
            if self.diag:
                diag_file = os.path.join(os.path.dirname(__file__), "..", "diag.log")
                with open(diag_file, "a", encoding="utf-8") as f:
                    tid = threading.get_ident()
                    h = resp.header
                    body_preview = (resp.xml_body or "")[:100].replace("\n", " ")
                    f.write(f"[{tid}] proto={h.protocol_num} status={h.status_code} body_len={len(resp.xml_body or '')} body='{body_preview}'\n")
            self.sig_done.emit({
                "proto": self.proto,
                "status": resp.header.status_code,
                "xml": resp.xml_body,
            })
        except Exception as e:
            self.sig_error.emit(str(e))


class ProtoWorker(QThread):
    """
    通用的单协议请求后台线程。
    sig_done(screen_id, status, xml_body)
    sig_error(screen_id, error_msg)
    sig_finished()  — 所有请求完成
    """
    sig_done = pyqtSignal(str, int, str)      # screen_id, status, xml
    sig_error = pyqtSignal(str, str)           # screen_id, error
    sig_finished = pyqtSignal()

    def __init__(self, session, socket, requests, parent=None):
        """
        requests: list of (screen_id, proto_num, request_xml)
        """
        super().__init__(parent)
        self.session = session
        self.socket = socket
        self.requests = requests
        self._stop = False

    def run(self):
        try:
            for screen_id, proto_num, xml in self.requests:
                if self._stop:
                    break
                try:
                    serial = self.session.next_serial()
                    msg = ProtocolMessage(ProtocolHeader(protocol_num=proto_num, serial=serial), xml)
                    self.socket.send_message(msg)
                    resp, err = self.socket.recv_until_response(proto_num + 1)
                    if err:
                        self.sig_error.emit(screen_id, err)
                    else:
                        self.sig_done.emit(screen_id, resp.header.status_code, resp.xml_body or "")
                except Exception as e:
                    self.sig_error.emit(screen_id, str(e))
        except Exception:
            # 防止未预期异常导致线程崩溃
            pass
        self.sig_finished.emit()


class BkCycleWorker(QThread):
    """
    底图轮询后台线程：在一个后台线程中依次设置所有屏组的底图。
    """
    sig_done = pyqtSignal()
    sig_error = pyqtSignal(str, str)   # screen_id, error
    sig_set_ok = pyqtSignal(str, int, str)  # screen_id, status, xml

    def __init__(self, session, socket, screen_tasks, parent=None):
        """
        screen_tasks: list of (screen_id, bk_id, x, y, w, h)
        """
        super().__init__(parent)
        self.session = session
        self.socket = socket
        self.screen_tasks = screen_tasks
        self._stop = False

    def run(self):
        for screen_id, bk_id, x, y, w, h in self.screen_tasks:
            if self._stop:
                break
            plugin = BkSetPlugin()
            xml = plugin.build_request_xml(
                self.session,
                screen_id=screen_id,
                bk_id=bk_id,
                enable=1,
                x=x, y=y, w=w, h=h,
            )
            try:
                serial = self.session.next_serial()
                msg = ProtocolMessage(ProtocolHeader(protocol_num=80850, serial=serial), xml)
                self.socket.send_message(msg)
                resp, err = self.socket.recv_until_response(80851)
                if err:
                    self.sig_error.emit(screen_id, err)
                else:
                    self.sig_set_ok.emit(screen_id, resp.header.status_code, resp.xml_body or "")
            except Exception as e:
                self.sig_error.emit(screen_id, str(e))
        self.sig_done.emit()


class WallPanel(QWidget):
    """
    拼接测试面板：
    - 自动加载信号源和屏组
    - 步进开窗：多屏组同时开窗，固定位置，关旧开新，信号源依次循环
    """

    def __init__(self, session, socket, log_mgr):
        super().__init__()
        self.session = session
        self.socket = socket
        self.log_mgr = log_mgr
        self._destroying = False  # 销毁中，禁止启动新 worker
        self._main_closing = False  # 主窗口关闭中，禁止启动新 worker

        self._signal_sources: list = []
        self._screen_groups: list = []
        self._active_workers: list = []
        self._all_workers: list = []
        self.destroyed.connect(self._wait_all_workers)

        # 步进状态
        self._step_running = False
        self._step_timer = QTimer()      # 统一复用，不重复创建
        self._step_timer.timeout.connect(self._do_step_cycle)
        self._step_state: dict = {}
        self._step_worker: ProtoWorker = None
        self._step_worker_busy = False
        self._step_gen: int = 0          # 代数计数器，防止旧回调清空新 worker

        # 随机开窗状态
        self._rand_running = False
        self._rand_timer = QTimer()  # 统一复用
        self._rand_timer.timeout.connect(self._do_rand_cycle)
        self._rand_state: dict = {}
        self._rand_worker: ProtoWorker = None
        self._rand_worker_busy = False
        self._rand_gen: int = 0

        # 底图轮询状态
        self._bk_running = False
        self._bk_timer = QTimer()    # 统一复用
        self._bk_timer.timeout.connect(self._do_bk_cycle)
        self._bk_state: dict = {}
        self._bk_worker: BkCycleWorker = None
        self._bk_worker_busy = False
        self._bk_gen: int = 0
        self._backdrops: list = []

        # OSD 滚动字幕状态
        self._osd_running = False
        self._osd_timer = QTimer()         # 统一复用，不重复创建
        self._osd_timer.timeout.connect(self._do_osd_cycle)
        self._osd_state: dict = {}
        self._osd_worker: ProtoWorker = None
        self._osd_gen: int = 0
        self._osd_screen_moves: dict = {}
        self._osd_res: dict = {}

        # 顺序执行状态
        self._seq_running = False
        self._seq_timer = QTimer()         # 统一复用，不重复创建
        self._seq_timer.timeout.connect(self._on_seq_duration_expired)
        self._seq_timer.setSingleShot(True)
        self._seq_order: list = []
        self._seq_idx: int = 0
        self._seq_durations: dict = {}

        self._setup_ui()
        self._load_settings()
        self._save_spin_signals()
        QTimer.singleShot(100, self._auto_load)

    def _track_worker(self, w: QThread):
        self._all_workers.append(w)
        w.finished.connect(lambda: self._untrack_worker(w))

    def _untrack_worker(self, w):
        if getattr(self, '_destroying', False):
            return
        if w in self._all_workers:
            self._all_workers.remove(w)
        # 不调用 w.deleteLater()，避免 Qt 内部 COM 交互导致 0x8001010d

    def _safe_call(self, func, *args, **kwargs):
        """防御性调用：任何异常都记录，不传播。"""
        try:
            return func(*args, **kwargs)
        except Exception as e:
            import traceback
            self.log_mgr.error(f"[安全调用] {func.__name__} 异常: {e}\n{traceback.format_exc()}")

    def _clear_step_worker(self, gen: int = -1):
        """清除 worker 并恢复 UI 状态（stop 时由 finished 信号触发）。"""
        try:
            if getattr(self, '_destroying', False):
                return
            if gen >= 0 and gen != self._step_gen:
                return
            if self._step_worker is not None:
                self._step_worker = None
            self.btn_step_start.setEnabled(True)
            self.btn_step_stop.setEnabled(False)
            self.btn_rand_start.setEnabled(True)
            self.src_idx_label.setText("—")
            self.win_size_label.setText("当前窗口: —")
            self.log_mgr.info("步进已停止")
        except Exception as e:
            import traceback
            self.log_mgr.error(f"_clear_step_worker 异常: {e}\n{traceback.format_exc()}")

    def _clear_rand_worker(self, gen: int = -1):
        try:
            if getattr(self, '_destroying', False):
                return
            if gen >= 0 and gen != self._rand_gen:
                return
            if self._rand_worker is not None:
                self._rand_worker = None
            self.btn_rand_start.setEnabled(True)
            self.btn_rand_stop.setEnabled(False)
            self.btn_step_start.setEnabled(True)
            self.rand_src_label.setText("—")
            self.log_mgr.info("随机开窗已停止")
        except Exception as e:
            import traceback
            self.log_mgr.error(f"_clear_rand_worker 异常: {e}\n{traceback.format_exc()}")

    def _clear_bk_worker(self, gen: int = -1):
        try:
            if getattr(self, '_destroying', False):
                return
            if gen >= 0 and gen != self._bk_gen:
                return
            if self._bk_worker is not None:
                self._bk_worker = None
            self.btn_bk_start.setEnabled(True)
            self.btn_bk_stop.setEnabled(False)
            self.btn_step_start.setEnabled(True)
            self.btn_rand_start.setEnabled(True)
            self.bk_idx_label.setText("—")
            self.log_mgr.info("底图轮询已停止")
        except Exception as e:
            import traceback
            self.log_mgr.error(f"_clear_bk_worker 异常: {e}\n{traceback.format_exc()}")

    def _clear_osd_worker(self, gen: int = -1):
        try:
            if getattr(self, '_destroying', False):
                return
            if gen >= 0 and gen != self._osd_gen:
                return
            if self._osd_worker is not None:
                self._osd_worker = None
            self.btn_osd_start.setEnabled(True)
            self.btn_osd_stop.setEnabled(False)
            self.btn_step_start.setEnabled(True)
            self.btn_rand_start.setEnabled(True)
            self.btn_bk_start.setEnabled(True)
            self.osd_idx_label.setText("—")
            self.log_mgr.info("OSD 字幕轮播已停止")
        except Exception as e:
            import traceback
            self.log_mgr.error(f"_clear_osd_worker 异常: {e}\n{traceback.format_exc()}")

    def _wait_all_workers(self):
        """
        在 widget 销毁前被调用（由 destroyed 信号或 closeEvent 调用）。
        顺序：1) 断开 destroyed 信号防止重入 2) 停止所有定时器
        3) 标记销毁 4) 断开所有 worker 信号 5) 清空 _active_workers
        6) 关闭 socket unblock workers 7) 等待线程退出

        策略：quit() → wait(30000) → Python GC 自然清理。
        1. 不调用 terminate() —— 会撕裂 Qt COM 状态导致 0x8001010d
        2. 不调用 deleteLater() —— wait() 返回后线程已停止，GC 清理不会触发 FATAL
        """
        # 0. 防止重复进入
        if getattr(self, '_destroying', False):
            return

        # 1. 断开 destroyed 信号，防止重入调用
        try:
            self.destroyed.disconnect(self._wait_all_workers)
        except Exception:
            pass

        # 2. 停止所有定时器，防止在关闭过程中触发
        for t in [self._step_timer, self._rand_timer,
                  self._bk_timer, self._osd_timer, self._seq_timer]:
            if t:
                t.stop()

        # 3. 标记销毁，禁止所有回调访问 UI
        self._destroying = True
        self._main_closing = True
        self._step_running = False
        self._rand_running = False
        self._bk_running = False
        self._osd_running = False
        self._seq_running = False

        # 4. 先断开所有 _active_workers 的信号，防止 widget 销毁后仍有回调触发
        for w in list(self._active_workers):
            try:
                w.sig_done.disconnect()
                w.sig_error.disconnect()
                w.sig_finished.disconnect()
                w.finished.disconnect()
            except Exception:
                pass
        self._active_workers.clear()

        # 5. 关闭 socket：unblock 所有阻塞在 recv() 的 worker
        #   注意：必须在所有信号断开之后、quit() 之前关闭 socket
        if self.socket:
            try:
                self.socket.close()
            except Exception:
                pass

        # 6. 停止所有已命名 worker：告知停止，quit() 让 Qt 事件循环退出，wait() 等待线程结束
        for attr in ['_step_worker', '_rand_worker', '_bk_worker', '_osd_worker']:
            w = getattr(self, attr, None)
            if w:
                if hasattr(w, '_stop'):
                    w._stop = True
                if w.isRunning():
                    w.quit()
                    w.wait(30000)
                setattr(self, attr, None)

        for w in list(self._all_workers):
            if hasattr(w, '_stop'):
                w._stop = True
            if w.isRunning():
                w.quit()
                w.wait(30000)
        self._all_workers.clear()

        # 7. 断开 stop 操作中直接创建的 ProtoWorker 的信号
        for attr in ['_step_stop_worker', '_rand_stop_worker']:
            w = getattr(self, attr, None)
            if w:
                try:
                    w.sig_done.disconnect()
                    w.sig_error.disconnect()
                    w.sig_finished.disconnect()
                    w.finished.disconnect()
                except Exception:
                    pass
                if hasattr(w, '_stop'):
                    w._stop = True
                if w.isRunning():
                    w.quit()
                    w.wait(30000)
                setattr(self, attr, None)

        # 8. 扫描所有实例属性，将遗漏的 ProtoWorker/BkCycleWorker 也停止
        #    （防止 stop 操作快速切换时旧 worker 被覆盖引用但仍在运行）
        for attr_name in dir(self):
            if attr_name.startswith('_') and not attr_name.startswith('__'):
                obj = getattr(self, attr_name, None)
                if isinstance(obj, QThread):
                    try:
                        obj.sig_done.disconnect()
                        obj.sig_error.disconnect()
                        obj.sig_finished.disconnect()
                        obj.finished.disconnect()
                    except Exception:
                        pass
                    if hasattr(obj, '_stop'):
                        obj._stop = True
                    if obj.isRunning():
                        obj.quit()
                        obj.wait(5000)

    def _load_settings(self):
        """从配置文件加载已保存的控件数值。"""
        s = config.load_settings()
        spinners = {
            "rand_count": self.rand_count_spin,
            "rand_w_max": self.rand_w_max_spin,
            "rand_h_max": self.rand_h_max_spin,
            "rand_dwell": self.rand_dwell_spin,
            "rand_interval": self.rand_interval_spin,
            "step_w": self.win_w_spin,
            "step_h": self.win_h_spin,
            "step_w_step": self.step_w_spin,
            "step_h_step": self.step_h_spin,
            "step_dwell": self.dwell_spin,
            "step_interval": self.step_interval_spin,
            "start_x": self.start_x_spin,
            "start_y": self.start_y_spin,
            "bk_dwell": self.bk_dwell_spin,
            "osd_dwell": self.osd_dwell_spin,
        }
        for key, spin in spinners.items():
            val = s.get(key)
            if val is not None:
                spin.setValue(int(val))

        src_text = s.get("src_range")
        if src_text:
            self.src_range_edit.setPlainText(src_text)

    def _on_chk_all_changed(self, state):
        if not self._screen_chkboxes:
            return
        checked = (state == Qt.Checked)
        for cb in self._screen_chkboxes:
            cb.blockSignals(True)
            cb.setChecked(checked)
            cb.blockSignals(False)
        if self._chk_all:
            self._save_checked_groups()

    def _on_screen_check_changed(self):
        if not self._chk_all:
            return
        checked_ids = [cb._grp_id for cb in self._screen_chkboxes if cb.isChecked()]
        self._chk_all.blockSignals(True)
        self._chk_all.setChecked(len(checked_ids) == len(self._screen_chkboxes))
        self._chk_all.blockSignals(False)
        self._save_checked_groups()

    def _save_checked_groups(self):
        checked_ids = [cb._grp_id for cb in self._screen_chkboxes if cb.isChecked()]
        data = config.load_settings()
        data["checked_groups"] = checked_ids
        config.save_settings(data)

    def _save_settings(self):
        """保存当前控件数值到配置文件。"""
        checked_ids = [cb._grp_id for cb in self._screen_chkboxes if cb.isChecked()]
        data = {
            "rand_count": self.rand_count_spin.value(),
            "rand_w_max": self.rand_w_max_spin.value(),
            "rand_h_max": self.rand_h_max_spin.value(),
            "rand_dwell": self.rand_dwell_spin.value(),
            "rand_interval": self.rand_interval_spin.value(),
            "step_w": self.win_w_spin.value(),
            "step_h": self.win_h_spin.value(),
            "step_w_step": self.step_w_spin.value(),
            "step_h_step": self.step_h_spin.value(),
            "step_dwell": self.dwell_spin.value(),
            "step_interval": self.step_interval_spin.value(),
            "start_x": self.start_x_spin.value(),
            "start_y": self.start_y_spin.value(),
            "src_range": self.src_range_edit.toPlainText().strip(),
            "checked_groups": checked_ids,
            "bk_dwell": self.bk_dwell_spin.value(),
            "osd_dwell": self.osd_dwell_spin.value(),
        }
        config.save_settings(data)

    def _save_spin_signals(self):
        """将所有 SpinBox 的 valueChanged 信号连接到自动保存。"""
        for spin in [
            self.rand_count_spin, self.rand_w_max_spin, self.rand_h_max_spin,
            self.rand_dwell_spin, self.rand_interval_spin,
            self.win_w_spin, self.win_h_spin,
            self.step_w_spin, self.step_h_spin,
            self.dwell_spin, self.step_interval_spin,
            self.start_x_spin, self.start_y_spin,
            self.bk_dwell_spin, self.osd_dwell_spin,
        ]:
            spin.valueChanged.connect(self._save_settings)
        self.src_range_edit.textChanged.connect(self._save_settings)

    def _auto_load(self):
        if getattr(self, '_destroying', False) or getattr(self, '_main_closing', False):
            return
        # 清空诊断日志
        import os
        diag_path = os.path.join(os.path.dirname(__file__), "..", "diag.log")
        try:
            open(diag_path, "w", encoding="utf-8").close()
        except Exception:
            pass
        self.log_mgr.info("自动加载信号源 (80400)...")
        self._do_query_proto(80400, plugin=SignalSourcePlugin(),
                             on_done=lambda: self._auto_load_2())

    def _auto_load_2(self):
        if getattr(self, '_destroying', False) or getattr(self, '_main_closing', False):
            return
        self.log_mgr.info("自动加载屏组 (80800)...")
        self._do_query_proto(80800, plugin=ScreenGroupPlugin(),
                             on_done=lambda: self._auto_load_3())

    def _auto_load_3(self):
        if getattr(self, '_destroying', False) or getattr(self, '_main_closing', False):
            return
        self.log_mgr.info("自动加载底图列表 (80842)...")
        # 清空诊断日志
        import os
        diag_path = os.path.join(os.path.dirname(__file__), "..", "diag.log")
        try:
            open(diag_path, "w", encoding="utf-8").close()
        except Exception:
            pass
        self._do_query_proto(80842, request_xml=BkListPlugin().build_request_xml(self.session), diag=True)

    # ===================================================================
    # UI 布局
    # ===================================================================

    def _setup_ui(self):
        main_layout = QHBoxLayout(self)
        main_layout.setSpacing(8)
        main_layout.setContentsMargins(8, 8, 8, 8)

        # --- 左侧：屏组列表 ---
        left = QVBoxLayout()
        left.setSpacing(4)

        grp_box = QGroupBox("屏组列表")
        grp_layout = QVBoxLayout(grp_box)
        grp_layout.setContentsMargins(4, 4, 4, 4)

        self._screen_chkboxes: list[QCheckBox] = []
        self.grp_list_widget = QWidget()
        grp_vbox = QVBoxLayout(self.grp_list_widget)
        grp_vbox.setSpacing(2)
        grp_vbox.setContentsMargins(0, 0, 0, 0)
        grp_layout.addWidget(self.grp_list_widget)

        self.screen_info_label = QLabel("（等待加载屏组...）")
        self.screen_info_label.setStyleSheet("color: #666; font-size: 12px;")
        self.screen_info_label.setWordWrap(True)
        grp_layout.addWidget(self.screen_info_label)

        left.addWidget(grp_box)
        left.addStretch(1)

        # --- 右侧：分页 ---
        self._right_tabs = QTabWidget()

        # ---- 标签1：步进开窗 ----
        step_page = QWidget()
        step_page_layout = QVBoxLayout(step_page)
        step_page_layout.setSpacing(8)

        # 信号源范围
        src_box = QGroupBox("信号源设置")
        src_layout = QHBoxLayout(src_box)
        src_layout.setContentsMargins(4, 4, 4, 4)
        src_layout.addWidget(QLabel("信号源（范围如 0-3，或列表如 0,1,2,3）:"))
        self.src_range_edit = QTextEdit()
        self.src_range_edit.setPlaceholderText("例如: 0-3  或  0,1,2,3")
        self.src_range_edit.setFixedHeight(36)
        src_layout.addWidget(self.src_range_edit, 1)
        step_page_layout.addWidget(src_box)

        # 步进参数
        step_box = QGroupBox("步进参数")
        step_layout = QGridLayout(step_box)

        step_layout.addWidget(QLabel("起始 X:"), 0, 0)
        self.start_x_spin = QSpinBox()
        self.start_x_spin.setRange(0, 99999)
        self.start_x_spin.setValue(0)
        self.start_x_spin.setFixedWidth(80)
        step_layout.addWidget(self.start_x_spin, 0, 1)

        step_layout.addWidget(QLabel("起始 Y:"), 0, 2)
        self.start_y_spin = QSpinBox()
        self.start_y_spin.setRange(0, 99999)
        self.start_y_spin.setValue(0)
        self.start_y_spin.setFixedWidth(80)
        step_layout.addWidget(self.start_y_spin, 0, 3)

        step_layout.addWidget(QLabel("初始窗口宽:"), 0, 4)
        self.win_w_spin = QSpinBox()
        self.win_w_spin.setRange(100, 99999)
        self.win_w_spin.setValue(100)
        self.win_w_spin.setFixedWidth(80)
        step_layout.addWidget(self.win_w_spin, 0, 5)

        step_layout.addWidget(QLabel("高:"), 0, 6)
        self.win_h_spin = QSpinBox()
        self.win_h_spin.setRange(100, 99999)
        self.win_h_spin.setValue(100)
        self.win_h_spin.setFixedWidth(80)
        step_layout.addWidget(self.win_h_spin, 0, 7)

        step_layout.addWidget(QLabel("宽步进:"), 1, 0)
        self.step_w_spin = QSpinBox()
        self.step_w_spin.setRange(0, 99999)
        self.step_w_spin.setValue(100)
        self.step_w_spin.setFixedWidth(80)
        step_layout.addWidget(self.step_w_spin, 1, 1)

        step_layout.addWidget(QLabel("高步进:"), 1, 2)
        self.step_h_spin = QSpinBox()
        self.step_h_spin.setRange(0, 99999)
        self.step_h_spin.setValue(100)
        self.step_h_spin.setFixedWidth(80)
        step_layout.addWidget(self.step_h_spin, 1, 3)

        step_layout.addWidget(QLabel("停留时间(ms):"), 1, 4)
        self.dwell_spin = QSpinBox()
        self.dwell_spin.setRange(0, 60000)
        self.dwell_spin.setValue(1000)
        self.dwell_spin.setFixedWidth(80)
        step_layout.addWidget(self.dwell_spin, 1, 5)

        step_layout.addWidget(QLabel("间隔(ms):"), 1, 6)
        self.step_interval_spin = QSpinBox()
        self.step_interval_spin.setRange(100, 10000)
        self.step_interval_spin.setValue(500)
        self.step_interval_spin.setFixedWidth(80)
        step_layout.addWidget(self.step_interval_spin, 1, 7)

        step_layout.addWidget(QLabel("信号源索引:"), 2, 0)
        self.src_idx_label = QLabel("—")
        self.src_idx_label.setStyleSheet("color: #333; font-weight: bold;")
        step_layout.addWidget(self.src_idx_label, 2, 1, 1, 3)

        self.win_size_label = QLabel("当前窗口: —")
        self.win_size_label.setStyleSheet("color: #555;")
        step_layout.addWidget(self.win_size_label, 2, 4, 1, 4)

        btn_row = QHBoxLayout()
        self.btn_step_start = QPushButton("开始步进")
        self.btn_step_start.setFixedWidth(100)
        self.btn_step_start.clicked.connect(self._on_step_start)
        btn_row.addWidget(self.btn_step_start)

        self.btn_step_stop = QPushButton("停止")
        self.btn_step_stop.setFixedWidth(80)
        self.btn_step_stop.setEnabled(False)
        self.btn_step_stop.clicked.connect(self._on_step_stop)
        btn_row.addWidget(self.btn_step_stop)

        self.btn_close_selected = QPushButton("关闭选中窗口")
        self.btn_close_selected.clicked.connect(self._on_close_selected)
        self.btn_close_selected.setEnabled(False)
        btn_row.addWidget(self.btn_close_selected)

        btn_row.addStretch()
        step_layout.addLayout(btn_row, 3, 0, 1, 8)

        step_page_layout.addWidget(step_box)
        step_page_layout.addStretch(1)

        # ---- 标签2：随机开窗 ----
        rand_page = QWidget()
        rand_page_layout = QVBoxLayout(rand_page)
        rand_page_layout.setSpacing(8)

        rand_box = QGroupBox("随机开窗")
        rand_layout = QGridLayout(rand_box)

        rand_layout.addWidget(QLabel("窗口数:"), 0, 0)
        self.rand_count_spin = QSpinBox()
        self.rand_count_spin.setRange(1, 100)
        self.rand_count_spin.setValue(1)
        self.rand_count_spin.setFixedWidth(80)
        rand_layout.addWidget(self.rand_count_spin, 0, 1)

        rand_layout.addWidget(QLabel("宽最大值(px):"), 0, 2)
        self.rand_w_max_spin = QSpinBox()
        self.rand_w_max_spin.setRange(100, 99999)
        self.rand_w_max_spin.setValue(640)
        self.rand_w_max_spin.setFixedWidth(80)
        rand_layout.addWidget(self.rand_w_max_spin, 0, 3)

        rand_layout.addWidget(QLabel("高最大值(px):"), 0, 4)
        self.rand_h_max_spin = QSpinBox()
        self.rand_h_max_spin.setRange(100, 99999)
        self.rand_h_max_spin.setValue(480)
        self.rand_h_max_spin.setFixedWidth(80)
        rand_layout.addWidget(self.rand_h_max_spin, 0, 5)

        rand_layout.addWidget(QLabel("停留时间(ms):"), 1, 0)
        self.rand_dwell_spin = QSpinBox()
        self.rand_dwell_spin.setRange(0, 60000)
        self.rand_dwell_spin.setValue(1000)
        self.rand_dwell_spin.setFixedWidth(80)
        rand_layout.addWidget(self.rand_dwell_spin, 1, 1)

        rand_layout.addWidget(QLabel("间隔(ms):"), 1, 2)
        self.rand_interval_spin = QSpinBox()
        self.rand_interval_spin.setRange(100, 10000)
        self.rand_interval_spin.setValue(500)
        self.rand_interval_spin.setFixedWidth(80)
        rand_layout.addWidget(self.rand_interval_spin, 1, 3)

        rand_layout.addWidget(QLabel("信号源索引:"), 1, 4)
        self.rand_src_label = QLabel("—")
        self.rand_src_label.setStyleSheet("color: #333; font-weight: bold;")
        rand_layout.addWidget(self.rand_src_label, 1, 5, 1, 3)

        rand_btn_row = QHBoxLayout()
        self.btn_rand_start = QPushButton("开始随机")
        self.btn_rand_start.setFixedWidth(100)
        self.btn_rand_start.clicked.connect(self._on_rand_start)
        rand_btn_row.addWidget(self.btn_rand_start)

        self.btn_rand_stop = QPushButton("停止")
        self.btn_rand_stop.setFixedWidth(80)
        self.btn_rand_stop.setEnabled(False)
        self.btn_rand_stop.clicked.connect(self._on_rand_stop)
        rand_btn_row.addWidget(self.btn_rand_stop)

        rand_btn_row.addStretch()
        rand_layout.addLayout(rand_btn_row, 2, 0, 1, 8)

        rand_page_layout.addWidget(rand_box)
        rand_page_layout.addStretch(1)

        # ---- 标签3：底图轮询 ----
        bk_page = QWidget()
        bk_page_layout = QVBoxLayout(bk_page)
        bk_page_layout.setSpacing(8)

        bk_box = QGroupBox("底图轮询")
        bk_layout = QGridLayout(bk_box)

        bk_layout.addWidget(QLabel("停留时间(ms):"), 0, 0)
        self.bk_dwell_spin = QSpinBox()
        self.bk_dwell_spin.setRange(100, 60000)
        self.bk_dwell_spin.setValue(3000)
        self.bk_dwell_spin.setFixedWidth(80)
        bk_layout.addWidget(self.bk_dwell_spin, 0, 1)

        self.bk_status_label = QLabel("（底图加载中...）")
        self.bk_status_label.setStyleSheet("color: #666; font-size: 12px;")
        bk_layout.addWidget(self.bk_status_label, 0, 2, 1, 5)

        self.bk_table = QTableWidget()
        self.bk_table.setColumnCount(4)
        self.bk_table.setHorizontalHeaderLabels(["底图ID", "名称", "宽", "高"])
        self.bk_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.bk_table.setMaximumHeight(100)
        self.bk_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.bk_table.setEditTriggers(QTableWidget.NoEditTriggers)
        bk_layout.addWidget(self.bk_table, 1, 0, 1, 7)

        bk_layout.addWidget(QLabel("当前底图:"), 2, 0)
        self.bk_idx_label = QLabel("—")
        self.bk_idx_label.setStyleSheet("color: #333; font-weight: bold;")
        bk_layout.addWidget(self.bk_idx_label, 2, 1)

        bk_btn_row = QHBoxLayout()
        self.btn_bk_start = QPushButton("开始轮询")
        self.btn_bk_start.setFixedWidth(100)
        self.btn_bk_start.clicked.connect(self._on_bk_start)
        bk_btn_row.addWidget(self.btn_bk_start)

        self.btn_bk_stop = QPushButton("停止")
        self.btn_bk_stop.setFixedWidth(80)
        self.btn_bk_stop.setEnabled(False)
        self.btn_bk_stop.clicked.connect(self._on_bk_stop)
        bk_btn_row.addWidget(self.btn_bk_stop)

        bk_btn_row.addStretch()
        bk_layout.addLayout(bk_btn_row, 2, 2, 1, 5)

        bk_page_layout.addWidget(bk_box)
        bk_page_layout.addStretch(1)

        # ---- 标签4：OSD 滚动字幕 ----
        osd_page = QWidget()
        osd_page_layout = QVBoxLayout(osd_page)
        osd_page_layout.setSpacing(8)

        osd_box = QGroupBox("OSD 滚动字幕")
        osd_layout = QGridLayout(osd_box)

        osd_layout.addWidget(QLabel("停留时间(ms):"), 0, 0)
        self.osd_dwell_spin = QSpinBox()
        self.osd_dwell_spin.setRange(500, 60000)
        self.osd_dwell_spin.setValue(3000)
        self.osd_dwell_spin.setFixedWidth(80)
        osd_layout.addWidget(self.osd_dwell_spin, 0, 1)

        self.btn_query_osd = QPushButton("查询字幕列表")
        self.btn_query_osd.setFixedWidth(120)
        self.btn_query_osd.clicked.connect(self._on_query_osd_moves)
        osd_layout.addWidget(self.btn_query_osd, 0, 2)

        self.osd_status_label = QLabel("（请先查询字幕列表）")
        self.osd_status_label.setStyleSheet("color: #666; font-size: 12px;")
        osd_layout.addWidget(self.osd_status_label, 0, 3, 1, 4)

        self.osd_table = QTableWidget()
        self.osd_table.setColumnCount(3)
        self.osd_table.setHorizontalHeaderLabels(["屏组", "MoveID", "字幕名称"])
        self.osd_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.osd_table.setMaximumHeight(100)
        self.osd_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.osd_table.setEditTriggers(QTableWidget.NoEditTriggers)
        osd_layout.addWidget(self.osd_table, 1, 0, 1, 7)

        osd_layout.addWidget(QLabel("当前字幕:"), 2, 0)
        self.osd_idx_label = QLabel("—")
        self.osd_idx_label.setStyleSheet("color: #333; font-weight: bold;")
        osd_layout.addWidget(self.osd_idx_label, 2, 1)

        osd_btn_row = QHBoxLayout()
        self.btn_osd_start = QPushButton("开始轮播")
        self.btn_osd_start.setFixedWidth(100)
        self.btn_osd_start.clicked.connect(self._on_osd_start)
        osd_btn_row.addWidget(self.btn_osd_start)

        self.btn_osd_stop = QPushButton("停止")
        self.btn_osd_stop.setFixedWidth(80)
        self.btn_osd_stop.setEnabled(False)
        self.btn_osd_stop.clicked.connect(self._on_osd_stop)
        osd_btn_row.addWidget(self.btn_osd_stop)

        osd_btn_row.addStretch()
        osd_layout.addLayout(osd_btn_row, 2, 2, 1, 5)

        osd_page_layout.addWidget(osd_box)
        osd_page_layout.addStretch(1)

        # ---- 标签5：顺序执行 ----
        seq_page = QWidget()
        seq_page_layout = QVBoxLayout(seq_page)
        seq_page_layout.setSpacing(8)

        seq_box = QGroupBox("执行顺序（勾选启用的模块，可调整顺序）")
        seq_layout = QVBoxLayout(seq_box)
        seq_layout.setSpacing(6)

        # 模块定义
        self._seq_modules = [
            {"name": "step",    "label": "步进开窗",  "enabled": True,  "duration": 30000},
            {"name": "rand",    "label": "随机开窗",  "enabled": True,  "duration": 30000},
            {"name": "bk",      "label": "底图轮询",  "enabled": True,  "duration": 30000},
            {"name": "osd",     "label": "OSD字幕",   "enabled": True,  "duration": 30000},
        ]

        # 表头
        hdr_layout = QHBoxLayout()
        hdr_layout.setSpacing(4)
        hdr_layout.addWidget(QLabel("  #"), 0)
        hdr_layout.addWidget(QLabel("模块"), 0)
        hdr_layout.addWidget(QLabel("运行时长(s)"), 0)
        hdr_layout.addStretch(1)
        seq_layout.addLayout(hdr_layout)

        self._seq_rows: list = []  # {chk, label, spin}
        for i, mod in enumerate(self._seq_modules):
            row_layout = QHBoxLayout()
            row_layout.setSpacing(4)

            chk = QCheckBox()
            chk.setChecked(mod["enabled"])
            chk.setFixedWidth(24)
            row_layout.addWidget(chk, 0)

            label = QLabel(mod["label"])
            label.setFixedWidth(90)
            row_layout.addWidget(label, 0)

            spin = QSpinBox()
            spin.setRange(1, 3600)
            spin.setValue(mod["duration"] // 1000)
            spin.setSuffix(" s")
            spin.setFixedWidth(90)
            row_layout.addWidget(spin, 0)

            row_layout.addStretch(1)

            self._seq_rows.append({"chk": chk, "label": label, "spin": spin})
            seq_layout.addLayout(row_layout)

        # 顺序调整按钮
        seq_btn_layout = QHBoxLayout()
        btn_seq_up = QPushButton("▲ 上移")
        btn_seq_up.setFixedWidth(80)
        btn_seq_up.clicked.connect(self._on_seq_move_up)
        seq_btn_layout.addWidget(btn_seq_up)

        btn_seq_down = QPushButton("▼ 下移")
        btn_seq_down.setFixedWidth(80)
        btn_seq_down.clicked.connect(self._on_seq_move_down)
        seq_btn_layout.addWidget(btn_seq_down)

        seq_btn_layout.addStretch()

        # 一键执行按钮
        self.btn_seq_start = QPushButton("▶ 一键执行")
        self.btn_seq_start.setFixedWidth(120)
        self.btn_seq_start.setStyleSheet("font-weight: bold; color: #fff; background-color: #2c8c3c;")
        self.btn_seq_start.clicked.connect(self._on_seq_start)
        seq_btn_layout.addWidget(self.btn_seq_start)

        self.btn_seq_stop = QPushButton("■ 停止")
        self.btn_seq_stop.setFixedWidth(80)
        self.btn_seq_stop.setEnabled(False)
        self.btn_seq_stop.clicked.connect(self._on_seq_stop)
        seq_btn_layout.addWidget(self.btn_seq_stop)

        seq_layout.addLayout(seq_btn_layout)

        # 当前状态标签
        self.seq_status_label = QLabel("就绪")
        self.seq_status_label.setStyleSheet("color: #666; font-size: 12px; padding: 4px;")
        seq_layout.addWidget(self.seq_status_label)

        seq_page_layout.addWidget(seq_box)
        seq_page_layout.addStretch(1)

        # 添加标签页
        self._right_tabs.addTab(step_page, "步进开窗")
        self._right_tabs.addTab(rand_page, "随机开窗")
        self._right_tabs.addTab(bk_page, "底图轮询")
        self._right_tabs.addTab(osd_page, "OSD字幕")
        self._right_tabs.addTab(seq_page, "顺序执行")

        # 当前窗口列表（保持在底部）
        win_group = QGroupBox("当前窗口")
        win_layout = QVBoxLayout(win_group)
        win_layout.setContentsMargins(4, 4, 4, 4)
        self.win_table = QTableWidget()
        self.win_table.setColumnCount(6)
        self.win_table.setHorizontalHeaderLabels(["窗口ID", "X0", "Y0", "X1", "Y1", "信号源"])
        self.win_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.win_table.setMaximumHeight(120)
        self.win_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.win_table.setEditTriggers(QTableWidget.NoEditTriggers)
        win_layout.addWidget(self.win_table)

        right = QVBoxLayout()
        right.setSpacing(8)
        right.addWidget(self._right_tabs, 1)
        right.addWidget(win_group)

        main_layout.addLayout(left, 0)
        main_layout.addLayout(right, 1)

    # ===================================================================
    # 通用协议请求
    # ===================================================================

    def _do_query_proto(self, proto_num: int, request_xml: str = None, plugin=None, on_done=None, diag=False):
        if getattr(self, '_destroying', False) or getattr(self, '_main_closing', False):
            return
        if request_xml is None and plugin:
            request_xml = plugin.build_request_xml(self.session)
        worker = WallWorker(self.session, self.socket, proto_num, request_xml, diag=diag)
        self._active_workers.append(worker)

        # sig_done/sig_error 各传1个参数，proto_num 通过闭包捕获
        def _proto_done_safe(result):
            if getattr(self, '_destroying', False):
                return
            if not self._active_workers:  # widget 已销毁
                return
            try:
                self._on_proto_done(result, proto_num)
            except Exception as e:
                import traceback
                self.log_mgr.error(f"[{proto_num}] _on_proto_done 异常: {e}\n{traceback.format_exc()}")
        worker.sig_done.connect(_proto_done_safe, type=Qt.QueuedConnection)

        def _proto_err_safe(err):
            if getattr(self, '_destroying', False):
                return
            if not self._active_workers:
                return
            try:
                self._on_proto_error(err, proto_num)
            except Exception as e:
                self.log_mgr.error(f"[{proto_num}] _on_proto_error 异常: {e}")
        worker.sig_error.connect(_proto_err_safe, type=Qt.QueuedConnection)

        if on_done:
            def _safe_done(result):
                if getattr(self, '_destroying', False):
                    return
                if not self._active_workers:
                    return
                try:
                    on_done(result)
                except TypeError:
                    try:
                        on_done()
                    except Exception as e:
                        import traceback
                        self.log_mgr.error(f"on_done 异常: {e}\n{traceback.format_exc()}")
                except Exception as e:
                    import traceback
                    self.log_mgr.error(f"on_done 异常: {e}\n{traceback.format_exc()}")
            worker.sig_done.connect(_safe_done, type=Qt.QueuedConnection)

            def _safe_err(err):
                if getattr(self, '_destroying', False):
                    return
                if not self._active_workers:
                    return
                self.log_mgr.error(f"[{proto_num}] 请求失败: {err}")
            worker.sig_error.connect(_safe_err, type=Qt.QueuedConnection)

        worker.finished.connect(lambda w=worker: self._cleanup_worker(w))
        worker.start()

    def _cleanup_worker(self, worker: WallWorker):
        if getattr(self, '_destroying', False):
            return
        if worker in self._active_workers:
            self._active_workers.remove(worker)
        # 不调用 worker.deleteLater()，避免 Qt 内部 COM 交互导致 0x8001010d

    def _on_proto_error(self, err: str, proto: int):
        if getattr(self, '_destroying', False):
            return
        self.log_mgr.error(f"[{proto}] 请求失败: {err}")

    def _on_proto_done(self, result: dict, proto: int):
        if getattr(self, '_destroying', False):
            return
        if not isinstance(result, dict):
            self.log_mgr.error(f"[{proto}] _on_proto_done 收到非字典 result: {type(result)}")
            return
        if "xml" not in result:
            self.log_mgr.error(f"[{proto}] 响应缺少 xml 字段: {result}")
            return
        try:
            if proto == 80400:
                self._handle_sources_response(result["xml"])
            elif proto == 80800:
                self._handle_groups_response(result["xml"])
            elif proto == 80822:
                self._handle_windows_response(result["xml"])
            elif proto in (80826, 80828):
                self._handle_simple_response(proto, result.get("status", -1), result["xml"])
            elif proto == 80842:
                self._handle_bk_list_response(result["xml"])
            elif proto == 80874:
                self._handle_osd_query_response(result["xml"])
        except Exception as e:
            import traceback
            self.log_mgr.error(f"[{proto}] 处理响应异常: {e}\n{traceback.format_exc()}")

    # ===================================================================
    # 自动加载回调
    # ===================================================================

    def _handle_sources_response(self, xml: str):
        plugin = SignalSourcePlugin()
        data = plugin.parse_response(xml)
        self._signal_sources = data.get("sources", [])
        self.log_mgr.info(f"加载到 {len(self._signal_sources)} 个信号源")

    def _handle_groups_response(self, xml: str):
        plugin = ScreenGroupPlugin()
        data = plugin.parse_response(xml)
        self._screen_groups = data.get("groups", [])
        self._populate_screen_checkboxes()
        self.log_mgr.info(f"加载到 {len(self._screen_groups)} 个屏组")

    def _populate_screen_checkboxes(self):
        # 先断开所有信号再删除，防止残留回调导致崩溃
        for cb in self._screen_chkboxes:
            try:
                cb.blockSignals(True)
                cb.stateChanged.disconnect()
            except Exception:
                pass
            cb.setParent(None)
        self._screen_chkboxes.clear()

        # 加载已保存的勾选状态
        saved = config.load_settings()
        checked_ids = set(str(x) for x in saved.get("checked_groups", []))

        vbox = self.grp_list_widget.layout()

        # 全选复选框
        self._chk_all = QCheckBox("全选")
        self._chk_all.setChecked(bool(checked_ids))
        self._chk_all.stateChanged.connect(self._on_chk_all_changed)
        vbox.addWidget(self._chk_all)

        for grp in self._screen_groups:
            gid = grp["id"]
            total_w = grp.get("total_w", "?")
            total_h = grp.get("total_h", "?")
            hnum = grp.get("hnum", "?")
            vnum = grp.get("vnum", "?")
            name = grp.get("name", "") or f"屏组{grp['id']}"

            cb = QCheckBox(f"{name} [{hnum}x{vnum}] ({total_w}x{total_h})")
            cb.setChecked(str(gid) in checked_ids)
            cb._grp_id = gid
            cb.stateChanged.connect(self._on_screen_check_changed)
            vbox.addWidget(cb)
            self._screen_chkboxes.append(cb)

        if self._screen_groups:
            lines = []
            for grp in self._screen_groups:
                lines.append(
                    f"屏组{grp['id']} - {grp.get('name', '?')}: "
                    f"规模 {grp.get('hnum','?')}x{grp.get('vnum','?')}, "
                    f"单口 {grp.get('dis_act_hsize','?')}x{grp.get('dis_act_vsize','?')}, "
                    f"总分辨率 {grp.get('total_w','?')}x{grp.get('total_h','?')}"
                )
            self.screen_info_label.setText("\n".join(lines))

    # ===================================================================
    # 步进开窗
    # ===================================================================

    def _get_selected_groups(self) -> list:
        return [
            grp for grp, cb in zip(self._screen_groups, self._screen_chkboxes)
            if cb.isChecked()
        ]

    def _parse_src_ids(self) -> list[int]:
        """解析信号源，支持 '0-3' 范围和 '0,1,2,3' 列表两种格式。"""
        text = self.src_range_edit.toPlainText().strip()
        if not text:
            return []

        if "-" in text and "," not in text:
            try:
                parts = text.split("-")
                if len(parts) == 2:
                    start = int(parts[0].strip())
                    end = int(parts[1].strip())
                    return list(range(start, end + 1))
            except ValueError:
                pass

        if "," in text:
            try:
                return [int(x.strip()) for x in text.split(",") if x.strip()]
            except ValueError:
                pass

        try:
            return [int(text)]
        except ValueError:
            return []

    def _on_step_start(self):
        if self._step_running:
            return
        # 若随机模式正在运行，先停止
        if self._rand_running:
            self._on_rand_stop()

        groups = self._get_selected_groups()
        if not groups:
            QMessageBox.warning(self, "提示", "请先选择至少一个屏组")
            return

        src_ids = self._parse_src_ids()
        if not src_ids:
            QMessageBox.warning(self, "提示", "请填写正确的信号源（如 0-3 或 0,1,2,3）")
            return

        x0 = self.start_x_spin.value()
        y0 = self.start_y_spin.value()
        win_w = self.win_w_spin.value()
        win_h = self.win_h_spin.value()
        step_w = self.step_w_spin.value()
        step_h = self.step_h_spin.value()

        # 验证窗口尺寸最小值
        if win_w < 100 or win_h < 100:
            QMessageBox.warning(self, "提示", "窗口宽高最小为 100x100")
            return

        # 验证步进为0时不会全部卡死（初始窗口已经超界）
        if step_w == 0 and step_h == 0:
            QMessageBox.warning(self, "提示", "宽步进和高步进不能同时为0（会无限循环）")
            return

        # 验证位置不超过屏组范围
        for grp in groups:
            tw = int(grp.get("total_w", 0)) or 3840
            th = int(grp.get("total_h", 0)) or 2160
            if x0 + win_w > tw or y0 + win_h > th:
                QMessageBox.warning(
                    self, "范围超限",
                    f"窗口超出屏组 {grp['id']} 范围 (最大 {tw}x{th})"
                )
                return

        self._step_running = True
        self._step_worker: ProtoWorker = None
        self._step_gen += 1
        self.btn_step_start.setEnabled(False)
        self.btn_rand_start.setEnabled(False)
        self.btn_step_stop.setEnabled(True)

        # 信号源迭代器
        src_iter = itertools.cycle(src_ids)
        # 当前信号源索引（显示用）
        self._step_src_idx = 0

        self._step_state = {
            "groups": groups,
            "src_ids": src_ids,
            "src_iter": src_iter,
            "x0": x0,
            "y0": y0,
            "cur_w": win_w,
            "cur_h": win_h,
            "step_w": step_w,
            "step_h": step_h,
            "dwell_ms": self.dwell_spin.value(),
            "interval_ms": self.step_interval_spin.value(),
            "active_wins": {},   # {screen_id: win_id}
            "_phase": "close",   # "close" -> 关窗阶段 -> "open" -> 开窗阶段 -> 循环
        }
        self.win_size_label.setText(f"当前窗口: {win_w}x{win_h}")

        # 启动定时器
        interval = self.step_interval_spin.value()
        self._step_timer.start(interval)

        self.log_mgr.info(
            f"开始步进: 屏组={[g['id'] for g in groups]}, "
            f"信号源范围={src_ids}, 位置=({x0},{y0}), "
            f"初始窗口={win_w}x{win_h}, 步进=({self.step_w_spin.value()}, {self.step_h_spin.value()}), "
            f"停留={self.dwell_spin.value()}ms, 间隔={interval}ms"
        )
        self._do_step_cycle()

    def _do_step_cycle(self):
        """
        步进定时器触发时调用。
        单定时器 + phase 标志驱动两阶段流程：
          phase='close': 关旧窗 → 进入停留 → 重新启动定时器（等待到期后开窗）
          phase='open':  开新窗 → 更新尺寸 → 重新启动定时器（下一次循环）
        """
        try:
            if getattr(self, '_destroying', False) or getattr(self, '_main_closing', False):
                return
            if not self._step_running:
                return
            if self._step_worker_busy:
                # worker 仍在运行，忽略本次 timer 触发
                return

            state = self._step_state
            if not state:
                self.log_mgr.warning("_do_step_cycle: _step_state 为空，跳过")
                return

            phase = state.get("_phase", "close")
            groups = state["groups"]
            src_iter = state["src_iter"]

            if phase == "close":
                # --- 阶段1：关旧窗（后台执行） ---
                src_id = next(src_iter)
                self._step_src_idx = (self._step_src_idx + 1) % len(state["src_ids"])
                self.src_idx_label.setText(f"{src_id} ({self._step_src_idx + 1}/{len(state['src_ids'])})")

                x0 = state["x0"]
                y0 = state["y0"]
                cur_w = state["cur_w"]
                cur_h = state["cur_h"]
                x1 = x0 + cur_w
                y1 = y0 + cur_h
                self.log_mgr.debug(f"=== 步进 close === src={src_id} pos=({x0},{y0})-({x1},{y1})")

                # 保存本次开窗参数，供 phase='open' 使用
                state["_open_src"] = src_id
                state["_open_x0"] = x0
                state["_open_y0"] = y0
                state["_open_x1"] = x1
                state["_open_y1"] = y1

                # 构建关窗请求列表（仅有关旧窗的屏组）
                close_reqs = []
                for grp in groups:
                    screen_id = grp["id"]
                    old_win_id = state["active_wins"].get(screen_id)
                    if old_win_id is not None:
                        close_plugin = CloseWindowPlugin()
                        close_xml = close_plugin.build_request_xml(
                            self.session, screen_id=screen_id, win_id=old_win_id
                        )
                        close_reqs.append((screen_id, 80828, close_xml))
                    else:
                        self.log_mgr.debug(f"[{screen_id}] 无旧窗")

                if close_reqs:
                    self._step_worker_busy = True
                    gen = self._step_gen
                    self._step_worker = ProtoWorker(self.session, self.socket, close_reqs)
                    self._step_worker.sig_done.connect(
                        lambda sid, st, xm: self.log_mgr.debug(f"[{sid}] 关窗成功 status={st}"),
                        type=Qt.QueuedConnection
                    )
                    self._step_worker.sig_error.connect(
                        lambda sid, err, _gen=gen: self._on_step_worker_error(sid, err) if _gen == self._step_gen else None,
                        type=Qt.QueuedConnection
                    )
                    self._step_worker.sig_finished.connect(
                        lambda: self._on_step_close_done(gen),
                        type=Qt.QueuedConnection
                    )
                    self._step_worker.start()
                else:
                    # 无需关窗，直接进入开窗阶段
                    self._on_step_close_done(self._step_gen)

            elif phase == "open":
                # --- 阶段2：开新窗（后台执行） ---
                state["_phase"] = "close"

                src_id = state["_open_src"]
                x0 = state["_open_x0"]
                y0 = state["_open_y0"]
                x1 = state["_open_x1"]
                y1 = state["_open_y1"]

                open_reqs = []
                for grp in groups:
                    screen_id = grp["id"]
                    open_plugin = OpenWindowPlugin()
                    open_xml = open_plugin.build_request_xml(
                        self.session,
                        screen_id=screen_id,
                        x0=x0, y0=y0, x1=x1, y1=y1,
                        bcolor="", bwidth="0",
                        src_id=str(src_id),
                    )
                    open_reqs.append((screen_id, 80826, open_xml))

                self._step_worker_busy = True
                gen = self._step_gen
                self._step_worker = ProtoWorker(self.session, self.socket, open_reqs)
                self._step_worker.sig_done.connect(
                    lambda sid, st, xm, _gen=gen: self._on_step_win_opened(sid, st, xm) if _gen == self._step_gen else None,
                    type=Qt.QueuedConnection
                )
                self._step_worker.sig_error.connect(
                    lambda sid, err, _gen=gen: self._on_step_worker_error(sid, err) if _gen == self._step_gen else None,
                    type=Qt.QueuedConnection
                )
                self._step_worker.sig_finished.connect(
                    lambda: self._on_step_open_done(gen),
                    type=Qt.QueuedConnection
                )
                self._step_worker.start()
        except Exception:
            import traceback
            self.log_mgr.error(f"_do_step_cycle 异常:\n{traceback.format_exc()}")
            self._on_step_stop()

    def _on_step_close_done(self, gen: int):
        """关窗阶段完成，进入停留 → 开窗阶段。gen 参数防止旧回调干扰。"""
        try:
            if getattr(self, '_destroying', False):
                return
            if gen != self._step_gen:
                return
            self._step_worker = None
            self._step_worker_busy = False
            if not self._step_running:
                return
            state = self._step_state
            if not state:
                return
            state["_phase"] = "open"
            dwell_ms = state["dwell_ms"]
            if dwell_ms > 0:
                self._step_timer.start(dwell_ms)
            else:
                self._do_step_cycle()
        except Exception:
            import traceback
            self.log_mgr.error(f"_on_step_close_done 异常:\n{traceback.format_exc()}")
            self._on_step_stop()

    def _on_step_win_opened(self, screen_id, status, xml_body):
        """处理单个开窗成功：记录新窗口ID。"""
        if getattr(self, '_destroying', False):
            return
        try:
            state = self._step_state
            if not state:
                return
            root = ET.fromstring(xml_body)
            win_elem = root.find(".//Win")
            new_win_id = win_elem.get("id") if win_elem is not None else "?"
            state["active_wins"][screen_id] = new_win_id
            src_id = state["_open_src"]
            self.log_mgr.info(
                f"[{screen_id}] 开窗成功 src={src_id} "
                f"pos=({state['_open_x0']},{state['_open_y0']})-({state['_open_x1']},{state['_open_y1']}) "
                f"win={new_win_id} status={status}"
            )
        except Exception:
            pass

    def _on_step_open_done(self, gen: int):
        """开窗阶段完成：更新尺寸、检查边界、启动间隔定时器。gen 参数防止旧回调干扰。"""
        try:
            if getattr(self, '_destroying', False):
                return
            if gen != self._step_gen:
                return
            self._step_worker = None
            self._step_worker_busy = False
            if not self._step_running:
                return
            state = self._step_state
            if not state:
                return
            groups = state["groups"]

            # 更新窗口尺寸
            state["cur_w"] += state["step_w"]
            state["cur_h"] += state["step_h"]
            new_x1 = state["x0"] + state["cur_w"]
            new_y1 = state["y0"] + state["cur_h"]
            self.win_size_label.setText(f"当前窗口: {state['cur_w']}x{state['cur_h']}")

            # 检查是否超出屏组范围（步进为0时不比较）
            for grp in groups:
                tw = int(grp.get("total_w", 0)) or 3840
                th = int(grp.get("total_h", 0)) or 2160
                if (state["step_w"] > 0 and new_x1 > tw) or (state["step_h"] > 0 and new_y1 > th):
                    self.log_mgr.info(
                        f"窗口 {state['cur_w']}x{state['cur_h']} 超出屏组 {grp['id']} 范围 ({tw}x{th})，自动停止"
                    )
                    self._on_step_stop()
                    return

            self._step_timer.start(state["interval_ms"])
        except Exception:
            import traceback
            self.log_mgr.error(f"_on_step_open_done 异常:\n{traceback.format_exc()}")
            self._on_step_stop()

    def _on_step_worker_error(self, sid: str, err: str):
        """步进 worker 发生错误时调用：停止步进，防止 timer 再次触发导致崩溃。"""
        if getattr(self, '_destroying', False):
            return
        self.log_mgr.warning(f"[{sid}] 步进 worker 错误: {err}，停止步进")
        self._on_step_stop()

    def _on_rand_worker_error(self, sid: str, err: str):
        """随机开窗 worker 发生错误时调用：停止随机开窗，防止 timer 再次触发导致崩溃。"""
        if getattr(self, '_destroying', False):
            return
        self.log_mgr.warning(f"[{sid}] 随机开窗 worker 错误: {err}，停止随机开窗")
        self._on_rand_stop()

    def _on_step_stop(self):
        if getattr(self, '_destroying', False):
            return
        gen = self._step_gen
        self._step_running = False
        self._step_worker_busy = False
        if self._step_timer:
            self._step_timer.stop()
        if self._step_worker:
            self._step_worker._stop = True
            self._step_worker.finished.connect(lambda: self._clear_step_worker(gen))
        else:
            # worker 已由 cycle callback 清空，此时必须直接恢复 UI
            self._clear_step_worker(gen)

        # 异步关闭所有屏组的当前窗口
        close_reqs = []
        for grp in self._step_state.get("groups", []):
            screen_id = grp["id"]
            old_win_id = self._step_state["active_wins"].get(screen_id)
            if old_win_id is not None:
                close_plugin = CloseWindowPlugin()
                close_xml = close_plugin.build_request_xml(
                    self.session, screen_id=screen_id, win_id=old_win_id
                )
                close_reqs.append((screen_id, 80828, close_xml))
        self._step_state = {}

        if close_reqs:
            self._step_stop_worker = ProtoWorker(self.session, self.socket, close_reqs)
            self._step_stop_worker.sig_finished.connect(
                lambda: QTimer.singleShot(0, self._on_step_stop_refresh),
                type=Qt.QueuedConnection
            )
            self._step_stop_worker.start()

    # ===================================================================
    # 随机开窗
    # ===================================================================

    def _on_rand_start(self):
        if self._rand_running:
            return
        if self._step_running:
            self._on_step_stop()

        groups = self._get_selected_groups()
        if not groups:
            QMessageBox.warning(self, "提示", "请先选择至少一个屏组")
            return

        src_ids = self._parse_src_ids()
        if not src_ids:
            QMessageBox.warning(self, "提示", "请填写正确的信号源（如 0-3 或 0,1,2,3）")
            return

        count = self.rand_count_spin.value()
        w_max = self.rand_w_max_spin.value()
        h_max = self.rand_h_max_spin.value()

        if w_max < 100 or h_max < 100:
            QMessageBox.warning(self, "提示", "窗口宽高最大值为 100")
            return

        # 验证屏组范围足够容纳最小窗口 100x100
        for grp in groups:
            tw = int(grp.get("total_w", 0)) or 3840
            th = int(grp.get("total_h", 0)) or 2160
            if tw < 100 or th < 100:
                QMessageBox.warning(
                    self, "范围不足",
                    f"屏组 {grp['id']} 范围 ({tw}x{th}) 过小"
                )
                return

        self._rand_running = True
        self._rand_worker: ProtoWorker = None
        self._rand_gen += 1
        self.btn_rand_start.setEnabled(False)
        self.btn_rand_stop.setEnabled(True)
        self.btn_step_start.setEnabled(False)

        src_iter = itertools.cycle(src_ids)
        self._rand_src_idx = 0

        self._rand_state = {
            "groups": groups,
            "src_ids": src_ids,
            "src_iter": src_iter,
            "count": count,
            "w_max": w_max,
            "h_max": h_max,
            "dwell_ms": self.rand_dwell_spin.value(),
            "interval_ms": self.rand_interval_spin.value(),
            "active_wins": {},
            "_phase": "close",
        }

        # 启动前先异步关闭所有选中屏组上已有的窗口
        close_reqs = []
        for g in groups:
            plugin = CloseAllWindowsPlugin()
            xml = plugin.build_request_xml(self.session, screen_id=g["id"])
            close_reqs.append((g["id"], 80824, xml))

        def _after_cleanup():
            if getattr(self, '_destroying', False):
                return
            interval = self.rand_interval_spin.value()
            self._rand_timer.start(interval)
            self._do_rand_cycle()

        if close_reqs:
            w = ProtoWorker(self.session, self.socket, close_reqs)
            w.sig_finished.connect(
                lambda: QTimer.singleShot(0, _after_cleanup),
                type=Qt.QueuedConnection
            )
            w.start()
        else:
            _after_cleanup()

        self.log_mgr.info(
            f"开始随机开窗: 屏组={[g['id'] for g in groups]}, "
            f"信号源={src_ids}, 窗口数={count}, 宽≤{w_max}, 高≤{h_max}, "
            f"停留={self.rand_dwell_spin.value()}ms, 间隔={self.rand_interval_spin.value()}ms"
        )

    def _do_rand_cycle(self):
        try:
            if getattr(self, '_destroying', False) or getattr(self, '_main_closing', False):
                return
            if not self._rand_running:
                return
            if self._rand_worker_busy:
                return

            state = self._rand_state
            if not state:
                self.log_mgr.warning("_do_rand_cycle: _rand_state 为空，跳过")
                return

            phase = state.get("_phase", "close")
            groups = state["groups"]
            src_iter = state["src_iter"]

            if phase == "close":
                # 取下一个信号源
                src_id = next(src_iter)
                self._rand_src_idx = (self._rand_src_idx + 1) % len(state["src_ids"])
                self.rand_src_label.setText(f"{src_id} ({self._rand_src_idx + 1}/{len(state['src_ids'])})")

                # 为每个屏组 × 每个窗口槽位 生成随机坐标
                windows_this_round = []
                count_per_group = state["count"]
                for grp in groups:
                    tw = int(grp.get("total_w", 0)) or 3840
                    th = int(grp.get("total_h", 0)) or 2160
                    w_max = min(state["w_max"], tw)
                    h_max = min(state["h_max"], th)
                    for i in range(count_per_group):
                        rw = _rnd.randint(100, w_max)
                        rh = _rnd.randint(100, h_max)
                        rx0 = _rnd.randint(0, max(0, tw - rw))
                        ry0 = _rnd.randint(0, max(0, th - rh))
                        rx1 = rx0 + rw
                        ry1 = ry0 + rh
                        windows_this_round.append((grp["id"], rx0, ry0, rx1, ry1))
                        self.log_mgr.debug(
                            f"随机窗口 [{grp['id']}] {i+1}/{count_per_group}: "
                            f"pos=({rx0},{ry0})-({rx1},{ry1})"
                        )

                state["_open_src"] = src_id
                state["_windows"] = windows_this_round

                # 构建关窗请求（异步执行）
                close_reqs = []
                for g in groups:
                    screen_id = g["id"]
                    old_wins = state["active_wins"].get(screen_id, [])
                    for old_id in old_wins:
                        close_plugin = CloseWindowPlugin()
                        close_xml = close_plugin.build_request_xml(
                            self.session, screen_id=screen_id, win_id=old_id
                        )
                        close_reqs.append((screen_id, 80828, close_xml))
                    state["active_wins"][screen_id] = []

                if close_reqs:
                    self._rand_worker_busy = True
                    gen = self._rand_gen
                    self._rand_worker = ProtoWorker(self.session, self.socket, close_reqs)
                    self._rand_worker.sig_error.connect(
                        lambda sid, err, _gen=gen: self._on_rand_worker_error(sid, err) if _gen == self._rand_gen else None,
                        type=Qt.QueuedConnection
                    )
                    self._rand_worker.sig_finished.connect(
                        lambda: self._on_rand_close_done(gen),
                        type=Qt.QueuedConnection
                    )
                    self._rand_worker.start()
                else:
                    self._on_rand_close_done(self._rand_gen)

            elif phase == "open":
                state["_phase"] = "close"
                src_id = state["_open_src"]
                windows = state["_windows"]

                open_reqs = []
                for (screen_id, x0, y0, x1, y1) in windows:
                    open_plugin = OpenWindowPlugin()
                    open_xml = open_plugin.build_request_xml(
                        self.session,
                        screen_id=screen_id,
                        x0=x0, y0=y0, x1=x1, y1=y1,
                        bcolor="", bwidth="0",
                        src_id=str(src_id),
                    )
                    open_reqs.append((screen_id, 80826, open_xml))

                self._rand_worker_busy = True
                gen = self._rand_gen
                self._rand_worker = ProtoWorker(self.session, self.socket, open_reqs)
                self._rand_worker.sig_done.connect(
                    lambda sid, st, xm, _gen=gen: self._on_rand_win_opened(sid, st, xm) if _gen == self._rand_gen else None,
                    type=Qt.QueuedConnection
                )
                self._rand_worker.sig_error.connect(
                    lambda sid, err, _gen=gen: self._on_rand_worker_error(sid, err) if _gen == self._rand_gen else None,
                    type=Qt.QueuedConnection
                )
                self._rand_worker.sig_finished.connect(
                    lambda: self._on_rand_open_done(gen),
                    type=Qt.QueuedConnection
                )
                self._rand_worker.start()
        except Exception:
            import traceback
            self.log_mgr.error(f"_do_rand_cycle 异常:\n{traceback.format_exc()}")
            self._on_rand_stop()

    def _on_rand_close_done(self, gen: int):
        """关窗阶段完成，进入停留 → 开窗阶段。gen 参数防止旧回调干扰。"""
        try:
            if getattr(self, '_destroying', False):
                return
            if gen != self._rand_gen:
                return
            self._rand_worker = None
            self._rand_worker_busy = False
            if not self._rand_running:
                return
            state = self._rand_state
            if not state:
                return
            state["_phase"] = "open"
            if state["dwell_ms"] > 0:
                self._rand_timer.start(state["dwell_ms"])
            else:
                self._do_rand_cycle()
        except Exception:
            import traceback
            self.log_mgr.error(f"_on_rand_close_done 异常:\n{traceback.format_exc()}")
            self._on_rand_stop()

    def _on_rand_win_opened(self, screen_id, status, xml_body):
        """处理单个开窗成功：记录新窗口ID。"""
        if getattr(self, '_destroying', False):
            return
        try:
            state = self._rand_state
            if not state:
                return
            root = ET.fromstring(xml_body)
            win_elem = root.find(".//Win")
            wid = win_elem.get("id") if win_elem is not None else "?"
            state["active_wins"].setdefault(screen_id, []).append(wid)
            self.log_mgr.info(
                f"[{screen_id}] 开窗成功 src={state['_open_src']} win={wid}"
            )
        except Exception:
            pass

    def _on_rand_open_done(self, gen: int):
        """开窗阶段完成，启动间隔定时器。gen 参数防止旧回调干扰。"""
        try:
            if getattr(self, '_destroying', False):
                return
            if gen != self._rand_gen:
                return
            self._rand_worker = None
            self._rand_worker_busy = False
            if not self._rand_running:
                return
            self._rand_timer.start(self._rand_state["interval_ms"])
        except Exception:
            import traceback
            self.log_mgr.error(f"_on_rand_open_done 异常:\n{traceback.format_exc()}")
            self._on_rand_stop()

    def _on_rand_stop_refresh(self):
        """stop 时发起异步关窗后，刷新窗口列表（带 _destroying 保护）。"""
        if getattr(self, '_destroying', False):
            return
        self._refresh_windows()

    def _on_rand_stop(self):
        if getattr(self, '_destroying', False):
            return
        gen = self._rand_gen
        self._rand_running = False
        self._rand_worker_busy = False
        if self._rand_timer:
            self._rand_timer.stop()
        if self._rand_worker:
            self._rand_worker._stop = True
            self._rand_worker.finished.connect(lambda: self._clear_rand_worker(gen))
        else:
            self._clear_rand_worker(gen)

        # 异步关闭所有屏组的所有当前窗口
        if self._rand_state:
            close_reqs = []
            for grp in self._rand_state.get("groups", []):
                screen_id = grp["id"]
                old_ids = self._rand_state["active_wins"].get(screen_id, [])
                for old_id in old_ids:
                    close_plugin = CloseWindowPlugin()
                    close_xml = close_plugin.build_request_xml(
                        self.session, screen_id=screen_id, win_id=old_id
                    )
                    close_reqs.append((screen_id, 80828, close_xml))
            if close_reqs:
                self._rand_stop_worker = ProtoWorker(self.session, self.socket, close_reqs)
                self._rand_stop_worker.sig_finished.connect(
                    lambda: QTimer.singleShot(0, self._on_rand_stop_refresh),
                    type=Qt.QueuedConnection
                )
                self._rand_stop_worker.start()

        self._rand_state = {}

    # ===================================================================
    # 底图轮询
    # ===================================================================

    def _handle_bk_list_response(self, xml: str):
        """解析底图列表响应。"""
        plugin = BkListPlugin()
        data = plugin.parse_response(xml)
        self._backdrops = data.get("backdrops", [])
        self._populate_bk_table()
        if self._backdrops:
            self.bk_status_label.setText(f"共 {len(self._backdrops)} 个底图，可开始轮询")
            self.bk_status_label.setStyleSheet("color: green; font-size: 12px;")
        else:
            self.bk_status_label.setText("未查询到底图，可开始轮询")
            self.bk_status_label.setStyleSheet("color: red; font-size: 12px;")

    def _populate_bk_table(self):
        self.bk_table.setRowCount(0)
        for bk in self._backdrops:
            row = self.bk_table.rowCount()
            self.bk_table.insertRow(row)
            self.bk_table.setItem(row, 0, QTableWidgetItem(str(bk.get("id", ""))))
            self.bk_table.setItem(row, 1, QTableWidgetItem(bk.get("name", "")))
            self.bk_table.setItem(row, 2, QTableWidgetItem(str(bk.get("w", ""))))
            self.bk_table.setItem(row, 3, QTableWidgetItem(str(bk.get("h", ""))))

    def _on_bk_start(self):
        if self._bk_running:
            return
        if self._step_running:
            self._on_step_stop()
        if self._rand_running:
            self._on_rand_stop()

        groups = self._get_selected_groups()
        if not groups:
            QMessageBox.warning(self, "提示", "请先选择至少一个屏组")
            return

        if not self._backdrops:
            QMessageBox.warning(self, "提示", "底图列表尚未加载，请稍候")
            return

        dwell = self.bk_dwell_spin.value()
        if dwell < 100:
            QMessageBox.warning(self, "提示", "停留时间最小为 100ms")
            return

        self._bk_running = True
        self._bk_worker_busy = False
        self._bk_worker: BkCycleWorker = None
        self._bk_gen += 1
        self.btn_bk_start.setEnabled(False)
        self.btn_bk_stop.setEnabled(True)
        self.btn_step_start.setEnabled(False)
        self.btn_rand_start.setEnabled(False)

        # 每个屏组独立的底图索引
        bk_indices = {g["id"]: 0 for g in groups}

        self._bk_state = {
            "groups": groups,
            "backdrops": self._backdrops,
            "bk_indices": bk_indices,
            "dwell_ms": dwell,
            "_screen_idx": 0,
        }

        self._update_bk_idx_label(groups[0]["id"], 0, len(self._backdrops))
        self._do_bk_cycle()

    def _do_bk_cycle(self):
        """底图轮询主体：依次对每个屏组设置底图，每个设置完成后等待 dwell 再设下一个。"""
        try:
            if getattr(self, '_destroying', False) or getattr(self, '_main_closing', False):
                return
            if not self._bk_running:
                return
            if self._bk_worker_busy:
                return
            state = self._bk_state
            if not state:
                self.log_mgr.warning("_do_bk_cycle: _bk_state 为空，跳过")
                return

            groups = state["groups"]
            backdrops = state["backdrops"]
            bk_indices = state["bk_indices"]

            idx = state["_screen_idx"]
            gid = groups[idx]["id"]
            bk_idx = bk_indices[gid]
            bk = backdrops[bk_idx]

            # 计算随机位置
            g = groups[idx]
            hnum = int(g.get("hnum", 1)) or 1
            vnum = int(g.get("vnum", 1)) or 1
            col = _rnd.randint(0, hnum - 1)
            row = _rnd.randint(0, vnum - 1)
            cols = _rnd.randint(1, hnum - col)
            vrows = _rnd.randint(1, vnum - row)

            self.log_mgr.debug(
                f"[{gid}] 底图 idx={bk_idx}/{len(backdrops)-1} "
                f"({bk.get('name','?')}) grid=({col},{row})+({cols},{vrows})"
            )

            # 推进该屏组的索引
            bk_indices[gid] = (bk_idx + 1) % len(backdrops)
            self._update_bk_idx_label(gid, bk_indices[gid], len(backdrops))

            # 单个后台线程设置一个屏组
            self._bk_worker_busy = True
            gen = self._bk_gen
            self._bk_worker = BkCycleWorker(
                self.session, self.socket, [(gid, bk["id"], col, row, cols, vrows)]
            )
            self._bk_worker.sig_set_ok.connect(
                lambda sid, st, xm, _gen=gen: self._on_bk_set_ok(sid, st, xm) if _gen == self._bk_gen else None,
                type=Qt.QueuedConnection
            )
            self._bk_worker.sig_error.connect(
                lambda sid, err, _gen=gen: self.log_mgr.error(f"[{sid}] 底图设置失败: {err}") if _gen == self._bk_gen else None,
                type=Qt.QueuedConnection
            )
            self._bk_worker.sig_done.connect(
                lambda: self._on_bk_one_done(gen),
                type=Qt.QueuedConnection
            )
            self._bk_worker.start()
        except Exception:
            import traceback
            self.log_mgr.error(f"_do_bk_cycle 异常:\n{traceback.format_exc()}")
            self._on_bk_stop()

    def _on_bk_one_done(self, gen: int):
        """一个屏组底图设置完成，等待 dwell 后处理下一个。gen 参数防止旧回调干扰。"""
        try:
            if getattr(self, '_destroying', False):
                return
            if gen != self._bk_gen:
                return
            self._bk_worker = None
            self._bk_worker_busy = False
            if not self._bk_running:
                return
            state = self._bk_state
            if not state:
                return
            groups = state["groups"]

            # 移动到下一个屏组
            state["_screen_idx"] = (state["_screen_idx"] + 1) % len(groups)

            # 若刚轮完一圈，重置所有屏组的索引
            if state["_screen_idx"] == 0:
                for gid in state["bk_indices"]:
                    state["bk_indices"][gid] = 0
                self._update_bk_idx_label(groups[0]["id"], 0, len(state["backdrops"]))
                self._bk_timer.start(state["dwell_ms"])
            else:
                self._bk_timer.start(state["dwell_ms"])
        except Exception:
            import traceback
            self.log_mgr.error(f"_on_bk_one_done 异常:\n{traceback.format_exc()}")
            self._on_bk_stop()

    def _on_bk_set_ok(self, screen_id, status, xml):
        if getattr(self, '_destroying', False):
            return
        self.log_mgr.info(f"[{screen_id}] 底图设置成功 status={status}")

    def _update_bk_idx_label(self, screen_id, idx, total):
        self.bk_idx_label.setText(
            f"屏组{screen_id}: {idx}/{total-1} ({self._backdrops[idx]['name'] if idx < total else '?'})"
        )

    def _on_bk_stop(self):
        if getattr(self, '_destroying', False):
            return
        gen = self._bk_gen
        self._bk_running = False
        self._bk_worker_busy = False
        if self._bk_timer:
            self._bk_timer.stop()
        if self._bk_worker:
            self._bk_worker._stop = True
            self._bk_worker.finished.connect(lambda: self._clear_bk_worker(gen))
        else:
            self._clear_bk_worker(gen)
        self._bk_state = {}

    # ===================================================================
    # 顺序执行
    # ===================================================================

    def _on_seq_move_up(self):
        """将选中的模块向上移动一行。"""
        enabled = self._get_enabled_seq_modules()
        if len(enabled) < 2:
            return
        idx = self._seq_rows.index(
            next(r for r in self._seq_rows if r["chk"].isChecked())
        )
        if idx == 0:
            return
        self._swap_seq_rows(idx, idx - 1)

    def _on_seq_move_down(self):
        """将选中的模块向下移动一行。"""
        enabled = self._get_enabled_seq_modules()
        if len(enabled) < 2:
            return
        idx = self._seq_rows.index(
            next(r for r in self._seq_rows if r["chk"].isChecked())
        )
        if idx == len(self._seq_rows) - 1:
            return
        self._swap_seq_rows(idx, idx + 1)

    def _swap_seq_rows(self, i: int, j: int):
        """交换 self._seq_rows 中第 i 行和第 j 行的位置。"""
        row_i = self._seq_rows[i]
        row_j = self._seq_rows[j]

        # 交换 checkbox 状态
        tmp_chk = row_i["chk"].isChecked()
        row_i["chk"].setChecked(row_j["chk"].isChecked())
        row_j["chk"].setChecked(tmp_chk)

        # 交换 spin 值
        tmp_val = row_i["spin"].value()
        row_i["spin"].setValue(row_j["spin"].value())
        row_j["spin"].setValue(tmp_val)

        # 交换标签文字
        tmp_label = row_i["label"].text()
        row_i["label"].setText(row_j["label"].text())
        row_j["label"].setText(tmp_label)

    def _get_enabled_seq_modules(self) -> list:
        """返回当前启用的模块列表，按 UI 顺序。"""
        enabled = []
        for row in self._seq_rows:
            if row["chk"].isChecked():
                label = row["label"].text()
                duration_ms = row["spin"].value() * 1000
                enabled.append({"label": label, "duration_ms": duration_ms})
        return enabled

    def _on_seq_start(self):
        """一键执行：按顺序依次运行各模块。"""
        if self._seq_running:
            return

        groups = self._get_selected_groups()
        if not groups:
            QMessageBox.warning(self, "提示", "请先选择至少一个屏组")
            return

        enabled = self._get_enabled_seq_modules()
        if not enabled:
            QMessageBox.warning(self, "提示", "请至少勾选一个模块")
            return

        # 停止所有正在运行的模块
        if self._step_running:
            self._on_step_stop()
        if self._rand_running:
            self._on_rand_stop()
        if self._bk_running:
            self._on_bk_stop()
        if self._osd_running:
            self._on_osd_stop()

        self._seq_running = True
        self._seq_idx = 0
        self.btn_seq_start.setEnabled(False)
        self.btn_seq_stop.setEnabled(True)
        self._seq_status("正在执行: —")

        self._do_next_seq_module()

    def _do_next_seq_module(self):
        """执行顺序中的下一个模块。"""
        if not self._seq_running:
            return

        enabled = self._get_enabled_seq_modules()
        if self._seq_idx >= len(enabled):
            # 所有模块执行完毕
            self._on_seq_finished()
            return

        mod = enabled[self._seq_idx]
        self._seq_status(f"正在执行 [{self._seq_idx + 1}/{len(enabled)}]: {mod['label']}")
        self.log_mgr.info(f"顺序执行 [{self._seq_idx + 1}/{len(enabled)}] {mod['label']}，运行时长 {mod['duration_ms'] / 1000:.0f}s")

        # 启动对应模块
        name_map = {
            "步进开窗": "_on_step_start",
            "随机开窗": "_on_rand_start",
            "底图轮询": "_on_bk_start",
            "OSD字幕":  "_on_osd_start",
        }
        method_name = name_map.get(mod["label"])
        if method_name and hasattr(self, method_name):
            getattr(self, method_name)()

        # 启动时长定时器
        self._seq_timer.start(mod["duration_ms"])

    def _on_seq_duration_expired(self):
        """当前模块运行时长到期，停止并执行下一个。"""
        if not self._seq_running:
            return

        # 停止当前模块
        if self._step_running:
            self._on_step_stop()
        elif self._rand_running:
            self._on_rand_stop()
        elif self._bk_running:
            self._on_bk_stop()
        elif self._osd_running:
            self._on_osd_stop()

        self._seq_idx += 1
        self._do_next_seq_module()

    def _on_seq_finished(self):
        """所有模块执行完毕。"""
        self._seq_running = False
        self._seq_idx = 0
        self.btn_seq_start.setEnabled(True)
        self.btn_seq_stop.setEnabled(False)
        self._seq_status("执行完毕")
        self.log_mgr.info("顺序执行全部完成")

    def _on_seq_stop(self):
        """手动停止顺序执行。"""
        if getattr(self, '_destroying', False):
            return
        if not self._seq_running:
            return

        self._seq_running = False
        if self._seq_timer:
            self._seq_timer.stop()

        # 停止当前运行的模块
        if self._step_running:
            self._on_step_stop()
        elif self._rand_running:
            self._on_rand_stop()
        elif self._bk_running:
            self._on_bk_stop()
        elif self._osd_running:
            self._on_osd_stop()

        self.btn_seq_start.setEnabled(True)
        self.btn_seq_stop.setEnabled(False)
        self._seq_status("已停止")
        self.log_mgr.info("顺序执行已手动停止")

    def _seq_status(self, text: str):
        self.seq_status_label.setText(text)

    # ===================================================================
    # OSD 滚动字幕
    # ===================================================================

    def _on_query_osd_moves(self):
        """查询所有选中屏组的滚动字幕列表。"""
        groups = self._get_selected_groups()
        if not groups:
            QMessageBox.warning(self, "提示", "请先选择至少一个屏组")
            return
        self.osd_status_label.setText("查询中...")
        self.osd_status_label.setStyleSheet("color: #666; font-size: 12px;")
        # 清空旧数据
        self._osd_screen_moves = {}
        self._osd_res = {}
        # 依次查询每个屏组
        self._osd_query_remaining = [g["id"] for g in groups]
        self._query_next_osd_screen()

    def _query_next_osd_screen(self):
        if not self._osd_query_remaining:
            # 所有屏组查询完毕
            self._populate_osd_table()
            total = sum(len(moves) for moves in self._osd_screen_moves.values())
            if total > 0:
                self.osd_status_label.setText(f"共 {total} 个字幕，可开始轮播")
                self.osd_status_label.setStyleSheet("color: green; font-size: 12px;")
            else:
                self.osd_status_label.setText("未查询到字幕")
                self.osd_status_label.setStyleSheet("color: red; font-size: 12px;")
            return
        screen_id = self._osd_query_remaining.pop(0)
        plugin = OsdMoveQueryPlugin()
        xml = plugin.build_request_xml(self.session, screen_id=screen_id)
        self._do_query_proto(80874, request_xml=xml,
                             on_done=lambda result=None: self._on_osd_screen_query_done(screen_id))

    def _on_osd_screen_query_done(self, result=None):
        # 结果已由 _handle_osd_query_response 填充，继续查下一个
        self._query_next_osd_screen()

    def _handle_osd_query_response(self, xml: str):
        """解析 OSD 字幕列表响应。"""
        self.log_mgr.debug(f"[OSD查询] 收到响应 XML ({len(xml)} bytes): {xml[:500]}")
        plugin = OsdMoveQueryPlugin()
        data = plugin.parse_response(xml)
        for screen in data.get("screens", []):
            sid = screen["id"]
            moves = screen.get("moves", [])
            res_w = screen.get("res_w", 1920)
            res_h = screen.get("res_h", 1080)
            # 字幕列表单独存储，分辨率存到独立字典，避免键类型混乱
            self._osd_screen_moves[sid] = moves
            self._osd_res[sid] = (res_w, res_h)
            for mv in moves:
                self.log_mgr.debug(
                    f"[OSD解析] MoveID={mv['id']} frame_count={mv.get('frame_count','')} "
                    f"dir={mv.get('direction','')} mov_step={mv.get('mov_step','')}"
                )
            self.log_mgr.info(f"[OSD查询] 屏组{sid} 解析到 {len(moves)} 个字幕: {[m['id'] for m in moves]}, 分辨率={res_w}x{res_h}")

    def _populate_osd_table(self):
        self.osd_table.setRowCount(0)
        for screen_id, moves in sorted(self._osd_screen_moves.items()):
            for mv in moves:
                row = self.osd_table.rowCount()
                self.osd_table.insertRow(row)
                self.osd_table.setItem(row, 0, QTableWidgetItem(screen_id))
                self.osd_table.setItem(row, 1, QTableWidgetItem(mv.get("id", "")))
                self.osd_table.setItem(row, 2, QTableWidgetItem(mv.get("name", "")))

    def _on_osd_start(self):
        self.log_mgr.debug("OSD _on_osd_start called")
        if self._osd_running:
            return
        if self._step_running:
            self._on_step_stop()
        if self._rand_running:
            self._on_rand_stop()
        if self._bk_running:
            self._on_bk_stop()

        groups = self._get_selected_groups()
        if not groups:
            QMessageBox.warning(self, "提示", "请先选择至少一个屏组")
            return

        if not self._osd_screen_moves:
            QMessageBox.warning(self, "提示", "请先点击「查询字幕列表」")
            return

        # 过滤掉无字幕的屏组
        groups = [
            g for g in groups
            if self._osd_screen_moves.get(g["id"])
        ]
        if not groups:
            QMessageBox.warning(self, "提示", "所有选中屏组均无字幕，请重新查询")
            return

        dwell = self.osd_dwell_spin.value()
        if dwell < 500:
            QMessageBox.warning(self, "提示", "停留时间最小为 500ms")
            return

        self._osd_running = True
        self._osd_worker: ProtoWorker = None
        self._osd_gen += 1
        self.btn_osd_start.setEnabled(False)
        self.btn_osd_stop.setEnabled(True)
        self.btn_step_start.setEnabled(False)
        self.btn_rand_start.setEnabled(False)
        self.btn_bk_start.setEnabled(False)

        # 每个屏组独立的字幕索引
        move_indices = {g["id"]: 0 for g in groups}
        self._osd_state = {
            "groups": groups,
            "move_indices": move_indices,
            "dwell_ms": dwell,
            "_screen_idx": 0,
        }

        self._update_osd_idx_label(groups[0]["id"], 0, len(self._osd_screen_moves[groups[0]["id"]]))
        try:
            self._do_osd_cycle()
        except Exception as e:
            import traceback
            self.log_mgr.error(f"OSD 轮播异常: {e}\n{traceback.format_exc()}")
            self._on_osd_stop()

    # Direction (80862): 0=左, 1=右, 2=上, 3=下, 6=静止
    # MovStep: 水平 0=1px/1=2px/2=4px/3=8px/4=16px；垂直 1=1行/2=2行
    OSD_DIRECTIONS = [2, 3, 0, 1, 6]   # 上、下、左、右、静止
    OSD_SPEEDS = [4, 2, 1]              # 快、较快、慢

    def _do_osd_cycle(self):
        """字幕轮播主体：依次对每个屏组设置字幕，设置完成后等待 dwell 再设下一个。"""
        try:
            if getattr(self, '_destroying', False) or getattr(self, '_main_closing', False):
                return
            if not self._osd_running:
                return
            self.log_mgr.debug("OSD _do_osd_cycle start")

            state = self._osd_state
            if not state:
                self.log_mgr.warning("_do_osd_cycle: _osd_state 为空，跳过")
                return

            groups = state["groups"]
            move_indices = state["move_indices"]
            sidx = state["_screen_idx"]
            gid = groups[sidx]["id"]
            moves = self._osd_screen_moves[gid]
            midx = move_indices[gid]
            mv = moves[midx]

            # 随机方向、速度和起始位置（在屏幕分辨率范围内）
            direction = _rnd.choice(self.OSD_DIRECTIONS)
            mov_step = _rnd.choice(self.OSD_SPEEDS)

            res_w, res_h = self._osd_res.get(gid, (1920, 1080))
            start_row = _rnd.randint(0, max(res_h - 100, 0))
            start_col = _rnd.randint(0, max(res_w - 100, 0))
            frame_count = mv.get("frame_count") or "2047"

            self.log_mgr.info(
                f"[{gid}] OSD 轮播 → MoveID={mv['id']} "
                f"dir={direction}(上2/下3/左0/右1/静止6) mov_step={mov_step} "
                f"pos=({start_row},{start_col}) screen={res_w}x{res_h} "
                f"frame_count={frame_count} idx={midx}/{len(moves)-1} ({mv.get('name','?'):.8s})"
            )

            plugin = OsdMoveSetPlugin()
            xml = plugin.build_request_xml(
                self.session,
                screen_id=gid,
                move_id=mv["id"],
                start_row=start_row,
                start_col=start_col,
                direction=direction,
                mov_step=mov_step,
                frame_count=frame_count,
            )

            gen = self._osd_gen
            self._osd_worker = WallWorker(
                self.session, self.socket,
                80862, xml, diag=True
            )

            def _osd_done(result, _gen=gen):
                if _gen != self._osd_gen:
                    return
                try:
                    st = result.get("status", "?")
                    xm = result.get("xml", "")
                    self.log_mgr.info(f"[{gid}] OSD 设置 status={st} xml_len={len(xm)}")
                except Exception as e:
                    self.log_mgr.error(f"OSD done 异常: {e}")

            def _osd_err(err, _gen=gen):
                if _gen != self._osd_gen:
                    return
                try:
                    self.log_mgr.error(f"[{gid}] OSD 设置失败: {err}")
                except Exception as e:
                    self.log_mgr.error(f"OSD err 异常: {e}")

            def _osd_finished(_gen=gen):
                try:
                    self._on_osd_one_done(_gen)
                except Exception as e:
                    import traceback
                    self.log_mgr.error(f"OSD finished 异常: {e}\n{traceback.format_exc()}")
                    self._on_osd_stop()

            self._osd_worker.sig_done.connect(_osd_done, type=Qt.QueuedConnection)
            self._osd_worker.sig_error.connect(_osd_err, type=Qt.QueuedConnection)
            self._osd_worker.finished.connect(_osd_finished, type=Qt.QueuedConnection)
            self._osd_worker.start()
        except Exception:
            import traceback
            self.log_mgr.error(f"_do_osd_cycle 异常:\n{traceback.format_exc()}")
            self._on_osd_stop()

    def _on_osd_one_done(self, gen: int):
        """一次 OSD 设置完成，等待 dwell 后继续下一轮。gen 参数防止旧回调干扰。"""
        try:
            if getattr(self, '_destroying', False):
                return
            if gen != self._osd_gen:
                return
            if not self._osd_running:
                return
            self._osd_worker = None
            state = self._osd_state
            if not state:
                return
            groups = state["groups"]
            move_indices = state["move_indices"]
            sidx = state["_screen_idx"]
            gid = groups[sidx]["id"]
            moves = self._osd_screen_moves[gid]

            # 推进当前屏组的 MoveID 索引（只推进当前屏组）
            old_midx = move_indices[gid]
            new_midx = (old_midx + 1) % len(moves)
            move_indices[gid] = new_midx
            self.log_mgr.info(f"[{gid}] MoveID索引推进: {old_midx} → {new_midx} (len={len(moves)})")

            # 推进屏组指针
            next_sidx = (sidx + 1) % len(groups)
            state["_screen_idx"] = next_sidx

            # 如果所有屏组都轮完一遍，重置屏组指针回 0，
            # 但不要重置 move_indices，让每个屏组的 MoveID 独立循环
            if next_sidx == 0:
                state["_screen_idx"] = 0
                self.log_mgr.info("所有屏组轮播完毕")

            self._osd_timer.start(state["dwell_ms"])
        except Exception:
            import traceback
            self.log_mgr.error(f"_on_osd_one_done 异常:\n{traceback.format_exc()}")
            self._on_osd_stop()

    def _update_osd_idx_label(self, screen_id, idx, total):
        moves = self._osd_screen_moves.get(screen_id, [])
        if idx < len(moves):
            name = moves[idx].get("name", "?")
            self.osd_idx_label.setText(f"屏组{screen_id}: {idx}/{total-1} (MoveID={moves[idx]['id']})")
        else:
            self.osd_idx_label.setText(f"屏组{screen_id}: —")

    def _on_step_stop_refresh(self):
        """stop 时发起异步关窗后，刷新窗口列表（带 _destroying 保护）。"""
        if getattr(self, '_destroying', False):
            return
        self._refresh_windows()

    def _on_osd_stop(self):
        if getattr(self, '_destroying', False):
            return
        gen = self._osd_gen
        self._osd_running = False
        if self._osd_timer:
            self._osd_timer.stop()
        if self._osd_worker:
            self._osd_worker._stop = True
            self._osd_worker.finished.connect(lambda: self._clear_osd_worker(gen))
        else:
            self._clear_osd_worker(gen)
        self._osd_state = {}

    # ===================================================================
    # 窗口管理
    # ===================================================================

    def _refresh_windows(self):
        try:
            if getattr(self, '_destroying', False):
                return
            if not self.isVisible():
                return
            groups = self._get_selected_groups()
            if not groups:
                return
            grp = groups[0]
            plugin = QueryWindowsPlugin()
            xml = plugin.build_request_xml(self.session, screen_id=grp["id"])
            self._do_query_proto(80822, request_xml=xml)
        except Exception:
            import traceback
            self.log_mgr.error(f"_refresh_windows 异常:\n{traceback.format_exc()}")

    def _handle_windows_response(self, xml: str):
        if getattr(self, '_destroying', False):
            return
        plugin = QueryWindowsPlugin()
        data = plugin.parse_response(xml)
        windows = data.get("windows", [])
        self._populate_windows_table(windows)
        self.btn_close_selected.setEnabled(len(windows) > 0)
        self.log_mgr.debug(f"窗口列表刷新: {len(windows)} 个窗口")

    def _populate_windows_table(self, windows: list):
        if getattr(self, '_destroying', False):
            return
        self.win_table.setRowCount(0)
        for w in windows:
            row = self.win_table.rowCount()
            self.win_table.insertRow(row)
            self.win_table.setItem(row, 0, QTableWidgetItem(str(w.get("id", ""))))
            self.win_table.setItem(row, 1, QTableWidgetItem(str(w.get("x0", ""))))
            self.win_table.setItem(row, 2, QTableWidgetItem(str(w.get("y0", ""))))
            self.win_table.setItem(row, 3, QTableWidgetItem(str(w.get("x1", ""))))
            self.win_table.setItem(row, 4, QTableWidgetItem(str(w.get("y1", ""))))
            self.win_table.setItem(row, 5, QTableWidgetItem(str(w.get("src_id", ""))))

    def _on_close_selected(self):
        row = self.win_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "提示", "请先在窗口列表中选中一行")
            return
        win_id_item = self.win_table.item(row, 0)
        if not win_id_item:
            return
        win_id = win_id_item.text()

        groups = self._get_selected_groups()
        if not groups:
            QMessageBox.warning(self, "提示", "请先选择屏组")
            return
        grp = groups[0]

        plugin = CloseWindowPlugin()
        xml = plugin.build_request_xml(self.session, screen_id=grp["id"], win_id=win_id)
        self.log_mgr.info(f"关闭窗口 {win_id} (80828)...")
        self._do_query_proto(80828, request_xml=xml)

    def _handle_simple_response(self, proto: int, status: int, xml: str):
        if getattr(self, '_destroying', False):
            return
        if proto == 80828:
            if status == 200:
                self.log_mgr.debug("关闭窗口成功")
                QTimer.singleShot(200, self._refresh_windows)
            else:
                self.log_mgr.error(f"关闭窗口失败: status={status}")
