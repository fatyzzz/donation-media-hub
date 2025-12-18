from __future__ import annotations

import queue as thread_queue
import shutil
import threading
import time
import webbrowser
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import tkinter as tk
from tkinter import ttk, messagebox

from donation_media_hub.config import APP_TITLE
from donation_media_hub.downloader import Downloader
from donation_media_hub.models import Track
from donation_media_hub.paths import TEMP_DIR
from donation_media_hub.playback import AudioPlayer
from donation_media_hub.pollers import Pollers
from donation_media_hub.queue_manager import QueueManager
from donation_media_hub.storage import load_json, save_json
from donation_media_hub.ui.dialogs import show_help
from donation_media_hub.ui.theme import DarkTheme


class MainWindow(tk.Tk):
    """
    Vertical player UI + event-driven logic.

    UI emits user actions -> controller methods.
    Background work (polling / downloading) emits events -> UI thread handles.
    """

    def __init__(
        self,
        queue_manager: QueueManager,
        config_file: Path,
        state_da_file: Path,
        state_dx_file: Path,
    ) -> None:
        super().__init__()

        self.queue = queue_manager
        self.config_file = config_file
        self.state_da_file = state_da_file
        self.state_dx_file = state_dx_file

        self.ui_events: "thread_queue.Queue[dict]" = thread_queue.Queue()
        self._closing = False

        self._load_config()
        self._load_states()

        self.downloader = Downloader(TEMP_DIR)
        self.player = AudioPlayer(volume=float(self.config_data.get("volume", 0.7)))

        self.pollers = Pollers(
            emit_event=self.ui_events.put,
            get_da_token=lambda: self.da_token_var.get(),
            get_dx_token=lambda: self.dx_token_var.get(),
            da_last_media_id=int(self.da_state.get("last_media_id", 0) or 0),
            dx_last_timestamp=self.dx_state.get("last_timestamp"),
        )

        self._download_thread = threading.Thread(target=self._download_loop, daemon=True)
        self._download_thread.start()

        self._build_ui()
        self._refresh_queue_table()
        self._update_now_playing()

        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.after(120, self._process_ui_events)
        self.after(450, self._watchdog)

    # -------------------- config/state --------------------
    def _load_config(self) -> None:
        self.config_data = load_json(
            self.config_file,
            {
                "da_token": "",
                "dx_token": "",
                "show_tokens": False,
                "download_mode": True,
                "volume": 0.7,
                "current_track_id": None,
            },
        )

        self.da_token_var = tk.StringVar(value=self.config_data.get("da_token", ""))
        self.dx_token_var = tk.StringVar(value=self.config_data.get("dx_token", ""))
        self.show_tokens_var = tk.BooleanVar(value=bool(self.config_data.get("show_tokens", False)))
        self.download_mode_var = tk.BooleanVar(value=bool(self.config_data.get("download_mode", True)))
        self.volume_var = tk.DoubleVar(value=float(self.config_data.get("volume", 0.7)))

        self.queue.set_current(self.config_data.get("current_track_id"))

    def _save_config(self) -> None:
        self.config_data["da_token"] = self.da_token_var.get().strip()
        self.config_data["dx_token"] = self.dx_token_var.get().strip()
        self.config_data["show_tokens"] = bool(self.show_tokens_var.get())
        self.config_data["download_mode"] = bool(self.download_mode_var.get())
        self.config_data["volume"] = float(self.volume_var.get())
        self.config_data["current_track_id"] = self.queue.current_track_id
        save_json(self.config_file, self.config_data)

    def _load_states(self) -> None:
        self.da_state = load_json(self.state_da_file, {"last_media_id": 0})
        self.dx_state = load_json(self.state_dx_file, {"last_timestamp": None})

    def _save_states(self) -> None:
        snap = self.pollers.state_snapshot()
        self.da_state["last_media_id"] = int(snap.get("da_last_media_id", 0) or 0)
        self.dx_state["last_timestamp"] = snap.get("dx_last_timestamp")
        save_json(self.state_da_file, self.da_state)
        save_json(self.state_dx_file, self.dx_state)

    # -------------------- UI --------------------
    def _build_ui(self) -> None:
        self.title(APP_TITLE)
        # Вертикальный прямоугольник
        self.geometry("520x860")
        self.minsize(480, 780)

        DarkTheme().apply(self)

        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)

        # Header / Now playing
        header = ttk.Frame(root, style="Card.TFrame", padding=14)
        header.pack(fill="x")

        ttk.Label(header, text="Now Playing", style="Muted.TLabel").pack(anchor="w")
        self.now_big_var = tk.StringVar(value="—")
        self.now_small_var = tk.StringVar(value="Queue empty")
        ttk.Label(header, textvariable=self.now_big_var, style="Title.TLabel").pack(anchor="w", pady=(6, 0))
        ttk.Label(header, textvariable=self.now_small_var, style="Small.TLabel").pack(anchor="w", pady=(4, 0))

        # Controls
        controls = ttk.Frame(root, style="Card.TFrame", padding=12)
        controls.pack(fill="x", pady=(10, 0))

        row1 = ttk.Frame(controls)
        row1.pack(fill="x")

        ttk.Button(row1, text="⏮", width=4, command=self.go_start).pack(side="left", padx=(0, 6))
        ttk.Button(row1, text="Prev", command=self.prev_track).pack(side="left", padx=(0, 6))
        ttk.Button(row1, text="Play/Pause", style="Accent.TButton", command=self.play_pause).pack(side="left", fill="x", expand=True, padx=(0, 6))
        ttk.Button(row1, text="Next", command=self.next_track).pack(side="left", padx=(0, 6))
        ttk.Button(row1, text="Skip", command=self.skip_track).pack(side="left")

        row2 = ttk.Frame(controls)
        row2.pack(fill="x", pady=(10, 0))

        self.status_var = tk.StringVar(value="Idle")
        ttk.Label(row2, textvariable=self.status_var, style="Muted.TLabel").pack(side="left")

        ttk.Label(row2, text="🔊", style="Muted.TLabel").pack(side="left", padx=(12, 6))
        vol = ttk.Scale(row2, from_=0.0, to=1.0, variable=self.volume_var, command=self._on_volume, length=180)
        vol.pack(side="left")

        # Queue
        queue_card = ttk.Frame(root, style="Card.TFrame", padding=12)
        queue_card.pack(fill="both", expand=True, pady=(10, 0))

        top = ttk.Frame(queue_card)
        top.pack(fill="x")

        ttk.Label(top, text="Queue", style="Big.TLabel").pack(side="left")
        ttk.Button(top, text="Clear", style="Danger.TButton", command=self.clear_queue).pack(side="right")

        columns = ("num", "title", "status")
        self.tree = ttk.Treeview(queue_card, columns=columns, show="headings", height=14)
        self.tree.heading("num", text="#")
        self.tree.heading("title", text="Title")
        self.tree.heading("status", text="Status")

        self.tree.column("num", width=42, anchor="center")
        self.tree.column("title", width=320)
        self.tree.column("status", width=90, anchor="center")

        sb = ttk.Scrollbar(queue_card, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)

        self.tree.pack(side="left", fill="both", expand=True, pady=(10, 0))
        sb.pack(side="right", fill="y", pady=(10, 0))

        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", self._on_open_link)

        # Settings (compact bottom panel)
        settings = ttk.Frame(root, style="Card.TFrame", padding=12)
        settings.pack(fill="x", pady=(10, 0))

        ttk.Label(settings, text="Tokens / Mode", style="Big.TLabel").pack(anchor="w")

        r1 = ttk.Frame(settings)
        r1.pack(fill="x", pady=(8, 0))
        ttk.Label(r1, text="DA:", width=5, style="Muted.TLabel").pack(side="left")
        self.da_entry = ttk.Entry(r1, textvariable=self.da_token_var)
        self.da_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(r1, text="?", width=3, command=self._help_da).pack(side="left", padx=(6, 0))

        r2 = ttk.Frame(settings)
        r2.pack(fill="x", pady=(8, 0))
        ttk.Label(r2, text="DX:", width=5, style="Muted.TLabel").pack(side="left")
        self.dx_entry = ttk.Entry(r2, textvariable=self.dx_token_var)
        self.dx_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(r2, text="?", width=3, command=self._help_dx).pack(side="left", padx=(6, 0))

        r3 = ttk.Frame(settings)
        r3.pack(fill="x", pady=(10, 0))

        ttk.Checkbutton(r3, text="Show tokens", variable=self.show_tokens_var, command=self._toggle_tokens).pack(side="left")
        ttk.Checkbutton(r3, text="Download & Play (mp3)", variable=self.download_mode_var, command=self._save_config).pack(side="left", padx=(12, 0))

        r4 = ttk.Frame(settings)
        r4.pack(fill="x", pady=(10, 0))
        ttk.Button(r4, text="Start", style="Accent.TButton", command=self.start).pack(side="left", fill="x", expand=True, padx=(0, 6))
        ttk.Button(r4, text="Stop", command=self.stop).pack(side="left", fill="x", expand=True, padx=(0, 6))
        ttk.Button(r4, text="Temp", command=self.clear_temp).pack(side="left", fill="x", expand=True)

        # Log
        log = ttk.Frame(root, style="Card2.TFrame", padding=10)
        log.pack(fill="x", pady=(10, 0))
        ttk.Label(log, text="Log", style="Muted.TLabel").pack(anchor="w")
        self.log_text = tk.Text(
            log,
            height=7,
            bg="#10131a",
            fg="#e7eaf0",
            insertbackground="#e7eaf0",
            relief="flat",
            highlightthickness=1,
            highlightbackground="#242a3a",
        )
        self.log_text.pack(fill="x", pady=(8, 0))

        self._toggle_tokens()

    def _toggle_tokens(self) -> None:
        show = bool(self.show_tokens_var.get())
        self.da_entry.configure(show="" if show else "*")
        self.dx_entry.configure(show="" if show else "*")
        self._save_config()

    def _on_volume(self, _evt=None) -> None:
        self.player.set_volume(float(self.volume_var.get()))
        self._save_config()

    def _log(self, msg: str) -> None:
        try:
            self.log_text.insert("end", msg + "\n")
            self.log_text.see("end")
        except Exception:
            pass

    # -------------------- actions: start/stop --------------------
    def start(self) -> None:
        da = self.da_token_var.get().strip()
        dx = self.dx_token_var.get().strip()
        if not da and not dx:
            messagebox.showerror("Ошибка", "Вставь хотя бы один токен (DA или DX).", parent=self)
            return

        # refresh clients tokens immediately
        self.pollers.da_client.token = da
        self.pollers.dx_client.token = dx

        self.pollers.start()
        self.status_var.set("Polling: ON")
        self._log("▶ polling started")
        self._save_config()

        # autoplay if have queue
        if self.queue.current() and (not self.download_mode_var.get()):
            self.play_current(force=True)

    def stop(self) -> None:
        self.pollers.stop()
        self.status_var.set("Polling: OFF")
        self._log("⏹ polling stopped")
        self._save_states()
        self._save_config()

    # -------------------- selection + open --------------------
    def _on_select(self, _evt=None) -> None:
        tid = self._selected_id()
        if not tid:
            return
        self.queue.set_current(tid)
        self.queue.save()
        self._save_config()
        self._update_now_playing()

    def _on_open_link(self, _evt=None) -> None:
        t = self.queue.current()
        if not t:
            return
        webbrowser.open(t.url)

    def _selected_id(self) -> Optional[str]:
        sel = self.tree.selection()
        return sel[0] if sel else None

    # -------------------- queue table --------------------
    def _refresh_queue_table(self) -> None:
        for iid in self.tree.get_children():
            self.tree.delete(iid)

        self.queue.sort()
        for idx, t in enumerate(self.queue.tracks, start=1):
            self.tree.insert("", "end", iid=t.track_id, values=(idx, t.title, t.status))

        # keep selection
        if self.queue.current_track_id and self.queue.get(self.queue.current_track_id):
            try:
                self.tree.selection_set(self.queue.current_track_id)
                self.tree.see(self.queue.current_track_id)
            except Exception:
                pass

        self.queue.save()
        self.after(900, self._refresh_queue_table)

    def _update_now_playing(self) -> None:
        t = self.queue.current()
        if not t:
            self.now_big_var.set("—")
            self.now_small_var.set("Queue empty")
            return
        self.now_big_var.set(f"[{t.source}] {t.title}")
        extra = f"Status: {t.status}"
        if t.local_path and Path(t.local_path).exists():
            extra += f" · {Path(t.local_path).name}"
        self.now_small_var.set(extra)

    # -------------------- events from threads --------------------
    def _process_ui_events(self) -> None:
        try:
            while True:
                ev = self.ui_events.get_nowait()
                et = ev.get("type")

                if et == "log":
                    self._log(str(ev.get("msg", "")))

                elif et == "new_track":
                    try:
                        t = Track(**ev["track"])
                        if self.queue.append_if_new(t):
                            self._log(f"➕ NEW [{t.source}] {t.title}")
                            self.status_var.set(f"Queue: {len(self.queue.tracks)}")

                            if not self.download_mode_var.get() and not self.player.is_playing():
                                self.play_current(force=True)

                            self.queue.save()
                            self._save_config()
                            self._update_now_playing()
                    except Exception as e:
                        self._log(f"❌ track parse error: {e}")

                elif et == "track_status":
                    tid = ev.get("track_id")
                    t = self.queue.get(tid) if tid else None
                    if t:
                        t.status = ev.get("status", t.status) or t.status
                        if ev.get("error"):
                            t.error = str(ev["error"])
                        self.queue.save()
                        self._update_now_playing()

                elif et == "download_done":
                    tid = ev.get("track_id")
                    path = ev.get("path")
                    t = self.queue.get(tid) if tid else None
                    if t:
                        t.local_path = str(path)
                        if t.status not in {"playing", "paused"}:
                            t.status = "ready"
                        self.queue.save()
                        # auto start if it's current and nothing is playing
                        if t.track_id == self.queue.current_track_id and self.download_mode_var.get() and not self.player.is_playing():
                            self.play_current(force=True)

        except thread_queue.Empty:
            pass

        self.after(120, self._process_ui_events)

    # -------------------- download loop --------------------
    def _download_loop(self) -> None:
        while True:
            time.sleep(0.25)

            if not self.pollers.is_running():
                continue
            if not self.download_mode_var.get():
                continue

            cur = self.queue.current()
            if not cur:
                continue

            idx = self.queue.index_of_current()
            targets: list[Track] = []
            if 0 <= idx < len(self.queue.tracks):
                targets.append(self.queue.tracks[idx])
            if 0 <= idx + 1 < len(self.queue.tracks):
                targets.append(self.queue.tracks[idx + 1])

            for t in targets:
                if t.local_path and Path(t.local_path).exists():
                    continue
                if t.status in {"downloading", "playing", "paused"}:
                    continue
                # download
                self.ui_events.put({"type": "track_status", "track_id": t.track_id, "status": "downloading"})
                try:
                    out = self.downloader.download_mp3(t)
                    self.ui_events.put({"type": "download_done", "track_id": t.track_id, "path": str(out)})
                    self.ui_events.put({"type": "track_status", "track_id": t.track_id, "status": "ready"})
                    self.ui_events.put({"type": "log", "msg": f"✅ downloaded: {out.name}"})
                except Exception as e:
                    self.ui_events.put({"type": "track_status", "track_id": t.track_id, "status": "failed", "error": str(e)})
                    self.ui_events.put({"type": "log", "msg": f"❌ download failed: {t.title} — {e}"})

    # -------------------- playback --------------------
    def play_current(self, force: bool = False) -> None:
        t = self.queue.current()
        if not t:
            return

        # browser mode
        if not self.download_mode_var.get():
            webbrowser.open(t.url)
            self._log(f"🌐 opened: {t.title}")
            t.status = "played"
            self.queue.save()
            self.next_track(auto=True)
            return

        if not self.player.is_ready():
            messagebox.showerror("Audio error", "pygame не найден/не инициализировался.\n\npip install pygame", parent=self)
            return

        if not t.local_path or not Path(t.local_path).exists():
            if force:
                self._log(f"⏳ waiting download: {t.title}")
            t.status = "queued"
            self.queue.save()
            self._update_now_playing()
            return

        try:
            self.player.play(t.local_path, volume=float(self.volume_var.get()))
            t.status = "playing"
            self.queue.save()
            self._update_now_playing()
            self.status_var.set("Playing")
            self._cleanup_temp_window()
        except Exception as e:
            t.status = "failed"
            t.error = str(e)
            self.queue.save()
            self._log(f"❌ play error: {e}")

    def play_pause(self) -> None:
        t = self.queue.current()
        if not t:
            return

        if not self.download_mode_var.get():
            self.play_current(force=True)
            return

        if not self.player.is_ready():
            self.play_current(force=True)
            return

        if self.player.is_playing() and not self.player.is_paused():
            self.player.pause()
            t.status = "paused"
            self._log("⏸ pause")
        elif self.player.is_paused():
            self.player.resume()
            t.status = "playing"
            self._log("⏯ resume")
        else:
            self.play_current(force=True)
            return

        self.queue.save()
        self._update_now_playing()

    def next_track(self, auto: bool = False) -> None:
        cur = self.queue.current()
        if not cur:
            return

        if cur.status in {"playing", "paused"}:
            cur.status = "played" if auto else "skipped"
        elif cur.status not in {"played", "skipped", "failed"}:
            cur.status = "played" if auto else "skipped"

        self.player.stop()

        nid = self.queue.next_id()
        if not nid:
            self.queue.save()
            self.status_var.set("End of queue")
            self._log("ℹ end of queue")
            self._cleanup_temp_window()
            self._update_now_playing()
            return

        self.queue.set_current(nid)
        self.queue.save()
        self._save_config()
        self.play_current(force=True)

    def prev_track(self) -> None:
        pid = self.queue.prev_id()
        if not pid:
            self._log("ℹ no previous")
            return
        self.player.stop()
        self.queue.set_current(pid)
        self.queue.save()
        self._save_config()
        self.play_current(force=True)

    def skip_track(self) -> None:
        cur = self.queue.current()
        if not cur:
            return
        cur.status = "skipped"
        self.queue.save()
        self.player.stop()
        self._log(f"⏩ skipped: {cur.title}")
        self.next_track(auto=False)

    def go_start(self) -> None:
        if not self.queue.tracks:
            return
        self.player.stop()
        self.queue.set_current(self.queue.tracks[0].track_id)
        self.queue.save()
        self._save_config()
        self.play_current(force=True)

    # -------------------- watchdog: auto next --------------------
    def _watchdog(self) -> None:
        try:
            if self.pollers.is_running() and self.download_mode_var.get() and self.player.is_ready():
                if not self.player.is_paused() and self.queue.current_track_id:
                    if not self.player.is_playing():
                        cur = self.queue.current()
                        if cur and cur.status == "playing":
                            cur.status = "played"
                            self.queue.save()
                            self._cleanup_temp_window()
                            self.next_track(auto=True)
        finally:
            self.after(450, self._watchdog)

    # -------------------- cleanup --------------------
    def _cleanup_temp_window(self) -> None:
        idx = self.queue.index_of_current()
        if idx < 0:
            return

        keep_paths: set[str] = set()
        keep_ids = set()
        if idx - 1 >= 0:
            keep_ids.add(self.queue.tracks[idx - 1].track_id)
        keep_ids.add(self.queue.tracks[idx].track_id)
        if idx + 1 < len(self.queue.tracks):
            keep_ids.add(self.queue.tracks[idx + 1].track_id)

        for t in self.queue.tracks:
            if t.track_id in keep_ids and t.local_path and Path(t.local_path).exists():
                keep_paths.add(str(Path(t.local_path).resolve()))

        from donation_media_hub.downloader import Downloader  # local import to avoid cycles
        Downloader.cleanup_keep(TEMP_DIR, keep_paths)

        # normalize local_path
        for t in self.queue.tracks:
            if t.local_path and not Path(t.local_path).exists():
                t.local_path = None
        self.queue.save()

    def clear_temp(self) -> None:
        self.player.stop()
        try:
            if TEMP_DIR.exists():
                shutil.rmtree(TEMP_DIR, ignore_errors=True)
            TEMP_DIR.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось очистить temp: {e}", parent=self)
            return

        for t in self.queue.tracks:
            t.local_path = None
            if t.status in {"ready", "playing", "paused"}:
                t.status = "queued"

        self.queue.save()
        self._log("🧹 temp cleared")
        self._update_now_playing()

    def clear_queue(self) -> None:
        self.player.stop()
        self.queue.clear()
        self.queue.save()
        self.queue.set_current(None)
        self._save_config()
        self._log("🗑 queue cleared")
        self.status_var.set("Queue cleared")
        self._update_now_playing()

    # -------------------- help --------------------
    def _help_da(self) -> None:
        show_help(
            self,
            "DonationAlerts — токен",
            "1) Открой страницу\n2) Найди «Секретный токен»\n3) Скопируй и вставь",
            "https://www.donationalerts.com/dashboard/general-settings/account",
        )

    def _help_dx(self) -> None:
        show_help(
            self,
            "DonateX — токен",
            "1) Открой страницу\n2) «Последние донаты»\n3) В адресной строке token=XXXX\n4) Скопируй XXXX",
            "https://donatex.gg/streamer/dashboard",
        )

    # -------------------- close --------------------
    def on_close(self) -> None:
        if self._closing:
            return
        self._closing = True

        self.pollers.stop()

        # ask cleanup
        try:
            has_files = TEMP_DIR.exists() and any(TEMP_DIR.glob("*.mp3"))
            if has_files:
                yes = messagebox.askyesno(
                    "Очистка временных файлов",
                    "Очистить загруженные треки (mp3) из temp папки?",
                    parent=self,
                )
                if yes:
                    shutil.rmtree(TEMP_DIR, ignore_errors=True)
                    TEMP_DIR.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        self._save_states()
        self.queue.save()
        self._save_config()

        self.player.shutdown()
        self.destroy()
