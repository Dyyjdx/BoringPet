# -*- mode: python ; coding: utf-8 -*-
"""BoringPet 打包配置(PyInstaller)。

用法(在项目根目录):
    pyinstaller BoringPet.spec --noconfirm
产物:
    dist/BoringPet/BoringPet.exe

设计要点:
* onedir(文件夹)模式,不是 onefile。素材有 200 MB 以上,onefile 每次启动都要
  把它解压到临时目录,启动会慢十几秒,而且临时目录被清理后还要重解压。
* 素材(assets/图标/心率图标/音乐素材/config.json)打进包里,只读。
* 用户数据(settings.json / memory / recordings / 音乐 / *.log)不打包,
  运行时在 exe 同目录自动创建,方便升级时保留数据。
  settings.json 也绝不打包 —— 里面有 API Key,打进去就等于发给所有人。
* 排除 torch/transformers:本项目已不再使用,装了就多 3~5 GB。
"""
import glob
import importlib.util
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

ROOT = SPECPATH  # spec 所在目录,也就是项目根目录

# ─────────────── Anaconda 专用:让依赖分析找得到 Library\bin ───────────────
# Anaconda 把 openssl / expat / ffi / lzma / bz2 这些共享库放在 <prefix>\Library\bin,
# 而不是 <prefix>\DLLs。PyInstaller 在 Windows 上是**按 PATH** 去找依赖的
# (PyInstaller/depend/bindepend.py 的 resolve_library_path),
# 找不到就打一堆 "Library not found: could not resolve 'libexpat.dll'" 警告,
# 打出来的 exe 一启动就崩:
#   Failed to execute script 'pyi_rth_pkgres' ...
#   DLL load failed while importing pyexpat: 找不到指定的模块。
# 所以这里把该目录临时加进 PATH。
# 注意:依赖分析只会拷**真正被引用到**的那几个 DLL(约 9 MB),
# 不会把整个 Library\bin(1.2 GB)打进去。
for _libdir in (os.path.join(sys.prefix, "Library", "bin"),
                os.path.join(sys.base_prefix, "Library", "bin")):
    if os.path.isdir(_libdir) and _libdir not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = _libdir + os.pathsep + os.environ.get("PATH", "")

# ---------------------------------------------------------------- 打进包里的素材
datas = [
    (os.path.join(ROOT, "config.json"), "."),
    (os.path.join(ROOT, "assets"), "assets"),
    (os.path.join(ROOT, "图标"), "图标"),
    (os.path.join(ROOT, "心率图标"), "心率图标"),
    (os.path.join(ROOT, "音乐素材"), "音乐素材"),
]

# oraclebone 的排盘需要 data/*.json、schemas/*.json、templates/*.md,
# PyInstaller 不会自动带上这些数据文件,必须显式收集。
datas += collect_data_files("oraclebone")

# dxcam 的色彩转换内核是一个编译扩展(_numpy_kernels.*.pyd)。
# 它是被 importlib.import_module("dxcam.processor._numpy_kernels") 动态加载的,
# PyInstaller 的静态分析看不到;而 collect_dynamic_libs() 只收 .dll、不收 .pyd,
# 所以必须手动按包路径加进去。
# 不加的后果:录屏仍能用,但会退回纯 numpy 转换(慢很多),而且会在日志里
# 抱怨 "dxcam.processor._numpy_kernels is unavailable"。
binaries = collect_dynamic_libs("dxcam")   # dxcam 自带的 .dll(如果有)
_dxcam_spec = importlib.util.find_spec("dxcam")
if _dxcam_spec and _dxcam_spec.submodule_search_locations:
    _dxcam_root = list(_dxcam_spec.submodule_search_locations)[0]
    _kernels = glob.glob(os.path.join(_dxcam_root, "processor", "_numpy_kernels*.pyd"))
    binaries += [(p, os.path.join("dxcam", "processor")) for p in _kernels]
    print("[spec] dxcam kernels bundled: %d" % len(_kernels))

# ---------------------------------------------------------------- 隐式导入
hiddenimports = [
    "oraclebone",
    "oraclebone.bazi",
    "oraclebone.iching",
    "oraclebone.tarot",
    "oraclebone.xiaoliuren",
    "dxcam",
    "pygame",
    "pygame.mixer",
    "mutagen",
    "mutagen.mp3",
    "mutagen.mp4",
    "mutagen.flac",
    "mutagen.oggvorbis",
    "mutagen.wave",
    "bleak",
]

# ---------------------------------------------------------------- 排除
# 这些要么本项目根本不用,要么是体积大户。特别注意 torch:一旦被拉进来,
# 打包结果会从几百 MB 涨到好几个 GB。
excludes = [
    "torch", "torchvision", "torchaudio", "transformers", "sentence_transformers",
    "tensorflow", "keras", "sklearn", "scipy", "pandas", "matplotlib",
    "IPython", "jupyter", "notebook", "nbformat", "pytest", "sphinx",
    "tkinter", "PyQt5", "PyQt6", "PySide2",
    # 用不到的 Qt 模块(界面全是 PySide6.QtWidgets + QtGui)
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel", "PySide6.QtWebSockets",
    "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQuickWidgets", "PySide6.QtQml",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DAnimation",
    "PySide6.Qt3DExtras", "PySide6.Qt3DInput", "PySide6.Qt3DLogic",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtTest", "PySide6.QtUiTools",
    "PySide6.QtSql", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtSpatialAudio",
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning",
    "PySide6.QtSerialPort", "PySide6.QtSerialBus", "PySide6.QtRemoteObjects",
    "PySide6.QtScxml", "PySide6.QtSensors", "PySide6.QtStateMachine",
    "PySide6.QtTextToSpeech", "PySide6.QtNetworkAuth", "PySide6.QtHttpServer",
    # ── 下面这些是被别的包「顺带拽进来」的,本项目从没 import 过 ──
    # 判定方法:先把 main.py 的启动链 import 一遍,看 sys.modules 里出现了谁;
    # 再把每个懒加载功能(oraclebone / dxcam / deepseek_harness / mutagen)
    # 各自单独 import 一遍,确认它们也不依赖这些。两轮都没出现,才放进这里。
    #
    # 注意几个「看着没用但故意保留」的:
    #   numpy / pydantic / pydantic_core / comtypes / win32 → 录屏(dxcam)和 DSH 要用
    #   setuptools(pkg_resources)→ pygame 启动时就会 import,删了会崩
    #   zmq / cryptography / yaml → DSH 的 SDK 运行时可能用到,体积不大,不冒险
    "mypy",        # 类型检查器,运行时永远不会被导入
    "PIL",         # 12.8 MB;本项目只用 imageio_ffmpeg 定位 ffmpeg.exe,不处理图片
    "imageio",     # 同理,imageio_ffmpeg 是独立包,不需要 imageio
    "jinja2", "markupsafe", "dateutil",   # 模板/日期库,项目里一处都没用到
]

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT, os.path.join(ROOT, "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

# ---------------------------------------------------------------- 裁掉用不到的 Qt 文件
# excludes 只能挡 Python 模块,挡不住 PySide6 的 DLL 和多语言包(它们是数据文件),
# 所以这里直接过滤 Analysis 出来的文件清单。
_DROP_FILES = {
    # 项目里没有 QML / Quick / 虚拟键盘 / PDF,这几个 DLL 不会被加载
    "qt6qml.dll", "qt6qmlmodels.dll", "qt6quick.dll", "qt6quickwidgets.dll",
    "qt6virtualkeyboard.dll", "qt6pdf.dll", "qpdf.dll",
    # Qt 的 OpenGL 软件回退库(19.7 MB):界面全是 QWidget + QPainter 光栅渲染,
    # 走不到 OpenGL 路径。想再省这 20 MB 就把下面这行的注释去掉
    # (极小的概率会在完全没有显卡驱动的机器上出问题,所以默认保留)。
    # "opengl32sw.dll",
}


def _trim_toc(entries):
    """丢掉用不到的 Qt 文件。entry 是 (目标路径, 源路径, 类型) 三元组。"""
    kept = []
    for entry in entries:
        low = entry[0].replace("\\", "/").lower()
        base = low.rsplit("/", 1)[-1]
        # Qt 自带多语言包:只留简/繁中文(设置窗口的 QInputDialog 按钮要用)
        if "/pyside6/translations/" in low:
            if not (base.endswith("zh_cn.qm") or base.endswith("zh_tw.qm")):
                continue
        elif base in _DROP_FILES:
            continue
        kept.append(entry)
    return kept


_before = len(a.binaries) + len(a.datas)
a.binaries = _trim_toc(a.binaries)
a.datas = _trim_toc(a.datas)
print("[spec] trimmed %d unused Qt files" % (_before - len(a.binaries) - len(a.datas)))

pyz = PYZ(a.pure)


exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BoringPet",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX 压 Qt 的 DLL 容易压坏,别开
    console=False,      # 桌宠不要黑框;日志写在 exe 同目录的 boringpet.log
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(ROOT, "图标", "程序.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="BoringPet",
)
