# -*- coding: utf-8 -*-
"""帧缓存:加载 png 序列,统一缩放到目标高度,支持水平翻转。"""
import glob
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap

import config


def _num_key(p):
    """帧文件名排序:纯数字按数值排,其他按字符串排(避免 int() 崩溃)。"""
    b = os.path.splitext(os.path.basename(p))[0]
    try:
        return (0, int(b))
    except ValueError:
        return (1, b)


def _scale_to_h(img, target_h):
    if target_h <= 0 or img.height() == target_h:
        return img
    return img.scaledToHeight(target_h, Qt.SmoothTransformation)


class FrameCache:
    """按 (变体, 朝向, 目标高度) 缓存 QPixmap 帧序列。"""

    def __init__(self):
        self._cache = {}

    def clear(self):
        self._cache.clear()

    def get(self, variant, direction, target_h):
        key = (variant, direction, target_h)
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        folder = os.path.join(config.ASSETS, variant)
        paths = glob.glob(os.path.join(folder, "*.png"))
        paths.sort(key=_num_key)
        imgs, wmax, hmax = [], 0, 0
        for p in paths:
            img = QImage(p)
            if img.isNull():
                continue
            img = _scale_to_h(img, target_h)
            if direction < 0:
                img = img.mirrored(True, False)
            pm = QPixmap.fromImage(img)
            imgs.append(pm)
            wmax = max(wmax, pm.width())
            hmax = max(hmax, pm.height())
        out = (imgs, (wmax, hmax))
        self._cache[key] = out
        return out
