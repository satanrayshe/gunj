#!/usr/bin/env python
"""Metadata-only harvest of candidate YouTube live streams for Gunj.

NO MEDIA IS DOWNLOADED. This script only reads public metadata pages
(channel "Live" tabs and watch pages) through the yt-dlp Python API with
skip_download, no cookies, no sign-in and no proxy. Every HTTP request is
spaced by at least MIN_GAP_S seconds and logged.

If YouTube answers with a bot check, CAPTCHA, sign-in wall or HTTP 429 the
script stops at once, keeps what it already has, and exits with code 3.

Subcommands
  search  --query Q [--query Q ...]   channel search (channels filter), logged
  run     --sources FILE [--group india|negative] [--only k1,k2]
          flat-list each source's Live tab, sample in-window streams, fetch
          per-video metadata, write data/meta_raw/<id>.json
  build   --sources FILE              write candidate CSVs and the summary

Paths default to the repo layout (data/, results/); override with flags.
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yt_dlp
from yt_dlp.networking.exceptions import HTTPError
from yt_dlp.utils import DownloadError, ExtractorError

REPO = Path(__file__).resolve().parents[1]
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
UTC = dt.timezone.utc

# Reference coverage (IST, from PREP.md section 3): 20 Dec 2020 .. 20 Aug 2023.
COVER_START = dt.datetime(2020, 12, 20, 0, 0, 0, tzinfo=IST)
COVER_END = dt.datetime(2023, 8, 21, 0, 0, 0, tzinfo=IST)  # exclusive
MIN_DURATION_S = 20 * 60
MIN_GAP_S = 2.5            # minimum spacing between any two HTTP requests
REQUEST_CAP = 1500         # total metadata HTTP requests across all runs
STOP_INDIA_N = 300         # stop early once this many Indian candidates ...
STOP_INDIA_CHANNELS = 15   # ... spread over at least this many channels
STOP_NEG_N = 120           # negatives target
STOP_NEG_CHANNELS = 8

# Court content is excluded (Gujarat HC Live Streaming Rules 2021 and similar).
COURT_RE = re.compile(
    r'high\s*court|supreme\s*court|\bcourt\s*(?:no\b|room|hall|hearing)|courtroom|'
    r'tribunal|\bnclat?\b|\bjudiciary\b|\bhearing\b|\blive\s*streaming\s*of\s*court',
    re.I)
# Excluded grids that may be synchronous with India.
EXCLUDED_COUNTRIES = {'NP', 'BT'}
BLOCK_RE = re.compile(
    r"not a bot|confirm you.?re not|captcha|unusual traffic|too many requests|"
    r"http error 429|sign in to confirm(?! your age)|/sorry/",
    re.I)
SKIP_RE = re.compile(
    r'private video|members.only|join this channel|video unavailable|has been removed|'
    r'confirm your age|age.restricted|premiere|will begin|is not available|terminated|'
    r'copyright',
    re.I)

DROP_KEYS = {
    'formats', 'requested_formats', 'automatic_captions', 'subtitles', 'thumbnails',
    'heatmap', 'http_headers', 'url', 'manifest_url', 'fragments', '_format_sort_fields',
    'requested_subtitles', 'requested_downloads', 'storyboards', 'format', 'format_id',
}


class Blocked(RuntimeError):
    """YouTube refused us (bot check, CAPTCHA, sign-in wall or HTTP 429)."""


class CapReached(RuntimeError):
    """Global request cap reached."""


class Paths:
    def __init__(self, data: Path, results: Path):
        self.data = data
        self.results = results
        self.raw = data / 'meta_raw'
        self.flat = data / 'meta_flat'
        self.req_log = data / 'harvest_requests.jsonl'
        self.err_log = data / 'harvest_errors.jsonl'
        self.events = data / 'harvest_events.jsonl'
        for p in (self.data, self.results, self.raw, self.flat):
            p.mkdir(parents=True, exist_ok=True)


def now_utc() -> dt.datetime:
    return dt.datetime.now(UTC)


def append_jsonl(path: Path, obj: dict) -> None:
    with path.open('a', encoding='utf-8') as f:
        f.write(json.dumps(obj, ensure_ascii=False) + '\n')


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with path.open(encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


class CountingYDL(yt_dlp.YoutubeDL):
    """YoutubeDL that spaces, counts and logs every HTTP request."""

    def __init__(self, params, paths: Paths, phase: str):
        super().__init__(params)
        self._paths = paths
        self._phase = phase
        self._last = 0.0
        self.n_requests = len(read_jsonl(paths.req_log))

    def set_phase(self, phase: str) -> None:
        self._phase = phase

    request_cap = REQUEST_CAP

    def urlopen(self, req):
        if self.n_requests >= self.request_cap:
            raise CapReached(f'request cap {self.request_cap} reached')
        url = req if isinstance(req, str) else getattr(req, 'url', str(req))
        gap = time.monotonic() - self._last
        if gap < MIN_GAP_S:
            time.sleep(MIN_GAP_S - gap)
        u = urlparse(url)
        rec = {'t': now_utc().isoformat(timespec='seconds'), 'phase': self._phase,
               'host': u.netloc, 'path': u.path}
        v = parse_qs(u.query).get('v')
        if v:
            rec['v'] = v[0]
        self.n_requests += 1
        try:
            resp = super().urlopen(req)
            rec['status'] = getattr(resp, 'status', None)
            final = getattr(resp, 'url', '') or ''
            if '/sorry/' in final or 'consent.youtube' in final:
                rec['blocked'] = final[:120]
                append_jsonl(self._paths.req_log, rec)
                raise Blocked(f'redirected to {final[:120]}')
            append_jsonl(self._paths.req_log, rec)
            return resp
        except HTTPError as e:
            rec['status'] = e.status
            append_jsonl(self._paths.req_log, rec)
            if e.status == 429:
                raise Blocked('HTTP 429 Too Many Requests') from e
            raise
        except Blocked:
            raise
        except Exception as e:  # network errors
            rec['status'] = 'exc:' + type(e).__name__
            append_jsonl(self._paths.req_log, rec)
            raise
        finally:
            self._last = time.monotonic()


def make_ydl(paths: Paths, phase: str) -> CountingYDL:
    params = {
        'skip_download': True,
        'simulate': True,
        'quiet': True,
        'no_warnings': True,
        'noprogress': True,
        'ignoreerrors': False,
        'ignore_no_formats_error': True,
        'check_formats': False,
        'extract_flat': 'in_playlist',
        'lazy_playlist': True,
        'sleep_interval_requests': 2,   # yt-dlp's own --sleep-requests 2
        'retries': 1,
        'extractor_retries': 1,
        'socket_timeout': 30,
        'cachedir': False,
        'writesubtitles': False,
        'writeautomaticsub': False,
        'getcomments': False,
        'extractor_args': {
            # Only the watch page itself is read; no player JS, no extra clients.
            'youtube': {'player_client': ['web'], 'player_skip': ['js', 'configs'],
                        'skip': ['hls', 'dash', 'translated_subs'],
                        'lang': ['en']},
            'youtubetab': {'approximate_date': ['']},
        },
    }
    return CountingYDL(params, paths, phase)


def classify_error(msg: str) -> str:
    if BLOCK_RE.search(msg or ''):
        return 'blocked'
    if SKIP_RE.search(msg or ''):
        return 'unavailable'
    return 'error'


def ist_iso(ts) -> str | None:
    if ts is None:
        return None
    return dt.datetime.fromtimestamp(int(ts), IST).isoformat()


def in_coverage(ts) -> bool:
    if ts is None:
        return False
    t = dt.datetime.fromtimestamp(int(ts), UTC)
    return COVER_START <= t < COVER_END


def approx_age_years(ts, ref: dt.datetime) -> float | None:
    if ts is None:
        return None
    return (ref.timestamp() - ts) / (365.25 * 86400)


# --------------------------------------------------------------------- search

def cmd_search(args, paths: Paths) -> int:
    ydl = make_ydl(paths, 'search')
    out = paths.data / 'channel_search.jsonl'
    for q in args.query:
        url = ('https://www.youtube.com/results?search_query='
               + q.replace(' ', '+') + '&sp=EgIQAg%253D%253D')
        try:
            info = ydl.extract_info(url, download=False, process=False)
        except Blocked as e:
            append_jsonl(paths.events, {'t': now_utc().isoformat(), 'event': 'blocked', 'msg': str(e)})
            print('BLOCKED:', e)
            return 3
        except (DownloadError, ExtractorError) as e:
            if classify_error(str(e)) == 'blocked':
                print('BLOCKED:', e)
                return 3
            print('error', q, e)
            continue
        res = []
        for i, ent in enumerate(info.get('entries') or []):
            if i >= args.n:
                break
            res.append({k: ent.get(k) for k in ('channel', 'channel_id', 'uploader_id',
                                                 'channel_follower_count', 'url')})
        append_jsonl(out, {'t': now_utc().isoformat(), 'query': q, 'results': res})
        print(f'## {q}')
        for r in res:
            print('   ', r['channel'], '|', r['uploader_id'], '|', r['channel_id'],
                  '|', r['channel_follower_count'])
    print('requests so far:', ydl.n_requests)
    return 0


# ------------------------------------------------------------------- listing

def tab_urls(src: dict) -> list[str]:
    """Expand a source into the URLs to list.

    tabs: "streams" (default) lists the channel's Live tab newest first;
    "search:Q" lists the channel's own search results for Q (used to reach
    2021-2023 streams on channels that stream daily).
    """
    base = src['url'].rstrip('/')
    if '/playlist' in base or '/results' in base:
        return [base]
    out = []
    for t in src.get('tabs', ['streams']):
        if t.startswith('search:'):
            out.append(base + '/search?query=' + t.split(':', 1)[1].replace(' ', '+'))
        else:
            out.append(base + '/' + t)
    return out


def flat_list(ydl: CountingYDL, src: dict, paths: Paths, ref_now: dt.datetime) -> dict:
    """List a source's Live tab (or its channel search) lazily, newest first."""
    cache = paths.flat / f"{src['key']}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding='utf-8'))
    ydl.set_phase(f"list:{src['key']}")
    max_items = int(src.get('max_pages', 25)) * 30
    n0 = ydl.n_requests
    entries, stops, seen = [], [], set()
    channel = channel_id = None
    for url in tab_urls(src):
        stop = 'exhausted'
        n_pool = 0
        try:
            info = ydl.extract_info(url, download=False, process=False)
            channel = channel or info.get('channel') or info.get('uploader') or info.get('title')
            channel_id = channel_id or info.get('channel_id')
            for pos, ent in enumerate(info.get('entries') or []):
                if pos >= max_items:
                    stop = 'max_pages'
                    break
                age = approx_age_years(ent.get('timestamp'), ref_now)
                if ent.get('id') in seen:
                    continue
                seen.add(ent.get('id'))
                entries.append({
                    'tab': url.rsplit('/', 1)[-1], 'pos': pos, 'id': ent.get('id'),
                    'title': ent.get('title'), 'duration': ent.get('duration'),
                    'approx_ts': ent.get('timestamp'),
                    'approx_age_y': None if age is None else round(age, 2),
                    'live_status': ent.get('live_status'),
                    'channel_id': ent.get('channel_id'),
                })
                if '/search?' in url:
                    continue
                # Newest first on the Live tab: "6 years ago" is already before the window.
                if age is not None and age >= 5.75:
                    stop = 'older_than_window'
                    break
                if age is not None and 2.5 <= age < 5.5:
                    n_pool += 1
                    if n_pool >= int(src.get('max_pool', 240)):
                        stop = 'max_pool'
                        break
        except (Blocked, CapReached):
            raise
        except (DownloadError, ExtractorError) as e:
            kind = classify_error(str(e))
            if kind == 'blocked':
                raise Blocked(str(e)) from e
            stop = f'{kind}: {str(e)[:200]}'
        stops.append(stop)
    res = {'key': src['key'], 'urls': tab_urls(src), 'channel': channel, 'channel_id': channel_id,
           'listed_at': now_utc().isoformat(timespec='seconds'),
           'n_entries': len(entries), 'stop': '; '.join(stops),
           'n_requests': ydl.n_requests - n0, 'entries': entries}
    cache.write_text(json.dumps(res, ensure_ascii=False, indent=0), encoding='utf-8')
    return res


YEAR_S = 365.25 * 86400


def estimate_times(flat: dict) -> None:
    """Refine coarse "N years ago" dates on Live tabs using list position.

    A Live tab is newest first, so inside one complete "N years ago" block the
    i-th of n entries is roughly (N + (i + 0.5) / n) years old. Only blocks
    followed by an older block are complete. The last (oldest) block may be
    cut short by the page cap; its entries are dated with the streaming rate
    of the block before it. Sets e['est_ts'] where possible.
    """
    ref = dt.datetime.fromisoformat(flat['listed_at']).timestamp()
    tabs = collections.defaultdict(list)
    for e in flat['entries']:
        if not e.get('tab', '').startswith('search?') and e.get('approx_age_y') is not None:
            tabs[e['tab']].append(e)
    for es in tabs.values():
        blocks = collections.defaultdict(list)
        for e in es:
            blocks[round(e['approx_age_y'])].append(e)
        kmax = max(blocks)
        for k, b in blocks.items():
            b.sort(key=lambda e: e['pos'])
            if k < kmax:
                for i, e in enumerate(b):
                    e['est_ts'] = ref - (k + (i + 0.5) / len(b)) * YEAR_S
            elif (k - 1) in blocks:
                rate = len(blocks[k - 1])  # streams per year in the previous, complete block
                for i, e in enumerate(b):
                    e['est_ts'] = ref - (k + min((i + 0.5) / rate, 1.0)) * YEAR_S


def pick_for_fetch(flat: dict, max_fetch: int, done: set | None = None) -> list[dict]:
    """Choose in-window-looking, long-enough streams, evenly spaced in time.

    `done` holds ids already fetched for this source; they count towards
    max_fetch and only the remainder is picked from the other streams.
    """
    estimate_times(flat)
    margin = 20 * 86400
    lo, hi = COVER_START.timestamp() - margin, COVER_END.timestamp() + margin
    pool = []
    for e in flat['entries']:
        age = e.get('approx_age_y')
        dur = e.get('duration')
        if not e.get('id'):
            continue
        if e.get('live_status') in ('is_live', 'is_upcoming'):
            continue
        if e.get('tab', '').startswith('search?') and e.get('live_status') != 'was_live':
            continue  # channel search mixes uploads and streams; keep streams only
        if flat.get('channel_id') and e.get('channel_id') and e['channel_id'] != flat['channel_id']:
            continue
        # "N years ago" with N in {3, 4, 5} spans Nov 2020 .. Nov 2023 (from Nov 2026).
        if age is None or not (2.5 <= age < 5.5):
            continue
        if e.get('est_ts') is not None and not (lo <= e['est_ts'] <= hi):
            continue
        if dur is not None and dur < MIN_DURATION_S:
            continue
        if COURT_RE.search(e.get('title') or ''):
            continue
        pool.append(e)
    pool.sort(key=lambda e: (-(e.get('est_ts') or e.get('approx_ts') or 0), e.get('tab', ''), e['pos']))
    if done:
        max_fetch -= sum(1 for e in pool if e['id'] in done)
        pool = [e for e in pool if e['id'] not in done]
    if max_fetch <= 0:
        return []
    if len(pool) <= max_fetch:
        return pool
    if max_fetch == 1:
        return [pool[len(pool) // 2]]
    idx = sorted({round(i * (len(pool) - 1) / (max_fetch - 1)) for i in range(max_fetch)})
    return [pool[i] for i in idx]


def reduce_info(info: dict) -> dict:
    return {k: v for k, v in info.items() if k not in DROP_KEYS and not k.startswith('__')}


def fetch_video(ydl: CountingYDL, vid: str, src: dict, paths: Paths) -> dict | None:
    out = paths.raw / f'{vid}.json'
    if out.exists():
        return json.loads(out.read_text(encoding='utf-8'))
    ydl.set_phase(f"video:{src['key']}")
    try:
        info = ydl.extract_info(f'https://www.youtube.com/watch?v={vid}', download=False)
    except (Blocked, CapReached):
        raise
    except (DownloadError, ExtractorError) as e:
        kind = classify_error(str(e))
        append_jsonl(paths.err_log, {'t': now_utc().isoformat(timespec='seconds'), 'id': vid,
                                     'source': src['key'], 'kind': kind, 'msg': str(e)[:300]})
        if kind == 'blocked':
            raise Blocked(str(e)) from e
        return None
    red = reduce_info(info)
    red['_gunj'] = {'source_key': src['key'], 'group': src['group'],
                    'fetched_at': now_utc().isoformat(timespec='seconds'),
                    'note': 'metadata only; formats/captions/thumbnail URL lists dropped; no media'}
    out.write_text(json.dumps(red, ensure_ascii=False, indent=1), encoding='utf-8')
    return red


def keep_reason(info: dict, src: dict) -> str:
    """'' if the video is a usable candidate, else why not."""
    ts = info.get('release_timestamp') or info.get('timestamp')
    if info.get('live_status') != 'was_live':
        return f"live_status={info.get('live_status')}"
    if ts is None:
        return 'no_timestamp'
    if not in_coverage(ts):
        return 'outside_coverage'
    if (info.get('duration') or 0) < MIN_DURATION_S:
        return 'too_short'
    text = ' '.join(str(info.get(k) or '') for k in ('title', 'channel'))
    if COURT_RE.search(text):
        return 'court_excluded'
    if src.get('country') in EXCLUDED_COUNTRIES:
        return 'excluded_country'
    if info.get('availability') not in (None, 'public', 'unlisted'):
        return f"availability={info.get('availability')}"
    return ''


def load_sources(path: Path) -> list[dict]:
    srcs = json.loads(path.read_text(encoding='utf-8'))['sources']
    for s in srcs:
        if s.get('country') in EXCLUDED_COUNTRIES:
            raise SystemExit(f"source {s['key']} is from an excluded country")
        if COURT_RE.search(s.get('name', '')):
            raise SystemExit(f"source {s['key']} looks like a court channel")
    return srcs


def fetched_ids(paths: Paths, key: str) -> set:
    out = set()
    for p in paths.raw.glob('*.json'):
        info = json.loads(p.read_text(encoding='utf-8'))
        if info.get('_gunj', {}).get('source_key') == key:
            out.add(info.get('id'))
    return out


def kept_counts(srcs: list[dict], paths: Paths, group: str) -> tuple[int, int]:
    by_key = {s['key']: s for s in srcs}
    n, chans = 0, set()
    for p in paths.raw.glob('*.json'):
        info = json.loads(p.read_text(encoding='utf-8'))
        g = info.get('_gunj', {})
        s = by_key.get(g.get('source_key'))
        if not s or s['group'] != group:
            continue
        if keep_reason(info, s) == '':
            n += 1
            chans.add(info.get('channel_id'))
    return n, len(chans)


def cmd_run(args, paths: Paths) -> int:
    srcs = load_sources(Path(args.sources))
    if args.group:
        srcs_run = [s for s in srcs if s['group'] == args.group]
    else:
        srcs_run = list(srcs)
    if args.only:
        keys = set(args.only.split(','))
        srcs_run = [s for s in srcs_run if s['key'] in keys]
    ydl = make_ydl(paths, 'run')
    if args.request_cap:
        ydl.request_cap = min(args.request_cap, REQUEST_CAP)
    ref_now = now_utc()
    print(f'start: {ydl.n_requests} requests already logged; cap {REQUEST_CAP}', flush=True)
    try:
        for s in srcs_run:
            if s.get('disabled'):
                continue
            if not args.no_stop:
                n, c = kept_counts(srcs, paths, s['group'])
                need_n, need_c = ((STOP_INDIA_N, STOP_INDIA_CHANNELS) if s['group'] == 'india'
                                  else (STOP_NEG_N, STOP_NEG_CHANNELS))
                if n >= need_n and c >= need_c:
                    print(f"stop rule met for {s['group']}: {n} candidates over {c} channels; "
                          f"skipping {s['key']}", flush=True)
                    append_jsonl(paths.events, {'t': now_utc().isoformat(), 'event': 'stop_rule',
                                                'group': s['group'], 'n': n, 'channels': c,
                                                'skipped': s['key']})
                    continue
            flat = flat_list(ydl, s, paths, ref_now)
            n_fetch = int(s.get('max_fetch', args.max_fetch))
            if args.fetch_cap:
                n_fetch = min(n_fetch, args.fetch_cap)
            done = fetched_ids(paths, s['key'])
            picks = pick_for_fetch(flat, n_fetch, done)
            print(f"[{s['key']}] {flat.get('channel')} listed={flat['n_entries']} "
                  f"stop={flat['stop'][:60]} cached={len(done)} new_picks={len(picks)} req={ydl.n_requests}", flush=True)
            kept = 0
            picks = [{'id': i} for i in sorted(done)] + picks  # cached; no new requests
            for e in picks:
                info = fetch_video(ydl, e['id'], s, paths)
                if info is not None and keep_reason(info, s) == '':
                    kept += 1
            print(f"[{s['key']}] kept={kept}/{len(picks)} req={ydl.n_requests}", flush=True)
    except Blocked as e:
        append_jsonl(paths.events, {'t': now_utc().isoformat(), 'event': 'blocked', 'msg': str(e)[:300],
                                    'requests': ydl.n_requests})
        print('BLOCKED, stopping:', e, flush=True)
        return 3
    except CapReached as e:
        append_jsonl(paths.events, {'t': now_utc().isoformat(), 'event': 'cap', 'msg': str(e)})
        print('CAP, stopping:', e, flush=True)
        return 4
    print('done; requests logged:', ydl.n_requests, flush=True)
    return 0


# --------------------------------------------------------------------- build

CSV_COLS = ['video_id', 'title', 'channel', 'channel_id', 'source_key', 'venue_type', 'country',
            'upload_date', 'release_timestamp', 'release_time_ist', 'timestamp', 'timestamp_ist',
            'start_ts_used', 'end_time_ist', 'duration_s', 'was_live', 'live_status', 'license', 'license_cc',
            'availability', 'webpage_url']


def row_for(info: dict, s: dict) -> dict:
    ts = info.get('release_timestamp') or info.get('timestamp')
    lic = info.get('license') or ''
    return {
        'video_id': info.get('id'), 'title': info.get('title'), 'channel': info.get('channel'),
        'channel_id': info.get('channel_id'), 'source_key': s['key'],
        'venue_type': s['venue_type'], 'country': s['country'],
        'upload_date': info.get('upload_date'),
        'release_timestamp': info.get('release_timestamp'),
        'release_time_ist': ist_iso(info.get('release_timestamp')),
        'timestamp': info.get('timestamp'), 'timestamp_ist': ist_iso(info.get('timestamp')),
        'start_ts_used': 'release_timestamp' if info.get('release_timestamp') else 'timestamp',
        'end_time_ist': ist_iso(ts + (info.get('duration') or 0)) if ts else None,
        'duration_s': info.get('duration'),
        'was_live': info.get('live_status') == 'was_live',
        'live_status': info.get('live_status'),
        'license': lic or 'standard (no Creative Commons licence shown)',
        'license_cc': 'creative commons' in lic.lower(),
        'availability': info.get('availability'),
        'webpage_url': info.get('webpage_url') or f"https://www.youtube.com/watch?v={info.get('id')}",
        '_ts': ts,
    }


def md_table(header: list[str], rows: list[list]) -> str:
    out = ['| ' + ' | '.join(header) + ' |', '|' + '|'.join('---' for _ in header) + '|']
    for r in rows:
        out.append('| ' + ' | '.join(str(x).replace('|', '/') for x in r) + ' |')
    return '\n'.join(out)


def cmd_build(args, paths: Paths) -> int:
    srcs = load_sources(Path(args.sources))
    by_key = {s['key']: s for s in srcs}
    rows = {'india': [], 'negative': []}
    rejects = {'india': collections.Counter(), 'negative': collections.Counter()}
    fetched = {'india': 0, 'negative': 0}
    src_fetched, src_kept = collections.Counter(), collections.Counter()
    for p in sorted(paths.raw.glob('*.json')):
        info = json.loads(p.read_text(encoding='utf-8'))
        s = by_key.get(info.get('_gunj', {}).get('source_key'))
        if not s:
            continue
        g = s['group']
        fetched[g] += 1
        src_fetched[s['key']] += 1
        if not keep_reason(info, s):
            src_kept[s['key']] += 1
        why = keep_reason(info, s)
        if why:
            rejects[g][why.split('=')[0] if why.startswith('live_status') else why] += 1
            continue
        rows[g].append(row_for(info, s))
    files = {'india': paths.data / 'candidates_india.csv',
             'negative': paths.data / 'candidates_negatives.csv'}
    for g, rs in rows.items():
        rs.sort(key=lambda r: (r['source_key'], r['_ts']))
        with files[g].open('w', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=CSV_COLS, extrasaction='ignore')
            w.writeheader()
            for r in rs:
                w.writerow(r)

    reqs = read_jsonl(paths.req_log)
    errs = read_jsonl(paths.err_log)
    events = read_jsonl(paths.events)
    phase_ct = collections.Counter(r['phase'].split(':')[0] for r in reqs)
    status_ct = collections.Counter(str(r.get('status')) for r in reqs)
    flats = {p.stem: json.loads(p.read_text(encoding='utf-8')) for p in paths.flat.glob('*.json')}
    raw_bytes = sum(p.stat().st_size for p in paths.raw.glob('*.json'))

    L = ['# Candidate metadata summary', '',
         'Generated by `scripts/harvest_metadata.py build` from `data/meta_raw/*.json`, '
         '`data/meta_flat/*.json` and `data/harvest_requests.jsonl`. '
         'Metadata only. No audio or video was downloaded.', '',
         f'Generated at {now_utc().isoformat(timespec="seconds")}.', '',
         '## Filters', '',
         '- Past live streams only (`live_status = was_live`).',
         '- Start time is `release_timestamp` (actual stream start); `timestamp` is used only if that is missing.',
         f'- Start inside the reference coverage, {COVER_START.date()} to {(COVER_END - dt.timedelta(days=1)).date()} IST.',
         f'- Duration at least {MIN_DURATION_S // 60} minutes.',
         '- Court channels and court proceedings excluded by channel choice and a title filter. '
         'Nepal and Bhutan sources excluded.',
         '- Per channel, the Live tab was listed newest first with approximate dates ("N years ago"), '
         'and up to a fixed number of long streams that looked 3 to 5 years old were picked, evenly spaced in time, '
         'for a per-video metadata fetch. So the lists are samples, not complete channel inventories.', '',
         '## Requests', '',
         f'- HTTP requests made: {len(reqs)} (cap {REQUEST_CAP}).',
         '- By phase: ' + ', '.join(f'{k} {v}' for k, v in sorted(phase_ct.items())) + '.',
         '- By status: ' + ', '.join(f'{k} {v}' for k, v in sorted(status_ct.items())) + '.',
         f'- First request {reqs[0]["t"] if reqs else "-"}, last {reqs[-1]["t"] if reqs else "-"} (UTC).',
         f'- Per-video fetches that failed: {len(errs)} '
         f'({", ".join(f"{k} {v}" for k, v in collections.Counter(e["kind"] for e in errs).items()) or "none"}).',
         f'- Block events (bot check / 429 / sign-in wall): {sum(1 for e in events if e["event"] == "blocked")}.',
         f'- Raw per-video JSON: {len(list(paths.raw.glob("*.json")))} files, {raw_bytes / 1e6:.2f} MB.', '']

    for g, title in (('india', 'Indian candidates'), ('negative', 'Negative candidates (foreign 50 Hz grids)')):
        rs = rows[g]
        L += [f'## {title}', '',
              f'- Per-video metadata fetched: {fetched[g]}. Kept: {len(rs)} '
              f'from {len({r["channel_id"] for r in rs})} channels.',
              '- Rejected: ' + (', '.join(f'{k} {v}' for k, v in rejects[g].most_common()) or 'none') + '.',
              f'- Total kept duration: {sum(r["duration_s"] or 0 for r in rs) / 3600:.1f} h.',
              f'- Whole stream (start + duration) inside the coverage: '
              f'{sum(1 for r in rs if r["_ts"] + (r["duration_s"] or 0) < COVER_END.timestamp())} of {len(rs)}.',
              f'- Creative Commons licence: {sum(1 for r in rs if r["license_cc"])} of {len(rs)}.',
              f'- Channels with at least 10 candidates: '
              f'{sum(1 for n in collections.Counter(r["channel_id"] for r in rs).values() if n >= 10)}.', '']
        ch = collections.Counter((r['source_key'], r['channel'], r['venue_type'], r['country']) for r in rs)
        dur = collections.defaultdict(float)
        for r in rs:
            dur[r['source_key']] += (r['duration_s'] or 0) / 3600
        L += ['### Per channel', '',
              md_table(['source', 'channel', 'venue', 'country', 'kept', 'hours'],
                       [[k[0], k[1], k[2], k[3], n, f'{dur[k[0]]:.1f}'] for k, n in
                        sorted(ch.items(), key=lambda x: (-x[1], x[0][0]))]), '']
        vt = collections.Counter(r['venue_type'] for r in rs)
        L += ['### Per venue type', '', md_table(['venue_type', 'kept'], sorted(vt.items())), '']
        lic = collections.Counter(r['license'] for r in rs)
        L += ['### Per licence', '', md_table(['licence', 'kept'], lic.most_common()), '']
        mon = collections.Counter(dt.datetime.fromtimestamp(r['_ts'], IST).strftime('%Y-%m') for r in rs)
        L += ['### Per month (IST, by stream start)', '', md_table(['month', 'kept'], sorted(mon.items())), '']
        if g == 'negative':
            cc = collections.Counter(r['country'] for r in rs)
            L += ['### Per country', '', md_table(['country', 'kept'], sorted(cc.items())), '']

    L += ['## Sources tried', '',
          'listed = entries seen on the Live tab or channel search; fetched = per-video metadata '
          'requests; kept = candidates. "not run" means the source was skipped (stop rule or budget).', '',
          md_table(['source', 'group', 'venue', 'country', 'channel (as listed)', 'tabs', 'listed', 'list stop',
                    'list req', 'fetched', 'kept', 'notes'],
                   [[s['key'], s['group'], s['venue_type'], s['country'],
                     (flats.get(s['key']) or {}).get('channel') or '-',
                     ', '.join(s.get('tabs', ['streams'])),
                     (flats.get(s['key']) or {}).get('n_entries', 'not run'),
                     str((flats.get(s['key']) or {}).get('stop', '-'))[:60],
                     (flats.get(s['key']) or {}).get('n_requests', '-'),
                     src_fetched[s['key']], src_kept[s['key']], s.get('notes', '')] for s in srcs]), '']
    stops = [e for e in events if e['event'] == 'stop_rule']
    L += ['## Run history', '']
    for g in ('india', 'negative'):
        first = next((e for e in stops if e.get('group') == g), None)
        if first:
            L.append(f"- Early-stop rule for {g} first met at {first['n']} candidates over "
                     f"{first['channels']} channels ({first['t'][:19]} UTC).")
    L.append('- After the India stop rule was met, a second pass capped at 10 fetches per channel '
             'added more channels, because the test split allows at most 10 streams per channel.')
    tried = collections.defaultdict(int)
    for s in srcs:
        if s['group'] == 'negative':
            tried[s['country']] += src_kept[s['key']]
    empty = sorted(c for c, n in tried.items() if n == 0)
    if empty:
        L.append(f"- Negative countries tried that gave no candidates: {', '.join(empty)} "
                 '(see Sources tried for why).')
    L.append('')
    all_rows = rows['india'] + rows['negative']
    n12 = sum(1 for r in all_rows if 11.8 * 3600 <= (r['duration_s'] or 0) <= 12.05 * 3600)
    L += ['## Caveats', '',
          "- A stream's audio may not come from the venue grid: generators, battery laptops, "
          'or remote speakers on video calls. Hum presence is measured later, not assumed.',
          "- Some channels' official status was not verified (see the notes column).",
          "- `release_timestamp` is YouTube's recorded stream start. The 5 Nov probe suggested a "
          'constant start latency; the calibration set will measure it.',
          f'- {n12} kept streams last between 11.8 and 12.05 h, which looks like a YouTube archive '
          'length cap. It is not verified here whether such an archive keeps the start or the end '
          'of the broadcast, so check its alignment before using `release_timestamp` as truth.',
          '- Channel-search tabs return results by relevance, so those sources are samples of '
          'what the search surfaced, not of all streams.', '']
    blocked = [e for e in events if e['event'] == 'blocked']
    if blocked:
        L += ['## Blocks', ''] + [f'- {e["t"]}: {e["msg"]}' for e in blocked] + ['']
    out = paths.results / 'metadata_summary.md'
    out.write_text('\n'.join(L), encoding='utf-8')
    print(f'india kept {len(rows["india"])}, negatives kept {len(rows["negative"])}, '
          f'requests {len(reqs)}; wrote {files["india"]}, {files["negative"]}, {out}')
    return 0


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--data-dir', default=str(REPO / 'data'))
    ap.add_argument('--results-dir', default=str(REPO / 'results'))
    sub = ap.add_subparsers(dest='cmd', required=True)
    a = sub.add_parser('search')
    a.add_argument('--query', action='append', required=True)
    a.add_argument('--n', type=int, default=5)
    a = sub.add_parser('run')
    a.add_argument('--sources', default=str(REPO / 'scripts' / 'harvest_sources.json'))
    a.add_argument('--group', choices=['india', 'negative'])
    a.add_argument('--only')
    a.add_argument('--max-fetch', type=int, default=30)
    a.add_argument('--no-stop', action='store_true', help='ignore the early-stop rule')
    a.add_argument('--fetch-cap', type=int, help='cap per-source fetches (for a channel-diversity pass)')
    a.add_argument('--request-cap', type=int, help=f'lower total request cap (max {REQUEST_CAP})')
    a = sub.add_parser('build')
    a.add_argument('--sources', default=str(REPO / 'scripts' / 'harvest_sources.json'))
    args = ap.parse_args(argv)
    paths = Paths(Path(args.data_dir), Path(args.results_dir))
    return {'search': cmd_search, 'run': cmd_run, 'build': cmd_build}[args.cmd](args, paths)


if __name__ == '__main__':
    sys.exit(main())
