# -*- coding: utf-8 -*-
"""Token 用量统计模块。"""
import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path

from paths import app_dir

# 用量统计文件的读改写全局锁: 桌宠多个 AI worker 线程会并发 record_usage,
# 不锁会互踩覆盖丢记录。RLock 便于嵌套。
_USAGE_LOCK = threading.RLock()

# 模型价格（元/百万 token）
# 结构: 每个字段为 (空闲价, 高峰价)。高峰=每日北京时间 9:00-12:00、14:00-18:00
# 数据来源: DeepSeek 官方平台价格表(2026-09 实时)
MODEL_PRICES = {
    # DeepSeek V4 Flash —— deepseek-chat / deepseek-flash / deepseek-v4-flash / deepseek-reasoner 均为其兼容名
    # (deepseek-chat/reasoner 已弃用, 分别对应 flash 的非思考/思考模式, 同价)
    # 缓存命中: 空闲0.02/高峰0.04; 未命中: 空闲1/高峰2; 输出: 空闲4/高峰8
    "deepseek-chat": {"input": (1, 2), "cache_hit": (0.02, 0.04), "output": (4, 8)},
    "deepseek-flash": {"input": (1, 2), "cache_hit": (0.02, 0.04), "output": (4, 8)},
    "deepseek-v4-flash": {"input": (1, 2), "cache_hit": (0.02, 0.04), "output": (4, 8)},
    "deepseek-reasoner": {"input": (1, 2), "cache_hit": (0.02, 0.04), "output": (4, 8)},
    # DeepSeek V4 Pro
    # 缓存命中: 空闲0.15/高峰0.30; 未命中: 空闲4.5/高峰9.0; 输出: 空闲13.5/高峰27.0
    "deepseek-v4-pro": {"input": (4.5, 9.0), "cache_hit": (0.15, 0.30), "output": (13.5, 27.0)},
    # 硅基流动视觉(无峰谷)
    "Qwen/Qwen3-VL-30B-A3B-Instruct": {"input": (0.7, 0.7), "output": (2.8, 2.8)},
    "Qwen/Qwen3-VL-8B-Instruct": {"input": (0.5, 0.5), "output": (2.0, 2.0)},
    # 免费模型
    "Qwen/Qwen3-8B": {"input": (0, 0), "cache_hit": (0, 0), "output": (0, 0)},
    "Qwen/Qwen2.5-7B-Instruct": {"input": (0, 0), "cache_hit": (0, 0), "output": (0, 0)},
    "THUDM/GLM-4-9B-0414": {"input": (0, 0), "cache_hit": (0, 0), "output": (0, 0)},
}

# 默认价格（未知模型, 按 deepseek-v4-flash 计）
DEFAULT_PRICE = {"input": (1, 2), "cache_hit": (0.02, 0.04), "output": (4, 8)}


def _bj_now():
    """返回北京时间(Asia/Shanghai)的当前 datetime。价格按北京时段判定, 不受本机时区影响。"""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now().astimezone(ZoneInfo("Asia/Shanghai"))
    except Exception:
        # zoneinfo 不可用/时区库缺失时退化为本地时间
        return datetime.now()


def _is_peak_now():
    """当前是否高峰时段(北京时间 9-12点 和 14-18点)。"""
    h = _bj_now().hour
    return (9 <= h < 12) or (14 <= h < 18)


def _pick_price(val):
    """取当前时段价格: tuple=(空闲,高峰), 数字=固定价。"""
    if isinstance(val, tuple):
        return val[1] if _is_peak_now() else val[0]
    return val


def _usage_file():
    """用量统计文件路径。

    必须用 app_dir()(exe/项目根目录),不能用 __file__ 推算:
    打包后 __file__ 指向 _MEIPASS 临时目录,往里写 usage.json 会在退出时整个丢掉,
    等于每次运行统计都清零。
    """
    return app_dir() / "memory" / "usage.json"


def _empty_usage():
    return {"records": [], "daily": {}}


def _load_usage_raw():
    """读取并结构校验, 返回 (data, ok)。损坏/缺键时隔离备份并返回默认。"""
    f = _usage_file()
    if not f.exists():
        return _empty_usage(), True
    try:
        with open(f, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        if not isinstance(data, dict):
            raise ValueError("usage.json 根节点不是对象")
        # 结构校验: 保证 records/daily 存在且为列表/字典, 避免旧版或缺键时 KeyError
        if not isinstance(data.get("records"), list):
            data["records"] = []
        if not isinstance(data.get("daily"), dict):
            data["daily"] = {}
        return data, True
    except Exception:
        # 损坏文件隔离备份, 避免后续 save 覆盖成只剩一条、历史永久丢失
        try:
            backup = str(f) + f".corrupt-{int(time.time())}"
            os.replace(f, backup)
            print(f"[TokenStats] 检测到损坏的 usage.json, 已备份: {backup}")
        except Exception:
            pass
        return _empty_usage(), False


def load_usage():
    """加载用量统计(线程安全)。"""
    with _USAGE_LOCK:
        data, _ = _load_usage_raw()
        return data


def save_usage(data):
    """保存用量统计(原子写 + 线程安全)。"""
    with _USAGE_LOCK:
        f = _usage_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = str(f) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fp:
            json.dump(data, fp, ensure_ascii=False, indent=2)
        os.replace(tmp, f)


def estimate_tokens(text):
    """估算 token 数（简单估算：中文 1 字≈1.5 token，英文 1 词≈1.3 token）。"""
    if not text:
        return 0
    # 简单估算
    cn_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    en_words = len([w for w in text.split() if any(c.isascii() and c.isalpha() for c in w)])
    other_chars = len(text) - cn_chars - sum(len(w) for w in text.split() if any(c.isascii() and c.isalpha() for c in w))
    return int(cn_chars * 1.5 + en_words * 1.3 + other_chars * 0.5)


def record_usage(model, input_text, output_text,
                 input_tokens=None, output_tokens=None, cache_hit_tokens=None):
    """记录一次对话的用量。

    传入 API 返回的真实 token 时精确计费(含缓存命中拆分);
    不传则用字数估算(兼容旧调用)。
    """
    # 同一把锁内完成 读->改->写, 避免多个 AI worker 线程并发互踩丢记录
    with _USAGE_LOCK:
        data = load_usage()
        if input_tokens is None:
            input_tokens = estimate_tokens(input_text)
        if output_tokens is None:
            output_tokens = estimate_tokens(output_text)
        total_tokens = input_tokens + output_tokens

        # 计算费用: 缓存命中的输入按 cache_hit 价, 未命中按 input 价; 按当前峰谷时段取价
        price = MODEL_PRICES.get(model, DEFAULT_PRICE)
        if cache_hit_tokens is None:
            cache_hit_tokens = 0
        cache_hit_tokens = max(0, min(cache_hit_tokens, input_tokens))
        miss_tokens = input_tokens - cache_hit_tokens
        p_in = _pick_price(price.get("input", (1, 1)))
        p_hit = _pick_price(price.get("cache_hit", price.get("input", (1, 1))))
        p_out = _pick_price(price.get("output", (2, 2)))
        cost = (miss_tokens * p_in + cache_hit_tokens * p_hit + output_tokens * p_out) / 1000000

        now_str = _bj_now().strftime("%Y-%m-%d %H:%M:%S")
        record = {
            "time": now_str,
            "model": model,
            "input_tokens": input_tokens,
            "cache_hit_tokens": cache_hit_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "cost": round(cost, 6),
            "input_preview": input_text[:50],
            "output_preview": output_text[:50],
        }

        data["records"].append(record)
        # 只保留最近 1000 条
        if len(data["records"]) > 1000:
            data["records"] = data["records"][-1000:]

        # 按天统计(北京时间)
        today = _bj_now().strftime("%Y-%m-%d")
        if today not in data["daily"]:
            data["daily"][today] = {"total_tokens": 0, "cost": 0, "count": 0}
        data["daily"][today]["total_tokens"] += total_tokens
        data["daily"][today]["cost"] += cost
        data["daily"][today]["count"] += 1

        save_usage(data)
        return record


def get_today_stats():
    """获取今天的统计(北京时间)。"""
    data = load_usage()
    today = _bj_now().strftime("%Y-%m-%d")
    return data["daily"].get(today, {"total_tokens": 0, "cost": 0, "count": 0})


def get_recent_records(limit=20):
    """获取最近的记录。"""
    data = load_usage()
    return data["records"][-limit:][::-1]


def get_recent_days(days=7):
    """获取最近 N 天的每日统计: [(日期, token数, 费用, 次数), ...] 新→旧。"""
    data = load_usage()
    daily = data.get("daily", {})
    out = []
    from datetime import timedelta
    base = _bj_now().date()
    for i in range(days - 1, -1, -1):
        d = (base - timedelta(days=i)).strftime("%Y-%m-%d")
        item = daily.get(d, {"total_tokens": 0, "cost": 0, "count": 0})
        out.append((d[5:], item.get("total_tokens", 0), item.get("cost", 0.0), item.get("count", 0)))
    return out
