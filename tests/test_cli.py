"""CLI tests. The reference folder and the audio files are SYNTHETIC and are
written to a temp folder; the real reference is not touched."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import soundfile as sf

from gunj import cli
from gunj.synthetic import synthetic_hum

from conftest import SYNTH_T0

AMPS = {1: 0.1, 2: 0.3, 3: 0.1}


def run(capsys, argv):
    code = cli.main(argv)
    return code, json.loads(capsys.readouterr().out)


def write_config(path, threshold):
    path.write_text(json.dumps({
        "confidence_threshold": threshold, "confidence_threshold_calibrated": False,
        "confidence_definition": "test", "top_k": 5}), encoding="utf-8")
    return path


@pytest.fixture()
def hum_wav(tmp_path, synth_ref_small):
    """SYNTHETIC 900 s hum clip cut from the SYNTHETIC reference at P0."""
    ref, _ = synth_ref_small
    p0 = 123_456
    audio = synthetic_hum(ref[p0:p0 + 901].astype(np.float64), sr=1000, amps=AMPS, seed=8)
    p = tmp_path / "synthetic_hum.wav"
    sf.write(str(p), (0.05 * audio).astype(np.float32), 1000, subtype="FLOAT")
    return p, pd.Timestamp(SYNTH_T0) + pd.Timedelta(seconds=p0)


def test_no_verdict_on_pure_noise(tmp_path, capsys, synth_ref_dir):
    """(d) SYNTHETIC white noise: no hum, so "no verdict" and no search."""
    rng = np.random.default_rng(3)
    p = tmp_path / "synthetic_noise.wav"
    sf.write(str(p), (0.05 * rng.normal(size=300 * 8000)).astype(np.float32), 8000, subtype="FLOAT")
    code, out = run(capsys, ["date", str(p), "--ref", str(synth_ref_dir)])
    assert code == 0
    assert out["verdict"] == "no verdict"
    assert out["hum"]["present"] is False
    assert "no mains hum" in out["verdict_reason"]
    assert out["candidates"] == []
    assert out["audio"]["analysis_sr"] == 1000


def test_match_on_synthetic_hum(tmp_path, capsys, synth_ref_dir, hum_wav):
    p, start = hum_wav
    cfg = write_config(tmp_path / "cfg.json", 0.0)
    code, out = run(capsys, ["date", str(p), "--ref", str(synth_ref_dir), "--config", str(cfg)])
    assert code == 0
    assert out["hum"]["present"] is True
    assert len(out["candidates"]) == 5
    assert out["candidates"][0]["recording_start_ist"] == start.strftime("%Y-%m-%d %H:%M:%S")
    assert out["verdict"] == "match"
    assert out["recording_start_ist"] == out["candidates"][0]["recording_start_ist"]
    assert out["margin"] > 0
    assert out["threshold"]["calibrated"] is False
    assert "NOT CALIBRATED" in out["threshold"]["note"]


def test_claimed_day_scope(tmp_path, capsys, synth_ref_dir, hum_wav):
    p, start = hum_wav
    cfg = write_config(tmp_path / "cfg.json", 0.0)
    day = start.strftime("%Y-%m-%d")
    code, out = run(capsys, ["date", str(p), "--ref", str(synth_ref_dir), "--config", str(cfg),
                             "--scope", "1d", "--claimed", day])
    assert code == 0
    assert out["scope"]["mode"] == "1d"
    assert out["candidates"][0]["recording_start_ist"] == start.strftime("%Y-%m-%d %H:%M:%S")
    lo = pd.Timestamp(out["scope"]["recording_start_from_ist"])
    hi = pd.Timestamp(out["scope"]["recording_start_to_ist"])
    for c in out["candidates"]:
        assert lo <= pd.Timestamp(c["recording_start_ist"]) <= hi

    # a claimed day far from the truth cannot return the true start
    other = (start + pd.Timedelta(days=-1, hours=-1)).strftime("%Y-%m-%dT%H:%M:%S")
    code, out = run(capsys, ["date", str(p), "--ref", str(synth_ref_dir), "--config", str(cfg),
                             "--scope", "1d", "--claimed", other])
    assert code == 0
    assert all(c["recording_start_ist"] != start.strftime("%Y-%m-%d %H:%M:%S")
               for c in out["candidates"])


def test_scope_needs_claimed(capsys, synth_ref_dir, hum_wav):
    p, _ = hum_wav
    code, out = run(capsys, ["date", str(p), "--ref", str(synth_ref_dir), "--scope", "90d"])
    assert code == 2 and "needs --claimed" in out["error"]


def test_low_confidence_gives_no_verdict(tmp_path, capsys, synth_ref_dir, hum_wav):
    p, _ = hum_wav
    cfg = write_config(tmp_path / "cfg.json", 99.0)
    code, out = run(capsys, ["date", str(p), "--ref", str(synth_ref_dir), "--config", str(cfg)])
    assert out["verdict"] == "no verdict"
    assert "below threshold" in out["verdict_reason"]
    assert out["candidates"]            # candidates are still reported


def test_env_ref_and_excerpt(tmp_path, capsys, monkeypatch, synth_ref_dir, hum_wav):
    """GUNJ_REF selects the reference; excerpt times map back to the clip start."""
    p, start = hum_wav
    monkeypatch.setenv("GUNJ_REF", str(synth_ref_dir / "grid_1s_f32.npy"))
    cfg = write_config(tmp_path / "cfg.json", 0.0)
    code, out = run(capsys, ["date", str(p), "--config", str(cfg),
                             "--excerpt-start", "100", "--excerpt-length", "600"])
    assert code == 0
    assert Path(out["reference_dir"]) == synth_ref_dir
    assert out["candidates"][0]["recording_start_ist"] == start.strftime("%Y-%m-%d %H:%M:%S")


def test_edits_flag_reports_synthetic_cut(tmp_path, capsys, synth_ref_dir, synth_ref_small):
    """--edits finds a SYNTHETIC 40 s cut made at 600 s in SYNTHETIC hum."""
    from gunj.synthetic import cut_audio
    ref, _ = synth_ref_small
    p0 = 60_000
    audio = synthetic_hum(ref[p0:p0 + 1301].astype(np.float64), sr=1000, amps=AMPS, seed=9)
    audio = cut_audio(audio, 1000, 600.0, 40.0)
    p = tmp_path / "synthetic_cut.wav"
    sf.write(str(p), (0.05 * audio).astype(np.float32), 1000, subtype="FLOAT")
    cfg = write_config(tmp_path / "cfg.json", 0.0)
    code, out = run(capsys, ["date", str(p), "--ref", str(synth_ref_dir), "--config", str(cfg),
                             "--edits"])
    assert code == 0
    found = out["edits"]["found"]
    assert len(found) == 1
    assert abs(found[0]["jump_s"] - 40) <= 2
    assert abs(found[0]["clip_time_s"] - 600) <= 10
    assert found[0]["kind"] == "cut"


def test_default_config_is_marked_placeholder():
    """Before scripts/set_threshold.py runs the threshold is a marked placeholder;
    after it runs, the config must match results/threshold.json (PROTOCOL section 10)."""
    cfg = cli.load_config()
    if not cfg["confidence_threshold_calibrated"]:
        assert "PLACEHOLDER" in cfg["_note"]
        return
    thr = json.loads((Path(cli.__file__).resolve().parents[1] / "results" / "threshold.json")
                     .read_text(encoding="utf-8"))
    ext = cfg["confidence_threshold_extractor"]
    assert ext == thr["primary_extractor"]
    cells = {m: {s: c["tau"] for s, c in by.items()} for m, by in thr["cells"][ext].items()}
    assert cfg["confidence_threshold_cells"] == cells
    assert cfg["confidence_threshold"] == max(t for by in cells.values() for t in by.values() if t is not None)
    assert "PLACEHOLDER" not in cfg["_note"]


def test_extract_writes_csv(tmp_path, capsys, hum_wav):
    p, _ = hum_wav
    out_csv = tmp_path / "trace.csv"
    code, out = run(capsys, ["extract", str(p), "--out", str(out_csv)])
    assert code == 0
    df = pd.read_csv(out_csv)
    assert list(df.columns[:4]) == ["t_s", "freq_hz", "freq_raw_hz", "snr"]
    assert len(df) == out["trace_seconds"] == 900 - 16 + 1
    assert df["t_s"].iloc[0] == 8.0


def test_console_script_installed():
    exe = shutil.which("gunj", path=str(Path(sys.executable).parent))
    if exe is None:
        pytest.skip("gunj console script not on this interpreter's Scripts path")
    out = subprocess.run([exe, "--version"], capture_output=True, text=True, check=True)
    assert out.stdout.strip().startswith("gunj ")
