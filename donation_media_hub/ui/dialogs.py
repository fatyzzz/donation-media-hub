from __future__ import annotations

import webbrowser
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QApplication,
    QMessageBox,
)


class HelpDialog(QDialog):
    def __init__(self, parent, title: str, text: str, link: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setFixedSize(560, 320)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        lbl = QLabel(text)
        lbl.setWordWrap(True)
        root.addWidget(lbl)

        root.addWidget(QLabel("Ссылка:"))
        link_lbl = QLabel(f'<a href="{link}">{link}</a>')
        link_lbl.setTextFormat(Qt.RichText)
        link_lbl.setTextInteractionFlags(Qt.TextBrowserInteraction)
        link_lbl.setOpenExternalLinks(False)
        link_lbl.linkActivated.connect(lambda _u: webbrowser.open(link))
        root.addWidget(link_lbl)

        row = QHBoxLayout()
        copy_btn = QPushButton("Copy")
        ok_btn = QPushButton("OK")
        ok_btn.setObjectName("Primary")

        def copy_link() -> None:
            QApplication.clipboard().setText(link)
            QMessageBox.information(self, "OK", "Ссылка скопирована.")

        copy_btn.clicked.connect(copy_link)
        ok_btn.clicked.connect(self.accept)

        row.addWidget(copy_btn)
        row.addStretch(1)
        row.addWidget(ok_btn)
        root.addLayout(row)


def ask_yes_no(parent, title: str, text: str) -> bool:
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Question)
    box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
    box.setDefaultButton(QMessageBox.No)
    return box.exec() == QMessageBox.Yes
