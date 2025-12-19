from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QFrame,
    QLabel,
    QPushButton,
    QLineEdit,
    QCheckBox,
    QSlider,
    QTableView,
    QPlainTextEdit,
    QHeaderView,
)

from donation_media_hub.config import APP_TITLE
from donation_media_hub.ui.style import load_qss
from donation_media_hub.ui.models import QueueTableModel
from donation_media_hub.ui.dialogs import HelpDialog, ask_yes_no
from donation_media_hub.ui.controller import PlayerController
from donation_media_hub.queue_manager import QueueManager
from donation_media_hub.paths import TEMP_DIR


class MainWindow(QMainWindow):
    def __init__(
        self,
        queue_manager: QueueManager,
        config_file: Path,
        state_da_file: Path,
        state_dx_file: Path,
    ) -> None:
        super().__init__()
        self.queue = queue_manager

        self.setWindowTitle(APP_TITLE)
        self.resize(1180, 760)
        self.setStyleSheet(load_qss())

        self._build_ui()
        self._wire()

        # --- controller (SOURCE OF TRUTH) ---
        self.controller = PlayerController(
            queue_manager=self.queue,
            config_file=config_file,
            state_da_file=state_da_file,
            state_dx_file=state_dx_file,
            on_ui_update=self._ui_refresh,
            on_log=self._log,
            on_status_text=self._set_status,
            on_now_playing=self._set_now_playing,
            set_current_in_view=self._select_track_id,
        )

        # ------------------------------------------------------------------
        # RESTORE CONFIG INTO UI (CRITICAL: BLOCK WIDGET SIGNALS)
        # ------------------------------------------------------------------
        cfg = self.controller.config_data

        widgets = (
            self.da_token,
            self.dx_token,
            self.show_tokens,
            self.download_mode,
            self.vol,
        )

        for w in widgets:
            w.blockSignals(True)

        self.da_token.setText(cfg.get("da_token", ""))
        self.dx_token.setText(cfg.get("dx_token", ""))
        self.show_tokens.setChecked(bool(cfg.get("show_tokens", False)))
        self.download_mode.setChecked(bool(cfg.get("download_mode", True)))
        self.vol.setValue(int(float(cfg.get("volume", 0.7)) * 100))

        for w in widgets:
            w.blockSignals(False)

        self._apply_show_tokens()

        # ------------------------------------------------------------------
        # MODEL
        # ------------------------------------------------------------------
        self.model = QueueTableModel(self.queue)
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setSelectionMode(QTableView.SingleSelection)
        self.table.setAlternatingRowColors(False)
        self.table.horizontalHeader().setStretchLastSection(True)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)  # Src
        header.setSectionResizeMode(1, QHeaderView.Stretch)  # Title
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.setColumnWidth(2, 140)

        # ------------------------------------------------------------------
        # TIMERS
        # ------------------------------------------------------------------
        self.ev_timer = QTimer(self)
        self.ev_timer.timeout.connect(self.controller.process_ui_events)
        self.ev_timer.start(120)

        self.wd_timer = QTimer(self)
        self.wd_timer.timeout.connect(self.controller.watchdog)
        self.wd_timer.start(450)

        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self._ui_refresh)
        self.refresh_timer.start(650)

        # initial draw
        self._ui_refresh()

    # -------------------- UI --------------------
    def _build_ui(self) -> None:
        root = QWidget(self)
        self.setCentralWidget(root)

        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Sidebar
        self.sidebar = QFrame(objectName="Sidebar")
        self.sidebar.setFixedWidth(320)
        s = QVBoxLayout(self.sidebar)
        s.setContentsMargins(18, 18, 18, 18)
        s.setSpacing(12)

        brand = QLabel("Donation Media Hub")
        brand.setStyleSheet("font-size:14pt;font-weight:800;color:#fff;")
        s.addWidget(brand)

        # Tokens card
        card = QFrame(objectName="Card")
        c = QVBoxLayout(card)
        c.setContentsMargins(14, 14, 14, 14)
        c.setSpacing(10)

        c.addWidget(QLabel("Tokens / Mode", objectName="Sub"))

        self.da_token = QLineEdit()
        self.da_token.setPlaceholderText("DonationAlerts token")
        self.dx_token = QLineEdit()
        self.dx_token.setPlaceholderText("DonateX token")

        row_da = QHBoxLayout()
        row_da.addWidget(self.da_token, 1)
        self.btn_help_da = QPushButton("?")
        self.btn_help_da.setFixedWidth(36)
        row_da.addWidget(self.btn_help_da)

        row_dx = QHBoxLayout()
        row_dx.addWidget(self.dx_token, 1)
        self.btn_help_dx = QPushButton("?")
        self.btn_help_dx.setFixedWidth(36)
        row_dx.addWidget(self.btn_help_dx)

        c.addLayout(row_da)
        c.addLayout(row_dx)

        self.show_tokens = QCheckBox("Show tokens")
        self.download_mode = QCheckBox("Download & Play (mp3)")
        c.addWidget(self.show_tokens)
        c.addWidget(self.download_mode)

        row_ctrl = QHBoxLayout()
        self.btn_start = QPushButton("Start")
        self.btn_start.setObjectName("Primary")
        self.btn_stop = QPushButton("Stop")
        row_ctrl.addWidget(self.btn_start, 1)
        row_ctrl.addWidget(self.btn_stop, 1)
        c.addLayout(row_ctrl)

        row_maint = QHBoxLayout()
        self.btn_temp = QPushButton("Temp")
        self.btn_clear = QPushButton("Clear")
        self.btn_clear.setObjectName("Danger")
        row_maint.addWidget(self.btn_temp, 1)
        row_maint.addWidget(self.btn_clear, 1)
        c.addLayout(row_maint)

        s.addWidget(card)

        # Volume card
        card2 = QFrame(objectName="Card2")
        v = QVBoxLayout(card2)
        v.setContentsMargins(14, 14, 14, 14)
        v.setSpacing(10)

        v.addWidget(QLabel("Volume", objectName="Sub"))
        self.vol = QSlider(Qt.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setValue(70)
        v.addWidget(self.vol)

        self.status = QLabel("Idle", objectName="Sub")
        v.addWidget(self.status)

        s.addWidget(card2)
        s.addStretch(1)

        outer.addWidget(self.sidebar)

        # Main area
        main = QVBoxLayout()
        main.setContentsMargins(18, 18, 18, 18)
        main.setSpacing(12)

        # Now Playing card
        np = QFrame(objectName="Card")
        npl = QVBoxLayout(np)
        npl.setContentsMargins(16, 16, 16, 16)
        npl.setSpacing(6)

        self.now_title = QLabel("—", objectName="Title")
        self.now_sub = QLabel("Queue empty", objectName="Sub")
        npl.addWidget(QLabel("Now Playing", objectName="Sub"))
        npl.addWidget(self.now_title)
        npl.addWidget(self.now_sub)

        # Transport row
        tr = QHBoxLayout()
        tr.setSpacing(10)
        self.btn_go_start = QPushButton("⏮")
        self.btn_prev = QPushButton("Prev")
        self.btn_play = QPushButton("Play/Pause")
        self.btn_play.setObjectName("Primary")
        self.btn_next = QPushButton("Next")
        self.btn_skip = QPushButton("Skip")
        tr.addWidget(self.btn_go_start)
        tr.addWidget(self.btn_prev)
        tr.addWidget(self.btn_play)
        tr.addWidget(self.btn_next)
        tr.addWidget(self.btn_skip)
        npl.addLayout(tr)

        main.addWidget(np)

        # Queue table card
        qc = QFrame(objectName="Card")
        qcl = QVBoxLayout(qc)
        qcl.setContentsMargins(16, 16, 16, 16)
        qcl.setSpacing(10)

        qcl.addWidget(QLabel("Queue (double click opens link)", objectName="Sub"))

        self.table = QTableView()
        qcl.addWidget(self.table, 1)

        main.addWidget(qc, 1)

        # Log
        lc = QFrame(objectName="Card2")
        lcl = QVBoxLayout(lc)
        lcl.setContentsMargins(16, 16, 16, 16)
        lcl.setSpacing(10)
        lcl.addWidget(QLabel("Log", objectName="Sub"))
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(600)
        lcl.addWidget(self.log)
        main.addWidget(lc)

        outer.addLayout(main, 1)

    def _wire(self) -> None:
        self.btn_help_da.clicked.connect(self._help_da)
        self.btn_help_dx.clicked.connect(self._help_dx)

        self.show_tokens.toggled.connect(self._apply_show_tokens)
        self.download_mode.toggled.connect(self._save_config)
        self.da_token.textChanged.connect(self._save_config)
        self.dx_token.textChanged.connect(self._save_config)
        self.vol.valueChanged.connect(self._on_volume)

        self.btn_start.clicked.connect(self._start)
        self.btn_stop.clicked.connect(self._stop)

        self.btn_clear.clicked.connect(self._clear_queue)
        self.btn_temp.clicked.connect(self._clear_temp)

        self.btn_go_start.clicked.connect(lambda: self.controller.go_start())
        self.btn_prev.clicked.connect(lambda: self.controller.prev_track())
        self.btn_play.clicked.connect(lambda: self.controller.play_pause())
        self.btn_next.clicked.connect(lambda: self.controller.next_track(auto=False))
        self.btn_skip.clicked.connect(lambda: self.controller.skip_track())

        self.table.doubleClicked.connect(self._open_selected_link)
        self.table.clicked.connect(self._select_current_from_table)

    # -------------------- UI helpers --------------------
    def _log(self, msg: str) -> None:
        self.log.appendPlainText(msg)

    def _set_status(self, text: str) -> None:
        self.status.setText(text)

    def _set_now_playing(self, big: str, small: str) -> None:
        self.now_title.setText(big)
        self.now_sub.setText(small)

    def _ui_refresh(self) -> None:
        if hasattr(self, "model"):
            self.model.refresh()
            self._ensure_selection_visible()

    def _ensure_selection_visible(self) -> None:
        row = self.model.row_of_track_id(self.queue.current_track_id)
        if row >= 0:
            idx = self.model.index(row, 0)
            sel = self.table.selectionModel()
            if sel and not sel.isRowSelected(row, idx.parent()):
                self.table.selectRow(row)
            self.table.scrollTo(idx)

    def _select_track_id(self, track_id: str | None) -> None:
        row = self.model.row_of_track_id(track_id)
        if row >= 0:
            self.table.selectRow(row)

    # -------------------- actions --------------------
    def _apply_show_tokens(self) -> None:
        show = self.show_tokens.isChecked()
        self.da_token.setEchoMode(QLineEdit.Normal if show else QLineEdit.Password)
        self.dx_token.setEchoMode(QLineEdit.Normal if show else QLineEdit.Password)
        self._save_config()

    def _save_config(self) -> None:
        if not hasattr(self, "controller"):
            return
        self.controller.save_config(
            da_token=self.da_token.text(),
            dx_token=self.dx_token.text(),
            show_tokens=self.show_tokens.isChecked(),
            download_mode=self.download_mode.isChecked(),
            volume=float(self.vol.value()) / 100.0,
        )

    def _on_volume(self) -> None:
        if hasattr(self, "controller"):
            self.controller.set_volume(float(self.vol.value()) / 100.0)
            self._save_config()

    def _start(self) -> None:
        self._save_config()
        self.controller.start()

    def _stop(self) -> None:
        self.controller.stop()
        self._save_config()

    def _clear_queue(self) -> None:
        self.controller.clear_queue()

    def _clear_temp(self) -> None:
        self.controller.clear_temp()

    def _select_current_from_table(self) -> None:
        idx = self.table.currentIndex()
        t = self.model.track_at(idx.row())
        if t:
            self.controller.set_current_by_id(t.track_id)

    def _open_selected_link(self) -> None:
        idx = self.table.currentIndex()
        t = self.model.track_at(idx.row())
        if t:
            self.controller.set_current_by_id(t.track_id)
            self.controller.open_current_link()

    # -------------------- help --------------------
    def _help_da(self) -> None:
        HelpDialog(
            self,
            "DonationAlerts — токен",
            "1) Открой страницу\n2) Найди «Секретный токен»\n3) Скопируй и вставь",
            "https://www.donationalerts.com/dashboard/general-settings/account",
        ).exec()

    def _help_dx(self) -> None:
        HelpDialog(
            self,
            "DonateX — токен",
            "1) Открой страницу\n2) «Последние донаты»\n3) В адресной строке token=XXXX\n4) Скопируй XXXX",
            "https://donatex.gg/streamer/dashboard",
        ).exec()

    # -------------------- close --------------------
    def closeEvent(self, event) -> None:
        try:
            has_files = TEMP_DIR.exists() and any(TEMP_DIR.glob("*.mp3"))
            if has_files:
                yes = ask_yes_no(
                    self,
                    "Очистка временных файлов",
                    "Очистить загруженные треки (mp3) из temp папки?",
                )
                if yes:
                    import shutil

                    shutil.rmtree(TEMP_DIR, ignore_errors=True)
                    TEMP_DIR.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        try:
            self._save_config()
        except Exception:
            pass

        try:
            self.controller.close()
        except Exception:
            pass

        event.accept()


def run_qt(
    queue: QueueManager, config_file: Path, state_da_file: Path, state_dx_file: Path
) -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    win = MainWindow(queue, config_file, state_da_file, state_dx_file)
    win.show()
    sys.exit(app.exec())
