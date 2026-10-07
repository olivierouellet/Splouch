"""The Pi's health: SoC temperature, under-voltage and throttling, sampled in RAM.

A Pi on a pool deck runs warm and often on a long or thin power cable, and the
symptoms (a laggy board, a reboot mid-meet) do not say which. The firmware keeps
both answers: the temperature, and a `get_throttled` word whose low bits say what
is happening now and whose high bits say what has happened since boot.

Sampled every few seconds into a ring that lives only in memory — never written
to disk, since this is usually an SD card — so the Hardware tab can show the last
hour with its minimum and maximum. A restart starts the history over; the
"since boot" bits do not, the firmware holds those.
"""

import collections
import glob
import os
import shutil
import subprocess
import threading
import time

SAMPLE_EVERY = 5  # seconds
HISTORY = 720  # samples: an hour at SAMPLE_EVERY

_THERMAL = "/sys/class/thermal/thermal_zone0/temp"
_FREQ = "/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq"
# Newer Raspberry Pi kernels expose the throttled word here, which spares a
# `vcgencmd` process per sample; older ones only answer through vcgencmd.
_THROTTLED_SYSFS = glob.glob("/sys/devices/platform/soc/soc:firmware/get_throttled")

# get_throttled bits. The same four, 16 bits up, mean "has happened since boot".
FLAGS = {
    "undervoltage": 0,
    "freq_capped": 1,
    "throttled": 2,
    "soft_temp_limit": 3,
}

_samples = collections.deque(maxlen=HISTORY)
_lock = threading.Lock()
_started = False


def _read_int(path):
    try:
        with open(path, encoding="utf-8") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _throttled_word():
    if _THROTTLED_SYSFS:
        # sysfs prints it in hex without the 0x.
        try:
            with open(_THROTTLED_SYSFS[0], encoding="utf-8") as f:
                return int(f.read().strip(), 16)
        except (OSError, ValueError):
            pass
    try:
        out = subprocess.run(
            ["vcgencmd", "get_throttled"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        ).stdout
        # It prints `throttled=0x50005`.
        return int(out.strip().split("=", 1)[1], 16)
    except (OSError, IndexError, ValueError, subprocess.SubprocessError):
        return None


def available():
    """Whether this machine has a temperature to read — a Pi, not a dev laptop."""
    return os.path.exists(_THERMAL)


def sample():
    """One reading: `{t, temp, freq_mhz, throttled}`, absent values as None."""
    milli = _read_int(_THERMAL)
    khz = _read_int(_FREQ)
    return {
        "t": time.time(),
        "temp": round(milli / 1000, 1) if milli is not None else None,
        "freq_mhz": khz // 1000 if khz is not None else None,
        "throttled": _throttled_word(),
    }


def _loop():
    while True:
        s = sample()
        with _lock:
            _samples.append(s)
        time.sleep(SAMPLE_EVERY)


def start():
    """Start sampling, once, and only where there is something to sample."""
    global _started
    if _started or not available():
        return
    _started = True
    threading.Thread(target=_loop, daemon=True, name="hardware-sampler").start()


def decode(word):
    """The throttled word as `{flag: {"now": bool, "since_boot": bool}}`."""
    if word is None:
        return None
    return {
        name: {
            "now": bool(word >> bit & 1),
            "since_boot": bool(word >> (bit + 16) & 1),
        }
        for name, bit in FLAGS.items()
    }


def _meminfo():
    info = {}
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                key, _, rest = line.partition(":")
                info[key] = int(rest.split()[0]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    total, avail = info.get("MemTotal"), info.get("MemAvailable")
    if total is None or avail is None:
        return None
    return {"total": total, "used": total - avail}


def _uptime():
    try:
        with open("/proc/uptime", encoding="utf-8") as f:
            return float(f.read().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def _model():
    try:
        with open("/proc/device-tree/model", encoding="utf-8") as f:
            return f.read().strip("\x00\n ")
    except OSError:
        return ""


def status(data_dir):
    """What the Hardware tab shows: the latest reading, its history and the box."""
    with _lock:
        history = list(_samples)
    latest = history[-1] if history else (sample() if available() else None)
    temps = [s["temp"] for s in history if s["temp"] is not None]
    try:
        du = shutil.disk_usage(data_dir)
        disk = {"total": du.total, "used": du.used}
    except OSError:
        disk = None
    try:
        load = os.getloadavg()[0]
    except OSError:
        load = None
    return {
        "available": available(),
        "model": _model(),
        "latest": latest,
        "flags": decode(latest["throttled"]) if latest else None,
        "temp_min": min(temps) if temps else None,
        "temp_max": max(temps) if temps else None,
        # Each sample as [seconds ago, °C, under-voltage now], oldest first —
        # what the chart draws, without shipping the full dicts every poll.
        "history": [
            [
                round(time.time() - s["t"]),
                s["temp"],
                bool(s["throttled"] is not None and s["throttled"] & 1),
            ]
            for s in history
        ],
        "sample_every": SAMPLE_EVERY,
        "uptime": _uptime(),
        "load": load,
        "memory": _meminfo(),
        "disk": disk,
    }
