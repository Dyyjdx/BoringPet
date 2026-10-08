# -*- coding: utf-8 -*-
"""心率监测模块:BLE 扫描/连接、心率解析、桌面爱心浮窗、完整监测窗口(波形自绘)。

整合自 legacy/heart_monitor_v2.py(测试2.py),主要改动:
  * 去掉 pyqtgraph / numpy(与本机 numpy2 不兼容,import 即崩),波形改用 QPainter 自绘;
  * 修复心率解析:原代码只取 d[1],16 位心率设备会读出错误数值;现在按 flags 区分 8/16 位;
  * BLE 线程统一由 HeartManager 管理,宠物左键菜单可以直接控制;
  * 爱心浮窗的大小/透明度/颜色可在宠物菜单和浮窗右键菜单里调整。
"""
import asyncio
import glob
import os
from collections import deque
from datetime import datetime

from PySide6.QtCore import (Property, QEasingCurve, QObject, QPoint, QPointF,
                            QPropertyAnimation, QSize, Qt, QThread, QTimer, Signal)
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QComboBox, QFrame, QGraphicsDropShadowEffect,
                               QHBoxLayout, QLabel, QMainWindow, QMenu,
                               QPushButton, QSlider, QVBoxLayout, QWidget)

from bleak import BleakClient, BleakScanner

HEART_UUID = "00002a37-0000-1000-8000-00805f9b34fb"

# ── 连接超时与自动重连 ──
CONNECT_TIMEOUT = 10       # 单次连接超时(秒),防"一直连不上就无限卡住"
RECONNECT_BACKOFF = 2.0    # 断线后首次重连间隔(秒)
RECONNECT_BACKOFF_MAX = 15.0  # 重连退避上限(秒)
RECONNECT_MAX_RETRIES = 12    # 自动重连次数上限,防死循环

# ── 配色 ──
C_BG = "#f5f8ff"
C_CARD = "#ffffff"
C_TITLE = "#2a3a5a"
C_TEXT2 = "#8a9ab8"
C_ACCENT = "#e879a8"
C_GREEN = "#4ecdc4"
C_ORANGE = "#f0a868"
C_RED = "#e05678"
C_BLUE = "#6b9dff"
C_DIVIDER = "#e0e8f8"

MENU_QSS = """
QMenu{background:#ffffff;border:none;border-radius:14px;padding:8px;}
QMenu::item{padding:9px 18px;font-size:13px;color:#2a3a5a;border-radius:8px;}
QMenu::item:selected{background:qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #7ba8ff, stop:1 #f090bc);color:white;}
QMenu::separator{height:1px;background:qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #d0e0ff, stop:1 #f5d5e8);margin:5px 10px;}
QMenu::indicator{width:14px;height:14px;border-radius:4px;border:1px solid #c8d0e8;}
QMenu::indicator:checked{background:qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #7ba8ff, stop:1 #f090bc);border:none;}
"""

# ── 图标资源 ──
from paths import resource_dir

BASE = str(resource_dir())
ICON_DIR = os.path.join(BASE, "图标")
HEART_STYLE_DIR = os.path.join(BASE, "心率图标")


def _ico(name, dir_=ICON_DIR):
    """加载 图标/ 或 心率图标/ 下的资源,缺失返回空 QIcon(调用方自行降级)。"""
    p = os.path.join(dir_, name)
    if not os.path.exists(p):
        return QIcon()
    if name.lower().endswith(".ico"):
        ico = QIcon()
        for sz in (16, 32, 48, 64, 256):
            ico.addFile(p, QSize(sz, sz))
        return ico
    return QIcon(QPixmap(p))


def shadow():
    s = QGraphicsDropShadowEffect()
    s.setBlurRadius(16)
    s.setOffset(0, 3)
    s.setColor(QColor("#0000000a"))
    return s


def hr_color(hr):
    """心率 -> 波形/文本颜色:绿 <100, 橙 <130, 红 >=130。"""
    if hr < 100:
        return C_GREEN
    if hr < 130:
        return C_ORANGE
    return C_RED


def parse_hr(data):
    """BLE Heart Rate Measurement 解析。

    第 0 字节是 flags:bit0=1 表示心率值是 16 位(小端),否则是 8 位。
    返回 int 或 None(数据不足时)。
    """
    if not data or len(data) < 2:
        return None
    flags = data[0]
    if flags & 0x01:
        if len(data) < 3:
            return None
        return int.from_bytes(data[1:3], "little")
    return int(data[1])


# ════════════════ 蓝牙线程 ════════════════
class ScanThread(QThread):
    deviced = Signal(str, str)
    successed = Signal()

    def run(self):
        asyncio.run(self._run())

    async def _run(self):
        try:
            for d in await BleakScanner.discover(timeout=5):
                if d.name and d.address:
                    self.deviced.emit(d.name, d.address)
        except Exception:
            pass
        finally:
            self.successed.emit()


class ConnectThread(QThread):
    """连接 + 自动重连线程。

    - 每次连接带 CONNECT_TIMEOUT 超时,设备不可达不会无限卡住;
    - 断线后按 RECONNECT_BACKOFF 退避自动重试(有次数上限),直到用户 stop 或
      达到上限才退出,不必手动重新打开监测窗口。
    """
    connected = Signal()
    disconnected = Signal()
    hr = Signal(int)

    def __init__(self, address):
        super().__init__()
        self._address = address
        self._running = True
        self._connected = False

    @property
    def is_connected(self):
        """是否真的已连上(不再用 isRunning 误判)。"""
        return self._connected

    def stop(self):
        self._running = False

    def run(self):
        asyncio.run(self._run())

    async def _connect_once(self):
        """尝试连一次,带超时。成功返回 BleakClient,失败/超时返回 None。"""
        try:
            client = BleakClient(self._address)
            await asyncio.wait_for(client.connect(), timeout=CONNECT_TIMEOUT)
            if client.is_connected:
                return client
            try:
                await client.disconnect()
            except Exception:
                pass
            return None
        except Exception:
            return None

    async def _run(self):
        retries = 0
        backoff = RECONNECT_BACKOFF
        while self._running:
            client = await self._connect_once()
            if client is None or not client.is_connected:
                # 连接失败:退避重试,给用户留出反应时间
                retries += 1
                if not self._running or retries > RECONNECT_MAX_RETRIES:
                    break
                waited = 0.0
                while self._running and waited < backoff:
                    await asyncio.sleep(0.25)
                    waited += 0.25
                backoff = min(backoff * 1.5, RECONNECT_BACKOFF_MAX)
                continue

            # 连接成功
            retries = 0
            backoff = RECONNECT_BACKOFF
            self._connected = True
            self.connected.emit()
            try:
                self._cb = lambda _s, data: self._notify(data)
                await client.start_notify(HEART_UUID, self._cb)
                while self._running and client.is_connected:
                    await asyncio.sleep(0.1)
            except Exception:
                pass
            finally:
                self._connected = False
                try:
                    if client.is_connected:
                        await client.disconnect()
                except Exception:
                    pass
            self.disconnected.emit()
            # 断线了但还在运行 → 回到 while 顶部自动重连
        self._connected = False
        self.disconnected.emit()

    def _notify(self, data):
        v = parse_hr(bytes(data))
        if v is not None:
            self.hr.emit(v)


# ════════════════ 心率管家(桌宠菜单直接操作它) ════════════════
class HeartManager(QObject):
    hr = Signal(int)
    connected = Signal()
    disconnected = Signal()
    device_found = Signal(str, str)
    scan_finished = Signal()

    def __init__(self):
        super().__init__()
        self._scanner = None
        self._connector = None
        self._float = None
        # 正在被替换/停止、但 run() 还没跑完的旧线程。
        # 防止“新建线程直接覆盖旧引用”导致旧线程对象被立即回收、线程+BLE 连接泄漏。
        self._retiring = []

    def _retire_thread(self, t):
        """请求 t 停止, 并把它移进 _retiring 队列托管; 等它真正 run 结束再 deleteLater。
        - 不阻塞 GUI 线程(不 wait), 避免冻结界面;
        - finished -> deleteLater 释放对象, 同时从队列移除, 队列不会无限增长。"""
        if t is None or not t.isRunning():
            return
        t.stop()
        if t not in self._retiring:
            self._retiring.append(t)
            t.finished.connect(lambda: self._drop_retiring(t))
            try:
                t.finished.connect(t.deleteLater)
            except Exception:
                pass

    def _drop_retiring(self, t):
        try:
            if t in self._retiring:
                self._retiring.remove(t)
        except ValueError:
            pass

    # ---- BLE ----
    def start_scan(self):
        if self._scanner and self._scanner.isRunning():
            return
        self._scanner = ScanThread()
        self._scanner.deviced.connect(self.device_found)
        self._scanner.successed.connect(self.scan_finished)
        self._scanner.start()

    def connect_device(self, address):
        if not address:
            return
        # 旧的连接线程若还在跑: 先托管退场, 不要直接覆盖丢引用
        if self._connector and self._connector.isRunning():
            self._retire_thread(self._connector)
        self._connector = ConnectThread(address)
        self._connector.connected.connect(self.connected)
        self._connector.disconnected.connect(self._on_disconnected)
        self._connector.hr.connect(self._on_hr)
        self._connector.start()

    def disconnect_device(self):
        if self._connector and self._connector.isRunning():
            self._retire_thread(self._connector)

    def is_connected(self):
        return bool(self._connector and self._connector.is_connected)

    def _on_hr(self, v):
        self.hr.emit(v)
        if self._float is not None:
            self._float.set_hr(v)

    def _on_disconnected(self):
        self.disconnected.emit()

    # ---- 爱心浮窗 ----
    def show_float(self):
        if self._float is None:
            self._float = HeartFloat()
        self._float.show()
        self._float.raise_()

    def hide_float(self):
        if self._float is not None:
            self._float.hide()

    def float_visible(self):
        return self._float is not None and self._float.isVisible()

    def set_float_scale(self, f):
        if self._float is None:
            self._float = HeartFloat()
        self._float.set_scale(f)

    def set_float_opacity(self, v):
        if self._float is None:
            self._float = HeartFloat()
        self._float.set_opacity(v)

    def set_float_color(self, c):
        if self._float is None:
            self._float = HeartFloat()
        self._float.set_text_color(c)

    def set_float_style(self, img_path):
        if self._float is None:
            self._float = HeartFloat()
        self._float.set_style(img_path)

    def stop(self):
        if self._connector and self._connector.isRunning():
            self._connector.stop()
        if self._float is not None:
            self._float.close()


# ════════════════ 桌面爱心浮窗 ════════════════
class HeartFloat(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._w, self._h = 380, 220
        self._hr = "--"
        self._text_color = "#e8544a"
        self._drag = None
        default_style = os.path.join(HEART_STYLE_DIR, "红色经典.png")
        self._heart_img = default_style if os.path.exists(default_style) else None
        self._build()

    def _build(self):
        self.resize(self._w, self._h)
        b = int(self._h * 0.55)
        self.xin = QLabel(self)
        self._img(b)
        ml = int(self._w * 0.06)
        my = (self._h - b) // 2
        self.xin.setGeometry(ml, my, b, b)
        hs = int(b * 0.5)
        self.hl = QLabel(self._hr, self)
        self.hl.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self._style_text(hs)
        lx = ml + b + 2
        lw = self._w - lx - ml
        lh = int(hs * 1.5)
        self.hl.setGeometry(lx, (self._h - lh) // 2, lw, lh)
        self._anim(b)

    def _img(self, b):
        p = self._heart_img
        if p and os.path.exists(p):
            self.xin.setPixmap(
                QPixmap(p).scaled(b, b, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.xin.setText("❤️")
            self.xin.setAlignment(Qt.AlignCenter)
            self.xin.setStyleSheet(
                f"font-size:{int(b * 0.7)}px;color:{self._text_color};background:transparent;")

    def set_style(self, img_path):
        self._heart_img = img_path
        b = int(self._h * 0.55)
        self._img(b)
        self.xin.setGeometry(int(self._w * 0.06), (self._h - b) // 2, b, b)

    def _style_text(self, hs):
        self.hl.setStyleSheet(
            f"font-size:{hs}px;font-weight:800;color:{self._text_color};"
            f"background:transparent;font-family:'Segoe UI',sans-serif;")

    def _anim(self, b):
        self.anim = QPropertyAnimation(self.xin, b"size")
        self.anim.setDuration(700)
        self.anim.setLoopCount(-1)
        l = int(b * 0.15)
        d = int(b * 0.10)
        r = int(b * 0.03)
        self.anim.setKeyValueAt(0.0, QSize(b, b))
        self.anim.setKeyValueAt(0.08, QSize(b - l, b - l))
        self.anim.setKeyValueAt(0.18, QSize(b + d, b + d))
        self.anim.setKeyValueAt(0.35, QSize(b - r, b - r))
        self.anim.setKeyValueAt(0.5, QSize(b + r, b + r))
        self.anim.setKeyValueAt(1.0, QSize(b, b))
        self.anim.setEasingCurve(QEasingCurve.InOutSine)
        self.anim.start()

    # ---- 外部接口 ----
    def set_hr(self, v):
        self._hr = str(v)
        if hasattr(self, "hl"):
            self.hl.setText(self._hr)

    def set_scale(self, f):
        self._w = max(150, int(380 * f))
        self._h = max(100, int(220 * f))
        self.resize(self._w, self._h)
        b = int(self._h * 0.55)
        self._img(b)
        ml = int(self._w * 0.06)
        my = (self._h - b) // 2
        self.xin.setGeometry(ml, my, b, b)
        self._style_text(int(b * 0.5))
        lx = ml + b + 2
        lw = self._w - lx - ml
        lh = int(b * 0.5 * 1.5)
        self.hl.setGeometry(lx, (self._h - lh) // 2, lw, lh)
        self.anim.stop()
        self._anim(b)

    def set_text_color(self, color):
        self._text_color = color
        self._style_text(int(self._h * 0.55 * 0.5))
        self._img(int(self._h * 0.55))

    def set_opacity(self, v):
        self.setWindowOpacity(v)

    # ---- 拖动 ----
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e):
        if e.buttons() == Qt.LeftButton and self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)
            e.accept()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = None
            e.accept()

    # ---- 右键菜单 ----
    def contextMenuEvent(self, e):
        menu = QMenu(self)
        menu.setStyleSheet(MENU_QSS)
        ms = QGraphicsDropShadowEffect()
        ms.setBlurRadius(20)
        ms.setOffset(0, 6)
        ms.setColor(QColor("#00000018"))
        menu.setGraphicsEffect(ms)

        sz = menu.addMenu("调节大小")
        sz.setStyleSheet(MENU_QSS)
        for txt, f in (("小", 0.5), ("中", 0.75), ("大", 1.0), ("特大", 1.5)):
            a = sz.addAction(txt)
            a.setData(f)
        sz.triggered.connect(lambda a: self.set_scale(a.data()))

        op = menu.addMenu("调节透明度")
        op.setStyleSheet(MENU_QSS)
        for txt, v in (("30%", 0.3), ("50%", 0.5), ("70%", 0.7), ("100%", 1.0)):
            a = op.addAction(txt)
            a.setData(v)
        op.triggered.connect(lambda a: self.set_opacity(a.data()))

        menu.addSeparator()
        menu.addAction("关闭", self.hide)
        menu.exec(e.globalPos())


# ════════════════ 波形(自绘,替代 pyqtgraph) ════════════════
class WaveWidget(QWidget):
    """滚动心率波形:150 个点,纵轴 50~150,颜色随当前心率变化。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data = deque([50] * 150, maxlen=150)
        self.setMinimumHeight(160)

    def append(self, v):
        self._data.append(v)
        self.update()

    def clear(self):
        self._data = deque([50] * 150, maxlen=150)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        if w < 2 or h < 2:
            p.end()
            return

        def y_of(v):
            t = (v - 50) / 100.0
            return h - 12 - t * (h - 24)

        # 网格(50/75/100/125/150)
        p.setPen(QPen(QColor("#e8e8f0"), 1))
        for gv in (50, 75, 100, 125, 150):
            gy = y_of(gv)
            p.drawLine(0, int(gy), w, int(gy))

        # 波形
        n = len(self._data)
        pts = []
        for i, v in enumerate(self._data):
            x = i * w / (n - 1)
            pts.append(QPointF(x, y_of(v)))
        cur = self._data[-1]
        p.setPen(QPen(QColor(hr_color(cur)), 2))
        p.drawPolyline(pts)
        p.end()


# ════════════════ 脉冲环(当前心率卡片背后的扩散圈) ════════════════
class PulseRing(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._a = 30
        self._anim = QPropertyAnimation(self, b"ring_alpha")
        self._anim.setDuration(700)
        self._anim.setLoopCount(-1)
        self._anim.setKeyValueAt(0.0, 50)
        self._anim.setKeyValueAt(0.5, 8)
        self._anim.setKeyValueAt(1.0, 50)
        self._anim.setEasingCurve(QEasingCurve.InOutSine)
        self._anim.start()

    def _ga(self):
        return self._a

    def _sa(self, a):
        self._a = int(a)
        self.update()

    ring_alpha = Property(int, _ga, _sa)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = min(self.width(), self.height()) // 2 - 4
        if r > 0:
            c = QColor(C_ACCENT)
            c.setAlpha(self._a)
            p.setPen(Qt.NoPen)
            p.setBrush(c)
            p.drawEllipse(self.rect().center(), r, r)
        p.end()


# ════════════════ 完整监测窗口 ════════════════
class HeartWindow(QMainWindow):
    def __init__(self, manager):
        super().__init__()
        self.manager = manager
        self.setWindowTitle("心率监测")
        self.setFixedSize(840, 580)
        self.setWindowIcon(_ico("程序.ico"))

        central = QWidget()
        central.setStyleSheet(f"background:{C_BG};")
        self.setCentralWidget(central)

        self.max_hr = 0
        self.hr_sum = 0
        self.hr_cnt = 0
        self.conn_start = None
        self.conn_timer = QTimer(self)
        self.conn_timer.timeout.connect(self._tick)

        # 波形
        self.wave = WaveWidget()

        # 状态栏
        self.statusBar().setStyleSheet(
            f"QStatusBar{{background:{C_BG};border-top:1px solid {C_DIVIDER};"
            f"font-size:12px;color:{C_TEXT2};padding:1px 12px;}}")
        self.s_dot = QLabel("●")
        self.s_dot.setStyleSheet("font-size:7px;color:#8e8ea0;")
        self.s_txt = QLabel("就绪")
        self.s_addr = QLabel("")
        self.statusBar().addPermanentWidget(self.s_dot)
        self.statusBar().addPermanentWidget(self.s_txt)
        self.statusBar().addPermanentWidget(self.s_addr)

        self._build(central)
        self.lanya.currentIndexChanged.connect(self._sel)

        # 管家信号 -> 界面
        manager.device_found.connect(self._found)
        manager.scan_finished.connect(lambda: self.s_txt.setText("就绪"))
        manager.hr.connect(self._hr)
        manager.connected.connect(self._con)
        manager.disconnected.connect(self._dis)

    # ── 界面 ──
    def _build(self, central):
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 左侧导航
        nav = QFrame()
        nav.setFixedWidth(210)
        nav.setStyleSheet(f"background:{C_CARD};")
        nav.setGraphicsEffect(shadow())
        nl = QVBoxLayout(nav)
        nl.setContentsMargins(18, 24, 18, 18)
        nl.setSpacing(4)

        t = QLabel("心率监测")
        t.setStyleSheet(f"font-size:17px;font-weight:800;color:{C_TITLE};background:transparent;padding-bottom:16px;")
        nl.addWidget(t)

        nl.addWidget(self._lbl("蓝牙设备"))
        self.lanya = QComboBox()
        self.lanya.addItem("选择设备...")
        self.lanya.setStyleSheet(
            f"QComboBox{{background:transparent;color:{C_TITLE};border:none;border-radius:6px;"
            f"padding:7px 8px;font-size:13px;}}"
            f"QComboBox:hover{{background:#eef2ff;}}"
            f"QComboBox::drop-down{{border:none;width:22px;}}"
            f"QComboBox QAbstractItemView{{background:{C_CARD};color:{C_TITLE};border:none;"
            f"border-radius:6px;selection-background-color:#eef2ff;padding:4px;}}")
        nl.addWidget(self.lanya)
        nl.addSpacing(6)
        nl.addWidget(self._btn("扫描设备", self.manager.start_scan, icon=_ico("扫描设备.png")))
        nl.addWidget(self._btn("连接设备", self._connect, icon=_ico("连接设备.png")))
        self.bd = self._btn("断开连接", self.manager.disconnect_device, True,
                            icon=_ico("断开连接.png"))
        self.bd.setEnabled(False)
        nl.addWidget(self.bd)

        nl.addSpacing(20)
        nl.addWidget(QFrame(styleSheet=f"background:{C_DIVIDER};max-height:1px;"))

        # 爱心控制
        nl.addSpacing(16)
        nl.addWidget(self._lbl("心率设置"))
        self.bh = self._btn("显示爱心", self._toggle_heart, icon=_ico("心率显示.png"))
        nl.addWidget(self.bh)
        nl.addSpacing(6)
        nl.addLayout(self._slider("大小", 50, 200, 100,
                                  lambda v: self.manager.set_float_scale(v / 100.0),
                                  icon_file="大小调节 .png"))
        nl.addLayout(self._slider("透明", 10, 100, 100,
                                  lambda v: self.manager.set_float_opacity(v / 100.0),
                                  icon_file="透明度.png"))
        nl.addLayout(self._style_selector())
        nl.addLayout(self._color_selector())
        nl.addStretch(1)

        # 右侧
        main = QWidget()
        main.setContentsMargins(24, 20, 20, 16)
        ml2 = QVBoxLayout(main)
        ml2.setSpacing(14)

        ml2.addWidget(self._cards())

        pf = QFrame()
        pf.setStyleSheet(f"background:{C_CARD};border:1px solid {C_DIVIDER};border-radius:14px;")
        pf.setGraphicsEffect(shadow())
        pv = QVBoxLayout(pf)
        pv.setContentsMargins(14, 10, 14, 10)
        pv.addWidget(self.wave)
        ml2.addWidget(pf, stretch=1)

        self.lc = QLabel("")
        self.lc.setStyleSheet(f"color:{C_TEXT2};font-size:12px;background:transparent;padding:0 4px;")
        ml2.addWidget(self.lc)

        root.addWidget(nav)
        root.addWidget(main, stretch=1)

    # ── 小组件 ──
    def _lbl(self, t):
        l = QLabel(t)
        l.setStyleSheet(
            f"color:{C_TEXT2};font-size:10px;font-weight:700;letter-spacing:1.5px;"
            f"background:transparent;padding:6px 2px 4px 2px;")
        return l

    def _btn(self, t, cb, danger=False, icon=None):
        b = QPushButton(t)
        b.setCursor(Qt.PointingHandCursor)
        c = C_RED if danger else C_TITLE
        if icon:
            b.setIcon(icon)
            b.setIconSize(QSize(22, 22))
        b.setStyleSheet(
            f"QPushButton{{background:transparent;color:{c};border:none;border-radius:8px;"
            f"padding:10px 12px;font-size:13px;font-weight:500;text-align:left;}}"
            f"QPushButton:hover{{background:#eef2ff;}}"
            f"QPushButton:pressed{{background:#e0e8ff;}}"
            f"QPushButton:disabled{{color:{C_DIVIDER};}}")
        b.clicked.connect(cb)
        return b

    def _slider(self, lbl, lo, hi, default, cb, icon_file=None):
        row = QHBoxLayout()
        row.setSpacing(8)
        if icon_file:
            _ic = QLabel()
            _ic.setPixmap(_ico(icon_file).pixmap(18, 18))
            row.addWidget(_ic)
        l = QLabel(lbl)
        l.setStyleSheet(f"color:{C_TEXT2};font-size:11px;background:transparent;min-width:24px;")
        sl = QSlider(Qt.Horizontal)
        sl.setRange(lo, hi)
        sl.setValue(default)
        sl.setFixedHeight(14)
        sl.setStyleSheet(
            f"QSlider::groove:horizontal{{height:4px;background:{C_DIVIDER};border-radius:2px;}}"
            f"QSlider::handle:horizontal{{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            f"stop:0 {C_BLUE},stop:1 {C_ACCENT});width:14px;height:14px;margin:-5px 0;"
            f"border-radius:7px;border:2px solid white;}}"
            f"QSlider::sub-page:horizontal{{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            f"stop:0 {C_BLUE},stop:1 {C_ACCENT});border-radius:2px;}}")
        sl.valueChanged.connect(cb)
        vl = QLabel(str(default))
        vl.setStyleSheet(f"color:{C_TEXT2};font-size:11px;background:transparent;min-width:22px;")
        sl.valueChanged.connect(lambda v, lb=vl: lb.setText(str(v)))
        row.addWidget(l)
        row.addWidget(sl, stretch=1)
        row.addWidget(vl)
        return row

    def _style_selector(self):
        root = QHBoxLayout()
        root.setSpacing(5)
        ic = QLabel()
        ic.setPixmap(_ico("心率风格.png").pixmap(16, 16))
        l = QLabel("风格")
        l.setStyleSheet(f"color:{C_TEXT2};font-size:11px;background:transparent;")
        root.addWidget(ic)
        root.addWidget(l)

        self._style_btn = QPushButton("红色经典 ▼")
        self._style_btn.setCursor(Qt.PointingHandCursor)
        self._style_btn.setStyleSheet(
            f"QPushButton{{background:transparent;color:{C_TITLE};border:none;border-radius:6px;"
            f"padding:6px 4px;font-size:13px;text-align:left;}}"
            f"QPushButton:hover{{background:#f0f1f8;}}")

        self._style_menu = QMenu(self._style_btn)
        self._style_menu.setStyleSheet(MENU_QSS)
        styles = [("红色经典", "红色经典.png"), ("绿色经典", "绿色经典.png"),
                  ("极简主义", "极简主义.png"), ("无畏契约", "无畏契约.png"),
                  ("csgo", "csgo.png"), ("小草神", "小草神.png")]
        for name, fname in styles:
            act = self._style_menu.addAction(name)
            p = os.path.join(HEART_STYLE_DIR, fname)
            act.setData(p)
            if os.path.exists(p):
                act.setIcon(QIcon(QPixmap(p).scaled(
                    20, 20, Qt.KeepAspectRatio, Qt.SmoothTransformation)))
                act.setIconVisibleInMenu(True)

        self._style_menu.triggered.connect(self._style_changed)
        self._style_btn.clicked.connect(
            lambda: self._style_menu.exec(
                self._style_btn.mapToGlobal(self._style_btn.rect().bottomLeft())))
        root.addWidget(self._style_btn, stretch=1)
        return root

    def _style_changed(self, act):
        path = act.data()
        self._style_btn.setText(act.text() + " ▼")
        if path:
            self.manager.set_float_style(path)

    def _color_selector(self):
        root = QHBoxLayout()
        root.setSpacing(5)
        ic = QLabel()
        ic.setPixmap(_ico("心率风格.png").pixmap(16, 16))
        l = QLabel("颜色")
        l.setStyleSheet(f"color:{C_TEXT2};font-size:11px;background:transparent;")
        root.addWidget(ic)
        root.addWidget(l)

        self._color_btn = QPushButton("红色 ▼")
        self._color_btn.setCursor(Qt.PointingHandCursor)
        self._color_btn.setStyleSheet(
            f"QPushButton{{background:transparent;color:{C_TITLE};border:none;border-radius:6px;"
            f"padding:6px 4px;font-size:13px;text-align:left;}}"
            f"QPushButton:hover{{background:#f0f1f8;}}")
        self._color_menu = QMenu(self._color_btn)
        self._color_menu.setStyleSheet(MENU_QSS)
        colors = [("红色", "#e8544a"), ("绿色", "#00b894"), ("紫色", "#a855f7"),
                  ("蓝色", "#0066ff"), ("黄色", "#f59e0b"), ("白色", "#ffffff"),
                  ("黑色", "#1a1a2e"), ("粉色", "#ec4899")]
        for name, hexc in colors:
            act = self._color_menu.addAction(name)
            act.setData(hexc)
            pix = QPixmap(16, 16)
            pix.fill(QColor(hexc))
            act.setIcon(QIcon(pix))
            act.setIconVisibleInMenu(True)
        self._color_menu.triggered.connect(self._color_changed)
        self._color_btn.clicked.connect(
            lambda: self._color_menu.exec(self._color_btn.mapToGlobal(self._color_btn.rect().bottomLeft())))
        root.addWidget(self._color_btn, stretch=1)
        return root

    def _cards(self):
        wrap = QFrame()
        wrap.setStyleSheet(f"background:{C_CARD};border:none;border-radius:14px;")
        wrap.setGraphicsEffect(shadow())
        row = QHBoxLayout(wrap)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        def _card(lbl, accent, icon_name, wide=False):
            f = QFrame()
            f.setStyleSheet("QFrame{background:transparent;border:none;}")
            v = QVBoxLayout(f)
            v.setContentsMargins(22, 18, 22, 18)
            v.setSpacing(6)
            _ic = QLabel()
            _ic.setPixmap(_ico(icon_name).pixmap(20, 20))
            l = QLabel(lbl)
            l.setStyleSheet(f"color:{C_TEXT2};font-size:11px;background:transparent;")
            _row = QHBoxLayout()
            _row.setContentsMargins(0, 0, 0, 0)
            _row.setSpacing(5)
            _row.addWidget(_ic)
            _row.addWidget(l)
            _row.addStretch()
            val = QLabel("--")
            val.setStyleSheet(
                f"color:{accent};font-size:{36 if wide else 26}px;font-weight:800;background:transparent;")
            v.addLayout(_row)
            v.addWidget(val)
            return f, val

        cur, self.v_hr = _card("当前心率", C_ACCENT, "当前心率.png", wide=True)
        cur.setMinimumWidth(200)
        self.pulse = PulseRing(cur)
        self.pulse.setGeometry(0, 0, 80, 80)

        self._card_mx, self.v_mx = _card("最大心率", C_TITLE, "最大心率.png")
        self._card_av, self.v_av = _card("平均心率", C_BLUE, "平均心率.png")

        row.addWidget(cur, 2)
        row.addWidget(self._card_mx, 1)
        row.addWidget(self._card_av, 1)
        return wrap

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, "pulse") and hasattr(self, "v_hr"):
            g = self.v_hr.geometry()
            self.pulse.setGeometry(g.right() - 60, g.top() - 10, 80, 80)

    # ── 回调 ──
    def _toggle_heart(self):
        if self.manager.float_visible():
            self.manager.hide_float()
            self.bh.setText("显示爱心")
        else:
            self.manager.show_float()
            self.bh.setText("隐藏爱心")

    def _color_changed(self, act):
        hexc = act.data()
        self._color_btn.setText(act.text() + " ▼")
        if hexc:
            self.manager.set_float_color(hexc)

    def _connect(self):
        if self.manager.is_connected():
            return
        a = self.lanya.currentData()
        if not a:
            self.s_txt.setText("请选择设备")
            return
        self.s_txt.setText("连接中...")
        self.manager.connect_device(a)

    def _con(self):
        self.conn_start = datetime.now()
        self.s_txt.setText("已连接")
        self.s_dot.setStyleSheet(f"color:{C_GREEN};font-size:7px;")
        self.bd.setEnabled(True)
        self.conn_timer.start(1000)
        self.lc.setText("连接时间  00:00")

    def _dis(self):
        self.conn_start = None
        self.s_txt.setText("未连接")
        self.s_dot.setStyleSheet("font-size:7px;color:#8e8ea0;")
        self.bd.setEnabled(False)
        self.conn_timer.stop()
        self.lc.setText("")

    def _tick(self):
        if self.conn_start:
            s = int((datetime.now() - self.conn_start).total_seconds())
            self.lc.setText(f"连接时间  {s // 60:02d}:{s % 60:02d}")

    def _found(self, n, a):
        self.lanya.addItem(n, a)

    def _sel(self):
        self.s_addr.setText(self.lanya.currentData() or "")

    def _hr(self, d):
        self.v_hr.setText(str(d))
        if d > self.max_hr:
            self.max_hr = d
        self.v_mx.setText(str(self.max_hr))
        self.hr_sum += d
        self.hr_cnt += 1
        self.v_av.setText(str(round(self.hr_sum / self.hr_cnt)))
        self.wave.append(d)

    def closeEvent(self, e):
        # 关窗口只隐藏,从宠物菜单可再打开;真正退出走宠物菜单
        e.ignore()
        self.hide()
