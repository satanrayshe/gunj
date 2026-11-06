"""Optional smoke test against the real derived reference.

Skipped unless the reference built by scripts/build_reference.py exists. It
maps the arrays read-only and takes a self-match: a 20-minute stretch of the
real reference is searched for within +-1 day of itself. This checks wiring
and indexing only; it is not an accuracy result.
"""
import numpy as np
import pytest

from gunj import reference as gref
from gunj.match import ncc_search

DERIVED = gref.default_derived_dir()
NEEDED = [DERIVED / gref.F32_NAME, DERIVED / gref.VALID_NAME, DERIVED / gref.META_NAME]


@pytest.mark.skipif(not all(p.exists() for p in NEEDED), reason="derived reference not built")
def test_real_reference_self_match():
    try:
        ref = gref.load(DERIVED, mmap=True)
    except Exception as exc:  # partly written or unexpected layout
        pytest.skip(f"reference not loadable: {exc}")
    assert isinstance(ref.freq, np.memmap) and not ref.freq.flags.writeable
    n = 1200
    i = None
    for cand in range(len(ref) // 2, len(ref) - n, 3600):
        if ref.valid[cand:cand + n].all():
            i = cand
            break
    if i is None:
        pytest.skip("no fully valid 20-minute stretch found")
    q = np.asarray(ref.freq[i:i + n], dtype=np.float64)
    res = ncc_search(q, ref.freq, ref.valid, start=max(0, i - 86_400), stop=i + 86_400)
    assert res.top.index == i
    assert res.top.r == pytest.approx(1.0, abs=1e-6)
