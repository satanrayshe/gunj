#!/usr/bin/env python
"""One-off repair of data/harvest_manifest.csv after the 7 Nov 2026 network outage
(PROTOCOL section 19, 12:15 IST entry).

From about 10:18 to 11:38 IST the laptop had no working internet connection. Every
download in that window failed and was charged one of its MAX_ATTEMPTS tries, before
the harvester learned not to charge network-down failures (commit ad55ed6). This script
gives those tries back.

Rule: for each row whose status is "failed" and retryable, attempts is set to the number
of failure lines for that video in logs/harvest_*.log that are (a) outside the outage window
below and (b) not explained by harvest.NETWORK_DOWN_RE. Done, unavailable, blocked and
skipped rows are not touched.

The window starts at 10:15 IST, before the first getaddrinfo failure (10:18:46). That way
zQSjO3YREkM's "short audio: 540 s" at 10:18:27 counts as caused by the outage: the download
lost its connection mid-stream. The window ends at 11:40 IST, before the 11:42 restart.
Its 11:43 "ffmpeg exited with code 3753488571" failure stays charged.

Run it only while no harvester is running (the harvester rewrites the whole manifest).
It writes data/attempt_repair_20261107.json with every change.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import harvest as hv  # noqa: E402

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
WINDOW = (dt.datetime(2026, 11, 7, 10, 15, tzinfo=IST), dt.datetime(2026, 11, 7, 11, 40, tzinfo=IST))
LINE_RE = re.compile(r"^(\S+) (?:WARNING|ERROR) ([A-Za-z0-9_-]{11}): (failed.*|unexpected error.*)$")


def failure_lines(log_dir: Path) -> dict:
    out = {}
    for p in sorted(log_dir.glob("harvest_*.log")):
        for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
            m = LINE_RE.match(ln)
            if not m:
                continue
            ts = dt.datetime.fromisoformat(m.group(1).replace("+0530", "+05:30"))
            out.setdefault(m.group(2), []).append((ts, m.group(3), p.name))
    return out


def main() -> int:
    lock = hv.RunLock(REPO / "logs" / "harvest.lock")
    if not lock.acquire():
        print("a harvester is running; stop it first", file=sys.stderr)
        return 4
    try:
        mpath = REPO / "data" / "harvest_manifest.csv"
        manifest = hv.load_manifest(mpath)
        fails = failure_lines(REPO / "logs")
        changes = []
        for vid, m in manifest.items():
            if m.get("status") != "failed" or m.get("retryable") != "True":
                continue
            charged = [f for f in fails.get(vid, [])
                       if not (WINDOW[0] <= f[0] <= WINDOW[1]) and not hv.NETWORK_DOWN_RE.search(f[1])]
            new = str(len(charged))
            if new != (m.get("attempts") or "0"):
                changes.append({"video_id": vid, "split": m.get("split"), "attempts_before": m.get("attempts"),
                                "attempts_after": new,
                                "charged_failures": [f"{f[0].isoformat()} {f[1][:160]}" for f in charged]})
                m["attempts"] = new
        hv.write_manifest(mpath, manifest)
        rec = {"window_ist": [w.isoformat() for w in WINDOW], "rule": __doc__.split("Rule: ")[1].split("\n\n")[0],
               "n_changed": len(changes), "changes": changes}
        out = REPO / "data" / "attempt_repair_20261107.json"
        tmp = out.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(rec, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(out)
        print(f"changed {len(changes)} rows; record in {out.relative_to(REPO)}")
        return 0
    finally:
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
