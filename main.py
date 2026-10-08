# -*- coding: utf-8 -*-
"""BoringPet 入口:python main.py"""
import os
import sys

# 可选:本机若装了 torch,先导入它,避免 torch 的 c10.dll 与 PySide6 同时加载时报 WinError 1114。
# 注意 torch/transformers 已不再被本项目使用(向量检索走云端 API, 见 src/memory.py),
# 所以这里缺失时直接跳过 —— 打包分发时不必带上几个 GB 的 torch。
try:
    import torch  # noqa: F401
except Exception:
    pass

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))


def _setup_frozen_logging():
    """打包后没有控制台,把 stdout/stderr 落到 exe 同目录的 boringpet.log。

    不做这件事的话,打包版一旦启动报错,用户那边完全看不到任何线索。
    """
    if not getattr(sys, "frozen", False):
        return  # 开发环境保留控制台输出
    try:
        from paths import app_dir
        path = app_dir() / "boringpet.log"
        if path.exists() and path.stat().st_size > 2 * 1024 * 1024:
            path.replace(app_dir() / "boringpet.log.1")
        stream = open(path, "a", encoding="utf-8", buffering=1)
        sys.stdout = stream
        sys.stderr = stream
    except Exception:
        pass


_setup_frozen_logging()

from PySide6.QtCore import QSharedMemory
from PySide6.QtWidgets import QApplication

from pet import PetWindow


def _finalize_memory():
    """退出兜底: 当前会话若有未总结的对话, 后台补一次总结(不阻塞退出)。"""
    try:
        import json
        from paths import app_dir
        import memory as mem  # 延迟 import, 避免退出时拖慢(与运行时同一个模块实例)
        state_path = app_dir() / "chat_state.json"
        if not state_path.exists():
            return
        with open(state_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        msgs = [m for m in (data.get("messages") or []) if m.get("role") != "system"]
        if msgs:
            mem.append_raw_messages(msgs)  # 逐字原文先归档(本地写文件, 不阻塞)
        if len(msgs) >= 2:
            mem.extract_memory_patch_async(msgs)
    except Exception as e:
        print(f"[Memory] 退出兜底总结失败: {e}")


def main():
    # 单例:防止重复启动出两只宠物
    shared = QSharedMemory("BoringPet_SingleInstance_v2")
    if not shared.create(1):
        print("BoringPet 已经在运行了")
        return

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # Windows 任务栏图标:设置 AppUserModelID
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("BoringPet.Pet")

    # 设置全局图标(任务栏):打包后用 _MEIPASS 里的资源目录,开发时用项目根目录
    from PySide6.QtGui import QIcon
    from paths import resource_dir
    app.setWindowIcon(QIcon(str(resource_dir() / "图标" / "程序.ico")))

    # 启动时做一次旧记忆衰减清理(防膨胀)
    try:
        import memory as mem
        mem._cleanup_old_memories(days=30)
    except Exception as e:
        print(f"[Memory] 启动清理失败: {e}")

    w = PetWindow()
    w.show()

    app.aboutToQuit.connect(_finalize_memory)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
