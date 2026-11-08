"""scripts/video_review.py on synthetic clips: cut detection and storyboard input.

Needs ffmpeg/ffprobe and Pillow; skips without them. No Resolve.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

try:
    import video_review
except SystemExit:  # pragma: no cover - Pillow absent; the script exits on import
    video_review = None

HAVE_FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")


def _make_three_shot_clip(path: Path) -> None:
    # testsrc2 3 s | mandelbrot 2.5 s | smptebars 2 s at 25 fps: cuts at 3.00 s and 5.52 s (frame 138).
    subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25:duration=3",
        "-f", "lavfi", "-i", "mandelbrot=size=320x180:rate=25",
        "-f", "lavfi", "-i", "smptebars=size=320x180:rate=25:duration=2",
        "-filter_complex", "[1]trim=duration=2.5,setpts=PTS-STARTPTS[m];[0][m][2]concat=n=3:v=1:a=0",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
    ], check=True)


@unittest.skipIf(video_review is None or not HAVE_FFMPEG, "Pillow or ffmpeg not installed")
class VideoReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        cls.clip = cls.dir / "cuts.mp4"
        _make_three_shot_clip(cls.clip)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _run(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = video_review.main(["--out-dir", str(self.dir / "out"), *argv])
        return code, out.getvalue()

    def test_shots_finds_both_cuts_with_adaptive_threshold(self):
        shots, meta = video_review.find_shots(str(self.clip), "ffmpeg", None, 0.4)
        self.assertEqual(len(shots), 3)
        self.assertAlmostEqual(shots[1]["start"], 3.0, delta=0.05)
        self.assertAlmostEqual(shots[2]["start"], 5.52, delta=0.05)
        self.assertEqual(meta["detector"], "ffmpeg scene score")

    def test_min_shot_merges_short_shots(self):
        shots, _ = video_review.find_shots(str(self.clip), "ffmpeg", None, 2.8)
        self.assertEqual([s["start"] for s in shots][:2], [0.0, shots[1]["start"]])
        self.assertEqual(len(shots), 2)

    def test_storyboard_accepts_timeline_items_in_source_frames(self):
        # source_end is exclusive and in the media's frame rate, as the server reports timeline items.
        cut = self.dir / "cut.json"
        cut.write_text(json.dumps({"items": [
            {"file_path": "cuts.mp4", "source_start": 10, "source_end": 60, "source_fps": 25, "name": "a"},
            {"file": "cuts.mp4", "in": 3.2, "out": 5.2},
        ]}), encoding="utf-8")
        segs = video_review._segments(str(cut))
        self.assertAlmostEqual(segs[0]["in"], 0.4)
        self.assertAlmostEqual(segs[0]["out"], 2.4)
        code, text = self._run("storyboard", str(cut), "--per", "2", "--tile", "120")
        self.assertEqual(code, 0)
        self.assertIn("Total: 4.00s", text)
        self.assertTrue(list((self.dir / "out").glob("cut-storyboard-*.png")))

    def test_sheet_reads_the_last_frame(self):
        code, text = self._run("sheet", str(self.clip), "--count", "3", "--tile", "120")
        self.assertEqual(code, 0)
        self.assertIn("3 frames", text)


if __name__ == "__main__":
    unittest.main()
