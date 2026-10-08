# -*- coding: utf-8 -*-
"""路径工具:区分打包环境和开发环境。

打包后(PyInstaller):
  - 素材资源在 sys._MEIPASS(临时解压目录)
  - 用户配置在 exe 同目录
开发环境:
  - 都在项目根目录
"""
import sys
from pathlib import Path


def is_frozen():
    """是否是 PyInstaller 打包后的 exe。"""
    return getattr(sys, "frozen", False)


def app_dir():
    """应用程序目录(exe 所在目录)——用户配置文件放这里。"""
    if is_frozen():
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def resource_dir():
    """资源目录(打包进去的素材)——只读。"""
    if is_frozen():
        # _MEIPASS 缺失时要退回 exe 所在目录。注意是 .parent:
        # sys.executable 是文件路径,直接当目录用会拼出 "<exe>\assets" 这种废路径。
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent
