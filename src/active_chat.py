# -*- coding: utf-8 -*-
"""主动消息:桌宠主动调 SiliconFlow 生成一句话,用气泡显示。

直连 SiliconFlow API(不经过 OpenClaw),省 token 开销。
防重复:随机语气 + 最近说话历史 + 重复自动重生成。
"""
import difflib
import json
import random
from datetime import datetime
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from token_stats import record_usage

from PySide6.QtCore import QThread, Signal

from settings_window import load_settings, build_pet_identity

import chat_window  # 用于把主动消息追加到对话历史

# ── 主动功能配置(每次调用时动态读取,确保设置修改后立即生效) ──
DEFAULT_BASE_URL = "https://api.siliconflow.cn/v1"
DEFAULT_TEXT_MODEL = "Qwen/Qwen3-8B"
# 视觉模型走 DeepSeek(快、准、便宜,单图~1厘)
DEFAULT_VISION_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_VISION_MODEL = "deepseek-flash"


def _get_active_cfg():
    """每次调用时重新读取 settings.json,返回最新配置。"""
    try:
        cfg = load_settings()["active"]
    except Exception:
        cfg = {}
    return {
        "base_url": cfg.get("base_url") or DEFAULT_BASE_URL,
        "api_key": cfg.get("api_key") or "",
        "text_model": cfg.get("text_model") or DEFAULT_TEXT_MODEL,
        # 视觉独立配置: 默认走 DeepSeek
        "vision_base_url": cfg.get("vision_base_url") or DEFAULT_VISION_BASE_URL,
        "vision_api_key": cfg.get("vision_api_key") or "",
        "vision_model": cfg.get("vision_model") or DEFAULT_VISION_MODEL,
    }

# ── 桌宠人设: 骨架(写死) + 用户设置(名字/称呼/额外设定) 动态合并 ──
def _pet_name():
    return build_pet_identity()[0]


def _owner_name():
    return build_pet_identity()[1]


def _build_persona():
    """精简条件模板: 骨架只保留说话方式, 身份/关系/动作由 build_pet_identity() 决定。
    女友模式不注入宠物动作词。改设置立即生效。"""
    name, owner, persona, idt = build_pet_identity()
    text = (
        f"你是 {name}，{owner}桌面上的{idt['身份']}，年龄感约 {idt['年龄']} 岁。"
        f"你是{owner}的{idt['关系']}，不是 AI、不是助手。"
        f"【优先级】额外设定 > 默认。冲突时按额外设定，只保留微信短句、不 AI 腔、口头禅。"
        f"【默认性格】(可被额外设定覆盖) 嘴硬心软、小调皮。"
        f"【主动评论】像微信发消息，1-2 句，短，带感受，不服务腔。"
        f"动作符合{idt['关系']}，禁用动作：{idt['禁用动作']}。"
    )
    if persona:
        text += f"\n【额外设定】{persona}"
    return text

# ── 防重复:最近说话历史 ──
_recent_says = []  # 最近说过的话,最多 30 条
_MAX_HISTORY = 30

# 随机语气池
_MOODS = [
    "撒娇卖萌", "温柔关心", "俏皮吐槽", "热情鼓励",
    "傲娇别扭", "元气满满", "慵懒随意", "紧张担心",
    "好奇八卦", "一本正经", "坏坏调侃", "软萌委屈",
]

# 修辞手法池(增加多样性)
_RHETORICS = [
    "用比喻", "用夸张", "用反问", "用感叹",
    "用拟声词", "用叠词", "用短句", "用反差",
]

# Fallback 句子池(模型生成的都重复时用,确保不重复)
_FALLBACK_SAYS = [
    "主人加油呀~", "稳住我们能赢！", "哇好厉害！", "小心点哦~",
    "休息一下眼睛吧", "今天状态不错嘛", "别太拼啦", "我陪着你呢~",
    "深呼吸放轻松", "这波操作可以啊", "别紧张慢慢来", "你超棒的！",
    "记得喝水哦", "坐姿端正点~", "打完这把休息吧", "我相信你~",
    "哇哦精彩！", "小心身后！", "冲冲冲！", "悠着点别太累~",
    "主人最棒了！", "这把稳了！", "别慌问题不大", "劳逸结合哦~",
    "我在旁边给你加油", "专心点别看我~", "打完奖励自己一下", "今天也要开心哦~",
    "别熬夜啦", "手酸不酸？", "眼睛累不累？", "你认真的样子真好看~",
    "这把输了我背锅", "赢了请我吃小鱼干！", "哇这手速！", "冷静冷静~",
]


def _random_mood():
    """随机选一种语气。"""
    return random.choice(_MOODS)


def _time_hint():
    """按时间段返回语气提示(时间感知, 0 token)。"""
    h = datetime.now().hour
    if 5 <= h < 9:
        return "现在是清晨,你刚醒还有点迷糊,说话软软的带点起床气,乖乖跟主人道早安。"
    if 9 <= h < 12:
        return "现在是上午,你元气满满,说话轻快有活力。"
    if 12 <= h < 14:
        return "现在是中午,你有点犯困,说话懒洋洋的,偶尔打个哈欠。"
    if 14 <= h < 18:
        return "现在是下午,你精神不错,说话活泼随意。"
    if 18 <= h < 22:
        return "现在是晚上,你心情放松开心,说话带点撒娇的黏糊劲。"
    if 22 <= h < 24:
        return "现在是深夜,你困了但还想陪主人,说话软糯慵懒,会催主人早点睡。"
    return "现在是凌晨,夜深人静,你轻声说话怕吵到别人,特别想窝在主人身边。"


def _emotion_hint():
    """从情绪系统读当前心情, 生成一句语境注入评论 prompt(语气随心情走, 不像精神分裂)。"""
    try:
        from memory import get_emotion_status
        emoji, word, _, state = get_emotion_status()
        long = state.get("longing", 0.0)
        nrg = state.get("energy", 0.85)
        hint = f"你现在的心情：{emoji}{word}"
        if long > 0.5:
            hint += "，特别想主人，话里带点撒娇和委屈"
        elif long > 0.25:
            hint += "，有点想主人，语气软一点"
        if nrg < 0.2:
            hint += "，而且很困，话少而懒"
        elif nrg < 0.4:
            hint += "，有点累，别太闹腾"
        return hint
    except Exception:
        return ""


def _is_dup(text):
    """检查是否和最近说过的话太像(相似度>0.4或前4字相同)。"""
    if not text:
        return False
    for old in _recent_says[-12:]:
        if text == old:
            return True
        if len(text) >= 4 and len(old) >= 4 and text[:4] == old[:4]:
            return True
        ratio = difflib.SequenceMatcher(None, text, old).ratio()
        if ratio > 0.4:
            return True
    return False


def _remember(text):
    """记录到历史。"""
    if text:
        _recent_says.append(text)
        if len(_recent_says) > _MAX_HISTORY:
            _recent_says.pop(0)


def _history_hint(n=10):
    """生成'不要重复最近说过的话'的提示。"""
    recent = _recent_says[-n:]
    if not recent:
        return ""
    return (
        "【极其重要】下面这些话最近已经说过了，你必须和每一句都完全不同："
        "开头前3个字绝对不能相同、用词不能重复、句式不能相似、角度要换。"
        "绝对不能有任何相似感，换个全新的说法！最近说过的：" + "；".join(recent)
    )


def _record_chat_usage(model, messages, output_text, usage):
    """用 API 真实 usage 记录(缓存命中/未命中拆分); 缺失字段则回退估算。"""
    try:
        in_tok = usage.get("prompt_tokens")
        out_tok = usage.get("completion_tokens")
        cache_hit = usage.get("prompt_cache_hit_tokens")
        if in_tok is None or out_tok is None:
            in_tok = out_tok = cache_hit = None
        texts = []
        for m in messages:
            c = m.get("content")
            if isinstance(c, str):
                texts.append(c)
            elif isinstance(c, list):
                for p in c:
                    if isinstance(p, dict) and p.get("type") == "text":
                        texts.append(p.get("text", ""))
        from token_stats import record_usage
        record_usage(model, "\n".join(texts), output_text,
                     input_tokens=in_tok, output_tokens=out_tok,
                     cache_hit_tokens=cache_hit)
    except Exception:
        pass


def _sf_chat(messages, max_tokens=100, model=None, vision=False):
    """调 API chat completions,返回回复文本。每次调用动态读取配置。
    vision=True 时走 DeepSeek 视觉配置并关闭思考模式。"""
    cfg = _get_active_cfg()
    if vision:
        base_url = cfg["vision_base_url"]
        api_key = cfg["vision_api_key"]
        use_model = model or cfg["vision_model"]
        body = {
            "model": use_model,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": False,
            "thinking": {"type": "disabled"},
        }
    else:
        base_url = cfg["base_url"]
        api_key = cfg["api_key"]
        use_model = model or cfg["text_model"]
        body = {
            "model": use_model,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": False,
        }
    body_data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        base_url + "/chat/completions",
        data=body_data,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    msg = data["choices"][0]["message"]
    content = (msg.get("content") or "").strip()
    # DeepSeek 可能把内容放 reasoning_content,兜底
    if not content:
        content = (msg.get("reasoning_content") or "").strip()
    # 用量统计: 用 API 返回的真实 token(含缓存命中拆分), 拿不到就估算
    try:
        _record_chat_usage(use_model, messages, content, data.get("usage") or {})
    except Exception:
        pass
    return content


def _min_similarity(text):
    """计算和历史的最小相似度(越低越不重复)。"""
    if not _recent_says:
        return 0.0
    return min(difflib.SequenceMatcher(None, text, old).ratio() for old in _recent_says[-12:])


def _trim_to_short(text, max_len=20):
    """把回复截短: 优先截到最后一个标点, 仍超就硬切。"""
    if not text:
        return text
    text = text.strip().strip('"').strip("'")
    if len(text) <= max_len:
        return text
    # 从 max_len 往前找最近的标点
    for cut in range(max_len, max_len // 2, -1):
        if text[cut - 1] in "。！？!?~～，,、；;":
            return text[:cut]
    return text[:max_len]


def _generate_diverse(user_prompt, max_tokens=100, rounds=2):
    """生成一句话,每轮生成2个候选选最不重复的,最多rounds轮。返回文本或 None。"""
    candidates = []
    for r in range(rounds):
        for i in range(2):  # 每轮2个候选,共4个
            mood = _random_mood()
            rhetoric = random.choice(_RHETORICS)
            hint = _history_hint(10)
            full_prompt = user_prompt
            if hint:
                full_prompt += f"\n\n{hint}"
            full_prompt += (
                f"\n\n这次用「{mood}」的语气，{rhetoric}的方式来说，"
                f"必须和之前说过的所有话在开头、用词、句式、角度上都完全不同。"
                f"\n长度要有变化：有时候两个字（好险、完了、诶嘿），有时候一整句话，别每句都一样长。"
            )
            try:
                text = _sf_chat([
                    {"role": "system", "content": _build_persona() + "\n" + _time_hint()},
                    {"role": "user", "content": full_prompt},
                ], max_tokens=max_tokens)
                if text:
                    candidates.append(text)
                    if not _is_dup(text):
                        _remember(text)
                        return text
            except Exception:
                pass
    # 所有候选都重复,从 fallback 池选不重复的 (称呼动态替换)
    owner = _owner_name()
    for _ in range(15):
        fb = random.choice(_FALLBACK_SAYS).replace("主人", owner)
        if not _is_dup(fb):
            _remember(fb)
            return fb
    # fallback 也重复了,选相似度最低的候选
    if candidates:
        best = min(candidates, key=_min_similarity)
        _remember(best)
        return best
    return random.choice(_FALLBACK_SAYS).replace("主人", owner)


class ActiveChatWorker(QThread):
    """后台线程:纯文本主动消息(心率/凌晨/游戏开场)。"""
    finished_text = Signal(str)
    failed = Signal(str)

    def __init__(self, user_prompt, parent=None):
        super().__init__(parent)
        self._user_prompt = user_prompt

    def run(self):
        try:
            eh = _emotion_hint()
            prompt = f"{self._user_prompt}\n\n{eh}" if eh else self._user_prompt
            text = _generate_diverse(prompt)
            if text:
                self.finished_text.emit(text)
            else:
                self.failed.emit("空回复")
        except Exception as e:
            self.failed.emit(str(e))


def active_say(pet, user_prompt, fallback_text=None):
    """纯文本主动消息:心率/凌晨/游戏开场。"""
    def _on_text(text):
        pet._show_bubble(text)
        chat_window.append_active_message(text)
        # 实时刷新AI对话窗口(如果已打开)
        if pet._chat_win and pet._chat_win.isVisible():
            pet._chat_win.add_external_message(text)

    def _on_fail(err):
        if fallback_text:
            pet._show_bubble(fallback_text)

    worker = ActiveChatWorker(user_prompt, pet)
    worker.finished_text.connect(_on_text)
    worker.failed.connect(_on_fail)
    worker.start()
    if not hasattr(pet, "_active_workers"):
        pet._active_workers = []
    pet._active_workers.append(worker)
    worker.finished.connect(lambda: pet._active_workers.remove(worker) if worker in pet._active_workers else None)


class GameWatchWorker(QThread):
    """游戏观察:视觉描述(短) + 文本模型生成话术(防重复好)。"""
    finished_text = Signal(str)
    failed = Signal(str)

    def __init__(self, image_b64, game_name, hr=None, parent=None):
        super().__init__(parent)
        self._image_b64 = image_b64
        self._game_name = game_name
        self._hr = hr

    def run(self):
        try:
            cfg = _get_active_cfg()
            # 一步:视觉模型直接看图生成话术(DeepSeek,快且准)
            hr_hint = f"，{_owner_name()}当前心率 {self._hr}" if self._hr else ""
            user_prompt = (
                f"{_owner_name()}正在玩{self._game_name}{hr_hint}。"
                f"{_time_hint()}"
                f"{_emotion_hint()}。"
                f"你是{_pet_name()},像朋友一样看屏幕随口说一句,"
                f"吐槽/鼓励/提醒/惊叹都行,只针对画面里的具体细节,别总结画面,别客套。"
                f"长度随意:可能就两个字(好险/完了),也可能一句话,别每句都一个模板。"
            )
            text = _sf_chat([
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{self._image_b64}"}},
                    {"type": "text", "text": user_prompt},
                ]}
            ], max_tokens=48, model=cfg["vision_model"], vision=True)
            if not text:
                text = f"在玩{self._game_name}，加油~"
                _remember(text)
            self.finished_text.emit(text)
        except Exception as e:
            self.failed.emit(str(e))


def game_watch_say(pet, image_b64, game_name, hr=None, fallback_text=None):
    """游戏观察:截屏分析后说话。"""
    def _on_text(text):
        pet._show_bubble(text)
        chat_window.append_active_message(text)
        if pet._chat_win and pet._chat_win.isVisible():
            pet._chat_win.add_external_message(text)

    def _on_fail(err):
        if fallback_text:
            pet._show_bubble(fallback_text)

    worker = GameWatchWorker(image_b64, game_name, hr, pet)
    worker.finished_text.connect(_on_text)
    worker.failed.connect(_on_fail)
    worker.start()
    if not hasattr(pet, "_active_workers"):
        pet._active_workers = []
    pet._active_workers.append(worker)
    worker.finished.connect(lambda: pet._active_workers.remove(worker) if worker in pet._active_workers else None)


class ScreenshotCommentWorker(QThread):
    """双击截屏评论:直接视觉模型看图生成话术(一步搞定)。"""
    finished_text = Signal(str)
    failed = Signal(str)

    def __init__(self, image_b64, extra_hint=None, parent=None):
        super().__init__(parent)
        self._image_b64 = image_b64
        self._extra_hint = extra_hint

    def run(self):
        try:
            cfg = _get_active_cfg()
            # 随机选一种语气,避免千篇一律
            tones = [
                "俏皮活泼", "温柔关心", "吐槽打趣", "撒娇卖萌", "元气满满",
                "慵懒犯困", "好奇八卦", "傲娇毒舌", "软萌治愈", "元气满满"
            ]
            tone = random.choice(tones)
            _, _, _, idt = build_pet_identity()
            context_hint = f"{self._extra_hint}。" if self._extra_hint else ""
            user_prompt = (
                f"你是{_pet_name()},{_owner_name()}桌面上的{idt['身份']},是{_owner_name()}的{idt['关系']},性格{tone},很爱{_owner_name()}。"
                f"{_time_hint()}"
                f"{_emotion_hint()}。"
                f"{context_hint}"
                f"看看{_owner_name()}屏幕上在做什么,用{tone}的语气随口说一句短话,像发微信一样短。"
                f"只输出一句话,10~20字,不许超过20字,不要客套,不要'{_owner_name()}你好',要接地气。"
                f"动作风格:{idt['动作风格']},禁用动作:{idt['禁用动作']}。"
            )
            text = _sf_chat([
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{self._image_b64}"}},
                    {"type": "text", "text": user_prompt},
                ]}
            ], max_tokens=48, model=cfg["vision_model"], vision=True)
            if not text:
                text = f"{_owner_name()}找我玩啦~"
            # 硬截断: 超过20字截到最后一个标点, 仍超就硬切
            text = _trim_to_short(text, 20)
            _remember(text)
            self.finished_text.emit(text)
        except Exception as e:
            self.failed.emit(str(e))


def screenshot_comment_say(pet, image_b64, fallback_text=None, extra_hint=None):
    """双击/主动截屏评论。extra_hint: 触发语境(如'好久没陪我了,有点想你')。"""
    def _on_text(text):
        pet._show_bubble(text)
        chat_window.append_active_message(text)
        if pet._chat_win and pet._chat_win.isVisible():
            pet._chat_win.add_external_message(text)

    def _on_fail(err):
        if fallback_text:
            pet._show_bubble(fallback_text)

    def _on_done():
        if worker in pet._active_workers:
            pet._active_workers.remove(worker)
        pet._double_tap_busy = False

    worker = ScreenshotCommentWorker(image_b64, extra_hint, pet)
    worker.finished_text.connect(_on_text)
    worker.failed.connect(_on_fail)
    worker.finished.connect(_on_done)
    worker.start()
    if not hasattr(pet, "_active_workers"):
        pet._active_workers = []
    pet._active_workers.append(worker)