# -*- coding: utf-8 -*-
"""桌宠主窗口(PySide6 版)。

交互:
  * 左键按住拖动 -> 播放 drag 动画;松手 -> 原地待着(已取消下落)
  * 左键单击   -> 开心
  * 右键       -> 弹出菜单(宠物大小/透明度滑动条、心率监测、隐藏、AI对话、退出)
"""
import ctypes
import os
import random
import subprocess
import sys
import threading
import time
import winreg

from PySide6.QtCore import QPropertyAnimation, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QLinearGradient, QPainter, QPainterPath
from PySide6.QtWidgets import (QApplication, QGraphicsDropShadowEffect, QHBoxLayout,
                               QLabel, QMenu, QSlider, QSystemTrayIcon, QVBoxLayout,
                               QWidget, QWidgetAction)

import active_chat
import config
import sounds
from divination import run_divination, review_pending
from divination_ui import DivinationWindow
from frames import FrameCache
from heart import MENU_QSS, HeartManager, HeartWindow, _ico
from settings_window import SettingsWindow, load_settings, build_pet_identity
from usage_window import UsageWindow
from paths import resource_dir, app_dir
from screen_recorder import get_recorder, prewarm as prewarm_recorder
from music_player import MusicPlayer
from lyric_window import LyricWindow

GRAV = 1500.0     # 登场下落重力加速度(px/s^2)
MAX_VY = 2600.0   # 下落最大速度(px/s)


class BubbleWidget(QWidget):
    """自绘气泡:渐变粉白背景 + 圆角 + 底部小尾巴 + 柔和阴影。"""

    TAIL_H = 10       # 尾巴高度
    TAIL_W = 16       # 尾巴宽度
    PAD_X = 14        # 左右内边距
    PAD_TOP = 10      # 顶部内边距
    PAD_BOTTOM = 8    # 底部内边距(不含尾巴)
    MAX_W = 240       # 气泡最大宽度

    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._font_size = 14  # 气泡字体大小,可滚轮调节
        self._label = QLabel(self)
        self._label.setWordWrap(True)
        self._label.setAlignment(Qt.AlignCenter)
        self._update_label_style()
        # 阴影效果在分层窗口上会导致 UpdateLayeredWindowIndirect 失败,去掉

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit()

    def _update_label_style(self):
        self._label.setStyleSheet(
            f"color:#5a4a5a;font-size:{self._font_size}px;font-weight:500;background:transparent;"
        )

    def wheelEvent(self, e):
        """鼠标悬浮气泡上滚轮:调字体大小(10~28px)。"""
        delta = e.angleDelta().y()
        if delta > 0:
            self._font_size = min(28, self._font_size + 1)
        else:
            self._font_size = max(10, self._font_size - 1)
        self._update_label_style()
        # 重新调整大小
        text = self._label.text()
        self.setText(text)
        e.accept()

    def setText(self, text):
        self._label.setText(text)
        # 用 fontMetrics 精确计算文字尺寸
        fm = self._label.fontMetrics()
        max_text_w = self.MAX_W - self.PAD_X * 2
        # 计算多行文字的实际高度和宽度
        rect = fm.boundingRect(0, 0, max_text_w, 0, Qt.TextWordWrap, text)
        text_w = min(rect.width(), max_text_w)
        text_h = rect.height()
        w = text_w + self.PAD_X * 2
        h = text_h + self.PAD_TOP + self.PAD_BOTTOM + self.TAIL_H + 4  # 多留4px防裁剪
        self.resize(w, h)
        self._label.setGeometry(self.PAD_X, self.PAD_TOP, text_w, text_h)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        # 渐变背景:白 -> 淡粉
        grad = QLinearGradient(0, 0, 0, self.height() - self.TAIL_H)
        grad.setColorAt(0.0, QColor("#ffffff"))
        grad.setColorAt(1.0, QColor("#fff0f6"))
        painter.setBrush(grad)
        painter.setPen(Qt.NoPen)
        # 圆角矩形主体
        body = QRectF(0, 0, self.width(), self.height() - self.TAIL_H)
        painter.drawRoundedRect(body, 16, 16)
        # 底部小尾巴(三角形,居中)
        cx = self.width() / 2
        path = QPainterPath()
        path.moveTo(cx - self.TAIL_W / 2, self.height() - self.TAIL_H)
        path.lineTo(cx, self.height())
        path.lineTo(cx + self.TAIL_W / 2, self.height() - self.TAIL_H)
        path.closeSubpath()
        painter.fillPath(path, QColor("#fff0f6"))


class RecordButton(QWidget):
    """悬浮录制按钮:桌宠右上角,默认半透明,悬停显示,录制中变红闪烁。"""

    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(36, 36)
        self._recording = False
        self._hover = False
        self._breath = 0.0
        # 呼吸动画(录制中闪烁)
        self._breath_timer = QTimer(self)
        self._breath_timer.timeout.connect(self._on_breath)
        self._breath_timer.start(50)

    def set_recording(self, rec):
        self._recording = rec
        self.update()

    def _on_breath(self):
        if self._recording:
            self._breath = (self._breath + 0.08) % (2 * 3.14159)
            self.update()

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(2, 2, -2, -2)

        # 透明度:悬停或录制中不透明,否则半透明
        opacity = 1.0 if (self._hover or self._recording) else 0.45
        p.setOpacity(opacity)

        # 背景:录制中红色呼吸,否则粉蓝渐变
        if self._recording:
            breath_val = (1.0 + 0.3 * __import__("math").sin(self._breath)) / 1.3
            color = QColor(255, int(80 * breath_val + 40), int(100 * breath_val + 40))
        else:
            grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
            grad.setColorAt(0, QColor("#a8c8ff"))
            grad.setColorAt(1, QColor("#ffb8d9"))
            color = None

        p.setPen(Qt.NoPen)
        if color:
            p.setBrush(color)
        else:
            p.setBrush(grad)
        p.drawEllipse(rect)

        # 图标:录制中是停止方块,否则是摄像机圆点
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#ffffff"))
        if self._recording:
            s = 10
            p.drawRoundedRect(rect.center().x() - s/2, rect.center().y() - s/2, s, s, 2, 2)
        else:
            p.drawEllipse(rect.center(), 6, 6)


class PetWindow(QWidget):
    div_done = Signal(object)   # 算命完成(跨线程 emit, 自动在主线程槽执行)

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowIcon(_ico("程序.ico"))

        scr = QApplication.primaryScreen().availableGeometry()
        self.sw, self.sh = scr.width(), scr.height()

        self.frames = FrameCache()
        # 从 settings.json 读取用户配置
        self._user_settings = load_settings()
        self.target_h = self._user_settings["pet"]["size"]
        self.setWindowOpacity(self._user_settings["pet"]["opacity"])
        # 游戏评论的随机间隔:必须在这里就按配置初始化,否则要等用户在设置窗口点一次保存
        # 才会生效(启动后一直用兜底的 2~10 分钟)。
        _iv = self._user_settings.get("intervals", {}) or {}
        self._game_comment_min = _iv.get("game_comment_min_minutes", 2)
        self._game_comment_max = _iv.get("game_comment_max_minutes", 10)

        self.label = QLabel(self)

        # 状态
        self.state = "home"
        self.variant = None
        self.direction = 1
        self.frame = 0
        self.anim_fps = config.FPS
        self._acc = 0.0
        self._last_tick = 0.0
        self._imgs = []
        self.w = self.h = 64
        self.x = self.sw * 0.75
        self.y = -self.h            # 登场:从屏幕顶端上方开始
        self.vx = 0.0
        self.vy = 0.0
        self.behave_until = 0.0
        self._landing = True
        self._land_y = int(self.sh * 0.5 + self.target_h / 2)  # 落到屏幕中间

        # 显示缓存(place 只在实际变化时更新,减少透明窗口反复重绘)
        self._last_img = None
        self._last_size = (0, 0)
        self._last_pos = None

        # 鼠标
        self._drag_offset = None
        self._dragging = False

        # 心率
        self.heart = HeartManager()
        self.heart_win = None
        self._chat_win = None
        # 悬浮录制按钮
        self._rec_btn = RecordButton()
        self._rec_btn.clicked.connect(self._toggle_record)
        self._rec_btn.hide()
        self.heart.hr.connect(self._on_heart_rate)
        self._last_hr_say = 0.0   # 上次心率说话时间(节流)

        # 心率气泡(独立顶层窗口,避免被宠物窗口裁剪;头顶说话)
        self._bubble_widget = BubbleWidget()
        self._bubble_widget.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self._bubble_widget.setAttribute(Qt.WA_TranslucentBackground)
        self._bubble = self._bubble_widget._label  # 兼容引用
        self._bubble_widget.hide()
        self._bubble_widget.clicked.connect(self._on_bubble_click)
        self._bubble_timer = QTimer(self)
        self._bubble_timer.setSingleShot(True)
        self._bubble_timer.timeout.connect(self._hide_bubble_animated)
        self._bubble_fade_anim = None

        # 系统托盘(隐藏桌宠后用托盘图标重新显示)
        self._tray = QSystemTrayIcon(_ico("程序.ico"), self)
        self._tray.setToolTip("BoringPet · 点击显示桌宠")
        self._tray.activated.connect(self._on_tray_activated)

        self.set_state("fall", sound=False)   # 登场播放下落动画
        self.behave_until = time.monotonic() + 30.0
        self.place()
        # 录制按钮默认隐藏,录屏时才显示在屏幕右上角

        # ── 主动功能:游戏检测 + 凌晨关心 ──
        self._current_game = None          # 当前检测到的游戏名
        self._last_game_say = 0.0          # 游戏说话节流
        self._last_midnight = None         # 上次凌晨提醒的日期字符串
        self._last_game_watch = 0.0        # 上次游戏观察(截屏分析)时间
        self._last_hr_watch = 0.0          # 上次心率触发观察时间
        self._recording_enabled = True      # 心率自动录屏开关
        self._last_rec_err = ""             # 上一次录屏错误(避免气泡刷屏)
        # 后台先把录屏要用的音频设备清单查好,免得第一次点录屏时卡一秒
        prewarm_recorder()
        self._game_process_name = None      # 当前游戏进程名(用于检测退出)
        self._game_start_time = None        # 游戏开始时间(计算时长)
        self._rec_count_at_game_start = 0   # 游戏开始时已有的视频数(统计本次新录)
        # 音乐歌词信号
        mp = MusicPlayer.get()
        mp.lyric_changed.connect(self._on_lyric_changed)
        mp.state_changed.connect(self._on_music_state_changed)

        # 歌词窗口
        self._lyric_win = LyricWindow()
        self._lyric_win.hide()
        self._last_hr_value = 0             # 最近一次心率值(供 tick 检查录屏)
        self._active_timer = QTimer(self)
        self._active_timer.timeout.connect(self._check_active_events)
        self._active_timer.start(5000)     # 每 5 秒检查一次

        # 游戏观察:每 5 分钟自动截屏分析(仅游戏在前台时)
        self._game_watch_timer = QTimer(self)
        self._game_watch_timer.timeout.connect(self._game_watch_check)
        self._game_watch_timer.start(300000)  # 5 分钟

        # 闲置主动:驱力驱动, 每5分钟检查一次想念值/保底时间, 由条件决定是否触发
        self._idle_comment_timer = QTimer(self)
        self._idle_comment_timer.timeout.connect(self._idle_comment_check)
        self._idle_comment_timer.start(300000)  # 5 分钟

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(max(30, 1000 // config.FPS))

        # ── 算命 ──
        self._div_busy = False            # 算命进行中防重复触发
        self._last_div_record_id = None   # 最近一次算命记录 id(气泡点击打开详情)
        self._last_div_mood_ts = 0.0      # 最近一次算命情绪反馈时间(30分钟冷却)
        self._div_review_hinted = set()   # 本次会话已提示过的待应验记录 id
        self._pending_div_results = []    # 算命完成但聊天窗未开时, 暂存待补进对话
        self.div_done.connect(self._on_div_done)  # 跨线程完成回调(比 QTimer.singleShot 可靠)
        if load_settings().get("divination", {}).get("remind", True):
            QTimer.singleShot(4000, self._check_div_review)

    # ---------- 尺寸 ----------
    def _resolve_target_h(self):
        target_h = config.DEFAULT_TARGET_H
        s = config.SCALE
        if isinstance(s, (int, float)) and s > 0:
            target_h = int(self.sh * s) if s <= 1.0 else int(config.DEFAULT_TARGET_H * s)
            target_h = max(80, min(target_h, 400))
        return target_h

    def _set_pet_size(self, h):
        h = max(80, min(int(h), 400))
        if h == self.target_h:
            return
        self.target_h = h
        self.frames.clear()
        self.set_state(self.state, variant=self.variant, sound=False)

    # ---------- 状态切换 ----------
    def set_state(self, state, variant=None, sound=True):
        variants = config.STATES.get(state)
        if not variants:
            return
        self.variant = variant or random.choice(variants)
        d = self.direction if state == "walk" else 1
        imgs, size = self.frames.get(self.variant, d, self.target_h)
        self.state = state
        self.anim_fps = config.action_fps(state)
        if imgs:
            self._imgs = imgs
            self.frame = 0
            self.w, self.h = size
        self._last_img = None          # 强制下一帧重绘
        self._last_size = (0, 0)
        if sound:
            sounds.play(state)

    def set_direction(self, d):
        if d == self.direction:
            return
        self.direction = d
        if self.state == "walk":
            # 只换帧序列,保留当前 frame 进度,转身不重播动画
            imgs, size = self.frames.get(self.variant, d, self.target_h)
            if imgs:
                self._imgs = imgs
                self.w, self.h = size
            self._last_img = None      # 镜像帧序列不同,强制重绘

    # ---------- 行为 ----------
    def choose_behavior(self):
        """按概率挑下一个动作;时长都按"播几轮动画"来定,减少频繁切换。"""
        now = time.monotonic()
        r = random.random()
        if r < 0.70:                       # 70% 待机:多播几轮再动
            self.set_state("idle")
            self.behave_until = now + random.uniform(20, 35)
        elif r < 0.80:                     # 10% 打哈欠:连打两轮
            self.set_state("yawn")
            self.behave_until = now + 8
        elif r < 0.90:                     # 10% 睡觉(时间长,一直躺着)
            self.set_state("sleep")
            self.behave_until = now + random.uniform(20, 40)
        else:                              # 10% 开心:多开心几轮
            self.set_state("happy")
            self.behave_until = now + 8

    # ---------- 鼠标 ----------
    def _in_music_mode(self):
        """是否在播放音乐状态(播放中或暂停中)。"""
        mp = MusicPlayer.get()
        return mp.is_playing or mp._paused

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            # 不管是不是音乐模式,都先记录拖动偏移
            self._drag_offset = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._dragging = False
            self._music_click = False
            self._landing = False   # 用户接管,取消登场下落
            e.accept()

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton and self._drag_offset is not None:
            pos = e.globalPosition().toPoint()
            if not self._dragging:
                if (pos - (self.frameGeometry().topLeft() + self._drag_offset)).manhattanLength() > 4:
                    self._dragging = True
                    self.set_state("drag", sound=False)
            if self._dragging:
                px = pos.x() - self._drag_offset.x()
                py = pos.y() - self._drag_offset.y()
                px = max(0, min(px, self.sw - self.w))
                py = max(0, min(py, self.sh - self.h))
                self.move(px, py)
                self.x = px + self.w / 2
                self.y = py + self.h
            e.accept()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_offset = None
            if self._dragging:
                # 拖动结束:松手后原地待着
                self._dragging = False
                self.set_state("idle")
            else:
                # 单击(不拖动)
                if self._in_music_mode():
                    # 音乐模式:单击=暂停/继续
                    mp = MusicPlayer.get()
                    name = mp.play_pause()
                    if name:
                        if mp.is_playing:
                            self._show_bubble(f"继续播放：{name}~", duration=2500)
                        else:
                            self._show_bubble("暂停啦~", duration=2000)
                else:
                    # 非音乐模式:单击=显示状态
                    self.set_state("happy")
                    self.behave_until = time.monotonic() + 3.0
                    try:
                        import sys
                        sys.path.insert(0, "src")
                        from memory import load_char_state, tick_hormones, get_emotion_status
                        tick_hormones()
                        state = load_char_state()
                        emoji, word, _, _ = get_emotion_status()
                        oxy = state["oxytocin"]
                        dop = state["dopamine"]
                        cor = state["cortisol"]
                        nrg = state["energy"]
                        msg = f"{emoji} {word} | 亲密:{oxy:.0%} | 开心:{dop:.0%} | 压力:{cor:.0%} | 精力:{nrg:.0%}"
                        self._show_bubble(msg, duration=3500)
                    except Exception as e:
                        print(f"[状态] {e}")
            e.accept()

    def mouseDoubleClickEvent(self, e):
        """双击桌宠:播放音乐时=下一首,否则=截屏分析。"""
        if e.button() == Qt.LeftButton:
            if self._in_music_mode():
                # 播放音乐时:双击=下一首
                mp = MusicPlayer.get()
                name = mp.next_song()
                if name:
                    self._show_bubble(f"下一首：{name}~", duration=3000)
                e.accept()
                return
            if getattr(self, "_double_tap_busy", False):
                e.accept()
                return
            self._double_tap_busy = True
            self.set_state("happy")
            self.behave_until = time.monotonic() + 3.0
            self._double_tap_comment()
            e.accept()

    def wheelEvent(self, e):
        """鼠标滚轮:播放音乐时调音量。"""
        mp = MusicPlayer.get()
        if mp.is_playing or mp._paused:
            delta = e.angleDelta().y()
            if delta > 0:
                vol = mp.volume_up(0.05)
            else:
                vol = mp.volume_down(0.05)
            self._show_bubble(f"音量 {int(vol*100)}%", duration=1200)
            e.accept()
        else:
            e.ignore()

    def _double_tap_comment(self):
        """双击截屏评论:直接截屏→视觉分析→开场白/夸奖(不隐藏桌宠)。"""
        import base64
        from PySide6.QtCore import QBuffer

        try:
            screen = QApplication.primaryScreen()
            pixmap = screen.grabWindow(0)
            buf = QBuffer()
            buf.open(QBuffer.ReadWrite)
            pixmap.save(buf, "PNG")
            img_b64 = base64.b64encode(buf.data().data()).decode()
        except Exception:
            img_b64 = None

        _, owner, _, _ = build_pet_identity()
        fallback = f"{owner}找我玩啦~"
        if img_b64:
            active_chat.screenshot_comment_say(
                self, img_b64, fallback_text=fallback,
            )
        else:
            self._show_bubble(fallback)
            self._double_tap_busy = False  # 截屏失败,直接重置

    def contextMenuEvent(self, e):
        self._open_menu(e.globalPos())

    # ---------- 右键菜单 ----------
    def _open_menu(self, pos=None):
        menu = self._build_menu()
        menu.exec(pos if pos is not None else QCursor.pos())

    def _build_menu(self):
        menu = QMenu()
        menu.setStyleSheet(MENU_QSS)
        ms = QGraphicsDropShadowEffect()
        ms.setBlurRadius(20)
        ms.setOffset(0, 6)
        ms.setColor(QColor("#00000018"))
        menu.setGraphicsEffect(ms)

        # 宠物设置:点击打开设置窗口
        settings_action = menu.addAction("宠物设置")
        settings_action.triggered.connect(self._open_settings)

        menu.addSeparator()

        # 心率监测
        hr = menu.addMenu("心率监测")
        hr.setStyleSheet(MENU_QSS)
        hr.addAction("打开监测窗口", self._open_heart_win)
        hf = hr.addAction("爱心浮窗")
        hf.setCheckable(True)
        hf.setChecked(self.heart.float_visible())
        hf.toggled.connect(lambda on: self.heart.show_float() if on else self.heart.hide_float())
        hz = hr.addMenu("爱心大小")
        hz.setStyleSheet(MENU_QSS)
        for txt, f in (("小", 0.5), ("中", 0.75), ("大", 1.0), ("特大", 1.5)):
            a = hz.addAction(txt)
            a.setData(f)
        hz.triggered.connect(lambda a: self.heart.set_float_scale(a.data()))
        ho = hr.addMenu("爱心透明度")
        ho.setStyleSheet(MENU_QSS)
        for txt, v in (("30%", 0.3), ("50%", 0.5), ("70%", 0.7), ("100%", 1.0)):
            a = ho.addAction(txt)
            a.setData(v)
        ho.triggered.connect(lambda a: self.heart.set_float_opacity(a.data()))

        hr.addSeparator()
        rec_act = hr.addAction("心率过高自动录屏")
        rec_act.setCheckable(True)
        rec_act.setChecked(self._recording_enabled)
        rec_act.toggled.connect(self._toggle_recording)

        menu.addSeparator()
        # 手动录屏(文字动态切换)
        self._manual_rec_action = menu.addAction("开始录屏")
        self._manual_rec_action.triggered.connect(self._toggle_record)
        menu.aboutToShow.connect(self._update_menu_rec_text)

        # 播放音乐
        menu.addAction("播放音乐", self._music_play_pause)
        menu.addAction("停止音乐", self._music_stop)

        menu.addAction('AI 对话', self._open_chat)
        menu.addAction('查看用量', self._open_usage)

        # 算命
        div = menu.addMenu('🔮 算命')
        div.setStyleSheet(MENU_QSS)
        div.addAction('塔罗', lambda: self._start_divination('tarot'))
        div.addAction('六爻', lambda: self._start_divination('iching'))
        div.addAction('小六壬', lambda: self._start_divination('xiaoliuren'))
        div.addAction('八字', lambda: self._start_divination('bazi'))
        div.addSeparator()
        div.addAction('最近记录', self._open_div_recent)

        # 游戏优化(接入 MeoBoost 精简版)
        gb = menu.addMenu('游戏优化')
        gb.setStyleSheet(MENU_QSS)
        gb.addAction('一键开启优化', self._game_boost_on)
        gb.addAction('一键关闭优化', self._game_boost_off)
        gb.addSeparator()
        self._gb_state_action = gb.addAction('优化状态: 查询中…')
        self._gb_state_action.setEnabled(False)
        menu.aboutToShow.connect(self._update_gb_state)
        menu.addSeparator()

        # 开机自启动
        auto = menu.addAction("开机自启动")
        auto.setCheckable(True)
        auto.setChecked(self._is_autostart())
        auto.toggled.connect(self._set_autostart)

        menu.addSeparator()
        menu.addAction("隐藏桌宠", self._hide_pet)
        menu.addSeparator()
        menu.addAction("退出", self.close)
        return menu

    def _open_settings(self):
        """打开设置窗口。"""
        if not hasattr(self, "_settings_win") or self._settings_win is None:
            self._settings_win = SettingsWindow(self)
            self._settings_win.settings_saved.connect(self._apply_settings)
        self._settings_win.show()
        self._settings_win.raise_()
        self._settings_win.activateWindow()

    def _apply_settings(self, settings):
        """保存设置后应用到桌宠。"""
        self._user_settings = settings
        # 大小
        new_h = settings["pet"]["size"]
        if new_h != self.target_h:
            self._set_pet_size(new_h)
        # 透明度
        self.setWindowOpacity(settings["pet"]["opacity"])
        # 游戏评论随机间隔参数(实际触发由 _game_watch_check 按随机重设 timer)
        intervals = settings.get("intervals", {})
        self._game_comment_min = intervals.get("game_comment_min_minutes", 2)
        self._game_comment_max = intervals.get("game_comment_max_minutes", 10)
        # 平时主动: 驱力驱动, idle timer 固定5分钟检查(触发由想念阈值/保底时间决定)
        self._idle_comment_timer.start(300000)

    # ---------- 游戏优化(MeoBoost 精简版) ----------
    def _game_boost_on(self):
        self._show_bubble('正在开启游戏优化…', duration=3000)
        QTimer.singleShot(200, self._run_game_boost_apply)

    def _game_boost_off(self):
        self._show_bubble('正在还原游戏优化…', duration=3000)
        QTimer.singleShot(200, self._run_game_boost_restore)

    def _run_game_boost_apply(self):
        self._run_game_boost("apply", "优化启动失败")

    def _run_game_boost_restore(self):
        self._run_game_boost("restore", "还原失败")

    def _run_game_boost(self, action, err_label):
        """执行游戏优化。

        开发环境:subprocess 跑 src/game_boost.py(脚本里的注册表操作可能需要管理员)。
        打包后:没有 .py 源码,而且 sys.executable 是桌宠自己的 exe(没法执行 .py),
        所以改为后台线程直接调用函数。注意打包版需要以管理员身份启动才能改注册表。
        """
        script = os.path.join(str(resource_dir()), "src", "game_boost.py")
        try:
            if not getattr(sys, "frozen", False) and os.path.isfile(script):
                subprocess.Popen(
                    [sys.executable, script, action],
                    creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
                )
                return
            import game_boost
            func = game_boost.apply_all if action == "apply" else game_boost.restore_all
            threading.Thread(target=func, kwargs={"quiet": True}, daemon=True).start()
        except Exception as ex:
            self._show_bubble(f'{err_label}: {ex}', duration=3000)

    def _update_gb_state(self):
        try:
            from game_boost import is_any_on
            on = is_any_on()
            self._gb_state_action.setText('优化状态: 已开启' if on else '优化状态: 未开启')
        except Exception:
            self._gb_state_action.setText('优化状态: 未知')

    def _toggle_recording(self, enable):
        """开关心率自动录屏。"""
        self._recording_enabled = enable
        if not enable:
            # 关闭时如果正在录,立刻停止
            rec = get_recorder()
            if rec.is_recording:
                rec.stop()
                self._show_bubble("录屏已停止", duration=3000)
        else:
            # 以前这里什么都不显示:勾上之后完全没有反馈,用户以为点了没用
            intervals = getattr(self, "_user_settings", {}).get("intervals", {}) or {}
            th = intervals.get("recording_threshold", 105)
            self._show_bubble(f"心率超过 {th} 就自动录屏~", duration=3500)

    def _open_heart_win(self):
        if self.heart_win is None:
            self.heart_win = HeartWindow(self.heart)
        self.heart_win.showNormal()
        self.heart_win.raise_()
        self.heart_win.activateWindow()

    def _open_chat(self):
        if self._chat_win is None:
            import chat_window
            self._chat_win = chat_window.ChatWindow()
            self._chat_win.reply_received.connect(self._ai_replied)
            self._chat_win.agent_thinking.connect(self._ai_thinking)
            self._chat_win.agent_working.connect(self._ai_working)
            self._chat_win.agent_error.connect(self._ai_error)
            self._chat_win.bubble_say.connect(lambda text: self._show_bubble(text))
            # 补进此前算命完成但窗口未开时暂存的结果
            for rec in list(getattr(self, "_pending_div_results", []) or []):
                try:
                    self._chat_win.append_div_result(rec)
                except Exception:
                    pass
            self._pending_div_results.clear()
        else:
            self._chat_win.refresh_history()  # 刷新外部追加的主动消息
        self._chat_win.showNormal()
        self._chat_win.raise_()
        self._chat_win.activateWindow()

    def _open_usage(self):
        """打开用量统计窗口。"""
        self._usage_win = UsageWindow(self)
        self._usage_win.show()

    def _on_bubble_click(self):
        """点击气泡:音乐模式打开歌词窗口;算命气泡打开详情;否则打开AI对话。"""
        if self._in_music_mode():
            self._lyric_win.show()
            self._lyric_win.raise_()
            return
        if self._last_div_record_id and time.monotonic() < getattr(self, "_div_bubble_until", 0.0):
            self._open_div_window(self._last_div_record_id)
            return
        self._open_chat()

    # ---------- 算命 ----------
    def _start_divination(self, div_type):
        """右键菜单触发算命: 先弹窗问问题(可留空), 后台线程跑(排盘+解读)。"""
        if self._div_busy:
            self._show_bubble("上一卦还在算，稍等一下~", duration=2500)
            return
        # 八字没设生日就提前提示, 不弹问题框白等
        if div_type == "bazi":
            try:
                from settings_window import load_settings
                if not (load_settings().get("divination", {}).get("birth") or "").strip():
                    self._show_bubble("先到 宠物设置 → 算命 里填公历生日，才能起八字哦~", duration=5000)
                    return
            except Exception:
                pass
        from PySide6.QtWidgets import QInputDialog
        name = {"tarot": "塔罗", "iching": "六爻", "xiaoliuren": "小六壬", "bazi": "八字"}.get(div_type, div_type)
        q, ok = QInputDialog.getText(self, f"{name}起卦", f"想算点什么？（可留空直接起卦）\n例如：我该不该跳槽")
        if not ok:
            return
        question = (q or "").strip()
        self._div_busy = True
        self.set_state("idle", sound=False)
        # 起卦气泡: 5分钟兜底, 结果出来时再接管(不会中途消失让用户干等)
        self._show_bubble("正在起卦…", duration=300000)
        t = threading.Thread(target=self._div_worker, args=(div_type, question), daemon=True)
        t.start()

    def _div_worker(self, div_type, question):
        rec = None
        try:
            rec = run_divination(div_type, question)
        except Exception as e:
            rec = {"error": str(e)}
        finally:
            self.div_done.emit(rec)   # Signal 跨线程 emit, 主线程 _on_div_done 必执行

    def _on_div_done(self, rec):
        self._div_busy = False
        # 先清掉"正在起卦…"气泡, 再显示结果
        try:
            self._bubble_timer.stop()
            if self._bubble_fade_anim:
                self._bubble_fade_anim.stop()
                self._bubble_fade_anim = None
            self._bubble_widget.hide()
        except Exception:
            pass
        if rec.get("error"):
            self.set_state("hurt")
            self._show_bubble(f"算命失败：{rec['error']}", duration=8000)
            return
        self._last_div_record_id = rec.get("id")
        self._div_bubble_until = time.monotonic() + 20
        self._show_bubble(rec.get("bubble", "算出来了~"), duration=15000)
        # 结果同步追加进 AI 对话窗口; 窗口没开过就暂存, 下次打开补进
        try:
            if getattr(self, "_chat_win", None) is not None:
                self._chat_win.append_div_result(rec)
                self._log_div(f"append chat ok id={rec.get('id')}")
            else:
                self._pending_div_results.append(rec)
                self._log_div(f"chat closed, pending id={rec.get('id')}")
        except Exception as e:
            self._log_div(f"append chat FAIL: {e}")
        # 即时情绪反馈(30分钟冷却, 不进长期情绪值)
        try:
            direction = (rec.get("forecast") or {}).get("direction", "")
            now = time.monotonic()
            if direction in ("偏吉",) and now - self._last_div_mood_ts >= 1800:
                self._last_div_mood_ts = now
                self.set_state("happy")
                self.behave_until = now + 3.0
            elif direction in ("偏凶",) and now - self._last_div_mood_ts >= 1800:
                self._last_div_mood_ts = now
                self.set_state("hurt")
                self.behave_until = now + 3.0
        except Exception:
            pass

    def _check_div_review(self):
        """启动时提示一条到期待应验记录(手动反馈, 不自动回访)。"""
        try:
            if not load_settings().get("divination", {}).get("remind", True):
                return
            for it in review_pending(limit=3):
                rid = it["id"]
                if rid in self._div_review_hinted:
                    continue
                self._div_review_hinted.add(rid)
                self._last_div_record_id = rid
                self._div_bubble_until = time.monotonic() + 30
                fc = it.get("forecast") or {}
                claim = fc.get("claim") or it.get("summary") or "上次那卦"
                self._show_bubble(f"上次说「{claim}」，后来咋样了？点我看详情~", duration=15000)
                break
        except Exception as e:
            print(f"[Divination] 待应验提示失败: {e}")

    def _open_div_window(self, rec_id):
        """打开算命结果详情窗(独立顶层窗口, 不继承桌宠 Tool/置顶标志, 一定可见)。"""
        try:
            win = getattr(self, "_div_win", None)
            if win is not None and win.record_id == rec_id:
                win.show()
                win.raise_()
                win.activateWindow()
                return
            w = DivinationWindow(rec_id, parent=None)
            self._div_win = w
            w.show()
            w.raise_()
            w.activateWindow()
            self._log_div(f"open_div_window id={rec_id} ok")
        except Exception as e:
            self._log_div(f"open_div_window id={rec_id} FAIL: {e}")

    def _open_div_recent(self):
        """最近算命记录列表(点开看详情/反馈)。"""
        from PySide6.QtWidgets import QInputDialog
        from divination import recent_records
        recs = recent_records(limit=10)
        if not recs:
            self._show_bubble("还没有算命记录哦~", duration=3000)
            return
        labels = []
        for r in recs:
            label = {"tarot": "塔罗", "iching": "六爻", "xiaoliuren": "小六壬", "bazi": "八字"}.get(r["type"], r["type"])
            q = (r.get("question") or "").strip() or "未填问题"
            st = {"pending": "待应验", "fulfilled": "已应验", "partial": "部分应验", "void": "已结束"}.get(r["status"], r["status"])
            labels.append(f"#{r['id']} {label}·{q} [{st}]")
        sel, ok = QInputDialog.getItem(self, "算命记录", "选择一条查看：", labels, 0, False)
        if ok and sel:
            try:
                rec_id = int(sel.split("#")[1].split(" ")[0])
                self._open_div_window(rec_id)
            except Exception as e:
                self._log_div(f"recent parse FAIL sel={sel!r}: {e}")

    def _log_div(self, msg):
        """算命调试日志(桌宠 pythonw 无控制台, 写文件方便排查)。"""
        try:
            from paths import app_dir
            with open(str(app_dir() / "divination_debug.log"), "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        except Exception:
            pass

    def _ai_thinking(self):
        """Agent 思考中:宠物站着不动想事。"""
        self.set_state("idle", sound=False)
        self.behave_until = time.monotonic() + 30.0

    def _ai_working(self):
        """Agent 执行工具中(干活):宠物站着不动,显得在认真忙。"""
        self.set_state("idle", sound=False)
        self.behave_until = time.monotonic() + 30.0

    def _ai_replied(self):
        """AI 回答到达时,宠物开心一下。"""
        self.set_state("happy")
        self.behave_until = time.monotonic() + 3.0

    def _ai_error(self):
        """Agent 出错时,宠物表现受伤/委屈。"""
        self.set_state("hurt")
        self.behave_until = time.monotonic() + 3.0

    # ---------- 主动功能:游戏检测 + 凌晨关心 ----------
    # 游戏关键词 -> 给 OpenClaw 的场景提示(生成不同话术)
    GAME_PROMPTS = {
        "三角洲": "比如问今天冲哪个图、刚打完哪局、提醒别蹲太久",
        "Delta Force": "比如问今天冲哪个图、战况如何",
        "英雄联盟": "比如祝上大分、别生气、这波操作咋样",
        "League of Legends": "比如问这波操作咋样、别上头",
        "CS2": "比如聊ACE、枪男、手感如何",
        "CSGO": "比如聊ACE、手感如何",
        "无畏契约": "比如问这把赢了没、手感如何",
        "Valorant": "比如问这把赢了没",
        "原神": "比如问体力清了吗、今天抽卡了吗、宝箱找了多少",
        "Apex": "比如问今天能吃鸡、跳哪了",
        "PUBG": "比如问这局能吃到鸡不",
        "永劫无间": "比如聊振刀、出金、连胜了没",
        "DOTA2": "比如问这局局势如何、别被翻盘",
        "王者荣耀": "比如问这把赢了没、玩啥英雄、别连跪",
        "王者玩象棋": "比如问这步走得妙不妙、局势如何、将军了没",
        "崩坏": "比如问抽卡出货没、打到哪里了",
        "我的世界": "比如问建了啥、挖到钻石没",
    }

    def _get_foreground_title(self):
        """获取当前前台窗口标题。"""
        try:
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
            return buf.value
        except Exception:
            return ""

    def _get_foreground_process_name(self):
        """获取当前前台窗口的进程名(如 DeltaForce.exe)。"""
        try:
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            pid = ctypes.c_ulong()
            ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value:
                import psutil
                proc = psutil.Process(pid.value)
                return proc.name().lower()
        except Exception:
            pass
        return ""

    def _is_process_running(self, proc_name):
        """检查指定进程名是否在运行。"""
        try:
            import psutil
            for p in psutil.process_iter(["name"]):
                if p.info["name"] and p.info["name"].lower() == proc_name.lower():
                    return True
        except Exception:
            pass
        return False

    def _count_recordings(self):
        """统计 recordings 文件夹里的视频数量。"""
        try:
            rec_dir = str(app_dir() / "recordings")
            if os.path.isdir(rec_dir):
                return len([f for f in os.listdir(rec_dir) if f.endswith((".mp4", ".mkv", ".avi"))])
        except Exception:
            pass
        return 0

    def _on_game_stopped(self):
        """游戏退出时的反应:说话+自动停录屏+统计时长和视频。"""
        # 计算游戏时长
        duration_str = ""
        if self._game_start_time:
            seconds = int(time.monotonic() - self._game_start_time)
            minutes = seconds // 60
            if minutes >= 60:
                hours = minutes // 60
                minutes = minutes % 60
                duration_str = f"{hours}小时{minutes}分钟"
            elif minutes > 0:
                duration_str = f"{minutes}分钟"
            else:
                duration_str = f"{seconds}秒"

        # 自动停止录屏
        rec = get_recorder()
        if rec.is_recording:
            rec.stop()
            self._rec_btn.set_recording(False)
            self._rec_btn.hide()

        # 统计本次游戏期间新录制的视频数量
        total_rec = self._count_recordings()
        new_rec = max(0, total_rec - self._rec_count_at_game_start)

        # 说话
        game_name = self._current_game or "游戏"
        parts = [f"打完{game_name}啦？"]
        if duration_str:
            parts.append(f"玩了{duration_str}，")
        parts.append("辛苦啦~休息一下吧")
        if new_rec > 0:
            parts.append(f"这局帮你录了{new_rec}个精彩视频哦~")
        msg = "".join(parts)
        self._show_bubble(msg, duration=8000)

    def _check_active_events(self):
        """每 5 秒检查:游戏检测 + 凌晨关心。"""
        now = time.monotonic()
        # 0. 时间代谢: 情绪冷却 + 想念值随时间增长 (60 秒节流结算)
        try:
            if now - getattr(self, "_last_meta_tick", 0.0) >= 60.0:
                self._last_meta_tick = now
                from memory import tick_hormones   # 放函数内导入,避免和 memory 的懒加载顺序打架
                tick_hormones()
        except Exception:
            pass
        # 向量模型空闲释放(方案C): 与情绪结算解耦,每 5 秒直接检查,5 分钟不用就卸载
        try:
            from memory import release_embedding_if_idle
            release_embedding_if_idle()
        except Exception:
            pass

        # 1. 游戏检测
        title = self._get_foreground_title()
        detected_game = None
        for keyword in self.GAME_PROMPTS:
            if keyword.lower() in title.lower():
                detected_game = keyword
                break

        if detected_game and detected_game != self._current_game:
            # 刚切换到游戏,主动说话(节流 60 秒);播放音乐时闭嘴,专心听歌
            if now - self._last_game_say > 60 and not self._in_music_mode():
                self._last_game_say = now
                _, owner, _, idt = build_pet_identity()
                seed = self.GAME_PROMPTS[detected_game]
                prompt = (
                    f"{owner}正在玩{detected_game}。{seed}。"
                    f"你是{owner}的{idt['关系']}，像朋友一样随口说一句，"
                    f"动作风格：{idt['动作风格']}，禁用动作：{idt['禁用动作']}。"
                )
                active_chat.active_say(self, prompt, fallback_text="又来玩游戏啦，加油哦~")
            # 记录游戏进程名和开始时间(用于检测退出)
            if not self._game_process_name:
                proc_name = self._get_foreground_process_name()
                if proc_name:
                    self._game_process_name = proc_name
                    self._game_start_time = time.monotonic()
                    self._rec_count_at_game_start = self._count_recordings()
        self._current_game = detected_game

        # 1.5 游戏退出检测:有记录的进程时,检查是否还在运行
        if self._game_process_name and not self._is_process_running(self._game_process_name):
            # 进程消失了,游戏结束
            self._on_game_stopped()
            self._game_process_name = None
            self._game_start_time = None
            self._current_game = None

        # 2. 凌晨 0 点关心
        try:
            import datetime
            cur = datetime.datetime.now()
            today_str = cur.strftime("%Y-%m-%d")
            if cur.hour == 0 and cur.minute <= 5 and self._last_midnight != today_str:
                self._last_midnight = today_str
                _, owner, _, idt = build_pet_identity()
                active_chat.active_say(
                    self,
                    f"现在是凌晨 12 点，{owner}还没睡。你是{owner}的{idt['关系']}，温柔地催{owner}睡觉，关心{owner}身体。"
                    f"动作风格：{idt['动作风格']}，禁用动作：{idt['禁用动作']}。",
                    fallback_text=f"已经 12 点啦，{owner}该睡觉了哦~",
                )
        except Exception:
            pass

    # ---------- 游戏观察:截屏 + 视觉分析 ----------
    def _game_watch_check(self):
        """定时触发:游戏在前台时截屏分析,生成话术。播放音乐时跳过。"""
        if not self._current_game:
            return
        if self._in_music_mode():
            return  # 播放音乐时不评论,专心听歌看歌词
        # 王者玩象棋固定2分钟一次; 其他游戏按设置随机间隔(默认2~10分钟)
        is_chess = "王者玩象棋" in self._current_game
        if is_chess:
            nxt_ms = 120000
        else:
            lo = max(1, int(getattr(self, "_game_comment_min", 2)))
            hi = max(lo, int(getattr(self, "_game_comment_max", 10)))
            nxt_ms = random.randint(lo, hi) * 60000
        self._game_watch_timer.start(nxt_ms)
        self._last_game_watch = time.monotonic()
        self._do_game_watch(hr=None)

    def _idle_comment_check(self):
        """驱力驱动主动: 想念值超过阈值 或 超过保底时间, 才截图看看主人在干嘛。
        借鉴 OpenHer proactive: 无冲动=不打扰=零成本。"""
        if self._in_music_mode():
            return
        if self._current_game:
            return  # 游戏中有自己的观察定时器
        # ── 驱力检测: longing(想念值) + 距上次主动时间 ──
        try:
            from memory import load_char_state
            longing = load_char_state().get("longing", 0.0)
        except Exception:
            longing = 0.0
        intervals = getattr(self, "_user_settings", {}).get("intervals", {})
        threshold = intervals.get("idle_longing_threshold", 0.45)
        max_hours = intervals.get("idle_max_hours", 4)
        now = time.monotonic()
        last = getattr(self, "_last_idle_comment", 0.0)
        idle_hours = (now - last) / 3600.0 if last else 999.0
        # 触发条件: 想念值 > 阈值 或 距上次主动 > 保底小时
        if longing < threshold and idle_hours < max_hours:
            return  # 还没那么想你, 不打扰
        self._last_idle_comment = now
        _, owner, _, idt = build_pet_identity()
        # ── 触发: 一半概率纯文本撒娇(快、省视觉模型), 一半概率截图看看主人在干嘛 ──
        if random.random() < 0.5:
            from memory import get_emotion_status
            try:
                _, word, _, _ = get_emotion_status()
            except Exception:
                word = "想你"
            prompt = (
                f"你现在的心情是{word}，特别想{owner}。你是{owner}的{idt['关系']}，"
                f"像朋友发微信一样随口说一句想{owner}的话，短一点，别肉麻。"
                f"动作风格：{idt['动作风格']}，禁用动作：{idt['禁用动作']}。"
            )
            active_chat.active_say(self, prompt, fallback_text=f"{owner}，我想你了~")
            return
        # 截图看看主人在干嘛, 带想念语境
        import base64
        from PySide6.QtCore import QBuffer
        try:
            screen = QApplication.primaryScreen()
            pixmap = screen.grabWindow(0)
            buf = QBuffer()
            buf.open(QBuffer.ReadWrite)
            pixmap.save(buf, "PNG")
            img_b64 = base64.b64encode(buf.data().data()).decode()
        except Exception:
            return
        if img_b64:
            hint = f"{owner}好久没陪我了,我有点想{owner},偷偷看看{owner}在干嘛"
            active_chat.screenshot_comment_say(self, img_b64, extra_hint=hint, fallback_text=f"{owner}在忙什么呀~")

    def _do_game_watch(self, hr=None):
        """执行游戏观察:截屏 → 视觉分析 → 生成话术(不隐藏桌宠)。"""
        import base64
        from PySide6.QtCore import QBuffer

        try:
            screen = QApplication.primaryScreen()
            pixmap = screen.grabWindow(0)
            buf = QBuffer()
            buf.open(QBuffer.ReadWrite)
            pixmap.save(buf, "PNG")
            img_b64 = base64.b64encode(buf.data().data()).decode()
        except Exception:
            img_b64 = None

        if img_b64:
            hr_val = hr if hr else None
            fallback = f"在玩{self._current_game}，加油哦~"
            active_chat.game_watch_say(
                self, img_b64, self._current_game,
                hr=hr_val, fallback_text=fallback,
            )

    # ---------- 心率气泡 ----------
    def _on_heart_rate(self, hr):
        """心率处理:超阈值才关心,按间隔节流。"""
        self._last_hr_value = hr

        # 从设置读取阈值和间隔
        intervals = getattr(self, '_user_settings', {}).get("intervals", {})
        hr_threshold = intervals.get("hr_alert_threshold", 115)
        hr_interval = intervals.get("hr_alert_interval_min", 2) * 60  # 转秒

        # 心率 >105 自动开始录屏
        rec_threshold = intervals.get("recording_threshold", 105)
        if self._recording_enabled and hr > rec_threshold:
            rec = get_recorder()
            if not rec.is_recording:
                path = rec.start()
                if path:
                    self._rec_btn.set_recording(True)
                    self._update_rec_btn_pos()
                    self._rec_btn.show()
                    self._last_rec_err = ""
                    self._show_bubble("心率上来了，帮你录下来~", duration=4000)
                elif rec.last_error and not rec.last_error.startswith("刚刚启动失败过"):
                    # 自动录屏失败也要说一声,同一条错误只说一次,别刷屏
                    if self._last_rec_err != rec.last_error:
                        self._last_rec_err = rec.last_error
                        self._show_bubble(f"录屏没起来:{rec.last_error}", duration=6000)

        # 低于阈值不关心
        if hr < hr_threshold:
            return
        now = time.monotonic()

        # 节流
        if now - self._last_hr_watch < hr_interval:
            return
        self._last_hr_watch = now

        if self._current_game and not self._in_music_mode():
            self._do_game_watch(hr=hr)
        else:
            # 没在玩游戏也高心率,普通关心
            _, owner, _, idt = build_pet_identity()
            prompt = (
                f"{owner}当前心率 {hr}，比较高，可能在紧张或激动。"
                f"你是{owner}的{idt['关系']}，像朋友一样随口说一句关心或鼓励的话，短一点。"
                f"动作风格：{idt['动作风格']}，禁用动作：{idt['禁用动作']}。"
            )
            active_chat.active_say(self, prompt, fallback_text=f"心率 {hr}，稳住哦~")

    def _show_bubble(self, text, duration=None):
        """在宠物头顶显示气泡,淡入动画,按字数动态显示时间。"""
        # 日志:记录每次气泡显示的内容
        try:
            from paths import app_dir
            with open(str(app_dir() / "bubble_debug.log"), "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%H:%M:%S')}] show: {text}\n")
        except Exception:
            pass
        # 停止之前的淡出动画
        if self._bubble_fade_anim:
            self._bubble_fade_anim.stop()
            self._bubble_fade_anim = None
        self._bubble_widget.setText(text)
        self._update_bubble_position()
        # 淡入
        self._bubble_widget.setWindowOpacity(0.0)
        self._bubble_widget.show()
        self._bubble_widget.raise_()
        fade_in = QPropertyAnimation(self._bubble_widget, b"windowOpacity", self)
        fade_in.setDuration(200)
        fade_in.setStartValue(0.0)
        fade_in.setEndValue(1.0)
        fade_in.start()
        # 显示时间:传了 duration 就用,否则按字数动态算(最少6秒,最多12秒)
        if duration is None:
            duration = min(12000, max(6000, 6000 + (len(text) // 10) * 1000))
        self._bubble_timer.start(duration)

    def _hide_bubble_animated(self):
        """淡出动画后隐藏气泡。"""
        self._bubble_fade_anim = QPropertyAnimation(self._bubble_widget, b"windowOpacity", self)
        self._bubble_fade_anim.setDuration(300)
        self._bubble_fade_anim.setStartValue(1.0)
        self._bubble_fade_anim.setEndValue(0.0)
        self._bubble_fade_anim.finished.connect(self._bubble_widget.hide)
        self._bubble_fade_anim.start()

    def _update_bubble_position(self):
        """气泡跟随宠物,显示在头顶上方(屏幕坐标)。"""
        bw = self._bubble_widget.width()
        bh = self._bubble_widget.height()
        if bw < 1 or bh < 1:
            return
        bx = int(self.x - bw / 2)
        by = int(self.y - self.h - bh - 8)
        # 防止超出屏幕
        bx = max(4, min(bx, self.sw - bw - 4))
        by = max(4, by)
        self._bubble_widget.move(bx, by)

    # ---------- 隐藏/显示 ----------
    def _hide_pet(self):
        """隐藏桌宠,显示系统托盘图标。"""
        self.hide()
        self._rec_btn.hide()
        self._bubble_widget.hide()
        self._tray.show()
        self._tray.showMessage("BoringPet", "桌宠已隐藏,点击托盘图标重新显示", QSystemTrayIcon.Information, 2000)

    def _on_tray_activated(self, reason):
        """点击托盘图标时显示桌宠。"""
        if reason == QSystemTrayIcon.Trigger:
            self._show_pet()

    def _show_pet(self):
        """显示桌宠,从天下落登场。"""
        self._tray.hide()
        self.showNormal()
        self.raise_()
        self.activateWindow()
        # 如果正在录屏,显示停止按钮
        if get_recorder().is_recording:
            self._update_rec_btn_pos()
            self._rec_btn.show()
        self._start_fall_from_sky()

    def moveEvent(self, e):
        """桌宠移动时,录制按钮跟随。"""
        super().moveEvent(e)
        self._update_rec_btn_pos()

    def _update_rec_btn_pos(self):
        """更新录制按钮位置:屏幕右上角。"""
        if not hasattr(self, "_rec_btn"):
            return
        scr = QApplication.primaryScreen().availableGeometry()
        bx = scr.width() - 50
        by = 20
        self._rec_btn.move(bx, by)

    def _toggle_record(self):
        """手动切换录屏开始/停止。"""
        rec = get_recorder()
        if rec.is_recording:
            path = rec.stop()
            self._rec_btn.set_recording(False)
            self._rec_btn.hide()
            if path:
                self._show_bubble("录好了，在 recordings 文件夹~", duration=5000)
        else:
            path = rec.start()
            if path:
                self._rec_btn.set_recording(True)
                self._update_rec_btn_pos()
                self._rec_btn.show()
                self._last_rec_err = ""
                if rec.last_mode == "video-only":
                    self._show_bubble("开始录屏啦(没录到声音)~", duration=4000)
                else:
                    self._show_bubble("开始录屏啦~", duration=3000)
            else:
                # 这里是以前最大的坑:start() 失败时什么都不做,
                # 用户点了「开始录屏」屏幕上一点变化都没有 = 「没反应」。
                self._rec_btn.set_recording(False)
                self._rec_btn.hide()
                msg = rec.last_error or "未知原因"
                self._last_rec_err = msg
                self._show_bubble(f"录屏没起来:{msg}", duration=7000)
                print(f"[录屏] 启动失败: {msg}")

    def _update_menu_rec_text(self):
        """菜单显示时更新录屏选项文字。"""
        if hasattr(self, "_manual_rec_action"):
            rec = get_recorder()
            self._manual_rec_action.setText("停止录屏" if rec.is_recording else "开始录屏")

    # ── 音乐控制 ──
    def _music_play_pause(self):
        mp = MusicPlayer.get()
        name = mp.play_pause()
        if name:
            if mp.is_playing:
                self._show_bubble(f"正在播放：{name}~", duration=4000)
            else:
                self._show_bubble("暂停啦~", duration=2000)

    def _music_next(self):
        mp = MusicPlayer.get()
        name = mp.next_song()
        if name:
            self._show_bubble(f"下一首：{name}~", duration=4000)

    def _music_prev(self):
        mp = MusicPlayer.get()
        name = mp.prev_song()
        if name:
            self._show_bubble(f"上一首：{name}~", duration=4000)

    def _music_vol_up(self):
        mp = MusicPlayer.get()
        vol = mp.volume_up()
        self._show_bubble(f"音量 {int(vol*100)}%", duration=1500)

    def _music_vol_down(self):
        mp = MusicPlayer.get()
        vol = mp.volume_down()
        self._show_bubble(f"音量 {int(vol*100)}%", duration=1500)

    def _music_toggle_shuffle(self):
        mp = MusicPlayer.get()
        on = mp.toggle_shuffle()
        self._show_bubble("随机播放已开启~" if on else "顺序播放~", duration=2000)

    def _music_stop(self):
        """停止播放(重置状态,下次播放重新扫描)。"""
        mp = MusicPlayer.get()
        if mp.is_playing or mp._paused:
            mp.stop()
            self._show_bubble("音乐已停止~", duration=2500)

    def _on_music_state_changed(self, playing):
        """音乐状态变化: 开始播放/暂停时偶尔说一句(节流5分钟), 增强陪伴感。"""
        try:
            now = time.monotonic()
            if now - getattr(self, "_last_music_say", 0.0) < 300:
                return
            self._last_music_say = now
            mp = MusicPlayer.get()
            _, owner, _, idt = build_pet_identity()
            if playing:
                name = mp.current_song_name or ""
                if not name:
                    return
                prompt = (
                    f"{owner}在听「{name}」。你是{owner}的{idt['关系']}，"
                    f"听到这首歌随口说一句感受(喜欢/好奇/调侃都行)，短一点，像发微信。"
                    f"动作风格：{idt['动作风格']}，禁用动作：{idt['禁用动作']}。"
                )
                active_chat.active_say(self, prompt, fallback_text=f"这首歌不错哦~")
            else:
                active_chat.active_say(
                    self,
                    f"音乐停了，你是{owner}的{idt['关系']}，轻轻跟{owner}说一句，短一点，别肉麻。"
                    f"动作风格：{idt['动作风格']}，禁用动作：{idt['禁用动作']}。",
                    fallback_text="听完了？想听我再放~",
                )
        except Exception:
            pass

    def _on_lyric_changed(self, lyric):
        """播放音乐时,歌词变化更新气泡。"""
        self._show_bubble(lyric, duration=8000)

    def _start_fall_from_sky(self):
        """从屏幕顶端上方开始下落,落到屏幕中间。"""
        self._landing = True
        self.vy = 0.0
        self.y = -self.h
        self.x = max(self.w / 2, min(self.x, self.sw - self.w / 2))
        self._land_y = int(self.sh * 0.5 + self.target_h / 2)
        self.set_state("fall", sound=False)
        self.behave_until = time.monotonic() + 30.0
        self.place()

    # ---------- 开机自启动 ----------
    AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
    AUTOSTART_NAME = "BoringPet"

    def _is_autostart(self):
        """检查是否已设置开机自启动。"""
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.AUTOSTART_KEY, 0, winreg.KEY_READ) as key:
                winreg.QueryValueEx(key, self.AUTOSTART_NAME)
                return True
        except FileNotFoundError:
            return False
        except OSError:
            return False

    def _set_autostart(self, enable):
        """设置或取消开机自启动。"""
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.AUTOSTART_KEY, 0, winreg.KEY_SET_VALUE) as key:
                if enable:
                    if getattr(sys, "frozen", False):
                        # 打包后:直接用 exe 路径
                        cmd = f'"{sys.executable}"'
                    else:
                        # 开发环境:用 pythonw.exe + main.py
                        main_py = os.path.join(str(resource_dir()), "main.py")
                        cmd = f'"{sys.executable.replace("python.exe", "pythonw.exe")}" "{main_py}"'
                    winreg.SetValueEx(key, self.AUTOSTART_NAME, 0, winreg.REG_SZ, cmd)
                else:
                    try:
                        winreg.DeleteValue(key, self.AUTOSTART_NAME)
                    except FileNotFoundError:
                        pass
        except OSError as e:
            print(f"设置开机自启动失败: {e}")

    # ---------- 主循环 ----------
    def tick(self):
        now = time.monotonic()
        dt = (now - self._last_tick) if self._last_tick else (1.0 / config.FPS)
        self._last_tick = now
        dt = min(dt, 0.1)   # 防止长时间挂起后动画跳变

        # 录屏停止检查:心率降下来且录满最低时长才结束;或达最长时长兜底停止(防磁盘录满)
        rec = get_recorder()
        if rec.is_recording and rec.can_stop() and (
                self._last_hr_value < 105 or rec.elapsed >= rec.MAX_DURATION):
            path = rec.stop()
            self._rec_btn.set_recording(False)
            self._rec_btn.hide()
            if path:
                self._show_bubble("录好了，在 recordings 文件夹~", duration=5000)
        # 抓帧线程意外挂了(ffmpeg 中途退出,比如音频设备被抢):把录制按钮收掉并提示。
        # 不做这一步的话按钮会一直显示"正在录制",而其实什么都没录 —— UI 在骗人,
        # 而且用户接着点「开始录屏」会撞上失败冷却,表现就是「没反应」。
        elif self._rec_btn.isVisible() and not rec.is_recording:
            self._rec_btn.set_recording(False)
            self._rec_btn.hide()
            msg = rec.last_error or "录屏中断了"
            if self._last_rec_err != msg:
                self._last_rec_err = msg
                self._show_bubble(f"录屏中断:{msg}", duration=6000)

        # 登场下落:重力加速,落地后转待机
        if self._landing:
            self.vy = min(self.vy + GRAV * dt, MAX_VY)
            self.y += self.vy * dt
            if self.y >= self._land_y:
                self.y = self._land_y
                self.vy = 0.0
                self._landing = False
                self.set_state("idle")
                self.behave_until = now + random.uniform(4, 10)

        # 帧推进(按动画 fps 累积,与 tick 频率解耦)
        if self._imgs:
            self._acc += dt
            interval = 1.0 / self.anim_fps
            if self._acc > interval * 8:
                self._acc = interval * 8
            while self._acc >= interval:
                self._acc -= interval
                self.frame += 1
            n = len(self._imgs)
            lf = config.loop_from(self.state)
            if lf is not None and n > lf:
                # sleep:前 lf 帧(躺下)只播一遍,之后循环"躺着"部分
                if self.frame >= n:
                    self.frame = lf + (self.frame - lf) % (n - lf)
            else:
                self.frame %= n

        if not self._dragging and not self._landing:
            if self.state in ("walk", "idle"):
                if self.state == "walk":
                    self.x += self.vx
                    if self.x <= self.w / 2:
                        self.x = self.w / 2
                        self.vx = abs(self.vx)
                        self.set_direction(1)
                    elif self.x >= self.sw - self.w / 2:
                        self.x = self.sw - self.w / 2
                        self.vx = -abs(self.vx)
                        self.set_direction(-1)
                if now >= self.behave_until:
                    self.choose_behavior()
            else:
                # 一次性动画(yawn/sleep/happy/hurt/home)播完回待机
                if now >= self.behave_until:
                    self.set_state("idle")
                    self.behave_until = now + random.uniform(20, 35)

        self.place()
        self._update_bubble_position()

    def _frame_index(self):
        """当前帧索引(处理 sleep 的循环起点)。"""
        n = len(self._imgs)
        if n <= 0:
            return 0
        f = self.frame
        if f >= n:
            lf = config.loop_from(self.state)
            if lf is not None and n > lf:
                f = lf + (f - lf) % (n - lf)
            else:
                f %= n
        return f

    def place(self):
        imgs = self._imgs
        if not imgs or self.w < 1 or self.h < 1:
            return
        img = imgs[self._frame_index()]
        # 只有帧变化才重设图片(避免 idle 原地站着时反复重绘)
        if img is not self._last_img:
            self._last_img = img
            self.label.setPixmap(img)
            self.label.setGeometry((self.w - img.width()) // 2,
                                   (self.h - img.height()) // 2,
                                   img.width(), img.height())
        # 只有尺寸变化才 resize
        if (self.w, self.h) != self._last_size:
            self._last_size = (self.w, self.h)
            self.resize(self.w, self.h)
        wx = int(self.x - self.w / 2)
        wy = int(self.y - self.h)
        wx = max(0, min(wx, self.sw - self.w))
        wy = max(0, min(wy, self.sh - self.h))
        # 只有位置变化才 move(透明窗口 move 开销大,idle 时别反复调)
        if (wx, wy) != self._last_pos:
            self._last_pos = (wx, wy)
            self.move(wx, wy)

    # ---------- 退出 ----------
    def closeEvent(self, e):
        # 退出前全面清理:避免孤儿进程 / 未回收线程 / 残留窗口
        sounds.stop()
        # 停止录屏(若有),避免 ffmpeg 孤儿进程继续写盘、句柄不释放
        rec = get_recorder()
        if rec.is_recording:
            rec.stop()
            self._rec_btn.set_recording(False)
            self._rec_btn.hide()
        # 停止音乐播放与 pygame mixer
        mp = MusicPlayer.get()
        if mp.is_playing or mp._paused:
            mp.stop()
        # 停止心率/蓝牙(HeartManager 内部负责 join 其线程)
        self.heart.stop()
        self._bubble_widget.close()
        if self.heart_win is not None:
            self.heart_win.close()
        if self._lyric_win is not None:
            self._lyric_win.close()
        if self._chat_win is not None:
            self._chat_win.close()
        # 主动消息后台线程:标记停止(让 run() 早日退出)
        for w in list(getattr(self, "_active_workers", []) or []):
            try:
                w.requestInterruption()
            except Exception:
                pass
        QApplication.quit()
        super().closeEvent(e)
