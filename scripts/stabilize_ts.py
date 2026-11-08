#!/usr/bin/env python3
"""Stabilize handheld footage with translation and uniform scale only, baked to a new file.

Resolve's Stabilize() runs in its default mode from the API and can warp the
picture (rotation/perspective). This removes only jitter: per frame pair a RANSAC
similarity fit on tracked corners gives dx, dy and scale; rotation is dropped.
The camera path is smoothed per shot (never across a cut) and the correction is
clamped to what a fixed safety zoom can hide, so intended pans stay and no edge
ever shows. Output keeps the frame count and rate, so it can replace the source
under existing timeline items (MediaPoolItem.ReplaceClip) without moving a cut.

Source-safe: reads the input, writes only the output path.

Run: python scripts/stabilize_ts.py in.mp4 out.mp4 [--zoom 1.08] [--sigma 10]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

try:
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover - guidance only
    raise SystemExit("needs: pip install opencv-python-headless numpy")


def read_frames(path: str):
    s = json.loads(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                   "stream=width,height,avg_frame_rate:stream_side_data=rotation", "-of", "json", path],
                                  capture_output=True, text=True).stdout)["streams"][0]
    w, h = s["width"], s["height"]
    rot = abs(int(((s.get("side_data_list") or [{}])[0]).get("rotation", 0) or 0))
    if rot in (90, 270):
        w, h = h, w
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, h, w, 3), w, h, s["avg_frame_rate"]


def motion(frames):
    """Per-frame (dx, dy, log scale) to the previous frame, and the frames where a new shot starts."""
    g = [cv2.cvtColor(cv2.resize(f, None, fx=0.5, fy=0.5), cv2.COLOR_BGR2GRAY) for f in frames]
    d, cuts = [np.zeros(3)], [0]
    for i, (a, b) in enumerate(zip(g, g[1:]), 1):
        if np.abs(a.astype(np.int16) - b).mean() > 40:
            cuts.append(i); d.append(np.zeros(3)); continue
        m = None
        pa = cv2.goodFeaturesToTrack(a, 400, 0.01, 10)
        if pa is not None and len(pa) > 10:
            pb, st, _ = cv2.calcOpticalFlowPyrLK(a, b, pa, None)
            ok = st.ravel() == 1
            if ok.sum() > 10:
                m, inl = cv2.estimateAffinePartial2D(pa[ok], pb[ok], method=cv2.RANSAC, ransacReprojThreshold=2)
                if m is not None and inl.sum() < 0.3 * ok.sum():
                    m = None
        if m is None:
            cuts.append(i); d.append(np.zeros(3)); continue
        d.append(np.array([m[0, 2] * 2, m[1, 2] * 2, np.log(np.hypot(m[0, 0], m[1, 0]))]))
    return np.array(d), sorted(set(cuts))


def smooth(x, sigma: float):
    k = int(sigma * 3) * 2 + 1
    t = np.arange(k) - k // 2
    w = np.exp(-t ** 2 / (2 * sigma ** 2)); w /= w.sum()
    return np.array([np.convolve(np.pad(x[:, i], k // 2, mode="reflect"), w, "valid") for i in range(x.shape[1])]).T


def stabilize(src: str, dst: str, sigma: float = 10, zoom: float = 1.08, crf: int = 14) -> dict:
    frames, w, h, rate = read_frames(src)
    d, cuts = motion(frames)
    corr = np.zeros_like(d)
    for a, b in zip(cuts, cuts[1:] + [len(d)]):
        sg = min(sigma, (b - a) / 7)
        if sg >= 1:
            traj = np.cumsum(d[a:b], axis=0)
            corr[a:b] = smooth(traj, sg) - traj
    lim = np.array([(zoom - 1) * w / 2 * 0.95, (zoom - 1) * h / 2 * 0.95, np.log(1.03)])
    corr = np.clip(corr, -lim, lim)
    enc = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}", "-r", rate,
                            "-i", "-", "-c:v", "libx264", "-preset", "slow", "-crf", str(crf), "-pix_fmt", "yuv420p", dst],
                           stdin=subprocess.PIPE)
    cx, cy = w / 2, h / 2
    for f, (dx, dy, ls) in zip(frames, corr):
        s = zoom * np.exp(ls)
        m = np.array([[s, 0, (1 - s) * cx + dx * zoom], [0, s, (1 - s) * cy + dy * zoom]], np.float32)
        enc.stdin.write(cv2.warpAffine(f, m, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT).tobytes())
    enc.stdin.close(); enc.wait()
    return {"frames": len(frames), "shots": len(cuts), "zoom": zoom,
            "mean_correction_px": [round(float(np.abs(corr[:, i]).mean()), 1) for i in (0, 1)]}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("src"); p.add_argument("dst")
    p.add_argument("--zoom", type=float, default=1.08, help="fixed safety zoom; also caps how much shake is removed")
    p.add_argument("--sigma", type=float, default=10, help="path smoothing in frames; higher = calmer, more zoom needed")
    p.add_argument("--crf", type=int, default=14)
    a = p.parse_args(argv)
    print(json.dumps(stabilize(a.src, a.dst, a.sigma, a.zoom, a.crf)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
