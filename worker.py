"""Background worker that scans the selected screen region for the target color.

Two modes, sharing the same scan/match loop:
- "click": fires the bound action (default: left click) repeatedly while the color stays
  visible (the original auto-clicker).
- "track": moves the cursor onto the color (offset by a horizontal pixel amount) and keeps it
  there while the color stays visible, without clicking — used by the "Lock" feature.

Color matching is per-channel (Chebyshev): a pixel matches when |R-r|, |G-g| AND |B-b| are all
within the tolerance. Tolerance 0 means exact match. This is more predictable than Euclidean
distance — the slider value is simply "how far any single channel may drift".

Runs on its own daemon thread so the Tk GUI stays responsive. A threading.Event is used
both to stop the loop and to sleep between cycles (so Stop takes effect immediately). Only one
mode can run at a time — start() is a no-op while a scan is already in progress, which is what
keeps the click-mode Start/Stop and the track-mode Lock from ever fighting over the mouse.
"""

import threading
import time

import mss
import numpy as np
from pynput.keyboard import Controller as KeyboardController
from pynput.mouse import Button, Controller


class ClickWorker:
    # Track mode aims for this cadence (~200x/sec). The wait each cycle is adaptive — grab and
    # match time is subtracted — so the real rate stays steady instead of stuttering when a
    # frame takes longer to process.
    TRACK_POLL = 0.005
    # Tracking finds the color far faster than the GUI needs to refresh; only push a status line
    # this often, or the flood of cross-thread updates makes the window sluggish.
    STATUS_MIN_INTERVAL = 0.1
    # Cursor smoothing (track mode): each tick the cursor moves this fraction of the remaining
    # distance to the target. Combined with the fast poll this reads as a smooth glide that
    # still converges in a few tens of ms.
    SMOOTH_ALPHA = 0.5
    # …but if the target is far away (fresh acquisition, or it jumped), snap instantly.
    SNAP_DIST = 80.0
    # …and inside this distance stop nudging entirely, so the cursor never vibrates by 1px.
    DEADBAND = 1.0

    def __init__(self, on_status=None):
        self.on_status = on_status
        self._thread = None
        self._stop = threading.Event()
        self._mouse = Controller()
        self._keys = KeyboardController()

    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self, color, region, tolerance, interval_ms, mode="click", offset_px=0, action=None):
        """color: (r,g,b); region: {left,top,width,height}; tolerance: 0-255; interval_ms: int.
        mode: "click" (default) or "track". offset_px: horizontal pixel shift, "track" mode only.
        action: ("mouse", pynput Button) or ("key", pynput key) — what click mode fires when the
        color is visible. Defaults to a left click."""
        if self.is_running():
            return
        if action is None:
            action = ("mouse", Button.left)
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            args=(tuple(color), dict(region), int(tolerance), max(interval_ms, 10) / 1000.0,
                  mode, int(offset_px), action),
            daemon=True,
        )
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _status(self, msg):
        if self.on_status:
            self.on_status(msg)

    def _fire_action(self, sx, sy, action):
        """Move onto the target, let Windows register the new cursor spot, then fire the bound
        input with an explicit press / short hold / release. Firing immediately after moving —
        or with a zero-length press — is the usual reason an input silently fails to register
        in the target app; the small delays are what make it consistent."""
        self._mouse.position = (sx, sy)
        time.sleep(0.008)              # let SetCursorPos take effect before the input event
        kind, value = action
        dev = self._mouse if kind == "mouse" else self._keys
        dev.press(value)
        time.sleep(0.02)               # brief hold so the target app sees a real press
        dev.release(value)

    def _run(self, color, region, tolerance, interval, mode, offset_px, action):
        target = np.array(color, dtype=np.int16)   # int16: diffs are within [-255, 255]
        mon = {k: region[k] for k in ("left", "top", "width", "height")}
        clicks = 0
        last_status = 0.0
        # Track-mode state: the last locked pixel (region coords) for frame-to-frame target
        # continuity, and a float "virtual cursor" that glides toward the target.
        lock_pt = None
        vx = vy = None
        last_set = None
        try:
            with mss.mss() as sct:
                while not self._stop.is_set():
                    t0 = time.perf_counter()
                    shot = sct.grab(mon)
                    arr = np.frombuffer(shot.bgra, dtype=np.uint8).reshape(shot.height, shot.width, 4)
                    rgb = arr[:, :, 2::-1].astype(np.int16)      # BGRA -> RGB
                    mask = (np.abs(rgb - target) <= tolerance).all(axis=2)
                    ys, xs = np.nonzero(mask)
                    if xs.size:
                        if mode == "track" and lock_pt is not None:
                            # Continuity: follow the matched pixel nearest to where we already
                            # are, instead of re-deriving a center every frame — otherwise the
                            # target hops between blobs and the cursor jitters.
                            idx = np.argmin((xs - lock_pt[0]) ** 2 + (ys - lock_pt[1]) ** 2)
                        else:
                            # Acquisition: median location is robust when several blobs match;
                            # snap it to the nearest pixel that actually matches so we always
                            # land on-color.
                            mx, my = np.median(xs), np.median(ys)
                            idx = np.argmin((xs - mx) ** 2 + (ys - my) ** 2)
                        px, py = int(xs[idx]), int(ys[idx])
                        sx = region["left"] + px
                        sy = region["top"] + py
                        if mode == "track":
                            lock_pt = (px, py)
                            tx, ty = float(sx + offset_px), float(sy)
                            if vx is None:
                                vx, vy = tx, ty              # fresh acquisition: snap
                            else:
                                dx, dy = tx - vx, ty - vy
                                dist = (dx * dx + dy * dy) ** 0.5
                                if dist > self.SNAP_DIST:
                                    vx, vy = tx, ty
                                elif dist > self.DEADBAND:
                                    vx += dx * self.SMOOTH_ALPHA
                                    vy += dy * self.SMOOTH_ALPHA
                            pos = (round(vx), round(vy))
                            if pos != last_set:              # skip redundant SetCursorPos calls
                                self._mouse.position = pos
                                last_set = pos
                            now = time.monotonic()
                            if now - last_status >= self.STATUS_MIN_INTERVAL:
                                self._status(f"Locked  ·  {pos}  ·  {xs.size} px matched")
                                last_status = now
                        else:
                            self._fire_action(sx, sy, action)
                            clicks += 1
                            self._status(f"Firing  ·  {clicks} fired  ·  ({sx},{sy})  ·  {xs.size} px matched")
                    else:
                        if mode == "track":
                            # Color gone: release the cursor and forget the smoothing state so
                            # the next appearance snaps straight onto it.
                            lock_pt = None
                            vx = vy = None
                            last_set = None
                            now = time.monotonic()
                            if now - last_status >= self.STATUS_MIN_INTERVAL:
                                self._status("Locked  ·  waiting for color…")
                                last_status = now
                        else:
                            self._status(f"Watching  ·  color not in region  ·  {clicks} fired so far")
                    if mode == "track":
                        elapsed = time.perf_counter() - t0
                        self._stop.wait(max(0.0, self.TRACK_POLL - elapsed))
                    else:
                        self._stop.wait(interval)
        except Exception as exc:  # surface capture/input errors to the UI instead of dying silently
            self._status(f"Error: {exc}")
            return
        self._status(f"Stopped  ·  {clicks} total fired" if mode == "click" else "Lock stopped")
