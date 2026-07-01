"""Background worker that scans the selected screen region for the target color.

Two modes, sharing the same scan/match loop:
- "click": left-clicks the color repeatedly while it stays visible (the original auto-clicker).
- "track": moves the cursor onto the color (offset by a horizontal pixel amount) and keeps it
  there while the color stays visible, without clicking — used by the "Lock" feature.

Runs on its own daemon thread so the Tk GUI stays responsive. A threading.Event is used
both to stop the loop and to sleep between cycles (so Stop takes effect immediately). Only one
mode can run at a time — start() is a no-op while a scan is already in progress, which is what
keeps the click-mode Start/Stop and the track-mode Lock from ever fighting over the mouse.
"""

import threading
import time

import mss
import numpy as np
from pynput.mouse import Button, Controller


class ClickWorker:
    # Lock/track polls this fast (~100x/sec) so the cursor stays pinned to the color, independent
    # of the click-interval slider (which only paces click mode). Anything slower feels like the
    # lock "isn't holding" because you can visibly drag the cursor away between updates.
    TRACK_POLL = 0.01
    # Tracking finds the color far faster than the GUI needs to refresh; only push a status line
    # this often, or the flood of cross-thread updates makes the window sluggish.
    STATUS_MIN_INTERVAL = 0.1

    def __init__(self, on_status=None):
        self.on_status = on_status
        self._thread = None
        self._stop = threading.Event()
        self._mouse = Controller()

    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self, color, region, tolerance, interval_ms, mode="click", offset_px=0):
        """color: (r,g,b); region: {left,top,width,height}; tolerance: 0-255; interval_ms: int.
        mode: "click" (default) or "track". offset_px: horizontal pixel shift, "track" mode only."""
        if self.is_running():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            args=(tuple(color), dict(region), float(tolerance), max(interval_ms, 10) / 1000.0,
                  mode, int(offset_px)),
            daemon=True,
        )
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _status(self, msg):
        if self.on_status:
            self.on_status(msg)

    def _click_at(self, sx, sy):
        """Move onto the target, let Windows register the new cursor spot, then do an explicit
        press / short hold / release. Clicking immediately after moving — or with a zero-length
        press — is the usual reason a click silently fails to register in the target app; the
        small delays are what make it consistent."""
        self._mouse.position = (sx, sy)
        time.sleep(0.008)              # let SetCursorPos take effect before the button event
        self._mouse.press(Button.left)
        time.sleep(0.02)               # brief hold so the target app sees a real click
        self._mouse.release(Button.left)

    def _run(self, color, region, tolerance, interval, mode, offset_px):
        target = np.array(color, dtype=np.int32)          # RGB; int32 so squares don't overflow
        tol_sq = tolerance * tolerance                     # compare squared distance
        mon = {k: region[k] for k in ("left", "top", "width", "height")}
        clicks = 0
        # Track mode follows the color continuously (fast fixed poll); click mode waits the
        # user's chosen interval between clicks.
        loop_wait = self.TRACK_POLL if mode == "track" else interval
        last_status = 0.0
        try:
            with mss.mss() as sct:
                while not self._stop.is_set():
                    shot = sct.grab(mon)
                    arr = np.frombuffer(shot.bgra, dtype=np.uint8).reshape(shot.height, shot.width, 4)
                    rgb = arr[:, :, 2::-1].astype(np.int32)      # BGRA -> RGB (int32: no overflow)
                    diff = rgb - target
                    dist_sq = np.einsum("ijk,ijk->ij", diff, diff)
                    ys, xs = np.where(dist_sq <= tol_sq)
                    if xs.size:
                        # Median location is robust when several blobs match; snap it to the
                        # nearest pixel that actually matches so we always land on-color.
                        mx, my = np.median(xs), np.median(ys)
                        idx = np.argmin((xs - mx) ** 2 + (ys - my) ** 2)
                        sx = region["left"] + int(xs[idx])
                        sy = region["top"] + int(ys[idx])
                        if mode == "track":
                            tx = sx + offset_px
                            self._mouse.position = (tx, sy)
                            now = time.monotonic()
                            if now - last_status >= self.STATUS_MIN_INTERVAL:
                                self._status(f"Locked  ·  ({tx},{sy})  ·  {xs.size} px matched")
                                last_status = now
                        else:
                            self._click_at(sx, sy)
                            clicks += 1
                            self._status(f"Clicking  ·  {clicks} clicks  ·  ({sx},{sy})  ·  {xs.size} px matched")
                    else:
                        if mode == "track":
                            now = time.monotonic()
                            if now - last_status >= self.STATUS_MIN_INTERVAL:
                                self._status("Locked  ·  waiting for color…")
                                last_status = now
                        else:
                            self._status(f"Watching  ·  color not in region  ·  {clicks} clicks so far")
                    self._stop.wait(loop_wait)
        except Exception as exc:  # surface capture/click errors to the UI instead of dying silently
            self._status(f"Error: {exc}")
            return
        self._status(f"Stopped  ·  {clicks} total clicks" if mode == "click" else "Lock stopped")
