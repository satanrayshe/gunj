"""scripts/set_threshold.py, scripts/evaluate.py and scripts/eval_common.py.

Every signal here is SYNTHETIC: a generated random-walk reference, traces cut
from it with generated noise, and unrelated random walks as negatives. No
real audio, trace or grid data is read, and no git commit is made (the git
check is replaced by a stand-in where a test needs it to pass).
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import calibrate_latency as cl  # noqa: E402
import eval_common as ec        # noqa: E402
import evaluate as ev           # noqa: E402
import set_threshold as st      # noqa: E402
from conftest import SYNTH_T0, write_synth_ref_dir  # noqa: E402
from gunj import reference as gref                 # noqa: E402
from gunj.synthetic import synthetic_enf_walk      # noqa: E402

T0 = pd.Timestamp(SYNTH_T0)
N_REF = 200_000
L_S, D_S = 3, 40
NF = {10: 585, 20: 1185}            # frames in a 10- and 20-min excerpt trace
SPLIT_COLS = ["video_id", "split", "half", "channel", "venue_type", "country", "release_timestamp",
              "release_time_ist", "label_drift_s", "label_stable", "official_status", "excerpt_lengths_min",
              "synthetic_seed", "title"]
MAN_COLS = ["video_id", "split", "status", "reason", "retryable", "trace_path", "finished_utc",
            "hum_present_v1", "hum_max_median_ratio_v1", "source_format"]


IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
NET_REASON = ("ERROR: [youtube] {vid}: Unable to download API page: [Errno 11001] getaddrinfo failed "
              "(caused by TransportError('[Errno 11001] getaddrinfo failed'))")


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    """evaluate.py's clock: 21:00 IST on 7 Nov, before the default decision time, unless a test moves it."""
    clock = {"now": dt.datetime(2026, 11, 7, 21, 0, tzinfo=IST)}
    monkeypatch.setattr(ev, "_now", lambda: clock["now"])
    return clock


def unix_of(idx: int) -> int:
    return int(gref.ist_to_unix(T0 + pd.Timedelta(seconds=int(idx))))


def write_trace(path: Path, vid: str, split: str, parts: dict) -> None:
    """parts: (ext, length) -> (freq, hum_present). v2 gets frame weights."""
    arrays, traces = {}, {}
    for (ext, m), (freq, hum) in parts.items():
        arrays[f"{ext}__x{m}__freq"] = np.asarray(freq, dtype=np.float64)
        if ext == "v2":
            arrays[f"{ext}__x{m}__fw"] = np.ones(len(freq), dtype=np.float32)
        traces[f"{ext}__x{m}"] = {"hum": {"present": bool(hum),
                                          "median_peak_to_floor": {"50 Hz": 4.0 if hum else 1.5}}}
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {"video_id": vid, "split": split, "traces": traces, "SYNTHETIC": True}
    np.savez_compressed(path, meta_json=np.array(json.dumps(meta)), **arrays)


def matching_parts(freq_ref, release_idx: int, seed: int, sigma: float = 0.0005, hum=True) -> dict:
    """Traces whose true start is release + L + D on the SYNTHETIC reference."""
    rng = np.random.default_rng(seed)
    p = release_idx + L_S + D_S + cl.FRAME0_S
    out = {}
    for ext, lens in (("v2", (10, 20)), ("v1", (10, 20)), ("naive", (10,))):
        for m in lens:
            seg = freq_ref[p:p + NF[m]].astype(np.float64)
            out[(ext, m)] = (seg + rng.normal(0, sigma, seg.size), hum)
    return out


def unrelated_parts(seed: int, hum=False) -> dict:
    out = {}
    for ext, lens in (("v2", (10, 20)), ("v1", (10, 20)), ("naive", (10,))):
        for m in lens:
            w = synthetic_enf_walk(NF[m], seed=seed + m + len(ext))
            out[(ext, m)] = (np.asarray(w, dtype=np.float64), hum)
    return out


def write_csv(path: Path, cols: list, rows: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in cols})


def srow(vid, split, idx, half="", lengths="10;20", stable="True"):
    return {"video_id": vid, "split": split, "half": half, "channel": "SYNTHETIC", "venue_type": "lecture",
            "country": "IN", "release_timestamp": unix_of(idx),
            "release_time_ist": str(T0 + pd.Timedelta(seconds=idx)),
            "label_drift_s": "" if split.startswith("negative") else f"{D_S:.1f}", "label_stable": stable,
            "official_status": "verified by metadata step", "excerpt_lengths_min": lengths,
            "synthetic_seed": "7" if split == "negative_synthetic" else "", "title": "SYNTHETIC"}


def mrow(vid, split, status="done", finished="2026-11-07T10:00:00+00:00", hum="True", synthetic=False, reason="",
         retryable=""):
    return {"video_id": vid, "split": split, "status": status, "reason": reason, "retryable": retryable,
            "trace_path": f"traces/{split}/{vid}.npz" if status == "done" else "",
            "finished_utc": finished, "hum_present_v1": hum if status == "done" else "",
            "hum_max_median_ratio_v1": "4.0" if hum == "True" else "1.5",
            "source_format": "SYNTHETIC" if synthetic else "233"}


@pytest.fixture()
def world(tmp_path, synth_ref_small):
    """A temporary repo layout with a SYNTHETIC reference, negatives, Calibration and Test traces."""
    f, v = synth_ref_small
    repo = tmp_path / "repo"
    ref_dir = write_synth_ref_dir(tmp_path / "ref", f, v)
    (repo / "gunj").mkdir(parents=True)
    (repo / "gunj" / "config.json").write_text(json.dumps(
        {"_note": "PLACEHOLDER", "confidence_threshold": 0.1, "confidence_threshold_calibrated": False,
         "confidence_definition": "atanh(r1) - atanh(r2)", "top_k": 5}), encoding="utf-8")
    splits, man = [], []
    # threshold-half negatives: 3 real + 2 SYNTHETIC; report half: 1 real + 1 SYNTHETIC
    negs = [("NEG-T1", "negative", "threshold"), ("NEG-T2", "negative", "threshold"),
            ("NEG-T3", "negative", "threshold"), ("SYN-NOISE-01", "negative_synthetic", "threshold"),
            ("SYN-WALK-01", "negative_synthetic", "threshold"), ("NEG-R1", "negative", "report"),
            ("SYN-WALK-02", "negative_synthetic", "report")]
    for i, (vid, split, half) in enumerate(negs):
        idx = 30_000 + 20_000 * i
        splits.append(srow(vid, split, idx, half=half))
        man.append(mrow(vid, split, hum="False", synthetic=split == "negative_synthetic"))
        write_trace(repo / "traces" / split / f"{vid}.npz", vid, split, unrelated_parts(100 + 10 * i))
    # Calibration and Test: 5 matching streams, 1 unrelated, 1 failed download, 1 not attempted
    for split, off in (("calibration", 0), ("test", 7_000)):
        for i in range(6):
            vid = f"{split[:3].upper()}-{i}"
            idx = 20_000 + 25_000 * i + off
            splits.append(srow(vid, split, idx))
            parts = matching_parts(f, idx, seed=i + off) if i < 5 else unrelated_parts(900 + i + off)
            man.append(mrow(vid, split, hum="True" if i < 5 else "False"))
            write_trace(repo / "traces" / split / f"{vid}.npz", vid, split, parts)
        splits.append(srow(f"{split[:3].upper()}-FAIL", split, 180_000 + off // 10))
        man.append(mrow(f"{split[:3].upper()}-FAIL", split, status="failed", reason="This video is not available",
                        finished="2026-11-07T09:00:00+00:00"))
        splits.append(srow(f"{split[:3].upper()}-LATER", split, 190_000))
    write_csv(repo / "data" / "splits.csv", SPLIT_COLS, splits)
    write_csv(repo / "data" / "harvest_manifest.csv", MAN_COLS, man)
    (repo / "logs").mkdir()
    (repo / "logs" / "harvest_test.log").write_text(
        "2026-11-07T15:00:00+0530 INFO X [test]: SYNTHETIC title with 429 in it (new)\n"
        "2026-11-07T15:01:00+0530 INFO X: downloaded format 233\n", encoding="utf-8")
    (repo / "results").mkdir()
    write_json(repo / "results" / "latency.json", {"L_s": L_S, "status": "calibrated"})
    return {"repo": repo, "ref_dir": ref_dir, "ref": f}


def write_json(path, obj):
    path.write_text(json.dumps(obj), encoding="utf-8")


def read_csv(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def run_st(w, *extra):
    return st.main(["--ref", str(w["ref_dir"]), "--workers", "1", *extra], repo=w["repo"])


def run_ev(w, *extra):
    return ev.main(["--ref", str(w["ref_dir"]), "--workers", "1", *extra], repo=w["repo"])


# -------------------------------------------------------------- statistics

def test_tau_rule_m_and_no_verdict():
    vals = [0.2, 0.05, None, 0.31, 0.1]
    out = ec.tau_rule(vals)
    assert out["n"] == 5 and out["m"] == 0 and out["n_no_verdict"] == 1
    assert out["tau"] == pytest.approx(0.31 + 0.0001) and out["n_reaching_tau"] == 0
    big = [i / 1000 for i in range(150)]                       # n = 150 -> m = 1
    out = ec.tau_rule(big)
    assert out["m"] == 1 and out["value_at_rank"] == pytest.approx(0.148) and out["n_reaching_tau"] == 1
    out = ec.tau_rule([None] * 100 + [0.5])                    # n = 101 -> m = 1, only one confidence
    assert out["m"] == 1 and out["tau"] is None
    assert ec.tau_rule([])["tau"] is None


def test_wilson_matches_protocol_numbers():
    # PROTOCOL section 10: 0 false matches gives these Wilson 95% upper bounds
    for n, upper in ((25, 0.1332), (19, 0.1682), (17, 0.1843), (45, 0.0787), (37, 0.0941)):
        lo, hi = ec.wilson(0, n)
        assert lo == 0 and hi == pytest.approx(upper, abs=5e-5)
    assert ec.wilson(0, 0) == (None, None)


def test_scope_positions(synth_ref_dir):
    ref = gref.load(synth_ref_dir)
    assert cl.scope_positions(ref, T0, "full") == (cl.FRAME0_S, len(ref))
    c = T0 + pd.Timedelta(seconds=100_000)
    p0, p1 = cl.scope_positions(ref, c, "1d")
    assert (p0, p1) == (100_000 - 86_400 + cl.FRAME0_S, 100_000 + 86_400 + 1 + cl.FRAME0_S)
    assert cl.scope_positions(ref, c, "90d") == (cl.FRAME0_S, len(ref))   # clipped to the series


# ------------------------------------------------------------ set_threshold

def test_set_threshold_refuses_until_every_row_final(world):
    repo = world["repo"]
    man = read_csv(repo / "data" / "harvest_manifest.csv")
    write_csv(repo / "data" / "harvest_manifest.csv", MAN_COLS, [m for m in man if m["video_id"] != "NEG-T2"])
    assert run_st(world) == 2
    assert not (repo / "results" / "threshold.json").exists()
    assert "NEG-T2" in (repo / "logs" / "set_threshold.log").read_text(encoding="utf-8")
    # cache-only searches what exists without setting tau
    assert run_st(world, "--cache-only") == 0
    assert not (repo / "results" / "threshold.json").exists()
    assert (repo / "results" / "search_cache" / "negative" / "NEG-T1.json").exists()


def test_set_threshold_writes_tau_config_and_reuses_cache(world):
    repo = world["repo"]
    man = read_csv(repo / "data" / "harvest_manifest.csv")
    for m in man:
        if m["video_id"] == "NEG-T3":
            m.update(status="failed", reason="Private video", trace_path="")
    write_csv(repo / "data" / "harvest_manifest.csv", MAN_COLS, man)
    assert run_st(world) == 0
    d = json.loads((repo / "results" / "threshold.json").read_text(encoding="utf-8"))
    cell = d["cells"]["v2"]["10"]["full"]
    assert cell["n"] == 4 and cell["n_real"] == 2 and cell["n_synthetic"] == 2 and cell["m"] == 0
    confs = [t["confidence"] for t in cell["trials"]]
    assert cell["tau"] == pytest.approx(max(confs) + 0.0001)
    assert d["cells"]["naive"]["20"]["full"]["n"] == 0 and d["cells"]["naive"]["20"]["full"]["tau"] is None
    assert [e["video_id"] for e in d["excluded"]] == ["NEG-T3"]
    assert all(not str(n.get("trace_path") or "").startswith(("C:", "/")) for n in d["negatives"])
    cfg = json.loads((repo / "gunj" / "config.json").read_text(encoding="utf-8"))
    taus = [t for by in cfg["confidence_threshold_cells"].values() for t in by.values() if t is not None]
    assert cfg["confidence_threshold_calibrated"] is True and cfg["confidence_threshold"] == max(taus)
    assert cfg["top_k"] == 5 and "PLACEHOLDER" not in cfg["_note"]
    assert cfg["confidence_threshold_cells"]["10"]["full"] == cell["tau"]
    # calibrate_latency reads tau(60 min, +-1 day) from this file format
    assert cl.load_tau(repo / "results" / "threshold.json") is None      # no 60-min trials here
    d60 = json.loads(json.dumps(d))
    d60["cells"]["v2"]["60"]["1d"]["tau"] = 0.25
    write_json(repo / "results" / "t60.json", d60)
    assert cl.load_tau(repo / "results" / "t60.json") == {"v2": 0.25}
    # second run: everything comes from the cache
    assert run_st(world) == 0
    d2 = json.loads((repo / "results" / "threshold.json").read_text(encoding="utf-8"))
    assert d2["timing"]["streams_to_search"] == 0 and d2["timing"]["streams_cached"] == 4
    assert d2["cells"] == d["cells"]
    # a changed trace file is searched again
    write_trace(repo / "traces" / "negative" / "NEG-T1.npz", "NEG-T1", "negative", unrelated_parts(555))
    assert run_st(world) == 0
    d3 = json.loads((repo / "results" / "threshold.json").read_text(encoding="utf-8"))
    assert d3["timing"]["streams_to_search"] == 1


def test_set_threshold_wait_until_rows_final_or_blocked(world, monkeypatch):
    repo = world["repo"]
    path = repo / "data" / "harvest_manifest.csv"
    full = read_csv(path)
    write_csv(path, MAN_COLS, [m for m in full if m["video_id"] != "NEG-T2"])
    calls = []

    def fake_sleep(s):                      # the harvester finishes NEG-T2 while we wait
        calls.append(s)
        write_csv(path, MAN_COLS, full)

    monkeypatch.setattr(st.time, "sleep", fake_sleep)
    assert run_st(world, "--wait", "--poll-seconds", "7") == 0
    assert calls == [7] and (repo / "results" / "threshold.json").exists()
    d = json.loads((repo / "results" / "threshold.json").read_text(encoding="utf-8"))
    assert d["cells"]["v2"]["10"]["full"]["n"] == 5
    # a blocked harvest never finishes the remaining rows: stop with exit 3
    (repo / "results" / "threshold.json").unlink()
    blocked = [dict(m, status="blocked") if m["video_id"] == "CAL-0" else m for m in full if m["video_id"] != "NEG-T2"]
    write_csv(path, MAN_COLS, blocked)
    monkeypatch.setattr(st.time, "sleep", lambda s: pytest.fail("should not wait when blocked"))
    assert run_st(world, "--wait") == 3 and not (repo / "results" / "threshold.json").exists()


# ---------------------------------------------------------------- evaluate

def _rows_for(rows, vid, ext=None, m=None, scope=None):
    return [r for r in rows if r["video_id"] == vid and (ext is None or r["extractor"] == ext)
            and (m is None or r["excerpt_min"] == str(m)) and (scope is None or r["scope"] == scope)]


def test_practice_pipeline_on_calibration(world, tmp_path):
    repo = world["repo"]
    assert run_st(world) == 0
    out = tmp_path / "practice"
    assert run_ev(world, "--practice", "--out", str(out)) == 0
    rows = read_csv(out / "test_results.csv")
    assert {r["split"] for r in rows} == {"calibration"} and {r["mode"] for r in rows} == {"practice"}
    # 6 searched streams x 15 searches, plus one row for the failed download
    assert len(rows) == 6 * 15 + 1
    fail = _rows_for(rows, "CAL-FAIL")
    assert len(fail) == 1 and fail[0]["harvest_status"] == "failed" and fail[0]["confidence"] == ""
    for i in range(5):
        for r in _rows_for(rows, f"CAL-{i}"):
            assert abs(int(r["error_s"])) <= 2 and r["hit_30s"] == "True", r
            assert int(r["error_D0_s"]) == int(r["error_s"]) + D_S and r["hit_30s_D0"] == "False"
            assert r["verdict"] == "match" and r["outcome"] == "confident hit"
    for r in _rows_for(rows, "CAL-5"):
        assert r["verdict"] == "no verdict"
    e = json.loads((out / "evaluation.json").read_text(encoding="utf-8"))
    g = e["gate"]
    assert g["N_processed"] == 6 and g["by_extractor"]["v2"]["n_streams_with_lock"] == 5
    assert g["by_extractor"]["v2"]["n_streams_with_lock_D0"] == 0
    assert g["gate_met"] is False                         # a practice run never meets the gate
    steps = {s["step"]: s["n"] for s in e["flow"]["steps"]}
    assert steps["selected"] == 8 and steps["download attempted"] == 7 and steps["audio obtained"] == 6
    assert steps["hum present (M1)"] == 5 and steps["hit (confident hit)"] == 5
    assert e["m3_counts"] == {"real": 1, "SYNTHETIC": 1, "harvested_not_yet_searched": 0}
    assert e["timing"]["negatives"]["streams_searched"] == 2          # searched after the first write
    assert e["m3"]["v2"]["10"]["full"]["together"]["false_match"]["n"] == 2
    assert "PRACTICE" in (out / "gate.md").read_text(encoding="utf-8")
    for name in ("flow_table.md", "false_matches.md", "false_matches.json"):
        assert (out / name).exists()
    # nothing went to results/ and no Test trace was cached
    assert not (repo / "results" / "test_results.csv").exists()
    assert not (repo / "results" / "search_cache" / "calibration").exists()
    assert not (out / "search_cache" / "test").exists()
    # second run: cached
    assert run_ev(world, "--practice", "--out", str(out)) == 0
    e2 = json.loads((out / "evaluation.json").read_text(encoding="utf-8"))
    assert e2["timing"]["streams_to_search"] == 0 and e2["gate"] == g


def test_damaged_trace_is_skipped_not_fatal(world, tmp_path):
    assert run_st(world) == 0
    (world["repo"] / "traces" / "calibration" / "CAL-1.npz").write_bytes(b"not a zip file")
    out = tmp_path / "p"
    assert run_ev(world, "--practice", "--out", str(out), "--no-negatives") == 0
    e = json.loads((out / "evaluation.json").read_text(encoding="utf-8"))
    assert e["timing"]["streams_unreadable"] == 1 and e["gate"]["N_processed"] == 5
    assert "CAL-1" in e["flow"]["drops"][2]["reasons"]["not searched yet"]


def test_practice_refuses_results_folder(world):
    assert run_ev(world, "--practice", "--out", str(world["repo"] / "results" / "x")) == 2


def test_real_mode_refuses_before_touching_test_traces(world, monkeypatch):
    repo = world["repo"]
    assert run_st(world) == 0
    monkeypatch.setattr(ec, "committed_unmodified",
                        lambda paths, repo=None: ["results/threshold.json: not committed (not in HEAD)"])

    def boom(*a, **k):
        raise AssertionError("a Test trace was searched before the blinding check passed")

    monkeypatch.setattr(ec, "run_searches", boom)
    monkeypatch.setattr(ec, "_trace_info", boom)
    assert run_ev(world) == 2
    assert not (repo / "results" / "test_results.csv").exists()
    assert not (repo / "results" / "search_cache" / "test").exists()
    assert "REFUSING" in (repo / "logs" / "evaluate.log").read_text(encoding="utf-8")


def _pass_git(monkeypatch, committed="2026-11-07T20:00:00+05:30"):
    monkeypatch.setattr(ec, "committed_unmodified", lambda paths, repo=None: [])
    monkeypatch.setattr(ec, "last_commit", lambda path, repo=None: {"commit": "f" * 40, "committed": committed})


def test_real_mode_gate(world, monkeypatch):
    repo = world["repo"]
    assert run_st(world) == 0
    _pass_git(monkeypatch)
    assert run_ev(world) == 0
    e = json.loads((repo / "results" / "evaluation.json").read_text(encoding="utf-8"))
    g = e["gate"]
    assert g["N_processed"] == 6 and g["by_extractor"]["v2"]["n_streams_with_lock"] == 5
    assert e["health"]["ok"] is True and g["gate_met"] is True
    gate_md = (repo / "results" / "gate.md").read_text(encoding="utf-8")
    assert "GATE MET" in gate_md and "S1 (D = 0" in gate_md
    rows = read_csv(repo / "results" / "test_results.csv")
    assert {r["split"] for r in rows} == {"test"} and {r["mode"] for r in rows} == {"real"}
    for name in ("test_results.csv", "gate.md", "flow_table.md", "evaluation.json", "false_matches.json"):
        txt = (repo / "results" / name).read_text(encoding="utf-8")
        assert str(Path.home()) not in txt and str(Path.home()).replace("\\", "/") not in txt
    assert not (repo / "results" / "gate_frozen.json").exists()          # before the decision time
    assert e["gate_freeze"]["status"] == "this run started before the decision time"
    # decision time before the harvest finished: nothing counts
    assert run_ev(world, "--decision-time", "2026-11-07T12:00:00+05:30") == 0
    g = json.loads((repo / "results" / "evaluation.json").read_text(encoding="utf-8"))["gate"]
    assert g["N_processed"] == 0 and g["gate_met"] is False


def test_real_mode_gate_fails_on_late_commit_or_block(world, monkeypatch):
    repo = world["repo"]
    assert run_st(world) == 0
    _pass_git(monkeypatch, committed="2026-11-07T23:00:00+05:30")
    assert run_ev(world) == 0
    g = json.loads((repo / "results" / "evaluation.json").read_text(encoding="utf-8"))["gate"]
    assert g["condition_1_2_met"] is True and g["tau_L_committed_in_time"] is False and g["gate_met"] is False
    _pass_git(monkeypatch)
    with open(repo / "logs" / "harvest_test.log", "a", encoding="utf-8") as f:
        f.write("2026-11-07T16:00:00+0530 WARNING yt-dlp: Sign in to confirm you're not a bot (SYNTHETIC)\n")
    assert run_ev(world) == 0
    e = json.loads((repo / "results" / "evaluation.json").read_text(encoding="utf-8"))
    assert e["health"]["ok"] is False and len(e["health"]["log_matches"]) == 1 and e["gate"]["gate_met"] is False


def test_pool_path_matches_inline(world, tmp_path):
    """The spawn pool (2 workers) gives the same search output as the inline path."""
    repo = world["repo"]
    ref = gref.load(world["ref_dir"])
    jobs = [ec.Job("calibration", f"CAL-{i}", f"traces/calibration/CAL-{i}.npz", unix_of(20_000 + 25_000 * i), [10, 20])
            for i in (0, 5)]
    a, ta = ec.run_searches(jobs, tmp_path / "c1", ref, world["ref_dir"], 1, repo=repo)
    b, tb = ec.run_searches(jobs, tmp_path / "c2", ref, world["ref_dir"], 2, repo=repo)
    assert ta["workers"] == 1 and tb["workers"] == 2 and tb["streams_searched"] == 2
    for k in a:
        sa = [{x: s[x] for x in s if x != "seconds"} for s in a[k]["searches"]]
        sb = [{x: s[x] for x in s if x != "seconds"} for s in b[k]["searches"]]
        assert sa == sb and len(sa) == 15
    assert not list((tmp_path / "c2").rglob("*.tmp"))


# --------------------------------------------------------- health and git

def test_harvest_health_rules(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    man = {"a": mrow("a", "test", finished="2026-11-07T10:00:00+00:00"),
           "b": mrow("b", "test", status="failed", reason="Private video", finished="2026-11-07T11:00:00+00:00"),
           "s": mrow("SYN-NOISE-01", "negative_synthetic", finished="2026-11-07T12:00:00+00:00", synthetic=True)}
    man["s"]["video_id"] = "SYN-NOISE-01"
    (logs / "harvest_test.log").write_text("2026-11-07T15:00:00+0530 INFO a [test]: excerpts 420..429 (new)\n",
                                           encoding="utf-8")
    h = ec.harvest_health(man, logs, None, tmp_path)
    assert h["most_recent_download"]["video_id"] == "b" and h["ok"] is False      # latest real attempt failed
    early = pd.Timestamp("2026-11-07T10:30:00+00:00").to_pydatetime()
    h = ec.harvest_health(man, logs, early, tmp_path)
    assert h["most_recent_download"]["video_id"] == "a" and h["ok"] is True and not h["log_matches"]
    man["b"]["status"] = "blocked"
    assert ec.harvest_health(man, logs, None, tmp_path)["blocked_rows"] == ["b"]
    (logs / "run_stage3.log").write_text("2026-11-07T17:00:00 end test exit 3\n", encoding="utf-8")
    h = ec.harvest_health({"a": man["a"]}, logs, None, tmp_path)
    assert h["ok"] is False and h["log_matches"][0]["file"].endswith("run_stage3.log")


def test_git_check_on_this_repo_is_read_only():
    assert ec.committed_unmodified(["gunj/match.py"]) == []                 # frozen and committed
    probs = ec.committed_unmodified(["no/such/file.py"])
    assert len(probs) == 1 and "not committed" in probs[0]


def test_no_absolute_paths_under_results(tmp_path):
    repo = tmp_path / "r"
    (repo / "results").mkdir(parents=True)
    with pytest.raises(ValueError):
        ec.write_json_atomic(repo / "results" / "x.json", {"p": str(repo / "traces" / "a.npz")}, repo)
    ec.write_json_atomic(tmp_path / "elsewhere.json", {"p": str(repo)}, repo)    # outside results/: allowed
    ec.write_json_atomic(repo / "results" / "y.json", {"p": "traces/test/a.npz"}, repo)
    assert not list((repo / "results").glob("*.tmp"))


# ------------------------------------------------- review fixes (7 Nov)

def _utc(s):
    return pd.Timestamp(s).to_pydatetime()


def test_harvest_health_skips_network_down_and_judges_at_decision_time(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    decision = _utc("2026-11-07T22:00:00+05:30")
    net = mrow("N1", "test", status="failed", reason=NET_REASON.format(vid="N1"), retryable="True",
               finished="2026-11-07T16:28:00+00:00")                       # 21:58 IST
    man = {"E": mrow("E", "test", finished="2026-11-07T16:20:00+00:00"), "N1": net}
    h = ec.harvest_health(man, logs, decision, tmp_path)
    # a network-down failure is not a download: the latest real download is E, done
    assert h["ok"] is True and h["most_recent_download"]["video_id"] == "E"
    assert [x["video_id"] for x in h["network_down_rows"]] == ["N1"]
    # an unavailable or other failure still counts
    man["U"] = mrow("U", "test", status="failed", reason="This video is not available", retryable="False",
                    finished="2026-11-07T16:25:00+00:00")
    h = ec.harvest_health(man, logs, decision, tmp_path)
    assert h["ok"] is False and h["most_recent_download"]["video_id"] == "U"
    del man["U"]
    # a block after the decision time does not fail the 22:00 gate; one before it does
    man["B1"] = mrow("B1", "test", status="blocked", reason="HTTP Error 429", finished="2026-11-07T16:45:00+00:00")
    h = ec.harvest_health(man, logs, decision, tmp_path)
    assert h["blocked_rows"] == [] and h["ok"] is True
    assert ec.harvest_health(man, logs, None, tmp_path)["blocked_rows"] == ["B1"]
    man["B1"]["finished_utc"] = "2026-11-07T16:10:00+00:00"
    assert ec.harvest_health(man, logs, decision, tmp_path)["blocked_rows"] == ["B1"]
    man["B1"]["finished_utc"] = ""                                         # unreadable time: still counts
    assert ec.harvest_health(man, logs, decision, tmp_path)["blocked_rows"] == ["B1"]


def test_harvest_health_keeps_attempts_a_later_retry_overwrote(tmp_path):
    """F failed at 21:58 IST and its retry finished after 22:00: the 22:00 answer must not change."""
    logs = tmp_path / "logs"
    logs.mkdir()
    decision = _utc("2026-11-07T22:00:00+05:30")
    man = {"E": mrow("E", "test", finished="2026-11-07T16:20:00+00:00"),
           "F": mrow("F", "test", status="failed", reason="short audio: 3000 s of 3900 s", retryable="True",
                     finished="2026-11-07T16:28:00+00:00"),
           "G": mrow("G", "test", finished="2026-11-07T17:00:00+00:00")}      # G's retry: done at 22:30 IST
    log_lines = [
        "2026-11-07T21:50:00+0530 INFO E: done, 3900.0 s audio, extraction 60 s, trace traces/test/E.npz",
        "2026-11-07T21:58:00+0530 WARNING F: failed (retryable): short audio: 3000 s of 3900 s",
        "2026-11-07T21:59:00+0530 WARNING G: failed, the laptop's network is down (not charged an attempt): "
        + NET_REASON.format(vid="G"),
        "2026-11-07T23:30:00+0530 INFO F: done, 3900.0 s audio, extraction 60 s, trace traces/test/F.npz",
    ]
    (logs / "harvest_test.log").write_text("\n".join(log_lines) + "\n", encoding="utf-8")
    h1 = ec.harvest_health(man, logs, decision, tmp_path)
    assert h1["ok"] is False and h1["most_recent_download"]["video_id"] == "F"
    # the retry overwrites F's manifest row after the decision time; the logs still hold the failure
    man["F"] = mrow("F", "test", finished="2026-11-07T18:00:00+00:00")
    h2 = ec.harvest_health(man, logs, decision, tmp_path)
    assert h2["ok"] is False and h2["most_recent_download"]["video_id"] == "F"
    assert h2["most_recent_download"]["status"] == "failed"
    assert h2["most_recent_download"]["source"] == "logs/harvest_test.log line 2"
    assert "G" in [x["video_id"] for x in h2["network_down_rows"]]
    # without the failure line, E (done at 21:50 IST) is the latest download by 22:00
    (logs / "harvest_test.log").write_text("\n".join(log_lines[:1] + log_lines[2:]) + "\n", encoding="utf-8")
    h3 = ec.harvest_health(man, logs, decision, tmp_path)
    assert h3["ok"] is True and h3["most_recent_download"]["video_id"] == "E"


def test_redact_paths_and_manifest_reason_with_path_does_not_stop_the_gate(world, monkeypatch):
    repo = world["repo"]
    home = str(Path.home())
    s = ec.redact_paths(f"x {repo}\\media\\tmp\\a.m4a; {str(repo).replace(chr(92), '/')}/b; "
                        f"{repr(str(repo))}; {home.upper()}\\cache\\c", repo)
    assert "<repo>" in s and "<home>" in s
    ec.check_no_abs_paths(s, repo)                                  # does not raise
    ec.check_no_abs_paths(json.dumps(s), repo)
    assert ec.redact_paths(None, repo) is None and ec.redact_paths("", repo) == ""
    # a Windows rename failure with the repr-doubled path, as yt-dlp reports it
    tmpf = repo / "media" / "tmp" / "TES-FAIL.src.m4a.part"
    bad = (f"ERROR: Unable to rename file: [WinError 32] The process cannot access the file because it is "
           f"being used by another process: {str(tmpf)!r} -> {str(tmpf.with_suffix(''))!r}")
    man = read_csv(repo / "data" / "harvest_manifest.csv")
    for m in man:
        if m["video_id"] == "TES-FAIL":
            m.update(reason=bad, retryable="True", finished_utc="2026-11-07T16:20:00+00:00")
    write_csv(repo / "data" / "harvest_manifest.csv", MAN_COLS, man)
    with open(repo / "logs" / "harvest_test.log", "a", encoding="utf-8") as f:
        f.write(f"2026-11-07T21:50:00+0530 WARNING TES-FAIL: failed (retryable): {bad}\n")
    assert run_st(world) == 0
    _pass_git(monkeypatch)
    assert run_ev(world) == 0
    for name in ("test_results.csv", "gate.md", "flow_table.md", "evaluation.json"):
        txt = (repo / "results" / name).read_text(encoding="utf-8")
        assert "<repo>" in txt, name
        ec.check_no_abs_paths(txt, repo)
    e = json.loads((repo / "results" / "evaluation.json").read_text(encoding="utf-8"))
    assert e["health"]["most_recent_download"]["video_id"] == "TES-FAIL"
    assert e["health"]["most_recent_download"]["reason"].count("<repo>") == 2


def test_network_down_rows_count_as_not_attempted(world, tmp_path):
    repo = world["repo"]
    splits = read_csv(repo / "data" / "splits.csv")
    splits.append(srow("CAL-NET", "calibration", 185_000))
    write_csv(repo / "data" / "splits.csv", SPLIT_COLS, splits)
    man = read_csv(repo / "data" / "harvest_manifest.csv")
    man.append(mrow("CAL-NET", "calibration", status="failed", reason=NET_REASON.format(vid="CAL-NET"),
                    retryable="True", finished="2026-11-07T05:10:00+00:00"))
    write_csv(repo / "data" / "harvest_manifest.csv", MAN_COLS, man)
    out = tmp_path / "p"
    assert run_ev(world, "--practice", "--out", str(out), "--no-negatives") == 0
    e = json.loads((out / "evaluation.json").read_text(encoding="utf-8"))
    steps = {s["step"]: s["n"] for s in e["flow"]["steps"]}
    assert steps["selected"] == 9 and steps["download attempted"] == 7 and steps["audio obtained"] == 6
    reasons = e["flow"]["drops"][0]["reasons"]
    assert reasons[ev.NETWORK_DOWN_BUCKET] == ["CAL-NET"] and reasons["not attempted yet"] == ["CAL-LATER"]
    assert all("getaddrinfo" not in k for k in e["flow"]["drops"][1]["reasons"])
    rows = read_csv(out / "test_results.csv")
    assert not _rows_for(rows, "CAL-NET") and len(_rows_for(rows, "CAL-FAIL")) == 1
    gate_md = (out / "gate.md").read_text(encoding="utf-8")
    assert "Attempted but no audio" in gate_md and "CAL-NET" not in gate_md.split("Attempted but no audio")[-1]


def test_transient_read_error_is_not_cached(world, tmp_path, monkeypatch):
    repo = world["repo"]
    ref = gref.load(world["ref_dir"])
    jobs = [ec.Job("calibration", "CAL-0", "traces/calibration/CAL-0.npz", unix_of(20_000), [10])]
    real_task, seen = ec.search_task, []

    def locked(t):                                    # the file cannot be read (e.g. briefly locked)
        r = real_task(dict(t, trace_abs=str(repo / "traces" / "calibration" / "gone.npz")))
        seen.append(r)
        return r

    monkeypatch.setattr(ec, "search_task", locked)
    recs, timing = ec.run_searches(jobs, tmp_path / "c", ref, world["ref_dir"], 1, repo=repo)
    assert recs == {} and timing["streams_not_cached_read_error"] == 1
    assert not (tmp_path / "c" / "calibration" / "CAL-0.json").exists()
    assert seen and all(r["transient"] and r["error"].startswith("FileNotFoundError") for r in seen)
    assert all("<repo>" in r["error"] and str(repo) not in r["error"] for r in seen)
    monkeypatch.setattr(ec, "search_task", real_task)
    recs, timing = ec.run_searches(jobs, tmp_path / "c", ref, world["ref_dir"], 1, repo=repo)
    assert timing["streams_to_search"] == 1 and timing["streams_searched"] == 1
    assert all(s["ok"] and "transient" not in s for s in recs["calibration/CAL-0"]["searches"])
    # an error from the trace content (no 60-min excerpt key) is a result, not transient
    r = ec.search_task({"key": "k", "ext": "v2", "length_min": 60, "scope": "full", "release_ts": unix_of(20_000),
                        "trace_abs": str(repo / "traces" / "calibration" / "CAL-0.npz"), "repo": str(repo)})
    assert r["ok"] is False and "transient" not in r and r.get("trace_sha256")


def test_cached_search_error_with_a_path_is_redacted(world):
    repo = world["repo"]
    ref = gref.load(world["ref_dir"])
    s = {"ext": "v2", "length_min": 10, "scope": "full", "error": f"PermissionError: [Errno 13] {repo}\\x.npz",
         "candidates": [], "confidence": None}
    out = ev.evaluate_search(s, {"hum": {}}, 100, 60, None, ref, repo)
    assert "<repo>" in out["verdict_reason"] and str(repo) not in out["verdict_reason"]


def test_gate_is_frozen_after_the_decision_time(world, monkeypatch, fixed_clock):
    repo = world["repo"]
    assert run_st(world) == 0
    _pass_git(monkeypatch)
    fixed_clock["now"] = dt.datetime(2026, 11, 7, 22, 30, tzinfo=IST)
    assert run_ev(world, "--no-negatives") == 0
    fz_path = repo / "results" / "gate_frozen.json"
    assert fz_path.exists()
    gate_md = (repo / "results" / "gate.md").read_text(encoding="utf-8")
    assert "FROZEN" in gate_md and "GATE MET" in gate_md
    e1 = json.loads((repo / "results" / "evaluation.json").read_text(encoding="utf-8"))
    assert e1["gate_freeze"]["status"] == "frozen this run" and e1["gate"]["gate_met"] is True
    # later: a block line is found that would change a recomputed gate; the frozen gate stays
    with open(repo / "logs" / "harvest_test.log", "a", encoding="utf-8") as f:
        f.write("2026-11-07T21:30:00+0530 ERROR TES-LATER: BLOCKED: HTTP Error 429 (SYNTHETIC)\n")
    assert ec.harvest_health(ec.read_manifest(repo / "data" / "harvest_manifest.csv"), repo / "logs",
                             _utc("2026-11-07T22:00:00+05:30"), repo)["ok"] is False
    fixed_clock["now"] = dt.datetime(2026, 11, 7, 23, 30, tzinfo=IST)
    assert run_ev(world, "--no-negatives") == 0
    assert (repo / "results" / "gate.md").read_text(encoding="utf-8") == gate_md
    e2 = json.loads((repo / "results" / "evaluation.json").read_text(encoding="utf-8"))
    assert e2["gate_freeze"]["status"] == "frozen" and e2["gate"] == e1["gate"] and e2["health"] == e1["health"]
    # a deleted gate.md is restored from the frozen file, unchanged
    (repo / "results" / "gate.md").unlink()
    assert run_ev(world, "--no-negatives") == 0
    assert (repo / "results" / "gate.md").read_text(encoding="utf-8") == gate_md
    # another decision time cannot overwrite the frozen gate
    assert run_ev(world, "--no-negatives", "--decision-time", "2026-11-07T23:00:00+05:30") == 2
    assert (repo / "results" / "gate.md").read_text(encoding="utf-8") == gate_md


def test_gate_not_frozen_while_a_stream_harvested_in_time_is_unsearched(world, monkeypatch, fixed_clock):
    repo = world["repo"]
    assert run_st(world) == 0
    _pass_git(monkeypatch)
    fixed_clock["now"] = dt.datetime(2026, 11, 7, 22, 5, tzinfo=IST)               # within the grace period
    assert run_ev(world, "--no-negatives") == 0
    assert not (repo / "results" / "gate_frozen.json").exists()
    fixed_clock["now"] = dt.datetime(2026, 11, 7, 22, 30, tzinfo=IST)
    (repo / "traces" / "test" / "TES-1.npz").write_bytes(b"not a zip file")      # cannot be searched this run
    assert run_ev(world, "--no-negatives") == 0
    assert not (repo / "results" / "gate_frozen.json").exists()
    e = json.loads((repo / "results" / "evaluation.json").read_text(encoding="utf-8"))
    assert "TES-1" in e["gate_freeze"]["status"]
    assert "Not frozen yet" in (repo / "results" / "gate.md").read_text(encoding="utf-8")
