# 7amany's Color-Bot (Windows)

A small desktop tool: pick a color, choose a screen region, and it **left-clicks that color
repeatedly** while the color is visible in the region — or **locks the cursor onto it** without
clicking. Requires a redeemed token (see [Licensing](#licensing) below).

## Install

```powershell
py -m pip install -r requirements.txt
```

Requires Python 3.x (uses the `py` launcher). Dependencies: `mss`, `numpy`, `pynput`, `Pillow`.

## Run

```powershell
py app.py
```

…or just double-click **`run.bat`**.

## How to use

1. On the splash screen, press **Start** to open the control panel.
2. **Target color** — pick one of:
   - **Eyedropper** — the screen freezes; move the mouse (a magnifier loupe shows the exact
     pixel and its RGB/hex), then click to sample that color.
   - **Palette…** — a standard color picker / hex box.
3. **Select region…** — the screen freezes; drag a box around the area to watch.
   Or press **Center** instead: no region needed — it watches an 80×80 box in the exact middle
   of the screen and shows a red crosshair there so you know where. The crosshair is
   click-through and its arms sit outside the scan box, so it never interferes with clicking or
   color matching. Press **Center** again to remove the crosshair and turn the mode off
   (picking a region manually also turns it off).
4. Adjust:
   - **Color tolerance** — how far each R/G/B channel may drift from your color (0 = exact
     match; a pixel matches only when *all three* channels are within the tolerance). Bump this
     up if a color has anti-aliasing/gradients.
   - **Click interval (ms)** — how often the action re-fires while the color stays visible
     (click mode only; Lock tracks continuously on its own fast cadence).
   - **Start delay (s)** — countdown before Start begins, so you can get positioned.
5. Enter a valid token (first run only — see [Licensing](#licensing)).
6. Optional: click **On color: Left Click** and press any mouse button or keyboard key — that
   input is what fires on the color when you press Start (Esc cancels; default is left click).
7. Press **▶ Start** to auto-fire the bound action, or **Lock** to track without clicking
   (see below).

## Lock (cursor tracking, no clicking)

Press **Lock** to continuously move the cursor onto the target color and keep it there for as
long as the color stays visible in the region — without clicking. Toggle it off (or press **F8**)
to release the cursor. Lock and Start/Stop are mutually exclusive — only one can run at a time.

- **Offset (cm)** slider (-0.6 to 0.6): shifts the tracked point horizontally — negative = left of
  the color, positive = right, measured in real centimeters on your screen. The offset is applied
  when Lock is turned on, so change the slider *before* pressing Lock (not while it's running).
- Because Lock actively moves the mouse, clicking the **Lock** button with your own cursor
  while it's already engaged can race with the tracking loop — **F8 is the reliable way to
  disengage it**.

## Binds

Two white bind boxes sit under the token box:

- **On color: …** — what input fires when the color is seen in click mode. Click it, then press
  any mouse button (left/right/middle/…) or keyboard key. Press **Esc** while it says
  "Press mouse button or key…" to cancel.
- **Hotkey: …** — press any key to make it toggle Start/Stop from anywhere (like F8, but
  bindable to a key of your choice). Press **Esc** while it says "Select key…" to cancel.

## Fullscreen & navigation

- **Fullscreen (F11)** button, or press **F11** anywhere, toggles fullscreen. **Esc** exits it.
- **← Back** (bottom-left of the control panel) returns to the splash screen, stopping any
  active click/Lock run first.

## Stopping

- **F8** — global emergency stop for both Start/Stop and Lock, works even when the app isn't focused.
- **■ Stop** button, or toggling **Lock** off.
- Closing the window also stops everything.

## Licensing

Start and Lock both require a redeemed token the first time you run the app. Enter it in the
**Enter Token** box — if it's valid, the app remembers it locally
(`%APPDATA%\7amanys-color-bot\license.json`) and never asks again on that machine. An empty or
invalid token shows **"You Must Enter A Valid Token First"** in red.

The valid/redeemed token list lives in a Google Sheet, not in this app's code — see
[**TOKENS.md**](TOKENS.md) for how to set up the (free) Google Sheet + Apps Script backend and
point the app at it via `server_config.json`. Until that's configured, no token will redeem
successfully.

## Notes / limitations

- Works on the **primary monitor**.
- On first sighting it targets the pixel nearest the *median* of all matching pixels (a robust
  center), snapped to a pixel that actually matches — so it lands on the color, not an empty
  gap between blobs. While Lock holds a target it follows the nearest matching pixel frame to
  frame with light motion smoothing, so the cursor glides instead of jittering.
- The process is set **DPI-aware** so clicks and the cm-based offset land accurately on scaled/
  high-DPI displays.
- Single target color per session. Ask if you want multiple colors, per-color actions, or
  whole-screen scanning added.
