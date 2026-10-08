# -*- coding: utf-8 -*-
"""屏幕录制模块:心率过高时自动录屏。

抓帧用 dxcam(DXGI 桌面复制,GPU 硬件加速,游戏全屏也能稳定高帧率),
编码用 FFmpeg + NVENC 硬件加速,音频从虚拟声卡(默认 VB-Audio Virtual Cable)录系统声音。

启动失败一律写进 `last_error`,由 UI 弹气泡 —— 以前只 print,打包版没控制台,
用户点「开始录屏」什么都看不到,以为是没反应。
"""
import os
import subprocess
import threading
import time
from datetime import datetime

import imageio_ffmpeg

from paths import app_dir

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
RECORD_DIR = app_dir() / "recordings"
# 这行是在 import 时执行的,而 pet.py 启动时就会 import 本模块。
# 如果程序被放在只读目录(例如 C:\Program Files),mkdir 抛异常会让桌宠直接起不来,
# 所以这里必须容错:真正的录屏失败留到 start() 里再报。
try:
    RECORD_DIR.mkdir(parents=True, exist_ok=True)
except OSError as e:
    print(f"[录屏] 无法创建输出目录 {RECORD_DIR}: {e}")

# 最低录屏时长(秒)
MIN_DURATION = 45
# 最长录屏时长(秒)——防心率持续过高时把磁盘录满
MAX_DURATION = 3600
# 启动失败后的冷却重试间隔(秒)。以前是 60 秒,而且冷却期内**静默**返回 None,
# 用户连点几次都是「没反应」;现在缩短到 15 秒,并且一定会给出提示。
START_FAIL_COOLDOWN = 15
# 录屏目标帧率(dxcam 实际能力远超此值,限流用)
FPS = 60
# ffmpeg 启动后等多久判断它是不是立刻就死了(秒)。
# 别调太大:这个等待是在点「开始录屏」时同步发生的,太久会让桌宠卡住。
STARTUP_PROBE = 0.6

# 默认录屏音频设备(VB-Audio 虚拟声卡,录系统声音)。
# 可以在 settings.json 的 intervals.record_audio_device 里改;
# 填 "" 或 "none" 就只录画面不录声音。
AUDIO_DEVICE = "CABLE Output (VB-Audio Virtual Cable)"

# ffmpeg 的 stderr 落在这里(以前丢进 DEVNULL,出错完全查不到原因)
FFMPEG_LOG = RECORD_DIR / "ffmpeg.log"
_LOG_TAIL_BYTES = 8192


def _audio_device():
    """从设置读录音设备;读不到就用默认值。返回 "" 表示不录音。"""
    try:
        from settings_window import load_settings
        v = (load_settings().get("intervals") or {}).get("record_audio_device")
        if v is None:
            return AUDIO_DEVICE
        v = str(v).strip()
        if v.lower() in ("", "none", "off", "false", "no"):
            return ""
        return v
    except Exception:
        return AUDIO_DEVICE


def _tail(path, n=_LOG_TAIL_BYTES):
    """读日志文件末尾 n 字节,用来把 ffmpeg 的报错带给用户。"""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > n:
                f.seek(size - n)
            return f.read().decode("utf-8", "replace").strip()
    except Exception:
        return ""


# dshow 设备清单只列一次,缓存起来。
# 为什么需要:设备名写错时 ffmpeg 不是立刻退出,而是先枚举设备、过一会儿才报错,
# 那个延迟比启动探测窗口长得多,光靠探测窗口抓不住。
_devices_cache = None


def _device_available(name):
    """名字在不在 ffmpeg 的 dshow 设备清单里。第一次调用才真的去列。"""
    global _devices_cache
    if _devices_cache is None:
        try:
            r = subprocess.run(
                [FFMPEG, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
                capture_output=True, text=True, errors="replace", timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            _devices_cache = (r.stderr or "") + (r.stdout or "")
        except Exception:
            _devices_cache = ""   # 列不出来就先放行,别把功能直接掐死
    if not _devices_cache:
        return True
    return name.lower() in _devices_cache.lower()


def _log_backend():
    """把录屏后端状态写一行日志。

    打包版没有控制台,这行会进 boringpet.log。
    录屏依赖 dxcam 的 numpy 内核,而这个内核是动态导入的 ——
    万一打包时没带上,dxcam 会悄悄退回需要 cv2 的后端,录屏就会失败。
    启动时先看一眼,出问题的时候一眼就能定位。
    """
    try:
        from importlib import import_module
        try:
            import_module("dxcam.processor._numpy_kernels")
            kernel = "ok"
        except Exception as e:
            kernel = "MISSING(%s: %s)" % (type(e).__name__, e)
    except Exception as e:
        kernel = "unknown(%s)" % e
    try:
        print(f"[录屏] dxcam numpy 内核: {kernel}")
    except Exception:
        pass
    return kernel


def prewarm():
    """后台做两件小事:查 dshow 设备清单 + 记录录屏后端状态。

    列设备要跑一次 ffmpeg(大约 1 秒)。放在启动时后台做掉,
    第一次点「开始录屏」就不会卡那一下了。
    """
    def _work():
        _log_backend()
        _device_available(_audio_device())

    threading.Thread(target=_work, daemon=True).start()


def _create_camera():
    """建 dxcam 摄像头,**显式指定 numpy 后端**。

    为什么必须指定:dxcam 默认走 cv2 后端,而 cv2 是它在内部用
    `import_module("cv2")` **动态**导入的 —— PyInstaller 的静态分析看不见这种导入,
    所以打包版里没有 cv2,默认后端一 grab 就抛 ImportError(用户看到的就是
    「录屏失败:没有 cv2 模块」)。

    numpy 后端走的是 `dxcam.processor._numpy_kernels` 这个 Cython 内核
    (spec 里已经手动打进包了),完全不需要 cv2;而且比把整个 opencv
    塞进安装包省 60~100 MB。实测两个后端抓到的帧数据一致。
    """
    import dxcam
    try:
        return dxcam.create(output_color="BGR", processor_backend="numpy")
    except TypeError:
        # 老版本 dxcam 没有 processor_backend 参数,退回默认行为
        return dxcam.create(output_color="BGR")


class ScreenRecorder:
    """dxcam 抓帧 + FFmpeg NVENC 硬件编码。"""

    # 类属性:外部是按实例访问的(pet.py 里写的 rec.MAX_DURATION),
    # 只有模块级常量的话会 AttributeError。
    MIN_DURATION = MIN_DURATION
    MAX_DURATION = MAX_DURATION

    def __init__(self):
        self._proc = None
        self._thread = None
        self._stop_flag = None
        self._cam = None
        self._output_path = None
        self._log_file = None
        self._size = None       # (w, h),中途换 ffmpeg 时重建命令要用
        self.start_time = None
        self._last_fail = 0.0   # 上次启动失败时间(用于冷却重试)
        self._seq = 0           # 文件名自增,避免同秒内多段录制重名
        self.last_error = ""    # 给 UI 看的失败原因
        self.last_mode = ""     # "audio" / "video-only"

    @property
    def is_recording(self):
        return self._thread is not None and self._thread.is_alive()

    @property
    def elapsed(self):
        """已录制时长(秒)。"""
        if self.start_time is None:
            return 0
        return time.time() - self.start_time

    # ---------------------------------------------------------------- 内部
    def _build_cmd(self, w, h, out_path, device, fps):
        cmd = [
            FFMPEG,
            "-f", "rawvideo",
            "-pix_fmt", "bgr24",
            "-s", f"{w}x{h}",
            "-framerate", str(fps),
            # 关键:按「到达时间」给帧打时间戳。
            # 不加这个的话,Python 这边写管道的速度往往达不到 60fps,
            # ffmpeg 仍按 60fps 解释,录出来的视频就会变成快进/卡顿。
            "-use_wallclock_as_timestamps", "1",
            "-i", "-",                # 视频从 stdin
        ]
        if device:
            cmd += [
                "-f", "dshow",
                "-i", f"audio={device}",   # 录系统声音
            ]
        cmd += [
            "-c:v", "h264_nvenc",
            "-preset", "p1",          # 最快编码
            "-rc", "cbr",             # 固定码率,更稳定
            "-b:v", "15M",            # 15Mbps 码率
        ]
        if device:
            cmd += ["-c:a", "aac", "-b:a", "128k"]
        cmd += [
            "-pix_fmt", "yuv420p",
            "-shortest",       # 视频结束即收尾,不等音频
            "-y",
            out_path,
        ]
        return cmd

    def _probe_start(self, cmd):
        """启动 ffmpeg,等一小会看它是不是立刻就退出了。

        返回 (ok, 错误摘要)。以前不做这一步:Popen 成功就以为录屏开始了,
        结果 ffmpeg 秒退,抓帧线程随即死掉,UI 却显示正在录 —— 用户完全蒙在鼓里。
        """
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            log = open(FFMPEG_LOG, "ab", buffering=0)
        except Exception:
            log = None
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=log if log else subprocess.DEVNULL,
                stderr=log if log else subprocess.DEVNULL,
                creationflags=creationflags,
            )
        except Exception as e:
            if log:
                try:
                    log.close()
                except Exception:
                    pass
            return None, f"启动 ffmpeg 失败: {e}", None

        time.sleep(STARTUP_PROBE)
        rc = proc.poll()
        if rc is not None:
            # 秒退 = 参数/设备有问题,把 ffmpeg 的报错捞出来给用户看
            if log:
                try:
                    log.close()
                except Exception:
                    pass
            return None, self._explain(rc), None
        return proc, "", log

    @staticmethod
    def _explain(rc):
        """把 ffmpeg 的 stderr 末尾转成一句人话。"""
        tail = _tail(FFMPEG_LOG)
        low = tail.lower()
        if "could not find" in low or "no such" in low or "unknown device" in low:
            hint = "音频设备找不到(虚拟声卡没装,或名字对不上)"
        elif "already in use" in low or "busy" in low:
            hint = "音频设备被别的程序占用了"
        elif "nvenc" in low and ("not found" in low or "cannot load" in low
                                 or "no capable devices" in low):
            hint = "显卡不支持 NVENC 编码(需要 N 卡)"
        elif "permission" in low or "access is denied" in low:
            hint = "没有权限"
        else:
            hint = "ffmpeg 启动后立刻退出"
        return "%s(ffmpeg 退出码 %s)" % (hint, rc)

    # ---------------------------------------------------------------- 对外
    def start(self):
        """开始录屏。成功返回输出路径;失败返回 None,并把原因写进 last_error。"""
        self.last_error = ""
        if self.is_recording:
            self.last_error = "已经在录屏了"
            return None
        if self._last_fail and time.time() - self._last_fail < START_FAIL_COOLDOWN:
            left = START_FAIL_COOLDOWN - (time.time() - self._last_fail)
            self.last_error = "刚刚启动失败过,%.0f 秒后再试" % left
            return None

        # 1) dxcam 初始化 + 抓首帧
        cam = None
        try:
            cam = _create_camera()
            if cam is None:
                raise RuntimeError("dxcam.create() 返回 None(显卡驱动/DXGI 不可用)")
            frame = None
            # 桌面静止时头几帧可能是 None(没有新帧),不能一次判死
            for _ in range(60):
                frame = cam.grab()
                if frame is not None:
                    break
                time.sleep(0.02)
            if frame is None:
                raise RuntimeError("连续 60 次都抓不到画面")
            h, w = frame.shape[:2]
        except Exception as e:
            if cam is not None:
                try:
                    cam.release()
                except Exception:
                    pass
            self._last_fail = time.time()
            self.last_error = "抓屏初始化失败: %s" % e
            print("[录屏] " + self.last_error)
            return None

        self._seq += 1
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = str(RECORD_DIR / f"recording_{timestamp}_{self._seq}.mp4")

        # 2) 先带音频试;音频起不来就退回只录画面(至少还能录下来)
        device = _audio_device()
        if device and not _device_available(device):
            print(f"[录屏] 音频设备「{device}」不在 ffmpeg 的设备列表里,改为只录画面")
            device = ""
        attempts = []
        if device:
            attempts.append(("audio", device))
        attempts.append(("video-only", ""))

        proc = log = None
        last_msg = ""
        used_mode = ""
        for mode, dev in attempts:
            cmd = self._build_cmd(w, h, out_path, dev, FPS)
            proc, last_msg, log = self._probe_start(cmd)
            if proc is not None:
                used_mode = mode
                break
            if mode == "audio":
                print(f"[录屏] 带音频启动失败,改试只录画面: {last_msg}")

        if proc is None:
            try:
                cam.release()
            except Exception:
                pass
            self._last_fail = time.time()
            self.last_error = last_msg or "ffmpeg 启动失败"
            print("[录屏] " + self.last_error)
            return None

        if used_mode == "video-only" and device:
            self.last_error = ""   # 成功了就不算错误
            print("[录屏] 已改为只录画面(音频设备不可用)")

        self._proc = proc
        self._log_file = log
        self._cam = cam
        self._size = (w, h)
        self._stop_flag = threading.Event()
        self.last_mode = used_mode
        self._output_path = out_path
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        self.start_time = time.time()
        return out_path

    def _capture_loop(self):
        """后台抓帧线程:dxcam 抓屏 -> 写 ffmpeg stdin。"""
        cam = self._cam
        proc = self._proc
        mode = self.last_mode
        retried = False
        died_early = False
        try:
            frame_interval = 1.0 / FPS
            last_t = time.time()
            while not self._stop_flag.is_set():
                frame = cam.grab()
                if frame is None:
                    # 无新帧时短暂让出,同时让 stop 有机会设置标志
                    if self._stop_flag.wait(0.005):
                        break
                    continue
                now = time.time()
                if now - last_t >= frame_interval:
                    last_t = now
                    try:
                        proc.stdin.write(frame.tobytes())
                        proc.stdin.flush()
                    except Exception:
                        # 管道断了 = ffmpeg 退出了。
                        # 如果刚才在用音频,而且录得还不久,就悄悄换成只录画面重来一次:
                        # 用户至少能拿到一段录像,而不是「点了没用」。
                        if mode == "audio" and not retried and self._stop_flag is not None \
                                and not self._stop_flag.is_set() and self._output_path:
                            retried = True
                            try:
                                if proc.stdin:
                                    proc.stdin.close()
                            except Exception:
                                pass
                            try:
                                proc.wait(timeout=2)
                            except Exception:
                                try:
                                    proc.kill()
                                except Exception:
                                    pass
                            w2, h2 = self._size or (0, 0)
                            new_cmd = self._build_cmd(w2, h2, self._output_path, "", FPS)
                            new_proc, msg, new_log = self._probe_start(new_cmd)
                            if new_proc is not None:
                                if self._log_file is not None:
                                    try:
                                        self._log_file.close()
                                    except Exception:
                                        pass
                                proc = new_proc
                                self._proc = new_proc
                                self._log_file = new_log
                                self.last_mode = mode = "video-only"
                                self.last_error = ""
                                self._last_fail = 0.0
                                print("[录屏] 音频中断,已自动改为只录画面继续")
                                continue
                            print(f"[录屏] 换只录画面也失败: {msg}")
                        died_early = True
                        break
        except Exception as e:
            died_early = True
            print(f"[录屏] 抓帧线程异常: {e}")
        finally:
            try:
                if proc and proc.stdin:
                    proc.stdin.close()
            except Exception:
                pass
            try:
                cam.release()
            except Exception:
                pass
            if self._log_file is not None:
                try:
                    self._log_file.close()
                except Exception:
                    pass
                self._log_file = None
            # 若线程非主动停止,说明 ffmpeg 中途挂了:记冷却,并留下原因给 UI
            if not self._stop_flag.is_set():
                self._last_fail = time.time()
                if died_early:
                    self.last_error = self._explain(
                        proc.poll() if proc else "?")
                    print("[录屏] 录到一半中断: " + self.last_error)
            self._cam = None

    def stop(self):
        """停止录屏并完成文件收尾。立即返回文件路径,后台等 ffmpeg 退出。"""
        if not self.is_recording:
            return None
        proc = self._proc
        path = self._output_path
        # 立即切资源,UI 层不再认为在录屏
        if self._stop_flag:
            self._stop_flag.set()
        self._proc = None
        self._output_path = None
        self.start_time = None

        def _finalize():
            # 主动给 ffmpeg 发 q(收到即收尾退出),再等进程退出
            try:
                if proc and proc.stdin:
                    proc.stdin.write(b'q\n')
                    proc.stdin.flush()
            except Exception:
                pass
            try:
                if proc:
                    proc.wait(timeout=8)
            except Exception:
                try:
                    proc.terminate()
                    proc.wait(timeout=3)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass

        threading.Thread(target=_finalize, daemon=True).start()
        return path

    def can_stop(self):
        """是否可以停止(已录满最低时长)。"""
        return self.elapsed >= MIN_DURATION


# 全局单例
_recorder = ScreenRecorder()


def get_recorder():
    return _recorder
