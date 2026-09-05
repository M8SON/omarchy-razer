#!/usr/bin/env python3
"""Tests for the parts of razerctl.py that local hardware cannot reach.

Both wired devices here raise NotImplementedError for battery_level and
available_dpi, so only the "absent" branch ever runs in practice. These stub a
device instead, which covers the branches that matter: the daemon's -1
no-reading sentinel, clamping, the charging flag, and refusing a DPI step a
mouse never advertised.

No hardware, no daemon, no openrazer import -- razerctl only imports openrazer
inside manager(), so the module loads fine without it.

    python3 tests/test_razerctl.py
"""

import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    "razerctl", os.path.join(HERE, os.pardir, "razerctl.py"))
razerctl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(razerctl)

FAILURES = []


def check(label, got, want):
    if got == want:
        print("  ok    %s" % label)
    else:
        print("  FAIL  %s\n          got  %r\n          want %r" % (label, got, want))
        FAILURES.append(label)


class Raises:
    """Marker: reading this attribute raises, as a wired device does."""

    def __init__(self, exc=NotImplementedError):
        self.exc = exc


class FakeDevice:
    def __init__(self, name="Fake", serial="SERIAL", **attrs):
        self.name = name
        self.serial = serial
        self._attrs = attrs
        self.dpi_set_to = None

    def __getattr__(self, item):
        if item not in self._attrs:
            raise AttributeError(item)
        value = self._attrs[item]
        if isinstance(value, Raises):
            raise value.exc()
        return value

    def __setattr__(self, key, value):
        if key == "dpi":
            self.__dict__["dpi_set_to"] = value
            self.__dict__.setdefault("_attrs", {})["dpi"] = value
            return
        object.__setattr__(self, key, value)


print("read_battery")
check("wireless device reports level and charging",
      razerctl.read_battery(FakeDevice(battery_level=87, is_charging=False)),
      {"level": 87, "charging": False})
check("charging flag passes through",
      razerctl.read_battery(FakeDevice(battery_level=100, is_charging=True)),
      {"level": 100, "charging": True})
check("daemon's -1 means no reading, not 0%",
      razerctl.read_battery(FakeDevice(battery_level=-1, is_charging=False)),
      None)
check("wired device raising NotImplementedError is absent, not an error",
      razerctl.read_battery(FakeDevice(battery_level=Raises(), is_charging=Raises())),
      None)
check("a level but an unreadable charging flag still reports the level",
      razerctl.read_battery(FakeDevice(battery_level=42, is_charging=Raises())),
      {"level": 42, "charging": False})
check("out-of-range level is clamped",
      razerctl.read_battery(FakeDevice(battery_level=150, is_charging=False)),
      {"level": 100, "charging": False})
check("float level is rounded",
      razerctl.read_battery(FakeDevice(battery_level=66.7, is_charging=False)),
      {"level": 67, "charging": False})
check("None level is absent",
      razerctl.read_battery(FakeDevice(battery_level=None, is_charging=False)),
      None)

print("read_dpi")
check("discrete steps are reported, sorted",
      razerctl.read_dpi(FakeDevice(dpi=(800, 800), max_dpi=16000,
                                   available_dpi=[1600, 400, 800])),
      {"x": 800, "y": 800, "max": 16000, "available": [400, 800, 1600]})
check("a mouse with a continuous range reports no step list",
      razerctl.read_dpi(FakeDevice(dpi=(800, 800), max_dpi=30000,
                                   available_dpi=Raises())),
      {"x": 800, "y": 800, "max": 30000})
check("an empty step list is not reported either",
      razerctl.read_dpi(FakeDevice(dpi=(400, 400), max_dpi=8000, available_dpi=[])),
      {"x": 400, "y": 400, "max": 8000})
check("an unreadable max still yields the current dpi",
      razerctl.read_dpi(FakeDevice(dpi=(1200, 1200), max_dpi=Raises(),
                                   available_dpi=Raises())),
      {"x": 1200, "y": 1200, "max": None})
check("a keyboard has no dpi at all",
      razerctl.read_dpi(FakeDevice(dpi=Raises(AttributeError))),
      None)

print("cmd_dpi")


def with_device(device):
    razerctl.devices = lambda: [device]


def run_dpi(device, value):
    with_device(device)
    try:
        return razerctl.cmd_dpi(device.serial, value)
    except razerctl.CommandError as exc:
        return "refused: %s" % exc


discrete = FakeDevice(name="Stepped Mouse", dpi=(800, 800), max_dpi=16000,
                      available_dpi=[400, 800, 1600, 3200])
check("an advertised step is accepted",
      run_dpi(discrete, "1600")["dpi"]["x"], 1600)
check("an unadvertised step is refused rather than silently moved",
      run_dpi(discrete, "900"),
      "refused: Stepped Mouse supports [400, 800, 1600, 3200] DPI, not 900")

continuous = FakeDevice(name="Smooth Mouse", dpi=(800, 800), max_dpi=30000,
                        available_dpi=Raises())
check("a continuous mouse clamps to its maximum",
      run_dpi(continuous, "99999")["dpi"]["x"], 30000)
check("and to a sane floor",
      run_dpi(continuous, "1")["dpi"]["x"], 100)
check("a non-numeric value is refused",
      run_dpi(continuous, "fast"), "refused: dpi must be a number")

# cmd_read_theme: the descriptor-bound read behind the panel's swatch row.
# Every rejection returns empty text rather than raising -- a bad theme file
# must cost the UI nothing but its swatches.
import tempfile

with tempfile.TemporaryDirectory() as td:
    # cmd_read_theme only serves files inside the omarchy trees; stand the
    # temp dir in for them so the tests exercise the read path, then check
    # containment itself against the real (untouched) roots below.
    saved_roots = razerctl.THEME_ROOTS
    razerctl.THEME_ROOTS = (os.path.realpath(td),)

    good = os.path.join(td, "colors.toml")
    with open(good, "w") as fh:
        fh.write('accent = "#aabbcc"\n')
    check("a regular colors.toml is read back",
          razerctl.cmd_read_theme(good)["themeText"], 'accent = "#aabbcc"\n')

    # Correctly named, readable, and outside the roots -- so only the
    # containment check can be what refuses it.
    with tempfile.TemporaryDirectory() as elsewhere:
        outside = os.path.join(elsewhere, "colors.toml")
        with open(outside, "w") as fh:
            fh.write('accent = "#aabbcc"\n')
        check("a colors.toml outside the theme roots is refused",
              razerctl.cmd_read_theme(outside), {"themeText": ""})

    escape = os.path.join(td, "escape")
    os.symlink("/etc", escape)
    check("a symlinked parent directory cannot escape the roots",
          razerctl.cmd_read_theme(os.path.join(escape, "colors.toml")),
          {"themeText": ""})

    check("a missing file reads as empty",
          razerctl.cmd_read_theme(os.path.join(td, "absent", "colors.toml")),
          {"themeText": ""})

    check("a path not named colors.toml is refused",
          razerctl.cmd_read_theme(os.path.join(td, "shadow")), {"themeText": ""})

    big = os.path.join(td, "big")
    os.mkdir(big)
    bigfile = os.path.join(big, "colors.toml")
    with open(bigfile, "wb") as fh:
        fh.write(b"x" * (razerctl.MAX_THEME_BYTES + 1))
    check("an oversized file is refused rather than truncated",
          razerctl.cmd_read_theme(bigfile), {"themeText": ""})

    linked = os.path.join(td, "linked")
    os.mkdir(linked)
    os.symlink(good, os.path.join(linked, "colors.toml"))
    check("a symlinked colors.toml is refused",
          razerctl.cmd_read_theme(os.path.join(linked, "colors.toml")),
          {"themeText": ""})

    fifod = os.path.join(td, "fifo")
    os.mkdir(fifod)
    fifo = os.path.join(fifod, "colors.toml")
    os.mkfifo(fifo)
    check("a FIFO neither blocks nor reads",
          razerctl.cmd_read_theme(fifo), {"themeText": ""})

    razerctl.THEME_ROOTS = saved_roots


print("cmd_list / error codes")

saved_devices = razerctl.devices
razerctl.devices = lambda: []
empty = razerctl.cmd_list()
check("an empty device list carries a group-membership hint",
      "openrazer group" in empty.get("hint", ""), True)
razerctl.devices = saved_devices

try:
    razerctl.fail("nope", "setup_required")
except razerctl.CommandError as exc:
    check("CommandError carries its code", exc.code, "setup_required")
    check("CommandError still reads as its message", str(exc), "nope")

try:
    razerctl.fail("plain failure")
except razerctl.CommandError as exc:
    check("a code-less failure has code None", exc.code, None)

print("serve: bounded command lines")

import io
import json

saved_serve = (razerctl.manager, razerctl.dispatch, sys.stdin, sys.stdout)
razerctl.manager = lambda refresh=False: None
razerctl.dispatch = lambda argv: {"echo": argv}

# One line past the cap, then a line exactly at it, then an ordinary command.
# The oversized line must be refused and fully discarded so the two that follow
# still parse -- a partial line left in the buffer would be read as a command.
cap = razerctl.MAX_COMMAND_BYTES
oversized = b'["' + b"x" * cap + b'"]\n'
at_cap = b'["' + b"x" * (cap - 4) + b'"]\n'
sys.stdin = io.TextIOWrapper(io.BytesIO(oversized + at_cap + b'["list"]\n'))
sys.stdout = io.StringIO()
try:
    razerctl.serve()
    replies = [json.loads(l) for l in sys.stdout.getvalue().splitlines()]
finally:
    razerctl.manager, razerctl.dispatch, sys.stdin, sys.stdout = saved_serve

check("serve announces ready", replies[0], {"ok": True, "cmd": "ready"})
check("an oversized line is refused", replies[1]["ok"], False)
check("and says why", "exceeds" in replies[1]["error"], True)
check("a line at the cap is accepted",
      replies[2]["echo"], ["x" * (cap - 4)])
check("the command after an oversized line still runs",
      replies[3], {"echo": ["list"], "ok": True, "cmd": "list"})
check("nothing else was emitted", len(replies), 4)


print("try_start_daemon: fixed executable path")

calls = []
saved_run, saved_sleep = razerctl.subprocess.run, razerctl.time.sleep
razerctl.subprocess.run = lambda argv, **kw: calls.append(argv)
razerctl.time.sleep = lambda s: None
razerctl._daemon_start_attempted = False
try:
    first = razerctl.try_start_daemon()
    second = razerctl.try_start_daemon()
finally:
    razerctl.subprocess.run, razerctl.time.sleep = saved_run, saved_sleep
check("systemctl is run by absolute path, not PATH lookup",
      calls, [["/usr/bin/systemctl", "--user", "start", "openrazer-daemon"]])
check("the first attempt reports it tried", first, True)
check("a second attempt in the same process is skipped", second, False)


print("persistence.py: descriptor-bound razer.conf edit")

spec = importlib.util.spec_from_file_location(
    "persistence", os.path.join(HERE, os.pardir, "persistence.py"))
persistence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(persistence)

def conf_text(d):
    with open(os.path.join(d, "razer.conf")) as fh:
        return fh.read()

def leftovers(d):
    return sorted(n for n in os.listdir(d) if n != "razer.conf")

def refused(d):
    try:
        persistence.enable_persistence(d)
    except persistence.Refused as exc:
        return str(exc)
    return None

with tempfile.TemporaryDirectory() as td:
    fresh = os.path.join(td, "fresh", "openrazer")
    check("a missing directory and file are created",
          persistence.enable_persistence(fresh), "updated")
    check("with just the Startup section",
          conf_text(fresh), "[Startup]\nrestore_persistence = True\n")
    check("and no temp file left behind", leftovers(fresh), [])
    check("a second run leaves it alone",
          persistence.enable_persistence(fresh), "already")

    d = os.path.join(td, "false")
    os.makedirs(d)
    with open(os.path.join(d, "razer.conf"), "w") as fh:
        fh.write("[General]\nverbose_logging = False\n\n[Startup]\n"
                 "restore_persistence = False\nsync_effects_enabled = True\n")
    check("an existing False is flipped in place",
          persistence.enable_persistence(d), "updated")
    check("without touching neighbouring keys",
          conf_text(d), "[General]\nverbose_logging = False\n\n[Startup]\n"
          "restore_persistence = True\nsync_effects_enabled = True\n")
    check("and no temp file left behind", leftovers(d), [])

    d = os.path.join(td, "nokey")
    os.makedirs(d)
    with open(os.path.join(d, "razer.conf"), "w") as fh:
        fh.write("[General]\nverbose_logging = False\n")
    persistence.enable_persistence(d)
    check("a file without the key gains a Startup section",
          conf_text(d), "[General]\nverbose_logging = False\n\n[Startup]\n"
          "restore_persistence = True\n")

    d = os.path.join(td, "linkdir")
    os.symlink(fresh, d)
    check("a symlinked config directory is refused",
          "symlink" in (refused(d) or ""), True)

    d = os.path.join(td, "linkfile")
    os.makedirs(d)
    os.symlink(os.path.join(fresh, "razer.conf"), os.path.join(d, "razer.conf"))
    check("a symlinked razer.conf is refused",
          "symlink" in (refused(d) or ""), True)
    check("and the link target is untouched",
          conf_text(fresh), "[Startup]\nrestore_persistence = True\n")

    d = os.path.join(td, "fifo")
    os.makedirs(d)
    os.mkfifo(os.path.join(d, "razer.conf"))
    check("a FIFO neither blocks nor is rewritten",
          "regular file" in (refused(d) or ""), True)

    d = os.path.join(td, "big")
    os.makedirs(d)
    with open(os.path.join(d, "razer.conf"), "wb") as fh:
        fh.write(b"x" * (persistence.MAX_CONF_BYTES + 1))
    check("an oversized file is refused rather than rewritten",
          "large" in (refused(d) or ""), True)

print()
if FAILURES:
    print("%d failing: %s" % (len(FAILURES), ", ".join(FAILURES)))
    sys.exit(1)
print("all passing")
