from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox
import webbrowser


def show_help(parent: tk.Tk, title: str, text: str, link: str) -> None:
    win = tk.Toplevel(parent)
    win.title(title)
    win.geometry("560x320")
    win.resizable(False, False)
    win.transient(parent)
    win.grab_set()

    frame = ttk.Frame(win, padding=14, style="Card.TFrame")
    frame.pack(fill="both", expand=True)

    ttk.Label(frame, text=text, justify="left", anchor="nw", wraplength=520).pack(fill="x", pady=(0, 12))

    ttk.Label(frame, text="Ссылка:", style="Muted.TLabel").pack(anchor="w")
    row = ttk.Frame(frame, style="Card2.TFrame", padding=10)
    row.pack(fill="x", pady=(8, 8))

    link_lbl = ttk.Label(row, text=link, foreground="#7aa2f7", cursor="hand2", wraplength=480)
    link_lbl.pack(side="left", fill="x", expand=True)
    link_lbl.bind("<Button-1>", lambda _e: webbrowser.open(link))

    def copy_link() -> None:
        parent.clipboard_clear()
        parent.clipboard_append(link)
        messagebox.showinfo("OK", "Ссылка скопирована.", parent=win)

    ttk.Button(row, text="Copy", command=copy_link).pack(side="right", padx=(10, 0))
    ttk.Button(frame, text="OK", command=win.destroy, style="Accent.TButton").pack(pady=(12, 0))
