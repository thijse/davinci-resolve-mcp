#!/usr/bin/env python3
"""Bake a ducked music bed from speech intervals, for a timeline track Resolve's API cannot automate.

The scripting API ignores Volume writes on audio items and has no sidechain, so
the ducking is baked into the music file. Speech intervals come from a JSON list
of [start, end] seconds on the timeline (e.g. the record ranges of the voice clips).

- Pauses shorter than --hold stay ducked; a short gap between sentences that let
  the music swell for a split second is the usual complaint.
- Look-ahead attack (--attack) so the bed is already down at the first word, and
  a slower release; the gain curve is cosine-eased so there are no corners.
- Levels by loudness: the bed sits at --open LUFS in the clear and --under LUFS under speech.

Run: python scripts/duck_music.py music.mp3 speech.json out.wav --length 120
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

import numpy as np

SR = 48000


def loudness(path: str) -> float:
    err = subprocess.run(["ffmpeg", "-hide_banner", "-i", path, "-af", "ebur128", "-f", "null", "-"], capture_output=True, text=True).stderr
    return float(re.findall(r"I:\s+(-?[\d.]+) LUFS", err)[-1])


def duck(music: str, speech, length: float, hold=1.0, attack=0.3, release=0.5, open_lufs=-19.0, under_lufs=-33.0,
         fade_in=0.4, fade_out=2.5) -> np.ndarray:
    n = int(length * SR)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", music, "-t", str(length), "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True).stdout
    m = np.frombuffer(raw, np.float32).copy()
    m = np.pad(m, (0, max(0, n - len(m))))[:n]
    act = np.zeros(n // (SR // 100))  # 10 ms bins
    for a, b in sorted(speech):
        act[int(a * 100):int(b * 100)] = 1
    edges = np.flatnonzero(np.diff(np.r_[0, act, 0]))
    for end, start in zip(edges[1::2][:-1], edges[2::2]):
        if (start - end) / 100 < hold:
            act[end:start] = 1
    fwd, back = act.copy(), act.copy()
    for i in range(1, len(act)):
        fwd[i] = max(act[i], fwd[i - 1] - 1 / (release * 100))
    for i in range(len(act) - 2, -1, -1):
        back[i] = max(act[i], back[i + 1] - 1 / (attack * 100))
    env = 0.5 - 0.5 * np.cos(np.pi * np.maximum(fwd, back))
    lm = loudness(music)
    up, down = 10 ** ((open_lufs - lm) / 20), 10 ** ((under_lufs - lm) / 20)
    g = np.repeat(up + (down - up) * env, SR // 100)
    g = np.pad(g, (0, n - len(g)), mode="edge")
    out = m * g
    fi, fo = int(fade_in * SR), int(fade_out * SR)
    out[:fi] *= np.linspace(0, 1, fi)
    out[-fo:] *= np.linspace(1, 0, fo) ** 1.5
    return out.astype(np.float32)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("music"); p.add_argument("speech", help="JSON [[start, end], ...] seconds"); p.add_argument("out")
    p.add_argument("--length", type=float, required=True, help="timeline length in seconds")
    p.add_argument("--hold", type=float, default=1.0, help="stay ducked through pauses shorter than this")
    p.add_argument("--attack", type=float, default=0.3)
    p.add_argument("--release", type=float, default=0.5)
    p.add_argument("--open", type=float, default=-19.0, help="bed loudness in the clear (LUFS)")
    p.add_argument("--under", type=float, default=-33.0, help="bed loudness under speech (LUFS)")
    a = p.parse_args(argv)
    speech = json.load(open(a.speech, encoding="utf-8"))
    out = duck(a.music, speech, a.length, a.hold, a.attack, a.release, a.open, a.under)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", "-", "-ac", "2", "-c:a", "pcm_s24le", a.out],
                   input=out.tobytes(), check=True)
    print("wrote %s (%.1f s)" % (a.out, a.length))
    return 0


if __name__ == "__main__":
    sys.exit(main())
