"""Harvester tests. No network: a fake downloader writes SYNTHETIC audio.

Every signal here is SYNTHETIC (a generated random walk and generated hum).
The tests cover the resume logic (kill mid-run, recovery from a finished
trace file, blocked runs, retries, atomic manifest writes) and the
SYNTHETIC cut generator that PROTOCOL M5 needs for Test streams.
"""
import csv
import logging
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import harvest as hv  # noqa: E402

from gunj.synthetic import cut_audio, synthetic_enf_walk, synthetic_hum  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")
LOG = logging.getLogger("harvest-test")
SPLIT_FIELDS = ["video_id", "split", "title", "duration_s", "excerpt_lengths_min",
                "release_timestamp", "label_drift_s"]


class SimulatedKill(BaseException):
    """Stands in for the process being killed (not caught by the harvester)."""


class FakeDownloader:
    """Writes SYNTHETIC hum audio (2 kHz WAV) instead of downloading."""

    def __init__(self, seconds=760, kill_on=(), block_on=(), fail_once=(), unavailable=()):
        self.seconds = seconds
        self.kill_on, self.block_on = set(kill_on), set(block_on)
        self.fail_once, self.unavailable = set(fail_once), set(unavailable)
        self.calls = {}
        self.total_requests = 0

    def fetch(self, video_id, outdir):
        self.calls[video_id] = self.calls.get(video_id, 0) + 1
        outdir.mkdir(parents=True, exist_ok=True)
        if video_id in self.kill_on:
            # a hard kill skips every cleanup; leave partial media in a dead run's folder
            dead = outdir.parent / f"{video_id}.deadrun"
            dead.mkdir(parents=True, exist_ok=True)
            (dead / f"{video_id}.src.part").write_bytes(b"partial")
            raise SimulatedKill()
        if video_id in self.block_on:
            raise hv.Blocked("Sign in to confirm you're not a bot (SYNTHETIC test)")
        if video_id in self.unavailable:
            raise hv.Unavailable("Private video (SYNTHETIC test)")
        if video_id in self.fail_once:
            self.fail_once.discard(video_id)
            raise hv.DownloadFailed("connection reset (SYNTHETIC test)")
        seed = sum(map(ord, video_id))
        walk = synthetic_enf_walk(self.seconds + 1, seed=seed)                  # SYNTHETIC
        audio = synthetic_hum(walk, sr=2000, amps={1: 0.05, 2: 0.2, 3: 0.05}, seed=seed)
        path = outdir / f"{video_id}.src.wav"
        sf.write(str(path), (0.3 * audio).astype(np.float32), 2000, subtype="FLOAT")
        return {"path": path, "format_id": "fake", "ext": "wav", "abr": "0.0", "protocol": "fake",
                "n_requests": 0, "title": "SYNTHETIC", "duration": self.seconds}


def make_repo(tmp_path, rows):
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    with open(repo / "data" / "splits.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SPLIT_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in SPLIT_FIELDS})
    return repo


def row(vid, split="dev", title="SYNTHETIC lecture", duration=760, lengths="10"):
    return {"video_id": vid, "split": split, "title": title, "duration_s": str(duration),
            "excerpt_lengths_min": lengths, "release_timestamp": "1670000000", "label_drift_s": "0"}


def args_for(*extra, splits="dev", extractors="naive"):
    return hv.build_parser().parse_args(["--splits", splits, "--extractors", extractors, *extra])


def manifest(repo):
    return hv.load_manifest(repo / "data" / "harvest_manifest.csv")


@needs_ffmpeg
def test_kill_mid_run_then_resume(tmp_path):
    repo = make_repo(tmp_path, [row("vidA"), row("vidB")])
    fake = FakeDownloader(kill_on={"vidB"})
    args = args_for("--keep-audio-splits", "dev")
    with pytest.raises(SimulatedKill):
        hv.run(args, downloader=fake, sleep=lambda s: None, repo=repo, log=LOG)
    m = manifest(repo)
    assert m["vidA"]["status"] == "done" and "vidB" not in m
    leftovers = list((repo / "media" / "tmp").iterdir())
    assert leftovers, "the simulated kill should leave partial media behind"

    fake.kill_on.clear()
    assert hv.run(args, downloader=fake, sleep=lambda s: None, repo=repo, log=LOG) == hv.EXIT_OK
    m = manifest(repo)
    assert m["vidA"]["status"] == m["vidB"]["status"] == "done"
    assert fake.calls == {"vidA": 1, "vidB": 2}          # A was not downloaded again
    assert list((repo / "media" / "tmp").iterdir()) == []
    for vid in ("vidA", "vidB"):
        r = m[vid]
        assert float(r["seconds"]) == pytest.approx(760, abs=0.01)
        flac = repo / r["flac_path"]
        back = hv.read_pcm16_any(flac)
        assert hv.sha256_pcm(back) == r["audio_sha256"]   # kept FLAC is the hashed 1 kHz audio
        with np.load(repo / r["trace_path"]) as z:
            keys = set(z.files)
        assert {"naive__full__freq", "naive__x10__freq", "meta_json"} <= keys
        meta = hv.read_npz_meta(repo / r["trace_path"])
        assert meta["complete"] and meta["traces"]["naive__x10"]["frame0_recording_s"] == 128.0
        assert r["hum_json"]


@needs_ffmpeg
def test_audio_deleted_when_split_not_kept(tmp_path):
    repo = make_repo(tmp_path, [row("vidC", split="calibration")])
    fake = FakeDownloader()
    assert hv.run(args_for("--keep-audio-splits", "dev", splits="calibration"), downloader=fake,
                  sleep=lambda s: None, repo=repo, log=LOG) == hv.EXIT_OK
    m = manifest(repo)["vidC"]
    assert m["status"] == "done" and m["flac_path"] == ""
    assert not (repo / "media" / "keep").exists() or not any((repo / "media" / "keep").iterdir())
    assert list((repo / "media" / "tmp").iterdir()) == []


@needs_ffmpeg
def test_recover_finished_trace_without_download(tmp_path):
    repo = make_repo(tmp_path, [row("vidA")])
    fake = FakeDownloader()
    args = args_for()
    hv.run(args, downloader=fake, sleep=lambda s: None, repo=repo, log=LOG)
    # simulate a kill after the trace file was renamed but before the manifest write
    (repo / "data" / "harvest_manifest.csv").unlink()
    assert hv.run(args, downloader=fake, sleep=lambda s: None, repo=repo, log=LOG) == hv.EXIT_OK
    m = manifest(repo)["vidA"]
    assert m["status"] == "done" and m["reason"] == "recovered from trace file"
    assert fake.calls == {"vidA": 1}


@needs_ffmpeg
def test_blocked_stops_run_and_refuses_restart(tmp_path):
    repo = make_repo(tmp_path, [row("vidA"), row("vidB")])
    fake = FakeDownloader(block_on={"vidA"})
    args = args_for()
    assert hv.run(args, downloader=fake, sleep=lambda s: None, repo=repo, log=LOG) == hv.EXIT_BLOCKED
    m = manifest(repo)
    assert m["vidA"]["status"] == "blocked" and "vidB" not in m
    assert fake.calls == {"vidA": 1}                    # stopped at the first block
    # a restart does not touch YouTube while a row is blocked
    assert hv.run(args, downloader=fake, sleep=lambda s: None, repo=repo, log=LOG) == hv.EXIT_BLOCKED
    assert fake.calls == {"vidA": 1}
    # only an explicit --clear-block (Shrey's decision) starts again
    fake.block_on.clear()
    assert hv.run(args_for("--clear-block"), downloader=fake, sleep=lambda s: None,
                  repo=repo, log=LOG) == hv.EXIT_OK
    assert manifest(repo)["vidA"]["status"] == "done"


@needs_ffmpeg
def test_retryable_and_permanent_failures(tmp_path):
    repo = make_repo(tmp_path, [row("vidA"), row("vidB")])
    fake = FakeDownloader(fail_once={"vidA"}, unavailable={"vidB"})
    sleeps = []
    args = args_for()
    hv.run(args, downloader=fake, sleep=sleeps.append, repo=repo, log=LOG)
    m = manifest(repo)
    assert m["vidA"]["status"] == "failed" and m["vidA"]["retryable"] == "True"
    assert m["vidB"]["status"] == "failed" and m["vidB"]["retryable"] == "False"
    assert len(sleeps) == 1 and 10.0 <= sleeps[0] <= 20.0   # random sleep between downloads
    hv.run(args, downloader=fake, sleep=lambda s: None, repo=repo, log=LOG)
    m = manifest(repo)
    assert m["vidA"]["status"] == "done" and m["vidA"]["attempts"] == "2"
    assert m["vidB"]["status"] == "failed" and fake.calls["vidB"] == 1   # not retried


def test_court_title_skipped_without_download(tmp_path):
    repo = make_repo(tmp_path, [row("vidA", title="High Court live streaming of hearing")])
    fake = FakeDownloader()
    assert hv.run(args_for(), downloader=fake, sleep=lambda s: None, repo=repo, log=LOG) == hv.EXIT_OK
    assert manifest(repo)["vidA"]["status"] == "skipped" and fake.calls == {}


def test_manifest_write_is_atomic_and_tmp_ignored(tmp_path):
    path = tmp_path / "m.csv"
    rows = {"a": {"video_id": "a", "split": "dev", "status": "done"}}
    hv.write_manifest(path, rows)
    # a crash during the next write leaves only a half-written .tmp behind
    (tmp_path / "m.csv.tmp").write_text("video_id,split\nbroken", encoding="utf-8")
    assert hv.load_manifest(path)["a"]["status"] == "done"
    traces = tmp_path / "traces" / "dev"
    traces.mkdir(parents=True)
    (traces / "x.npz.tmp").write_bytes(b"half")
    (traces / "y.npz").write_bytes(b"finished")
    media = tmp_path / "media" / "tmp"
    (media / "z.run1").mkdir(parents=True)
    (media / "z.run1" / "z.src.part").write_bytes(b"partial")
    n = hv.cleanup_partials(media, tmp_path / "traces", tmp_path / "keep", LOG)
    assert n == 2 and (traces / "y.npz").exists() and not (traces / "x.npz.tmp").exists()
    assert list(media.iterdir()) == []


def test_plan_reprocesses_rows_missing_cuts(tmp_path):
    repo = tmp_path
    (repo / "traces" / "test").mkdir(parents=True)
    (repo / "traces" / "test" / "t1.npz").write_bytes(b"x")
    m = {"t1": {"video_id": "t1", "split": "test", "status": "done", "extractors": "v2,v1,naive",
                "cuts": "0", "trace_path": "traces/test/t1.npz"}}
    rows = [row("t1", split="test", duration=4000, lengths="10;20;40;60")]
    kw = dict(trace_root=repo / "traces", keep_root=repo / "keep", keep_splits=set(),
              cut_splits={"test"}, repo=repo)
    assert hv.plan(rows, m, ["v2", "v1", "naive"], cuts=False, **kw)[0][1] == "skip"
    assert hv.plan(rows, m, ["v2", "v1", "naive"], cuts=True, **kw)[0][1] == "process"
    m["t1"]["cuts"] = "2"
    assert hv.plan(rows, m, ["v2", "v1", "naive"], cuts=True, **kw)[0][1] == "skip"
    assert hv.plan(rows, m, ["v2", "v1", "naive", "extra"], cuts=True, **kw)[0][1] == "process"


def test_cut_plan_is_seeded_and_within_bounds():
    a = hv.cut_plan("abcDEF12345")
    assert a == hv.cut_plan("abcDEF12345")                   # reproducible
    assert a != hv.cut_plan("abcDEF12346")                   # depends on the video ID
    assert a != hv.cut_plan("abcDEF12345", split_seed=hv.SPLIT_SEED + 1)   # and on the split seed
    seen = set()
    for i in range(400):
        for c in hv.cut_plan(f"vid{i:05d}"):
            assert c["length_s"] in hv.CUT_LENGTHS_S
            assert c["at_s"] >= 300 and c["at_s"] + c["length_s"] <= 3600 - 300
            assert c["at_recording_s"] == pytest.approx(120 + c["at_s"])
            assert "SYNTHETIC" in c["label"]
            seen.add(c["length_s"])
    assert seen == set(hv.CUT_LENGTHS_S)
    assert len(a) == 2


def test_cut_traces_from_synthetic_audio():
    sr = 1000
    walk = synthetic_enf_walk(3901, seed=5)                                    # SYNTHETIC
    audio = synthetic_hum(walk, sr=sr, amps={1: 0.05, 2: 0.2, 3: 0.05}, seed=6)
    arrays, meta = hv.build_traces(audio, sr, "cutTestVid1", [10, 60], ["naive"], "naive", cuts=True)
    plan = hv.cut_plan("cutTestVid1")
    assert [c["cut"] for c in meta["cuts"]] == [1, 2]
    x60 = arrays["naive__x60__freq"]
    assert x60.size == 3600 - 15
    for c in plan:
        key = f"naive__cut{c['cut']}"
        cut = arrays[f"{key}__freq"]
        assert cut.size == 3600 - c["length_s"] - 15
        assert meta["traces"][key]["SYNTHETIC"] is True
        # frames wholly before the cut equal the uncut excerpt's frames
        before = int(c["at_s"]) - 16
        assert np.mean(cut[:before] == x60[:before]) > 0.99
        # frames wholly after the cut are the uncut frames shifted by the cut length
        after0 = int(np.ceil(c["at_s"])) + 1
        n = cut.size - after0
        assert np.mean(cut[after0:after0 + n] == x60[after0 + c["length_s"]:after0 + c["length_s"] + n]) > 0.99
    # the cut audio itself: exactly length_s * sr samples removed at at_s
    ex = audio[120 * sr:3720 * sr]
    c = plan[0]
    edited = cut_audio(ex, sr, c["at_s"], c["length_s"])
    a = int(round(c["at_s"] * sr))
    assert edited.size == ex.size - c["length_s"] * sr
    assert np.array_equal(edited[:a], ex[:a])
    assert np.array_equal(edited[a:a + 1000], ex[a + c["length_s"] * sr:a + c["length_s"] * sr + 1000])


def test_synthetic_negatives_generated_not_downloaded(tmp_path):
    rows = [dict(row("SYN-NOISE-01", split="negative_synthetic", duration=3900, lengths="10"),
                 synthetic_seed="123"),
            dict(row("SYN-WALK-01", split="negative_synthetic", duration=3900, lengths="10"),
                 synthetic_seed="456")]
    repo = make_repo(tmp_path, rows)
    with open(repo / "data" / "splits.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SPLIT_FIELDS + ["synthetic_seed"])
        w.writeheader()
        for r in rows:
            w.writerow(r)
    fake = FakeDownloader()
    assert hv.run(args_for("--cuts", splits="negative_synthetic"), downloader=fake,
                  sleep=lambda s: None, repo=repo, log=LOG) == hv.EXIT_OK
    assert fake.calls == {}                                   # nothing sent to YouTube
    m = manifest(repo)
    for vid in ("SYN-NOISE-01", "SYN-WALK-01"):
        assert m[vid]["status"] == "done" and m[vid]["source_format"] == "SYNTHETIC"
        assert float(m[vid]["seconds"]) == 3900.0 and m[vid]["cuts"] == "0"
        meta = hv.read_npz_meta(repo / m[vid]["trace_path"])
        assert meta["SYNTHETIC"] is True and "naive__x10" in meta["traces"]
    a = hv.synthetic_negative_audio("SYN-WALK-01", 456)
    assert a.shape == (3_900_000,) and np.array_equal(a, hv.synthetic_negative_audio("SYN-WALK-01", 456))


def test_no_cuts_outside_cut_splits_or_short_audio():
    sr = 1000
    walk = synthetic_enf_walk(1001, seed=7)                                    # SYNTHETIC
    audio = synthetic_hum(walk, sr=sr, seed=8)
    arrays, meta = hv.build_traces(audio, sr, "shortVid", [10, 20, 40, 60], ["naive"], "naive", cuts=True)
    assert meta["cuts"] == [] and "naive__x10__freq" in arrays and "naive__x20__freq" not in arrays


def test_order_seed_is_deterministic_and_ignores_file_order(tmp_path):
    rows = [{"video_id": v, "split": "test"} for v in ["a1", "b2", "c3", "d4", "e5"]]
    rows.insert(2, {"video_id": "n1", "split": "negative"})
    fields = ["video_id", "split"]
    p1, p2 = tmp_path / "s1.csv", tmp_path / "s2.csv"
    for p, rs in ((p1, rows), (p2, list(reversed(rows)))):
        with open(p, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rs)
    plain = [r["video_id"] for r in hv.read_splits(p1, ["test"])]
    assert plain == ["a1", "b2", "c3", "d4", "e5"]          # default: file order
    s1 = [r["video_id"] for r in hv.read_splits(p1, ["test"], 2026110701)]
    s2 = [r["video_id"] for r in hv.read_splits(p2, ["test"], 2026110701)]
    assert s1 == s2                                         # file order does not matter
    assert s1 == sorted(s1, key=lambda v: hv.seeded_rank(2026110701, v))
    assert sorted(s1) == plain                              # same rows, only reordered


def test_network_down_is_not_charged_and_waits(tmp_path):
    assert hv.NETWORK_DOWN_RE.search("Unable to download API page: [Errno 11001] getaddrinfo failed")
    assert not hv.NETWORK_DOWN_RE.search("HTTP Error 429: Too Many Requests")
    assert not hv.NETWORK_DOWN_RE.search("Read timed out")
    states = iter([False, False, True])
    slept = []
    waited = hv.wait_for_network(logging.getLogger("t"), probe=lambda: next(states),
                                 sleep=slept.append, wait_s=5)
    assert waited == 10 and slept == [5, 5]
    assert hv.wait_for_network(logging.getLogger("t"), probe=lambda: True, sleep=slept.append) == 0


def test_network_down_rows_are_retried_past_max_attempts():
    import set_threshold as st
    net = {"status": "failed", "retryable": "True", "attempts": "5",
           "reason": "ERROR: [youtube] x: Unable to download API page: [Errno 11001] getaddrinfo failed"}
    other = {"status": "failed", "retryable": "True", "attempts": str(hv.MAX_ATTEMPTS), "reason": "short audio"}
    young = {"status": "failed", "retryable": "True", "attempts": "1", "reason": "short audio"}
    gone = {"status": "failed", "retryable": "False", "attempts": "1", "reason": "This video is not available"}
    assert not st.failed_is_final(net)
    assert st.failed_is_final(other)
    assert not st.failed_is_final(young)
    assert st.failed_is_final(gone)


@pytest.fixture(autouse=True)
def _network_is_up(monkeypatch):
    """No test in this module touches the real network probe."""
    monkeypatch.setattr(hv, "network_up", lambda *a, **k: True)
    monkeypatch.setattr(hv, "ctx_network_up", lambda: True)


def test_block_and_unavailable_wording():
    rate = ("ERROR: [youtube] abcdefghijk: This content isn't available, try again later. The current "
            "session has been rate-limited by YouTube for up to an hour.")
    assert hv.BLOCK_RE.search(rate)
    assert hv.BLOCK_RE.search("Sign in to confirm you're not a bot")
    assert hv.BLOCK_RE.search("HTTP Error 429: Too Many Requests")
    private = ("ERROR: [youtube] abcdefghijk: Private video. Sign in if you've been granted access to "
               "this video. Use --cookies-from-browser or --cookies for the authentication.")
    assert not hv.BLOCK_RE.search(private) and hv.UNAVAILABLE_RE.search(private)
    age = "Sign in to confirm your age. This video may be inappropriate for some users."
    assert not hv.BLOCK_RE.search(age) and hv.UNAVAILABLE_RE.search(age)
    assert not hv.BLOCK_RE.search("[hls] Failed to open segment 429 of playlist 0")
    assert hv.FF_429_EXIT_RE.search("ERROR: ffmpeg exited with code 3335375624")
    assert not hv.FF_429_EXIT_RE.search("ERROR: ffmpeg exited with code 3753488571")
    for s in ("[WinError 10051] A socket operation was attempted to an unreachable network",
              "Failed to resolve hostname rr1---sn.googlevideo.com: No such host is known."):
        assert hv.NETWORK_DOWN_RE.search(s)
    for s in ("Read timed out", "[SSL: UNEXPECTED_EOF_WHILE_READING]", "connection reset by peer"):
        assert not hv.NETWORK_DOWN_RE.search(s)


def test_ffreport_scan(tmp_path):
    rep = tmp_path / "ffreport.log"
    rep.write_text("ffmpeg started on 2026-11-07\nCommand line:\n"
                   '"ffmpeg" -headers "X-Seg: 429" -i "https://x.googlevideo.com/a?b=1"\n'
                   "[hls @ 0x1] Opening 'https://x/seg/429' for reading\n"
                   "[hls @ 0x1] Failed to open segment 429 of playlist 0\n", encoding="utf-8")
    s = hv._ffreport_scan(rep)
    assert not s["block"] and s["problems"] and "googlevideo" not in s["summary"]
    rep.write_text("Command line:\n\"ffmpeg\" -i x\n[https @ 0x2] HTTP error 429 Too Many Requests\n"
                   "Server returned 429 Too Many Requests\n", encoding="utf-8")
    assert hv._ffreport_scan(rep)["block"]
    rep.write_text("Command line:\n\"ffmpeg\" -i x\nsize=  1kB time=01:05:00.00\n", encoding="utf-8")
    s = hv._ffreport_scan(rep)
    assert not s["block"] and not s["problems"]


def test_wait_for_network_needs_stable_probes():
    seq = iter([True, False, True, True, True])
    slept = []
    hv.wait_for_network(LOG, probe=lambda: next(seq), sleep=slept.append, wait_s=1, stable=3)
    assert len(slept) == 4          # up, (down), up, up, up: the flap resets the count


def test_ambiguous_failure_with_network_down_is_not_charged(tmp_path, monkeypatch):
    repo = make_repo(tmp_path, [row("vidN", split="test")])
    fake = FakeDownloader(fail_once={"vidN"})
    probes = iter([False])
    monkeypatch.setattr(hv, "ctx_network_up", lambda: next(probes, True))
    assert hv.run(args_for(splits="test"), downloader=fake, sleep=lambda s: None, repo=repo, log=LOG) == hv.EXIT_OK
    m = manifest(repo)["vidN"]
    assert fake.calls["vidN"] == 2            # retried in the same run once the network was back
    assert m["status"] == "done" and m["attempts"] == "1"


def test_stops_after_unexplained_failures_in_a_row(tmp_path):
    vids = [f"vid{i}" for i in range(6)]
    repo = make_repo(tmp_path, [row(v, split="test") for v in vids])
    fake = FakeDownloader(fail_once=set(vids))
    rc = hv.run(args_for(splits="test"), downloader=fake, sleep=lambda s: None, repo=repo, log=LOG)
    assert rc == hv.EXIT_SUSPECT
    assert sum(fake.calls.values()) == hv.MAX_CONSECUTIVE_UNEXPLAINED
