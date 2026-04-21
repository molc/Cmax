import threading

DEFAULT_HOST = "172.21.109.151"
DEFAULT_PORT = 2100
DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "123456"

HEARTBEAT_INTERVAL_MS = 30_000  # 30秒
SOCKET_TIMEOUT_SEC = 15
LOG_DIR = "logs"
SETTINGS_FILE = "settings.json"

_settings_lock = threading.Lock()
_settings = {}


def load_settings():
    """从 settings.json 加载保存的配置（线程安全）。"""
    import json, os
    if not os.path.exists(SETTINGS_FILE):
        return {}
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(data: dict):
    """保存配置到 settings.json（线程安全）。"""
    import json
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# 预加载已保存的设置
_settings = load_settings()


def get_setting(key: str, default=None):
    with _settings_lock:
        return _settings.get(key, default)


def set_setting(key: str, value):
    with _settings_lock:
        _settings[key] = value
        save_settings(_settings)

