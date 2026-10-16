# -*- coding: utf-8 -*-
"""音乐窗口:歌词+进度条+播放列表(可换插画背景,简约风)。"""
from PySide6.QtCore import Qt, QTimer, QSize, QRectF
from PySide6.QtGui import QPixmap, QIcon, QPainter, QPainterPath, QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QFrame, QScrollArea, QPushButton,
    QHBoxLayout, QSlider, QListWidget, QListWidgetItem, QStackedWidget,
    QGraphicsDropShadowEffect,
)

from music_player import MusicPlayer

# 背景图文件名(放在 音乐素材/ 下,用户可自行替换成自己的图)
_BG_IMAGE = "背景-车窗少女.jpg"


def _icon_dir():
    """图标目录。

    不能用 __file__ 推算:打包后 __file__ 指向 _MEIPASS 里的临时文件,
    往上两级就不是项目根目录了,图标会整个丢失。优先 exe 同目录,再退回打包资源。
    """
    from paths import app_dir, resource_dir
    d = app_dir() / "图标"
    return str(d if d.is_dir() else resource_dir() / "图标")


def _asset_dir():
    """音乐素材目录(装饰图 / 默认封面)。

    和 _icon_dir 同理:打包后素材在 _MEIPASS 里,只找 exe 同目录的话
    默认封面永远加载不到,只能显示 ♫ 占位符。
    """
    from paths import app_dir, resource_dir
    d = app_dir() / "音乐素材"
    return str(d if d.is_dir() else resource_dir() / "音乐素材")


class LyricWindow(QFrame):
    """音乐窗口。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("音乐")
        # 设置窗口图标
        from PySide6.QtGui import QIcon
        import os
        icon_path = os.path.join(_icon_dir(), "程序.ico")
        self.setWindowIcon(QIcon(icon_path))
        self.setWindowFlags(Qt.WindowStaysOnTopHint)
        self.resize(400, 640)
        self.setObjectName("lyricRoot")
        # 粉蓝渐变精致主题:与用量窗口同款三色渐变
        self.setStyleSheet("""
            QFrame#lyricRoot {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #fff5f9, stop:0.5 #fdf3ff, stop:1 #f0f6ff);
                border-radius: 22px;
                border: 1.5px solid #eeddf5;
                font-family: "Microsoft YaHei";
            }
            QLabel {
                background: transparent;
                color: #5a4a6a;
            }
            QListWidget {
                background: rgba(255,255,255,0.92);
                border: 1.5px solid #eeddf5;
                border-radius: 14px;
                padding: 6px;
                color: #5a4a6a;
                font-size: 13px;
                outline: none;
            }
            QListWidget::item {
                padding: 10px 14px;
                margin: 2px 6px;
                border-radius: 10px;
            }
            QListWidget::item:selected {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #ff7fa8, stop:1 #9ec7ff);
                color: #fff;
            }
            QListWidget::item:hover {
                background: #ffe4ef;
            }
            QScrollBar:vertical {
                background: transparent; width: 8px; margin: 4px;
            }
            QScrollBar::handle:vertical {
                background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #ffb8d4, stop:1 #b8d0ff);
                border-radius: 4px; min-height: 30px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
        """)

        self._lyrics = []
        self._current_idx = -1
        self._seeking = False
        self._show_playlist = False

        # 背景插画(音乐素材/背景-车窗少女.jpg): 加载失败则保持原渐变主题
        self._bg_pixmap = None
        try:
            bg_path = os.path.join(_asset_dir(), _BG_IMAGE)
            if os.path.isfile(bg_path):
                pm = QPixmap(bg_path)
                if not pm.isNull():
                    self._bg_pixmap = pm
        except Exception:
            self._bg_pixmap = None

        self._setup_ui()
        self._load_playlist()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update)
        self._timer.start(300)

        mp = MusicPlayer.get()
        mp.state_changed.connect(self._on_state_changed)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # 歌名(加一点字母间距更精致)
        self._title = QLabel("未播放")
        self._title.setStyleSheet("color:#6a4a7a; font-size:18px; font-weight:700;")
        self._title.setAlignment(Qt.AlignCenter)
        self._title.setWordWrap(True)
        letter_line = QLabel("✦ ✦ ✦")
        letter_line.setStyleSheet("color:#c8a6ff; font-size:10px; letter-spacing:6px;")
        letter_line.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._title)
        layout.addWidget(letter_line)

        # 歌词区
        self._lyric_container = QWidget()
        self._lyric_container.setStyleSheet("background:transparent;")
        self._lyric_layout = QVBoxLayout(self._lyric_container)
        self._lyric_layout.setSpacing(16)
        self._lyric_layout.setAlignment(Qt.AlignTop)

        lyric_scroll = QScrollArea()
        lyric_scroll.setWidget(self._lyric_container)
        lyric_scroll.setWidgetResizable(True)
        lyric_scroll.setStyleSheet(
            "border:none; background:transparent; border-radius:16px;")
        lyric_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        layout.addWidget(lyric_scroll, 1)
        self._lyric_scroll = lyric_scroll  # 存成成员变量,用于滚动定位

        # 播放列表(隐藏)
        self._playlist_widget = QListWidget()
        self._playlist_widget.itemDoubleClicked.connect(self._on_song_selected)
        self._playlist_widget.hide()
        layout.addWidget(self._playlist_widget, 1)

        # 进度条
        self._progress = QSlider(Qt.Horizontal)
        self._progress.setRange(0, 1000)
        self._progress.setValue(0)
        self._progress.setStyleSheet("""
            QSlider::groove:horizontal {
                height: 6px;
                background: #f0e4f2;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                width: 16px;
                height: 16px;
                margin: -5px 0;
                background: #fff;
                border: 3px solid #ff6b9d;
                border-radius: 8px;
            }
            QSlider::handle:horizontal:hover {
                border: 3px solid #ff4f92;
                background: #fff0f6;
            }
            QSlider::sub-page:horizontal {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #ff9ec4, stop:0.5 #c3a6ff, stop:1 #8ec5ff);
                border-radius: 3px;
            }
        """)
        self._progress.sliderPressed.connect(lambda: setattr(self, '_seeking', True))
        self._progress.sliderReleased.connect(self._on_seek)
        layout.addWidget(self._progress)

        # 时间行
        time_row = QHBoxLayout()
        self._cur_time = QLabel("00:00")
        self._total_time = QLabel("00:00")
        for t in (self._cur_time, self._total_time):
            t.setStyleSheet(
                "background:transparent; color:#b0608a;"
                "padding: 2px 10px; font-size: 11px; font-weight: 600;"
            )
        time_row.addWidget(self._cur_time)
        time_row.addStretch()
        time_row.addWidget(self._total_time)
        layout.addLayout(time_row)

        # 按钮行:纯白色文字符号(图标PNG自带白底,在深色背景上突兀,改用无底色符号)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(20)

        self._list_btn = QPushButton("≡")
        self._prev_btn = QPushButton("◀◀")
        self._play_btn = QPushButton("▶")
        self._next_btn = QPushButton("▶▶")

        # 极简按钮: 透明背景 + 白色符号, 只有悬停时一圈淡淡的白
        _plain_btn_style = """
            QPushButton {
                border: none;
                background: transparent;
                color: rgba(255,255,255,0.88);
                font-family: "Segoe UI Symbol";
                font-weight: 600;
            }
            QPushButton:hover {
                background: rgba(255,255,255,0.2);
                border-radius: 50%;
                color: #ffffff;
            }
            QPushButton:pressed {
                background: rgba(255,255,255,0.4);
                border-radius: 50%;
                color: #ffffff;
            }
        """
        self._list_btn.setStyleSheet(_plain_btn_style)
        self._list_btn.setStyleSheet(_plain_btn_style + "QPushButton { font-size: 20px; }")
        for btn in (self._prev_btn, self._next_btn):
            btn.setStyleSheet(_plain_btn_style + "QPushButton { font-size: 18px; }")
        self._play_btn.setStyleSheet(_plain_btn_style + "QPushButton { font-size: 26px; }")

        self._list_btn.setFixedSize(40, 40)
        self._list_btn.clicked.connect(self._toggle_playlist)

        # 上一首/下一首
        for btn in (self._prev_btn, self._next_btn):
            btn.setFixedSize(46, 46)

        # 播放/暂停
        self._play_btn.setFixedSize(58, 58)

        self._prev_btn.clicked.connect(self._on_prev)
        self._play_btn.clicked.connect(self._on_play_pause)
        self._next_btn.clicked.connect(self._on_next)

        btn_row.addStretch()
        btn_row.addWidget(self._list_btn)
        btn_row.addWidget(self._prev_btn)
        btn_row.addWidget(self._play_btn)
        btn_row.addWidget(self._next_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

    def _toggle_playlist(self):
        """切换显示歌词/播放列表。"""
        self._show_playlist = not self._show_playlist
        if self._show_playlist:
            self._lyric_container.parent().hide()
            self._playlist_widget.show()
        else:
            self._playlist_widget.hide()
            self._lyric_container.parent().show()

    def _load_playlist(self):
        mp = MusicPlayer.get()
        mp.scan_music()
        self._playlist_widget.clear()
        for path in mp._playlist:
            import os
            name = os.path.splitext(os.path.basename(path))[0]
            item = QListWidgetItem(name)
            self._playlist_widget.addItem(item)

    def _on_song_selected(self, item):
        idx = self._playlist_widget.row(item)
        mp = MusicPlayer.get()
        mp._current_idx = idx
        mp._load_and_play()

    def set_lyrics(self, lyrics, title=""):
        self._lyrics = lyrics
        self._lyric_labels = []
        if title:
            self._title.setText(title)
        while self._lyric_layout.count():
            item = self._lyric_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for _, text in lyrics:
            lbl = QLabel(text)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setWordWrap(True)
            lbl.setStyleSheet("color:#9a8a9a; font-size:13px; padding:10px 12px; border-radius:10px;")
            self._lyric_layout.addWidget(lbl)
            self._lyric_labels.append(lbl)

    def _on_prev(self):
        mp = MusicPlayer.get()
        mp.prev_song()
        self._title.setText(mp.current_song_name)
        self.set_lyrics(mp._lyrics, mp.current_song_name)

    def _on_play_pause(self):
        mp = MusicPlayer.get()
        mp.play_pause()

    def _on_next(self):
        mp = MusicPlayer.get()
        mp.next_song()
        self._title.setText(mp.current_song_name)
        self.set_lyrics(mp._lyrics, mp.current_song_name)

    def _on_state_changed(self, playing):
        if playing:
            self._play_btn.setText("❚❚")
        else:
            self._play_btn.setText("▶")
        mp = MusicPlayer.get()
        if mp.current_song_name:
            self._title.setText(mp.current_song_name)
            self.set_lyrics(mp._lyrics, mp.current_song_name)

    def _on_seek(self):
        self._seeking = False
        mp = MusicPlayer.get()
        length = mp.get_length()
        if length > 0:
            ratio = self._progress.value() / 1000.0
            new_pos = ratio * length
            mp.seek(new_pos)   # get_pos() 现在返回绝对位置,无需再屏蔽定时器
            # 立即刷新一次当前歌词高亮
            self._update_lyrics_at(new_pos)

    def _update_lyrics_at(self, pos):
        """根据播放位置更新歌词高亮。"""
        if not self._lyrics:
            return
        current_idx = -1
        for i, (t, _) in enumerate(self._lyrics):
            if t <= pos:
                current_idx = i
            else:
                break

        if current_idx == self._current_idx:
            return
        self._current_idx = current_idx
        # 更新高亮
        for i, lbl in enumerate(self._lyric_labels):
            if i == current_idx:
                lbl.setStyleSheet("""
                    color:#ff4f92; font-size:15px; font-weight:bold;
                    background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #ffe8f1, stop:1 #eaf0ff);
                    padding:10px 12px; border-radius:12px;
                """)
            else:
                lbl.setStyleSheet("color:#9a8a9a; font-size:13px; padding:10px 12px; border-radius:10px;")

    def _format_time(self, sec):
        m = int(sec // 60)
        s = int(sec % 60)
        return f"{m:02d}:{s:02d}"

    def _update(self):
        mp = MusicPlayer.get()
        if not mp.is_playing and not mp._paused:
            return

        pos = mp.get_pos()   # 绝对位置,seek 之后仍然准确
        length = mp.get_length()

        # 拖动进度条时,进度值由用户控制,不覆盖;其余时刻正常刷新
        if length > 0 and not self._seeking:
            self._progress.setValue(int(pos / length * 1000))
            self._cur_time.setText(self._format_time(pos))
            self._total_time.setText(self._format_time(length))
        elif length > 0:
            self._total_time.setText(self._format_time(length))

        if not self._lyrics:
            return
        current_idx = -1
        for i, (t, _) in enumerate(self._lyrics):
            if t <= pos:
                current_idx = i
            else:
                break

        if current_idx == self._current_idx:
            return
        self._current_idx = current_idx

        for i in range(self._lyric_layout.count()):
            lbl = self._lyric_layout.itemAt(i).widget()
            if i == current_idx:
                lbl.setStyleSheet("""
                    color:#ff4f92; font-size:15px; font-weight:600;
                    background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #ffe8f1, stop:1 #eaf0ff);
                    padding:10px 12px; border-radius:12px;
                """)
            else:
                lbl.setStyleSheet("color:#9a8a9a; font-size:13px; padding:10px 12px; border-radius:10px;")

        if current_idx >= 0 and current_idx < len(self._lyric_labels):
            # 滚动到当前歌词
            lbl = self._lyric_labels[current_idx]
            self._lyric_scroll.ensureWidgetVisible(lbl)

    # ── 插画背景 ──
    def paintEvent(self, event):
        """有背景图时画插画铺满窗口(圆角裁剪 + 等比放大居中,不叠遮罩)。"""
        if self._bg_pixmap is None or self._bg_pixmap.isNull():
            super().paintEvent(event)
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 22, 22)
        p.setClipPath(path)
        w, h = self.width(), self.height()
        scaled = self._bg_pixmap.scaled(
            w, h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        x = (w - scaled.width()) // 2
        y = (h - scaled.height()) // 2
        p.drawPixmap(x, y, scaled)
        p.end()
