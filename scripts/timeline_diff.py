#!/usr/bin/env python3
"""What did the user change? Compare a live timeline with the clip_infos it was built from (read-only).

Before an agent applies a second round of changes to a timeline it built, the
user may have trimmed, moved or deleted items, or disabled a track. Rebuilding
from the plan would silently throw that away. This lists every item that is no
longer where the plan put it, plus disabled tracks, so changes can be applied on
top: swap media under items with MediaPoolItem.ReplaceClip (same length) instead
of re-appending.

Plan JSON: the clip_infos list used for append_to_timeline / create_timeline_from_clips
({record_frame, start_frame, end_frame (exclusive), track_index, media_type}).

Run: python scripts/timeline_diff.py "Timeline name" clip-infos.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from utils import resolve_bridge_client as bc  # noqa: E402


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("timeline"); p.add_argument("plan")
    a = p.parse_args(argv)
    built = json.load(open(a.plan, encoding="utf-8"))
    r = bc.connect(require_enabled=False)
    proj = r.GetProjectManager().GetCurrentProject()
    tl = next((proj.GetTimelineByIndex(i) for i in range(1, proj.GetTimelineCount() + 1)
               if proj.GetTimelineByIndex(i).GetName() == a.timeline), None)
    if tl is None:
        raise SystemExit("no timeline named %r" % a.timeline)
    t0 = tl.GetStartFrame()
    expected = {}
    for i in built:
        key = ("video" if i.get("media_type", 1) == 1 else "audio", i.get("track_index", 1))
        expected.setdefault(key, set()).add((i["record_frame"], i["record_frame"] + i["end_frame"] - i["start_frame"], i["start_frame"]))
    changed = 0
    print("%s: %d frames" % (a.timeline, tl.GetEndFrame() - t0))
    for kind in ("video", "audio"):
        for k in range(1, tl.GetTrackCount(kind) + 1):
            live = [(it.GetStart() - t0, it.GetEnd() - t0, int(it.GetLeftOffset()), it.GetName()) for it in tl.GetItemListInTrack(kind, k) or []]
            exp = expected.get((kind, k), set())
            added = [x for x in live if x[:3] not in exp]
            removed = sorted(exp - {x[:3] for x in live})
            enabled = tl.GetIsTrackEnabled(kind, k)
            flag = "" if enabled else "  (track DISABLED)"
            print("%s%d: %d live, %d planned%s" % (kind[0].upper(), k, len(live), len(exp), flag))
            for x in added:
                print("   + now  %5d-%5d src %5d  %s" % x)
            for x in removed:
                print("   - plan %5d-%5d src %5d" % x)
            changed += len(added) + len(removed) + (0 if enabled else 1)
    print("\n%s" % ("no user changes" if not changed else "%d differences: apply changes on top, do not rebuild" % changed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
