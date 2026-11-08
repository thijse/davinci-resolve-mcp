#!/usr/bin/env python3
"""Look at footage and cuts in a few images: shot lists, contact sheets, storyboards.

The host-chat vision loop sees one image per read. This turns a clip, or a whole
cut, into one labelled grid plus a Markdown table, so an agent can check what a
clip contains or whether an assembly works without reading dozens of frames.

  shots <video>        cuts in the footage and one tile per shot (middle frame)
  sheet <video>        evenly spaced frames, or --at times, as one grid
  storyboard <cut>     one row per segment of a planned or assembled cut: in,
                       middle and last frame, lengths and running time
  shake <video>        handheld jitter per window (px of high-frequency camera
                       motion): which stretches need stabilizing
  reframe <video>      where the action is, horizontally, per shot: the crop
                       centre for a 9:16 (or --aspect) window out of wider footage

Cut detection: ffmpeg's scene score with the same adaptive threshold
`media_analysis` uses (mean + 2.5 sd, clamped to 0.15-0.40), or PySceneDetect's
ContentDetector with `--detector pyscenedetect` when `scenedetect` is installed
(better on handheld footage and flashes, slower).

Storyboard input is JSON: an edit plan `[{file, in, out, label}]` in source
seconds, or timeline items as this server reports them (`file_path`,
`source_start_seconds`/`source_end_seconds`, or `source_start`/`source_end`
exclusive with `source_fps`), as a list or under "segments"/"items".

Source-safe: reads source media with ffmpeg, writes only PNGs and JSON under
--out-dir (default: ./temp/video-review). Needs ffmpeg/ffprobe and Pillow.

Run: python scripts/video_review.py shots clip.mp4
     python scripts/video_review.py storyboard cut.json --per 3
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover - guidance only, never auto-installs
    raise SystemExit("Pillow is required: pip install Pillow")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.utils.media_analysis import _adaptive_scene_threshold, _parse_scene_score_pairs  # noqa: E402

LABEL_HEIGHT = 24
GAP = 6
BG = (18, 18, 18)
# The chat refuses images over 8000 px a side, and scales or refuses over ~2000 px once it holds many.
VIEW_LIMIT = 2000
FONT_CANDIDATES = (
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)


def _font(size: int = 15):
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _run(args: Sequence[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(list(args), capture_output=True, check=False)
    except FileNotFoundError:
        raise SystemExit("%s is not on PATH (Windows: winget install Gyan.FFmpeg; macOS: brew install ffmpeg)" % args[0])


def probe(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        raise SystemExit("no such file: %s" % path)
    r = _run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", path])
    data = json.loads(r.stdout or b"{}")
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if not video:
        raise SystemExit("%s has no video stream" % path)
    num, _, den = (video.get("avg_frame_rate") or "0/1").partition("/")
    fps = float(num) / float(den) if float(den or 0) else 0.0
    # Phone footage stores rotation as side data; ffmpeg applies it when decoding.
    rotation = 0
    for side in video.get("side_data_list", []) or []:
        rotation = int(side.get("rotation", rotation) or 0)
    w, h = int(video["width"]), int(video["height"])
    if abs(rotation) in (90, 270):
        w, h = h, w
    return {
        "path": path,
        "name": os.path.basename(path),
        "width": w,
        "height": h,
        "fps": fps,
        "duration": float(data.get("format", {}).get("duration") or video.get("duration") or 0),
        "start": float(video.get("start_time") or 0),
    }


def frame_at(path: str, seconds: float, width: int) -> Image.Image:
    """Decode the frame on screen at `seconds`, scaled to `width` (0: native)."""
    vf = ["scale=%d:-2:flags=area" % width] if width else []
    filters = ["-vf", ",".join(vf)] if vf else []
    r = _run(["ffmpeg", "-v", "error", "-ss", "%.4f" % max(0.0, seconds), "-i", path, "-map", "0:v:0",
              "-frames:v", "1", *filters, "-f", "image2pipe", "-vcodec", "png", "-"])
    if not r.stdout:
        # A seek into the last frame's span can land past it: decode the last half second and keep its final frame.
        r = _run(["ffmpeg", "-v", "error", "-sseof", "-0.5", "-i", path, "-map", "0:v:0", *filters,
                  "-update", "1", "-f", "image2", "-vcodec", "png", "-"])
    if not r.stdout:
        raise SystemExit("could not decode %s at %.2fs: %s" % (path, seconds, r.stderr.decode(errors="replace").strip()[-200:]))
    from io import BytesIO
    return Image.open(BytesIO(r.stdout)).convert("RGB")


def grid(tiles: List[Tuple[Image.Image, str]], cols: int, title: str) -> Image.Image:
    tw = max(img.width for img, _ in tiles)
    th = max(img.height for img, _ in tiles)
    rows = math.ceil(len(tiles) / cols)
    top = LABEL_HEIGHT + 4
    sheet = Image.new("RGB", (cols * (tw + GAP) + GAP, top + rows * (th + LABEL_HEIGHT + GAP) + GAP), BG)
    draw = ImageDraw.Draw(sheet)
    font = _font(15)
    draw.text((GAP, 6), title, fill=(255, 210, 120), font=font)
    for i, (img, label) in enumerate(tiles):
        x = GAP + (i % cols) * (tw + GAP)
        y = top + GAP + (i // cols) * (th + LABEL_HEIGHT + GAP)
        draw.text((x + 2, y + 3), label, fill=(235, 235, 235), font=font)
        sheet.paste(img, (x, y + LABEL_HEIGHT))
    return sheet


def save(sheet: Image.Image, out_dir: str, name: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "%s-%s.png" % (name, time.strftime("%H%M%S")))
    sheet.save(path, optimize=True)
    note = ""
    if max(sheet.size) > VIEW_LIMIT:
        note = "; over %d px: lower --tile or --cols before looking at it" % VIEW_LIMIT
    return "%s (%dx%d%s)" % (path, sheet.width, sheet.height, note)


def fmt(seconds: float) -> str:
    return "%.2fs" % seconds


# ---------- Cut detection ----------


def cuts_ffmpeg(path: str, threshold: Optional[float]) -> Tuple[List[float], Dict[str, Any]]:
    info = probe(path)
    r = _run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-map", "0:v:0", "-vf",
              "scale=320:-2,select='gt(scene,0)',metadata=print:key=lavfi.scene_score,showinfo", "-f", "null", "-"])
    pairs = _parse_scene_score_pairs(r.stderr.decode(errors="replace"))
    if threshold is None:
        threshold, stats = _adaptive_scene_threshold(pairs)
    else:
        stats = {"source": "fixed", "chosen": threshold}
    return [t - info["start"] for t, s in pairs if s > threshold], {"detector": "ffmpeg scene score", "threshold": threshold, **stats}


def cuts_pyscenedetect(path: str, threshold: Optional[float]) -> Tuple[List[float], Dict[str, Any]]:
    try:
        from scenedetect import ContentDetector, detect
    except ImportError:
        raise SystemExit("--detector pyscenedetect needs: pip install scenedetect opencv-python-headless")
    t = 27.0 if threshold is None else threshold
    scenes = detect(path, ContentDetector(threshold=t))
    seconds = lambda tc: tc.seconds if hasattr(type(tc), "seconds") else tc.get_seconds()
    return [seconds(start) for start, _ in scenes[1:]], {"detector": "PySceneDetect ContentDetector", "threshold": t}


def find_shots(path: str, detector: str, threshold: Optional[float], min_shot: float) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    info = probe(path)
    cuts, meta = (cuts_pyscenedetect if detector == "pyscenedetect" else cuts_ffmpeg)(path, threshold)
    starts = [0.0]
    for t in sorted(cuts):
        if t - starts[-1] >= min_shot and info["duration"] - t >= min_shot:
            starts.append(t)
    shots = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else info["duration"]
        shots.append({"shot": i + 1, "start": round(start, 4), "end": round(end, 4), "middle": round((start + end) / 2, 4)})
    return shots, meta


# ---------- Commands ----------


def cmd_shots(args: argparse.Namespace) -> int:
    info = probe(args.video)
    shots, meta = find_shots(args.video, args.detector, args.threshold, args.min_shot)
    if args.json:
        print(json.dumps({"file": os.path.abspath(args.video), **meta, "shots": shots}, indent=2))
        return 0
    tiles = [(frame_at(args.video, s["middle"], args.tile), "S%d %s-%s" % (s["shot"], fmt(s["start"]), fmt(s["end"]))) for s in shots[: args.max_tiles]]
    title = "%s  %d shots  %s > %.2f  middle frame of each" % (info["name"], len(shots), meta["detector"], meta["threshold"])
    print("Wrote %s\n" % save(grid(tiles, min(args.cols, len(tiles)), title), args.out_dir, os.path.splitext(info["name"])[0] + "-shots"))
    print("| Shot | Start | End | Length |\n| --- | --- | --- | --- |")
    for s in shots:
        print("| %d | %s | %s | %s |" % (s["shot"], fmt(s["start"]), fmt(s["end"]), fmt(s["end"] - s["start"])))
    longest = max(s["end"] - s["start"] for s in shots)
    print("\nCoverage: every frame scored for cuts; one frame per shot%s, so the longest shot (%s) is summarised by its middle. "
          "Dissolves and slow fades can stay under the threshold; try --detector pyscenedetect or a lower --threshold."
          % ("" if len(shots) <= args.max_tiles else " (first %d shown)" % args.max_tiles, fmt(longest)))
    return 0


def cmd_sheet(args: argparse.Namespace) -> int:
    info = probe(args.video)
    if args.at:
        times = [float(t) for t in args.at.split(",")]
    else:
        n = max(1, args.count)
        # Stay a frame inside the end: seeking to the exact duration decodes nothing.
        last = max(0.0, info["duration"] - (1.0 / info["fps"] if info["fps"] else 0.05))
        times = [last * i / (n - 1) if n > 1 else 0.0 for i in range(n)]
    tiles = [(frame_at(args.video, t, args.tile), fmt(t)) for t in times]
    title = "%s  %dx%d  %s  %d frames" % (info["name"], info["width"], info["height"], fmt(info["duration"]), len(tiles))
    print("Wrote %s" % save(grid(tiles, min(args.cols, len(tiles)), title), args.out_dir, os.path.splitext(info["name"])[0] + "-sheet"))
    gap = max(b - a for a, b in zip([0.0] + times, times + [info["duration"]]))
    print("\nCoverage: %d frames, up to %s apart; anything shorter can fall between them (use shots to see every cut)." % (len(tiles), fmt(gap)))
    return 0


def _segments(spec: str) -> List[Dict[str, Any]]:
    with open(spec, encoding="utf-8-sig") as handle:
        raw = json.load(handle)
    items = raw if isinstance(raw, list) else raw.get("segments") or raw.get("items") or []
    if not items:
        raise SystemExit('%s: no segments (a list, or under "segments" or "items")' % spec)
    base = os.path.dirname(os.path.abspath(spec))
    out = []
    for i, s in enumerate(items, start=1):
        path = s.get("file") or s.get("file_path") or s.get("path")
        if not path:
            raise SystemExit("segment %d has no file, file_path or path" % i)
        path = os.path.join(base, path)
        fps = s.get("source_fps") or s.get("fps")
        start = s.get("in", s.get("source_start_seconds"))
        end = s.get("out", s.get("source_end_seconds"))
        if start is None:
            start = s["source_start"] / fps if fps and s.get("source_start") is not None else 0.0
        if end is None:
            end = s["source_end"] / fps if fps and s.get("source_end") is not None else probe(path)["duration"]
        if end <= start:
            raise SystemExit("segment %d: out (%s) must be after in (%s)" % (i, end, start))
        out.append({"k": i, "path": path, "in": float(start), "out": float(end),
                    "label": s.get("label") or s.get("name") or os.path.basename(path)})
    return out


def cmd_storyboard(args: argparse.Namespace) -> int:
    segs = _segments(args.cut)
    per = max(1, min(5, args.per))
    tiles = []
    for s in segs:
        info = probe(s["path"])
        # The last frame of a segment lies one frame before its exclusive out.
        last = s["out"] - (1.0 / info["fps"] if info["fps"] else 0.04)
        s["warn"] = "out %s is past the end (%s)" % (fmt(s["out"]), fmt(info["duration"])) if s["out"] > info["duration"] + 0.1 else ""
        times = [(s["in"] + last) / 2] if per == 1 else [s["in"] + (last - s["in"]) * j / (per - 1) for j in range(per)]
        for j, t in enumerate(times):
            tiles.append((frame_at(s["path"], min(t, info["duration"] - 0.05), args.tile), ("#%d %s %s" % (s["k"], s["label"], fmt(t))) if j == 0 else "#%d %s" % (s["k"], fmt(t))))
    title = "%s  %d segments  %s" % (os.path.basename(args.cut), len(segs), "middle frame" if per == 1 else "%d frames, in to out" % per)
    print("Wrote %s\n" % save(grid(tiles, per, title), args.out_dir, os.path.splitext(os.path.basename(args.cut))[0] + "-storyboard"))
    print("| # | Label | File | In | Out | Length | At in cut | Note |\n| --- | --- | --- | --- | --- | --- | --- | --- |")
    record = 0.0
    for s in segs:
        print("| %d | %s | %s | %s | %s | %s | %s | %s |" % (s["k"], s["label"], os.path.basename(s["path"]), fmt(s["in"]), fmt(s["out"]),
                                                       fmt(s["out"] - s["in"]), fmt(record), s["warn"]))
        record += s["out"] - s["in"]
    print("\nTotal: %s. Coverage: %d frame(s) per segment, in to last frame; what happens between them is not shown (sheet --at on the source)." % (fmt(record), per))
    return 0


# ---------- Motion: shake and reframe (need opencv + numpy) ----------


def _cv():
    try:
        import cv2
        import numpy as np
    except ImportError:
        raise SystemExit("shake and reframe need: pip install opencv-python-headless numpy")
    return cv2, np


def _gray_frames(path: str, start: float, end: float, fps: float, width: int):
    """Display-oriented grey frames (ffmpeg applies the rotation flag)."""
    _, np = _cv()
    info = probe(path)
    h = int(round(width * info["height"] / info["width"] / 2) * 2)
    r = _run(["ffmpeg", "-v", "error", "-ss", "%.3f" % start, "-t", "%.3f" % (end - start), "-i", path, "-map", "0:v:0",
              "-vf", "fps=%g,scale=%d:%d" % (fps, width, h), "-f", "rawvideo", "-pix_fmt", "gray", "-"])
    return np.frombuffer(r.stdout, np.uint8).reshape(-1, h, width)


def shake_px(path: str, start: float, end: float, width: int = 480) -> float:
    """Mean residual of the camera path after a 9-frame moving average, in px at `width`.

    Global motion per frame pair is a RANSAC similarity fit on tracked corners; the
    smooth part is intended movement (a pan), the residual is the shake.
    """
    cv2, np = _cv()
    g = _gray_frames(path, start, end, 24, width)
    if len(g) < 10:
        return 0.0
    path_xy = [np.zeros(2)]
    for a, b in zip(g, g[1:]):
        pa = cv2.goodFeaturesToTrack(a, 200, 0.01, 8)
        step = np.zeros(2)
        if pa is not None and len(pa) > 8:
            pb, st, _ = cv2.calcOpticalFlowPyrLK(a, b, pa, None)
            ok = st.ravel() == 1
            if ok.sum() > 8:
                m, _ = cv2.estimateAffinePartial2D(pa[ok], pb[ok])
                if m is not None:
                    step = m[:, 2]
        path_xy.append(path_xy[-1] + step)
    p = np.array(path_xy)
    k = 9
    smooth = np.array([np.convolve(np.pad(p[:, i], k // 2, mode="edge"), np.ones(k) / k, "valid") for i in range(2)]).T
    return float(np.sqrt(((p - smooth) ** 2).sum(1)).mean())


def cmd_shake(args: argparse.Namespace) -> int:
    info = probe(args.video)
    end = info["duration"] if args.end is None else min(args.end, info["duration"])
    rows, t = [], args.start
    while t < end - 0.3:
        w_end = min(end, t + args.window)
        rows.append((t, w_end, shake_px(args.video, t, w_end)))
        t = w_end
    if args.json:
        print(json.dumps([{"start": round(a, 3), "end": round(b, 3), "shake_px": round(s, 2)} for a, b, s in rows], indent=2))
        return 0
    print("| Start | End | Shake (px @480) | |\n| --- | --- | --- | --- |")
    for a, b, s in rows:
        print("| %s | %s | %.2f | %s |" % (fmt(a), fmt(b), s, "stabilize" if s >= args.limit else ""))
    print("\nShake is the mean distance (px at 480 wide) between the camera path and its 9-frame moving average: "
          "tripod ~0.1, steady hand ~0.5-1, walking 2-5, rough 5+. Rows at or over --limit %.1f are marked." % args.limit)
    return 0


def action_centre(path: str, start: float, end: float) -> float:
    """Horizontal centre (0-1) of frame-to-frame change: where people and things move."""
    cv2, np = _cv()
    g = _gray_frames(path, start, end, 10, 480).astype(np.float32)
    if len(g) < 2:
        return 0.5
    d = cv2.GaussianBlur(np.abs(np.diff(g, axis=0)).mean(0), (0, 0), 6)
    col = d.sum(0)
    col = np.clip(col - np.median(col), 0, None)
    return float((col * np.arange(len(col))).sum() / col.sum() / len(col)) if col.sum() > 0 else 0.5


def cmd_reframe(args: argparse.Namespace) -> int:
    info = probe(args.video)
    num, _, den = args.aspect.partition(":")
    window = (info["height"] * float(num) / float(den)) / info["width"]
    if window >= 1:
        raise SystemExit("%s is not wider than %s: nothing to reframe" % (info["name"], args.aspect))
    shots, _ = find_shots(args.video, args.detector, None, args.min_shot)
    half = window / 2
    out = []
    for s in shots:
        c = action_centre(args.video, s["start"], s["end"])
        out.append({**s, "centre": round(min(max(c, half), 1 - half), 3), "raw_centre": round(c, 3)})
    if args.json:
        print(json.dumps({"file": os.path.abspath(args.video), "aspect": args.aspect, "window_fraction": round(window, 4), "shots": out}, indent=2))
        return 0
    tiles = []
    for s in out[: args.max_tiles]:
        img = frame_at(args.video, s["middle"], args.tile)
        draw = ImageDraw.Draw(img)
        x0, x1 = (s["centre"] - half) * img.width, (s["centre"] + half) * img.width
        draw.rectangle((x0, 0, x1, img.height - 1), outline=(255, 200, 0), width=3)
        tiles.append((img, "S%d %s centre %.2f" % (s["shot"], fmt(s["start"]), s["centre"])))
    title = "%s  %s window = %.0f%% of width, yellow box per shot" % (info["name"], args.aspect, window * 100)
    print("Wrote %s\n" % save(grid(tiles, min(args.cols, len(tiles)), title), args.out_dir, os.path.splitext(info["name"])[0] + "-reframe"))
    print("| Shot | Start | End | Centre | Raw |\n| --- | --- | --- | --- | --- |")
    for s in out:
        print("| %d | %s | %s | %.3f | %.3f |" % (s["shot"], fmt(s["start"]), fmt(s["end"]), s["centre"], s["raw_centre"]))
    print("\nCentre is where frame-to-frame change concentrates, clamped so the window stays inside the frame. "
          "It finds movement, not faces: check the boxes, and for a two-shot interview pick each speaker by eye.")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out-dir", default=os.path.join(os.getcwd(), "temp", "video-review"))
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("shots", help="cuts and one tile per shot")
    p.add_argument("video")
    p.add_argument("--detector", choices=["ffmpeg", "pyscenedetect"], default="ffmpeg")
    p.add_argument("--threshold", type=float, help="ffmpeg: 0-1 (default adaptive); pyscenedetect: default 27")
    p.add_argument("--min-shot", type=float, default=0.4, help="merge shots shorter than this (s)")
    p.add_argument("--tile", type=int, default=320)
    p.add_argument("--cols", type=int, default=4)
    p.add_argument("--max-tiles", type=int, default=48)
    p.add_argument("--json", action="store_true", help="print the shot list only")
    p.set_defaults(func=cmd_shots)

    p = sub.add_parser("sheet", help="frames over time as one grid")
    p.add_argument("video")
    p.add_argument("--count", type=int, default=12)
    p.add_argument("--at", help="comma-separated seconds instead of --count")
    p.add_argument("--tile", type=int, default=320)
    p.add_argument("--cols", type=int, default=4)
    p.set_defaults(func=cmd_sheet)

    p = sub.add_parser("storyboard", help="a cut as one row per segment")
    p.add_argument("cut", help="JSON edit plan or timeline items")
    p.add_argument("--per", type=int, default=3, help="frames per segment (1-5)")
    p.add_argument("--tile", type=int, default=240)
    p.set_defaults(func=cmd_storyboard)

    p = sub.add_parser("shake", help="handheld jitter per window")
    p.add_argument("video")
    p.add_argument("--start", type=float, default=0.0)
    p.add_argument("--end", type=float)
    p.add_argument("--window", type=float, default=2.0, help="seconds per row")
    p.add_argument("--limit", type=float, default=2.0, help="mark rows at or above this")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_shake)

    p = sub.add_parser("reframe", help="crop centre per shot for a narrower aspect")
    p.add_argument("video")
    p.add_argument("--aspect", default="9:16", help="target aspect, w:h")
    p.add_argument("--detector", choices=["ffmpeg", "pyscenedetect"], default="ffmpeg")
    p.add_argument("--min-shot", type=float, default=0.4)
    p.add_argument("--tile", type=int, default=320)
    p.add_argument("--cols", type=int, default=4)
    p.add_argument("--max-tiles", type=int, default=24)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_reframe)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
