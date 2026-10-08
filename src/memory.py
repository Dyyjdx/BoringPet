# -*- coding: utf-8 -*-
"""Four-layer memory system: psychology + relationship + episodic + diary."""
import json
import time
import threading
import os
import re
from datetime import datetime

from paths import app_dir
from settings_window import load_settings, save_settings

MEMORY_DIR = app_dir() / "memory"
IMPORTANT_FILE = MEMORY_DIR / "important.json"
RAW_HISTORY_FILE = MEMORY_DIR / "raw_history.json"
FACTS_FILE = MEMORY_DIR / "facts.json"
PROMISES_FILE = MEMORY_DIR / "promises.json"
MEMORY_STORE = MEMORY_DIR / "memories.json"
CHAR_STATE_FILE = MEMORY_DIR / "char_state.json"
USER_STATE_FILE = MEMORY_DIR / "user_state.json"
DIARY_DIR = MEMORY_DIR / "diary"

MEMORY_DIR.mkdir(exist_ok=True)
DIARY_DIR.mkdir(exist_ok=True)

# 全局持久化锁: 序列化所有 "读 -> 改 -> 写" 事务, 防止 GUI 线程与
# 后台记忆提取线程并发互踩覆盖(丢失激素/记忆更新)。
_IO_LOCK = threading.RLock()

# 损坏文件备份: 加载 JSON 失败时先复制原文件为 .corrupt 再降级, 避免静默丢数据。
def _quarantine_corrupt(path):
    try:
        backup = str(path) + f".corrupt-{int(time.time())}"
        os.replace(path, backup)
        print(f"[Memory] 检测到损坏文件, 已备份: {backup}")
    except Exception:
        pass


def _default_char_state():
    return {
        # Neurohormones
        "oxytocin": 0.70,
        "dopamine": 0.60,
        "cortisol": 0.10,
        "energy": 0.85,
        # Longing (想念值: 0~1, 随时间增长, 互动后下降 — 联结饥饿)
        "longing": 0.0,
        # Emotion
        "primary_emotion": "平静",
        "emotion_intensity": 3,
        "psychological_tension": "无",
        "emotional_decay_counter": 0,
        # Cognition
        "active_agenda": "观察并回应主人",
        "immediate_focus": "当前的对话",
        "cognitive_dissonance": "无",
        # Timestamp
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def _default_user_state():
    return {
        "trust_level": "中立",
        "trust_score": 0.5,
        "preferences_habits": [],
        "shared_milestones": [],
        "known_attributes": "无",
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


def _load_json(path, default):
    if not path.exists():
        return default.copy()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for k, v in default.items():
            if k not in data:
                data[k] = v
        return data
    except Exception:
        # 文件损坏: 先隔离备份, 再降级为默认, 避免每次启动都重复读到坏文件
        _quarantine_corrupt(path)
        return default.copy()


def _save_json(path, data):
    tmp = str(path) + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception as e:
        print(f"[Memory] 写文件失败 {path.name}: {e}")


def load_char_state():
    return _load_json(CHAR_STATE_FILE, _default_char_state())


def save_char_state(state):
    state["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _save_json(CHAR_STATE_FILE, state)


def load_user_state():
    return _load_json(USER_STATE_FILE, _default_user_state())


def save_user_state(state):
    state["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    _save_json(USER_STATE_FILE, state)


def _atomic_store(load_fn, save_fn, mutator):
    """在持有全局锁的情况下执行 读->改->写 事务, 防止并发覆盖。
    load_fn: 无参, 返回当前 state;  save_fn: 接收改写后的 state;
    mutator: 接收可变的 state 并就地修改。"""
    with _IO_LOCK:
        state = load_fn()
        if state is None:
            state = []
        mutator(state)
        save_fn(state)
        return state


def _atomic_char_state(mutator):
    with _IO_LOCK:
        state = load_char_state()
        mutator(state)
        save_char_state(state)
        return state


# ========== Neurohormone System ==========
def _parse_time(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except:
        try:
            return datetime.strptime(s, "%Y-%m-%d %H:%M")
        except:
            return datetime.now()


def tick_hormones():
    """时间代谢:冷却(指数衰减) + 饥饿(想念线性增长) — 借鉴 OpenHer DriveMetabolism"""
    def _apply(state):
        now = datetime.now()
        last = _parse_time(state.get("last_updated", ""))
        elapsed_hours = max(0, (now - last).total_seconds() / 3600.0)
        if elapsed_hours < 0.001:
            return False
        # ── 冷却: 情绪随时间指数降温(≈8.7h 半衰期) ──
        import math
        decay = math.exp(-0.08 * elapsed_hours)
        state["oxytocin"] = max(0.0, min(1.0, state.get("oxytocin", 0.70) * decay))
        state["dopamine"] = max(0.0, min(1.0, state.get("dopamine", 0.60) * decay))
        state["cortisol"] = max(0.0, min(1.0, state.get("cortisol", 0.10) * decay))
        # ── 饥饿: 想念值随时间线性增长 ──
        state["longing"] = max(0.0, min(1.0, state.get("longing", 0.0) + 0.30 * elapsed_hours))
        # ── 精力随时间缓慢恢复 ──
        state["energy"] = min(1.0, state.get("energy", 0.85) + 0.20 * elapsed_hours)
        state["primary_emotion"] = _calc_emotion(state)
        state["emotion_intensity"] = _calc_intensity(state)
        return True

    with _IO_LOCK:
        state = load_char_state()
        # 距离上次更新不足则不变更(但仍需读最新值判断)
        now = datetime.now()
        last = _parse_time(state.get("last_updated", ""))
        if (now - last).total_seconds() / 3600.0 < 0.001:
            return
        if _apply(state):
            save_char_state(state)


def _calc_emotion(state):
    oxy = state.get("oxytocin", 0.70)
    dop = state.get("dopamine", 0.60)
    cor = state.get("cortisol", 0.10)
    nrg = state.get("energy", 0.85)
    
    is_lonely = oxy <= 0.25
    long = state.get("longing", 0.0)
    
    scores = {
        "melancholy": (1.0 - oxy) * 1.6 if is_lonely else 0.0,
        "concerned": cor * 1.4,
        "curious": dop * 1.1,
        "warm": oxy * 0.9 if not is_lonely else 0.0,
        "excited": (dop + oxy) * 0.7 if (dop > 0.5 and oxy > 0.5) else 0.0,
        "relaxed": (1.0 - dop) * 0.8 if dop < 0.3 else 0.0,
        "playful": (dop * 0.6 + (1.0 - cor) * 0.4) if dop > 0.5 else 0.0,
        "sleepy": (1.0 - nrg) * 1.5 if nrg < 0.2 else 0.0,
        "longing": long * 1.7 if long > 0.5 else 0.0,
        "neutral": 0.25,
    }
    
    return max(scores, key=scores.get)


def _calc_intensity(state):
    oxy = state.get("oxytocin", 0.70)
    dop = state.get("dopamine", 0.60)
    cor = state.get("cortisol", 0.10)
    max_hormone = max(oxy, dop, cor)
    return max(1, min(5, int(max_hormone * 5)))


def on_user_spoke():
    with _IO_LOCK:
        state = load_char_state()
        state["oxytocin"] = min(1.0, state.get("oxytocin", 0.70) + 0.15)
        state["dopamine"] = min(1.0, state.get("dopamine", 0.60) + 0.10)
        state["longing"] = max(0.0, state.get("longing", 0.0) - 0.15)
        state["primary_emotion"] = _calc_emotion(state)
        state["emotion_intensity"] = _calc_intensity(state)
        save_char_state(state)


def on_pet_spoke():
    with _IO_LOCK:
        state = load_char_state()
        state["energy"] = max(0.0, state.get("energy", 0.85) - 0.02)
        state["primary_emotion"] = _calc_emotion(state)
        state["emotion_intensity"] = _calc_intensity(state)
        save_char_state(state)


def on_pet_clicked():
    with _IO_LOCK:
        state = load_char_state()
        state["oxytocin"] = min(1.0, state.get("oxytocin", 0.70) + 0.05)
        state["dopamine"] = min(1.0, state.get("dopamine", 0.60) + 0.05)
        state["primary_emotion"] = _calc_emotion(state)
        state["emotion_intensity"] = _calc_intensity(state)
        save_char_state(state)


def on_new_event():
    with _IO_LOCK:
        state = load_char_state()
        state["dopamine"] = min(1.0, state.get("dopamine", 0.60) + 0.08)
        state["primary_emotion"] = _calc_emotion(state)
        state["emotion_intensity"] = _calc_intensity(state)
        save_char_state(state)


def is_sleeping():
    state = load_char_state()
    return state.get("energy", 0.85) <= 0.05


def is_lonely():
    state = load_char_state()
    return state.get("oxytocin", 0.70) <= 0.25


# ========== Vector Embedding (云端 API) ==========
# 语义检索改用云端向量 API(默认阿里百炼 text-embedding-v4, 兼容 OpenAI /embeddings 接口),
# 彻底绕开本地 torch/transformers 的 DLL 冲突与 1G 内存占用。
# 设置(settings.json vector 段): api_key / model / base_url; 缺 key 时降级为最近几条。
_embedding_cache = {}
_EMBEDDING_CACHE_MAX = 256
_embedding_last_used = 0.0
_reindex_started = False


def _vector_cfg():
    """读向量 API 配置; 未单独配 key 时回退用 chat.api_key。"""
    try:
        s = load_settings()
        v = s.get("vector", {}) or {}
        if not v.get("api_key"):
            v["api_key"] = (s.get("chat", {}) or {}).get("api_key", "")
        return v
    except Exception:
        return {}


def _get_embedding(text):
    """文本 → 向量(云端 API)。失败返回 None, 调用方已有兜底。"""
    global _embedding_last_used
    cache_key = text[:200]
    hit = _embedding_cache.get(cache_key)
    if hit is not None:
        _embedding_last_used = time.time()
        return hit

    cfg = _vector_cfg()
    api_key = cfg.get("api_key") or ""
    if not api_key:
        print("[Memory] 向量 API 未配置 key, 语义检索降级为最近记录")
        return None
    base_url = (cfg.get("base_url") or "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")
    model = cfg.get("model") or "text-embedding-v4"
    url = f"{base_url}/embeddings"

    import urllib.request
    body = json.dumps({
        "model": model,
        "input": text,
        "encoding_format": "float",
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        vec = data["data"][0]["embedding"]
        _embedding_last_used = time.time()
        _embedding_cache[cache_key] = vec
        if len(_embedding_cache) > _EMBEDDING_CACHE_MAX:
            for k in list(_embedding_cache)[:_EMBEDDING_CACHE_MAX // 2]:
                _embedding_cache.pop(k, None)
        _maybe_reindex(cfg, model)
        return vec
    except Exception as e:
        print(f"[Memory] Embedding API failed: {e}")
        return None


def _maybe_reindex(cfg, model):
    """向量模型变更检测: 换过模型(或首次配置) → 后台重建全部旧记忆的向量。
    只触发一次, 重建期间新记忆照常入库(用新模型), 语义检索暂时混合新旧向量。"""
    global _reindex_started
    if _reindex_started:
        return
    try:
        used = (load_settings().get("vector") or {}).get("model_used") or ""
    except Exception:
        return
    if used == model:
        return
    _reindex_started = True

    def _rebuild():
        global _reindex_started
        try:
            # 锁外: 对快照逐条转向量(网络 IO 不占锁, 避免阻塞其他记忆写入)
            snapshot = load_memories()
            n = 0
            text_to_vec = {}
            for m in snapshot:
                if not m.get("text"):
                    continue
                v = _get_embedding(str(m["text"]))
                if v:
                    text_to_vec[str(m["text"])] = v
                    n += 1
            # 锁内: 重读最新列表, 按文本合并向量, 保留重建期间新增的条目
            with _IO_LOCK:
                cur = load_memories()
                for m in cur:
                    if str(m.get("text", "")) in text_to_vec:
                        m["vector"] = text_to_vec[str(m["text"])]
                save_memories(cur)
            try:
                s = load_settings()
                s.setdefault("vector", {})["model_used"] = model
                save_settings(s)
            except Exception:
                pass
            print(f"[Memory] 向量模型切换为 {model}, 已重建 {n} 条记忆向量")
        except Exception as e:
            print(f"[Memory] 向量重建失败: {e}")
        finally:
            _reindex_started = False

    threading.Thread(target=_rebuild, daemon=True).start()


def release_embedding_if_idle(max_idle_seconds=300):
    """API 模式无本地模型, 无需释放内存。保留此函数兼容旧调用点。"""
    return


def _cosine_similarity(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ========== Episodic Memory ==========
def load_memories():
    if not MEMORY_STORE.exists():
        return []
    try:
        with open(MEMORY_STORE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_memories(memories):
    tmp = str(MEMORY_STORE) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(memories, f, ensure_ascii=False, indent=2)
    os.replace(tmp, MEMORY_STORE)


def load_important():
    if not IMPORTANT_FILE.exists():
        return []
    try:
        with open(IMPORTANT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_important(important):
    important = important[-20:]
    tmp = str(IMPORTANT_FILE) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(important, f, ensure_ascii=False, indent=2)
    os.replace(tmp, IMPORTANT_FILE)


def get_next_batch_id():
    memories = load_memories()
    if not memories:
        return 1
    return max(m.get("batch", 1) for m in memories) + 1




def _text_grams(t, n=2):
    """把文本切成 n-gram 集合(去空格), 用于轻量判重, 不依赖向量模型。"""
    t = str(t).replace(" ", "").replace("　", "")
    if len(t) < n:
        return {t} if t else set()
    return {t[i:i + n] for i in range(len(t) - n + 1)}


def _important_dup(new_text, existing):
    """新总结 vs 已有重要记忆: 新文本 60% 以上字符二元组已在某旧条中出现 → 视为重复。"""
    g_new = _text_grams(new_text)
    if not g_new:
        return False
    for e in existing:
        g_old = _text_grams(e.get("text", ""))
        if not g_old:
            continue
        if len(g_new & g_old) / len(g_new) > 0.6:
            return True
    return False

def add_memory(text, batch=None, is_important=False, time_range=None, mem_type=None):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    if batch is None:
        # 在锁外先算 batch id, 避免持锁做向量推理
        with _IO_LOCK:
            memories = load_memories()
            batch = (max(m.get("batch", 1) for m in memories) + 1) if memories else 1
    vector = _get_embedding(text)
    with _IO_LOCK:
        memories = load_memories()
        important = load_important()
        if batch is None:
            batch = get_next_batch_id()
        entry = {
            "id": len(memories) + 1,
            "text": text,
            "vector": vector,
            "time": now,
            "start_time": time_range[0] if time_range else now,
            "end_time": time_range[1] if time_range else now,
            "batch": batch,
            "type": mem_type or "对话",
            "is_important": is_important,
        }
        memories.append(entry)
        if is_important:
            if _important_dup(text, important):
                print(f"[Memory] 重要记忆判重: 与已有条目重复, 丢弃: {text[:40]}...")
            else:
                important.append({"text": text, "time": now})
                save_important(important)
        save_memories(memories)
        return entry


def load_facts():
    """facts 画像层: 关于主人的长期事实/偏好(独立于对话总结)。"""
    if not FACTS_FILE.exists():
        return []
    try:
        with open(FACTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_facts(facts):
    facts = facts[-50:]
    tmp = str(FACTS_FILE) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(facts, f, ensure_ascii=False, indent=2)
    os.replace(tmp, FACTS_FILE)


def add_fact(text):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    with _IO_LOCK:
        facts = load_facts()
        if _important_dup(text, facts):
            return False
        facts.append({"text": text, "time": now})
        save_facts(facts)
        return True


def delete_fact_by_text(text):
    with _IO_LOCK:
        facts = load_facts()
        kept = [f for f in facts if f.get("text", "") != text]
        changed = len(kept) != len(facts)
        if changed:
            save_facts(kept)
        return changed


def update_fact_by_text(old_text, new_text):
    with _IO_LOCK:
        facts = load_facts()
        changed = False
        for f in facts:
            if f.get("text", "") == old_text:
                f["text"] = new_text
                f["time"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                changed = True
        if changed:
            save_facts(facts)
        return changed


def load_promises():
    """承诺层: 主人交代的事/桌宠答应的事(待办性质)。"""
    if not PROMISES_FILE.exists():
        return []
    try:
        with open(PROMISES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_promises(promises):
    promises = promises[-20:]
    tmp = str(PROMISES_FILE) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(promises, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PROMISES_FILE)


def add_promise(text):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    with _IO_LOCK:
        promises = load_promises()
        if _important_dup(text, promises):
            return False
        promises.append({"text": text, "time": now, "done": False})
        save_promises(promises)
        return True


def complete_promise_by_text(text):
    with _IO_LOCK:
        promises = load_promises()
        changed = False
        for pr in promises:
            if pr.get("text", "") == text:
                pr["done"] = True
                pr["done_time"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                changed = True
        if changed:
            save_promises(promises)
        return changed


def _cleanup_old_memories(days=30):
    """衰减清理: 删除 N 天前且非重要的对话总结(防膨胀, 保留重要事件)。"""
    from datetime import timedelta
    try:
        # 整个 读->改->写 放进锁里:否则启动清理会用陈旧快照写回,
        # 把后台提取线程刚 add_memory 进去的条目直接覆盖掉。
        with _IO_LOCK:
            memories = load_memories()
            if not memories:
                return 0
            cutoff = datetime.now() - timedelta(days=days)
            kept = []
            removed = 0
            for m in memories:
                t = m.get("time", "") or m.get("start_time", "")
                try:
                    dt = datetime.strptime(t, "%Y-%m-%d %H:%M")
                except Exception:
                    dt = cutoff  # 无时间戳的旧数据保留
                if dt < cutoff and not m.get("is_important"):
                    removed += 1
                    continue
                kept.append(m)
            if removed:
                save_memories(kept)
        print(f"[Memory] 衰减清理: 移除 {removed} 条旧记忆, 保留 {len(kept)} 条")
        return removed
    except Exception as e:
        print(f"[Memory] 衰减清理失败: {e}")
        return 0


def search_memories(query, top_k=5):
    memories = load_memories()
    if not memories:
        return []
    query_vec = _get_embedding(query)
    if query_vec is None:
        return [m["text"] for m in memories[-top_k:]]
    scored = []
    for m in memories:
        if not m.get("vector"):
            continue
        score = _cosine_similarity(query_vec, m["vector"])
        scored.append((score, m["text"]))
    scored.sort(reverse=True)
    return [text for score, text in scored[:top_k]]


def get_recent_important(limit=3):
    important = load_important()
    return [item["text"] for item in important[-limit:]]


# ========== 记忆工具入口 (function calling 用, 带时间戳原文) ==========
def _parse_when(text):
    """把口语时间词解析成 (start_dt, end_dt); 解析不了返回 None。
    支持: 刚才/刚刚、今天、昨天、前天、上周、X月X日、X点半、(凌晨/早上/上午/中午/下午/晚上)X点、裸X点(1~6视为下午)。"""
    import re as _re
    from datetime import datetime as _dt, timedelta as _td, time as _tmod
    t = str(text or "")
    now = _dt.now()
    if not t:
        return None
    if "前天" in t:
        d = (now - _td(days=2)).date()
        return _dt.combine(d, _dt.min.time()), _dt.combine(d, _dt.max.time())
    if "昨天" in t:
        d = (now - _td(days=1)).date()
        return _dt.combine(d, _dt.min.time()), _dt.combine(d, _dt.max.time())
    if "上周" in t:
        monday = now.date() - _td(days=now.weekday())
        lm = monday - _td(days=7)
        return _dt.combine(lm, _dt.min.time()), _dt.combine(lm + _td(days=6), _dt.max.time())
    if "刚才" in t or "刚刚" in t:
        return now - _td(minutes=30), now
    if "今天" in t:
        d = now.date()
        return _dt.combine(d, _dt.min.time()), now
    m = _re.search(r"(\d{1,2})月(\d{1,2})[日号]", t)
    if m:
        try:
            d = _dt(now.year, int(m.group(1)), int(m.group(2))).date()
            return _dt.combine(d, _dt.min.time()), _dt.combine(d, _dt.max.time())
        except Exception:
            return None
    m = _re.search(r"(凌晨|早上|上午|中午|下午|晚上)?\s*(\d{1,2})\s*[点:](\d{1,2}|半)?", t)
    if m:
        try:
            hour = int(m.group(2))
            minute = 30 if m.group(3) == "半" else int(m.group(3) or 0)
            period = m.group(1)
            if period in ("下午", "晚上"):
                hour = (hour + 12) % 24 if hour < 12 else hour
            elif period == "凌晨" and hour == 12:
                hour = 0
            elif period is None and 1 <= hour <= 6:
                hour = hour + 12  # 裸"5点"视为下午
            d = now.date()
            s = _dt.combine(d, _tmod(hour, minute))
            return s, s + _td(minutes=59)
        except Exception:
            return None
    return None


def load_raw_history():
    """读逐字对话归档(append-only)。"""
    if not RAW_HISTORY_FILE.exists():
        return []
    try:
        with open(RAW_HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_raw_history(hist):
    tmp = str(RAW_HISTORY_FILE) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=2)
    os.replace(tmp, RAW_HISTORY_FILE)


def append_raw_messages(messages):
    """把逐字对话追加进归档(append-only, 保留最近90天)。返回本次净新增条数。"""
    if not messages:
        return 0
    try:
        with _IO_LOCK:
            from datetime import timedelta
            hist = load_raw_history()
            before = len(hist)
            tail_keys = set()
            for h in hist[-80:]:
                tail_keys.add((h.get("role"), str(h.get("time", "")), str(h.get("content", ""))[:60]))
            added = 0
            for m in messages:
                role = m.get("role")
                if role == "system":
                    continue
                content = m.get("content")
                if content is None:
                    content = ""
                tm = m.get("time") or datetime.now().strftime("%Y-%m-%d %H:%M")
                key = (role, str(tm), str(content)[:60])
                if key in tail_keys:
                    continue
                hist.append({"role": role, "content": str(content), "time": tm})
                tail_keys.add(key)
                added += 1
            # 防膨胀: 只保留最近90天
            cutoff = datetime.now() - timedelta(days=90)
            kept = []
            for h in hist:
                try:
                    dt = datetime.strptime(str(h.get("time", "")), "%Y-%m-%d %H:%M")
                except Exception:
                    dt = cutoff
                if dt >= cutoff:
                    kept.append(h)
            save_raw_history(kept)
            if added:
                print(f"[Memory] 逐字对话已归档: +{added} 条, 归档共 {len(kept)} 条")
            return added
    except Exception as e:
        print(f"[Memory] 逐字归档失败: {e}")
        return 0


def load_raw_messages(limit=None):
    """读原始对话记录: 历史归档 + 当前会话合并。带 time 的消息才可用于时间定位。"""
    try:
        msgs = load_raw_history()
        state_path = app_dir() / "chat_state.json"
        if state_path.exists():
            with open(state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            cur = data.get("messages", []) or []
            for m in cur:
                if m.get("role") == "system":
                    continue
                tm = m.get("time") or ""
                key = (m.get("role"), str(tm), str(m.get("content", ""))[:60])
                dup = any(key == (h.get("role"), str(h.get("time", "")), str(h.get("content", ""))[:60]) for h in msgs[-80:])
                if not dup:
                    msgs.append({"role": m.get("role"), "content": m.get("content"), "time": tm})
        if limit:
            msgs = msgs[-limit:]
        return msgs
    except Exception:
        return []


def _when_to_window(when, query=None):
    """把 when 翻译成 (start_dt, end_dt) 时间窗。支持三种形态:
    1. dict {date, start_time, end_time} — 模型翻译好的结构化时间(主路径, 比如'上周三傍晚'→{date,18:00,21:00})
    2. str — 口语时间(兜底正则 _parse_when)
    3. None — 尝试从 query 里解析
    """
    import re as _re
    from datetime import datetime as _dt, timedelta as _td, time as _tmod
    if isinstance(when, dict):
        try:
            d = when.get("date")
            st = when.get("start_time")
            et = when.get("end_time")
            now = _dt.now()
            # 日期: 相对词 / YYYY-MM-DD / X月X日
            if not d:
                return _parse_when(str(when))
            ds = str(d).strip()
            if ds in ("今天", "今日"):
                day = now.date()
            elif ds == "昨天":
                day = (now - _td(days=1)).date()
            elif ds == "前天":
                day = (now - _td(days=2)).date()
            elif _re.match(r"^\d{4}-\d{1,2}-\d{1,2}$", ds):
                day = _dt.strptime(ds, "%Y-%m-%d").date()
            else:
                m = _re.search(r"(\d{1,2})月(\d{1,2})[日号]", ds)
                if m:
                    day = _dt(now.year, int(m.group(1)), int(m.group(2))).date()
                else:
                    return _parse_when(str(when))
            # 时刻: HH:MM / H点 / H点M分
            def _to_time(txt, dh, dm):
                if not txt:
                    return _tmod(dh, dm)
                m2 = _re.match(r"^(\d{1,2})[:点时](\d{1,2})?$", str(txt).strip())
                if not m2:
                    return _tmod(dh, dm)
                h = int(m2.group(1))
                mi = int(m2.group(2) or 0)
                raw = str(txt)
                if 1 <= h <= 6 and "下午" not in raw and "晚上" not in raw:
                    h = (h + 12) % 24
                return _tmod(h % 24, mi)
            start_t = _to_time(st, 0, 0)
            end_t = _to_time(et, 23, 59)
            return _dt.combine(day, start_t), _dt.combine(day, end_t)
        except Exception:
            return _parse_when(str(when))
    return _parse_when(when or query or "")


def _mem_in_window(m, start, end):
    """条目 m 的时间窗(start_time~end_time, 回退单点 time)与查询窗 [start,end] 是否相交。"""
    st = m.get("start_time") or m.get("time")
    et = m.get("end_time") or m.get("time")
    if not st or not et:
        return False
    try:
        sdt = datetime.strptime(str(st), "%Y-%m-%d %H:%M")
        edt = datetime.strptime(str(et), "%Y-%m-%d %H:%M")
    except Exception:
        return False
    return not (edt < start or sdt > end)


def memory_search_tool(query, when=None, top_k=5):
    """工具: 回忆查询。
    when 或 query 里能解析出时间 → 按时间范围过滤(记忆总结/重要事件/画像事实 + 原始对话逐字, 带 source 标记);
    否则按语义相似度检索(带阈值, 低相关不返回)。"""
    memories = load_memories()
    window = _when_to_window(when, query)
    if window:
        start, end = window
        hits = []
        for m in memories:
            if _mem_in_window(m, start, end):
                tm = m.get("start_time") or m.get("time", "")
                hits.append({"time": tm, "text": m.get("text", ""), "source": f"记忆总结({m.get('type', '对话')})"})
        # 画像事实(带 time)也纳入时间过滤
        for m in load_facts():
            if _mem_in_window(m, start, end):
                hits.append({"time": m.get("time", ""), "text": m.get("text", ""), "source": "画像事实"})
        # 重要事件(带 time)也纳入时间过滤
        for m in load_important():
            if _mem_in_window(m, start, end):
                hits.append({"time": m.get("time", ""), "text": m.get("text", ""), "source": "重要事件"})
        # 原始对话逐字(优先展示, 更接近原话)
        for r in load_raw_messages():
            tm = r.get("time", "")
            if not tm:
                continue
            try:
                dt = datetime.strptime(tm, "%Y-%m-%d %H:%M")
            except Exception:
                continue
            if start <= dt <= end:
                c = str(r.get("content", ""))
                if c.startswith("[主动]"):
                    c = c[len("[主动]"):]
                tag = "主人" if r.get("role") == "user" else "桌宠"
                hits.append({"time": tm, "text": f"{tag}说: {c}", "source": "原始对话"})
        hits.sort(key=lambda x: str(x.get("time", "")))
        return json.dumps(hits[-20:], ensure_ascii=False, indent=2)
    if not query:
        return json.dumps(
            [{"time": m.get("time", ""), "text": m.get("text", ""), "source": "记忆总结"} for m in memories[-top_k:]],
            ensure_ascii=False, indent=2,
        )
    # 语义检索: 记忆总结 + 画像事实 一起参与, 带阈值
    pool = []
    for m in memories:
        if m.get("vector"):
            pool.append(m)
    for f in load_facts():
        fv = _get_embedding(f.get("text", ""))
        if fv:
            pool.append({"text": f.get("text", ""), "vector": fv, "time": f.get("time", ""), "type": "偏好"})
    query_vec = _get_embedding(query)
    if query_vec is None:
        hits = memories[-top_k:]
    else:
        scored = []
        for m in pool:
            score = _cosine_similarity(query_vec, m["vector"])
            if score >= 0.35:  # 阈值: 低相关不返回, 避免噪音污染
                scored.append((score, m))
        scored.sort(reverse=True)
        hits = [m for _, m in scored[:top_k]]
    return json.dumps(
        [{"time": m.get("start_time") or m.get("time", ""), "text": m.get("text", ""), "source": f"记忆总结({m.get('type', '对话')})"} for m in hits],
        ensure_ascii=False, indent=2,
    )


def memory_important_tool(limit=5):
    """工具: 返回最近重要事件 JSON。"""
    return json.dumps(load_important()[-limit:], ensure_ascii=False, indent=2)


def memory_diary_tool(date=None):
    """工具: 返回指定日期(或今天)的日记 JSON。"""
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")
    path = DIARY_DIR / f"{date}.md"
    if path.exists():
        return json.dumps(
            {"date": date, "content": path.read_text(encoding="utf-8")[:2000]},
            ensure_ascii=False, indent=2,
        )
    return json.dumps({"date": date, "content": "这天没有日记"}, ensure_ascii=False)

def memory_recent_tool(kind="全部", limit=1, before=None, after=None):
    """工具: 按类型精确查最近记录(不走相似度, 按时间倒序)。
    kind=占卜: 查 memories 里 type=='占卜' 或文本以 [占卜] 开头的记录(兼容旧数据);
    kind=承诺: 查 promises.json 最近;
    kind=重要: 查 important.json 最近;
    kind=全部: 查 memories 最近 limit 条。
    before/after: 截止/起始日期(YYYY-MM-DD), 只查该日期之前/之后的记录。
    返回 JSON 数组: [{time, text, source}]。"""
    try:
        limit = max(1, min(int(limit), 10))
    except Exception:
        limit = 1
    try:
        if kind == "占卜":
            mems = load_memories()
            hits = [m for m in mems
                    if m.get("type") == "占卜" or str(m.get("text", "")).startswith("[占卜]")]
            if before:
                hits = [m for m in hits if str(m.get("time", ""))[:10] <= str(before)[:10]]
            if after:
                hits = [m for m in hits if str(m.get("time", ""))[:10] >= str(after)[:10]]
            hits.sort(key=lambda m: str(m.get("time", "")), reverse=True)
            return json.dumps(
                [{"time": m.get("time", ""), "text": m.get("text", ""), "source": "占卜记录"} for m in hits[:limit]],
                ensure_ascii=False, indent=2,
            )
        if kind == "承诺":
            return json.dumps(load_promises()[-limit:], ensure_ascii=False, indent=2)
        if kind == "重要":
            return json.dumps(load_important()[-limit:], ensure_ascii=False, indent=2)
        # 默认/全部: memories 最近 limit 条
        mems = load_memories()
        hits = mems[-limit:]
        return json.dumps(
            [{"time": m.get("time", ""), "text": m.get("text", ""), "source": f"记忆({m.get('type', '对话')})"} for m in hits],
            ensure_ascii=False, indent=2,
        )
    except Exception as e:
        return json.dumps({"error": f"memory_recent 失败: {e}"}, ensure_ascii=False)


# ========== Diary Layer ==========
def save_diary_entry(content):
    today = datetime.now().strftime("%Y-%m-%d")
    path = DIARY_DIR / f"{today}.md"
    now = datetime.now().strftime("%H:%M")
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"\n## {now}\n{content}\n")
    return path


def load_recent_diaries(days=3):
    entries = []
    diary_files = sorted(DIARY_DIR.glob("*.md"), reverse=True)[:days]
    for f in reversed(diary_files):
        try:
            content = f.read_text(encoding="utf-8")
            if len(content) > 500:
                content = content[:500] + "..."
            entries.append(f"### {f.stem}\n{content}")
        except Exception:
            pass
    return entries


# ========== Behavior Signals (借鉴 OpenHer: 信号注入代替性格描述) ==========
def _signals_from_state(state):
    """把激素状态映射成 8 维行为信号(0~1), 模型自己解读情绪, 不硬写性格。"""
    oxy = state.get("oxytocin", 0.70)
    dop = state.get("dopamine", 0.60)
    cor = state.get("cortisol", 0.10)
    nrg = state.get("energy", 0.85)
    long = state.get("longing", 0.0)
    def clamp(v):
        return max(0.0, min(1.0, v))
    return {
        "温暖度": clamp(oxy * 0.8 + long * 0.2),
        "玩闹度": clamp(dop),
        "紧张度": clamp(cor),
        "活力值": clamp(nrg),
        "想念值": clamp(long),
        "主动度": clamp(long * 0.7 + (1.0 - cor) * 0.3),
        "坦露度": clamp(oxy * 0.6 + long * 0.4),
        "倔强度": clamp(cor * 0.7 + (1.0 - oxy) * 0.3),
    }


SIGNAL_ANCHORS = {
    "温暖度": ("疏离", "热切"),
    "玩闹度": ("正经", "调皮"),
    "紧张度": ("放松", "紧绷"),
    "活力值": ("疲惫", "元气"),
    "想念值": ("自在", "想你"),
    "主动度": ("被动", "主动"),
    "坦露度": ("封闭", "袒露"),
    "倔强度": ("随和", "嘴硬"),
}


def build_signal_injection(state):
    """把行为信号格式化为舞台指令, 注入提示词。
    含温度扰动: 每次说话信号有微小随机抖动(±0.06), 同一状态说得不完全一样。"""
    import random
    sigs = _signals_from_state(state)
    for k in list(sigs.keys()):
        sigs[k] = max(0.0, min(1.0, sigs[k] + random.uniform(-0.06, 0.06)))
    lines = ["【舞台指令：角色当前状态】"]
    for name in ("温暖度", "玩闹度", "紧张度", "活力值"):
        v = sigs[name]
        lo, hi = SIGNAL_ANCHORS[name]
        lines.append(f"· {name}: {v:.2f} (0{lo}→1{hi})")
    lines.append("【舞台指令：角色内在需求】")
    for name in ("想念值", "主动度", "坦露度", "倔强度"):
        v = sigs[name]
        lo, hi = SIGNAL_ANCHORS[name]
        lines.append(f"· {name}: {v:.2f} (0{lo}→1{hi})")
    emotion = state.get("primary_emotion", "平静")
    lines.append(f"（此刻情绪：{emotion}，强度 {state.get('emotion_intensity', 3)}/5）")
    lines.append(_time_period_hint())
    return "\n".join(lines)


def _time_period_hint():
    """按时间段返回语气提示(时间感知, 0 token)。"""
    h = datetime.now().hour
    if 5 <= h < 9:
        return "（现在是清晨，你刚醒还有点迷糊，说话软软的带点起床气）"
    if 9 <= h < 12:
        return "（现在是上午，你元气满满，说话轻快有活力）"
    if 12 <= h < 14:
        return "（现在是中午，你有点犯困，说话懒洋洋的）"
    if 14 <= h < 18:
        return "（现在是下午，你精神不错，说话活泼随意）"
    if 18 <= h < 22:
        return "（现在是晚上，你心情放松开心，说话带点撒娇）"
    if 22 <= h < 24:
        return "（现在是深夜，你困了但还想陪主人，说话软糯慵懒）"
    return "（现在是凌晨，夜深人静，你轻声说话，特别想窝在主人身边）"


# ========== Build Memory Prompt ==========
def build_memory_prompt(user_text):
    """构建注入对话的记忆提示词。任何一步失败都降级返回空串, 绝不阻塞聊天。"""
    try:
        return _build_memory_prompt_impl(user_text)
    except Exception as e:
        print(f"[Memory] 记忆检索降级(不阻塞对话): {e}")
        return ""


def _build_memory_prompt_impl(user_text):
    parts = []
    
    # 1. Psychology layer (舞台指令信号注入)
    char_state = load_char_state()
    parts.append(build_signal_injection(char_state))
    # 1.5 Critic 最近一次情境感知
    last_ctx = char_state.get("last_context")
    if last_ctx:
        parts.append("【上次感知到主人的状态】")
        parts.append(
            f"情绪 {last_ctx.get('user_emotion', 0):.2f}(负面→正面) | "
            f"投入 {last_ctx.get('user_engagement', 0):.2f} | "
            f"亲近 {last_ctx.get('topic_intimacy', 0):.2f} | "
            f"冲突 {last_ctx.get('conflict_level', 0):.2f} | "
            f"新鲜 {last_ctx.get('novelty_level', 0):.2f}"
        )
        parts.append("")
    
    # 2. Relationship layer
    user_state = load_user_state()
    parts.append("【关于主人的了解】")
    trust_score = user_state.get("trust_score", 0.5)
    parts.append(f"- 信任度: {user_state['trust_level']} ({trust_score:.2f}/1.0)")
    if user_state.get("preferences_habits"):
        parts.append("- 用户偏好/习惯:")
        for pref in user_state["preferences_habits"][-5:]:
            parts.append(f"  · {pref}")
    if user_state.get("shared_milestones"):
        parts.append("- 一起经历的重要时刻:")
        for milestone in user_state["shared_milestones"][-3:]:
            parts.append(f"  · {milestone}")
    parts.append("")
    
    # 2.5 待办承诺(主人交代的事)
    promises = load_promises()
    pending = [pr for pr in promises if not pr.get("done")]
    if pending:
        parts.append("【主人交代的事(待办)】")
        for pr in pending[-3:]:
            parts.append(f"- {pr['text']}")
        parts.append("")
    
    # 3. 记忆规则: 平时不灌全量记忆(省token), 回忆走工具查询
    parts.append("【记忆规则】")
    parts.append("- 主人问具体回忆(某天发生了什么/说过什么原话/几点)时，先调用记忆工具 memory_search / memory_important / memory_diary 查真实记录再回答。")
    parts.append("- 调用 memory_search 时，把主人话里的时间(昨天/上周三/下午5点/傍晚)自己换算成具体日期和时间段填进 when 参数，不要原样填口语。")
    parts.append("- 查不到或不确定就直接说\"记不太清了\"，禁止编造具体时间、原话或对话细节。")
    parts.append("- 历史中以 [主动] 开头的消息是桌宠自己主动说的话（游戏评论/主动关心等），不是和主人的对话，不要把它当成主人的行为。")
    parts.append("")
    
    if len(parts) <= 4:
        return ""
    
    return "\n".join(parts) + "\n（自然地记住这些，不要刻意提起记忆。）"


# ========== Extract Memory Patch ==========
def extract_memory_patch(conversation_history):
    if not conversation_history or len(conversation_history) < 2:
        return
    
    # 记忆/情绪提取单独走 DeepSeek(快,便宜),回退到对话配置
    cfg = load_settings().get("chat", {})
    base_url = cfg.get("base_url") or "https://api.deepseek.com/v1"
    # 记忆/情绪提取复用对话配置里的 api_key;也可用独立的 memory_api_key 覆盖
    api_key = cfg.get("memory_api_key") or cfg.get("api_key") or ""
    model = cfg.get("memory_model") or cfg.get("model") or "deepseek-chat"
    if not api_key:
        return
    
    dialog = []
    time_stamps = []
    for msg in conversation_history[-6:]:
        content = msg.get("content", "")
        if msg.get("role") == "user":
            role = "User(主人)"
        elif str(content).startswith("[主动]"):
            role = "You(桌宠主动说的, 不是对话)"
        else:
            role = "You(桌宠)"
        dialog.append(f"{role}: {content}")
        tm = msg.get("time")
        if tm:
            time_stamps.append(str(tm))
    dialog_text = "\n".join(dialog)
    # 时间窗: 该批对话的真实发生范围(最早~最晚), 时间回忆按此匹配
    if time_stamps:
        time_stamps.sort()
        batch_time_range = (time_stamps[0], time_stamps[-1])
    else:
        batch_time_range = None

    current_facts = load_facts()[-6:]
    facts_preview = "；".join(f.get("text", "") for f in current_facts) or "(暂无)"

    prompt = f"""你是桌宠的心理感知层（Critic）。分析下面的对话，感知情绪与关系变化，输出记忆更新。

当前宠物状态：
{json.dumps(load_char_state(), ensure_ascii=False)}

当前用户档案：
{json.dumps(load_user_state(), ensure_ascii=False)}

已知关于主人的长期事实(已有认知, 判断新增/更新时参考, 不要重复提取):
{facts_preview}

对话内容：
{dialog_text}

说明：标注"You(桌宠主动说的, 不是对话)"的消息是桌宠自己主动说的话(游戏评论/主动关心等)，不是和主人的对话。
总结时不要把桌宠主动说的话当成主人的行为或对话内容，也不要在 summary/milestones 里记成"主人说了…"。

【时间锚定规则】对话中出现的相对时间(刚才/今天/下午X点/昨天/上周/前几天等)必须转成具体日期写进 summary 和 facts_add，例如"主人昨天(9月25日)去爬山"、"主人下午5点(17:00)说要打游戏"。这样以后按时间才能查到。

只输出 JSON，不要输出其他任何文字：
{{
  "context": {{
    "user_emotion": 0.0,
    "user_engagement": 0.0,
    "topic_intimacy": 0.0,
    "conflict_level": 0.0,
    "novelty_level": 0.0
  }},
  "hormone_delta": {{
    "oxytocin": 0.0,
    "dopamine": 0.0,
    "cortisol": 0.0,
    "energy": 0.0
  }},
  "relationship_delta": 0.0,
  "trust_delta": 0.0,
  "preferences_add": ["新发现的用户偏好/习惯"],
  "milestones_add": ["新的重要时刻"],
  "facts_add": ["关于主人的新长期事实/偏好/计划, 最多2条, 与已知认知冲突或重复就填空数组"],
  "promises_add": ["主人交代给桌宠的事或桌宠答应主人的事, 最多1条, 没有就填空数组"],
  "memory_updates": [{{"action": "UPDATE", "target": "旧事实原文", "new_text": "更新后的事实"}}],
  "memory_type": "对话 或 事件 或 偏好 或 占卜",
  "summary": "这段对话的50字中文总结(相对时间必须锚定具体日期)",
  "is_important": true 或 false
}}

字段说明：
- context 各维度取值 0~1：user_emotion 负面→正面；user_engagement 冷淡→投入；topic_intimacy 话题亲近程度；conflict_level 冲突程度；novelty_level 新鲜程度。
- hormone_delta 取值 -0.35~0.35，表示对话让宠物激素的增减。
- relationship_delta 取值 -1~1，这段对话让关系更亲近还是更疏远。
- trust_delta 取值 -1~1，信任度的增减。
- memory_updates：仅当新对话与"已知事实/重要记忆"矛盾或过时时填(例如主人从爱吃辣改成不吃辣了)，action 只允许 UPDATE 或 DELETE，target 填旧事实的原文；没有冲突就填空数组。UPDATE 的 new_text 是新版本事实。

所有文字一律使用中文。没有变化就填空值或 0。"""
    # 数值统一为浮点数
    prompt += "\n数值必须是 JSON number，不要用字符串。"
    
    try:
        import urllib.request
        body = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 450,
            "stream": False,
            "temperature": 0.3,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        last_err = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
                content = result["choices"][0]["message"]["content"].strip()
                break
            except Exception as e:
                last_err = e
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
        else:
            raise last_err
        
        content = re.sub(r"^```json\s*|\s*```$", "", content).strip()
        patch = json.loads(content)
        
        _apply_patch(patch, batch_time_range=batch_time_range)
        print(f"[Memory] Patch applied: {patch.get('summary', '')[:30]}...")
        
    except Exception as e:
        print(f"[Memory] Extract patch failed: {e}")



def extract_memory_patch_async(conversation_history):
    """后台异步提取记忆+情绪,不阻塞对话"""
    def _run():
        try:
            extract_memory_patch(conversation_history)
        except Exception as e:
            print(f"[Memory] 异步提取失败: {e}")
    t = threading.Thread(target=_run, daemon=True)
    t.start()


def _apply_patch(patch, batch_time_range=None):
    now = datetime.now().strftime("%Y-%m-%d")
    
    # 1+2. Update hormones / user profile —— 在同一把锁的事务里读改写落盘
    with _IO_LOCK:
        char_state = load_char_state()
        delta = patch.get("hormone_delta", {})
        for k in ("oxytocin", "dopamine", "cortisol", "energy"):
            if k in delta:
                clamped = max(-0.35, min(0.35, float(delta[k])))
                char_state[k] = max(0.0, min(1.0, char_state.get(k, 0.5) + clamped))

        # Update emotion based on new hormones
        char_state["primary_emotion"] = _calc_emotion(char_state)
        char_state["emotion_intensity"] = _calc_intensity(char_state)

        # 1.5 Update context (对话情境感知) —— 必须在 save 之前写回, 否则改内存没落盘
        context = patch.get("context") or {}
        if context:
            char_state["last_context"] = {
                "user_emotion": float(context.get("user_emotion", 0.0)),
                "user_engagement": float(context.get("user_engagement", 0.0)),
                "topic_intimacy": float(context.get("topic_intimacy", 0.0)),
                "conflict_level": float(context.get("conflict_level", 0.0)),
                "novelty_level": float(context.get("novelty_level", 0.0)),
                "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            }

        save_char_state(char_state)

        # 2. Update user profile
        user_state = load_user_state()

        # 2.1 关系 EMA: relationship_delta 平滑更新信任值(α=0.3)
        rel_delta = float(patch.get("relationship_delta", 0.0) or 0.0)
        trust_delta = float(patch.get("trust_delta", 0.0) or 0.0)
        if rel_delta != 0.0 or trust_delta != 0.0:
            trust_score = float(user_state.get("trust_score", 0.5))
            # 关系变化一部分直接落信任, 一部分留作情感偏置
            new_score = trust_score + 0.3 * (rel_delta * 0.4 + trust_delta * 0.6)
            new_score = max(0.0, min(1.0, new_score))
            user_state["trust_score"] = round(new_score, 3)
            user_state["trust_level"] = _trust_to_level(new_score)

        if patch.get("preferences_add"):
            for pref in patch["preferences_add"]:
                if pref and pref not in user_state["preferences_habits"]:
                    user_state["preferences_habits"].append(pref)
            user_state["preferences_habits"] = user_state["preferences_habits"][-25:]
        if patch.get("milestones_add"):
            for milestone in patch["milestones_add"]:
                if milestone:
                    user_state["shared_milestones"].append(milestone)
            user_state["shared_milestones"] = user_state["shared_milestones"][-10:]
        save_user_state(user_state)

    # 2.2 facts 画像层(长期事实/偏好, 判重后入 facts.json)
    if patch.get("facts_add"):
        for fact in patch["facts_add"]:
            if fact and add_fact(fact):
                print(f"[Memory] 新增画像事实: {fact[:40]}...")

    # 2.3 承诺层(主人交代的事)
    if patch.get("promises_add"):
        for promise in patch["promises_add"]:
            if promise and add_promise(promise):
                print(f"[Memory] 新增承诺: {promise[:40]}...")

    # 2.4 记忆自维护(低频): 与已知事实/重要记忆冲突 → UPDATE/DELETE
    for upd in patch.get("memory_updates") or []:
        action = str(upd.get("action", "")).upper()
        target = str(upd.get("target", ""))
        new_text = str(upd.get("new_text", ""))
        if not target:
            continue
        try:
            if action == "DELETE":
                deleted = delete_fact_by_text(target)
                imp = load_important()
                kept = [i for i in imp if i.get("text", "") != target]
                if len(kept) != len(imp):
                    save_important(kept)
                    deleted = True
                if deleted:
                    print(f"[Memory] 自维护删除: {target[:40]}...")
            elif action == "UPDATE" and new_text:
                updated = update_fact_by_text(target, new_text)
                imp = load_important()
                for i in imp:
                    if i.get("text", "") == target:
                        i["text"] = new_text
                        i["time"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                        updated = True
                if updated:
                    save_important(imp)
                    print(f"[Memory] 自维护更新: {target[:30]}... -> {new_text[:30]}...")
        except Exception as e:
            print(f"[Memory] 自维护执行失败: {e}")

    # 3. Save summary to episodic memory (带时间窗 + type)
    summary = patch.get("summary", "")
    if summary:
        batch = get_next_batch_id()
        is_important = patch.get("is_important", False) or _is_important_event(summary)
        mem_type = patch.get("memory_type") or "对话"
        if mem_type not in ("对话", "事件", "偏好", "占卜"):
            mem_type = "对话"
        add_memory(summary, batch=batch, is_important=is_important,
                   time_range=batch_time_range, mem_type=mem_type)

    # 4. Write to diary
    if summary:
        save_diary_entry(summary)


def _trust_to_level(score):
    """信任分 0~1 → 中文等级"""
    if score >= 0.9:
        return "亲密无间"
    if score >= 0.75:
        return "很信任"
    if score >= 0.6:
        return "信任"
    if score >= 0.4:
        return "中立"
    if score >= 0.2:
        return "有点疏远"
    return "陌生"


def _is_important_event(summary):
    """重要事件判定: summary 是中文, 必须用中文关键词(原来的英文关键词永远匹配不上)。"""
    keywords = [
        "考试", "毕业", "分手", "恋爱", "喜欢", "告白", "生日", "工作", "offer",
        "搬家", "生病", "住院", "受伤", "赢了", "输了", "连跪", "第一次", "重要",
        "大事", "关键", "面试", "入职", "离职", "获奖", "比赛", "吵架", "和好",
        "exam", "graduation", "breakup", "love", "birthday",
        "job", "move", "sick", "win", "lose", "first",
    ]
    return any(kw.lower() in summary.lower() for kw in keywords)


# ========== Memory Prompt Cache (优化: 聊天不卡) ==========
class _MemoryPromptCache:
    """prompt 级缓存: 连续消息高度相似时直接复用上次检索结果(0 等待)。
    模型未加载时 build_memory_prompt 内部已降级(仍给情绪信号+重要记忆+最近对话),
    所以 get() 永远快速返回, 不会阻塞发送。"""

    def __init__(self):
        self._last_text = ""
        self._last_prompt = ""
        self._lock = threading.Lock()

    @staticmethod
    def _similar(a, b):
        """判断两段输入是否'同一话题追问'(子串/前缀重叠), 保守命中。"""
        if not a or not b or a == b:
            return a == b
        short, long_ = (a, b) if len(a) <= len(b) else (b, a)
        if short in long_:
            return len(short) >= 6   # 追问/接话场景
        return a[:40] == b[:40] and len(a) >= 20

    def get(self, text):
        with self._lock:
            if self._last_prompt and self._similar(self._last_text, text):
                return self._last_prompt
        prompt = build_memory_prompt(text)
        with self._lock:
            self._last_text = text
            self._last_prompt = prompt
        return prompt

    def warmup(self):
        """云端 API 无需预热, 保留方法兼容调用方。"""
        return


memory_prompt_cache = _MemoryPromptCache()


# ========== 情绪状态对外接口 (优化: 驱动行为/显示) ==========
_EMOJI_MAP = {
    "melancholy": ("😢", "有点低落"),
    "concerned": ("😟", "在担心"),
    "curious": ("🤔", "好奇"),
    "warm": ("😊", "暖暖的"),
    "excited": ("🥳", "超开心"),
    "relaxed": ("😌", "很放松"),
    "playful": ("😝", "想调皮"),
    "sleepy": ("🥱", "困了"),
    "longing": ("🥺", "想你"),
    "neutral": ("😐", "平静"),
}


def get_emotion_status():
    """返回 (emoji, 短词, 完整描述, state)。供状态气泡显示与主动评论语气注入。"""
    state = load_char_state()
    emo = state.get("primary_emotion", "neutral")
    intensity = state.get("emotion_intensity", 3)
    emoji, word = _EMOJI_MAP.get(emo, ("😐", "平静"))
    long = state.get("longing", 0.0)
    extra = ""
    if long > 0.5:
        extra = "，很想主人"
    elif long > 0.25:
        extra = "，有点想你"
    brief = f"{emoji} {word}（强度{intensity}/5）{extra}"
    return emoji, word, brief, state
