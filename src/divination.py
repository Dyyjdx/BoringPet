# -*- coding: utf-8 -*-
"""算命模块（自用精简版）
本地排盘(oraclebone) + DeepSeek 解读 + SQLite 记录 + 缓存 + 降级 + 待应验。

- 排盘: oraclebone 本地脚本, 0 token, 毫秒级
- 解读: DeepSeek(deepseek-chat) 一次调用返回结构化 JSON, 失败重试一次, 再失败本地模板
- 记录: SQLite 单表 divination_records
- 缓存: 同卦同问题 24h 内复用解读
- 八字: 发送给模型前剔除原始出生时间/农历日期(只传四柱/五行/日主), 生日不上云
"""
import json
import re
import sqlite3
import threading
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from paths import app_dir
from token_stats import record_usage

DB_PATH = app_dir() / "memory" / "divination.db"

# 小六壬六掌诀中文名（index 1..6）
XLR_NAMES = {1: "大安", 2: "留连", 3: "速喜", 4: "赤口", 5: "小吉", 6: "空亡"}

# 五行/阴阳 英文键 -> 中文显示（oraclebone 输出是英文）
ELEM_CN = {"wood": "木", "fire": "火", "earth": "土", "metal": "金", "water": "水"}
POL_CN = {"yang": "阳", "yin": "阴"}

DIV_LABELS = {"tarot": "塔罗", "iching": "六爻", "xiaoliuren": "小六壬", "bazi": "八字"}


# ---------- 数据库 ----------
def _conn():
    try:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"[算命] 无法创建数据目录 {DB_PATH.parent}: {e}")
    c = sqlite3.connect(str(DB_PATH))
    c.execute(
        """CREATE TABLE IF NOT EXISTS divination_records(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT, question TEXT,
            raw_json TEXT, interpretation TEXT, bubble TEXT, summary TEXT,
            forecast TEXT, status TEXT DEFAULT 'pending',
            created_at TEXT, review_at TEXT, feedback TEXT,
            important INTEGER DEFAULT 0, tags TEXT)"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS divination_cache(
            cache_key TEXT PRIMARY KEY, response TEXT, created_at TEXT)"""
    )
    return c


# ---------- 排盘（本地, 0 token） ----------
def _cast_raw(div_type, question):
    """本地排盘, 返回 raw dict; 失败返回 None。
    八字没设置生日/格式错误时抛 ValueError(带明确中文提示, 由 run_divination 透传给用户)。"""
    from settings_window import load_settings
    cfg = load_settings().get("divination", {})
    if div_type == "bazi":
        birth = (cfg.get("birth") or "").strip()
        if not birth:
            raise ValueError("还没设置出生时间：请打开 宠物设置 → 算命 → 填公历生日（示例 2006-02-26T15:40）")
        try:
            from oraclebone.bazi import cast
            return cast(birth, timezone="Asia/Shanghai")
        except Exception as e:
            raise ValueError(f"出生时间格式无法解析：{birth}（示例 2006-02-26T15:40）") from e
    if div_type == "xiaoliuren":
        # 传统农历法: 公历时间 -> lunar-python 转农历月日 + 地支时辰, 不再用公历回退
        from oraclebone.xiaoliuren import cast_lunar_time
        try:
            return cast_lunar_time(datetime.now().isoformat(timespec="seconds"))
        except ValueError as ve:
            # 闰月等无法自动起局的情况, 明确提示而不是笼统"排盘失败"
            raise ValueError(f"小六壬起局失败：{ve}") from ve
    try:
        if div_type == "tarot":
            from oraclebone.tarot import draw
            return draw(
                cfg.get("deck", "major"),
                cfg.get("spread", "three-card"),
                cfg.get("reversals", True),
                None,
            )
        if div_type == "iching":
            from oraclebone.iching import cast
            return cast("coins", None, None)
    except Exception as e:
        print(f"[Divination] 排盘失败 {div_type}: {e}")
        return None
    return None


def _filter_bazi(raw):
    """八字结果发送给模型前, 剔除含出生信息的字段(生日不上云)。"""
    if not isinstance(raw, dict):
        return raw
    keep = {}
    for k in ("system", "accuracy", "engine", "shengxiao", "day_master", "pillars",
              "five_elements_tally", "notes", "lunar_year_zodiac"):
        if k in raw:
            keep[k] = raw[k]
    return keep


# ---------- DeepSeek 解读 ----------
def _ds_chat(messages, max_tokens=1200):
    """调 DeepSeek chat completions, 返回 (content, usage)。失败抛异常。"""
    from settings_window import load_settings
    cfg = load_settings().get("chat", {})
    model = cfg.get("model", "deepseek-chat")
    body = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "stream": False,
    }
    req = urllib.request.Request(
        cfg["base_url"] + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg['api_key']}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    msg = data["choices"][0]["message"]
    content = (msg.get("content") or "").strip()
    if not content:
        content = (msg.get("reasoning_content") or "").strip()
    return content, data.get("usage") or {}


def _record_ds_usage(messages, output_text, usage):
    """把一次解读计入用量统计。"""
    try:
        from settings_window import load_settings
        model = load_settings().get("chat", {}).get("model", "deepseek-chat")
        in_tok = usage.get("prompt_tokens")
        out_tok = usage.get("completion_tokens")
        cache_hit = usage.get("prompt_cache_hit_tokens")
        texts = []
        for m in messages:
            c = m.get("content")
            if isinstance(c, str):
                texts.append(c)
        record_usage(model, "\n".join(texts), output_text,
                     input_tokens=in_tok, output_tokens=out_tok,
                     cache_hit_tokens=cache_hit)
    except Exception as e:
        print(f"[Divination] 用量记录失败: {e}")


SYSTEM_PROMPT = """你是 BoringPet 的占卜解读助手。你拿到的占卜结果(牌面/卦象/六壬/八字)是本地脚本按古籍规则排出的, 不是模型编造的。
你的任务: 结合用户的问题, 把结果解读成一份"像朋友认真聊天"的解读, 不装神弄鬼、不吓人。

严格只输出一个 JSON 对象, 不要任何其他文字、不要 markdown 代码块。格式:
{
  "bubble": "一句桌宠口吻的话, 20字以内, 带语气",
  "brief": "给主人看的白话简版解读, 2-3句, 不用玄学术语, 直接说结果和该怎么做, 像朋友说人话。例如'整体偏顺, 今天适合收尾和整理, 别急着开新坑'",
  "interpretation_md": "完整解读, 用markdown, 含六段: ## 结果 / ## 象征解读 / ## 情境映射 / ## 隐藏变量 / ## 可行指引 / ## 边界",
  "summary": "30字以内的一句话摘要, 用于记忆",
  "forecast": {"claim": "一句话对未来提示", "domain": "事业|感情|学业|健康|财运|综合|其他", "direction": "偏吉|偏凶|中性|待观察", "time_window": "如30天内, 不确定就写null"},
  "memory_note": "以[占卜]开头的一句话, 如 [占卜] 塔罗问跳槽, 偏吉需谨慎, 待应验"
}
规则:
- 所有判断必须来自输入的排盘 JSON, 禁止编造牌/卦/干支。
- 不要宿命论, 用"一种读法是""这指向"等表述, 不承诺必然发生。
- 不涉及医疗/法律/财务的具体决策建议。
- 八字只解读四柱/五行/日主/生肖, 不假装读大运流年神煞(结果里没有)。
- 用户问题为空时, 按"最近心境"解读。"""


def _parse_json_response(content):
    """从模型回复里剥离 markdown 代码块并解析 JSON。"""
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()
    # 截取第一个 { 到最后一个 }
    s, e = text.find("{"), text.rfind("}")
    if s == -1 or e == -1 or e <= s:
        raise ValueError("回复中没有 JSON")
    return json.loads(text[s : e + 1])


def _interpret(raw, div_type, question):
    """解读: 缓存 -> DeepSeek(重试1次) -> 本地模板。返回 dict。"""
    import hashlib
    import re as _re

    send_raw = _filter_bazi(raw) if div_type == "bazi" else raw
    if div_type == "xiaoliuren":
        # 排盘 JSON 的 inputs.hour 是地支时辰序号(1=子..12=亥), 不是公历小时。
        # 补上中文时辰说明, 防止模型把"戌时=序号11"误读成"上午11点/接近正午"。
        try:
            send_raw = dict(raw)
            ins = dict(send_raw.get("inputs") or {})
            h = ins.get("hour")
            if isinstance(h, int) and 1 <= h <= 12:
                dg = "子丑寅卯辰巳午未申酉戌亥"
                ins["hour_cn"] = f"{dg[h - 1]}时(地支序号{h}, 非公历小时)"
            send_raw["inputs"] = ins
        except Exception:
            pass
    # 缓存 key 只含 流派|问题(不含 raw_hash):
    # 塔罗/六爻每次洗牌都随机, raw 每次不同, 带 raw_hash 会让缓存永远不命中。
    # 同卦同问题 24h 内直接复用上次解读(省钱+秒回), 想重新起卦请换问题或等缓存过期。
    key = hashlib.md5(f"{div_type}|{question.strip()}".encode("utf-8")).hexdigest()

    # 缓存: 24h 内复用（一次查询取 response+created_at）
    c = _conn()
    try:
        row = c.execute(
            "SELECT response, created_at FROM divination_cache WHERE cache_key=?", (key,)
        ).fetchone()
        if row:
            try:
                dt = datetime.fromisoformat(row[1])
                if datetime.now() - dt < timedelta(hours=24):
                    cached = json.loads(row[0])
                    # 旧缓存没有 brief 字段, 用 summary 兜底
                    if not cached.get("brief"):
                        cached["brief"] = cached.get("summary") or ""
                    return cached
            except Exception:
                pass
    except Exception:
        pass

    label = DIV_LABELS.get(div_type, div_type)
    user_prompt = (
        f"[占卜类型] {label}\n"
        f"[用户问题] {question.strip() or '（空，按最近心境解读）'}\n"
        f"[排盘结果]\n{json.dumps(send_raw, ensure_ascii=False, indent=2)}"
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    result = None
    last_err = None
    from_model = False
    for attempt in range(2):
        try:
            content, usage = _ds_chat(messages, max_tokens=1400 if attempt else 1200)
            _record_ds_usage(messages, content, usage)
            parsed = _parse_json_response(content)
            # 校验必需字段
            for f in ("bubble", "interpretation_md", "summary", "forecast", "memory_note"):
                if f not in parsed:
                    raise ValueError(f"缺少字段 {f}")
            # brief 缺失时用 summary 兜底(兼容旧缓存/模型偶尔漏输出)
            if not parsed.get("brief"):
                parsed["brief"] = parsed.get("summary") or ""
            result = parsed
            from_model = True
            break
        except Exception as e:
            last_err = e
            print(f"[Divination] 解读第{attempt + 1}次失败: {e}")
            if attempt == 0:
                messages[-1]["content"] = user_prompt + "\n\n再次强调: 只输出合法 JSON, 不要任何解释文字或代码块。"
    if result is None:
        print(f"[Divination] 解读失败, 走本地模板: {last_err}")
        result = _local_fallback(raw, div_type, question)

    # 写缓存: 只有模型解读成功才缓存。失败 fallback 不写缓存,
    # 否则坏结果会锁死 cache_key 24 小时, 用户反复看到"模型解读暂不可用"。
    if from_model:
        try:
            c.execute(
                "INSERT OR REPLACE INTO divination_cache(cache_key, response, created_at) VALUES(?,?,?)",
                (key, json.dumps(result, ensure_ascii=False), datetime.now().isoformat(timespec="seconds")),
            )
            c.commit()
        except Exception:
            pass
    return result


def _local_fallback(raw, div_type, question):
    """模型不可用时, 用排盘结果自带字段拼一句直读。"""
    try:
        if div_type == "tarot":
            cards = raw.get("cards", [])
            lines = [
                f"{cd.get('position')}: {cd.get('name')}（{'逆位' if cd.get('reversed') else '正位'}）- {cd.get('meanings', {}).get('reading_cue', '')}"
                for cd in cards
            ]
            bubble = f"抽到 {cards[0].get('name') if cards else '未知'}，点我看解读~"
            return {
                "bubble": bubble,
                "interpretation_md": "## 结果\n" + "\n\n".join(lines) + "\n\n> 模型解读暂不可用，以上为牌面直读。",
                "summary": f"塔罗: {bubble}",
                "forecast": {"claim": None, "domain": "综合", "direction": "待观察", "time_window": None},
                "memory_note": f"[占卜] 塔罗{question or '最近心境'}，待观察",
            }
        if div_type == "iching":
            ph = raw.get("primary_hexagram", {})
            rh = raw.get("resulting_hexagram", {})
            bubble = f"本卦 {ph.get('name', '?')}，点我看解读~"
            return {
                "bubble": bubble,
                "interpretation_md": f"## 结果\n本卦: {ph.get('number')} {ph.get('name')}\n卦辞: {ph.get('texts', {}).get('judgment', '')}\n\n之卦: {rh.get('name', '无') if rh else '无'}\n\n> 模型解读暂不可用，以上为卦辞直读。",
                "summary": f"六爻: 本卦 {ph.get('name', '?')}",
                "forecast": {"claim": None, "domain": "综合", "direction": "待观察", "time_window": None},
                "memory_note": f"[占卜] 六爻{question or '最近心境'}，卦{ph.get('number', '?')}",
            }
        if div_type == "xiaoliuren":
            pos = raw.get("position", {})
            idx = pos.get("index", 0)
            cn = XLR_NAMES.get(idx, pos.get("name_en", "?"))
            bubble = f"小六壬得「{cn}」，点我看解读~"
            return {
                "bubble": bubble,
                "interpretation_md": f"## 结果\n小六壬落位: {cn}\n关键词: {', '.join(pos.get('keywords', []))}\n\n> 模型解读暂不可用，以上为掌诀直读。",
                "summary": f"小六壬: {cn}",
                "forecast": {"claim": None, "domain": "综合", "direction": "待观察", "time_window": None},
                "memory_note": f"[占卜] 小六壬{question or '此刻心境'}，得{cn}",
            }
        if div_type == "bazi":
            pillars = raw.get("pillars", {})
            tally = raw.get("five_elements_tally", {})
            line = " ".join(f"{k}柱{p['ganzhi']}" for k, p in pillars.items())
            dm = raw.get("day_master", {})
            tally_s = " ".join(
                f"{ELEM_CN.get(k, k)}:{v}" for k, v in tally.items()
            )
            stem = dm.get("stem", "?")
            elem = ELEM_CN.get(dm.get("element", ""), dm.get("element", ""))
            pol = POL_CN.get(dm.get("polarity", ""), "")
            bubble = f"八字排出，日主{stem}{elem}，点我看解读~"
            return {
                "bubble": bubble,
                "interpretation_md": f"## 结果\n{line}\n日主: {stem}（{elem}、{pol}）\n五行: {tally_s}\n\n> 模型解读暂不可用，以上为四柱直读。",
                "summary": f"八字: 日主{stem}{elem}，五行{tally_s}",
                "forecast": {"claim": None, "domain": "综合", "direction": "待观察", "time_window": None},
                "memory_note": f"[占卜] 八字{question or '命盘'}，日主{stem}{elem}",
            }
    except Exception as e:
        print(f"[Divination] 本地模板失败: {e}")
    return {
        "bubble": "算出来了，点我看解读~",
        "interpretation_md": "## 结果\n（排盘成功但解读暂不可用）",
        "summary": "占卜完成",
        "forecast": {"claim": None, "domain": "综合", "direction": "待观察", "time_window": None},
        "memory_note": "[占卜] 完成一次占卜",
    }


# ---------- 记录 ----------
def save_record(div_type, question, raw, interp):
    """保存一条占卜记录, 返回 id。"""
    forecast = interp.get("forecast") or {}
    review_at = (datetime.now() + timedelta(days=7)).isoformat(timespec="seconds")
    tags = f"#占卜 #{DIV_LABELS.get(div_type, div_type)} #待应验"
    c = _conn()
    cur = c.execute(
        """INSERT INTO divination_records
           (type, question, raw_json, interpretation, bubble, summary, forecast,
            status, created_at, review_at, tags)
           VALUES(?,?,?,?,?,?,?, 'pending', ?, ?, ?)""",
        (
            div_type, question, json.dumps(raw, ensure_ascii=False),
            interp.get("interpretation_md", ""), interp.get("bubble", ""),
            interp.get("summary", ""), json.dumps(forecast, ensure_ascii=False),
            datetime.now().isoformat(timespec="seconds"), review_at, tags,
        ),
    )
    c.commit()
    return cur.lastrowid


def _write_memory(interp):
    """占卜结果写一条短记忆(#占卜 标签)。"""
    try:
        from memory import add_memory
        note = interp.get("memory_note") or interp.get("summary") or ""
        if note:
            add_memory(note, is_important=False, mem_type="占卜")
    except Exception as e:
        print(f"[Divination] 写记忆失败: {e}")


def run_divination(div_type, question=""):
    """完整跑一次: 排盘 -> 解读 -> 保存 -> 写记忆。返回 dict。"""
    try:
        raw = _cast_raw(div_type, question)
    except ValueError as ve:
        return {"error": str(ve)}   # 八字没设置/格式错误等, 明确提示给用户
    if raw is None:
        return {"error": "排盘失败（可能是依赖没装或网络问题）"}
    interp = _interpret(raw, div_type, question)
    rec_id = save_record(div_type, question, raw, interp)
    _write_memory(interp)
    return {
        "id": rec_id,
        "type": div_type,
        "question": question,
        "bubble": interp.get("bubble", ""),
        "brief": interp.get("brief") or interp.get("summary") or "",
        "interpretation_md": interp.get("interpretation_md", ""),
        "summary": interp.get("summary", ""),
        "forecast": interp.get("forecast") or {},
        "memory_note": interp.get("memory_note", ""),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "status": "pending",
    }


# ---------- 查询/反馈 ----------
def get_record(rec_id):
    c = _conn()
    row = c.execute("SELECT * FROM divination_records WHERE id=?", (rec_id,)).fetchone()
    if not row:
        return None
    cols = [d[0] for d in c.execute("SELECT * FROM divination_records LIMIT 0").description]
    rec = dict(zip(cols, row))
    for f in ("forecast", "raw_json"):
        try:
            rec[f] = json.loads(rec[f])
        except Exception:
            pass
    return rec


def review_pending(limit=1):
    """待应验且到期的记录(最多 limit 条)。"""
    c = _conn()
    today = datetime.now().strftime("%Y-%m-%d")
    rows = c.execute(
        """SELECT id, type, question, summary, forecast, review_at FROM divination_records
           WHERE status='pending' AND substr(review_at,1,10)<=? ORDER BY review_at ASC LIMIT ?""",
        (today, limit),
    ).fetchall()
    out = []
    for r in rows:
        try:
            fc = json.loads(r[4]) if r[4] else {}
        except Exception:
            fc = {}
        out.append({"id": r[0], "type": r[1], "question": r[2], "summary": r[3], "forecast": fc, "review_at": r[5]})
    return out


FEEDBACK_STATUS = {"应验了": "fulfilled", "部分应验": "partial", "没应验": "void", "忘了": "void"}


def mark_feedback(rec_id, feedback):
    """手动标记应验结果, 写回状态并记一条记忆。"""
    status = FEEDBACK_STATUS.get(feedback, "void")
    c = _conn()
    c.execute("UPDATE divination_records SET status=?, feedback=? WHERE id=?",
              (status, feedback, rec_id))
    c.commit()
    try:
        from memory import add_memory
        add_memory(f"[占卜反馈] 记录 #{rec_id}: {feedback}", is_important=False)
    except Exception:
        pass
    return status


def mark_important(rec_id, important=True):
    """手动标记为重要记忆。"""
    c = _conn()
    c.execute("UPDATE divination_records SET important=? WHERE id=?", (1 if important else 0, rec_id))
    c.commit()


def recent_records(limit=10):
    """最近记录列表。"""
    c = _conn()
    rows = c.execute(
        """SELECT id, type, question, bubble, summary, status, created_at, important FROM divination_records
           ORDER BY id DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [{"id": r[0], "type": r[1], "question": r[2], "bubble": r[3], "summary": r[4],
             "status": r[5], "created_at": r[6], "important": r[7]} for r in rows]


# ---------- 命令解析 ----------
def parse_command(text):
    """解析 /卦 命令。返回 (div_type, question) 或 None。
    支持: /卦 塔罗 问题 / 卦 问题(默认塔罗) / 塔罗 问题 / 六爻 问题 / 小六壬 问题 / 八字 问题
    """
    t = text.strip()
    if not t.startswith("/"):
        return None
    body = t[1:].strip()
    parts = body.split(maxsplit=1)
    head = parts[0] if parts else ""
    rest = parts[1] if len(parts) > 1 else ""
    mapping = {"卦": None, "塔罗": "tarot", "六爻": "iching", "小六壬": "xiaoliuren", "八字": "bazi"}
    if head not in mapping:
        return None
    div_type = mapping[head]
    if div_type is None:
        # /卦 后面可能跟流派名, 再剥一次
        if rest:
            parts2 = rest.split(maxsplit=1)
            head2 = parts2[0]
            if head2 in mapping and mapping[head2] is not None:
                div_type = mapping[head2]
                rest = parts2[1] if len(parts2) > 1 else ""
        if div_type is None:
            div_type = "tarot"
    return div_type, rest.strip()
