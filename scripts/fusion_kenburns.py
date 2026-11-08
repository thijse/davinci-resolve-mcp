#!/usr/bin/env python3
"""Subpixel zoom/pan ("Ken Burns") on timeline items via a keyed Fusion Transform, through the in-app bridge.

Why this route (Resolve Free 21.0.4.5 over the bridge):
- DynamicZoomEnabled cannot be set (SetProperty returns False) and Edit-page
  keyframes are not in the API on that build.
- fusion_comp add_keyframe / set_expression fail over the bridge: Fusion inputs
  are reached by indexing (tool["Size"]), which the bridge proxy cannot do.
- comp.Execute(Lua) runs inside Resolve, where indexing works. Writes must NOT be
  wrapped in comp:Lock(): locked writes read back correctly but never reach the
  render (api_truth "Composition.Lock").
- The Transform is created from Python first; AddTool inside Execute on a fresh
  comp returned nothing.
Keys span the item's own source range: GetLeftOffset() .. +GetDuration()-1.

Plan JSON: [{"track": 1, "record_frame": 0, "s0": 1.0, "s1": 1.12,
             "x0": 0.5, "y0": 0.5, "x1": 0.5, "y1": 0.53}, ...]
record_frame is relative to the timeline start; s = Transform Size, x/y = Center.

Run: python scripts/fusion_kenburns.py plan.json [--timeline NAME]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from utils import resolve_bridge_client as bc  # noqa: E402

LUA = """
local c = comp
local mi, mo = c:FindTool("MediaIn1"), c:FindTool("MediaOut1")
local t = c:FindTool("KenBurns")
t.Input = mi.Output
mo.Input = t.Output
t.Size = BezierSpline({})
t.Size[%(t0)d] = %(s0)f
t.Size[%(t1)d] = %(s1)f
t.Center = XYPath({})
t.Center[%(t0)d] = {%(x0)f, %(y0)f}
t.Center[%(t1)d] = {%(x1)f, %(y1)f}
t.FilterMethod = 7
"""


def apply(item, move: dict) -> bool:
    if item.GetFusionCompCount() == 0:
        item.AddFusionComp()
    comp = item.GetFusionCompByIndex(1)
    if comp.FindTool("KenBurns") is None:
        comp.AddTool("Transform", -32768, -32768).SetAttrs({"TOOLS_Name": "KenBurns"})
    t0 = int(item.GetLeftOffset())
    comp.Execute(LUA % dict(t0=t0, t1=t0 + int(item.GetDuration()) - 1, **move))
    return comp.FindTool("KenBurns") is not None


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("plan"); p.add_argument("--timeline", help="default: current timeline")
    a = p.parse_args(argv)
    plan = json.load(open(a.plan, encoding="utf-8"))
    r = bc.connect(require_enabled=False)
    proj = r.GetProjectManager().GetCurrentProject()
    tl = proj.GetCurrentTimeline()
    if a.timeline:
        tl = next(proj.GetTimelineByIndex(i) for i in range(1, proj.GetTimelineCount() + 1) if proj.GetTimelineByIndex(i).GetName() == a.timeline)
    start = tl.GetStartFrame()
    for row in plan:
        by_start = {it.GetStart() - start: it for it in tl.GetItemListInTrack("video", row.get("track", 1)) or []}
        item = by_start.get(row["record_frame"])
        if item is None:
            print("no item at V%d frame %d" % (row.get("track", 1), row["record_frame"])); continue
        ok = apply(item, {k: float(row[k]) for k in ("s0", "s1", "x0", "y0", "x1", "y1")})
        print("V%d @%d %s %s" % (row.get("track", 1), row["record_frame"], item.GetName(), "ok" if ok else "FAILED"))
    print("Prove it in a rendered frame: comp readback is not evidence (api_truth AddFusionComp).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
