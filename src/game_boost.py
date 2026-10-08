# -*- coding: utf-8 -*-
"""
游戏性能优化模块（精简版，参考 MeoBoost 实现思路接入桌宠）

用法（独立运行，需要管理员权限的项会自动弹 UAC 提权）:
  python game_boost.py apply    # 应用全部优化
  python game_boost.py restore  # 还原全部优化
  python game_boost.py status   # 查看当前状态
  python game_boost.py check    # 检测是否已优化（供桌宠菜单勾选用）

优化内容:
  1. 关闭 Nagle 算法 + TCP ACK 频率 -> 降低网络延迟
  2. MMCSS 多媒体调度 -> 游戏进程 CPU/GPU 优先
  3. 关闭 GameDVR/游戏栏 -> 省 CPU/磁盘
  4. 高性能电源计划 + 关 CPU 节流 -> 稳定高频
  5. 定时器精度 0.5ms -> 降低输入延迟

全部操作可逆，状态标记存在 HKCU\\Software\\BoringPet\\GameBoost
"""

import os
import sys
import shutil
import ctypes
import subprocess
import time

APP_KEY = r"Software\BoringPet\GameBoost"
REG_APP = r"HKCU\Software\BoringPet\GameBoost"
POWER_HIGH = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"      # 高性能
POWER_BAL = "381b4222-f694-41f0-9685-ff5bb260df2e"       # 平衡


# ---------------- 基础工具 ----------------

def _is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def _run_cmd(cmd, shell=False, timeout=30):
    """执行命令，返回 (returncode, stdout, stderr)。不弹黑窗口。"""
    try:
        r = subprocess.run(
            cmd, shell=shell, capture_output=True, text=True,
            # 不能只写 text=True:中文系统上 powercfg/reg 的输出是 GBK,
            # 默认严格解码会抛 UnicodeDecodeError,结果被下面 except 吞成 (0, "", ""),
            # 调用方还以为是成功。errors="replace" 保证拿得到 returncode。
            errors="replace",
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        return r.returncode, r.stdout or "", r.stderr or ""
    except Exception as e:
        return -1, "", str(e)


def _reg_cmd(args):
    """reg.exe 命令封装。

    args 既可以是参数列表,也可以是整条命令行字符串 ——
    _nagle_apply/_nagle_restore 传的是带引号的整条命令行,
    如果直接 [reg] + args 会 TypeError(list + str)。
    """
    reg = shutil.which("reg") or "reg.exe"
    if isinstance(args, str):
        # 交给 cmd 解析,避免自己去拆那些反斜杠和引号
        return _run_cmd(f'"{reg}" {args}', shell=True)
    return _run_cmd([reg] + args)


def _reg_add(path, name, val, typ="REG_DWORD"):
    return _reg_cmd(["add", path, "/v", name, "/t", typ, "/d", str(val), "/f"])


def _reg_del(path, name=None):
    if name:
        return _reg_cmd(["delete", path, "/v", name, "/f"])
    return _reg_cmd(["delete", path, "/f"])


def _reg_read(path, name):
    """读注册表值，失败返回 None。"""
    try:
        import winreg
        hive = winreg.HKEY_LOCAL_MACHINE if path.startswith("HKLM") else winreg.HKEY_CURRENT_USER
        sub = path.split("\\", 1)[1]
        with winreg.OpenKey(hive, sub, 0, winreg.KEY_READ) as k:
            val, _ = winreg.QueryValueEx(k, name)
            return val
    except Exception:
        return None


def _mark(name):
    """写状态标记。"""
    _reg_add(REG_APP, name, 1, "REG_DWORD")


def _unmark(name):
    _reg_del(REG_APP, name)


def _marked(name):
    return _reg_read(REG_APP, name) is not None


def _bubble(msg):
    """独立运行时打印；被桌宠 import 时直接返回消息。"""
    print(msg)
    return msg


# ---------------- 优化项 ----------------

# 1. Nagle 关闭
def _nagle_is_on():
    return _marked("NagleOff")

def _nagle_apply():
    code, out, _ = _reg_cmd(
        r'query "HKLM\SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces" /s /v "DefaultGateway"'
    )
    if code != 0:
        return False
    cur = ""
    for ln in out.split("\n"):
        if "HKEY" in ln:
            cur = ln.strip().replace("HKEY_LOCAL_MACHINE", "HKLM")
        elif "DefaultGateway" in ln and cur:
            _reg_add(cur, "TcpAckFrequency", 1, "REG_DWORD")
            _reg_add(cur, "TCPNoDelay", 1, "REG_DWORD")
    _mark("NagleOff")
    return True

def _nagle_restore():
    code, out, _ = _reg_cmd(
        r'query "HKLM\SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces" /s /v "DefaultGateway"'
    )
    if code == 0:
        cur = ""
        for ln in out.split("\n"):
            if "HKEY" in ln:
                cur = ln.strip().replace("HKEY_LOCAL_MACHINE", "HKLM")
            elif "DefaultGateway" in ln and cur:
                _reg_del(cur, "TcpAckFrequency")
                _reg_del(cur, "TCPNoDelay")
    _unmark("NagleOff")


# 2. MMCSS 游戏优先
MMCSS = r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile"

def _mmcss_is_on():
    return _marked("MmcssOn")

def _mmcss_apply():
    _reg_add(MMCSS, "SystemResponsiveness", 0, "REG_DWORD")
    _reg_add(MMCSS, "NetworkThrottlingIndex", 0xFFFFFFFF, "REG_DWORD")
    _reg_add(MMCSS + r"\Tasks\Games", "Scheduling Category", "High", "REG_SZ")
    _reg_add(MMCSS + r"\Tasks\Games", "SFIO Priority", "High", "REG_SZ")
    _reg_add(MMCSS + r"\Tasks\Games", "GPU Priority", 8, "REG_DWORD")
    _reg_add(MMCSS + r"\Tasks\Games", "Priority", 6, "REG_DWORD")
    _mark("MmcssOn")
    return True

def _mmcss_restore():
    _reg_add(MMCSS, "SystemResponsiveness", 20, "REG_DWORD")
    _reg_add(MMCSS, "NetworkThrottlingIndex", 10, "REG_DWORD")
    _reg_add(MMCSS + r"\Tasks\Games", "Scheduling Category", "Medium", "REG_SZ")
    _reg_add(MMCSS + r"\Tasks\Games", "SFIO Priority", "Normal", "REG_SZ")
    _reg_add(MMCSS + r"\Tasks\Games", "GPU Priority", 4, "REG_DWORD")
    _reg_add(MMCSS + r"\Tasks\Games", "Priority", 2, "REG_DWORD")
    _unmark("MmcssOn")


# 3. GameDVR / 游戏栏
def _gdvr_is_on():
    return _marked("GameDvrOff")

def _gdvr_apply():
    _reg_add(r"HKCU\System\GameConfigStore", "GameDVR_Enabled", 0, "REG_DWORD")
    _reg_add(r"HKLM\SOFTWARE\Policies\Microsoft\Windows\GameDVR", "AllowGameDVR", 0, "REG_DWORD")
    _reg_add(r"HKCU\Software\Microsoft\Windows\CurrentVersion\GameDVR", "AppCaptureEnabled", 0, "REG_DWORD")
    _reg_add(r"HKCU\Software\Microsoft\GameBar", "UseNexusForGameBarEnabled", 0, "REG_DWORD")
    _reg_add(r"HKCU\Software\Microsoft\GameBar", "ShowStartupPanel", 0, "REG_DWORD")
    _mark("GameDvrOff")
    return True

def _gdvr_restore():
    _reg_add(r"HKCU\System\GameConfigStore", "GameDVR_Enabled", 1, "REG_DWORD")
    _reg_del(r"HKLM\SOFTWARE\Policies\Microsoft\Windows\GameDVR", "AllowGameDVR")
    _reg_add(r"HKCU\Software\Microsoft\Windows\CurrentVersion\GameDVR", "AppCaptureEnabled", 1, "REG_DWORD")
    _reg_add(r"HKCU\Software\Microsoft\GameBar", "UseNexusForGameBarEnabled", 1, "REG_DWORD")
    _reg_add(r"HKCU\Software\Microsoft\GameBar", "ShowStartupPanel", 1, "REG_DWORD")
    _unmark("GameDvrOff")


# 4. 电源计划 + 节流
def _power_is_on():
    return _marked("PowerHigh")

def _power_apply():
    _run_cmd(["powercfg", "/setactive", POWER_HIGH])
    _run_cmd(["powercfg", "/setacvalueindex", POWER_HIGH, "sub_processor", "CPMINCORES", "100"])
    _run_cmd(["powercfg", "/setactive", POWER_HIGH])
    _reg_add(r"HKLM\SYSTEM\CurrentControlSet\Control\Power\PowerThrottling", "PowerThrottlingOff", 1, "REG_DWORD")
    _mark("PowerHigh")
    return True

def _power_restore():
    _run_cmd(["powercfg", "/setactive", POWER_BAL])
    _reg_del(r"HKLM\SYSTEM\CurrentControlSet\Control\Power\PowerThrottling", "PowerThrottlingOff")
    _unmark("PowerHigh")


# 5. 定时器精度 0.5ms（仅当前进程维持；桌宠常驻所以持续有效）
def _timer_is_on():
    return _marked("TimerRes")

def _timer_apply():
    _run_cmd(["bcdedit", "/set", "disabledynamictick", "yes"])
    _mark("TimerRes")
    return True

def _timer_restore():
    _run_cmd(["bcdedit", "/deletevalue", "disabledynamictick"])
    _unmark("TimerRes")


# ---------------- 整体控制 ----------------

ALL = [
    ("Nagle 关闭（降网络延迟）", _nagle_is_on, _nagle_apply, _nagle_restore),
    ("MMCSS 游戏优先（CPU/GPU）", _mmcss_is_on, _mmcss_apply, _mmcss_restore),
    ("关闭 GameDVR（省资源）", _gdvr_is_on, _gdvr_apply, _gdvr_restore),
    ("高性能电源（稳定高频）", _power_is_on, _power_apply, _power_restore),
    ("定时器 0.5ms（降输入延迟）", _timer_is_on, _timer_apply, _timer_restore),
]


def apply_all(quiet=False):
    results = []
    for name, _, apply_fn, _ in ALL:
        try:
            ok = apply_fn()
            results.append((name, ok))
        except Exception as e:
            results.append((name, False))
    msgs = []
    for name, ok in results:
        msgs.append(f"{'✓' if ok else '✗'} {name}")
    msg = "\n".join(msgs)
    if not quiet:
        _bubble(msg)
    return msg


def restore_all(quiet=False):
    results = []
    for name, _, _, restore_fn in ALL:
        try:
            restore_fn()
            results.append((name, True))
        except Exception as e:
            results.append((name, False))
    msgs = []
    for name, ok in results:
        msgs.append(f"{'✓' if ok else '✗'} {name}")
    msg = "\n".join(msgs)
    if not quiet:
        _bubble(msg)
    return msg


def is_any_on():
    """是否已开启任意优化项（供菜单勾选）。"""
    return any(is_on() for _, is_on, _, _ in ALL)


def status_text():
    lines = []
    for name, is_on, _, _ in ALL:
        lines.append(f"{'●' if is_on() else '○'} {name}")
    return "\n".join(lines)


def check_admin():
    """若当前无管理员权限，弹 UAC 重新提权执行并退出。"""
    if not _is_admin():
        script = os.path.abspath(__file__)
        args = sys.argv[1:] if len(sys.argv) > 1 else ["apply"]
        # 用 pythonw 避免黑窗口
        pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if not os.path.exists(pyw):
            pyw = sys.executable
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", pyw,
            f'"{script}" {" ".join(args)}',
            os.path.dirname(script), 1,
        )
        sys.exit(0)


def _main():
    args = sys.argv[1:] if len(sys.argv) > 1 else ["apply"]
    cmd = args[0]

    # 调试日志：记录每次调用
    try:
        log_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        log_path = os.path.join(log_dir, "game_boost_log.txt")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] cmd={cmd} pid={os.getpid()} admin={_is_admin()} args={sys.argv}\n")
    except Exception:
        pass

    if cmd == "check":
        # 桌宠菜单勾选用：输出 0/1
        print("1" if is_any_on() else "0")
        return

    if cmd == "status":
        print(status_text())
        return

    # apply / restore 需要管理员权限（HKLM 键值）
    if not _is_admin():
        check_admin()
        return

    if cmd == "apply":
        apply_all()
        print("\n[完成] 游戏优化已开启，重启游戏生效最佳")
    elif cmd == "restore":
        restore_all()
        print("\n[完成] 已还原全部优化")


if __name__ == "__main__":
    _main()
