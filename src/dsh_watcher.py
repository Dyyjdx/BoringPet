# -*- coding: utf-8 -*-
"""DSH 任务完成监听器:轮询 DSH 会话日志,检测任务(turn)结束,弹气泡提醒。

背景
----
DSH(DeepSeek Harness)把每次会话(session)的完整事件流追加写到
`~/.dsh/sessions/<工作目录编码>/<session-uuid>/session.jsonl.zstd`
(zstd 压缩的 JSONL)。无论用户在 DSH 桌面版/Web 里自己跑任务,
还是桌宠通过 SDK 发起对话,事件都会落到这里。

本模块起一个后台线程:
  1. 每隔几秒扫描 sessions 目录,对每个有变化的日志文件全量解压;
  2. 找出新增的 `turn/end` 事件(reason.kind == "completed");
  3. 用同文件里的 `turn/start`(毫秒时间戳)算耗时,
     累加该 turn 内所有 `assistant/chunk` 的 usage 得 token 数;
  4. 用 token_stats.record_usage 计费(顺带进用量窗口统计);
  5. emit task_done 信号,由桌宠主线程弹气泡。

只弹"新写入的"完成事件:每个文件记录已处理的最后 seq,重启桌宠
不会把历史任务全部补弹一遍。
"""
import glob
import json
import os
import threading
import time
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from paths import app_dir
from token_stats import record_usage

# 轮询间隔(秒)
POLL_INTERVAL = 3.0
# 默认模型(与 dsh_chat.py 保持一致; 日志里不写模型名, 计费用它)
DSH_MODEL = "deepseek-v4-flash"


def _state_file():
    return app_dir() / "memory" / "dsh_watch_state.json"


def _load_state():
    """读取已处理进度 {日志文件绝对路径: 最后处理的 seq}。"""
    f = _state_file()
    if not f.exists():
        return {}
    try:
        with open(f, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(state):
    try:
        f = _state_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = str(f) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fp:
            json.dump(state, fp, ensure_ascii=False, indent=1)
        os.replace(tmp, f)
    except Exception as e:
        print(f"[DSHWatch] 保存进度失败: {e}")


def find_sessions_root():
    """定位 DSH 会话日志根目录, 依次尝试:
    设置里的 data_dir -> 环境变量 DSH_HOME -> ~/.dsh -> 旧路径。
    设置项优先, 新用户可自己在设置窗口填, 不用碰环境变量。
    """
    candidates = []
    # 1. 设置里手动填的 DSH 数据根目录
    try:
        cfg_file = app_dir() / "settings.json"
        if cfg_file.exists():
            with open(cfg_file, "r", encoding="utf-8") as fp:
                cfg = json.load(fp)
            data_dir = (cfg.get("dsh") or {}).get("data_dir", "") or ""
            data_dir = data_dir.strip()
            if data_dir:
                if os.path.isdir(os.path.join(data_dir, "sessions")):
                    candidates.append(os.path.join(data_dir, "sessions"))
                else:
                    # 填的是 sessions 本身也行
                    candidates.append(data_dir)
    except Exception:
        pass
    # 2. 环境变量 DSH_HOME
    env = os.environ.get("DSH_HOME")
    if env:
        candidates.append(os.path.join(env, "sessions"))
    # 3. 默认 ~/.dsh
    candidates.append(os.path.join(os.path.expanduser("~"), ".dsh", "sessions"))
    # 4. 旧路径兜底
    candidates.append(r"D:\deepseek-harness\dsh-data\sessions")
    for c in candidates:
        if c and os.path.isdir(c):
            return c
    return None


def _fmt_duration(seconds):
    """把秒数格式化成 38秒 / 2分15秒 / 1小时05分。"""
    s = int(seconds)
    if s < 60:
        return f"{s}秒"
    if s < 3600:
        return f"{s // 60}分{s % 60:02d}秒"
    return f"{s // 3600}小时{(s % 3600) // 60:02d}分"


def _fmt_tokens(n):
    """1234 -> '1.2k', 876 -> '876'。"""
    if n >= 10000:
        return f"{n / 1000:.1f}k"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def _fmt_cost(cost):
    """费用保留 4 位有效数字, 极小值显示 <0.0001。"""
    if cost <= 0:
        return "¥0"
    if cost < 0.0001:
        return "<¥0.0001"
    return f"¥{cost:.4f}".rstrip("0").rstrip(".")


class DshWatcher(QThread):
    """后台监听线程。task_done(dict) 在主线程触发, 字典含:
        seconds 耗时(秒)  input_tokens/output_tokens/cache_tokens
        total_tokens cost  model  cwd  finished_at(北京时间字符串)
    """

    task_done = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop_event = threading.Event()
        self._state = _load_state()
        # 历史会话基线: 首次成功发现 sessions 目录时, 把当时已存在的文件
        # 全部记为"历史任务"(只建基线不弹); 之后 DSH 新建的会话才弹提醒。
        self._baseline = set()
        self._baseline_done = False

    def start(self):
        self._snapshot_baseline()
        super().start()

    def _snapshot_baseline(self):
        """首次成功定位 sessions 目录时, 快照现有文件为历史基线。"""
        if self._baseline_done:
            return
        root = find_sessions_root()
        if root:
            self._baseline = set(
                glob.glob(os.path.join(root, "**", "*.zstd"), recursive=True)
            )
            self._baseline_done = True

    def stop(self):
        self._stop_event.set()

    def run(self):
        while not self._stop_event.is_set():
            try:
                self._scan_once()
            except Exception as e:
                print(f"[DSHWatch] 扫描异常: {e}")
            # 用带超时的等待替代固定 sleep, 停止时能立即退出
            self._stop_event.wait(POLL_INTERVAL)

    # ---------- 内部 ----------

    def _scan_once(self):
        root = find_sessions_root()
        if not root:
            return
        # 目录此刻才出现(比如用户后启动 DSH): 这次先建基线, 不弹历史
        self._snapshot_baseline()
        for path in glob.glob(os.path.join(root, "**", "*.zstd"), recursive=True):
            try:
                self._process_file(path)
            except Exception as e:
                print(f"[DSHWatch] {os.path.basename(path)}: {e}")

    def _process_file(self, path):
        """解压日志, 找出新增的 turn/end, 弹提醒。"""
        try:
            import zstandard
        except Exception:
            return  # 没装 zstandard 就跳过(不影响桌宠其它功能)

        # 文件没变过就跳过
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return
        last = self._state.get(path)
        if last and last.get("mtime") == mtime and last.get("size") == os.path.getsize(path):
            return

        # 解压
        dctx = zstandard.ZstdDecompressor()
        with open(path, "rb") as fh, dctx.stream_reader(fh) as r:
            raw = r.read().decode("utf-8", "replace")
        lines = raw.splitlines()
        max_seq = -1
        # 收集本轮要通知的任务
        done_turns = []   # [ {seconds, tokens..., cwd} ]
        # turn 状态: turn编号 -> {"start": 毫秒, "in":0, "out":0, "cache":0, "model":""}
        turns = {}
        known_file = path in self._state          # 是否已记录过此文件
        base_seq = (last or {}).get("seq", -1)    # 已处理的 seq
        is_new_session = path not in self._baseline  # 桌宠启动后新建的会话 → 立即按新任务弹
        if is_new_session:
            # 新会话文件: base_seq 仍用 -1, 全部 turn 都是新的; known_file=False
            # 因此下方"首次见到只建基线"的逻辑要跳过。
            pass

        for line in lines:
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            etype = ev.get("type")
            seq = ev.get("seq")
            if isinstance(seq, int):
                max_seq = max(max_seq, seq)

            if etype == "session":
                # 首行, 记录工作目录
                continue

            if etype == "turn/start":
                try:
                    turn = ev["data"]["turn"]
                    turns.setdefault(turn, {"start": ev.get("time"), "in": 0, "out": 0, "cache": 0, "model": ""})
                except Exception:
                    pass
                continue

            if etype == "assistant/chunk":
                # 旧格式(v0): usage 事件累加 token
                try:
                    chunk = ev["data"]["chunk"]
                    if chunk.get("type") == "usage":
                        usage = chunk.get("usage", {})
                        turn = ev["data"]["turn"]
                        st = turns.setdefault(turn, {"start": None, "in": 0, "out": 0, "cache": 0, "model": ""})
                        st["in"] += int(usage.get("inputTokens") or 0)
                        st["out"] += int(usage.get("outputTokens") or 0)
                        st["cache"] += int(usage.get("cacheReadTokens") or 0)
                except Exception:
                    pass
                continue

            if etype == "assistant/message":
                # 新格式(v4): assistant/message 自带 usage 和 model
                try:
                    data = ev.get("data", {})
                    turn = data.get("turn")
                    st = turns.setdefault(turn, {"start": None, "in": 0, "out": 0, "cache": 0, "model": ""})
                    usage = data.get("usage") or {}
                    if usage:
                        st["in"] += int(usage.get("inputTokens") or 0)
                        st["out"] += int(usage.get("outputTokens") or 0)
                        st["cache"] += int(usage.get("cacheReadTokens") or 0)
                    # 模型名(计费用)
                    src = (data.get("message") or {}).get("source") or {}
                    if src.get("model"):
                        st["model"] = src.get("model") or ""
                except Exception:
                    pass
                continue

            if etype == "turn/end":
                # 只处理"新写入"的结束事件; 旧事件用于初始化进度, 不弹
                if seq is not None and seq <= base_seq:
                    continue
                try:
                    data = ev.get("data", {})
                    if data.get("reason", {}).get("kind") != "completed":
                        continue
                    turn = data.get("turn")
                    st = turns.get(turn, {"start": ev.get("time"), "in": 0, "out": 0, "cache": 0, "model": ""})
                    end_ms = ev.get("time")
                    start_ms = st.get("start")
                    seconds = 0
                    if isinstance(start_ms, (int, float)) and isinstance(end_ms, (int, float)) and end_ms > start_ms:
                        seconds = (end_ms - start_ms) / 1000.0
                    done_turns.append({
                        "seconds": seconds,
                        "input_tokens": st.get("in", 0),
                        "output_tokens": st.get("out", 0),
                        "cache_tokens": st.get("cache", 0),
                        "model": st.get("model", "") or DSH_MODEL,
                    })
                except Exception:
                    continue

        # 更新进度
        new_last = {"mtime": mtime, "size": os.path.getsize(path), "seq": max_seq}
        self._state[path] = new_last
        _save_state(self._state)

        # 首次见到的历史文件(桌宠启动前就存在的会话):只建基线,不弹历史任务
        # (避免桌宠重启后补弹一堆旧任务); 桌宠启动后新建的会话不在此列, 正常弹。
        if not known_file and not is_new_session:
            return

        # 弹提醒
        for t in done_turns:
            total = t["input_tokens"] + t["output_tokens"]
            if total <= 0:
                continue  # 没有用量信息(可能被压缩掉/异常会话), 不打扰
            model = t.get("model") or DSH_MODEL
            try:
                rec = record_usage(
                    model, "", "",
                    input_tokens=t["input_tokens"],
                    output_tokens=t["output_tokens"],
                    cache_hit_tokens=t["cache_tokens"],
                )
            except Exception as e:
                print(f"[DSHWatch] 计费失败: {e}")
                rec = {"cost": 0.0}
            t["total_tokens"] = total
            t["cost"] = rec.get("cost", 0.0)
            t["model"] = model
            t["cwd"] = self._cwd_of(path)
            t["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            self.task_done.emit(t)

    @staticmethod
    def _cwd_of(path):
        """从日志路径反推工作目录(目录名做了 URL 编码)。"""
        parts = Path(path).parts
        try:
            for i, p in enumerate(parts):
                if p == "sessions" and i + 1 < len(parts):
                    raw = parts[i + 1]
                    if raw.startswith("--") and raw.endswith("--"):
                        raw = raw[2:-2]
                    try:
                        from urllib.parse import unquote
                        return unquote(raw) or raw
                    except Exception:
                        return raw
        except Exception:
            pass
        return ""
