# -*- coding: utf-8 -*-
"""全局配置:读取 config.json,提供素材路径、状态表、帧率等。"""
import json
import os

from paths import app_dir, resource_dir

BASE = str(resource_dir())  # 素材根目录(打包后在临时目录)
ASSETS = os.path.join(BASE, "assets")


def _config_path():
    """config.json 的位置:优先用 exe/项目根目录下那份(用户可改),再退回打包进去的那份。

    打包后 resource_dir() 指向 _MEIPASS 临时目录,改那里的 config.json 没有意义,
    所以在 exe 同目录放一份 config.json 即可覆盖内置默认值。
    """
    user = app_dir() / "config.json"
    if user.is_file():
        return user
    return resource_dir() / "config.json"

DEFAULT_FPS = 15
DEFAULT_TARGET_H = 220
WALK_SPEED = 3.5

# 状态 -> 素材文件夹(多个时随机挑一个)
STATES = {
    "idle":  ["idle_0", "idle_1", "idle_2", "idle_3"],
    "walk":  ["walk_0", "walk_1"],
    "happy": ["happy_0", "happy_1"],
    "hurt":  ["hurt_0", "hurt_1"],
    "drag":  ["drag"],
    "fall":  ["fall"],
    "yawn":  ["yawn"],
    "sleep": ["sleep"],
    "home":  ["home"],
}


def _load_config():
    try:
        with open(_config_path(), "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


_CONFIG = _load_config()
FPS = int(_CONFIG.get("fps", DEFAULT_FPS))
SCALE = _CONFIG.get("scale")          # 0<SCALE<=1: 宠物高度=屏幕高度×SCALE; >1: 基准高度×SCALE
ACTIONS = _CONFIG.get("actions") or {}


def action_fps(state):
    """每个动作的播放帧率(config.actions.<state>.fps),缺省用全局 FPS。"""
    a = ACTIONS.get(state)
    return int(a.get("fps", FPS)) if isinstance(a, dict) else FPS


def loop_from(state):
    """动画从第几帧开始循环(0-based);None 表示整段循环。

    用于 sleep:前 55 帧是"躺下"过程,只播一遍,第 56 帧起才是"躺着",
    循环只在这段里进行,避免睡觉中途又站起来。
    """
    a = ACTIONS.get(state)
    if isinstance(a, dict) and a.get("loop_from"):
        return int(a["loop_from"])
    return None
