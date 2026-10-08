"""
桌面宠物音乐播放器
扫描 exe 同目录/音乐/ 下所有子文件夹的音乐文件,用 pygame.mixer 播放。
"""
import os
import re
import random
import pygame
from PySide6.QtCore import QObject, QTimer, Signal
from paths import app_dir

MUSIC_DIR = str(app_dir() / "音乐")
SUPPORTED_EXT = (".mp3", ".flac", ".wav", ".ogg", ".m4a", ".aac")

# 全局只初始化一次 mixer
_mixer_inited = False


def _ensure_mixer():
    global _mixer_inited
    if not _mixer_inited:
        try:
            pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=4096)
            _mixer_inited = True
        except Exception:
            pass


class MusicPlayer(QObject):
    """音乐播放器单例。"""

    song_changed = Signal(str)   # 歌曲名变化
    state_changed = Signal(bool) # 播放状态变化(True=播放中)
    lyric_changed = Signal(str)  # 歌词变化

    _instance = None

    @classmethod
    def get(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        super().__init__()
        _ensure_mixer()
        self._playlist = []
        self._current_idx = -1
        self._shuffle = False
        self._playing = False
        self._paused = False
        self._volume = 0.7
        self._lyrics = []          # [(time_sec, text), ...]
        self._current_lyric = ""   # 当前显示的歌词
        self._base_offset = 0.0    # 本次 play() 开始时的绝对播放位置(秒)
        self._song_length = None   # 当前歌曲时长缓存(避免每 tick 重开文件解析)

        # 轮询播放结束(每 800ms 检查一次)
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._check_end)
        self._poll_timer.start(800)

        if _mixer_inited:
            pygame.mixer.music.set_volume(self._volume)

        self.scan_music()

    # ── 扫描音乐 ──
    def scan_music(self):
        self._playlist = []
        try:
            os.makedirs(MUSIC_DIR, exist_ok=True)
        except OSError as e:
            # 程序放在只读目录时不要让音乐窗口直接炸掉,当成空歌单处理
            print(f"[音乐] 无法创建音乐目录 {MUSIC_DIR}: {e}")
            return
        for root, dirs, files in os.walk(MUSIC_DIR):
            for f in files:
                if f.lower().endswith(SUPPORTED_EXT):
                    self._playlist.append(os.path.join(root, f))
        self._playlist.sort()

    @property
    def playlist(self):
        return self._playlist

    @property
    def current_song_name(self):
        if 0 <= self._current_idx < len(self._playlist):
            return os.path.splitext(os.path.basename(self._playlist[self._current_idx]))[0]
        return ""

    @property
    def is_playing(self):
        return self._playing

    @property
    def volume(self):
        return self._volume

    @property
    def shuffle(self):
        return self._shuffle

    # ── 播放控制 ──
    def play_pause(self):
        if not _mixer_inited:
            _ensure_mixer()
            if not _mixer_inited:
                return None
        # 首次播放或停止后再播放:重新扫描音乐库,新加的歌不用重启
        if self._current_idx < 0:
            self.scan_music()
        if not self._playlist:
            return None

        if self._current_idx < 0:
            # 首次播放
            self._current_idx = 0
            self._load_and_play()
        elif self._paused:
            # 从暂停恢复
            pygame.mixer.music.unpause()
            self._paused = False
            self._playing = True
            self.state_changed.emit(True)
        elif self._playing:
            # 暂停
            pygame.mixer.music.pause()
            self._paused = True
            self._playing = False
            self.state_changed.emit(False)
        else:
            # 停止状态,重新播放当前歌曲
            self._load_and_play()

        return self.current_song_name

    def next_song(self):
        if not self._playlist:
            return None
        if self._shuffle:
            self._current_idx = random.randint(0, len(self._playlist) - 1)
        else:
            self._current_idx = (self._current_idx + 1) % len(self._playlist)
        self._load_and_play()
        return self.current_song_name

    def prev_song(self):
        if not self._playlist:
            return None
        if self._shuffle:
            self._current_idx = random.randint(0, len(self._playlist) - 1)
        else:
            self._current_idx = (self._current_idx - 1) % len(self._playlist)
        self._load_and_play()
        return self.current_song_name

    def stop(self):
        if _mixer_inited:
            pygame.mixer.music.stop()
        self._playing = False
        self._paused = False
        self._current_idx = -1  # 重置,下次播放重新扫描音乐库
        self._song_length = None
        self.state_changed.emit(False)

    def get_pos(self):
        """当前播放位置(秒)。

        pygame 的 get_pos() 返回的是距上次 play() 的相对时间,并不包含
        play(start=...) 的偏移。因此 seek 之后必须加上 _base_offset 才是
        歌曲里的绝对位置,否则歌词/进度条会与真实播放不同步。
        """
        if not self._playing and not self._paused:
            return 0
        try:
            rel = pygame.mixer.music.get_pos() / 1000.0
            return max(0, self._base_offset + rel)
        except Exception:
            return self._base_offset

    def get_length(self):
        """当前歌曲总时长(秒)。缓存到切歌,避免定时器每 tick 重开文件做磁盘 I/O。"""
        if self._song_length is not None:
            return self._song_length
        if not (0 <= self._current_idx < len(self._playlist)):
            return 0
        self._song_length = self._read_length(self._playlist[self._current_idx])
        return self._song_length

    def _read_length(self, path):
        """通用时长解析:支持 mp3/flac/ogg/m4a/aac/wav(原实现只认 MP3)。"""
        try:
            from mutagen import File as MFile
            audio = MFile(path)
            if audio is not None and audio.info is not None:
                return max(0, audio.info.length)
        except Exception:
            pass
        try:
            import wave
            with wave.open(path, 'rb') as wf:
                return wf.getnframes() / wf.getframerate()
        except Exception:
            return 0

    def seek(self, pos_sec):
        """跳到指定位置(秒)。"""
        if not (0 <= self._current_idx < len(self._playlist)):
            return
        path = self._playlist[self._current_idx]
        try:
            pygame.mixer.music.load(path)
            self._base_offset = pos_sec   # 记录 seek 起点,让 get_pos() 返回绝对位置
            pygame.mixer.music.play(start=pos_sec)
            self._paused = False
            self._playing = True
            self.state_changed.emit(True)
            # seek 后手动更新气泡歌词
            if self._lyrics:
                lyric = self._get_lyric_at(pos_sec)
                if lyric and lyric != self._current_lyric:
                    self._current_lyric = lyric
                    self.lyric_changed.emit(lyric)
        except Exception:
            pass

    def set_volume(self, vol):
        self._volume = max(0.0, min(1.0, vol))
        if _mixer_inited:
            pygame.mixer.music.set_volume(self._volume)

    def volume_up(self, step=0.1):
        self.set_volume(min(1.0, self._volume + step))
        return self._volume

    def volume_down(self, step=0.1):
        self.set_volume(max(0.0, self._volume - step))
        return self._volume

    def toggle_shuffle(self):
        self._shuffle = not self._shuffle
        return self._shuffle

    # ── 内部 ──
    def _load_and_play(self):
        if 0 <= self._current_idx < len(self._playlist) and _mixer_inited:
            path = self._playlist[self._current_idx]
            try:
                pygame.mixer.music.load(path)
                self._base_offset = 0.0   # 新歌从头播放,绝对位置即相对位置
                self._song_length = None  # 切歌后失效,下次 get_length 再缓存
                pygame.mixer.music.play()
                self._playing = True
                self._paused = False
                self._lyrics = self._load_lrc(path)
                self._current_lyric = ""
                self.song_changed.emit(self.current_song_name)
                self.state_changed.emit(True)
            except Exception as e:
                print(f"播放失败 {path}: {e}")

    def _load_lrc(self, music_path):
        """解析同名 lrc 歌词文件(支持标准lrc和网易云JSON格式)。"""
        lrc_path = os.path.splitext(music_path)[0] + ".lrc"
        if not os.path.isfile(lrc_path):
            return []
        lyrics = []
        pattern = re.compile(r'\[(\d+):(\d+)[.:](\d+)\](.*)')
        try:
            with open(lrc_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    # 网易云JSON格式歌词
                    if line.startswith('{') and '"t"' in line:
                        try:
                            import json
                            data = json.loads(line)
                            t = data.get('t', 0) / 1000.0  # 毫秒转秒
                            text = ''.join(c.get('tx', '') for c in data.get('c', []))
                            if text.strip():
                                lyrics.append((t, text.strip()))
                        except Exception:
                            pass
                        continue
                    # 标准lrc格式
                    for m in pattern.finditer(line):
                        minutes = int(m.group(1))
                        seconds = int(m.group(2))
                        frac = m.group(3)
                        # LRC 时间戳有 [mm:ss.xx] 和 [mm:ss.xxx] 两种写法,
                        # 必须按小数位数换算,不能按数值大小猜:
                        # 049 是 49 毫秒(0.049 秒),不是 0.49 秒 —— 否则歌词会晚 0.9 秒。
                        time_sec = minutes * 60 + seconds + int(frac) / (10 ** len(frac))
                        text = m.group(4).strip()
                        if text:
                            lyrics.append((time_sec, text))
            lyrics.sort(key=lambda x: x[0])
            # 去重(同一时间只留一条)
            deduped = []
            last_t = -1
            for t, text in lyrics:
                if abs(t - last_t) > 0.01:
                    deduped.append((t, text))
                    last_t = t
            lyrics = deduped
        except Exception:
            pass
        return lyrics

    def _get_lyric_at(self, pos_sec):
        """获取当前时间对应的歌词。"""
        if not self._lyrics:
            return ""
        current = ""
        for time_sec, text in self._lyrics:
            if pos_sec >= time_sec:
                current = text
            else:
                break
        return current

    def _check_end(self):
        """轮询检测播放结束和歌词变化。"""
        if not _mixer_inited or not self._playing or self._paused:
            return
        if not pygame.mixer.music.get_busy():
            if self._playlist:
                self.next_song()
            return
        # 检查歌词变化(用绝对位置,否则 seek 之后会与音频不同步)
        if self._lyrics:
            pos = self.get_pos()
            lyric = self._get_lyric_at(pos)
            if lyric and lyric != self._current_lyric:
                self._current_lyric = lyric
                self.lyric_changed.emit(lyric)
