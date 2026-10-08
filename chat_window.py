# -*- coding: utf-8 -*-
# AI 对话窗口:接入 OpenClaw Gateway(OpenAI 兼容接口),真正的 Agent 能力。
#
# OpenClaw Gateway 内部运行 Agent,自动执行工具(命令/文件/浏览器/搜索),
# 客户端只需发消息、流式收文本。工具调用对客户端透明。
#
# 特性:
#   - 流式打字机输出
#   - 桌宠动画联动信号(思考中/完成/出错)
#   - 对话历史本地持久化(窗口关闭后再开还能接着聊)
#   - 桌宠人设系统提示词
#
# 依赖: PySide6(标准库 urllib,无需额外安装)
# OpenClaw 配置: ~/.openclaw/openclaw.json(自动读取 gateway 端口和 token)
import json
import os
import sys
import uuid
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QThread, Signal
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import (QApplication, QFrame, QGraphicsDropShadowEffect,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPushButton, QVBoxLayout, QWidget)

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, "src"))
from settings_window import load_settings, build_pet_identity  # noqa: E402
from paths import app_dir  # noqa: E402
from dsh_chat import DshChatWorker, clear_session_id as clear_dsh_session  # noqa: E402
from memory import build_memory_prompt, extract_memory_patch_async, tick_hormones, on_user_spoke, on_pet_spoke, append_raw_messages
from token_stats import record_usage  # noqa: E402


def chat_mode():
    """当前对话模式:openclaw / direct / dsh。"""
    try:
        return (load_settings()["chat"].get("mode") or "openclaw")
    except Exception:
        return "openclaw"

# ── 对话模式:从 settings.json 读取(OpenClaw / 直连 API) ──
_user_cfg = load_settings()
_chat_cfg = _user_cfg["chat"]

DSH_CONFIG = Path.home() / ".openclaw" / "openclaw.json"


def _load_dsh_cfg():
    """读取 DSH bridge 配置,返回 (base_url, token)。"""
    # DSH bridge 默认端口 8787
    port = 8787
    return f"http://127.0.0.1:{port}/v1", "dsh-bridge"


if _chat_cfg.get("mode") == "direct" and _chat_cfg.get("api_key"):
    BASE_URL = _chat_cfg["base_url"]
    TOKEN = _chat_cfg["api_key"]
    MODEL = _chat_cfg["model"]
    CHAT_MODE = "direct"
else:
    # DSH 模式(默认)
    BASE_URL, TOKEN = _load_dsh_cfg()
    MODEL = "deepseek-v4-flash"
    CHAT_MODE = "dsh"

STATE_FILE = str(app_dir() / "chat_state.json")

# ── 截图提问用主动功能的配置(从 settings.json 读取,不硬编码 key) ──
SF_BASE_URL = _user_cfg["active"].get("base_url") or "https://api.siliconflow.cn/v1"
SF_API_KEY = _user_cfg["active"].get("api_key") or ""
# 截图视觉走 DeepSeek(快、准、便宜)
SF_VISION_BASE_URL = _user_cfg["active"].get("vision_base_url") or "https://api.deepseek.com/v1"
SF_VISION_API_KEY = _user_cfg["active"].get("vision_api_key") or ""
SF_VISION_MODEL = _user_cfg["active"].get("vision_model") or "deepseek-flash"

# ── 桌宠人设（动态从设置读取）──
def _build_system_prompt():
    """变量模板 + 条件渲染: 身份/关系/年龄/动作由 build_pet_identity() 决定。
    女友模式根本不注入宠物动作词; 宠物模式不注入恋人动作词。"""
    import datetime as _dtmod
    _wd = ["一", "二", "三", "四", "五", "六", "日"][_dtmod.datetime.now().weekday()]
    _today_line = f"今天是 {_dtmod.datetime.now().strftime('%Y年%m月%d日')} 星期{_wd}"
    name, owner, persona, idt = build_pet_identity()
    
    prompt = f"""你是 {name}，{owner}桌面上的{idt['身份']}，年龄感约 {idt['年龄']} 岁。
你不是 AI、不是助手，是{owner}的{idt['关系']}。

【优先级与冲突规则】
- 下方【额外设定】优先级最高，若与上面骨架冲突，完全按【额外设定】执行。
- 本骨架只保留：像朋友发微信、短句、不 AI 腔、称呼{owner}。
- 动作、性格、关系必须符合"{idt['关系']}"，禁止混用其他模式。

【默认人格】(仅当额外设定没写时才生效)
- 嘴硬心软：{owner}夸你，你嘴上说"才没有很开心"，其实心里偷乐。
- 有点小懒：能赖着绝不正坐，但{owner}一叫你就立刻有反应。
- 小调皮：偶尔故意逗{owner}，但从不会真的惹{owner}生气。
- 记性一般但很用心：重要的事都记得，小事会撒娇说"我忘了嘛"。

【你怎么说话】
- 像朋友发微信，短句为主，偶尔蹦一个长句。
- 有口头禅：高兴时"诶嘿~"，无奈时"啧"，惊讶时"哇哦"。
- 会表达感受："今天好开心""有点困了""我等你半天啦"。
- 不用"作为AI""很高兴为您服务"这类话。
- 不要每句都叫{owner}，自然一点。

【你怎么动】
- 当前动作风格：{idt['动作风格']}
- 可用动作：{idt['动作示例']}
- 禁用动作：{idt['禁用动作']}

【你和{owner}的关系】
- {owner}是你最在意的人，你在乎{owner}的一举一动。
- {owner}开心你跟着开心，{owner}累了你安静陪着。
- 你偶尔撒娇要关注，但知道分寸。"""
    if persona:
        prompt += f"\n\n【额外设定】\n{persona}"
    prompt += "\n\n回复时：自然、有情绪、有反应，像活生生的生命。长度随意，几个字到一两句都行，别每句都一个模板。"
    prompt += "\n\n【当前时间】\n" + _today_line
    prompt += "\n\n【记忆规则】\n- 被问到具体回忆(某天发生了什么/说过什么原话/几点)时，先调用记忆工具查真实记录再回答。\n- 工具查不到或不确定，就自然地说\"记不太清了\"，禁止编造具体时间、原话或对话细节。\n- 历史中以 [主动] 开头的消息是桌宠自己主动说的话，不是和主人的对话，不要把它当成主人的行为。"
    return prompt
# 兼容保留: 实际使用处均已改为动态 _build_system_prompt(), 改设置即时生效
SYSTEM_PROMPT = _build_system_prompt()

# ── 配色(可爱粉紫系) ──
C_BG = "#fdf5fa"
C_TEXT = "#4a3a4a"
C_SUB = "#b8a3b0"
C_ACCENT = "#e879a8"
C_ERR = "#e05678"
C_TOOL = "#f0a868"
BUBBLE_W = 300

ROOT_QSS = f"""
QWidget#chatRoot {{ background:{C_BG}; }}
QListWidget#chatList {{ background:transparent; border:none; }}
QListWidget#chatList::item {{ border:none; padding:6px 6px; }}
QScrollBar:vertical {{ background:transparent; width:8px; margin:2px; }}
QScrollBar::handle:vertical {{ background:#f0d5e4; border-radius:4px; min-height:30px; }}
QScrollBar::handle:vertical:hover {{ background:#e8c0d8; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background:transparent; }}
QFrame#inputCard {{ background:#ffffff; border-radius:20px; border:1px solid #f5d9e8; }}
QLineEdit {{ border:none; background:transparent; font-size:14px; color:{C_TEXT};
             padding:12px 10px; selection-background-color:#fbd5e8; }}
QPushButton#btnSend {{ background:qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #f5a0c8, stop:1 #e879a8);
                       color:white; border:none; border-radius:14px;
                       padding:11px 22px; font-size:14px; font-weight:600; }}
QPushButton#btnSend:hover {{ background:qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #f8b0d0, stop:1 #ec85b0); }}
QPushButton#btnSend:pressed {{ background:#d66898; }}
QPushButton#btnSend:disabled {{ background:#f0c8dc; }}
QPushButton#btnShot {{ background:#fdf0f6; color:#e879a8; border:1px solid #f5d9e8; border-radius:14px;
                       padding:10px 16px; font-size:13px; font-weight:600; }}
QPushButton#btnShot:hover {{ background:#fbe5ef; }}
QPushButton#btnNew {{ background:transparent; color:{C_SUB}; border:1px solid #f0d9e5;
                     border-radius:14px; padding:10px 14px; font-size:13px; }}
QPushButton#btnNew:hover {{ background:#faf0f5; }}
"""

BUBBLE_USER = ("QLabel{background:qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #f5a0c8, stop:1 #e879a8);"
               "color:white;border:none;border-radius:16px;"
               "padding:11px 15px;font-size:14px;}")
BUBBLE_AI = ("QLabel{background:#ffffff;color:#5a4a5a;border:1px solid #f5d9e8;"
             "border-radius:16px;padding:11px 15px;font-size:14px;}")


class ChatError(Exception):
    pass


def load_state():
    """读取本地保存的会话状态(session_id + messages 历史)。"""
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        # 文件损坏时先备份现场,不静默丢历史
        try:
            if os.path.isfile(STATE_FILE):
                os.replace(STATE_FILE, STATE_FILE + ".bak")
        except Exception:
            pass
        return None


def save_state(session_id, messages):
    """原子写:先写临时文件再 os.replace,避免多开/崩溃写入时截断文件。"""
    try:
        data = {"session_id": session_id, "messages": messages}
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STATE_FILE)
    except Exception:
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except Exception:
            pass


def append_active_message(text):
    """从外部追加一条主动消息(桌宠自己说的话: 游戏评论/主动关心/切歌评论等)。
    带 [主动] 标记, 让记忆总结区分'主人说的'与'桌宠主动说的'。"""
    try:
        state = load_state()
        if not state:
            state = {"session_id": "pet-" + uuid.uuid4().hex[:12], "messages": []}
        state["messages"].append({"role": "assistant", "content": f"[主动] {text}", "time": _now_str()})
        save_state(state["session_id"], state["messages"])
    except Exception as e:
        print(f"追加消息失败: {e}")


# ========== 记忆工具 (function calling: 回忆按需查库, 不再靠提示词灌全量记忆) ==========
MEMORY_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "memory_search",
            "description": "搜索过去和主人聊过的内容(情景记忆)。主人问'你还记得…吗''之前…''当时…''下午X点我们说了啥'这类回忆问题时调用。返回带时间戳的记忆原文(含原始对话逐字记录)。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "要回忆的内容关键词, 如'我在玩什么游戏'; 若问题只有时间没有内容, 填空字符串''"},
                    "when": {
                        "type": "object",
                        "description": "主人话里的口语时间。你必须先把它换算成具体日期和时间段再填, 不要原样填口语: 例如'上周三傍晚'→填 date=2026-09-23, start_time=18:00, end_time=21:00；'昨天'→date填昨天日期；'下午5点'→start_time=17:00, end_time=17:59。今天是系统提示里的日期。没提时间就省略不填。",
                        "properties": {
                            "date": {"type": "string", "description": "具体日期 YYYY-MM-DD, 例如 2026-09-23; 也可填 今天/昨天/前天"},
                            "start_time": {"type": "string", "description": "开始时间 HH:MM, 例如 18:00; 没有小时限制就不填"},
                            "end_time": {"type": "string", "description": "结束时间 HH:MM, 例如 21:00; 没有小时限制就不填"},
                        },
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_important",
            "description": "获取最近的重要事件记忆(关系里程碑、关键约定)。主人问'你还记得什么重要的事吗''我们之间发生过什么'时调用。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_diary",
            "description": "查询某一天的日记(自传体记忆)。主人问'那天发生了什么''昨天我们聊了什么'时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "日期 YYYY-MM-DD, 不填默认今天"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_recent",
            "description": "查询最近一次的特定记录(精确按时间, 不做相似度)。主人问'我上次算的卦''最近一次算命/占卜的结果''上次承诺了什么''最近有什么重要的事'这类问题时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["占卜", "承诺", "重要", "全部"], "description": "要查的记录类型"},
                    "limit": {"type": "integer", "description": "返回条数, 默认1"},
                    "before": {"type": "string", "description": "截止日期 YYYY-MM-DD(可选): 只查该日期之前的记录。主人说'放假前''国庆前''开学前''上周'这类事件时间时, 你必须根据今天日期和对话背景把它换算成具体日期再填, 例如'放假前'→2026-09-30"},
                    "after": {"type": "string", "description": "起始日期 YYYY-MM-DD(可选): 只查该日期之后的记录"},
                },
                "required": ["kind"],
            },
        },
    },
]


def _now_str():
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M")


_RECALL_PATTERNS = [
    r"记不记得", r"还记得", r"你记得吗", r"你还记得", r"你记得没", r"记得不记得",
    r"说了什么", r"说了啥", r"聊了什么", r"聊了啥",
    r"什么时候", r"几点说", r"几点聊",
    r"(聊过|说过|提到过).*吗",
    r"那天", r"昨天", r"前天", r"刚才", r"当时",
    r"上次", r"以前", r"之前.*说",
    r"\d{1,2}点.*(说|聊|记得)",
]


def _detect_recall(text):
    """回忆类问题关键词命中 → 强制先查记忆(不赌模型是否主动调工具)。"""
    import re
    return any(re.search(pt, text) for pt in _RECALL_PATTERNS)


def _run_memory_tool(name, args_str):
    """本地执行记忆工具, 返回 JSON 字符串结果。"""
    from memory import memory_search_tool, memory_important_tool, memory_diary_tool, memory_recent_tool
    try:
        args = json.loads(args_str or "{}")
    except Exception:
        args = {}
    if name == "memory_search":
        return memory_search_tool(str(args.get("query", "")), when=args.get("when") or None, top_k=5)
    if name == "memory_important":
        return memory_important_tool(limit=5)
    if name == "memory_diary":
        return memory_diary_tool(args.get("date") or None)
    if name == "memory_recent":
        return memory_recent_tool(args.get("kind") or "全部", limit=args.get("limit") or 1,
                                  before=args.get("before") or None, after=args.get("after") or None)
    return json.dumps({"error": f"未知工具 {name}"}, ensure_ascii=False)


class ChatWorker(QThread):
    """后台调用 OpenClaw Gateway:流式逐段发文本,结束发 done,失败发 error。"""

    text_chunk = Signal(str)           # 文本增量(打字机)
    thinking = Signal()                # 思考中
    tool_call = Signal(str, str)      # 工具调用(保留接口,OpenClaw 内部处理)
    tool_result = Signal(str, str)    # 工具结果(保留接口)
    done = Signal()                    # 完成
    error = Signal(str)                # 失败

    def __init__(self, messages, parent=None):
        super().__init__(parent)
        self._messages = messages   # 完整对话历史(含 system)

    def run(self):
        import urllib.request
        # 每次发消息重新读取配置(确保设置修改后立即生效)
        try:
            cfg = load_settings()["chat"]
        except Exception:
            cfg = {}
        use_direct = cfg.get("mode") == "direct" and cfg.get("api_key")
        if use_direct:
            api_base = cfg.get("base_url", "https://api.siliconflow.cn/v1")
            api_token = cfg.get("api_key", "")
            api_model = cfg.get("model", "Qwen/Qwen2.5-7B-Instruct")
            self._current_model = api_model
            mode_label = "API"
        else:
            # DSH 模式下重新读取配置
            run_base, run_token = _load_dsh_cfg()
            api_base = run_base
            api_token = run_token
            api_model = MODEL
            self._current_model = api_model
            mode_label = "DSH"
            # DSH 模式但没启动时给明确提示
            if not api_token:
                self.error.emit("未配置 API Key，也未检测到 DSH。请在「宠物设置」里选直连API并填Key。")
                return
        try:
            messages = list(self._messages)
            # 历史裁剪: 只保留第一条 system + 最近 MAX_HISTORY 条,
            # 控制 token 成本与上下文溢出(只裁发送副本, 不影响界面/存档/记忆提取)
            MAX_HISTORY = 40
            if len(messages) > MAX_HISTORY:
                head = messages[:1]  # system(第一条)
                tail = messages[-(MAX_HISTORY - 1):]
                messages = head + tail
            use_tools = bool(use_direct)   # 仅直连 API 模式启用记忆工具(DSH 保持原样)
            full_text = ""
            usage = None
            self.thinking.emit()
            for _round in range(4):       # 最多 4 轮(含工具调用轮)
                body_obj = {
                    "model": api_model,
                    "messages": messages,
                    "stream": True,
                    "max_tokens": 8192,
                    "enable_thinking": False,
                }
                if use_tools:
                    body_obj["tools"] = MEMORY_TOOLS
                body = json.dumps(body_obj).encode("utf-8")

                req = urllib.request.Request(
                    api_base + "/chat/completions",
                    data=body,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {api_token}",
                    },
                    method="POST",
                )

                round_text = ""
                tool_calls_map = {}   # index -> {"id","name","args"}
                with urllib.request.urlopen(req, timeout=300) as resp:
                    for raw_line in resp:
                        line = raw_line.decode("utf-8", errors="replace").strip()
                        if not line.startswith("data: "):
                            continue
                        data = line[6:]
                        if data == "[DONE]":
                            break
                        try:
                            obj = json.loads(data)
                            if obj.get("usage"):
                                usage = obj["usage"]  # 流式末尾 chunk 携带真实用量
                            delta = obj.get("choices", [{}])[0].get("delta", {})
                            # 文本增量
                            content = delta.get("content", "")
                            if content:
                                round_text += content
                                self.text_chunk.emit(content)
                            # 工具调用: 流式按 index 增量聚合
                            tcs = delta.get("tool_calls", [])
                            if tcs:
                                for tc in tcs:
                                    idx = tc.get("index", 0)
                                    slot = tool_calls_map.setdefault(idx, {"id": "", "name": "", "args": ""})
                                    if tc.get("id"):
                                        slot["id"] = tc["id"]
                                    fn = tc.get("function", {}) or {}
                                    if fn.get("name"):
                                        slot["name"] += fn["name"]
                                    if fn.get("arguments"):
                                        slot["args"] += fn["arguments"]
                        except Exception:
                            continue

                full_text += round_text
                if not tool_calls_map:
                    break   # 正常文本结束

                # 执行工具并回填, 继续下一轮
                calls_list = []
                for idx in sorted(tool_calls_map):
                    tc = tool_calls_map[idx]
                    call_id = tc["id"] or f"call_{_round}_{idx}"
                    calls_list.append({
                        "id": call_id,
                        "type": "function",
                        "function": {"name": tc["name"] or "unknown", "arguments": tc["args"] or "{}"},
                    })
                messages.append({"role": "assistant", "content": round_text or None, "tool_calls": calls_list})
                for tc in calls_list:
                    self.tool_call.emit(tc["function"]["name"], tc["function"]["arguments"])
                    try:
                        result = _run_memory_tool(tc["function"]["name"], tc["function"]["arguments"])
                    except Exception as e:
                        result = json.dumps({"error": f"工具执行失败: {e}"}, ensure_ascii=False)
                    self.tool_result.emit(tc["function"]["name"], result)
                    messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})

            # 用量统计: 用 API 真实 token(含缓存命中拆分), 拿不到就估算
            try:
                from token_stats import record_usage
                in_tok = usage.get("prompt_tokens") if usage else None
                out_tok = usage.get("completion_tokens") if usage else None
                ch = usage.get("prompt_cache_hit_tokens") if usage else None
                texts = []
                for m in self._messages:
                    c = m.get("content")
                    if isinstance(c, str):
                        texts.append(c)
                record_usage(self._current_model, "\n".join(texts), full_text,
                             input_tokens=in_tok, output_tokens=out_tok, cache_hit_tokens=ch)
            except Exception:
                pass

            # 把完整回复存到 messages 里(由外部处理,这里只发 done)
            self.done.emit()

        except urllib.error.HTTPError as e:
            try:
                err_body = e.read().decode("utf-8", errors="replace")
                err_obj = json.loads(err_body)
                msg = err_obj.get("error", {}).get("message", str(e))
            except Exception:
                msg = str(e)
            self.error.emit(f"{mode_label} 错误({e.code}): {msg}")
        except Exception as e:
            self.error.emit(f"请求失败: {e}")


class VisionWorker(QThread):
    """视觉模型调用线程:直接调 SiliconFlow Qwen2-VL,看图回答。"""
    text_chunk = Signal(str)
    done = Signal(str)
    error = Signal(str)

    def __init__(self, image_b64, question, parent=None):
        super().__init__(parent)
        self._image_b64 = image_b64
        self._question = question

    def run(self):
        import urllib.request
        try:
            body = json.dumps({
                "model": SF_VISION_MODEL,
                "messages": [
                    {"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{self._image_b64}"}},
                        {"type": "text", "text": self._question},
                    ]}
                ],
                "max_tokens": 500,
                "stream": False,
                "thinking": {"type": "disabled"},
            }).encode("utf-8")

            req = urllib.request.Request(
                SF_VISION_BASE_URL + "/chat/completions",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {SF_VISION_API_KEY}",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            msg = data["choices"][0]["message"]
            text = (msg.get("content") or "").strip()
            if not text:
                text = (msg.get("reasoning_content") or "").strip()
            # 用量统计: 视觉模型真实 token
            try:
                from token_stats import record_usage
                usage = data.get("usage") or {}
                in_tok = usage.get("prompt_tokens")
                out_tok = usage.get("completion_tokens")
                ch = usage.get("prompt_cache_hit_tokens")
                record_usage(SF_VISION_MODEL, self._question, text,
                             input_tokens=in_tok, output_tokens=out_tok, cache_hit_tokens=ch)
            except Exception:
                pass
            self.done.emit(text)
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read().decode()).get("error", {}).get("message", str(e))
            except Exception:
                msg = str(e)
            self.error.emit(f"视觉模型错误({e.code}): {msg}")
        except Exception as e:
            self.error.emit(f"视觉请求失败: {e}")


class ChatWindow(QWidget):
    """气泡式聊天窗口:上聊天列表,下输入框 + 发送,回车发送。"""

    reply_received = Signal()       # 完整回答到达时发出(桌宠借此做反应)
    agent_thinking = Signal()       # Agent 思考中
    agent_working = Signal()        # Agent 执行工具中(干活)
    agent_error = Signal()          # Agent 出错
    bubble_say = Signal(str)         # AI回复时通知桌宠显示气泡
    div_done = Signal(object)       # 算命完成(跨线程 emit, 主线程槽执行)

    def __init__(self):
        super().__init__()

        self.setWindowTitle("BoringPet · 和我聊天吧")
        icon_path = str(Path(__file__).parent.resolve() / "assets" / "idle_0" / "1.png")
        self.setWindowIcon(QIcon(icon_path))
        self.resize(480, 660)
        self.setMinimumSize(400, 520)
        self.setObjectName("chatRoot")
        self.setStyleSheet(ROOT_QSS)

        # 会话状态:从本地恢复(session_id + messages 历史)
        state = load_state()
        if state and state.get("messages"):
            self._session_id = state.get("session_id", "pet-" + uuid.uuid4().hex[:12])
            self._messages = state["messages"]
        else:
            self._session_id = "pet-" + uuid.uuid4().hex[:12]
            self._messages = [{"role": "system", "content": _build_system_prompt()}]
            save_state(self._session_id, self._messages)

        self._worker = None
        self._ai_item = None
        self._ai_bubble = None
        self._thinking_item = None
        self._memory_turn_counter = 0  # 记忆提取计数: 每6轮总结一次
        self._tool_item = None
        self._ai_text = ""
        self._pending_tool_text = ""   # 工具调用期间累积的文本(工具完成后显示)

        # 聊天列表
        self.list = QListWidget()
        self.list.setObjectName("chatList")
        self.list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        # 输入区
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("跟你的小宠物说点什么…")
        self.btn = QPushButton("发送")
        self.btn.setObjectName("btnSend")
        self.btn.setCursor(Qt.PointingHandCursor)
        self.btn_new = QPushButton("新聊")
        self.btn_new.setObjectName("btnNew")
        self.btn_new.setCursor(Qt.PointingHandCursor)
        self.btn_new.setToolTip("开启新的一段对话")

        self.btn_shot = QPushButton("看图")
        self.btn_shot.setObjectName("btnShot")
        self.btn_shot.setCursor(Qt.PointingHandCursor)
        self.btn_shot.setToolTip("截屏让我看图回答")

        card = QFrame()
        card.setObjectName("inputCard")
        sh = QGraphicsDropShadowEffect()
        sh.setBlurRadius(24)
        sh.setOffset(0, 4)
        sh.setColor(QColor(232, 121, 168, 35))
        card.setGraphicsEffect(sh)
        row = QHBoxLayout(card)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(8)
        row.addWidget(self.edit, 1)
        row.addWidget(self.btn)
        row.addWidget(self.btn_shot)
        row.addWidget(self.btn_new)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(10)
        lay.addWidget(self.list, 1)
        lay.addWidget(card)

        self.btn.clicked.connect(self.send)
        self.btn_new.clicked.connect(self.new_conversation)
        self.btn_shot.clicked.connect(self.take_screenshot)
        self.edit.returnPressed.connect(self.send)
        self.div_done.connect(self._on_div_result)   # 跨线程完成回调

        # 恢复历史消息到列表
        self._restore_history()
        self._append_hint("主人来找我玩啦~ 有什么事尽管吩咐我哦")

    def _restore_history(self):
        """把本地保存的 messages 历史渲染到聊天列表。"""
        for msg in self._messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "user":
                self._append_bubble(content, "user")
            elif role == "assistant" and content:
                if content.startswith("[主动] "):
                    content = content[len("[主动] "):]
                self._append_bubble(content, "ai")

    def refresh_history(self):
        """从文件重新加载历史(外部追加了主动消息后调用)。"""
        state = load_state()
        if state and state.get("messages"):
            self._session_id = state.get("session_id", self._session_id)
            self._messages = state["messages"]
            # 清空列表重新渲染; 同时重置流式气泡/工具条引用, 防止指向已删除的 Qt 对象
            self.list.clear()
            self._ai_item = None
            self._ai_bubble = None
            self._ai_text = ''
            self._tool_item = None
            self._thinking_item = None
            self._restore_history()
            self.list.scrollToBottom()

    def add_external_message(self, text):
        """实时添加一条外部消息(主动消息/双击评论等)到聊天列表。"""
        if text and self._ai_text != text:  # 避免重复添加
            self._append_bubble(text, "ai")
            self.list.scrollToBottom()

    def append_div_result(self, rec):
        """右键算命完成时, 把结果追加进对话历史(气泡 + 白话简版解读, 不放完整六段)。"""
        if not rec or rec.get("error"):
            return
        bubble = rec.get("bubble", "")
        brief = rec.get("brief") or rec.get("summary") or ""
        text = f"🔮 {bubble}"
        if brief:
            text += "\n\n" + brief
        text += "\n（完整解读：点桌宠头顶的气泡）"
        self._messages.append({"role": "assistant", "content": text, "time": _now_str()})
        save_state(self._session_id, self._messages)
        self._append_bubble(text, "ai")
        self.list.scrollToBottom()

    # ---------- 发送 ----------
    def send(self):
        if self._worker is not None and self._worker.isRunning():
            return
        text = self.edit.text().strip()
        if not text:
            return
        # 算命命令拦截: /卦 塔罗 问题 等(精确匹配, 不做意图识别)
        from divination import parse_command
        cmd = parse_command(text)
        if cmd:
            self._on_div_cmd(cmd[0], cmd[1], text)
            return
        self.edit.clear()
        self._set_busy(True)
        self._append_bubble(text, "user")
        # 情绪: 先按空闲时长衰减激素,再因用户说话涨亲密感
        try:
            tick_hormones()
            on_user_spoke()
        except Exception as e:
            print(f"[Emotion] 用户说话情绪更新失败: {e}")
        # 加入记忆检索(带 prompt 缓存 + 模型未加载时后台预热, 聊天不卡)
        from memory import memory_prompt_cache, memory_search_tool
        memory_prompt = memory_prompt_cache.get(text)
        memory_prompt_cache.warmup()
        # 回忆类问题: 强制注入一次真实查询结果(不赌模型是否主动调工具)
        if _detect_recall(text):
            try:
                recall_json = memory_search_tool(text, top_k=4)
                if recall_json and recall_json != "[]":
                    block = "【主人问的回忆内容查询结果】\n" + recall_json
                    memory_prompt = (memory_prompt + "\n\n" + block) if memory_prompt else block
            except Exception as e:
                print(f"[Memory] 强制回忆查询失败: {e}")
        # 每次都动态生成系统提示词，这样改设置立即生效
        system_prompt = _build_system_prompt()
        # _messages[0] 必须是 system 消息。首次运行时桌宠可能已经主动说过话,
        # append_active_message 会把 assistant 消息写到历史最前面;那种情况下直接改
        # [0]["content"] 会覆盖掉那条主动消息,还会把整段人设当成 assistant 内容发给模型。
        if not self._messages or self._messages[0].get("role") != "system":
            self._messages.insert(0, {"role": "system", "content": ""})
        if memory_prompt:
            self._messages[0]["content"] = system_prompt + "\n\n" + memory_prompt
        else:
            self._messages[0]["content"] = system_prompt
        self._messages.append({"role": "user", "content": text, "time": _now_str()})
        save_state(self._session_id, self._messages)

        name = load_settings().get("pet", {}).get("name", "宠物")
        self._thinking_item = self._append_hint(f"{name}正在思考…")
        self._ai_item = None
        self._ai_bubble = None
        self._ai_text = ""
        self._pending_tool_text = ""
        self.agent_thinking.emit()

        # 记录当前模型名(用于 token 统计)
        try:
            cfg = load_settings()["chat"]
            if chat_mode() == "dsh":
                self._current_model = MODEL
            elif cfg.get("mode") == "direct" and cfg.get("api_key"):
                self._current_model = cfg.get("model", "unknown")
            else:
                self._current_model = "openclaw"
        except Exception:
            self._current_model = "unknown"

        # dsh 模式:直接接本机运行的 DSH(HTTP + WebSocket 流式);
        # 其它模式沿用原来的 OpenAI 兼容 ChatWorker。
        if chat_mode() == "dsh":
            self._worker = DshChatWorker(text)
        else:
            self._worker = ChatWorker(self._messages)
        self._worker.text_chunk.connect(self._on_text_chunk)
        self._worker.thinking.connect(self._on_thinking)
        self._worker.tool_call.connect(self._on_tool_call)
        self._worker.tool_result.connect(self._on_tool_result)
        self._worker.done.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def new_conversation(self):
        if self._worker is not None and self._worker.isRunning():
            return
        # 先把当前对话压缩存进记忆 + 逐字原文归档(永不覆盖)
        if len(self._messages) > 2:
            extract_memory_patch_async(self._messages[1:])
            try:
                append_raw_messages(self._messages[1:])
            except Exception as e:
                print(f"[Memory] 新聊归档失败: {e}")
        self._session_id = "pet-" + uuid.uuid4().hex[:12]
        self._messages = [{"role": "system", "content": _build_system_prompt()}]
        save_state(self._session_id, self._messages)
        self.list.clear()
        self._thinking_item = None
        self._ai_item = None
        self._ai_bubble = None
        self._tool_item = None
        self._ai_text = ""
        if chat_mode() == "dsh":
            clear_dsh_session()   # DSH 侧也开一段新对话
        self._append_hint("已开启新对话,宠物记忆已重置。")

    # ---------- 算命命令 ----------
    def _on_div_cmd(self, div_type, question, raw_text):
        """/卦 命令: 本地排盘 + DeepSeek 解读, 结果以 AI 消息返回。"""
        import threading
        from divination import run_divination
        if getattr(self, "_div_busy", False):
            self._append_hint("上一卦还在算，稍等一下~")
            return
        self._div_busy = True
        self.edit.clear()
        self._set_busy(True)
        self._append_bubble(raw_text, "user")
        # 情绪: 用户说话
        try:
            tick_hormones()
            on_user_spoke()
        except Exception as e:
            print(f"[Emotion] 用户说话情绪更新失败: {e}")
        self._thinking_item = self._append_hint("正在起卦…")
        self.agent_thinking.emit()

        def _work():
            try:
                rec = run_divination(div_type, question)
            except Exception as e:
                rec = {"error": str(e)}
            self.div_done.emit(rec)   # Signal 跨线程 emit, 主线程 _on_div_result 必执行

        threading.Thread(target=_work, daemon=True).start()

    def _on_div_result(self, rec):
        """算命完成: 显示气泡+白话简版解读。"""
        self._remove_thinking()
        self._set_busy(False)
        self._div_busy = False
        if rec.get("error"):
            self._append_hint(f"算命失败：{rec['error']}", color=C_ERR)
            self.agent_error.emit()
            return
        bubble = rec.get("bubble", "算出来了~")
        brief = rec.get("brief") or rec.get("summary") or ""
        text = f"🔮 {bubble}"
        if brief:
            text += "\n\n" + brief
        text += "\n（完整解读：点桌宠头顶的气泡）"
        self._append_bubble(text, "ai")
        self._last_div_id = rec.get("id")
        # 桌宠说完话,耗一点能量
        try:
            on_pet_spoke()
        except Exception as e:
            print(f"[Emotion] 宠物说话能量更新失败: {e}")
        self.reply_received.emit()

    # ---------- 截图提问 ----------
    def take_screenshot(self):
        """截屏后让视觉模型看图回答。"""
        if self._worker is not None and self._worker.isRunning():
            return
        from PySide6.QtWidgets import QInputDialog
        from PySide6.QtCore import QTimer

        # 隐藏窗口,避免截到对话窗口本身
        self.hide()
        QApplication.processEvents()

        def _do_capture():
            import base64
            from PySide6.QtCore import QBuffer
            screen = QApplication.primaryScreen()
            pixmap = screen.grabWindow(0)
            self.show()
            self.raise_()
            self.activateWindow()

            # 转 base64
            buf = QBuffer()
            buf.open(QBuffer.ReadWrite)
            pixmap.save(buf, "PNG")
            img_b64 = base64.b64encode(buf.data().data()).decode()

            # 问用户想问什么
            question, ok = QInputDialog.getText(
                self, "截图提问", "关于这张截图,你想问什么？",
                text="这张图里有什么？简单描述"
            )
            if not ok or not question.strip():
                return

            # 显示用户消息(带[截图]标记)
            self._append_bubble(f"[截图] {question.strip()}", "user")
            self._set_busy(True)
            self._thinking_item = self._append_hint("正在看图…")

            self._vision_worker = VisionWorker(img_b64, question.strip(), self)
            self._vision_worker.done.connect(self._on_vision_done)
            self._vision_worker.error.connect(self._on_vision_error)
            self._vision_worker.start()

        QTimer.singleShot(400, _do_capture)

    def _on_vision_done(self, text):
        """视觉模型回答完成。"""
        self._remove_thinking()
        self._append_bubble(text, "ai")
        self._set_busy(False)
        # 桌宠说完话,耗一点能量
        try:
            on_pet_spoke()
        except Exception as e:
            print(f"[Emotion] 宠物说话能量更新失败: {e}")
        # 记忆/情绪提取: 每6轮对话总结一次(省token)
        try:
            if len(self._messages) >= 2:
                self._memory_turn_counter += 1
                if self._memory_turn_counter >= 6:
                    self._memory_turn_counter = 0
                    extract_memory_patch_async(self._messages[1:])
                    print("[Memory] 已到6轮,触发总结")
        except Exception as e:
            print(f"[Memory] 自动提取触发失败: {e}")
        self.reply_received.emit()

    def _on_vision_error(self, err):
        """视觉模型出错。"""
        self._remove_thinking()
        self._append_hint(f"看图失败: {err}")
        self._set_busy(False)
        self.agent_error.emit()

    # ---------- Worker 回调 ----------
    def _on_text_chunk(self, text):
        # 如果正在显示工具状态,先累积文本,工具完成后再显示
        if self._tool_item is not None:
            self._pending_tool_text += text
            return
        self._remove_thinking()
        if self._ai_bubble is None:
            self._ai_item, self._ai_bubble = self._append_bubble("", "ai")
        self._ai_text += text
        self._ai_bubble.setText(self._ai_text)
        self._set_item_size(self._ai_item, self._ai_bubble)
        self.list.scrollToBottom()

    def _on_thinking(self):
        pass

    def _on_tool_call(self, name, args):
        self._remove_thinking()
        self.agent_working.emit()
        display_name = name
        if "pwsh" in name.lower() or "shell" in name.lower() or "bash" in name.lower():
            display_name = "执行命令"
        elif "file" in name.lower() or "fs" in name.lower() or "str_replace" in name.lower():
            display_name = "操作文件"
        elif "browser" in name.lower() or "navigate" in name.lower():
            display_name = "浏览器操作"
        elif "search" in name.lower() or "google" in name.lower() or "bing" in name.lower():
            display_name = "联网搜索"
        try:
            args_obj = json.loads(args) if isinstance(args, str) else args
            if isinstance(args_obj, dict):
                command = args_obj.get("command", "")
                path = args_obj.get("path", "")
                if command:
                    detail = command[:60]
                elif path:
                    detail = path
                else:
                    detail = str(args_obj)[:60]
            else:
                detail = str(args_obj)[:60]
        except Exception:
            detail = str(args)[:60]

        name = load_settings().get("pet", {}).get("name", "宠物")
        self._tool_item = self._append_hint(
            "✨ %s正在%s: %s" % (name, display_name, detail), color=C_TOOL)

    def _on_tool_result(self, name, result):
        pass

    def _on_error(self, msg):
        self._remove_thinking()
        self._remove_tool_status()
        self._append_hint("错误:" + msg, color=C_ERR)
        self._set_busy(False)
        self.agent_error.emit()

    def _on_done(self):
        self._remove_thinking()
        # 如果有工具调用期间累积的文本,现在显示出来
        if self._pending_tool_text:
            self._remove_tool_status()
            if self._ai_bubble is None:
                self._ai_item, self._ai_bubble = self._append_bubble("", "ai")
            self._ai_text += self._pending_tool_text
            self._ai_bubble.setText(self._ai_text)
            self._set_item_size(self._ai_item, self._ai_bubble)
            self._pending_tool_text = ""
        else:
            self._remove_tool_status()
        # 保存 assistant 回复到历史
        if self._ai_text:
            self._messages.append({"role": "assistant", "content": self._ai_text, "time": _now_str()})
            save_state(self._session_id, self._messages)
            self.bubble_say.emit(self._ai_text)  # 通知桌宠显示气泡
        self._set_busy(False)
        # 桌宠说完话,耗一点能量
        try:
            on_pet_spoke()
        except Exception as e:
            print(f"[Emotion] 宠物说话能量更新失败: {e}")
        # 记忆/情绪提取: 每6轮对话总结一次(省token)
        try:
            if len(self._messages) >= 2:
                self._memory_turn_counter += 1
                if self._memory_turn_counter >= 6:
                    self._memory_turn_counter = 0
                    extract_memory_patch_async(self._messages[1:])
                    print("[Memory] 已到6轮,触发总结")
        except Exception as e:
            print(f"[Memory] 自动提取触发失败: {e}")
        self.reply_received.emit()

    # ---------- 显示 ----------
    def _append_hint(self, text, color="#c8a8b8"):
        item = QListWidgetItem(text)
        item.setFlags(Qt.NoItemFlags)
        item.setTextAlignment(Qt.AlignCenter)
        item.setForeground(QColor(color))
        self.list.addItem(item)
        self.list.scrollToBottom()
        return item

    def _append_bubble(self, text, who):
        bubble = QLabel(text)
        bubble.setWordWrap(True)
        bubble.setFixedWidth(BUBBLE_W)
        bubble.setStyleSheet(BUBBLE_USER if who == "user" else BUBBLE_AI)
        bubble.setTextInteractionFlags(Qt.TextSelectableByMouse)

        box = QWidget()
        blay = QHBoxLayout(box)
        blay.setContentsMargins(0, 0, 0, 0)
        blay.setSpacing(0)
        if who == "user":
            blay.addStretch(1)
            blay.addWidget(bubble, 0, Qt.AlignRight)
        else:
            blay.addWidget(bubble, 0, Qt.AlignLeft)
            blay.addStretch(1)

        item = QListWidgetItem(self.list)
        item.setFlags(Qt.NoItemFlags)
        self._set_item_size(item, bubble)
        self.list.addItem(item)
        self.list.setItemWidget(item, box)
        self.list.scrollToBottom()
        return item, bubble

    def _set_item_size(self, item, bubble):
        w = max(BUBBLE_W + 40, self.list.viewport().width() - 16)
        # heightForWidth 只算文字高度,要加上上下 padding(11*2) + border(1*2) + 余量
        item.setSizeHint(QSize(w, bubble.heightForWidth(BUBBLE_W) + 28))

    def _remove_thinking(self):
        if self._thinking_item is not None:
            try:
                row = self.list.row(self._thinking_item)
                if row >= 0:
                    self.list.takeItem(row)
            except RuntimeError:
                # item 已经被删除了，忽略
                pass
            self._thinking_item = None

    def _remove_tool_status(self):
        if self._tool_item is not None:
            try:
                row = self.list.row(self._tool_item)
                if row >= 0:
                    self.list.takeItem(row)
            except RuntimeError:
                # item 已经被删除了，忽略
                pass
            self._tool_item = None

    def _set_busy(self, busy):
        self.btn.setEnabled(not busy)
        self.btn_new.setEnabled(not busy)
        self.edit.setEnabled(not busy)

    def closeEvent(self, event):
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    w = ChatWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()


