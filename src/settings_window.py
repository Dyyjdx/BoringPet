# -*- coding: utf-8 -*-
"""设置窗口:粉蓝可爱风,配置桌宠外观/身份/AI对话/主动功能。"""
import json
import os
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QFrame, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QRadioButton, QScrollArea,
                               QSlider, QTextEdit, QVBoxLayout, QWidget)

from paths import app_dir

BASE = Path(__file__).parent.parent.resolve()
SETTINGS_FILE = str(app_dir() / "settings.json")

# ── 提供商预设 ──
PROVIDERS = {
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "chat_models": ["deepseek-chat", "deepseek-reasoner"],
        "vision_models": ["deepseek-flash"],
        "text_models": ["deepseek-chat", "deepseek-reasoner"],
    },
    "siliconflow": {
        "label": "SiliconFlow(硅基流动)",
        "base_url": "https://api.siliconflow.cn/v1",
        "chat_models": ["Qwen/Qwen3-8B", "THUDM/GLM-4-9B-0414", "Qwen/Qwen2.5-7B-Instruct", "deepseek-ai/DeepSeek-V3"],
        "vision_models": ["Qwen/Qwen3-VL-8B-Instruct", "Qwen/Qwen3-VL-32B-Instruct"],
        "text_models": ["Qwen/Qwen3-8B", "THUDM/GLM-4-9B-0414", "Qwen/Qwen2.5-7B-Instruct"],
    },
    "custom": {
        "label": "自定义(OpenAI兼容)",
        "base_url": "",
        "chat_models": [],
        "vision_models": [],
        "text_models": [],
    },
}

# ── 关系模式判定: 根据用户 persona 文本决定注入哪套身份/动作模板 ──
_LOVER_KEYWORDS = ["女朋友", "女友", "恋人", "暗恋", "老婆", "男朋友", "男友", "老公", "对象", "心动", "情侣"]


def detect_relationship_mode(persona):
    """返回 "lover"(恋人/女友模式) 或 "pet"(宠物模式)。
    女友模式: 骨架不注入任何宠物动作词(趴着/蹦起来/蹭蹭等)。"""
    if not persona:
        return "pet"
    for kw in _LOVER_KEYWORDS:
        if kw in persona:
            return "lover"
    return "pet"


def build_pet_identity():
    """读取设置, 构建身份模板字典。核心: 身份/关系/年龄变量化 + 动作库条件化。
    返回 (name, owner, persona, identity_dict)。"""
    cfg = load_settings().get("pet", {})
    name = cfg.get("name", "BoringPet")
    owner = cfg.get("owner_name", "主人")
    persona = cfg.get("persona", "")
    mode = detect_relationship_mode(persona)
    if mode == "lover":
        identity = {
            "mode": "lover",
            "身份": f"住在{owner}桌面上的小人",
            "关系": "小女朋友",
            "年龄": "21",
            "动作风格": "害羞内敛",
            "动作示例": "凑近、偷看、碰指尖、靠过来、别开脸、轻轻拽衣袖",
            "禁用动作": "宠物、主人、趴着、蹦起来、蹭蹭、摇尾巴",
        }
    else:
        identity = {
            "mode": "pet",
            "身份": f"住在{owner}桌面上的小宠物",
            "关系": "小宠物",
            "年龄": "21",
            "动作风格": "懒洋洋、粘人",
            "动作示例": "趴着、蹦起来、蹭蹭",
            "禁用动作": "恋人动作、亲吻、牵手",
        }
    return name, owner, persona, identity


DEFAULT_SETTINGS = {
    "pet": {
        "name": "BoringPet",
        "persona": (
            "你是一只住在主人桌面上的可爱桌面宠物，名字叫 BoringPet。"
            "说话简短可爱，像宠物一样会撒娇、会关心主人。"
            "不要说自己是 AI，你就是主人的桌面宠物。"
        ),
        "owner_name": "主人",
        "size": 220,
        "opacity": 1.0,
    },
    "chat": {
        "mode": "direct",
        "provider": "deepseek",
        "api_key": "",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
    },
    "active": {
        "provider": "deepseek",
        "api_key": "",
        "base_url": "https://api.deepseek.com/v1",
        "vision_model": "deepseek-flash",
        "vision_base_url": "https://api.deepseek.com/v1",
        "vision_api_key": "",
        "text_model": "deepseek-chat",
    },
    "intervals": {
        "game_comment_minutes": 5,
        "game_comment_min_minutes": 2,
        "game_comment_max_minutes": 10,
        "idle_longing_threshold": 0.45,
        "idle_max_hours": 4,
        "hr_alert_threshold": 115,
        "hr_alert_interval_min": 2,
        "recording_threshold": 105,
        # 录屏时的系统声音设备名(ffmpeg dshow 的名字)。
        # 装了 VB-Audio Virtual Cable 就用默认值;填 "" 或 "none" 表示只录画面不录声音。
        # 名字必须和 `ffmpeg -list_devices true -f dshow -i dummy` 列出来的一致。
        "record_audio_device": "CABLE Output (VB-Audio Virtual Cable)",
    },
    "divination": {
        "deck": "major",
        "spread": "three-card",
        "reversals": True,
        "birth": "",
        "remind": True,
    },
    "vector": {
        "api_key": "",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "text-embedding-v4",
        "model_used": "",
    },
}

SETTINGS_QSS = """
QWidget#settingsRoot { background:#f5f8ff; }
QFrame#card { background:#ffffff; border:1px solid #e0e8f8; border-radius:12px; }
QLabel#cardTitle { color:#2a3a5a; font-size:14px; font-weight:700; }
QLabel { color:#5a6a8a; font-size:12px; }
QLineEdit { background:#ffffff; border:1px solid #d0dcf0; border-radius:8px; padding:7px 10px;
             font-size:12px; color:#2a3a5a; }
QLineEdit:focus { border:1px solid #7ba8ff; }
QTextEdit { background:#ffffff; border:1px solid #d0dcf0; border-radius:8px; padding:7px 10px;
            font-size:12px; color:#2a3a5a; }
QTextEdit:focus { border:1px solid #7ba8ff; }
QComboBox { background:#ffffff; border:1px solid #d0dcf0; border-radius:8px; padding:7px 10px;
            font-size:12px; color:#2a3a5a; min-height:18px; }
QComboBox:hover { background:#eef2ff; }
QPushButton#sizeBtn { background:#f0f4ff; color:#5a6a8a; border:1px solid #d0dcf0; border-radius:8px;
                      padding:6px 14px; font-size:12px; font-weight:600; }
QPushButton#sizeBtn:checked { background:qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #7ba8ff, stop:1 #f090bc);
                               color:#ffffff; border:none; }
QPushButton#saveBtn { background:qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #7ba8ff, stop:1 #f090bc);
                      color:#ffffff; border:none; border-radius:10px; padding:8px 28px;
                      font-size:13px; font-weight:600; }
QPushButton#saveBtn:hover { background:qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #8bb5ff, stop:1 #f5a0c8); }
QPushButton#cancelBtn { background:transparent; color:#8a9ab8; border:1px solid #d0dcf0; border-radius:10px;
                        padding:8px 20px; font-size:13px; }
QPushButton#cancelBtn:hover { background:#eef2ff; }
QRadioButton { color:#5a6a8a; font-size:12px; spacing:6px; }
QRadioButton::indicator { width:14px; height:14px; border-radius:7px; border:2px solid #c0d0f0; }
QRadioButton::indicator:checked { background:qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #7ba8ff, stop:1 #f090bc);
                                    border:2px solid #ffffff; }
QSlider::groove:horizontal { height:4px; background:#e0e8f8; border-radius:2px; }
QSlider::handle:horizontal { background:qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #7ba8ff, stop:1 #f090bc);
                              width:14px; height:14px; margin:-5px 0; border-radius:7px; border:2px solid #ffffff; }
QSlider::sub-page:horizontal { background:qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #7ba8ff, stop:1 #f090bc);
                                 border-radius:2px; }
QScrollArea { border:none; background:transparent; }
QScrollBar:vertical { background:transparent; width:6px; margin:2px; }
QScrollBar::handle:vertical { background:#d0dcf0; border-radius:3px; min-height:20px; }
QScrollBar::handle:vertical:hover { background:#b8c8f0; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
"""


_bootstrapped = False


def ensure_settings_file():
    """首次运行(例如别人刚拿到打包版)没有 settings.json 时,按默认值生成一份,保证能启动。

    返回 True 表示本次新建了文件。每个进程只检查一次。
    """
    global _bootstrapped
    if _bootstrapped:
        return False
    _bootstrapped = True
    if os.path.exists(SETTINGS_FILE):
        return False
    return save_settings(json.loads(json.dumps(DEFAULT_SETTINGS)))


def load_settings():
    """读取设置,缺失字段用默认值补全。"""
    ensure_settings_file()
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    # 深度合并默认值
    result = json.loads(json.dumps(DEFAULT_SETTINGS))
    for section in result:
        if section not in data:
            continue
        if isinstance(result[section], dict) and isinstance(data[section], dict):
            result[section].update(data[section])
        else:
            result[section] = data[section]
    return result


def save_settings(settings):
    """保存设置到文件(原子写:先写临时文件再替换,避免写到一半崩溃把配置截断)。"""
    tmp = SETTINGS_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        os.replace(tmp, SETTINGS_FILE)
        return True
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


def _card(title):
    """创建一个卡片容器。"""
    card = QFrame()
    card.setObjectName("card")
    lay = QVBoxLayout(card)
    lay.setContentsMargins(14, 12, 14, 12)
    lay.setSpacing(8)
    t = QLabel(title)
    t.setObjectName("cardTitle")
    lay.addWidget(t)
    return card, lay


def _label(text):
    l = QLabel(text)
    return l


class NoScrollComboBox(QComboBox):
    """禁用滚轮切换的下拉框。"""
    def wheelEvent(self, e):
        e.ignore()


class SettingsWindow(QDialog):
    """设置窗口。"""
    settings_saved = Signal(dict)  # 保存后发出,携带新配置

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setObjectName("settingsRoot")
        self.setStyleSheet(SETTINGS_QSS)
        self.resize(560, 900)
        self.setMinimumSize(500, 750)

        self._settings = load_settings()
        self._build_ui()
        self._fill_values()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 标题栏
        title_bar = QFrame()
        title_bar.setStyleSheet("background:#ffffff; border-bottom:1px solid #e0e8f8;")
        tl = QHBoxLayout(title_bar)
        tl.setContentsMargins(20, 14, 20, 14)
        title = QLabel("设置")
        title.setStyleSheet("color:#2a3a5a; font-size:18px; font-weight:800;")
        tl.addWidget(title)
        tl.addStretch()
        root.addWidget(title_bar)

        # 滚动区域
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content.setObjectName("settingsRoot")
        cl = QVBoxLayout(content)
        cl.setContentsMargins(12, 12, 12, 12)
        cl.setSpacing(10)

        # ── 1. 桌宠外观 ──
        card1, lay1 = _card("桌宠外观")
        # 大小
        lay1.addWidget(_label("大小"))
        size_row = QHBoxLayout()
        size_row.setSpacing(8)
        self._size_group = QButtonGroup(self)
        for txt, h in [("小", 150), ("中", 220), ("大", 300), ("超大", 380)]:
            btn = QPushButton(txt)
            btn.setObjectName("sizeBtn")
            btn.setCheckable(True)
            btn.setProperty("size_h", h)
            self._size_group.addButton(btn)
            size_row.addWidget(btn)
        size_row.addStretch()
        lay1.addLayout(size_row)
        # 透明度
        op_row = QHBoxLayout()
        op_row.setSpacing(12)
        op_row.addWidget(_label("透明度"))
        self._op_slider = QSlider(Qt.Horizontal)
        self._op_slider.setRange(20, 100)
        self._op_label = QLabel("100%")
        self._op_label.setStyleSheet("color:#7ba8ff; font-size:13px; font-weight:600; min-width:40px;")
        self._op_slider.valueChanged.connect(lambda v: self._op_label.setText(f"{v}%"))
        op_row.addWidget(self._op_slider, 1)
        op_row.addWidget(self._op_label)
        lay1.addLayout(op_row)
        cl.addWidget(card1)

        # ── 2. 桌宠身份 ──
        card2, lay2 = _card("桌宠身份")
        # 名字
        lay2.addWidget(_label("桌宠名字"))
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("给你的宠物起个名字")
        lay2.addWidget(self._name_edit)
        # 主人称呼
        lay2.addWidget(_label("对你的称呼"))
        self._owner_edit = QLineEdit()
        self._owner_edit.setPlaceholderText("它怎么叫你？比如：主人、老公、名字")
        lay2.addWidget(self._owner_edit)
        # 人设描述
        lay2.addWidget(_label("人设描述"))
        self._persona_edit = QTextEdit()
        self._persona_edit.setPlaceholderText("描述它的性格、说话风格、身份设定…")
        self._persona_edit.setFixedHeight(70)
        lay2.addWidget(self._persona_edit)
        cl.addWidget(card2)

        # ── 3. AI 对话 ──
        card3, lay3 = _card("AI 对话")
        # 模式
        lay3.addWidget(_label("对话模式"))
        mode_row = QHBoxLayout()
        mode_row.setSpacing(20)
        self._mode_group = QButtonGroup(self)
        self._rb_openclaw = QRadioButton("DSH（本地 agent，有工具能力）")
        self._rb_direct = QRadioButton("直连 API（简单对话，不需要 DSH）")
        self._mode_group.addButton(self._rb_openclaw)
        self._mode_group.addButton(self._rb_direct)
        mode_row.addWidget(self._rb_openclaw)
        mode_row.addWidget(self._rb_direct)
        mode_row.addStretch()
        lay3.addLayout(mode_row)
        # 直连配置容器
        self._direct_frame = QFrame()
        self._direct_frame.setStyleSheet("background:transparent;")
        dl = QVBoxLayout(self._direct_frame)
        dl.setContentsMargins(0, 8, 0, 0)
        dl.setSpacing(10)
        # 提供商
        dl.addWidget(_label("模型提供商"))
        self._chat_provider = NoScrollComboBox()
        for key, p in PROVIDERS.items():
            self._chat_provider.addItem(p["label"], key)
        self._chat_provider.currentIndexChanged.connect(self._on_chat_provider_change)
        # 修复下拉列表透明问题
        view = self._chat_provider.view()
        view.setAutoFillBackground(True)
        view.setStyleSheet("""
            QListView { background:#ffffff; border:1px solid #d0dcf0; }
            QListView::item { padding:8px 12px; color:#2a3a5a; }
            QListView::item:selected { background:#eef2ff; color:#2a3a5a; }
        """)
        dl.addWidget(self._chat_provider)
        # API Key
        dl.addWidget(_label("API Key"))
        self._chat_key = QLineEdit()
        self._chat_key.setPlaceholderText("sk-...")
        dl.addWidget(self._chat_key)
        # 模型名(只读显示)
        self._chat_model_label = QLabel("")
        self._chat_model_label.setStyleSheet("color:#7ba8ff; font-size:12px; background:transparent; padding:2px 0;")
        dl.addWidget(self._chat_model_label)
        # 自定义提供商时才显示的可编辑区
        self._chat_custom_frame = QFrame()
        self._chat_custom_frame.setStyleSheet("background:transparent;")
        ccl = QVBoxLayout(self._chat_custom_frame)
        ccl.setContentsMargins(0, 6, 0, 0)
        ccl.setSpacing(8)
        ccl.addWidget(_label("模型名"))
        self._chat_model = QLineEdit()
        ccl.addWidget(self._chat_model)
        ccl.addWidget(_label("API 地址（base_url）"))
        self._chat_base_url = QLineEdit()
        self._chat_base_url.setPlaceholderText("https://api.example.com/v1")
        ccl.addWidget(self._chat_base_url)
        dl.addWidget(self._chat_custom_frame)
        lay3.addWidget(self._direct_frame)
        # 模式切换显示/隐藏直连配置
        self._rb_openclaw.toggled.connect(lambda on: self._direct_frame.setVisible(not on))
        cl.addWidget(card3)

        # ── 4. 主动功能 ──
        card4, lay4 = _card("主动功能（识图/双击评论/心率关心）")
        # 提供商
        lay4.addWidget(_label("模型提供商"))
        self._active_provider = NoScrollComboBox()
        for key, p in PROVIDERS.items():
            self._active_provider.addItem(p["label"], key)
        self._active_provider.currentIndexChanged.connect(self._on_active_provider_change)
        # 修复下拉列表透明问题
        view = self._active_provider.view()
        view.setAutoFillBackground(True)
        view.setStyleSheet("""
            QListView { background:#ffffff; border:1px solid #d0dcf0; }
            QListView::item { padding:8px 12px; color:#2a3a5a; }
            QListView::item:selected { background:#eef2ff; color:#2a3a5a; }
        """)
        lay4.addWidget(self._active_provider)
        # API Key
        lay4.addWidget(_label("API Key"))
        self._active_key = QLineEdit()
        self._active_key.setPlaceholderText("sk-...")
        lay4.addWidget(self._active_key)
        # 模型名(只读显示)
        self._active_model_label = QLabel("")
        self._active_model_label.setStyleSheet("color:#7ba8ff; font-size:12px; background:transparent; padding:2px 0;")
        lay4.addWidget(self._active_model_label)
        # 自定义提供商时才显示的可编辑区
        self._active_custom_frame = QFrame()
        self._active_custom_frame.setStyleSheet("background:transparent;")
        cfl = QVBoxLayout(self._active_custom_frame)
        cfl.setContentsMargins(0, 6, 0, 0)
        cfl.setSpacing(8)
        cfl.addWidget(_label("识图模型"))
        self._vision_model = QLineEdit()
        cfl.addWidget(self._vision_model)
        cfl.addWidget(_label("说话模型"))
        self._text_model = QLineEdit()
        cfl.addWidget(self._text_model)
        cfl.addWidget(_label("API 地址（base_url）"))
        self._active_base_url = QLineEdit()
        self._active_base_url.setPlaceholderText("https://api.example.com/v1")
        cfl.addWidget(self._active_base_url)
        lay4.addWidget(self._active_custom_frame)
        cl.addWidget(card4)

        # ── 5. 评论与心率设置 ──
        card5, lay5 = _card("评论与心率设置")
        # 游戏评论间隔(随机)
        lay5.addWidget(_label("游戏内评论最小间隔（分钟）"))
        self._game_comment_min_edit = QLineEdit()
        self._game_comment_min_edit.setPlaceholderText("2")
        lay5.addWidget(self._game_comment_min_edit)
        lay5.addWidget(_label("游戏内评论最大间隔（分钟）"))
        self._game_comment_max_edit = QLineEdit()
        self._game_comment_max_edit.setPlaceholderText("10")
        lay5.addWidget(self._game_comment_max_edit)
        # 平时主动: 驱力驱动(想念值), 无固定定时
        lay5.addWidget(_label("平时想念阈值（0~1，越大越难主动）"))
        self._idle_threshold_edit = QLineEdit()
        self._idle_threshold_edit.setPlaceholderText("0.45（约3小时不理她会想你）")
        lay5.addWidget(self._idle_threshold_edit)
        lay5.addWidget(_label("平时保底时间（小时，超时必主动）"))
        self._idle_max_hours_edit = QLineEdit()
        self._idle_max_hours_edit.setPlaceholderText("4")
        lay5.addWidget(self._idle_max_hours_edit)
        # 心率关心阈值
        lay5.addWidget(_label("心率关心阈值（bpm）"))
        self._hr_threshold_edit = QLineEdit()
        self._hr_threshold_edit.setPlaceholderText("115")
        lay5.addWidget(self._hr_threshold_edit)
        # 录屏阈值
        lay5.addWidget(_label("自动录屏阈值（bpm）"))
        self._rec_threshold_edit = QLineEdit()
        self._rec_threshold_edit.setPlaceholderText("105")
        lay5.addWidget(self._rec_threshold_edit)
        # 心率关心间隔
        lay5.addWidget(_label("心率关心间隔（分钟）"))
        self._hr_interval_edit = QLineEdit()
        self._hr_interval_edit.setPlaceholderText("2")
        lay5.addWidget(self._hr_interval_edit)
        cl.addWidget(card5)

        # ── 6. 记忆向量模型 ──
        card6, lay6 = _card("记忆向量模型（语义检索）")
        lay6.addWidget(_label("API Key"))
        self._vector_key = QLineEdit()
        self._vector_key.setPlaceholderText("sk-...（不填则复用 AI 对话的 key）")
        lay6.addWidget(self._vector_key)
        lay6.addWidget(_label("向量模型名"))
        self._vector_model = QLineEdit()
        self._vector_model.setPlaceholderText("text-embedding-v4（阿里百炼）")
        lay6.addWidget(self._vector_model)
        lay6.addWidget(_label("API 地址（base_url）"))
        self._vector_base_url = QLineEdit()
        self._vector_base_url.setPlaceholderText("https://dashscope.aliyuncs.com/compatible-mode/v1")
        lay6.addWidget(self._vector_base_url)
        tip = QLabel("用于记忆的语义检索（回忆相似对话）。换模型后会自动重新向量化旧记忆，无需手动操作。")
        tip.setStyleSheet("color:#8a9ab8; font-size:11px;")
        tip.setWordWrap(True)
        lay6.addWidget(tip)
        cl.addWidget(card6)

        # ── 7. 算命设置 ──
        card7, lay7 = _card("算命（塔罗/六爻/小六壬/八字）")
        # 默认牌阵
        lay7.addWidget(_label("塔罗默认牌阵"))
        self._div_spread = NoScrollComboBox()
        for txt, key in [("三张（现状/阻碍/指引）", "three-card"), ("单张", "single"), ("决策四张", "decision")]:
            self._div_spread.addItem(txt, key)
        view7 = self._div_spread.view()
        view7.setAutoFillBackground(True)
        view7.setStyleSheet("""
            QListView { background:#ffffff; border:1px solid #d0dcf0; }
            QListView::item { padding:8px 12px; color:#2a3a5a; }
            QListView::item:selected { background:#eef2ff; color:#2a3a5a; }
        """)
        lay7.addWidget(self._div_spread)
        # 逆位开关
        self._div_reversals = QCheckBox("塔罗允许逆位")
        self._div_reversals.setStyleSheet("QCheckBox { color:#5a6a8a; font-size:12px; }")
        lay7.addWidget(self._div_reversals)
        # 八字生日
        lay7.addWidget(_label("八字生日（公历 ISO，如 2006-02-26T15:40）"))
        self._div_birth = QLineEdit()
        self._div_birth.setPlaceholderText("2006-02-26T15:40")
        lay7.addWidget(self._div_birth)
        # 待应验提醒
        self._div_remind = QCheckBox("启动时提醒待应验的卦")
        self._div_remind.setStyleSheet("QCheckBox { color:#5a6a8a; font-size:12px; }")
        lay7.addWidget(self._div_remind)
        cl.addWidget(card7)

        # 保存按钮(放在滚动区域里)
        save_row = QHBoxLayout()
        save_row.addStretch()
        self._save_btn = QPushButton("保存")
        self._save_btn.setObjectName("saveBtn")
        self._save_btn.setFixedWidth(120)
        self._save_btn.clicked.connect(self._on_save)
        save_row.addWidget(self._save_btn)
        self._cancel_btn = QPushButton("取消")
        self._cancel_btn.setObjectName("cancelBtn")
        self._cancel_btn.setFixedWidth(100)
        self._cancel_btn.clicked.connect(self.reject)
        save_row.addWidget(self._cancel_btn)
        cl.addLayout(save_row)

        cl.addStretch()
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

    def _fill_values(self):
        s = self._settings
        # 外观
        for btn in self._size_group.buttons():
            if btn.property("size_h") == s["pet"]["size"]:
                btn.setChecked(True)
        self._op_slider.setValue(int(s["pet"]["opacity"] * 100))
        # 身份
        self._name_edit.setText(s["pet"]["name"])
        self._owner_edit.setText(s["pet"]["owner_name"])
        self._persona_edit.setPlainText(s["pet"]["persona"])
        # AI 对话
        if s["chat"]["mode"] == "dsh":
            self._rb_openclaw.setChecked(True)
            self._direct_frame.setVisible(False)
        else:
            self._rb_direct.setChecked(True)
            self._direct_frame.setVisible(True)
        idx = self._chat_provider.findData(s["chat"]["provider"])
        if idx >= 0:
            self._chat_provider.setCurrentIndex(idx)
        self._chat_key.setText(s["chat"]["api_key"])
        self._chat_base_url.setText(s["chat"]["base_url"])
        self._on_chat_provider_change()  # 初始化标签和默认值
        self._chat_model.setText(s["chat"]["model"])
        self._chat_base_url.setText(s["chat"]["base_url"])
        # 主动功能
        idx = self._active_provider.findData(s["active"]["provider"])
        if idx >= 0:
            self._active_provider.setCurrentIndex(idx)
        self._active_key.setText(s["active"]["api_key"])
        self._active_base_url.setText(s["active"]["base_url"])
        self._on_active_provider_change()
        self._vision_model.setText(s["active"]["vision_model"])
        self._text_model.setText(s["active"]["text_model"])
        self._active_base_url.setText(s["active"]["base_url"])
        # 评论与心率
        self._game_comment_min_edit.setText(str(s["intervals"].get("game_comment_min_minutes", 2)))
        self._game_comment_max_edit.setText(str(s["intervals"].get("game_comment_max_minutes", 10)))
        self._idle_threshold_edit.setText(str(s["intervals"].get("idle_longing_threshold", 0.45)))
        self._idle_max_hours_edit.setText(str(s["intervals"].get("idle_max_hours", 4)))
        self._hr_threshold_edit.setText(str(s["intervals"]["hr_alert_threshold"]))
        self._hr_interval_edit.setText(str(s["intervals"]["hr_alert_interval_min"]))
        self._rec_threshold_edit.setText(str(s["intervals"].get("recording_threshold", 105)))
        # 算命
        idx = self._div_spread.findData(s.get("divination", {}).get("spread", "three-card"))
        if idx >= 0:
            self._div_spread.setCurrentIndex(idx)
        self._div_reversals.setChecked(s.get("divination", {}).get("reversals", True))
        self._div_birth.setText(s.get("divination", {}).get("birth", ""))
        self._div_remind.setChecked(s.get("divination", {}).get("remind", True))
        # 记忆向量模型
        vec = s.get("vector", {})
        self._vector_key.setText(vec.get("api_key", ""))
        self._vector_model.setText(vec.get("model", "text-embedding-v4"))
        self._vector_base_url.setText(vec.get("base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1"))

    def _on_chat_provider_change(self):
        key = self._chat_provider.currentData()
        p = PROVIDERS.get(key, {})
        is_custom = (key == "custom")
        models = p.get("chat_models", [])
        default_model = models[0] if models else ""
        if is_custom:
            self._chat_model_label.setText("请在下方填写模型名和 API 地址")
            self._chat_custom_frame.setVisible(True)
        else:
            self._chat_model_label.setText(f"默认模型：{default_model}")
            self._chat_custom_frame.setVisible(False)
            self._chat_model.setText(default_model)
            self._chat_base_url.setText(p.get("base_url", ""))

    def _on_active_provider_change(self):
        key = self._active_provider.currentData()
        p = PROVIDERS.get(key, {})
        is_custom = (key == "custom")
        v_models = p.get("vision_models", [])
        t_models = p.get("text_models", [])
        default_v = v_models[0] if v_models else ""
        default_t = t_models[0] if t_models else ""
        if is_custom:
            self._active_model_label.setText("请在下方填写模型名和 API 地址")
            self._active_custom_frame.setVisible(True)
        else:
            self._active_model_label.setText(f"识图：{default_v} ｜ 说话：{default_t}")
            self._active_custom_frame.setVisible(False)
            self._vision_model.setText(default_v)
            self._text_model.setText(default_t)
            self._active_base_url.setText(p.get("base_url", ""))

    def _on_save(self):
        s = self._settings
        # 旧配置/重置后可能缺段, 兜底补上(防 KeyError)
        s.setdefault("divination", {})
        s.setdefault("intervals", {})
        # 外观
        checked = self._size_group.checkedButton()
        if checked:
            s["pet"]["size"] = checked.property("size_h")
        s["pet"]["opacity"] = self._op_slider.value() / 100.0
        # 身份
        s["pet"]["name"] = self._name_edit.text().strip() or "BoringPet"
        s["pet"]["owner_name"] = self._owner_edit.text().strip() or "主人"
        s["pet"]["persona"] = self._persona_edit.toPlainText().strip() or DEFAULT_SETTINGS["pet"]["persona"]
        # AI 对话
        s["chat"]["mode"] = "dsh" if self._rb_openclaw.isChecked() else "direct"
        s["chat"]["provider"] = self._chat_provider.currentData()
        s["chat"]["api_key"] = self._chat_key.text().strip()
        s["chat"]["base_url"] = self._chat_base_url.text().strip()
        s["chat"]["model"] = self._chat_model.text().strip()
        # 主动功能
        s["active"]["provider"] = self._active_provider.currentData()
        s["active"]["api_key"] = self._active_key.text().strip()
        s["active"]["base_url"] = self._active_base_url.text().strip()
        s["active"]["vision_model"] = self._vision_model.text().strip()
        # 视觉走独立配置: 只有没单独设过时才跟随主配置(避免每次保存把用户单独配的看图通道覆盖掉)
        if not s["active"].get("vision_base_url"):
            s["active"]["vision_base_url"] = s["active"]["base_url"]
        if not s["active"].get("vision_api_key"):
            s["active"]["vision_api_key"] = s["active"]["api_key"]
        s["active"]["text_model"] = self._text_model.text().strip()
        # 评论与心率
        try:
            s["intervals"]["game_comment_min_minutes"] = int(self._game_comment_min_edit.text().strip())
        except ValueError:
            s["intervals"]["game_comment_min_minutes"] = 2
        try:
            s["intervals"]["game_comment_max_minutes"] = int(self._game_comment_max_edit.text().strip())
        except ValueError:
            s["intervals"]["game_comment_max_minutes"] = 10
        try:
            s["intervals"]["idle_longing_threshold"] = float(self._idle_threshold_edit.text().strip())
        except ValueError:
            s["intervals"]["idle_longing_threshold"] = 0.45
        try:
            s["intervals"]["idle_max_hours"] = int(self._idle_max_hours_edit.text().strip())
        except ValueError:
            s["intervals"]["idle_max_hours"] = 4
        try:
            s["intervals"]["hr_alert_threshold"] = int(self._hr_threshold_edit.text().strip())
        except ValueError:
            s["intervals"]["hr_alert_threshold"] = 115
        try:
            s["intervals"]["hr_alert_interval_min"] = int(self._hr_interval_edit.text().strip())
        except ValueError:
            s["intervals"]["hr_alert_interval_min"] = 2
        try:
            s["intervals"]["recording_threshold"] = int(self._rec_threshold_edit.text().strip())
        except ValueError:
            s["intervals"]["recording_threshold"] = 105
        # 算命
        s["divination"]["spread"] = self._div_spread.currentData() or "three-card"
        s["divination"]["reversals"] = self._div_reversals.isChecked()
        s["divination"]["birth"] = self._div_birth.text().strip()
        s["divination"]["remind"] = self._div_remind.isChecked()
        # 记忆向量模型
        s.setdefault("vector", {})
        s["vector"]["api_key"] = self._vector_key.text().strip()
        s["vector"]["model"] = self._vector_model.text().strip() or "text-embedding-v4"
        s["vector"]["base_url"] = self._vector_base_url.text().strip() or "https://dashscope.aliyuncs.com/compatible-mode/v1"

        if save_settings(s):
            self.settings_saved.emit(s)
            self.accept()
        else:
            self._save_btn.setText("保存失败")
