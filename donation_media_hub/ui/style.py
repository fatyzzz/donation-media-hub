from __future__ import annotations


def load_qss() -> str:
    # keep it self-contained; if you later add assets/theme.qss, load it here.
    return """
*{font-family:"Segoe UI";font-size:10.5pt;}
QMainWindow{background:#121212;}
QWidget{color:#fff;}
QFrame#Sidebar{background:#000;}
QFrame#Card{background:#181818;border:1px solid #2a2a2a;border-radius:14px;}
QFrame#Card2{background:#151515;border:1px solid #2a2a2a;border-radius:14px;}
QLabel#Title{font-size:18pt;font-weight:700;}
QLabel#Sub{color:#b3b3b3;}
QPushButton{background:transparent;border:none;padding:8px 10px;border-radius:12px;}
QPushButton:hover{background:#2a2a2a;}
QPushButton:pressed{background:#333;}
QPushButton#Primary{background:#1db954;color:#000;font-weight:700;border-radius:16px;padding:10px 14px;}
QPushButton#Danger{background:#f7768e;color:#000;font-weight:700;border-radius:16px;padding:10px 14px;}
QLineEdit{background:#1f1f1f;border:1px solid #2b2b2b;border-radius:10px;padding:8px;color:#fff;}
QCheckBox{spacing:8px;}
QTableView{background:transparent;border:none;gridline-color:#2a2a2a;}
QHeaderView::section{background:#151515;color:#b3b3b3;padding:8px;border:none;border-bottom:1px solid #2a2a2a;}
QTableView::item{padding:8px;border-bottom:1px solid #232323;}
QTableView::item:selected{background:#1db954;color:#000;}
QScrollBar:vertical{background:#0f0f0f;width:12px;margin:0;border:none;}
QScrollBar::handle:vertical{background:#2a2a2a;border-radius:6px;min-height:24px;}
QScrollBar::handle:vertical:hover{background:#3a3a3a;}
QSlider::groove:horizontal{height:4px;background:#404040;border-radius:2px;}
QSlider::handle:horizontal{background:#1db954;width:12px;margin:-4px 0;border-radius:6px;}
QPlainTextEdit{background:#0f0f0f;border:1px solid #2a2a2a;border-radius:12px;padding:8px;color:#eaeaea;}
"""
