#!/usr/bin/env python3
"""
Backend for the Omarchy razer plugin.

Everything goes through openrazer-daemon over D-Bus. Raw sysfs is deliberately
not used: on some models (Huntsman V3 Pro Mini) matrix_brightness and
device_mode accept writes, report success, and change nothing -- while the
daemon reports the true values. Never write device_mode: driver mode (0x03)
silently disables the keyboard's HID input until it is physically replugged.

Two modes:

  one-shot   razerctl.py list | brightness | effect | dpi | pollrate ...
  serve      razerctl.py serve

`serve` reads one JSON argv array per line on stdin and writes one JSON object
per line on stdout. It exists because importing openrazer costs ~92ms while the
D-Bus write itself costs ~4ms -- paying that import per colour-wheel sample
capped updates at ~9/sec. Held open, samples land in single-digit milliseconds.
"""

import json
import os
import sys

# Effects we expose, in menu order, mapped to the openrazer capability that
# gates them and whether they consume the RGB triple.
EFFECTS = [
    ("spectrum",  "lighting_spectrum",         False),
    ("static",    "lighting_static",           True),
    ("breath",    "lighting_breath_single",    True),
    ("wave",      "lighting_wave",             False),
    ("reactive",  "lighting_reactive",         True),
    ("starlight", "lighting_starlight_single", True),
    ("none",      "lighting_none",             False),
]

_manager = None

# What this plugin last applied, per serial: {"effect": str, "color": [r,g,b]}.
#
# Persisted rather than kept in memory because device.fx.effect is client-side
# bookkeeping that advanced.draw() never updates -- after a custom frame it
# reports whichever *named* effect was set last. The helper also exits when the
# panel has been closed for a while, so in-memory tracking would forget a solid
# colour on every idle cycle and show "Off" for a lit keyboard. Carrying the
# colour too lets the wheel reopen where the user left it.
STATE_PATH = os.path.join(
    os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"),
    "omarchy-razer", "state.json")

_state = {}


def load_state():
    global _state
    try:
        with open(STATE_PATH) as handle:
            loaded = json.load(handle)
        _state = loaded if isinstance(loaded, dict) else {}
    except Exception:
        _state = {}


def save_state():
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w") as handle:
            json.dump(_state, handle)
        os.replace(tmp, STATE_PATH)
    except Exception:
        # Losing the cache costs a stale effect label, never a failed command.
        pass


def remember(serial, effect, color=None, baseline=None):
    entry = {"effect": effect}
    if color is not None:
        entry["color"] = list(color)
    elif serial in _state and "color" in _state[serial]:
        entry["color"] = _state[serial]["color"]
    # What fx.effect read at the moment we painted a custom frame. While it
    # still reads that, our frame is what is on screen; once it changes,
    # something else set an effect and the live value is the truth.
    if baseline is not None:
        entry["baseline"] = baseline
    _state[serial] = entry
    save_state()

# The daemon reports its internal effect names; the plugin uses short ones.
EFFECT_ALIASES = {
    "breathsingle": "breath",
    "breathdual": "breath",
    "breathrandom": "breath",
    "starlightsingle": "starlight",
    "starlightdual": "starlight",
    "starlightrandom": "starlight",
}


class CommandError(Exception):
    """A failure that must not take the serve loop down with it."""


def fail(message):
    raise CommandError(str(message))


def manager(refresh=False):
    # Cached across commands in serve mode; the import is the expensive part.
    global _manager
    if _manager is None or refresh:
        try:
            from openrazer.client import DeviceManager
        except ImportError:
            fail("python-openrazer is not installed")
        try:
            _manager = DeviceManager()
        except Exception as exc:
            _manager = None
            fail("openrazer-daemon unreachable: %s" % exc)
    return _manager


def devices():
    try:
        return manager().devices
    except CommandError:
        raise
    except Exception:
        # A daemon restart invalidates the cached manager; rebuild once before
        # giving up, so serve mode survives `systemctl --user restart`.
        return manager(refresh=True).devices


def find(serial):
    for device in devices():
        if device.serial == serial:
            return device
    fail("no device with serial %s" % serial)


def read_brightness(device):
    # The DeathAdder V3 has no getBrightness DBus method at all; asking raises
    # UnknownMethod. A device without brightness is not an error, just absent.
    try:
        return round(float(device.brightness))
    except Exception:
        return None


def read_effect(device):
    try:
        raw = str(device.fx.effect)
    except Exception:
        raw = None
    effect = EFFECT_ALIASES.get(raw.lower(), raw) if raw is not None else None

    # A custom frame never updates fx.effect, so it keeps reporting whichever
    # named effect preceded it. Trust our record only while fx.effect still
    # matches the value captured when the frame was drawn; the moment it
    # differs, another client set an effect and the live value wins.
    entry = _state.get(device.serial)
    if (entry and entry.get("effect") == "static"
            and entry.get("baseline") is not None
            and raw == entry["baseline"]):
        return "static"
    return effect


def read_color(device):
    entry = _state.get(device.serial)
    color = entry.get("color") if entry else None
    if isinstance(color, list) and len(color) == 3:
        return [int(c) for c in color]
    return None


def apply_static(device, r, g, b):
    """Fill every key with one colour, without the firmware's crossfade.

    fx.static() sets a *named* effect, and at least the Huntsman V3 Pro Mini
    ramps between named effects over ~1.5s. A per-key custom frame is meant for
    animation and lands immediately, so prefer it and fall back if unavailable.
    """
    try:
        adv = device.fx.advanced
        if adv is None or not adv.rows or not adv.cols:
            return False
        matrix = adv.matrix
        color = (r, g, b)
        for row in range(adv.rows):
            for col in range(adv.cols):
                matrix[row, col] = color
        adv.draw()
        return True
    except Exception:
        return False


def supported(device):
    names = []
    for name, capability, _ in EFFECTS:
        try:
            if device.has(capability):
                names.append(name)
        except Exception:
            pass
    return names


def read_dpi(device):
    try:
        x, y = device.dpi
        return {"x": int(x), "y": int(y), "max": int(device.max_dpi)}
    except Exception:
        return None


def read_stages(device):
    # (active_index, [(x, y), ...]) -- we expose the X values as presets.
    try:
        _, stages = device.dpi_stages
        return [int(s[0]) for s in stages]
    except Exception:
        return []


def read_poll(device):
    try:
        current = int(device.poll_rate)
    except Exception:
        return None
    try:
        options = [int(r) for r in device.supported_poll_rates]
    except Exception:
        options = [current]
    return {"current": current, "options": options}


def cmd_list():
    # Cheap re-read: the file is tiny, and a long-lived serve process would
    # otherwise never see state written by a one-shot invocation.
    load_state()
    out = []
    for device in devices():
        out.append({
            "serial": device.serial,
            "name": device.name,
            "type": device.type,
            "brightness": read_brightness(device),
            "effect": read_effect(device),
            "color": read_color(device),
            "effects": supported(device),
            "dpi": read_dpi(device),
            "dpiStages": read_stages(device),
            "poll": read_poll(device),
        })
    return {"devices": out}


def cmd_brightness(serial, raw):
    try:
        value = max(0.0, min(100.0, float(raw)))
    except ValueError:
        fail("brightness must be a number 0-100")
    device = find(serial)
    try:
        device.brightness = value
    except Exception as exc:
        fail("could not set brightness: %s" % exc)
    return {"serial": serial, "brightness": read_brightness(device)}


def cmd_effect(serial, name, rgb):
    entry = next((e for e in EFFECTS if e[0] == name), None)
    if entry is None:
        fail("unknown effect %s" % name)
    _, capability, takes_color = entry

    device = find(serial)
    try:
        if not device.has(capability):
            fail("%s does not support %s" % (device.name, name))
    except CommandError:
        raise
    except Exception:
        pass

    r = g = b = 0
    if takes_color:
        try:
            r, g, b = (max(0, min(255, int(c))) for c in rgb)
        except (ValueError, TypeError):
            fail("effect %s needs an r g b triple" % name)

    fx = device.fx
    used_custom = False
    try:
        if name == "spectrum":
            fx.spectrum()
        elif name == "static":
            used_custom = apply_static(device, r, g, b)
            if not used_custom:
                fx.static(r, g, b)
        elif name == "breath":
            fx.breath_single(r, g, b)
        elif name == "wave":
            fx.wave(1)
        elif name == "reactive":
            fx.reactive(r, g, b, 2)
        elif name == "starlight":
            fx.starlight_single(r, g, b, 2)
        elif name == "none":
            fx.none()
    except Exception as exc:
        fail("could not apply %s: %s" % (name, exc))

    baseline = None
    if used_custom:
        try:
            baseline = str(device.fx.effect)
        except Exception:
            baseline = None
    remember(serial, name, (r, g, b) if takes_color else None, baseline)

    return {"serial": serial, "effect": read_effect(device)}


def cmd_dpi(serial, raw):
    try:
        value = int(float(raw))
    except ValueError:
        fail("dpi must be a number")
    device = find(serial)
    try:
        top = int(device.max_dpi)
    except Exception:
        fail("%s does not support DPI control" % device.name)
    value = max(100, min(top, value))
    try:
        device.dpi = (value, value)
    except Exception as exc:
        fail("could not set dpi: %s" % exc)
    return {"serial": serial, "dpi": read_dpi(device)}


def cmd_pollrate(serial, raw):
    try:
        value = int(raw)
    except ValueError:
        fail("poll rate must be a number")
    device = find(serial)
    # Refuse a rate the device did not advertise: openrazer accepts one and
    # silently clamps, which reads as the control being broken.
    try:
        options = [int(r) for r in device.supported_poll_rates]
        if value not in options:
            fail("%s supports %s Hz, not %d" % (device.name, options, value))
    except CommandError:
        raise
    except Exception:
        pass
    try:
        device.poll_rate = value
    except Exception as exc:
        fail("could not set poll rate: %s" % exc)
    return {"serial": serial, "poll": read_poll(device)}


def dispatch(argv):
    if not argv:
        fail("empty command")
    command, args = argv[0], argv[1:]
    if command == "list":
        return cmd_list()
    if command == "brightness":
        if len(args) != 2:
            fail("usage: brightness <serial> <0-100>")
        return cmd_brightness(args[0], args[1])
    if command == "effect":
        if len(args) < 2:
            fail("usage: effect <serial> <name> [r g b]")
        return cmd_effect(args[0], args[1], args[2:5])
    if command == "dpi":
        if len(args) != 2:
            fail("usage: dpi <serial> <value>")
        return cmd_dpi(args[0], args[1])
    if command == "pollrate":
        if len(args) != 2:
            fail("usage: pollrate <serial> <hz>")
        return cmd_pollrate(args[0], args[1])
    fail("unknown command %s" % command)


def serve():
    # Warm the import and the D-Bus connection before announcing readiness, so
    # the first real command is as fast as the rest.
    try:
        manager()
        ready = {"ok": True, "cmd": "ready"}
    except CommandError as exc:
        ready = {"ok": False, "cmd": "ready", "error": str(exc)}
    sys.stdout.write(json.dumps(ready) + "\n")
    sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            argv = json.loads(line)
            if not isinstance(argv, list):
                raise CommandError("command must be a JSON array")
            payload = dispatch([str(a) for a in argv])
            payload["ok"] = True
            payload["cmd"] = str(argv[0]) if argv else ""
        except CommandError as exc:
            payload = {"ok": False, "cmd": "", "error": str(exc)}
        except Exception as exc:
            payload = {"ok": False, "cmd": "", "error": "%s: %s" % (type(exc).__name__, exc)}
        sys.stdout.write(json.dumps(payload) + "\n")
        sys.stdout.flush()


def main():
    load_state()
    if len(sys.argv) < 2:
        print(json.dumps({"ok": False, "error": "usage: razerctl.py serve|list|..."}))
        sys.exit(1)
    if sys.argv[1] == "serve":
        serve()
        return
    try:
        payload = dispatch(sys.argv[1:])
    except CommandError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        sys.exit(1)
    payload["ok"] = True
    print(json.dumps(payload))


if __name__ == "__main__":
    main()
