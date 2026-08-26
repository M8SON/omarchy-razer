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

print()
if FAILURES:
    print("%d failing: %s" % (len(FAILURES), ", ".join(FAILURES)))
    sys.exit(1)
print("all passing")
