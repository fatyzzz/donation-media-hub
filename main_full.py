import json
import os
import re
import shutil
import threading
import time
import queue as thread_queue
import tempfile
import webbrowser
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict

import requests
import tkinter as tk
from tkinter import ttk, messagebox
import sys
from pathlib import Path

def get_app_dir() -> Path:
    if getattr(sys, 'frozen', False):
        # PyInstaller exe/app
        return Path(sys.executable).parent
    else:
        # обычный запуск
        return Path(__file__).parent

APP_DIR = get_app_dir()

# -------------------- OPTIONAL DEP: pygame --------------------
try:
    import pygame
except Exception:
    pygame = None

# ===================== CONFIG =====================
POLL_INTERVAL_SEC = 3

DA_MEDIA_URL = "https://www.donationalerts.com/api/getmediadata"
DX_API_URL = "https://donatex.gg/api/donations/get-donations"
YT_DL_API = "https://yt.butterflynet.work/download/mp3"

CONFIG_FILE = APP_DIR / "config.json"
STATE_DA_FILE = APP_DIR / "state_donationalerts.json"
STATE_DX_FILE = APP_DIR / "state_donatex.json"
QUEUE_FILE = APP_DIR / "queue.json"

TEMP_DIR = Path(tempfile.gettempdir()) / "donation_media_hub_tracks"

QUEUE_LIMIT = 50


# ===================== UTIL =====================
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path, default):
    try:
        if not path.exists():
            return default
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: Path, data) -> None:
    try:
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def parse_iso_ts(ts: str) -> float:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


def sanitize_filename(name: str, max_len: int = 120) -> str:
    name = (name or "").strip()
    name = re.sub(r"[\\/:*?\"<>|\n\r\t]+", "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    if len(name) > max_len:
        name = name[:max_len].rstrip()
    return name or "track"


def is_youtube_url(url: str) -> bool:
    u = (url or "").lower()
    return "youtube.com" in u or "youtu.be" in u


def youtube_oembed_title(url: str, timeout=10) -> Optional[str]:
    try:
        r = requests.get(
            "https://www.youtube.com/oembed",
            params={"url": url, "format": "json"},
            timeout=timeout,
            headers={"user-agent": "DonationMediaHub/1.0"},
        )
        if r.status_code != 200:
            return None
        data = r.json()
        return data.get("title")
    except Exception:
        return None


def ensure_temp_dir() -> None:
    TEMP_DIR.mkdir(parents=True, exist_ok=True)


def human_time_utc(ts: float) -> str:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def jsonp_to_json(text: str) -> Optional[dict]:
    m = re.search(r"\((\{.*\})\)\s*$", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None


# ===================== DATA MODEL =====================
@dataclass
class Track:
    track_id: str
    source: str
    created_ts: float
    url: str
    title: str
    status: str = "queued"  # queued/downloading/ready/playing/paused/played/skipped/failed
    local_path: Optional[str] = None
    error: Optional[str] = None


# ===================== HELP WINDOW =====================
def show_help(parent, title: str, text: str, link: str):
    win = tk.Toplevel(parent)
    win.title(title)
    win.geometry("620x310")
    win.resizable(False, False)
    win.transient(parent)
    win.grab_set()

    frame = ttk.Frame(win, padding=14)
    frame.pack(fill="both", expand=True)

    lbl = ttk.Label(frame, text=text, justify="left", anchor="nw", wraplength=580)
    lbl.pack(fill="x", pady=(0, 12))

    ttk.Label(frame, text="Ссылка (можно кликнуть и/или скопировать):").pack(anchor="w")
    link_row = ttk.Frame(frame)
    link_row.pack(fill="x", pady=(6, 6))

    # clickable label
    link_lbl = ttk.Label(link_row, text=link, foreground="#2a6fdb", cursor="hand2")
    link_lbl.pack(side="left", fill="x", expand=True)
    link_lbl.bind("<Button-1>", lambda _e: webbrowser.open(link))

    # copy button
    def copy_link():
        parent.clipboard_clear()
        parent.clipboard_append(link)

    ttk.Button(link_row, text="Copy", command=copy_link, width=10).pack(side="right", padx=(8, 0))

    ttk.Button(frame, text="OK", command=win.destroy).pack(pady=(12, 0))


# ===================== APP =====================
class DonationMediaHub(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Donation Media Hub — Player")
        self.geometry("1180x720")
        self.minsize(980, 640)

        ensure_temp_dir()

        # ---- thread-safe event queue ----
        self.ui_events: "thread_queue.Queue[dict]" = thread_queue.Queue()

        # ---- config ----
        self.config_data = load_json(CONFIG_FILE, {
            "da_token": "",
            "dx_token": "",
            "show_tokens": False,
            "download_mode": True,
            "volume": 0.70,
            "current_track_id": None,
        })

        # ---- cursors ----
        self.da_state = load_json(STATE_DA_FILE, {"last_media_id": 0})
        self.dx_state = load_json(STATE_DX_FILE, {"last_timestamp": None})

        # ---- queue ----
        q = load_json(QUEUE_FILE, {"tracks": [], "current_track_id": None})
        self.tracks: List[Track] = []
        for t in q.get("tracks", []):
            try:
                self.tracks.append(Track(**t))
            except Exception:
                pass
        self.current_track_id: Optional[str] = q.get("current_track_id") or self.config_data.get("current_track_id")

        # ---- runtime ----
        self.running = False
        self.poll_threads: List[threading.Thread] = []
        self.downloader_thread: Optional[threading.Thread] = None

        # ---- playback ----
        self.audio_ready = False
        self.paused = False
        self._closing = False

        # ---- caches ----
        self.title_cache: Dict[str, str] = {}

        # ---- UI vars ----
        self.da_token_var = tk.StringVar(value=self.config_data.get("da_token", ""))
        self.dx_token_var = tk.StringVar(value=self.config_data.get("dx_token", ""))
        self.status_var = tk.StringVar(value="Idle")
        self.now_playing_big_var = tk.StringVar(value="—")
        self.now_playing_small_var = tk.StringVar(value="Queue empty")
        self.download_mode_var = tk.BooleanVar(value=bool(self.config_data.get("download_mode", True)))
        self.volume_var = tk.DoubleVar(value=float(self.config_data.get("volume", 0.70)))
        self.show_tokens_var = tk.BooleanVar(value=bool(self.config_data.get("show_tokens", False)))

        # ---- styles ----
        self._init_style()

        # ---- UI ----
        self._build_ui()
        self._init_audio()

        # close hook
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # timers
        self.after(120, self._process_ui_events)
        self.after(500, self._refresh_table)
        self.after(350, self._playback_watchdog)

    # ===================== STYLE =====================
    def _init_style(self):
        s = ttk.Style(self)
        # keep native theme but tweak fonts/spacings
        try:
            s.theme_use("clam")
        except Exception:
            pass

        s.configure("PlayerTitle.TLabel", font=("Segoe UI", 16, "bold"))
        s.configure("PlayerSub.TLabel", font=("Segoe UI", 10))
        s.configure("PlayerBtn.TButton", padding=(12, 8))
        s.configure("Small.TButton", padding=(10, 6))
        s.configure("Danger.TButton", padding=(10, 6))
        s.configure("Card.TFrame", padding=10)
        s.configure("NowPlaying.TFrame", padding=14)

    # ===================== AUDIO =====================
    def _init_audio(self):
        if pygame is None:
            self.audio_ready = False
            self._log("⚠ pygame не найден. Установи: pip install pygame")
            return
        try:
            pygame.mixer.init()
            self.audio_ready = True
            pygame.mixer.music.set_volume(self.volume_var.get())
        except Exception as e:
            self.audio_ready = False
            self._log(f"⚠ Не удалось инициализировать звук (pygame): {e}")

    def _set_volume(self, *_):
        v = float(self.volume_var.get())
        if self.audio_ready:
            try:
                pygame.mixer.music.set_volume(v)
            except Exception:
                pass
        self._save_config()

    # ===================== UI BUILD =====================
    def _build_ui(self):
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)

        self.nb = ttk.Notebook(root)
        self.nb.pack(fill="both", expand=True)

        self.tab_player = ttk.Frame(self.nb, padding=0)
        self.tab_settings = ttk.Frame(self.nb, padding=12)

        self.nb.add(self.tab_player, text="🎵 Player")
        self.nb.add(self.tab_settings, text="⚙️ Settings")

        self._build_player_tab()
        self._build_settings_tab()

    # -------------------- PLAYER TAB --------------------
    def _build_player_tab(self):
        outer = ttk.Frame(self.tab_player, padding=10)
        outer.pack(fill="both", expand=True)

        # NOW PLAYING CARD
        now_card = ttk.Frame(outer, style="NowPlaying.TFrame")
        now_card.pack(fill="x")

        ttk.Label(now_card, text="Now Playing", style="PlayerSub.TLabel").pack(anchor="w")
        ttk.Label(now_card, textvariable=self.now_playing_big_var, style="PlayerTitle.TLabel").pack(anchor="w", pady=(2, 0))
        ttk.Label(now_card, textvariable=self.now_playing_small_var, style="PlayerSub.TLabel").pack(anchor="w", pady=(2, 0))

        # TRANSPORT BAR
        transport = ttk.Frame(outer, padding=(0, 10, 0, 6))
        transport.pack(fill="x")

        # left small actions
        left = ttk.Frame(transport)
        left.pack(side="left")
        ttk.Button(left, text="🗑 Clear queue", style="Small.TButton", command=self.clear_queue).pack(side="left", padx=(0, 8))
        ttk.Button(left, text="🧹 Clear temp", style="Small.TButton", command=self.clear_temp_folder).pack(side="left")

        # center player buttons
        center = ttk.Frame(transport)
        center.pack(side="left", fill="x", expand=True)

        center_inner = ttk.Frame(center)
        center_inner.pack(anchor="center")

        ttk.Button(center_inner, text="⏮", width=4, style="PlayerBtn.TButton", command=self.go_to_start).pack(side="left", padx=4)
        ttk.Button(center_inner, text="⏮ Prev", style="PlayerBtn.TButton", command=self.prev_track).pack(side="left", padx=4)
        ttk.Button(center_inner, text="⏯ Play/Pause", style="PlayerBtn.TButton", command=self.play_pause_toggle).pack(side="left", padx=4)
        ttk.Button(center_inner, text="Next ⏭", style="PlayerBtn.TButton", command=self.next_track).pack(side="left", padx=4)
        ttk.Button(center_inner, text="⏩ Skip", style="PlayerBtn.TButton", command=self.skip_track).pack(side="left", padx=4)

        # right status + volume
        right = ttk.Frame(transport)
        right.pack(side="right")

        ttk.Label(right, textvariable=self.status_var).pack(anchor="e")
        vol_row = ttk.Frame(right)
        vol_row.pack(anchor="e", pady=(6, 0))
        ttk.Label(vol_row, text="🔊").pack(side="left", padx=(0, 6))
        ttk.Scale(vol_row, from_=0.0, to=1.0, variable=self.volume_var, command=self._set_volume, length=220).pack(side="left")

        # QUEUE TABLE
        queue_wrap = ttk.Frame(outer)
        queue_wrap.pack(fill="both", expand=True, pady=(6, 0))

        ttk.Label(queue_wrap, text="Queue (double click = open link)", style="PlayerSub.TLabel").pack(anchor="w")

        table_frame = ttk.Frame(queue_wrap)
        table_frame.pack(fill="both", expand=True, pady=(6, 0))

        columns = ("num", "source", "title", "status", "created", "url")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=16)
        self.tree.heading("num", text="#")
        self.tree.heading("source", text="Src")
        self.tree.heading("title", text="Title")
        self.tree.heading("status", text="Status")
        self.tree.heading("created", text="Created (UTC)")
        self.tree.heading("url", text="URL")

        self.tree.column("num", width=44, anchor="center")
        self.tree.column("source", width=60, anchor="center")
        self.tree.column("title", width=420)
        self.tree.column("status", width=90, anchor="center")
        self.tree.column("created", width=170)
        self.tree.column("url", width=340)

        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)

        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self.tree.bind("<Double-1>", self._on_row_double_click)
        self.tree.bind("<<TreeviewSelect>>", self._on_row_select_only)

        # LOG (compact)
        log_frame = ttk.Frame(outer, padding=(0, 8, 0, 0))
        log_frame.pack(fill="both")

        ttk.Label(log_frame, text="Log", style="PlayerSub.TLabel").pack(anchor="w")
        self.log_text = tk.Text(log_frame, height=7)
        self.log_text.pack(fill="x")

        self._refresh_table()

    # -------------------- SETTINGS TAB --------------------
    def _build_settings_tab(self):
        outer = ttk.Frame(self.tab_settings)
        outer.pack(fill="both", expand=True)

        # Tokens card
        tok = ttk.LabelFrame(outer, text="Tokens", padding=12)
        tok.pack(fill="x", pady=(0, 10))

        row1 = ttk.Frame(tok)
        row1.pack(fill="x", pady=4)
        ttk.Label(row1, text="DonationAlerts token:", width=20).pack(side="left")
        self.da_entry = ttk.Entry(row1, textvariable=self.da_token_var)
        self.da_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(row1, text="❓", width=3, command=self._help_da).pack(side="left", padx=6)

        row2 = ttk.Frame(tok)
        row2.pack(fill="x", pady=4)
        ttk.Label(row2, text="DonateX token:", width=20).pack(side="left")
        self.dx_entry = ttk.Entry(row2, textvariable=self.dx_token_var)
        self.dx_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(row2, text="❓", width=3, command=self._help_dx).pack(side="left", padx=6)

        row3 = ttk.Frame(tok)
        row3.pack(fill="x", pady=(8, 0))
        ttk.Checkbutton(row3, text="Показать токены", variable=self.show_tokens_var, command=self._toggle_token_visibility).pack(side="left")
        ttk.Button(row3, text="💾 Save", command=self._save_config).pack(side="left", padx=10)

        self._toggle_token_visibility()

        # Mode card
        mode = ttk.LabelFrame(outer, text="Playback mode", padding=12)
        mode.pack(fill="x", pady=(0, 10))

        ttk.Checkbutton(
            mode,
            text="Скачивать MP3 и проигрывать (через API). Если выключить — просто открывать ссылку в браузере.",
            variable=self.download_mode_var,
            command=self._save_config
        ).pack(anchor="w")

        # Polling card
        pol = ttk.LabelFrame(outer, text="Polling", padding=12)
        pol.pack(fill="x", pady=(0, 10))

        r = ttk.Frame(pol)
        r.pack(fill="x")
        ttk.Button(r, text="▶ Start polling", command=self.start).pack(side="left", padx=(0, 8))
        ttk.Button(r, text="⏹ Stop", command=self.stop).pack(side="left")

        ttk.Label(pol, text="Подсказка: можно оставить один токен пустым — второй сервис будет работать.", padding=(0, 8, 0, 0)).pack(anchor="w")

        # Extra actions
        extra = ttk.LabelFrame(outer, text="Maintenance", padding=12)
        extra.pack(fill="x")

        rr = ttk.Frame(extra)
        rr.pack(fill="x")
        ttk.Button(rr, text="🗑 Clear queue", command=self.clear_queue).pack(side="left", padx=(0, 8))
        ttk.Button(rr, text="🧹 Clear temp", command=self.clear_temp_folder).pack(side="left")

    # ===================== HELP =====================
    def _help_da(self):
        show_help(
            self,
            "DonationAlerts — токен",
            "Как получить токен DonationAlerts:\n\n"
            "1) Открой страницу ниже\n"
            "2) Найди «Секретный токен»\n"
            "3) Скопируй токен и вставь в поле",
            "https://www.donationalerts.com/dashboard/general-settings/account",
        )

    def _help_dx(self):
        show_help(
            self,
            "DonateX — токен",
            "Как получить токен DonateX:\n\n"
            "1) Открой страницу ниже\n"
            "2) Открой «Последние донаты» в окне\n"
            "3) В адресной строке будет ...token=XXXX\n"
            "4) Скопируй то, что после token= и вставь в поле",
            "https://donatex.gg/streamer/dashboard",
        )

    def _toggle_token_visibility(self):
        show = bool(self.show_tokens_var.get())
        try:
            self.da_entry.configure(show="" if show else "*")
            self.dx_entry.configure(show="" if show else "*")
        except Exception:
            pass
        self._save_config()

    # ===================== LOG =====================
    def _log(self, msg: str):
        try:
            self.log_text.insert("end", msg + "\n")
            self.log_text.see("end")
        except Exception:
            pass

    # ===================== SAVE =====================
    def _save_config(self):
        self.config_data["da_token"] = self.da_token_var.get().strip()
        self.config_data["dx_token"] = self.dx_token_var.get().strip()
        self.config_data["show_tokens"] = bool(self.show_tokens_var.get())
        self.config_data["download_mode"] = bool(self.download_mode_var.get())
        self.config_data["volume"] = float(self.volume_var.get())
        self.config_data["current_track_id"] = self.current_track_id
        save_json(CONFIG_FILE, self.config_data)

    def _save_queue(self):
        save_json(QUEUE_FILE, {
            "tracks": [asdict(t) for t in self.tracks],
            "current_track_id": self.current_track_id
        })

    # ===================== QUEUE / TABLE =====================
    def _refresh_table(self):
        # clear
        for iid in self.tree.get_children():
            self.tree.delete(iid)

        self.tracks.sort(key=lambda t: t.created_ts)
        current = self.current_track_id

        for idx, t in enumerate(self.tracks, start=1):
            values = (
                idx,
                t.source,
                t.title,
                t.status,
                human_time_utc(t.created_ts),
                t.url
            )
            iid = t.track_id
            self.tree.insert("", "end", iid=iid, values=values)

        # keep selection on current (but selection DOES NOT autoplay)
        if current and self._get_track(current):
            try:
                self.tree.selection_set(current)
                self.tree.see(current)
            except Exception:
                pass

        self._update_now_playing_labels()
        self._save_queue()
        self.after(650, self._refresh_table)

    def _on_row_double_click(self, _evt=None):
        tid = self._selected_track_id()
        if not tid:
            return
        t = self._get_track(tid)
        if not t:
            return
        webbrowser.open(t.url)

    def _on_row_select_only(self, _evt=None):
        # IMPORTANT: selecting row must NOT autoplay
        tid = self._selected_track_id()
        if not tid:
            return
        self.current_track_id = tid
        self._save_config()
        self._save_queue()
        self._update_now_playing_labels()

    def _selected_track_id(self) -> Optional[str]:
        sel = self.tree.selection()
        if not sel:
            return None
        return sel[0]

    def clear_queue(self):
        # stop playback
        self._stop_audio_only()

        # delete downloaded files
        for t in self.tracks:
            if t.local_path and Path(t.local_path).exists():
                try:
                    Path(t.local_path).unlink(missing_ok=True)
                except Exception:
                    pass

        self.tracks.clear()
        self.current_track_id = None
        self.paused = False
        self._save_queue()
        self._save_config()
        self.status_var.set("Queue cleared")
        self._update_now_playing_labels()
        self._log("🗑 queue cleared")

    def _trim_queue(self, limit: int = QUEUE_LIMIT):
        """
        Keep last `limit` tracks while trying to keep current track.
        """
        if len(self.tracks) <= limit:
            return

        self.tracks.sort(key=lambda t: t.created_ts)

        keep_current = self.current_track_id
        # Prefer keeping the newest tracks.
        # Remove from the oldest side, but never remove current/playing/paused if possible.
        def removable(t: Track) -> bool:
            if keep_current and t.track_id == keep_current:
                return False
            if t.status in ("playing", "paused"):
                return False
            return True

        while len(self.tracks) > limit:
            # find first removable (oldest)
            idx = next((i for i, t in enumerate(self.tracks) if removable(t)), None)
            if idx is None:
                break
            victim = self.tracks.pop(idx)
            if victim.local_path and Path(victim.local_path).exists():
                try:
                    Path(victim.local_path).unlink(missing_ok=True)
                except Exception:
                    pass

    # ===================== TRACK ACCESS =====================
    def _get_track(self, track_id: str) -> Optional[Track]:
        for t in self.tracks:
            if t.track_id == track_id:
                return t
        return None

    def _current_index(self) -> int:
        if not self.current_track_id:
            return -1
        for i, t in enumerate(self.tracks):
            if t.track_id == self.current_track_id:
                return i
        return -1

    def _ensure_current_track(self):
        if self.current_track_id and self._get_track(self.current_track_id):
            return
        if self.tracks:
            self.current_track_id = self.tracks[0].track_id
        else:
            self.current_track_id = None
        self._save_config()

    # ===================== POLLING CONTROL =====================
    def start(self):
        if self.running:
            self.status_var.set("Already running")
            return

        da = self.da_token_var.get().strip()
        dx = self.dx_token_var.get().strip()
        if not da and not dx:
            messagebox.showerror("Ошибка", "Вставь хотя бы один токен (DA или DonateX)")
            return

        self.running = True
        self.status_var.set("Polling started")
        self._log("▶ Polling started")

        self.poll_threads = []
        if da:
            th = threading.Thread(target=self._poll_da_loop, daemon=True)
            th.start()
            self.poll_threads.append(th)
        if dx:
            th = threading.Thread(target=self._poll_dx_loop, daemon=True)
            th.start()
            self.poll_threads.append(th)

        if self.downloader_thread is None or not self.downloader_thread.is_alive():
            self.downloader_thread = threading.Thread(target=self._download_worker_loop, daemon=True)
            self.downloader_thread.start()

        self._ensure_current_track()

        # Autoplay first track if exists:
        # - if browser mode: open and move next automatically
        # - if download mode: play when ready (download_done handler)
        if self.tracks:
            if not self.download_mode_var.get():
                self.play_current(force=True)
            else:
                # If already ready downloaded, start immediately
                t = self._get_track(self.current_track_id) if self.current_track_id else None
                if t and t.local_path and Path(t.local_path).exists():
                    self.play_current(force=True)
                else:
                    self._log("⏳ waiting for first track download...")

        self._update_now_playing_labels()

        # switch to player tab
        try:
            self.nb.select(self.tab_player)
        except Exception:
            pass

    def stop(self):
        self.running = False
        self.status_var.set("Stopped")
        self._log("⏹ Stopped")

    # ===================== POLLER: DonationAlerts =====================
    def _poll_da_loop(self):
        while self.running:
            try:
                self._fetch_da()
            except Exception as e:
                self.ui_events.put({"type": "log", "msg": f"❌ DA error: {e}"})
            time.sleep(POLL_INTERVAL_SEC)

    def _fetch_da(self):
        token = self.da_token_var.get().strip()
        if not token:
            return

        ts_ms = int(time.time() * 1000)
        params = {"callback": f"jQuery{ts_ms}", "token": token, "_": ts_ms}

        r = requests.get(
            DA_MEDIA_URL,
            params=params,
            timeout=15,
            headers={"user-agent": "DonationMediaHub/1.0"},
        )
        r.raise_for_status()

        data = jsonp_to_json(r.text)
        if not data:
            return

        media_list = data.get("media", []) or []

        last_media_id = int(self.da_state.get("last_media_id", 0) or 0)
        new_max = last_media_id

        for media in media_list:
            try:
                media_id = int(media.get("media_id", 0))
            except Exception:
                continue
            if media_id <= last_media_id:
                continue

            if media.get("sub_type") != "youtube":
                continue

            add_raw = media.get("additional_data") or ""
            try:
                add = json.loads(add_raw) if add_raw else {}
            except Exception:
                add = {}
            url = add.get("url")
            if not url:
                continue

            title = media.get("title") or "YouTube"
            created_str = media.get("date_created")
            created_ts = time.time()
            if created_str:
                try:
                    dt = datetime.strptime(created_str, "%Y-%m-%d %H:%M:%S")
                    created_ts = dt.replace(tzinfo=timezone.utc).timestamp()
                except Exception:
                    created_ts = time.time()

            t = self._make_track("DA", float(created_ts), str(url), str(title), stable_suffix=str(media_id))
            self.ui_events.put({"type": "new_track", "track": asdict(t)})

            if media_id > new_max:
                new_max = media_id

        if new_max != last_media_id:
            self.da_state["last_media_id"] = new_max
            save_json(STATE_DA_FILE, self.da_state)

    # ===================== POLLER: DonateX =====================
    def _poll_dx_loop(self):
        while self.running:
            try:
                self._fetch_dx()
            except Exception as e:
                self.ui_events.put({"type": "log", "msg": f"❌ DonateX error: {e}"})
            time.sleep(POLL_INTERVAL_SEC)

    def _fetch_dx(self):
        token = self.dx_token_var.get().strip()
        if not token:
            return

        headers = {
            "accept": "application/json",
            "x-external-token": token,
            "user-agent": "DonationMediaHub/1.0",
        }
        params = {"skip": 0, "take": 20, "hideTest": "true", "withAi": "true"}

        r = requests.get(DX_API_URL, headers=headers, params=params, timeout=15)
        r.raise_for_status()

        data = r.json()
        donations = data.get("donations", []) or []
        if not donations:
            return

        donations.sort(key=lambda d: parse_iso_ts(d.get("timestamp", now_iso())))
        last_ts = self.dx_state.get("last_timestamp")

        updated_last_ts = last_ts
        for d in donations:
            ts_str = d.get("timestamp")
            if not ts_str:
                continue
            ts = parse_iso_ts(ts_str)
            if last_ts and ts <= float(last_ts):
                continue

            url = d.get("musicLink")
            if not url:
                continue

            if url in self.title_cache:
                title = self.title_cache[url]
            else:
                title = youtube_oembed_title(url) if is_youtube_url(url) else None
                title = title or "Track"
                self.title_cache[url] = title

            stable_suffix = d.get("id") or ts_str
            t = self._make_track("DX", float(ts), str(url), str(title), stable_suffix=str(stable_suffix))
            self.ui_events.put({"type": "new_track", "track": asdict(t)})

            updated_last_ts = ts

        if updated_last_ts and updated_last_ts != last_ts:
            self.dx_state["last_timestamp"] = float(updated_last_ts)
            save_json(STATE_DX_FILE, self.dx_state)

    # ===================== TRACK CREATE / DEDUPE =====================
    def _make_track(self, source: str, created_ts: float, url: str, title: str, stable_suffix: str) -> Track:
        tid = f"{source}:{stable_suffix}"
        return Track(track_id=tid, source=source, created_ts=created_ts, url=url, title=title, status="queued")

    def _append_track_if_new(self, t: Track) -> bool:
        if any(x.track_id == t.track_id for x in self.tracks):
            return False

        for x in self.tracks:
            if x.url == t.url and abs(x.created_ts - t.created_ts) <= 2.0:
                return False

        self.tracks.append(t)
        self.tracks.sort(key=lambda z: z.created_ts)
        self._trim_queue(QUEUE_LIMIT)
        self._ensure_current_track()
        self._save_queue()
        return True

    # ===================== UI EVENTS =====================
    def _process_ui_events(self):
        try:
            while True:
                ev = self.ui_events.get_nowait()
                et = ev.get("type")

                if et == "log":
                    self._log(ev.get("msg", ""))

                elif et == "new_track":
                    try:
                        t = Track(**ev["track"])
                        if self._append_track_if_new(t):
                            self.status_var.set(f"Queue: {len(self.tracks)}")
                            self._log(f"➕ NEW [{t.source}] {t.title} — {t.url}")

                            # if nothing selected -> select first
                            if not self.current_track_id:
                                self.current_track_id = t.track_id
                                self._save_config()

                            # autoplay behavior:
                            # - browser mode: if nothing playing, open immediately
                            # - download mode: start when ready (download_done handler)
                            if not self.download_mode_var.get():
                                if not self._is_playing_anything():
                                    self.play_current(force=True)

                    except Exception as e:
                        self._log(f"❌ track parse error: {e}")

                elif et == "track_status":
                    tid = ev.get("track_id")
                    t = self._get_track(tid) if tid else None
                    if t:
                        t.status = ev.get("status", t.status) or t.status
                        if ev.get("error"):
                            t.error = ev["error"]
                        self._save_queue()
                        self._update_now_playing_labels()

                elif et == "download_done":
                    tid = ev.get("track_id")
                    path = ev.get("path")
                    t = self._get_track(tid) if tid else None
                    if t:
                        t.local_path = path
                        if t.status not in ("playing", "paused"):
                            t.status = "ready"
                        self._save_queue()

                        # AUTO START: start only if this is current track and nothing is playing
                        if t.track_id == self.current_track_id and self.download_mode_var.get() and not self._is_playing_anything():
                            self.play_current(force=True)

                else:
                    pass

        except thread_queue.Empty:
            pass

        self.after(120, self._process_ui_events)

    # ===================== DOWNLOADER =====================
    def _download_worker_loop(self):
        while True:
            time.sleep(0.30)

            if not self.running:
                continue
            if not self.download_mode_var.get():
                continue

            self._ensure_current_track()
            idx = self._current_index()
            if idx < 0:
                continue

            targets = []
            if 0 <= idx < len(self.tracks):
                targets.append(self.tracks[idx])
            if 0 <= idx + 1 < len(self.tracks):
                targets.append(self.tracks[idx + 1])

            for t in targets:
                if not is_youtube_url(t.url):
                    continue
                if t.local_path and Path(t.local_path).exists():
                    continue
                if t.status in ("downloading", "playing", "paused"):
                    continue
                self._download_track(t)

    def _download_track(self, t: Track):
        self.ui_events.put({"type": "track_status", "track_id": t.track_id, "status": "downloading"})
        try:
            ensure_temp_dir()

            title = sanitize_filename(t.title)
            base = f"{title}.mp3"
            out_path = TEMP_DIR / base
            if out_path.exists():
                out_path = TEMP_DIR / f"{title}__{int(t.created_ts)}.mp3"

            r = requests.get(
                YT_DL_API,
                params={"url": t.url},
                timeout=40,
                headers={"user-agent": "DonationMediaHub/1.0"},
            )
            r.raise_for_status()

            with out_path.open("wb") as f:
                f.write(r.content)

            self.ui_events.put({"type": "download_done", "track_id": t.track_id, "path": str(out_path)})
            self.ui_events.put({"type": "track_status", "track_id": t.track_id, "status": "ready"})
            self.ui_events.put({"type": "log", "msg": f"✅ downloaded: {out_path.name}"})

        except Exception as e:
            self.ui_events.put({"type": "track_status", "track_id": t.track_id, "status": "failed", "error": str(e)})
            self.ui_events.put({"type": "log", "msg": f"❌ download failed: {t.title} — {e}"})

    # ===================== PLAYBACK CORE =====================
    def _is_playing_anything(self) -> bool:
        if not self.audio_ready:
            return False
        try:
            return bool(pygame.mixer.music.get_busy())
        except Exception:
            return False

    def _stop_audio_only(self):
        if self.audio_ready:
            try:
                pygame.mixer.music.stop()
            except Exception:
                pass
        self.paused = False

    def _mark_current_as(self, status: str):
        idx = self._current_index()
        if idx < 0:
            return
        cur = self.tracks[idx]
        cur.status = status
        self._save_queue()

    def _update_now_playing_labels(self):
        if not self.tracks:
            self.now_playing_big_var.set("—")
            self.now_playing_small_var.set("Queue empty")
            return

        self._ensure_current_track()
        t = self._get_track(self.current_track_id) if self.current_track_id else None
        if not t:
            self.now_playing_big_var.set("—")
            self.now_playing_small_var.set("Queue empty")
            return

        self.now_playing_big_var.set(f"[{t.source}] {t.title}")
        extra = f"Status: {t.status} · {human_time_utc(t.created_ts)}"
        if t.local_path and Path(t.local_path).exists():
            extra += f" · file: {Path(t.local_path).name}"
        self.now_playing_small_var.set(extra)

    def play_current(self, force: bool = False):
        """
        force=True means user pressed play/next/prev/etc.
        If force=False we do conservative behavior.
        """
        self._ensure_current_track()
        t = self._get_track(self.current_track_id) if self.current_track_id else None
        if not t:
            self._update_now_playing_labels()
            return

        # browser mode
        if not self.download_mode_var.get():
            webbrowser.open(t.url)
            self._log(f"🌐 opened in browser: [{t.source}] {t.title}")
            t.status = "played"
            self._save_queue()
            # auto-advance to next
            self.next_track(auto=True)
            return

        # download mode
        if not self.audio_ready:
            messagebox.showerror("Audio error", "Нет звука: установи pygame и перезапусти.\n\npip install pygame")
            return

        if not t.local_path or not Path(t.local_path).exists():
            # don’t spam if not force
            if force:
                self._log(f"⏳ waiting for download: {t.title}")
            t.status = "queued"
            self._save_queue()
            self._update_now_playing_labels()
            return

        try:
            # stop old playback
            self._stop_audio_only()

            pygame.mixer.music.load(t.local_path)
            pygame.mixer.music.set_volume(float(self.volume_var.get()))
            pygame.mixer.music.play()

            t.status = "playing"
            self.paused = False
            self._save_queue()
            self._update_now_playing_labels()
            self.status_var.set(f"Playing ({len(self.tracks)} in queue)")
            self._log(f"▶ playing: [{t.source}] {Path(t.local_path).name}")

            self._cleanup_temp_keep_window()

        except Exception as e:
            t.status = "failed"
            t.error = str(e)
            self._save_queue()
            self._log(f"❌ play error: {e}")

    def play_pause_toggle(self):
        """
        If playing -> pause.
        If paused -> resume.
        If not playing -> play current.
        """
        if not self.download_mode_var.get():
            # In browser mode: "Play" just opens the current link
            self.play_current(force=True)
            return

        if not self.audio_ready:
            self.play_current(force=True)
            return

        t = self._get_track(self.current_track_id) if self.current_track_id else None
        if not t:
            return

        try:
            if self._is_playing_anything() and not self.paused:
                pygame.mixer.music.pause()
                self.paused = True
                t.status = "paused"
                self._log("⏸ pause")
            elif self.paused:
                pygame.mixer.music.unpause()
                self.paused = False
                t.status = "playing"
                self._log("⏯ resume")
            else:
                # not busy -> start
                self.play_current(force=True)
                return

            self._save_queue()
            self._update_now_playing_labels()

        except Exception as e:
            self._log(f"❌ play/pause error: {e}")

    # ===================== NEXT/PREV/SKIP/START =====================
    def next_track(self, auto: bool = False):
        self._ensure_current_track()
        idx = self._current_index()
        if idx < 0:
            return

        # mark previous track correctly
        cur = self.tracks[idx]
        if cur.status in ("playing", "paused"):
            cur.status = "played" if auto else "skipped"
        elif cur.status not in ("played", "skipped", "failed"):
            cur.status = "played" if auto else "skipped"

        self._stop_audio_only()

        if idx + 1 >= len(self.tracks):
            self._save_queue()
            self._cleanup_temp_keep_window()
            self.status_var.set("End of queue")
            self._update_now_playing_labels()
            self._log("ℹ end of queue")
            return

        self.current_track_id = self.tracks[idx + 1].track_id
        self._save_config()
        self._save_queue()
        self.play_current(force=True)

    def prev_track(self):
        self._ensure_current_track()
        idx = self._current_index()
        if idx <= 0:
            self._log("ℹ no previous track")
            return

        # mark current as played (user goes back)
        cur = self.tracks[idx]
        if cur.status in ("playing", "paused"):
            cur.status = "played"
        self._stop_audio_only()

        self.current_track_id = self.tracks[idx - 1].track_id
        self._save_config()
        self._save_queue()
        self.play_current(force=True)

    def skip_track(self):
        self._ensure_current_track()
        idx = self._current_index()
        if idx < 0:
            return
        t = self.tracks[idx]
        t.status = "skipped"
        self._save_queue()
        self._stop_audio_only()
        self._log(f"⏩ skipped: [{t.source}] {t.title}")
        self.next_track(auto=False)

    def go_to_start(self):
        if not self.tracks:
            return
        # mark current if playing
        idx = self._current_index()
        if idx >= 0:
            cur = self.tracks[idx]
            if cur.status in ("playing", "paused"):
                cur.status = "played"
        self._stop_audio_only()
        self.current_track_id = self.tracks[0].track_id
        self._save_config()
        self._save_queue()
        self.play_current(force=True)
        # scroll to top
        try:
            first = self.tracks[0].track_id
            self.tree.see(first)
        except Exception:
            pass

    # ===================== WATCHDOG =====================
    def _playback_watchdog(self):
        try:
            if self.running and self.download_mode_var.get() and self.audio_ready:
                if not self.paused and self.current_track_id:
                    busy = False
                    try:
                        busy = bool(pygame.mixer.music.get_busy())
                    except Exception:
                        busy = False

                    if not busy:
                        # if current is playing -> ended naturally
                        idx = self._current_index()
                        if idx >= 0:
                            cur = self.tracks[idx]
                            if cur.status == "playing":
                                cur.status = "played"
                                self._save_queue()
                                self._cleanup_temp_keep_window()
                                self.next_track(auto=True)
        finally:
            self.after(350, self._playback_watchdog)

    # ===================== TEMP CLEANUP =====================
    def _cleanup_temp_keep_window(self):
        """
        Keep only prev/current/next mp3 files on disk.
        """
        try:
            if not TEMP_DIR.exists():
                return
            self._ensure_current_track()
            idx = self._current_index()
            if idx < 0:
                return

            keep_ids = set()
            if idx - 1 >= 0:
                keep_ids.add(self.tracks[idx - 1].track_id)
            keep_ids.add(self.tracks[idx].track_id)
            if idx + 1 < len(self.tracks):
                keep_ids.add(self.tracks[idx + 1].track_id)

            keep_paths = set()
            for t in self.tracks:
                if t.track_id in keep_ids and t.local_path and Path(t.local_path).exists():
                    keep_paths.add(str(Path(t.local_path).resolve()))

            for p in TEMP_DIR.glob("*.mp3"):
                rp = str(p.resolve())
                if rp not in keep_paths:
                    try:
                        p.unlink(missing_ok=True)
                    except Exception:
                        pass

            for t in self.tracks:
                if t.local_path and str(Path(t.local_path).resolve()) not in keep_paths:
                    if not Path(t.local_path).exists():
                        t.local_path = None

            self._save_queue()
        except Exception:
            pass

    def clear_temp_folder(self):
        self._stop_audio_only()

        try:
            if TEMP_DIR.exists():
                shutil.rmtree(TEMP_DIR, ignore_errors=True)
            ensure_temp_dir()
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось очистить папку: {e}")
            return

        for t in self.tracks:
            t.local_path = None
            if t.status in ("ready", "playing", "paused"):
                t.status = "queued"

        self.paused = False
        self._save_queue()
        self._log("🧹 temp folder cleared")
        self.status_var.set("Temp cleared")
        self._update_now_playing_labels()

    # ===================== CLOSE =====================
    def _on_close(self):
        if self._closing:
            return
        self._closing = True

        self.running = False

        # IMPORTANT: show dialog BEFORE destroying window
        def ask_and_finish():
            try:
                if TEMP_DIR.exists() and any(TEMP_DIR.glob("*.mp3")):
                    yes = messagebox.askyesno(
                        "Очистка временных файлов",
                        "Очистить оставшиеся загруженные треки?\n\n(Да = удалить mp3 из temp папки)",
                        parent=self
                    )
                    if yes:
                        try:
                            shutil.rmtree(TEMP_DIR, ignore_errors=True)
                        except Exception:
                            pass
                        ensure_temp_dir()
            except Exception:
                pass

            self._save_config()
            self._save_queue()
            save_json(STATE_DA_FILE, self.da_state)
            save_json(STATE_DX_FILE, self.dx_state)

            if self.audio_ready:
                try:
                    pygame.mixer.music.stop()
                    pygame.mixer.quit()
                except Exception:
                    pass

            self.destroy()

        # schedule via after to avoid focus issues on some systems
        self.after(50, ask_and_finish)


# ===================== RUN =====================
if __name__ == "__main__":
    app = DonationMediaHub()
    app.mainloop()
