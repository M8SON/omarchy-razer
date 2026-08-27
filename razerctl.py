#!/usr/bin/env python3
"""
Backend for the Omarchy razer plugin.

Everything goes through openrazer-daemon over D-Bus. Raw sysfs is deliberately
not used: on some models (Huntsman V3 Pro Mini) matrix_brightness and
device_mode accept writes, report success, and change nothing -- while the
daemon reports the true values. Never write device_mode: driver mode (0x03)
silently disables the keyboard's HID input until it is physically replugged.

This helper writes no files. Everything it reports is read live from the
daemon, which is both simpler and more correct than a local cache: the daemon
already tracks the active effect and its colours, and reading them picks up
changes made by other clients (polychromatic, razer-cli) that a cache cannot
see.

Two modes:

  one-shot   razerctl.py list | brightness | effect | dpi | pollrate | theme ...
  serve      razerctl.py serve

`serve` reads one JSON argv array per line on stdin and writes one JSON object
per line on stdout. It exists because importing openrazer costs ~92ms while the
D-Bus write itself costs ~4ms -- paying that import per colour-wheel sample
capped updates at ~9/sec. Held open, samples land in single-digit milliseconds.
"""

import json
import os
import stat
import sys

# Effects we expose, in menu order: (name, gating capability, colours consumed).
#
# breath_triple (3 colours) and wheel (a wheel-zone effect) are deliberately
# absent: three pickers is more UI than the effect earns, and wheel belongs
# with per-zone control, which this plugin does not do yet.
EFFECTS = [
    ("spectrum",         "lighting_spectrum",          0),
    ("static",           "lighting_static",            1),
    ("breath",           "lighting_breath_single",     1),
    ("breath_dual",      "lighting_breath_dual",       2),
    ("breath_random",    "lighting_breath_random",     0),
    ("wave",             "lighting_wave",              0),
    ("reactive",         "lighting_reactive",          1),
    ("ripple",           "lighting_ripple",            1),
    ("ripple_random",    "lighting_ripple_random",     0),
    ("starlight",        "lighting_starlight_single",  1),
    ("starlight_dual",   "lighting_starlight_dual",    2),
    ("starlight_random", "lighting_starlight_random",  0),
    ("none",             "lighting_none",              0),
]

_manager = None

# The daemon reports its internal effect names; the plugin uses short ones.
EFFECT_ALIASES = {
    "breathsingle": "breath",
    "breathdual": "breath_dual",
    "breathrandom": "breath_random",
    "starlightsingle": "starlight",
    "starlightdual": "starlight_dual",
    "starlightrandom": "starlight_random",
    "ripplerandom": "ripple_random",
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
    # Always live. Because a static apply calls fx.static() before painting the
    # frame, the daemon's own bookkeeping is correct and needs no second-
    # guessing -- which also means an effect set in polychromatic just works.
    try:
        raw = str(device.fx.effect)
    except Exception:
        return None
    return EFFECT_ALIASES.get(raw.lower(), raw)


def read_colors(device):
    """Live effect colours from the daemon.

    getEffectColors() returns 9 bytes: three RGB triples, for the effects that
    take one, two, or three colours. This is authoritative for everything this
    plugin sets, because a static apply calls fx.static() before painting the
    custom frame -- so the daemon holds the real colour even though a custom
    frame on its own would not update it.
    """
    try:
        raw = list(bytes(device.fx.colors))
    except Exception:
        return None, None
    primary = [int(c) for c in raw[0:3]] if len(raw) >= 3 else None
    secondary = [int(c) for c in raw[3:6]] if len(raw) >= 6 else None
    return primary, secondary


def read_battery(device):
    """Charge level for wireless devices; None for wired ones.

    battery_level exists on every device class but raises NotImplementedError
    on wired hardware, and the daemon reports -1 when it has no reading.
    """
    try:
        level = device.battery_level
    except Exception:
        return None
    try:
        level = int(round(float(level)))
    except (TypeError, ValueError):
        return None
    if level < 0:
        return None
    try:
        charging = bool(device.is_charging)
    except Exception:
        charging = False
    return {"level": max(0, min(100, level)), "charging": charging}


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


def color_slots(device):
    """How many colours each supported effect consumes, for the UI."""
    out = {}
    for name, capability, count in EFFECTS:
        try:
            if device.has(capability):
                out[name] = count
        except Exception:
            pass
    return out


def read_dpi(device):
    try:
        x, y = device.dpi
    except Exception:
        return None
    info = {"x": int(x), "y": int(y)}
    try:
        info["max"] = int(device.max_dpi)
    except Exception:
        info["max"] = None
    # Mice with discrete DPI steps advertise them. Reported so the UI can snap
    # the slider instead of sending a value the daemon will silently move.
    try:
        available = [int(v) for v in device.available_dpi]
        if available:
            info["available"] = sorted(available)
    except Exception:
        pass
    return info


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
    out = []
    for device in devices():
        primary, secondary = read_colors(device)
        out.append({
            "serial": device.serial,
            "name": device.name,
            "type": device.type,
            "brightness": read_brightness(device),
            "effect": read_effect(device),
            "color": primary,
            "color2": secondary,
            "effects": supported(device),
            "colorSlots": color_slots(device),
            "battery": read_battery(device),
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


def triple(values, offset, label):
    chunk = values[offset:offset + 3]
    if len(chunk) < 3:
        fail("effect needs %s r g b" % label)
    try:
        return tuple(max(0, min(255, int(c))) for c in chunk)
    except (ValueError, TypeError):
        fail("effect needs %s r g b as numbers" % label)


def cmd_effect(serial, name, rgb):
    entry = next((e for e in EFFECTS if e[0] == name), None)
    if entry is None:
        fail("unknown effect %s" % name)
    _, capability, slots = entry

    device = find(serial)
    try:
        if not device.has(capability):
            fail("%s does not support %s" % (device.name, name))
    except CommandError:
        raise
    except Exception:
        pass

    r = g = b = 0
    r2 = g2 = b2 = 0
    if slots >= 1:
        r, g, b = triple(rgb, 0, "a")
    if slots >= 2:
        r2, g2, b2 = triple(rgb, 3, "a second")

    fx = device.fx
    try:
        if name == "spectrum":
            fx.spectrum()
        elif name == "static":
            # Both, and the order is load-bearing -- frame FIRST, named effect
            # LAST. It used to be the other way round, which left the number
            # row stale on a Huntsman V3 Pro Mini: the custom frame does not
            # address that row (the firmware keeps it for its own indicator),
            # so whichever call lands last decides whether that row is right.
            # A named effect reaches every key; the custom frame does not.
            #
            #   the custom frame lands instantly, so the drag has no lag
            #   fx.static() then fixes the rows the frame cannot reach, tells
            #     the daemon what the device is doing so fx.effect and
            #     fx.colors read back correctly, and gets "static" plus the
            #     colour into persistence.conf for restore_persistence.
            #
            # fx.static() costs ~1.3ms against ~7ms for the frame, so paying
            # for both still sustains ~119 updates/sec.
            apply_static(device, r, g, b)
            fx.static(r, g, b)
        elif name == "breath":
            fx.breath_single(r, g, b)
        elif name == "breath_dual":
            fx.breath_dual(r, g, b, r2, g2, b2)
        elif name == "breath_random":
            fx.breath_random()
        elif name == "wave":
            fx.wave(1)
        elif name == "reactive":
            fx.reactive(r, g, b, 2)
        elif name == "ripple":
            fx.ripple(r, g, b, 0.05)
        elif name == "ripple_random":
            fx.ripple_random(0.05)
        elif name == "starlight":
            fx.starlight_single(r, g, b, 2)
        elif name == "starlight_dual":
            fx.starlight_dual(r, g, b, r2, g2, b2, 2)
        elif name == "starlight_random":
            fx.starlight_random(2)
        elif name == "none":
            fx.none()
    except Exception as exc:
        fail("could not apply %s: %s" % (name, exc))

    primary, secondary = read_colors(device)
    return {"serial": serial, "effect": read_effect(device),
            "color": primary, "color2": secondary}


def cmd_theme(raw):
    """Put one colour on every device that can hold a static effect.

    Used by the theme-set hook. Devices that cannot do static -- a wired
    DeathAdder V3 reports no lighting at all -- are reported as skipped rather
    than treated as a failure, because "no Razer lighting here" is a normal
    outcome for a hook that runs on every theme change.
    """
    text = str(raw).lstrip("#").strip()
    if len(text) != 6:
        fail("theme colour must be 6 hex digits")
    try:
        r, g, b = (int(text[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        fail("theme colour must be 6 hex digits")

    applied, skipped = [], []
    for device in devices():
        try:
            usable = device.has("lighting_static")
        except Exception:
            usable = False
        if not usable:
            skipped.append(device.name)
            continue
        try:
            # Same order as cmd_effect, and for the same reason: the frame
            # first so it lands instantly, the named effect last so it reaches
            # the rows the frame cannot address and leaves the daemon's
            # bookkeeping and persistence correct.
            apply_static(device, r, g, b)
            device.fx.static(r, g, b)
            applied.append(device.name)
        except Exception:
            skipped.append(device.name)
    return {"color": [r, g, b], "applied": applied, "skipped": skipped}


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
    # Refuse a step the device did not advertise, for the same reason as the
    # poll-rate guard below: openrazer accepts it and quietly moves to a
    # neighbouring value, which reads as the control being broken. The UI snaps
    # the slider to these, so a refusal here means something else sent it.
    try:
        available = [int(v) for v in device.available_dpi]
    except Exception:
        available = []
    if available:
        if value not in available:
            fail("%s supports %s DPI, not %d" % (device.name, sorted(available), value))
    else:
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


# The panel's swatch row wants the current theme's colors.toml, which sits at
# a replaceable path under the user's config. Reading it directly from QML
# (FileView) had no size, type, or symlink guard, so an oversized or special
# file there could stall or exhaust the persistent shell process. This is the
# guarded version: open the descriptor without following a final-component
# symlink and without blocking (so a FIFO cannot hang the open), fstat the
# descriptor itself -- not the path, which could be swapped between check and
# read -- and refuse anything that is not a regular file within the cap.
#
# Failure here is never an error to the UI: a theme without colors.toml is
# normal, and the only consequence of any rejection is an empty swatch row.
MAX_THEME_BYTES = 64 * 1024

# Only theme palettes are readable, and only from where Omarchy keeps them.
# O_NOFOLLOW below covers the final component only; a symlinked *parent*
# directory would still traverse, so resolve the whole path first and require
# it to land inside the omarchy state or config tree. Same-user either way,
# so this is defense-in-depth, not a privilege boundary.
THEME_ROOTS = tuple(
    os.path.realpath(os.path.join(os.path.expanduser("~"), p))
    for p in (".local/state/omarchy", ".config/omarchy")
)


def theme_path_allowed(path):
    # Containment is checked on the fully resolved path (so Omarchy's
    # `current` symlink into ~/.config/omarchy/themes still works), but the
    # caller opens the *original* path, so O_NOFOLLOW keeps refusing a
    # symlink as the final component just as before.
    if os.path.basename(path) != "colors.toml":
        return False
    real = os.path.realpath(path)
    if os.path.basename(real) != "colors.toml":
        return False
    return any(real.startswith(root + os.sep) for root in THEME_ROOTS)


def cmd_read_theme(path):
    if not theme_path_allowed(path):
        return {"themeText": ""}
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return {"themeText": ""}
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_THEME_BYTES:
            return {"themeText": ""}
        # Regular files never block on read; drop O_NONBLOCK now that the type
        # is proven so a slow filesystem cannot surface EAGAIN.
        os.set_blocking(fd, True)
        chunks = []
        remaining = MAX_THEME_BYTES
        while remaining > 0:
            chunk = os.read(fd, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
    except OSError:
        return {"themeText": ""}
    finally:
        os.close(fd)
    return {"themeText": data.decode("utf-8", "replace")}


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
            fail("usage: effect <serial> <name> [r g b [r2 g2 b2]]")
        return cmd_effect(args[0], args[1], args[2:8])
    if command == "theme":
        if len(args) != 1:
            fail("usage: theme <rrggbb>")
        return cmd_theme(args[0])
    if command == "readtheme":
        if len(args) != 1:
            fail("usage: readtheme <path to colors.toml>")
        return cmd_read_theme(args[0])
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
