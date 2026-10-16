# -*- coding: utf-8 -*-
"""设置窗口:qfluentwidgets Fluent 风格,配置桌宠外观/身份/AI对话/主动功能。

对外接口保持不变(供 pet.py / chat_window.py 使用):
  SettingsWindow(QDialog)  settings_saved 信号
  load_settings / save_settings / ensure_settings_file
  build_pet_identity / detect_relationship_mode
  PROVIDERS / DEFAULT_SETTINGS / SETTINGS_FILE
"""
import json
import os
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QButtonGroup, QDialog, QFrame, QHBoxLayout,
                               QLabel, QPushButton, QScrollArea, QTextEdit,
                               QVBoxLayout, QWidget)

from qfluentwidgets import (
    FluentIcon, LineEdit, ComboBox, Slider, SwitchButton, RadioButton,
    PushButton, PrimaryPushButton, SettingCard, SettingCardGroup, setTheme,
    Theme,
)

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
        "idle_max_hours": 8,
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
    "dsh": {
        # DSH 数据根目录(含 sessions 子目录), 留空则自动检测:
        #   环境变量 DSH_HOME -> ~/.dsh -> 旧路径
        "data_dir": "",
    },
}


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


class NoScrollComboBox(ComboBox):
    """禁用滚轮切换的下拉框。"""
    def wheelEvent(self, e):
        e.ignore()


def _card_edit(placeholder="", width=230):
    """Fluent LineEdit:设置卡片右侧的输入框。"""
    edit = LineEdit()
    edit.setPlaceholderText(placeholder)
    edit.setFixedWidth(width)
    return edit


class SettingsWindow(QDialog):
    """设置窗口(Fluent 风格)。"""
    settings_saved = Signal(dict)  # 保存后发出,携带新配置

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.resize(640, 920)
        self.setMinimumSize(580, 760)

        # 统一 Fluent 浅色主题(仅影响 qfluentwidgets 组件,桌宠本体自绘不受影响)
        setTheme(Theme.LIGHT)

        self._settings = load_settings()
        self._build_ui()
        self._fill_values()

    # ────────────────────────── UI 构建 ──────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 标题栏
        title_bar = QFrame()
        title_bar.setFixedHeight(56)
        tl = QHBoxLayout(title_bar)
        tl.setContentsMargins(24, 0, 24, 0)
        title = QLabel("设置")
        title.setStyleSheet(
            "font-size:20px;font-weight:700;color:#1f1f1f;background:transparent;"
        )
        tl.addWidget(title)
        tl.addStretch()
        root.addWidget(title_bar)

        # 滚动区域
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setContentsMargins(16, 8, 16, 16)
        cl.setSpacing(10)

        # ── 1. 桌宠外观 ──
        g1 = SettingCardGroup("桌宠外观", content)
        c_size = SettingCard(FluentIcon.ZOOM, "大小", "桌宠尺寸")
        self._size_row = QHBoxLayout()
        self._size_row.setSpacing(6)
        self._size_group = QButtonGroup(self)
        for txt, h in [("小", 150), ("中", 220), ("大", 300), ("超大", 380)]:
            btn = QPushButton(txt)
            btn.setCheckable(True)
            btn.setProperty("size_h", h)
            btn.setStyleSheet(
                "QPushButton{border:1px solid #d0d0d0;border-radius:6px;padding:5px 14px;"
                "background:transparent;color:#333;}"
                "QPushButton:hover{background:#f0f0f0;}"
                "QPushButton:checked{background:#2b7ff5;color:white;border:none;}"
            )
            self._size_group.addButton(btn)
            self._size_row.addWidget(btn)
        self._size_row.addStretch()
        c_size.hBoxLayout.addLayout(self._size_row)
        g1.addSettingCard(c_size)

        c_op = SettingCard(FluentIcon.TRANSPARENT, "透明度", "不透明度")
        self._op_slider = Slider(Qt.Horizontal)
        self._op_slider.setRange(20, 100)
        self._op_slider.setFixedWidth(220)
        self._op_label = QLabel("100%")
        self._op_label.setStyleSheet("color:#2b7ff5;font-size:13px;font-weight:600;min-width:44px;")
        self._op_slider.valueChanged.connect(lambda v: self._op_label.setText(f"{v}%"))
        c_op.hBoxLayout.addWidget(self._op_slider)
        c_op.hBoxLayout.addWidget(self._op_label)
        g1.addSettingCard(c_op)
        cl.addWidget(g1)

        # ── 2. 桌宠身份 ──
        g2 = SettingCardGroup("桌宠身份", content)
        c_name = SettingCard(FluentIcon.EDIT, "桌宠名字", "给你的宠物起个名字")
        self._name_edit = _card_edit()
        c_name.hBoxLayout.addWidget(self._name_edit)
        g2.addSettingCard(c_name)

        c_owner = SettingCard(FluentIcon.PEOPLE, "对你的称呼", "它怎么叫你?比如:主人、名字")
        self._owner_edit = _card_edit()
        c_owner.hBoxLayout.addWidget(self._owner_edit)
        g2.addSettingCard(c_owner)

        c_persona = SettingCard(FluentIcon.QUICK_NOTE, "人设描述", "性格、说话风格、身份设定")
        self._persona_edit = QTextEdit()
        self._persona_edit.setPlaceholderText("描述它的性格、说话风格、身份设定…")
        self._persona_edit.setFixedSize(240, 70)
        self._persona_edit.setStyleSheet(
            "QTextEdit{border:1px solid #d0d0d0;border-radius:6px;padding:6px;"
            "background:white;color:#333;font-size:13px;}"
            "QTextEdit:focus{border:1px solid #2b7ff5;}"
        )
        c_persona.hBoxLayout.addWidget(self._persona_edit)
        g2.addSettingCard(c_persona)
        cl.addWidget(g2)

        # ── 3. AI 对话 ──
        g3 = SettingCardGroup("AI 对话", content)
        c_mode = SettingCard(FluentIcon.ROBOT, "对话模式", "DSH 有工具能力;直连 API 简单对话")
        self._mode_row = QHBoxLayout()
        self._mode_row.setSpacing(12)
        self._mode_group = QButtonGroup(self)
        self._rb_openclaw = RadioButton("DSH（本地 agent，有工具能力）")
        self._rb_direct = RadioButton("直连 API（简单对话）")
        self._mode_group.addButton(self._rb_openclaw)
        self._mode_group.addButton(self._rb_direct)
        self._mode_row.addWidget(self._rb_openclaw)
        self._mode_row.addWidget(self._rb_direct)
        self._mode_row.addStretch()
        c_mode.hBoxLayout.addLayout(self._mode_row)
        g3.addSettingCard(c_mode)

        # 直连配置容器(放在独立的卡片里,切换模式时隐藏)
        self._direct_card = SettingCard(FluentIcon.SETTING, "直连配置", "提供商 / API Key / 模型")
        dl = QVBoxLayout()
        dl.setContentsMargins(0, 4, 0, 0)
        dl.setSpacing(8)

        prov_row = QHBoxLayout()
        prov_row.addWidget(QLabel("模型提供商"))
        self._chat_provider = NoScrollComboBox()
        for key, p in PROVIDERS.items():
            self._chat_provider.addItem(p["label"], key)
        self._chat_provider.setFixedWidth(230)
        self._chat_provider.currentIndexChanged.connect(self._on_chat_provider_change)
        prov_row.addWidget(self._chat_provider)
        prov_row.addStretch()
        dl.addLayout(prov_row)

        key_row = QHBoxLayout()
        key_row.addWidget(QLabel("API Key"))
        self._chat_key = _card_edit("sk-...")
        key_row.addWidget(self._chat_key)
        key_row.addStretch()
        dl.addLayout(key_row)

        self._chat_model_label = QLabel("")
        self._chat_model_label.setStyleSheet("color:#2b7ff5;font-size:12px;background:transparent;")
        dl.addWidget(self._chat_model_label)

        # 自定义提供商时才显示的可编辑区
        self._chat_custom_frame = QFrame()
        self._chat_custom_frame.setStyleSheet("background:transparent;")
        ccl = QVBoxLayout(self._chat_custom_frame)
        ccl.setContentsMargins(0, 4, 0, 0)
        ccl.setSpacing(8)
        m_row = QHBoxLayout()
        m_row.addWidget(QLabel("模型名"))
        self._chat_model = _card_edit()
        m_row.addWidget(self._chat_model)
        m_row.addStretch()
        ccl.addLayout(m_row)
        b_row = QHBoxLayout()
        b_row.addWidget(QLabel("API 地址"))
        self._chat_base_url = _card_edit("https://api.example.com/v1", width=260)
        b_row.addWidget(self._chat_base_url)
        b_row.addStretch()
        ccl.addLayout(b_row)
        dl.addWidget(self._chat_custom_frame)

        self._direct_card.setLayout(dl)
        g3.addSettingCard(self._direct_card)

        # DSH 数据目录(留空自动检测, 用于 DSH 任务完成气泡提醒)
        self._dsh_data_dir_edit = _card_edit("", 280)
        c_dsh = SettingCard(FluentIcon.FOLDER, "DSH 数据目录", "留空自动检测（DSH_HOME / ~/.dsh）")
        c_dsh.hBoxLayout.addWidget(self._dsh_data_dir_edit)
        g3.addSettingCard(c_dsh)

        self._rb_openclaw.toggled.connect(lambda on: self._direct_card.setVisible(not on))
        cl.addWidget(g3)

        # ── 4. 主动功能 ──
        g4 = SettingCardGroup("主动功能（识图/双击评论/心率关心）", content)
        c_ap = SettingCard(FluentIcon.MEGAPHONE, "模型提供商", "主动说话/识图用")
        ap_row = QHBoxLayout()
        self._active_provider = NoScrollComboBox()
        for key, p in PROVIDERS.items():
            self._active_provider.addItem(p["label"], key)
        self._active_provider.setFixedWidth(230)
        self._active_provider.currentIndexChanged.connect(self._on_active_provider_change)
        ap_row.addWidget(self._active_provider)
        ap_row.addStretch()
        c_ap.hBoxLayout.addLayout(ap_row)
        g4.addSettingCard(c_ap)

        c_ak = SettingCard(FluentIcon.CERTIFICATE, "API Key", "sk-...")
        self._active_key = _card_edit()
        c_ak.hBoxLayout.addWidget(self._active_key)
        g4.addSettingCard(c_ak)

        self._active_model_label = QLabel("")
        self._active_model_label.setStyleSheet("color:#2b7ff5;font-size:12px;background:transparent;")
        self._active_model_card = SettingCard(FluentIcon.INFO, "默认模型", "识图 / 说话模型")
        self._active_model_card.hBoxLayout.addWidget(self._active_model_label)
        g4.addSettingCard(self._active_model_card)

        self._active_custom_frame = QFrame()
        self._active_custom_frame.setStyleSheet("background:transparent;")
        cfl = QVBoxLayout(self._active_custom_frame)
        cfl.setContentsMargins(0, 4, 0, 0)
        cfl.setSpacing(8)
        v_row = QHBoxLayout()
        v_row.addWidget(QLabel("识图模型"))
        self._vision_model = _card_edit()
        v_row.addWidget(self._vision_model)
        v_row.addStretch()
        cfl.addLayout(v_row)
        t_row = QHBoxLayout()
        t_row.addWidget(QLabel("说话模型"))
        self._text_model = _card_edit()
        t_row.addWidget(self._text_model)
        t_row.addStretch()
        cfl.addLayout(t_row)
        ab_row = QHBoxLayout()
        ab_row.addWidget(QLabel("API 地址"))
        self._active_base_url = _card_edit("https://api.example.com/v1", width=260)
        ab_row.addWidget(self._active_base_url)
        ab_row.addStretch()
        cfl.addLayout(ab_row)

        self._active_custom_card = SettingCard(FluentIcon.CODE, "自定义配置", "模型名与 API 地址")
        self._active_custom_card.setLayout(cfl)
        g4.addSettingCard(self._active_custom_card)
        cl.addWidget(g4)

        # ── 5. 评论与心率设置 ──
        g5 = SettingCardGroup("评论与心率设置", content)
        self._game_comment_min_edit = _card_edit("2", 120)
        c_gmin = SettingCard(FluentIcon.STOP_WATCH, "游戏评论最小间隔", "分钟")
        c_gmin.hBoxLayout.addWidget(self._game_comment_min_edit)
        g5.addSettingCard(c_gmin)

        self._game_comment_max_edit = _card_edit("10", 120)
        c_gmax = SettingCard(FluentIcon.STOP_WATCH, "游戏评论最大间隔", "分钟")
        c_gmax.hBoxLayout.addWidget(self._game_comment_max_edit)
        g5.addSettingCard(c_gmax)

        self._idle_threshold_edit = _card_edit("0.45", 120)
        c_it = SettingCard(FluentIcon.SPEED_MEDIUM, "平时想念阈值", "0~1，越大越难主动")
        c_it.hBoxLayout.addWidget(self._idle_threshold_edit)
        g5.addSettingCard(c_it)

        self._idle_max_hours_edit = _card_edit("8", 120)
        c_im = SettingCard(FluentIcon.DATE_TIME, "平时保底时间", "小时，超时必主动")
        c_im.hBoxLayout.addWidget(self._idle_max_hours_edit)
        g5.addSettingCard(c_im)

        self._hr_threshold_edit = _card_edit("115", 120)
        c_ht = SettingCard(FluentIcon.HEART, "心率关心阈值", "bpm")
        c_ht.hBoxLayout.addWidget(self._hr_threshold_edit)
        g5.addSettingCard(c_ht)

        self._rec_threshold_edit = _card_edit("105", 120)
        c_rt = SettingCard(FluentIcon.VIDEO, "自动录屏阈值", "bpm")
        c_rt.hBoxLayout.addWidget(self._rec_threshold_edit)
        g5.addSettingCard(c_rt)

        self._hr_interval_edit = _card_edit("2", 120)
        c_hi = SettingCard(FluentIcon.STOP_WATCH, "心率关心间隔", "分钟")
        c_hi.hBoxLayout.addWidget(self._hr_interval_edit)
        g5.addSettingCard(c_hi)
        cl.addWidget(g5)

        # ── 6. 记忆向量模型 ──
        g6 = SettingCardGroup("记忆向量模型（语义检索）", content)
        self._vector_key = _card_edit("sk-...（不填则复用 AI 对话的 key）", 260)
        c_vk = SettingCard(FluentIcon.CERTIFICATE, "API Key", "向量服务密钥")
        c_vk.hBoxLayout.addWidget(self._vector_key)
        g6.addSettingCard(c_vk)

        self._vector_model = _card_edit("text-embedding-v4", 230)
        c_vm = SettingCard(FluentIcon.SEARCH, "向量模型名", "如 text-embedding-v4")
        c_vm.hBoxLayout.addWidget(self._vector_model)
        g6.addSettingCard(c_vm)

        self._vector_base_url = _card_edit("https://dashscope.aliyuncs.com/compatible-mode/v1", 280)
        c_vb = SettingCard(FluentIcon.LINK, "API 地址", "base_url")
        c_vb.hBoxLayout.addWidget(self._vector_base_url)
        g6.addSettingCard(c_vb)
        cl.addWidget(g6)

        # ── 7. 决疑设置 ──
        g7 = SettingCardGroup("决疑（塔罗/六爻/小六壬/八字）", content)
        self._div_spread = NoScrollComboBox()
        for txt, key in [("三张（现状/阻碍/指引）", "three-card"), ("单张", "single"), ("决策四张", "decision")]:
            self._div_spread.addItem(txt, key)
        self._div_spread.setFixedWidth(230)
        c_ds = SettingCard(FluentIcon.CALENDAR, "塔罗默认牌阵", "抽牌张数")
        c_ds.hBoxLayout.addWidget(self._div_spread)
        g7.addSettingCard(c_ds)

        self._div_reversals = SwitchButton("允许")
        c_dr = SettingCard(FluentIcon.SYNC, "塔罗允许逆位", "牌可倒置解读")
        c_dr.hBoxLayout.addWidget(self._div_reversals)
        g7.addSettingCard(c_dr)

        self._div_birth = _card_edit("2006-02-26T15:40", 230)
        c_db = SettingCard(FluentIcon.DATE_TIME, "八字生日", "公历 ISO，如 2006-02-26T15:40")
        c_db.hBoxLayout.addWidget(self._div_birth)
        g7.addSettingCard(c_db)

        self._div_remind = SwitchButton("提醒")
        c_dm = SettingCard(FluentIcon.RINGER, "启动时提醒待应验的卦", "打开桌宠时提示")
        c_dm.hBoxLayout.addWidget(self._div_remind)
        g7.addSettingCard(c_dm)
        cl.addWidget(g7)

        # 底部按钮
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._save_btn = PrimaryPushButton("保存")
        self._save_btn.setFixedWidth(120)
        self._save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(self._save_btn)
        self._cancel_btn = PushButton("取消")
        self._cancel_btn.setFixedWidth(100)
        self._cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._cancel_btn)
        cl.addLayout(btn_row)

        cl.addStretch()
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

    # ────────────────────────── 填值 ──────────────────────────

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
            self._direct_card.setVisible(False)
        else:
            self._rb_direct.setChecked(True)
            self._direct_card.setVisible(True)
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
        # DSH 数据目录
        self._dsh_data_dir_edit.setText((s.get("dsh") or {}).get("data_dir", ""))

    # ────────────────────────── 交互 ──────────────────────────

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
            self._active_custom_card.setVisible(True)
        else:
            self._active_model_label.setText(f"识图：{default_v} ｜ 说话：{default_t}")
            self._active_custom_card.setVisible(False)
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
        # DSH 数据目录
        s.setdefault("dsh", {})
        s["dsh"]["data_dir"] = self._dsh_data_dir_edit.text().strip()

        if save_settings(s):
            self.settings_saved.emit(s)
            self.accept()
        else:
            self._save_btn.setText("保存失败")
