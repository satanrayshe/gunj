"""Load and index the one-second Indian grid frequency reference.

The reference is built by scripts/build_reference.py from Mendeley Data
dataset bvwh9d6227 (Grid-India NERLDC PMU frequency, IIT Guwahati, CC BY 4.0).

On disk (in the derived folder, outside the repo):
  grid_1s_f32.npy       float32, Hz, one value per second, NaN where not measured
  grid_1s_valid_u8.npy  uint8, 1 = measured, 0 = missing
  grid_1s_src_u8.npy    uint8, source file of each second (1..5 = I..V, 0 = none)
  grid_1s_meta.json     t0, length, sources, licence, build rules

Time axis: index i is the second t0 + i, where t0 and every time handled here
are IST (UTC+05:30) wall-clock times kept as naive pandas Timestamps. India has
no daylight saving, so this axis has no folds or jumps. Timezone-aware inputs
are converted to IST before use; naive inputs are taken to be IST already.

Caveat: the labels are the source files' own labels, uncorrected. Overlapping
files disagree by 1-5 minutes on when the same values occurred, and labels drift
late within files (meta["time_labels"], meta["junctions"],
results/time_axis_check.json). Treat absolute time as uncertain by minutes.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

F32_NAME = "grid_1s_f32.npy"
VALID_NAME = "grid_1s_valid_u8.npy"
SRC_NAME = "grid_1s_src_u8.npy"
META_NAME = "grid_1s_meta.json"
IST_TZ = "Asia/Kolkata"
IST_OFFSET = pd.Timedelta(hours=5, minutes=30)


def default_derived_dir() -> Path:
    """Folder holding the built arrays: $GUNJ_DERIVED, else <threws>/data/derived."""
    env = os.environ.get("GUNJ_DERIVED")
    if env:
        return Path(env)
    # gunj/gunj/reference.py -> parents[2] is the threws folder
    return Path(__file__).resolve().parents[2] / "data" / "derived"


def to_ist(t) -> pd.Timestamp:
    """Return t as a naive IST Timestamp. Naive inputs are assumed to be IST."""
    ts = pd.Timestamp(t)
    if ts.tzinfo is not None:
        ts = ts.tz_convert(IST_TZ).tz_localize(None)
    return ts


def unix_to_ist(unix_seconds: float) -> pd.Timestamp:
    """Unix time (UTC seconds, e.g. yt-dlp release_timestamp) to naive IST."""
    return pd.Timestamp(unix_seconds, unit="s", tz="UTC").tz_convert(IST_TZ).tz_localize(None)


def ist_to_unix(t) -> float:
    """Naive IST (or tz-aware) time to Unix seconds."""
    ts = pd.Timestamp(t)
    if ts.tzinfo is None:
        ts = ts.tz_localize(IST_TZ)
    return ts.timestamp()


@dataclass
class Reference:
    """One contiguous one-second series. Arrays may be read-only memmaps."""

    freq: np.ndarray            # float32 Hz, NaN where valid == 0
    valid: np.ndarray           # uint8, 1 = measured
    t0: pd.Timestamp            # naive IST time of index 0
    meta: dict = field(default_factory=dict)
    source: np.ndarray | None = None  # uint8 file index (1..5), 0 = none

    def __post_init__(self):
        if self.freq.shape != self.valid.shape:
            raise ValueError("freq and valid differ in length")
        self.t0 = to_ist(self.t0)

    # ---- time <-> index -------------------------------------------------
    def __len__(self) -> int:
        return int(self.freq.shape[0])

    @property
    def t_end(self) -> pd.Timestamp:
        """IST time of the last sample (inclusive)."""
        return self.time_of(len(self) - 1)

    def index_of(self, t, *, check: bool = True) -> int:
        """Index of the second containing time t (floored to the whole second)."""
        sec = (to_ist(t) - self.t0).total_seconds()
        i = int(np.floor(sec))
        if check and not 0 <= i < len(self):
            raise IndexError(f"{to_ist(t)} is outside the reference ({self.t0} .. {self.t_end})")
        return i

    def time_of(self, i) -> pd.Timestamp:
        """Naive IST time of index i."""
        return self.t0 + pd.Timedelta(seconds=int(i))

    def times(self, i0: int, i1: int) -> pd.DatetimeIndex:
        """IST times for indices i0 .. i1-1."""
        return pd.date_range(self.time_of(i0), periods=max(int(i1) - int(i0), 0), freq="s")

    def contains(self, t) -> bool:
        return 0 <= self.index_of(t, check=False) < len(self)

    # ---- scopes ---------------------------------------------------------
    def window(self, start=None, end=None) -> tuple[int, int]:
        """Half-open index range [i0, i1) for IST times [start, end), clipped to the series."""
        i0 = 0 if start is None else self.index_of(start, check=False)
        i1 = len(self) if end is None else self.index_of(end, check=False)
        i0 = min(max(i0, 0), len(self))
        i1 = min(max(i1, i0), len(self))
        return i0, i1

    def scope(self, center, *, days: float = 0.0, seconds: float = 0.0) -> tuple[int, int]:
        """Index range for center +- (days, seconds), clipped. scope(t, days=90) is the
        +-90-day scope; scope(t, days=1) is the claimed-date scope."""
        half = pd.Timedelta(days=days) + pd.Timedelta(seconds=seconds)
        c = to_ist(center)
        i0, _ = self.window(c - half, None)
        i1 = min(self.index_of(c + half, check=False) + 1, len(self))
        return i0, max(i1, i0)

    def scope_full(self) -> tuple[int, int]:
        return 0, len(self)

    # ---- data access ----------------------------------------------------
    def slice(self, i0: int, i1: int) -> tuple[np.ndarray, np.ndarray]:
        """(freq, valid) views for [i0, i1). Views into memmaps: copy before writing."""
        return self.freq[i0:i1], self.valid[i0:i1]

    def valid_fraction(self, i0: int, i1: int) -> float:
        n = int(i1) - int(i0)
        return float(np.count_nonzero(self.valid[i0:i1])) / n if n > 0 else 0.0

    def filled(self, i0: int, i1: int, dtype=np.float64) -> np.ndarray:
        """Copy of [i0, i1) with missing seconds linearly interpolated from valid
        neighbours inside the range (held flat at the edges). All-missing -> NaN."""
        f = np.asarray(self.freq[i0:i1], dtype=dtype).copy()
        v = np.asarray(self.valid[i0:i1]).astype(bool)
        if v.all() or f.size == 0:
            return f
        if not v.any():
            f[:] = np.nan
            return f
        x = np.arange(f.size)
        f[~v] = np.interp(x[~v], x[v], f[v])
        return f

    def at(self, t) -> tuple[float, int]:
        """(frequency, valid flag) at IST time t."""
        i = self.index_of(t)
        return float(self.freq[i]), int(self.valid[i])


def load(derived_dir=None, *, mmap: bool = True, with_source: bool = False) -> Reference:
    """Load the built reference. mmap=True keeps RAM use near zero."""
    d = Path(derived_dir) if derived_dir else default_derived_dir()
    meta = json.loads((d / META_NAME).read_text(encoding="utf-8"))
    mode = "r" if mmap else None
    freq = np.load(d / F32_NAME, mmap_mode=mode)
    valid = np.load(d / VALID_NAME, mmap_mode=mode)
    src = np.load(d / SRC_NAME, mmap_mode=mode) if with_source and (d / SRC_NAME).exists() else None
    if freq.dtype != np.float32 or valid.dtype != np.uint8:
        raise ValueError(f"unexpected dtypes {freq.dtype}, {valid.dtype}")
    if len(freq) != int(meta["length"]):
        raise ValueError(f"length {len(freq)} differs from meta {meta['length']}")
    return Reference(freq=freq, valid=valid, t0=pd.Timestamp(meta["t0_ist"]), meta=meta, source=src)
