# -*- coding: utf-8 -*-
"""Token 用量统计窗口 —— Fluent 浅色简约风(数据可视化保留)。"""

from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QPainter, QColor, QLinearGradient, QFont, QBrush, QPen
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget,
    QTableWidgetItem, QHeaderView, QWidget,
    QGraphicsDropShadowEffect,
)

from qfluentwidgets import PrimaryPushButton, FluentIcon

from token_stats import get_today_stats, get_recent_records, get_recent_days, _is_peak_now

# Fluent 主题色(低饱和,不刺眼)
PINK = "#e0639b"
BLUE = "#3b8be0"
PURPLE = "#7c6cf0"
GRAY = "#a0a0b8"

QSS = """
QDialog {
    background: #faf9fc;
    font-family: "Microsoft YaHei";
}
QLabel {
    color: #3a3a4a;
    font-family: "Microsoft YaHei";
}
#title {
    font-size: 20px; font-weight: 800; color: #2b2b3a;
}
#subtitle {
    font-size: 11px; color: #9a9ab0;
}
#peakBadge {
    background: #fff3e0;
    color: #d0842a;
    border: 1px solid #ffe0b3;
    border-radius: 14px;
    padding: 6px 16px;
    font-size: 12px;
    font-weight: 600;
}
#sectionTitle {
    font-size: 13px; font-weight: 700; color: #4a4a5a;
    border-left: 4px solid #7c6cf0;
    padding-left: 8px;
}
QTableWidget {
    background: white;
    border: 1px solid #e8e6f0;
    border-radius: 10px;
    gridline-color: transparent;
    font-size: 12px;
    color: #3a3a4a;
    alternate-background-color: #f7f6fb;
    selection-background-color: #ece9ff;
    selection-color: #3a3a4a;
}
QTableWidget::item { padding: 8px 6px; border-bottom: 1px solid #f2f1f8; }
QTableWidget::item:hover { background: #f4f2ff; }
QHeaderView::section {
    background: #f2f1f8;
    color: #5a5a6a;
    padding: 10px 6px;
    border: none;
    font-weight: 700;
    font-size: 12px;
}
QScrollBar:vertical {
    background: transparent; width: 8px; margin: 4px;
}
QScrollBar::handle:vertical {
    background: #d4d2e4; border-radius: 4px; min-height: 30px;
}
QScrollBar::handle:vertical:hover { background: #b8b5d0; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
#cardTitle { font-size: 12px; font-weight: 700; color: #6a6a7a; }
#cardValue { font-size: 24px; font-weight: 800; }
#cardSub { font-size: 10px; color: #b0b0c0; }
"""


class StatCard(QWidget):
    """统计卡片:白底圆角 + 左侧色条 + 顶部渐变点缀(极简)。"""

    def __init__(self, icon, title, color_a, color_b, parent=None):
        super().__init__(parent)
        self._c1 = QColor(color_a)
        self._c2 = QColor(color_b)
        self.setFixedHeight(96)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 14, 18, 12)
        lay.setSpacing(2)

        self._value = QLabel("0")
        self._value.setObjectName("cardValue")
        self._value.setStyleSheet(f"color: {color_a};")
        lay.addWidget(self._value)

        row = QHBoxLayout()
        icon_l = QLabel(icon)
        icon_l.setStyleSheet("font-size: 15px;")
        t = QLabel(title)
        t.setObjectName("cardTitle")
        row.addWidget(icon_l)
        row.addWidget(t)
        row.addStretch()
        lay.addLayout(row)

        self._sub = QLabel("")
        self._sub.setObjectName("cardSub")
        lay.addWidget(self._sub)

        # 柔和投影
        sh = QGraphicsDropShadowEffect(self)
        sh.setBlurRadius(20)
        sh.setOffset(0, 3)
        sh.setColor(QColor(180, 170, 220, 45))
        self.setGraphicsEffect(sh)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        # 白底
        p.setBrush(QBrush(QColor("white")))
        p.setPen(QPen(QColor(230, 228, 242, 200), 1))
        p.drawRoundedRect(rect, 14, 14)
        # 左侧竖色条
        p.setBrush(QBrush(self._c1))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(rect.x(), rect.y() + 18, 5, rect.height() - 36), 2.5, 2.5)

    def set_value(self, v):
        self._value.setText(v)

    def set_sub(self, s):
        self._sub.setText(s)


class TrendChart(QWidget):
    """近7天用量迷你柱状图(Fluent 蓝紫渐变)。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data = []  # [(label, tokens, cost, cnt)]
        self.setMinimumHeight(132)

    def set_data(self, data):
        self._data = data
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        if not self._data:
            p.setPen(QColor("#a0a0b8"))
            p.setFont(QFont("Microsoft YaHei", 10))
            p.drawText(self.rect(), Qt.AlignCenter, "还没有用量数据，先去聊聊天吧~")
            return
        max_v = max((d[1] for d in self._data), default=0) or 1
        n = len(self._data)
        slot = w / n
        bar_w = min(34, slot * 0.5)
        pad_top = 18
        chart_h = h - pad_top - 24
        colors = [QColor("#3b8be0"), QColor("#7c6cf0"), QColor("#e0639b")]
        for i, (label, tokens, cost, _cnt) in enumerate(self._data):
            cx = slot * i + slot / 2
            bh = max(2, chart_h * tokens / max_v)
            rect = QRectF(cx - bar_w / 2, pad_top + chart_h - bh, bar_w, bh)
            grad = QLinearGradient(0, rect.top(), 0, rect.bottom())
            c = colors[i % len(colors)]
            grad.setColorAt(0, c)
            grad.setColorAt(1, c.darker(118))
            p.setBrush(QBrush(grad))
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(rect, 7, 7)
            # 数值(>0 才标)
            if tokens > 0:
                p.setPen(QColor("#6a6a7a"))
                p.setFont(QFont("Microsoft YaHei", 8, QFont.Bold))
                p.drawText(QRectF(rect.x() - 4, rect.y() - 14, bar_w + 8, 12),
                           Qt.AlignCenter, f"{tokens}")
            # 日期
            p.setPen(QColor("#9a9ab0"))
            p.setFont(QFont("Microsoft YaHei", 8))
            p.drawText(QRectF(cx - slot / 2, h - 20, slot, 16), Qt.AlignCenter, label)


class UsageWindow(QDialog):
    """用量统计窗口: 今日卡片 + 近7天趋势 + 明细。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("用量统计")
        self.setMinimumSize(760, 640)
        self.setStyleSheet(QSS)
        self._setup_ui()
        self._load_data()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 18)
        root.setSpacing(14)

        # ── 标题区 ──
        head = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel("Token 用量统计")
        title.setObjectName("title")
        title_box.addWidget(title)
        sub = QLabel("每次调用都按 API 真实 token + 峰谷时价精确计费")
        sub.setObjectName("subtitle")
        title_box.addWidget(sub)
        head.addLayout(title_box)
        head.addStretch()
        peak = _is_peak_now()
        badge = QLabel("🔆 高峰时段 · 价格翻倍" if peak else "🌙 空闲时段 · 价格半价")
        badge.setObjectName("peakBadge")
        head.addWidget(badge)
        root.addLayout(head)

        # ── 今日统计卡片 ──
        cards = QHBoxLayout()
        cards.setSpacing(14)
        self._count_card = StatCard("💬", "今日对话", PINK, PINK)
        self._tokens_card = StatCard("📊", "今日 Token", BLUE, BLUE)
        self._cost_card = StatCard("💰", "今日费用", PURPLE, PURPLE)
        cards.addWidget(self._count_card)
        cards.addWidget(self._tokens_card)
        cards.addWidget(self._cost_card)
        root.addLayout(cards)

        # ── 近7天趋势 ──
        sec = QLabel("近 7 天用量")
        sec.setObjectName("sectionTitle")
        root.addWidget(sec)
        self._chart = TrendChart()
        self._chart.setStyleSheet(
            "background: white; border: 1px solid #e8e6f0; border-radius: 10px;")
        root.addWidget(self._chart)

        # ── 明细表 ──
        sec2 = QLabel("最近对话记录")
        sec2.setObjectName("sectionTitle")
        root.addWidget(sec2)

        self._table = QTableWidget()
        self._table.setColumnCount(7)
        self._table.setHorizontalHeaderLabels(["时间", "模型", "输入", "缓存命中", "输出", "总 Token", "费用"])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setShowGrid(False)
        root.addWidget(self._table, 1)

        # ── 刷新按钮(Fluent) ──
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        refresh_btn = PrimaryPushButton(FluentIcon.SYNC, "刷新")
        refresh_btn.clicked.connect(self._load_data)
        btn_row.addWidget(refresh_btn)
        root.addLayout(btn_row)

    def _friendly_model(self, m):
        """模型名友好化。"""
        mapping = {
            "deepseek-flash": "DeepSeek Flash",
            "deepseek-chat": "DeepSeek Flash",
            "deepseek-v4-flash": "DeepSeek V4 Flash",
            "deepseek-reasoner": "DeepSeek Flash 思考",
            "deepseek-v4-pro": "DeepSeek V4 Pro",
            "Qwen/Qwen3-VL-8B-Instruct": "Qwen3-VL 8B",
            "Qwen/Qwen3-VL-30B-A3B-Instruct": "Qwen3-VL 30B",
            "Qwen/Qwen3-8B": "Qwen3 8B",
            "Qwen/Qwen2.5-7B-Instruct": "Qwen2.5 7B",
            "THUDM/GLM-4-9B-0414": "GLM-4 9B",
        }
        return mapping.get(m, m)

    def _load_data(self):
        today = get_today_stats()
        self._count_card.set_value(f"{today['count']} 次")
        self._count_card.set_sub("对话 / 评论调用")
        self._tokens_card.set_value(f"{today['total_tokens']:,}")
        self._tokens_card.set_sub("输入 + 输出")
        self._cost_card.set_value(f"¥ {today['cost']:.4f}")
        self._cost_card.set_sub("按当前时段计价")

        # 近7天
        days = get_recent_days(7)
        self._chart.set_data(days)

        # 明细
        records = get_recent_records(30)
        self._table.setRowCount(len(records))
        for row, rec in enumerate(records):
            self._table.setItem(row, 0, QTableWidgetItem(rec["time"][5:]))
            self._table.setItem(row, 1, QTableWidgetItem(self._friendly_model(rec["model"])))
            self._table.setItem(row, 2, QTableWidgetItem(f"{rec['input_tokens']:,}"))
            ch = rec.get("cache_hit_tokens", 0)
            item_ch = QTableWidgetItem(f"{ch:,}" if ch else "—")
            item_ch.setForeground(QColor("#d0842a") if ch else QColor("#c0b0c0"))
            self._table.setItem(row, 3, item_ch)
            self._table.setItem(row, 4, QTableWidgetItem(f"{rec['output_tokens']:,}"))
            self._table.setItem(row, 5, QTableWidgetItem(f"{rec['total_tokens']:,}"))
            cost_item = QTableWidgetItem(f"¥ {rec['cost']:.6f}")
            cost_item.setForeground(QColor("#7c6cf0"))
            self._table.setItem(row, 6, cost_item)
