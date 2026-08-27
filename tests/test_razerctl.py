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

print()
if FAILURES:
    print("%d failing: %s" % (len(FAILURES), ", ".join(FAILURES)))
    sys.exit(1)
print("all passing")
