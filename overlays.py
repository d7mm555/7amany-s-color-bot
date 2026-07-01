"""Fullscreen overlays for picking a color (eyedropper) and selecting a scan region.

Both overlays freeze a screenshot of the primary monitor and let the user interact with
that frozen image, so the app's own windows never interfere. All coordinates returned are
absolute screen pixels (they include the monitor's left/top offset), which is what the
click worker and pynput need.
"""

import tkinter as tk

import mss
import numpy as np
from PIL import Image, ImageTk


def _grab_primary():
    """Grab the primary monitor. Returns (monitor_dict, rgb_ndarray, PIL.Image)."""
    with mss.mss() as sct:
        mon = sct.monitors[1]  # [0] is the virtual "all monitors" box; [1] is primary
        shot = sct.grab(mon)
    # mss gives BGRA; build an RGB numpy array for sampling and a PIL image for display.
    rgb = np.array(shot)[:, :, :3][:, :, ::-1]  # BGR -> RGB
    img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    return mon, rgb, img


def _make_overlay(root, mon):
    """Create a borderless, topmost Toplevel positioned exactly over the primary monitor."""
    top = tk.Toplevel(root)
    top.overrideredirect(True)
    top.geometry(f"{mon['width']}x{mon['height']}+{mon['left']}+{mon['top']}")
    top.attributes("-topmost", True)
    top.configure(cursor="crosshair")
    canvas = tk.Canvas(top, highlightthickness=0, bd=0)
    canvas.pack(fill="both", expand=True)
    top.after(10, lambda: (top.focus_force(), top.grab_set()))
    return top, canvas


def pick_color(root):
    """Eyedropper: show a frozen screen, let the user click a pixel.

    Returns an (r, g, b) tuple, or None if cancelled with Escape.
    A small magnifier loupe follows the cursor for pixel-precise picking.
    """
    mon, rgb, img = _grab_primary()
    top, canvas = _make_overlay(root, mon)

    photo = ImageTk.PhotoImage(img)
    canvas.create_image(0, 0, anchor="nw", image=photo)
    canvas.image = photo  # keep a reference so it isn't garbage-collected

    result = {"color": None}
    h, w = rgb.shape[:2]
    LOUPE = 12       # half-size of the sampled area (pixels)
    ZOOM = 8         # magnification factor
    box = LOUPE * 2 + 1

    loupe_id = None
    cross_ids = []
    text_id = None

    def on_motion(event):
        nonlocal loupe_id, cross_ids, text_id
        x = max(0, min(w - 1, event.x))
        y = max(0, min(h - 1, event.y))
        x0, x1 = max(0, x - LOUPE), min(w, x + LOUPE + 1)
        y0, y1 = max(0, y - LOUPE), min(h, y + LOUPE + 1)
        crop = img.crop((x0, y0, x1, y1)).resize((box * ZOOM, box * ZOOM), Image.NEAREST)
        loupe_img = ImageTk.PhotoImage(crop)
        canvas.loupe_img = loupe_img  # keep reference

        # place loupe offset from the cursor, flipping to stay on-screen
        lx = x + 20 if x + 20 + box * ZOOM < w else x - 20 - box * ZOOM
        ly = y + 20 if y + 20 + box * ZOOM < h else y - 20 - box * ZOOM

        r, g, b = (int(v) for v in rgb[y, x])
        if loupe_id is None:
            loupe_id = canvas.create_image(lx, ly, anchor="nw", image=loupe_img)
            cx, cy = lx + box * ZOOM // 2, ly + box * ZOOM // 2
            cross_ids = [
                canvas.create_line(lx, cy, lx + box * ZOOM, cy, fill="red"),
                canvas.create_line(cx, ly, cx, ly + box * ZOOM, fill="red"),
                canvas.create_rectangle(lx, ly, lx + box * ZOOM, ly + box * ZOOM, outline="white"),
            ]
            text_id = canvas.create_text(lx, ly - 14, anchor="nw", fill="white",
                                         font=("Consolas", 11, "bold"))
        else:
            canvas.itemconfig(loupe_id, image=loupe_img)
            canvas.coords(loupe_id, lx, ly)
            cx, cy = lx + box * ZOOM // 2, ly + box * ZOOM // 2
            canvas.coords(cross_ids[0], lx, cy, lx + box * ZOOM, cy)
            canvas.coords(cross_ids[1], cx, ly, cx, ly + box * ZOOM)
            canvas.coords(cross_ids[2], lx, ly, lx + box * ZOOM, ly + box * ZOOM)
            canvas.coords(text_id, lx, ly - 14)
        canvas.itemconfig(text_id, text=f"RGB {r},{g},{b}  #{r:02X}{g:02X}{b:02X}")

    def on_click(event):
        x = max(0, min(w - 1, event.x))
        y = max(0, min(h - 1, event.y))
        r, g, b = (int(v) for v in rgb[y, x])
        result["color"] = (r, g, b)
        top.destroy()

    def on_cancel(_event=None):
        top.destroy()

    canvas.bind("<Motion>", on_motion)
    canvas.bind("<Button-1>", on_click)
    top.bind("<Escape>", on_cancel)
    canvas.bind("<Escape>", on_cancel)
    root.wait_window(top)
    return result["color"]


def select_region(root):
    """Region selector: drag a rectangle over a frozen screen.

    Returns {"left", "top", "width", "height"} in absolute screen pixels,
    or None if cancelled (Escape) or the drag was too small.
    """
    mon, rgb, img = _grab_primary()
    top, canvas = _make_overlay(root, mon)

    photo = ImageTk.PhotoImage(img)
    canvas.create_image(0, 0, anchor="nw", image=photo)
    canvas.image = photo

    canvas.create_text(mon["width"] // 2, 24, fill="white", font=("Segoe UI", 14, "bold"),
                       text="Drag to select the scan region  •  Esc to cancel")

    state = {"x0": 0, "y0": 0, "rect": None}
    result = {"region": None}

    def on_press(event):
        state["x0"], state["y0"] = event.x, event.y
        if state["rect"] is None:
            state["rect"] = canvas.create_rectangle(event.x, event.y, event.x, event.y,
                                                    outline="#00E5FF", width=2)

    def on_drag(event):
        canvas.coords(state["rect"], state["x0"], state["y0"], event.x, event.y)

    def on_release(event):
        x0, y0 = state["x0"], state["y0"]
        x1, y1 = event.x, event.y
        left, top_ = min(x0, x1), min(y0, y1)
        width, height = abs(x1 - x0), abs(y1 - y0)
        if width >= 4 and height >= 4:
            result["region"] = {
                "left": mon["left"] + left,
                "top": mon["top"] + top_,
                "width": width,
                "height": height,
            }
        top.destroy()

    def on_cancel(_event=None):
        top.destroy()

    canvas.bind("<Button-1>", on_press)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", on_release)
    top.bind("<Escape>", on_cancel)
    canvas.bind("<Escape>", on_cancel)
    root.wait_window(top)
    return result["region"]
