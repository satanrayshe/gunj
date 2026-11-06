"""Shared fixtures. Every signal built here is SYNTHETIC (random walks and
generated hum); none of it is real grid or audio data."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from gunj import reference as gref
from gunj.synthetic import synthetic_enf_walk

SYNTH_T0 = "2022-03-01 00:00:00"   # arbitrary IST start for SYNTHETIC references


@pytest.fixture(scope="session")
def synth_ref_small():
    """SYNTHETIC 200,000-second reference (float32 Hz) and all-valid mask."""
    f = synthetic_enf_walk(200_000, seed=11, dtype=np.float32)
    return f, np.ones(f.size, dtype=np.uint8)


@pytest.fixture(scope="session")
def synth_ref_large():
    """SYNTHETIC 2,000,000-second reference, big enough to need several blocks."""
    f = synthetic_enf_walk(2_000_000, seed=12, dtype=np.float32)
    return f, np.ones(f.size, dtype=np.uint8)


def write_synth_ref_dir(path, freq, valid, t0=SYNTH_T0):
    """Write a SYNTHETIC reference in the on-disk layout gunj.reference.load reads."""
    path.mkdir(parents=True, exist_ok=True)
    np.save(path / gref.F32_NAME, freq.astype(np.float32))
    np.save(path / gref.VALID_NAME, valid.astype(np.uint8))
    meta = {"t0_ist": pd.Timestamp(t0).isoformat(), "length": int(freq.size),
            "note": "SYNTHETIC reference written by the test suite"}
    (path / gref.META_NAME).write_text(json.dumps(meta), encoding="utf-8")
    return path


@pytest.fixture()
def synth_ref_dir(tmp_path, synth_ref_small):
    f, v = synth_ref_small
    return write_synth_ref_dir(tmp_path / "ref", f, v)
