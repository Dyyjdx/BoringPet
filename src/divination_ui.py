# -*- coding: utf-8 -*-
"""算命详情窗口: 显示完整解读 + 记重要 + 手动标记应验。"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QFrame, QHBoxLayout, QLabel,
                               QPushButton, QTextBrowser, QVBoxLayout)

from divination import get_record, mark_feedback, mark_important

DIV_LABELS = {"tarot": "塔罗", "iching": "六爻", "xiaoliuren": "小六壬", "bazi": "八字"}
FEEDBACK_OPTIONS = ["应验了", "部分应验", "没应验", "忘了"]

_QSS = """
QDialog#divRoot { background:#f5f8ff; }
QLabel#divTitle { color:#2a3a5a; font-size:17px; font-weight:800; }
QLabel#divMeta { color:#8a9ab8; font-size:11px; }
QTextBrowser { background:#ffffff; border:1px solid #e0e8f8; border-radius:10px;
               padding:12px; color:#2a3a5a; font-size:13px; }
QPushButton#divBtn { background:#ffffff; color:#5a6a8a; border:1px solid #d0dcf0;
                     border-radius:8px; padding:7px 14px; font-size:12px; font-weight:600; }
QPushButton#divBtn:hover { background:#eef2ff; }
QPushButton#divBtn:checked { background:qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #7ba8ff, stop:1 #f090bc);
                             color:#ffffff; border:none; }
QComboBox { background:#ffffff; border:1px solid #d0dcf0; border-radius:8px; padding:6px 10px;
            font-size:12px; color:#2a3a5a; min-height:18px; }
QComboBox:hover { background:#eef2ff; }
"""


class DivinationWindow(QDialog):
    """算命结果详情窗: 完整解读 + 记重要 + 应验反馈。"""

    def __init__(self, record_id, parent=None):
        super().__init__(parent)
        self.record_id = record_id
        self._record = get_record(record_id)
        self.setWindowTitle("🔮 算命结果")
        self.setObjectName("divRoot")
        self.setStyleSheet(_QSS)
        self.resize(460, 600)
        self.setMinimumSize(400, 500)
        self._build_ui()
        self._fill()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        title = QLabel("🔮 算命结果")
        title.setObjectName("divTitle")
        root.addWidget(title)

        self._meta = QLabel("")
        self._meta.setObjectName("divMeta")
        root.addWidget(self._meta)

        self._browser = QTextBrowser()
        self._browser.setOpenExternalLinks(True)
        root.addWidget(self._browser, 1)

        # 按钮行
        row = QHBoxLayout()
        row.setSpacing(10)
        self._important_btn = QPushButton("⭐ 记重要")
        self._important_btn.setObjectName("divBtn")
        self._important_btn.setCheckable(True)
        self._important_btn.toggled.connect(self._on_important)
        row.addWidget(self._important_btn)

        row.addStretch()

        self._fb_combo = QComboBox()
        for opt in FEEDBACK_OPTIONS:
            self._fb_combo.addItem(opt)
        self._fb_combo.setEnabled(self._record is not None and self._record.get("status") == "pending")
        row.addWidget(self._fb_combo)

        fb_btn = QPushButton("标记应验")
        fb_btn.setObjectName("divBtn")
        fb_btn.clicked.connect(self._on_feedback)
        row.addWidget(fb_btn)

        close_btn = QPushButton("关闭")
        close_btn.setObjectName("divBtn")
        close_btn.clicked.connect(self.accept)
        row.addWidget(close_btn)

        root.addLayout(row)

    def _fill(self):
        rec = self._record
        if rec is None:
            self._meta.setText("记录不存在")
            self._browser.setPlainText("（找不到这条占卜记录）")
            return
        label = DIV_LABELS.get(rec.get("type"), rec.get("type"))
        q = (rec.get("question") or "").strip() or "（未填写问题）"
        created = (rec.get("created_at") or "")[:16].replace("T", " ")
        status_txt = {"pending": "待应验", "fulfilled": "已应验", "partial": "部分应验", "void": "已标记"}.get(
            rec.get("status"), rec.get("status")
        )
        self._meta.setText(f"{label} ｜ 问题：{q} ｜ {created} ｜ 状态：{status_txt}")
        # 数据库列名是 interpretation(不是 interpretation_md), 兼容两者
        md = rec.get("interpretation") or rec.get("interpretation_md") or ""
        if not md.startswith("#"):
            md = "# 结果\n" + md
        self._browser.setMarkdown(md)
        self._important_btn.setChecked(bool(rec.get("important")))

    def _on_important(self, on):
        try:
            mark_important(self.record_id, on)
        except Exception as e:
            print(f"[Divination] 记重要失败: {e}")

    def _on_feedback(self):
        if self._record is None:
            return
        fb = self._fb_combo.currentText()
        try:
            mark_feedback(self.record_id, fb)
            self._fb_combo.setEnabled(False)
            self._record = get_record(self.record_id)
            self._fill()
        except Exception as e:
            print(f"[Divination] 标记应验失败: {e}")
