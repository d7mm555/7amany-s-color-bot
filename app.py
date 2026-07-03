"""7amany's Color-Bot — control window.

Pick a target color (eyedropper or palette), select a screen region, and the app left-clicks
the color repeatedly while it stays visible in that region (Start/Stop), or locks the cursor onto
it without clicking (Lock). Emergency stop: F8 (works even when this window isn't focused). A
custom key can also be bound to toggle Start/Stop.

Requires a redeemed token before Start/Lock will run — see TOKENS.md for the cloud-side setup.
"""

import ctypes

# Make the process DPI-aware BEFORE creating any Tk window, so that the pixels mss captures
# line up 1:1 with the coordinates pynput clicks (otherwise clicks drift on scaled displays).
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor v2
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import colorsys
import threading
import tkinter as tk
import tkinter.font as tkfont
from tkinter import colorchooser

from pynput import keyboard, mouse

import licensing
import overlays
import updater
from worker import ClickWorker

APP_NAME = "7amany's Color-Bot"

# ----- theme -----
BG = "#0b0b0f"
PANEL_BG = "#151318"
BORDER = "#2a2530"
PURPLE = "#a855f7"
PURPLE_BRIGHT = "#c084fc"
PURPLE_DARK = "#5b21b6"
PURPLE_DARKER = "#3b0764"
TEXT = "#f1f1f1"
TEXT_MUTED = "#8a8a93"
RED = "#f87171"


def hexcolor(rgb):
    return "#%02X%02X%02X" % tuple(int(c) for c in rgb)


def make_button(parent, text, command, big=False):
    btn = tk.Button(
        parent, text=text, command=command,
        bg=PURPLE_DARK, fg=TEXT, activebackground=PURPLE, activeforeground=TEXT,
        disabledforeground=TEXT_MUTED, relief="flat", bd=0, cursor="hand2",
        font=("Segoe UI", 12 if big else 9, "bold"),
        padx=24 if big else 12, pady=10 if big else 6,
        highlightthickness=0,
    )

    def on_enter(_e):
        if btn["state"] != "disabled":
            btn.config(bg=PURPLE)

    def on_leave(_e):
        if btn["state"] != "disabled":
            btn.config(bg=PURPLE_DARK)

    btn.bind("<Enter>", on_enter)
    btn.bind("<Leave>", on_leave)
    return btn


def set_button_enabled(btn, enabled):
    btn.config(state="normal" if enabled else "disabled",
               bg=PURPLE_DARK if enabled else BORDER,
               fg=TEXT if enabled else TEXT_MUTED)


def key_label(key):
    if key is None:
        return "None"
    if isinstance(key, keyboard.KeyCode):
        return (key.char or f"<{key.vk}>").upper()
    return str(key).replace("Key.", "").upper()


def action_label(action):
    """Human label for a color-action: ("mouse", Button) or ("key", key)."""
    kind, value = action
    if kind == "mouse":
        name = getattr(value, "name", str(value))
        return f"{name.title()} Click"
    return f"Key {key_label(value)}"


class App:
    CENTER_BOX = 80   # size (px) of the scan box "Center" mode watches in the screen middle

    def __init__(self, root):
        self.root = root
        self.color = None                 # (r, g, b)
        self.region = None                # {left, top, width, height}
        self.worker = ClickWorker(on_status=self._status_from_thread)
        self._countdown_job = None
        self._splash_anim_job = None
        self._active_mode = None          # None | "click" | "track"
        self._is_fullscreen = False
        self.bound_key = None
        self._awaiting_bind = False
        self.color_action = ("mouse", mouse.Button.left)   # what click mode fires on the color
        self._awaiting_action = False
        self._action_mouse_listener = None
        self.center_mode = False
        self._crosshair = None            # Toplevel overlay marking the screen center
        self._crosshair_hidden = False
        self._ch_canvas = None
        self._ch_h = self._ch_v = None    # the two lines of the "+" crosshair
        self.hold_key = None              # key that runs click mode only while held down
        self._awaiting_hold = False
        self._hold_active = False
        self.licensed = licensing.is_licensed()
        self.update_available = False

        root.title(APP_NAME)
        root.configure(bg=BG)
        root.attributes("-topmost", True)
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.bind("<F11>", lambda e: self.toggle_fullscreen())
        root.bind("<Escape>", lambda e: self._exit_fullscreen())

        self.splash_frame = tk.Frame(root, bg=BG)
        self.main_frame = tk.Frame(root, bg=BG)

        self._build_splash(self.splash_frame)
        self._build_main(self.main_frame)
        self._update_token_visibility()

        self._show_splash()
        self._check_for_updates_async()

        # Global F8 hotkey — stop even when this window isn't focused. Also captures the
        # next keypress when a keybind is being recorded, and fires the bound hotkey.
        self._listener = keyboard.Listener(on_press=self._on_key, on_release=self._on_key_release)
        self._listener.daemon = True
        self._listener.start()

    # ----- splash screen -----
    def _build_splash(self, parent):
        title_text = APP_NAME
        font = tkfont.Font(family="Segoe UI", size=22, weight="bold")
        widths = [font.measure(ch) for ch in title_text]
        total_width = sum(widths)
        canvas_w = max(total_width + 60, 420)
        canvas_h = 90

        outer = tk.Frame(parent, bg=BG)
        outer.pack(padx=50, pady=(50, 40))

        canvas = tk.Canvas(outer, width=canvas_w, height=canvas_h, bg=BG, highlightthickness=0, bd=0)
        canvas.pack()

        x = (canvas_w - total_width) // 2
        y = canvas_h // 2
        self._title_chars = []
        for ch, w in zip(title_text, widths):
            item = canvas.create_text(x, y, text=ch, font=font, fill=TEXT, anchor="w")
            self._title_chars.append(item)
            x += w
        self.splash_canvas = canvas

        subtitle = tk.Label(outer, text="color-triggered auto-clicker", bg=BG, fg=TEXT_MUTED,
                            font=("Segoe UI", 9))
        subtitle.pack(pady=(4, 26))

        start_btn = make_button(outer, "Start", self._enter_main, big=True)
        start_btn.pack()

        self._hue_phase = 0.0

    def _show_splash(self):
        self.main_frame.pack_forget()
        self.splash_frame.pack(fill="both", expand=True)
        self._animate_rainbow()
        self.root.update_idletasks()
        self._center_window()

    def _animate_rainbow(self):
        self._hue_phase = (self._hue_phase + 0.006) % 1.0
        for i, item in enumerate(self._title_chars):
            hue = (self._hue_phase + i * 0.045) % 1.0
            r, g, b = colorsys.hsv_to_rgb(hue, 0.85, 1.0)
            color = "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))
            self.splash_canvas.itemconfig(item, fill=color)
        self._splash_anim_job = self.root.after(40, self._animate_rainbow)

    def _enter_main(self):
        if self._splash_anim_job is not None:
            self.root.after_cancel(self._splash_anim_job)
            self._splash_anim_job = None
        # Swap frames without moving the window during the click itself — repositioning here
        # would shift window content under the pointer while the OS click is still in flight,
        # letting the release land on whatever is now underneath (e.g. a slider).
        self.splash_frame.pack_forget()
        self.main_frame.pack(fill="both", expand=True)

    def _back_to_splash(self):
        self.stop()  # don't leave a click/lock loop running invisibly behind the splash
        self.main_frame.pack_forget()
        self.splash_frame.pack(fill="both", expand=True)
        self._animate_rainbow()

    def _center_window(self):
        w = self.root.winfo_reqwidth()
        h = self.root.winfo_reqheight()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry(f"+{(sw - w) // 2}+{(sh - h) // 3}")

    # ----- fullscreen -----
    def toggle_fullscreen(self):
        self._is_fullscreen = not self._is_fullscreen
        self.root.attributes("-fullscreen", self._is_fullscreen)

    def _exit_fullscreen(self):
        if self._is_fullscreen:
            self._is_fullscreen = False
            self.root.attributes("-fullscreen", False)

    # ----- main control panel -----
    def _build_main(self, parent):
        pad = {"padx": 10, "pady": 6}

        header = tk.Frame(parent, bg=BG)
        header.pack(fill="x", padx=16, pady=(14, 0))
        tk.Label(header, text=APP_NAME, bg=BG, fg=PURPLE_BRIGHT,
                font=("Segoe UI", 13, "bold")).pack(side="left")
        make_button(header, "Fullscreen (F11)", self.toggle_fullscreen).pack(side="right")
        self.update_btn = make_button(header, "⭯ Update", self._do_update)
        # hidden until a newer VERSION is found on GitHub, see _on_update_check_result

        card = tk.Frame(parent, bg=PANEL_BG, highlightthickness=1, highlightbackground=BORDER)
        card.pack(fill="both", expand=True, padx=16, pady=14)
        frm = tk.Frame(card, bg=PANEL_BG)
        frm.pack(padx=14, pady=12)

        # --- Color row ---
        tk.Label(frm, text="Target color", bg=PANEL_BG, fg=TEXT, font=("Segoe UI", 10, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", **pad)
        self.swatch = tk.Label(frm, width=6, height=2, bg="#DDDDDD", bd=0,
                               highlightthickness=2, highlightbackground=PURPLE)
        self.swatch.grid(row=1, column=0, **pad)
        self.color_var = tk.StringVar(value="not set")
        tk.Label(frm, textvariable=self.color_var, bg=PANEL_BG, fg=TEXT_MUTED, width=18, anchor="w"
                ).grid(row=1, column=1, sticky="w", **pad)
        btns = tk.Frame(frm, bg=PANEL_BG)
        btns.grid(row=1, column=2, sticky="w", **pad)
        make_button(btns, "Eyedropper", self.pick_eyedropper).grid(row=0, column=0, padx=2)
        make_button(btns, "Palette…", self.pick_palette).grid(row=0, column=1, padx=2)

        # --- Region row ---
        tk.Label(frm, text="Scan region", bg=PANEL_BG, fg=TEXT, font=("Segoe UI", 10, "bold")).grid(
            row=2, column=0, columnspan=3, sticky="w", **pad)
        self.region_var = tk.StringVar(value="not set")
        tk.Label(frm, textvariable=self.region_var, bg=PANEL_BG, fg=TEXT_MUTED, width=26, anchor="w"
                ).grid(row=3, column=0, columnspan=2, sticky="w", **pad)
        rbtns = tk.Frame(frm, bg=PANEL_BG)
        rbtns.grid(row=3, column=2, sticky="w", **pad)
        make_button(rbtns, "Select region…", self.pick_region).grid(row=0, column=0, padx=2)
        # Plain tk.Button (no hover rebind) so its background can show an "active" state.
        self.center_btn = tk.Button(
            rbtns, text="Center", command=self.toggle_center,
            bg=PURPLE_DARK, fg=TEXT, activebackground=PURPLE, activeforeground=TEXT,
            relief="flat", bd=0, cursor="hand2", font=("Segoe UI", 9, "bold"), padx=12, pady=6,
        )
        self.center_btn.grid(row=0, column=1, padx=2)

        # --- Center mode options (only visible while Center is on) ---
        self.center_frame = tk.Frame(frm, bg=PANEL_BG)
        self.center_frame.grid(row=4, column=0, columnspan=3, sticky="ew", padx=10)
        self.center_size_var = tk.IntVar(value=self.CENTER_BOX)
        tk.Label(self.center_frame, text="Center size", bg=PANEL_BG, fg=TEXT).grid(
            row=0, column=0, sticky="w", padx=(0, 8))
        tk.Scale(self.center_frame, from_=20, to=300, variable=self.center_size_var,
                 orient="horizontal", command=lambda _v: self._apply_center_options(),
                 bg=PANEL_BG, fg=TEXT, troughcolor=PURPLE_DARKER, activebackground=PURPLE,
                 highlightthickness=0, bd=0, sliderrelief="flat", showvalue=False, length=160
                 ).grid(row=0, column=1, sticky="ew")
        self.center_size_lbl = tk.Label(self.center_frame, width=4, bg=PANEL_BG, fg=TEXT)
        self.center_size_lbl.grid(row=0, column=2, sticky="w", padx=(6, 0))
        self.center_hue_var = tk.IntVar(value=0)
        tk.Label(self.center_frame, text="Crosshair color", bg=PANEL_BG, fg=TEXT).grid(
            row=1, column=0, sticky="w", padx=(0, 8))
        tk.Scale(self.center_frame, from_=0, to=359, variable=self.center_hue_var,
                 orient="horizontal", command=lambda _v: self._apply_center_options(),
                 bg=PANEL_BG, fg=TEXT, troughcolor=PURPLE_DARKER, activebackground=PURPLE,
                 highlightthickness=0, bd=0, sliderrelief="flat", showvalue=False, length=160
                 ).grid(row=1, column=1, sticky="ew")
        self.center_color_swatch = tk.Label(self.center_frame, width=3, bg="#FF0000")
        self.center_color_swatch.grid(row=1, column=2, sticky="w", padx=(6, 0))
        self.hide_ch_btn = make_button(self.center_frame, "Hide crosshair", self._toggle_crosshair_visible)
        self.hide_ch_btn.grid(row=2, column=0, columnspan=3, pady=(4, 2))
        self.center_frame.grid_remove()

        # --- Tolerance ---
        self.tol_var = tk.IntVar(value=30)
        tk.Label(frm, text="Color tolerance", bg=PANEL_BG, fg=TEXT).grid(row=5, column=0, sticky="w", **pad)
        self._make_scale(frm, 0, 150, self.tol_var).grid(row=5, column=1, sticky="ew", **pad)
        self.tol_lbl = tk.Label(frm, width=4, bg=PANEL_BG, fg=TEXT)
        self.tol_lbl.grid(row=5, column=2, sticky="w", **pad)

        # --- Click interval ---
        self.interval_var = tk.IntVar(value=200)
        tk.Label(frm, text="Click interval (ms)", bg=PANEL_BG, fg=TEXT).grid(row=6, column=0, sticky="w", **pad)
        self._make_scale(frm, 50, 1000, self.interval_var).grid(row=6, column=1, sticky="ew", **pad)
        self.int_lbl = tk.Label(frm, width=5, bg=PANEL_BG, fg=TEXT)
        self.int_lbl.grid(row=6, column=2, sticky="w", **pad)

        # --- Start delay ---
        self.delay_var = tk.IntVar(value=3)
        tk.Label(frm, text="Start delay (s)", bg=PANEL_BG, fg=TEXT).grid(row=7, column=0, sticky="w", **pad)
        self._make_scale(frm, 0, 10, self.delay_var).grid(row=7, column=1, sticky="ew", **pad)
        self.delay_lbl = tk.Label(frm, width=4, bg=PANEL_BG, fg=TEXT)
        self.delay_lbl.grid(row=7, column=2, sticky="w", **pad)

        # --- Start / Stop ---
        actions = tk.Frame(frm, bg=PANEL_BG)
        actions.grid(row=8, column=0, columnspan=3, pady=(10, 4))
        self.start_btn = make_button(actions, "▶  Start", self.start)
        self.start_btn.grid(row=0, column=0, padx=6)
        self.stop_btn = make_button(actions, "■  Stop (F8)", self.stop)
        self.stop_btn.grid(row=0, column=1, padx=6)
        set_button_enabled(self.stop_btn, False)

        # --- Token (shown right below Start/Stop until a token is redeemed) ---
        self.token_frame = tk.Frame(frm, bg=PANEL_BG)
        self.token_frame.grid(row=9, column=0, columnspan=3, sticky="w", pady=(4, 0), padx=10)
        tk.Label(self.token_frame, text="Enter Token", bg=PANEL_BG, fg=TEXT).pack(side="left", padx=(0, 8))
        self.token_var = tk.StringVar()
        tk.Entry(self.token_frame, textvariable=self.token_var, bg="white", fg="black",
                 relief="flat", width=22, insertbackground="black").pack(side="left")
        self.token_error_var = tk.StringVar(value="")
        self.token_error_lbl = tk.Label(frm, textvariable=self.token_error_var, bg=PANEL_BG, fg=RED,
                                        font=("Segoe UI", 9, "bold"))
        self.token_error_lbl.grid(row=10, column=0, columnspan=3, sticky="w", padx=10)

        # --- Binds: what to fire on the color (click mode) + hotkey that toggles Start/Stop
        #     + hold key that runs click mode only while held down ---
        binds = tk.Frame(frm, bg=PANEL_BG)
        binds.grid(row=11, column=0, columnspan=3, pady=(10, 8))
        self.action_btn = tk.Button(
            binds, text=f"On color: {action_label(self.color_action)}",
            command=self._start_action_capture,
            bg="white", fg="black", activebackground="#eeeeee", activeforeground="black",
            relief="flat", bd=0, cursor="hand2", font=("Segoe UI", 9, "bold"), padx=12, pady=6,
        )
        self.action_btn.grid(row=0, column=0, padx=4)
        self.bind_btn = tk.Button(
            binds, text="Hotkey: None", command=self._start_bind_capture,
            bg="white", fg="black", activebackground="#eeeeee", activeforeground="black",
            relief="flat", bd=0, cursor="hand2", font=("Segoe UI", 9, "bold"), padx=12, pady=6,
        )
        self.bind_btn.grid(row=0, column=1, padx=4)
        self.hold_btn = tk.Button(
            binds, text="Hold: None", command=self._start_hold_capture,
            bg="white", fg="black", activebackground="#eeeeee", activeforeground="black",
            relief="flat", bd=0, cursor="hand2", font=("Segoe UI", 9, "bold"), padx=12, pady=6,
        )
        self.hold_btn.grid(row=0, column=2, padx=4)

        # --- Lock ---
        self.lock_btn = tk.Button(
            frm, text="Lock: OFF", command=self._toggle_lock,
            relief="flat", bd=0, cursor="hand2", font=("Segoe UI", 10, "bold"), padx=16, pady=8,
        )
        self.lock_btn.grid(row=12, column=0, columnspan=3, pady=(0, 4))

        # --- Offset (Lock only) ---
        self.offset_var = tk.DoubleVar(value=0.0)
        tk.Label(frm, text="Offset (cm)", bg=PANEL_BG, fg=TEXT).grid(row=13, column=0, sticky="w", **pad)
        self._make_scale(frm, -0.6, 0.6, self.offset_var, resolution=0.01).grid(
            row=13, column=1, sticky="ew", **pad)
        self.offset_lbl = tk.Label(frm, width=6, bg=PANEL_BG, fg=TEXT)
        self.offset_lbl.grid(row=13, column=2, sticky="w", **pad)

        # --- Status ---
        self.status_var = tk.StringVar(value="Set a color and a region, then Start.")
        sep = tk.Frame(frm, bg=BORDER, height=1)
        sep.grid(row=14, column=0, columnspan=3, sticky="ew", pady=6)
        self.status_lbl = tk.Label(frm, textvariable=self.status_var, bg=PANEL_BG, fg=PURPLE_BRIGHT,
                                   width=40, anchor="w", justify="left")
        self.status_lbl.grid(row=15, column=0, columnspan=3, sticky="w", **pad)
        tk.Label(frm, text="Emergency stop: F8  (global)", bg=PANEL_BG, fg=TEXT_MUTED).grid(
            row=16, column=0, columnspan=3, sticky="w", padx=10)

        footer = tk.Frame(parent, bg=BG)
        footer.pack(fill="x", padx=16, pady=(0, 14))
        make_button(footer, "← Back", self._back_to_splash).pack(side="left")

        self._sync_labels()
        self._apply_center_options()
        self._refresh_buttons()

    def _make_scale(self, parent, lo, hi, var, resolution=1):
        return tk.Scale(
            parent, from_=lo, to=hi, variable=var, orient="horizontal", resolution=resolution,
            command=lambda e: self._sync_labels(),
            bg=PANEL_BG, fg=TEXT, troughcolor=PURPLE_DARKER, activebackground=PURPLE,
            highlightthickness=0, bd=0, sliderrelief="flat", showvalue=False,
        )

    # ----- helpers -----
    def _sync_labels(self):
        self.tol_lbl.config(text=str(self.tol_var.get()))
        self.int_lbl.config(text=f"{self.interval_var.get()}")
        self.delay_lbl.config(text=str(self.delay_var.get()))
        self.offset_lbl.config(text=f"{self.offset_var.get():+.2f}")

    def _hide_self(self):
        """Make the control window invisible so it isn't captured in overlay screenshots."""
        self.root.attributes("-alpha", 0.0)
        self.root.update()
        self.root.after(60)  # let the compositor hide it before we grab the screen

    def _show_self(self):
        self.root.attributes("-alpha", 1.0)

    def _px_per_cm(self):
        return self.root.winfo_fpixels("1c")

    def _status_from_thread(self, msg):
        # Called from the worker/hotkey threads — marshal onto the Tk main thread.
        self.root.after(0, lambda: self._set_status(msg))
        self.root.after(0, self._refresh_buttons)

    def _set_status(self, msg):
        self.status_var.set(msg)
        self.status_lbl.config(fg=RED if msg.startswith("Error") else PURPLE_BRIGHT)

    def _refresh_buttons(self):
        countdown_active = self._countdown_job is not None
        running = self.worker.is_running() or countdown_active
        click_active = running and self._active_mode == "click"
        track_active = running and self._active_mode == "track"

        set_button_enabled(self.start_btn, not running)
        set_button_enabled(self.stop_btn, click_active)

        if click_active:
            self.lock_btn.config(state="disabled", bg=BORDER, fg=TEXT_MUTED, text="Lock: OFF")
        elif track_active:
            self.lock_btn.config(state="normal", bg=PURPLE, fg=TEXT, text="Lock: ON")
        else:
            self.lock_btn.config(state="normal", bg=PURPLE_DARK, fg=TEXT, text="Lock: OFF")

    def _on_key(self, key):
        if self._awaiting_action:
            if key == keyboard.Key.esc:
                self.root.after(0, lambda: self._finish_action_bind(None))
            else:
                self.root.after(0, lambda: self._finish_action_bind(("key", key)))
            return
        if self._awaiting_bind:
            if key == keyboard.Key.esc:
                self.root.after(0, self._cancel_bind_capture)
            else:
                self.root.after(0, lambda: self._finish_bind(key))
            return
        if self._awaiting_hold:
            if key == keyboard.Key.esc:
                self.root.after(0, self._cancel_hold_capture)
            else:
                self.root.after(0, lambda: self._finish_hold_bind(key))
            return
        if self.hold_key is not None and key == self.hold_key:
            # Key auto-repeat fires repeated presses while held — only the first one starts.
            # Don't hijack a run that Start/Lock already owns.
            if (not self._hold_active and not self.worker.is_running()
                    and self._countdown_job is None):
                self._hold_active = True
                self.root.after(0, self._hold_start)
            return
        if key == keyboard.Key.esc:
            # Route through the global listener rather than relying solely on the Tk <Escape>
            # binding — that binding needs a focused Tk widget, which a borderless fullscreen
            # window doesn't reliably have, so Escape could silently do nothing.
            self.root.after(0, self._exit_fullscreen)
            return
        if key == keyboard.Key.f8:
            self.root.after(0, self.stop)
            return
        if self.bound_key is not None and key == self.bound_key:
            self.root.after(0, self._toggle_run)

    def _on_key_release(self, key):
        if self._hold_active and self.hold_key is not None and key == self.hold_key:
            self._hold_active = False
            self.root.after(0, self.stop)

    # ----- keybind -----
    def _start_bind_capture(self):
        if self._awaiting_action or self._awaiting_hold:
            return
        self._awaiting_bind = True
        self.bind_btn.config(text="Select key…")

    def _cancel_bind_capture(self):
        self._awaiting_bind = False
        self.bind_btn.config(text=f"Hotkey: {key_label(self.bound_key)}")

    def _finish_bind(self, key):
        self._awaiting_bind = False
        self.bound_key = key
        self.bind_btn.config(text=f"Hotkey: {key_label(key)}")

    # ----- color-action bind (what click mode fires when it sees the color) -----
    def _start_action_capture(self):
        if self._awaiting_action or self._awaiting_bind or self._awaiting_hold:
            return
        self._awaiting_action = True
        self.action_btn.config(text="Press mouse button or key…")
        # Arm the mouse listener after a short delay so the left click that pressed this very
        # button isn't captured as the chosen action.
        self.root.after(250, self._arm_action_mouse_listener)

    def _arm_action_mouse_listener(self):
        if not self._awaiting_action:
            return
        self._action_mouse_listener = mouse.Listener(on_click=self._on_action_mouse)
        self._action_mouse_listener.daemon = True
        self._action_mouse_listener.start()

    def _on_action_mouse(self, x, y, button, pressed):
        if pressed and self._awaiting_action:
            self.root.after(0, lambda: self._finish_action_bind(("mouse", button)))

    def _finish_action_bind(self, action):
        """action=None cancels and keeps the previous binding."""
        self._awaiting_action = False
        if self._action_mouse_listener is not None:
            try:
                self._action_mouse_listener.stop()
            except Exception:
                pass
            self._action_mouse_listener = None
        if action is not None:
            self.color_action = action
        self.action_btn.config(text=f"On color: {action_label(self.color_action)}")

    # ----- hold bind (click mode runs only while this key is held down) -----
    def _start_hold_capture(self):
        if self._awaiting_action or self._awaiting_bind or self._awaiting_hold:
            return
        self._awaiting_hold = True
        self.hold_btn.config(text="Select key…")

    def _cancel_hold_capture(self):
        self._awaiting_hold = False
        self.hold_btn.config(text=f"Hold: {key_label(self.hold_key)}")

    def _finish_hold_bind(self, key):
        self._awaiting_hold = False
        self.hold_key = key
        self.hold_btn.config(text=f"Hold: {key_label(key)}")

    def _hold_start(self):
        """Begin click mode while the hold key stays down. No countdown — holding a key
        already means "now". Releasing the key stops it (see _on_key_release)."""
        if self.worker.is_running() or self._countdown_job is not None:
            return
        if self.update_available:
            self.token_error_var.set("Update Required")
            return

        def proceed():
            if not self._hold_active:
                return   # key was released while the license check was still running
            if self.color is None:
                self._set_status("Pick a target color first.")
                return
            if self.region is None:
                self._set_status("Select a scan region first.")
                return
            self._active_mode = "click"
            self.worker.start(self.color, self.region, self.tol_var.get(), self.interval_var.get(),
                              mode="click", action=self.color_action)
            self._refresh_buttons()

        self._require_license(proceed)

    def _toggle_run(self):
        if self.worker.is_running() or self._countdown_job is not None:
            self.stop()
        else:
            self.start()

    # ----- color / region pickers -----
    def _set_color(self, rgb):
        self.color = tuple(int(c) for c in rgb)
        hexc = hexcolor(self.color)
        self.swatch.config(bg=hexc)
        self.color_var.set(f"{hexc}  ({self.color[0]},{self.color[1]},{self.color[2]})")

    def pick_eyedropper(self):
        self._hide_self()
        try:
            color = overlays.pick_color(self.root)
        finally:
            self._show_self()
        if color:
            self._set_color(color)
            self._set_status("Color picked with eyedropper.")

    def pick_palette(self):
        initial = hexcolor(self.color) if self.color else "#FF0000"
        rgb, _hexc = colorchooser.askcolor(color=initial, parent=self.root, title="Pick target color")
        if rgb:
            self._set_color(rgb)
            self._set_status("Color picked from palette.")

    def pick_region(self):
        self._hide_self()
        try:
            region = overlays.select_region(self.root)
        finally:
            self._show_self()
        if region:
            if self.center_mode:
                self._disable_center_mode()   # a hand-picked region replaces Center mode
            self.region = region
            self.region_var.set(
                f"{region['width']}×{region['height']} @ ({region['left']},{region['top']})")
            self._set_status("Region selected.")

    # ----- center mode (watch the middle of the screen, marked by a "+" crosshair) -----
    CH_WIN = 320   # crosshair overlay window size; big enough for the largest Center size

    def toggle_center(self):
        if self.center_mode:
            self._disable_center_mode()
            self.region = None
            self.region_var.set("not set")
            self._set_status("Center mode off — crosshair removed.")
            return
        self.center_mode = True
        self.center_btn.config(bg=PURPLE)
        self.center_frame.grid()
        self._show_crosshair()
        self._apply_center_options()   # sets self.region + label from the size slider
        self._set_status("Center mode on — watching the middle of the screen.")

    def _disable_center_mode(self):
        self.center_mode = False
        self.center_btn.config(bg=PURPLE_DARK)
        self.center_frame.grid_remove()
        self._hide_crosshair()
        self._crosshair_hidden = False
        self.hide_ch_btn.config(text="Hide crosshair")

    def _crosshair_color(self):
        r, g, b = colorsys.hsv_to_rgb(self.center_hue_var.get() / 360.0, 1.0, 1.0)
        return "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))

    def _apply_center_options(self):
        """Live handler for the Center size / crosshair color sliders."""
        size = self.center_size_var.get()
        self.center_size_lbl.config(text=str(size))
        self.center_color_swatch.config(bg=self._crosshair_color())
        if not self.center_mode:
            return
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        half = size // 2
        self.region = {"left": sw // 2 - half, "top": sh // 2 - half,
                       "width": size, "height": size}
        self.region_var.set(f"Center of screen ({size}×{size})")
        self._redraw_crosshair()

    def _show_crosshair(self):
        if self._crosshair is not None:
            return
        transparent = "#010203"            # transparency key; everything this color is see-through
        ch = tk.Toplevel(self.root)
        ch.overrideredirect(True)
        ch.attributes("-topmost", True)
        ch.configure(bg=transparent)
        ch.attributes("-transparentcolor", transparent)
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        ch.geometry(f"{self.CH_WIN}x{self.CH_WIN}"
                    f"+{sw // 2 - self.CH_WIN // 2}+{sh // 2 - self.CH_WIN // 2}")
        cv = tk.Canvas(ch, width=self.CH_WIN, height=self.CH_WIN, bg=transparent,
                       highlightthickness=0)
        cv.pack()
        self._ch_canvas = cv
        self._ch_h = cv.create_line(0, 0, 0, 0, width=2)
        self._ch_v = cv.create_line(0, 0, 0, 0, width=2)
        self._apply_crosshair_window_styles(ch)
        self._crosshair = ch
        self._redraw_crosshair()
        if self._crosshair_hidden:
            ch.withdraw()

    def _redraw_crosshair(self):
        """Size the "+" to span the scan box and apply the chosen color."""
        if self._crosshair is None:
            return
        c = self.CH_WIN // 2
        half = self.center_size_var.get() // 2
        color = self._crosshair_color()
        self._ch_canvas.coords(self._ch_h, c - half, c, c + half, c)
        self._ch_canvas.coords(self._ch_v, c, c - half, c, c + half)
        self._ch_canvas.itemconfig(self._ch_h, fill=color)
        self._ch_canvas.itemconfig(self._ch_v, fill=color)

    def _apply_crosshair_window_styles(self, ch):
        """Make the overlay click-through (input passes to whatever is underneath) and — where
        Windows supports it — invisible to screen capture, so the color scan never sees the
        crosshair's own pixels even though the "+" sits inside the scan box."""
        ch.update_idletasks()
        try:
            GWL_EXSTYLE = -20
            WS_EX_LAYERED = 0x00080000
            WS_EX_TRANSPARENT = 0x00000020
            WDA_EXCLUDEFROMCAPTURE = 0x11
            hwnd = ctypes.windll.user32.GetParent(ch.winfo_id()) or ch.winfo_id()
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE,
                                                style | WS_EX_LAYERED | WS_EX_TRANSPARENT)
            ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
        except Exception:
            pass

    def _toggle_crosshair_visible(self):
        """Hide/show the crosshair overlay without leaving Center mode."""
        if self._crosshair is None:
            return
        if self._crosshair_hidden:
            self._crosshair.deiconify()
            self._crosshair.attributes("-topmost", True)
            self._apply_crosshair_window_styles(self._crosshair)
            self._crosshair_hidden = False
            self.hide_ch_btn.config(text="Hide crosshair")
        else:
            self._crosshair.withdraw()
            self._crosshair_hidden = True
            self.hide_ch_btn.config(text="Show crosshair")

    def _hide_crosshair(self):
        if self._crosshair is not None:
            try:
                self._crosshair.destroy()
            except Exception:
                pass
            self._crosshair = None
            self._ch_canvas = None
            self._ch_h = self._ch_v = None

    # ----- licensing -----
    def _update_token_visibility(self):
        # The error label stays gridded even once licensed -- it's also used to show
        # "Update Required", which can apply regardless of license state.
        if self.licensed:
            self.token_frame.grid_remove()
        else:
            self.token_frame.grid()

    def _require_license(self, on_success):
        """If already licensed, run on_success() right away. Otherwise redeem the entered
        token in a background thread (so the network call can't freeze the UI) and only run
        on_success() if it's accepted."""
        if self.licensed:
            on_success()
            return
        token = self.token_var.get().strip()
        if not token:
            self.token_error_var.set("You Must Enter A Token First")
            return
        self.token_error_var.set("")
        self._set_status("Checking token…")
        set_button_enabled(self.start_btn, False)
        self.lock_btn.config(state="disabled")

        def check():
            ok, _msg = licensing.redeem_token(token)
            self.root.after(0, lambda: self._on_license_result(ok, on_success))

        threading.Thread(target=check, daemon=True).start()

    def _on_license_result(self, ok, on_success):
        if ok:
            self.licensed = True
            self.token_error_var.set("")
            self._update_token_visibility()
            self._set_status("Token accepted.")
            self._refresh_buttons()
            on_success()
        else:
            self.token_error_var.set("Invalid Token")
            self._set_status("Set a color and a region, then Start.")
            self._refresh_buttons()

    # ----- self-update -----
    def _check_for_updates_async(self):
        def check():
            available, latest, err = updater.check_for_update()
            self.root.after(0, lambda: self._on_update_check_result(available, latest, err))

        threading.Thread(target=check, daemon=True).start()

    def _on_update_check_result(self, available, latest, err):
        if err:
            return  # network hiccup -- stay silent, don't block using the app
        self.update_available = available
        if available:
            self.update_btn.pack(side="right", padx=(0, 8))
            self._set_status(f"Update available (v{latest}). Click Update to install.")
        else:
            self.update_btn.pack_forget()

    def _do_update(self):
        if not self.update_available:
            return
        set_button_enabled(self.update_btn, False)
        set_button_enabled(self.start_btn, False)
        self.lock_btn.config(state="disabled")
        self._set_status("Downloading update…")

        def work():
            ok, err = updater.download_update()
            self.root.after(0, lambda: self._on_update_download_result(ok, err))

        threading.Thread(target=work, daemon=True).start()

    def _on_update_download_result(self, ok, err):
        if ok:
            self._set_status("Update installed. Restarting…")
            self.root.after(400, self._restart_now)
        else:
            set_button_enabled(self.update_btn, True)
            self._refresh_buttons()
            self._set_status(f"Error: {err}")

    def _restart_now(self):
        self.worker.stop()
        try:
            self._listener.stop()
        except Exception:
            pass
        updater.restart_app()

    # ----- start / stop (click mode) -----
    def start(self):
        if self.update_available:
            self.token_error_var.set("Update Required")
            return

        def proceed():
            if self.color is None:
                self._set_status("Pick a target color first.")
                return
            if self.region is None:
                self._set_status("Select a scan region first.")
                return
            delay = self.delay_var.get()
            if delay > 0:
                self._countdown(delay)
            else:
                self._launch()
            self._refresh_buttons()

        self._require_license(proceed)

    def _countdown(self, remaining):
        if remaining <= 0:
            self._countdown_job = None
            self._launch()
            return
        self._set_status(f"Starting in {remaining}…  (Stop/F8 to cancel)")
        self._countdown_job = self.root.after(1000, lambda: self._countdown(remaining - 1))

    def _launch(self):
        self._active_mode = "click"
        self.worker.start(self.color, self.region, self.tol_var.get(), self.interval_var.get(),
                          mode="click", action=self.color_action)
        self._refresh_buttons()

    def stop(self):
        if self._countdown_job is not None:
            self.root.after_cancel(self._countdown_job)
            self._countdown_job = None
            self._set_status("Cancelled before start.")
        self.worker.stop()
        self._active_mode = None
        self._refresh_buttons()

    # ----- lock (track mode) -----
    def _toggle_lock(self):
        if self._active_mode == "track" and self.worker.is_running():
            self.stop()
            return
        if self.worker.is_running() or self._countdown_job is not None:
            return  # click mode busy; button should be disabled anyway
        if self.update_available:
            self.token_error_var.set("Update Required")
            return

        def proceed():
            if self.color is None:
                self._set_status("Pick a target color first.")
                return
            if self.region is None:
                self._set_status("Select a scan region first.")
                return
            offset_px = int(round(self.offset_var.get() * self._px_per_cm()))
            self._active_mode = "track"
            self.worker.start(self.color, self.region, self.tol_var.get(), self.interval_var.get(),
                              mode="track", offset_px=offset_px)
            self._refresh_buttons()

        self._require_license(proceed)

    def on_close(self):
        self.worker.stop()
        try:
            self._listener.stop()
        except Exception:
            pass
        if self._action_mouse_listener is not None:
            try:
                self._action_mouse_listener.stop()
            except Exception:
                pass
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
