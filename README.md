# Razer lighting for Omarchy

Control OpenRazer keyboard and mouse lighting from the Omarchy bar — brightness,
effects, and colour — without the polychromatic tray applet.

![kind](https://img.shields.io/badge/kind-bar--widget-blue) ![omarchy](https://img.shields.io/badge/omarchy-4.x-green)

## Why

Polychromatic's tray applet works, but it rides in on
`libayatana-appindicator`, which exports a DBusMenu and a no-op `Activate`
method and never sets the `ItemIsMenu` property. Omarchy's tray reads that as
"this item has a click action", calls `activate()`, and nothing happens — so the
applet only responds to **right**-click. This plugin is a native bar widget
instead, so it behaves like every other Omarchy widget.

## Install

```bash
omarchy plugin add https://github.com/M8SON/omarchy-razer
bash ~/.config/omarchy/plugins/daedalus.razer/setup.sh   # first time only
omarchy plugin enable daedalus.razer right
```

`setup.sh` installs `openrazer-daemon`, adds you to the `openrazer` group, and
sets `restore_persistence = True`. Skip it only if OpenRazer already works.

## Use

- **Left-click** the bar icon to open the panel
- **Middle-click** to force a refresh
- Brightness slider, effect grid, and — for the effects that take a colour
  (static, breath, reactive, starlight) — a full HSV **colour wheel**: hue
  around the circumference, saturation centre-to-edge, a value slider beneath,
  and a live hex readout
- `r` refreshes, `o` turns lighting off, `Esc` closes

A device appears if *anything* about it is controllable. RGB keyboards and mice
get the lighting controls; a mouse gets **Sensitivity** (DPI slider plus the
device's own stage presets) and **Polling rate** (only the rates the device
actually advertises). A wired DeathAdder V3 reports zero lighting capabilities
but full DPI and poll-rate ones, so it shows the performance controls alone.

When more than one device is controllable, a switcher row appears under the
title.

## Settings

| Key | Default | Meaning |
|-----|---------|---------|
| `refreshIntervalSec` | 30 | How often the open panel re-reads device state |

## Implementation notes

Everything goes through `openrazer-daemon` over D-Bus, via `razerctl.py`.

**The helper is held open, not spawned per command.** Importing `openrazer`
costs ~92 ms while the D-Bus write itself costs ~4 ms, so a process per command
capped the colour wheel at ~9 updates/sec and read as the colour lagging behind
the pointer. `razerctl.py serve` reads one JSON argv array per line on stdin and
replies with one JSON object per line; a colour sample then round-trips in ~3 ms.
The helper starts when the panel opens and exits 45 s after it closes, so its
~32 MB is not held while idle — the bar icon keeps rendering from the last read.
It is restarted automatically if it dies, up to three times per panel session.

`Process.running` is assigned imperatively and **never bound**. Quickshell writes
that property itself when the child exits, and an imperative write from C++
destroys a QML binding permanently — so `running: someFlag` works exactly once,
after which the helper can never be restarted and every command queues forever.

What the plugin last applied is persisted to
`$XDG_STATE_HOME/omarchy-razer/state.json`, because `device.fx.effect` is
client-side bookkeeping that a custom-frame draw never updates: after painting
one it still reports whichever *named* effect preceded it. The file records that
effect plus the `fx.effect` value seen at draw time; while the live value still
matches, the custom frame is what is on screen, and the moment it differs
another client has set an effect and the live value wins. The stored colour also
lets the wheel reopen on the colour the device is actually showing.

**Static calls `fx.static()` *and then* paints a per-key custom frame.** Both
matter. Setting a named effect makes at least the Huntsman V3 Pro Mini
crossfade to the new colour over ~1.5 s, which feels broken on a colour wheel;
the custom frame lands instantly and overrides that ramp. But a custom frame
never updates `fx.effect`, so on its own it leaves the daemon believing the
device is still running whatever effect preceded it — which `restore_persistence`
then faithfully restores at the next boot. Calling `fx.static()` first keeps the
daemon's bookkeeping correct, so `persistence.conf` records `static` plus the
colour and the right thing comes back after a reboot. Devices without per-key
matrix support simply get the named effect. The animated effects stay as named
effects, since the firmware is what runs them.

Raw sysfs is deliberately **not** used, for reasons worth writing down:

- On the Huntsman V3 Pro Mini, `matrix_brightness` accepts writes, reports
  success, and permanently reads back `0`, while the daemon reports the true
  value. `device_mode` behaves the same way.
- **Never write `device_mode = 0x03`.** Driver mode makes raw sysfs effect
  writes take effect, but it also disables the keyboard's HID input entirely —
  no keystrokes until you physically unplug and replug it. This is also why
  there is no "light the keyboard at the LUKS prompt" feature here: it would
  hand you a beautifully lit keyboard you cannot type your passphrase on.
- The daemon restores persisted state *asynchronously* at startup and can
  overwrite a write that lands during the window. The panel re-reads state
  250 ms after every action rather than trusting its own echo.

## Known limitations


- **Single lighting zone.** Effects are applied through `device.fx`, which drives
  the device's main zone. Mice with independently addressable zones (logo,
  scroll wheel, side strips — `device.fx.misc.*` in OpenRazer) will light up,
  but you cannot yet target a zone individually. This is untested against real
  multi-zone hardware; patches welcome, ideally with the device name.
- **Only `static` escapes the firmware crossfade.** The animated effects are run
  by the device, so switching *into* one still ramps.
- **Effect parameters are fixed.** Wave direction, and the reactive/starlight
  response time, use sensible defaults rather than being exposed as controls.
- Setting a poll rate the device did not advertise is refused rather than sent,
  because OpenRazer accepts it and silently clamps — which reads as a broken
  control.

## Requirements

- Omarchy 4.x (Quattro shell)
- `openrazer-daemon` and `python-openrazer`
- Membership in the `openrazer` group

## Notes for other plugin authors

Two things cost a debug cycle here and are documented nowhere:

- **Set `implicitWidth`/`implicitHeight` on the root.** `bar/Bar.qml` sizes each
  slot from `activeItem.implicitWidth`. Without them the slot is 0×0, the icon
  never renders, and you get *no QML error* — the icon `Component` is simply
  never instantiated. Use `implicitWidth: button.implicitWidth`. A full
  `omarchy restart shell` is needed; `rescanPlugins` reloads plugin code but
  does not re-measure the slot.
- **`manifest` is not injected into bar widgets.** `Bar.qml`'s `injectProps()`
  sets only `bar`, `moduleName`, and `settings`, so `manifest.__sourceDir` is
  undefined. First-party plugins use the `omarchyPath` global, which you don't
  get. Resolve bundled files against the QML file's own URL with
  `Qt.resolvedUrl(...)` — see `helperPath` in `Panel.qml`.

## Licence

MIT
