# -*- coding: utf-8 -*-
"""播放 state 对应素材文件夹里的 sound.m4a(尽力而为,失败自动静音)。"""
import os
import random
import subprocess
import sys
import threading
import time

import config

SOUND = False
_lock = threading.Lock()
_proc = None
_last_play = 0.0        # 上次真正起进程播放的时间戳(节流用)
_MIN_GAP = 1.5          # 两次播放最小间隔(秒),避免频繁切换动作时狂起进程


def play(state):
    if not SOUND:
        return
    global _last_play
    now = time.monotonic()
    if now - _last_play < _MIN_GAP:
        return
    variants = config.STATES.get(state) or []
    if not variants:
        return
    path = os.path.join(config.ASSETS, random.choice(variants), "sound.m4a")
    if not os.path.isfile(path):
        return
    _last_play = now   # 先占位节流,避免线程还没起来时连调绕过

    def run():
        global _proc
        try:
            with _lock:
                # 停掉上一个没播完的
                if _proc is not None and _proc.poll() is None:
                    try:
                        _proc.kill()
                    except Exception:
                        pass
                if os.name == "nt":      # Windows: 用系统自带 WMP COM 播 m4a
                    cmd = ["powershell", "-NoProfile", "-Command",
                           "$w=New-Object -ComObject WMPlayer.OCX; "
                           "$w.URL='%s'; "
                           "while($w.playState -eq 3){Start-Sleep -Milliseconds 100}"
                           % path.replace("'", "''")]
                elif sys.platform == "darwin":
                    cmd = ["afplay", path]
                else:                    # Linux: 依次尝试常见播放器
                    cmd = ["ffplay", "-nodisp", "-autoexit", path]
                try:
                    kwargs = dict(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    if os.name == "nt":
                        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
                    _proc = subprocess.Popen(cmd, **kwargs)
                    return
                except Exception:
                    if sys.platform.startswith("linux"):
                        for c in (["paplay", path], ["mpv", "--no-video", path]):
                            try:
                                _proc = subprocess.Popen(
                                    c, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
                                return
                            except Exception:
                                continue
        except Exception:
            pass

    threading.Thread(target=run, daemon=True).start()


def stop():
    """退出时停掉可能还在播放的音效进程。"""
    global _proc
    with _lock:
        if _proc is not None:
            try:
                _proc.kill()
            except Exception:
                pass
            _proc = None
