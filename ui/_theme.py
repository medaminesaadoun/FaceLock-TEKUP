# ui/_theme.py
import tkinter as tk
from tkinter import ttk


def apply(root: tk.Tk) -> None:
    style = ttk.Style(root)
    for name in ("vista", "winnative", "clam"):
        if name in style.theme_names():
            style.theme_use(name)
            break
    style.configure("Section.TLabel", font=("Segoe UI", 11, "bold"))
    style.configure("Hint.TLabel", foreground="#666666", font=("Segoe UI", 9))


def center(win: tk.Tk) -> None:
    win.update_idletasks()
    w = win.winfo_reqwidth()
    h = win.winfo_reqheight()
    sw = win.winfo_screenwidth()
    sh = win.winfo_screenheight()
    win.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")
