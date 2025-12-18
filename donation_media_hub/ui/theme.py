from __future__ import annotations

import tkinter as tk
from tkinter import ttk


class DarkTheme:
    """
    Pure ttk dark theme (no external libs).
    Works best with 'clam'.
    """

    BG = "#0f1115"
    PANEL = "#151924"
    PANEL_2 = "#10131a"
    FG = "#e7eaf0"
    MUTED = "#aab1c0"
    ACCENT = "#7aa2f7"
    DANGER = "#f7768e"
    BORDER = "#242a3a"
    SELECT = "#1e2638"

    def apply(self, root: tk.Tk) -> None:
        root.configure(bg=self.BG)
        style = ttk.Style(root)

        try:
            style.theme_use("clam")
        except Exception:
            pass

        style.configure(".", background=self.BG, foreground=self.FG, fieldbackground=self.PANEL, bordercolor=self.BORDER)

        style.configure("TFrame", background=self.BG)
        style.configure("Card.TFrame", background=self.PANEL, relief="flat")
        style.configure("Card2.TFrame", background=self.PANEL_2, relief="flat")

        style.configure("TLabel", background=self.BG, foreground=self.FG)
        style.configure("Muted.TLabel", background=self.BG, foreground=self.MUTED)
        style.configure("Title.TLabel", background=self.BG, foreground=self.FG, font=("Segoe UI", 14, "bold"))
        style.configure("Big.TLabel", background=self.BG, foreground=self.FG, font=("Segoe UI", 12, "bold"))
        style.configure("Small.TLabel", background=self.BG, foreground=self.MUTED, font=("Segoe UI", 9))

        style.configure("TButton", padding=(10, 8), background=self.PANEL, foreground=self.FG, borderwidth=1)
        style.map(
            "TButton",
            background=[("active", self.SELECT)],
            foreground=[("disabled", self.MUTED)],
        )

        style.configure("Accent.TButton", background=self.ACCENT, foreground="#0b0d12")
        style.map("Accent.TButton", background=[("active", self.ACCENT)])

        style.configure("Danger.TButton", background=self.DANGER, foreground="#0b0d12")
        style.map("Danger.TButton", background=[("active", self.DANGER)])

        style.configure("TEntry", fieldbackground=self.PANEL, foreground=self.FG)
        style.configure("TCheckbutton", background=self.BG, foreground=self.FG)

        style.configure("TNotebook", background=self.BG, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(10, 6), background=self.PANEL, foreground=self.FG)
        style.map("TNotebook.Tab", background=[("selected", self.SELECT)])

        style.configure("Treeview", background=self.PANEL, fieldbackground=self.PANEL, foreground=self.FG, bordercolor=self.BORDER)
        style.configure("Treeview.Heading", background=self.PANEL_2, foreground=self.FG, relief="flat")
        style.map("Treeview", background=[("selected", self.SELECT)])

        style.configure("Vertical.TScrollbar", background=self.PANEL, troughcolor=self.BG, bordercolor=self.BORDER)
