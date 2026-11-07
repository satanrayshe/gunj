"""Latency rule (PROTOCOL section 6, steps 4-6) on made-up o values (SYNTHETIC)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import calibrate_latency as cl  # noqa: E402


def _r(o, conf):
    return {"o_s": o, "confidence": conf}


def test_counted_median_when_three_or_more():
    res = [_r(10, 0.5), _r(14, 0.6), _r(30, 0.7), _r(5000, 0.9), _r(12, 0.01)]
    out = cl.latency_rule(res, tau=0.1)
    assert out["status"] == "calibrated"
    assert out["counted"]["n"] == 3                 # 5000 s is too far, 0.01 is below tau
    assert out["L_s"] == 14
    assert out["counted"]["constant_latency_holds"] is True


def test_fewer_than_three_gives_zero_and_label():
    out = cl.latency_rule([_r(10, 0.5), _r(20, 0.6), _r(30, 0.05)], tau=0.1)
    assert out["L_s"] == 0 and "not calibrated" in out["status"]


def test_range_flag_and_pending_without_tau():
    res = [_r(-40, 0.5), _r(0, 0.5), _r(45, 0.5)]
    out = cl.latency_rule(res, tau=0.1)
    assert out["counted"]["range_width_s"] == 85 and out["counted"]["constant_latency_holds"] is False
    pend = cl.latency_rule(res + [_r(900, 0.9)], tau=None)
    assert pend["L_s"] is None and pend["status"].startswith("PENDING")
    assert pend["provisional"]["n"] == 3 and "PROVISIONAL" in pend["provisional"]["what"]
