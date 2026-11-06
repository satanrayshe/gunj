"""Offline tests for scripts/harvest_metadata.py (no network)."""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import harvest_metadata as h  # noqa: E402

IST = h.IST


def ts(y, m, d, hh=12):
    return int(dt.datetime(y, m, d, hh, tzinfo=IST).timestamp())


def test_coverage_edges():
    assert not h.in_coverage(ts(2020, 12, 19, 23))
    assert h.in_coverage(int(dt.datetime(2020, 12, 20, 0, 0, tzinfo=IST).timestamp()))
    assert h.in_coverage(ts(2023, 8, 20, 23))
    assert not h.in_coverage(int(dt.datetime(2023, 8, 21, 0, 0, tzinfo=IST).timestamp()))


def test_keep_reason():
    src = {'country': 'IN'}
    good = {'live_status': 'was_live', 'release_timestamp': ts(2022, 3, 1),
            'duration': 1500, 'title': 'Budget session', 'channel': 'X', 'availability': 'public'}
    assert h.keep_reason(good, src) == ''
    assert h.keep_reason({**good, 'duration': 1199}, src) == 'too_short'
    assert h.keep_reason({**good, 'release_timestamp': ts(2023, 9, 1)}, src) == 'outside_coverage'
    assert h.keep_reason({**good, 'live_status': 'not_live'}, src).startswith('live_status')
    assert h.keep_reason({**good, 'title': 'Live streaming of High Court of Gujarat'}, src) == 'court_excluded'
    assert h.keep_reason(good, {'country': 'NP'}) == 'excluded_country'


def test_block_classifier():
    assert h.classify_error("Sign in to confirm you're not a bot") == 'blocked'
    assert h.classify_error('HTTP Error 429: Too Many Requests') == 'blocked'
    assert h.classify_error('Sign in to confirm your age') == 'unavailable'
    assert h.classify_error('Private video') == 'unavailable'


def test_pick_spreads_and_filters():
    listed = dt.datetime(2026, 11, 7, tzinfo=dt.timezone.utc)
    entries = []
    pos = 0
    # 10 entries per "N years ago" block for N = 2..6, newest first
    for k in range(2, 7):
        for i in range(10):
            entries.append({'tab': 'streams', 'pos': pos, 'id': f'v{pos}', 'title': 't',
                            'duration': 3600, 'approx_age_y': float(k), 'approx_ts': 0,
                            'live_status': 'was_live'})
            pos += 1
    flat = {'listed_at': listed.isoformat(), 'channel_id': None, 'entries': entries}
    picks = h.pick_for_fetch(flat, 5)
    assert len(picks) == 5
    assert all(2.5 <= p['approx_age_y'] < 5.5 for p in picks)
    lo = h.COVER_START.timestamp() - 20 * 86400
    hi = h.COVER_END.timestamp() + 20 * 86400
    assert all(lo <= p['est_ts'] <= hi for p in picks)
