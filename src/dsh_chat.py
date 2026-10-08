# -*- coding: utf-8 -*-
"""DSH 对话后台线程:通过官方 Python SDK 驱动本地 DSH runtime。

与 chat_window.ChatWorker 的信号接口保持一致,便于在窗口里按模式替换:
    text_chunk(str)  文本增量(一次性整段)
    thinking()       已发出、等首字
    tool_call(str,str)   工具调用(名称, 摘要)
    tool_result(str,str) 工具结果
    done()           本轮结束
    error(str)       失败

走官方 deepseek-harness-sdk(profile=sdk):不依赖本机 3080 web 服务,
工具调用自动放行(不弹权限确认),会话 id 持久化在 dsh_session.json,
重开桌宠能接着上一段对话。
"""
import json
import os
import shutil
import threading
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from paths import app_dir

SESSION_FILE = str(app_dir() / "dsh_session.json")

# 桌宠工作目录(DSH 执行工具时的 cwd)。
# 不能用 __file__ 推:打包后它指向 _MEIPASS 里的临时文件,算出来的不是项目根目录,
# DSH 的工具调用会去操作安装目录(在 Program Files 下还是只读的)。
DSH_CWD = str(app_dir())
# 本机 DSH 配置:优先读环境变量,其次在 PATH 里找 dsh,最后退回本机常见安装位置
# (写死绝对路径会导致别人拿到项目后这个通道直接不可用)
DSH_HOME = os.environ.get("DSH_HOME") or r"D:\deepseek-harness\dsh-data"
DSH_BIN = os.environ.get("DSH_BIN") or shutil.which("dsh") or r"D:\Anaconda\Scripts\dsh.exe"
DSH_MODEL = "deepseek-v4-flash"

# 工具调用里展示用的友好名称(按名字关键词归类)
_TOOL_LABELS = (
    ("pwsh", "执行命令"), ("shell", "执行命令"), ("bash", "执行命令"), ("terminal", "执行命令"),
    ("file", "操作文件"), ("fs", "操作文件"), ("read", "读取文件"), ("write", "写入文件"),
    ("edit", "修改文件"), ("str_replace", "修改文件"), ("glob", "查找文件"), ("grep", "搜索内容"),
    ("browser", "浏览器操作"), ("fetch", "联网抓取"), ("web", "联网"),
    ("search", "联网搜索"), ("todo", "整理任务"), ("task", "任务"),
)


def _tool_label(name):
    low = (name or "").lower()
    for key, label in _TOOL_LABELS:
        if key in low:
            return label
    return name or "工具"


def _tool_detail(args, limit=60):
    """把工具参数压成一行摘要。"""
    if isinstance(args, dict):
        for key in ("command", "cmd", "path", "file_path", "pattern", "query", "url"):
            if args.get(key):
                return str(args[key])[:limit]
        return str(args)[:limit]
    return str(args)[:limit]


def _load_chat_cfg():
    try:
        from settings_window import load_settings
        return (load_settings().get("chat", {}) or {})
    except Exception:
        return {}


# ── 模块级 SDK 单例: 桌宠进程内只启动一次 DSH runtime ──
# 原因: DSH 的会话状态在 runtime 进程内存里, 每次新开 runtime 复用旧 session_id
# 会报 "already exists"; 常驻单例才能让多轮对话在同一 runtime 里持续有效。
_harness = None
_harness_lock = threading.Lock()


def _get_harness(api_key):
    global _harness
    with _harness_lock:
        if _harness is None:
            from deepseek_harness import DeepSeekHarness
            _harness = DeepSeekHarness(
                provider="deepseek-official",
                model=DSH_MODEL,
                api_key=api_key,
                max_tokens=2048,
                cwd=DSH_CWD,
                dsh_home=DSH_HOME,
                dsh_bin=DSH_BIN,
                profile="sdk",
                initialize_timeout_seconds=60,
                request_timeout_seconds=180,
            )
        return _harness


def _harness_run(api_key, text, session_id):
    """在单例 runtime 上跑一轮(全局锁串行, 桌宠一次只有一个对话)。"""
    h = _get_harness(api_key)
    with _harness_lock:
        return h.run(text, session_id=session_id)


def _close_harness():
    global _harness
    with _harness_lock:
        if _harness is not None:
            try:
                _harness.close()
            except Exception:
                pass
            _harness = None


import atexit
atexit.register(_close_harness)


def load_session_id():
    try:
        with open(SESSION_FILE, "r", encoding="utf-8") as f:
            return (json.load(f) or {}).get("session_id") or None
    except Exception:
        return None


def save_session_id(session_id):
    try:
        tmp = SESSION_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"session_id": session_id}, f, ensure_ascii=False, indent=2)
        os.replace(tmp, SESSION_FILE)
    except Exception:
        pass


def clear_session_id():
    try:
        if os.path.isfile(SESSION_FILE):
            os.remove(SESSION_FILE)
    except Exception:
        pass


def pet_persona():
    """读取设置里的桌宠人设(取不到就用空串)。"""
    try:
        from settings_window import load_settings
        return (load_settings().get("pet", {}) or {}).get("persona") or ""
    except Exception:
        return ""


def _persona_prefix(text):
    """给新会话的第一条消息带上人设,让 DSH 的 agent 用桌宠的语气讲话。

    只在会话刚创建时加一次,之后的对话不再重复,避免每次都污染输入。
    设置里把 chat.dsh_persona 设为 false 可关闭。
    """
    try:
        from settings_window import load_settings
        cfg = (load_settings().get("chat", {}) or {})
        if cfg.get("dsh_persona") is False:
            return text
    except Exception:
        pass
    persona = pet_persona()
    if not persona:
        return text
    return (
        f"【角色设定】{persona}\n"
        f"(请始终保持这个身份和语气,不要自称 AI。以下是我的话)\n"
        f"{text}"
    )


class DshChatWorker(QThread):
    """一轮 DSH 对话:官方 SDK 驱动,结束后一次性回文本。"""

    text_chunk = Signal(str)
    thinking = Signal()
    tool_call = Signal(str, str)
    tool_result = Signal(str, str)
    done = Signal()
    error = Signal(str)

    def __init__(self, text, images=None, parent=None):
        super().__init__(parent)
        self._text = text
        self._images = images or []

    def run(self):
        cfg = _load_chat_cfg()
        api_key = cfg.get("api_key") or ""
        if not api_key:
            self.error.emit("DSH 通道需要先在设置里填 DeepSeek API Key")
            return

        sid = load_session_id()
        created = not sid
        # 新会话:第一条消息带上人设,让 agent 用桌宠的语气
        text = _persona_prefix(self._text) if created else self._text

        if self._images:
            self.tool_call.emit("图片", "DSH 通道暂不支持图片,已忽略")

        try:
            self.thinking.emit()
            try:
                result = _harness_run(api_key, text, sid)
            except Exception as e:
                # 跨进程残留会话(runtime 重建后磁盘 session 还在) → 丢弃旧会话重试一次
                if "already exists" in str(e):
                    clear_session_id()
                    result = _harness_run(api_key, _persona_prefix(self._text), None)
                else:
                    raise

            # 展示工具调用(从事件里提取,仅展示不阻断)
            for ev in result.events:
                if ev.get("type") == "tool/call":
                    data = ev.get("data") or {}
                    name = data.get("name") or data.get("toolName") or "tool"
                    args = data.get("args") or data.get("arguments") or {}
                    self.tool_call.emit(_tool_label(name), _tool_detail(args))

            if result.final_response:
                self.text_chunk.emit(result.final_response)
            save_session_id(result.session_id)
            self.done.emit()
        except Exception as e:
            self.error.emit(f"DSH 调用失败: {e}")
