# M4 at the measured noise level

Written by `scripts/make_figures.py`. Inputs: `results/test_results.csv`, `results/gate_frozen.json`, `results/search_cache/test/`, `traces/test/`, the built reference series, and `results/uniqueness_table.csv` (real reference excerpts with SYNTHETIC noise; no audio). PROTOCOL.md section 11, M4: "The 'measured SNR' is the equivalent trace noise of the Test streams that lock, computed as in `scripts/probe_noise.py`. The curve is read at that noise level by interpolating between the pre-set levels." The simulation and its noise levels were set before any Test audio existed; the Test results only set the level at which the table is read.

## What was measured

- **Streams that lock:** the 38 Test streams that the frozen gate counts as locks (`results/gate_frozen.json`): extractor v2, top-1 candidate in the full span or ±90 days within 30 s of T* for at least one excerpt length, with confidence at or above tau of that cell (PROTOCOL.md section 15). The same list recomputed from `results/test_results.csv`: identical.
- **Excerpts:** every excerpt length at which such a stream locks (88 excerpts; the same excerpt in both scopes is counted once).
- **Noise per excerpt, as in `scripts/probe_noise.py`:** sigma = s * sqrt(1/r^2 - 1), where s is the standard deviation of the gap-filled reference over the window the excerpt matched and r is the correlation at the lock.
- **One value per stream:** the median over its locking excerpts.
- **Reading A (r as scored):** r is the r1 that the frozen evaluation recorded. For v2 this is the weighted correlation of `gunj/match_v2.py`, which gives little weight to frames with weak hum.
- **Reading B (unweighted r):** r is the plain correlation of the same trace with the same window, every frame weighted equally. The M4 simulation adds noise of one size to every second and uses the unweighted matcher, so this is the noise that simulation's matcher would see in these traces.

## Measured noise (mHz)

| reading | streams | min | Q1 | median | Q3 | max |
|---|---|---|---|---|---|---|
| A: r as scored (v2, weighted) | 38 | 1.5 | 4.1 | 9.4 | 16.8 | 46.2 |
| B: same traces, unweighted r | 38 | 1.9 | 12.9 | 37.4 | 71.2 | 211.8 |

- Reading A with s also weighted by the frame weights: median 8.4 mHz (Q1 3.7, Q3 16.9).
- For comparison, the 5 Nov feasibility probe (one stream, an earlier extractor): median 38.6 mHz over 11 excerpts (`results/probe_noise.json`).
- Median over locking excerpts by excerpt length, reading A / reading B: 10 min 4.1 / 17.1 mHz (n = 21); 20 min 5.5 / 13.5 mHz (n = 22); 40 min 14.3 / 47.6 mHz (n = 23); 60 min 13.3 / 47.9 mHz (n = 22). The cells have different tau, so different streams lock at each length.

## Clip length needed for 90% unique locks at the measured level

The hit rates of `results/uniqueness_table.csv` are interpolated linearly between the two pre-set SYNTHETIC noise levels that bracket the measured median (levels in mHz: 0, 10, 20, 40, 60; the 0 mHz control is shared by both noise kinds). The entry is the shortest tested length whose interpolated rate is at least 90%. Only these lengths were tested (min): 5, 10, 20, 40, 60. The true length therefore lies between the previous tested length and the one shown. `white` noise is independent each second; `hann16` is smoothed with a 16 s Hann window like real extractor error and is the closer stand-in (`scripts/uniqueness.py`).

| reading | level (mHz) | SYNTHETIC noise | full span | ±90 days | ±1 day |
|---|---|---|---|---|---|
| A, median | 9.4 | hann16 | 10 min (91.2%; 5 min is below 90%) | 10 min (96.1%; 5 min is below 90%) | 10 min (99.6%; 5 min is below 90%) |
| A, median | 9.4 | white | 5 min (99.0%; shortest tested) | 5 min (99.3%; shortest tested) | 5 min (100.0%; shortest tested) |
| B, median | 37.4 | hann16 | 40 min (94.6%; 20 min is below 90%) | 40 min (97.4%; 20 min is below 90%) | 40 min (99.4%; 20 min is below 90%) |
| B, median | 37.4 | white | 20 min (100.0%; 10 min is below 90%) | 20 min (100.0%; 10 min is below 90%) | 10 min (97.1%; 5 min is below 90%) |

Interpolated share of unique locks by excerpt length, `hann16` noise:

| reading | scope | 5 min | 10 min | 20 min | 40 min | 60 min |
|---|---|---|---|---|---|---|
| A, median | full span | 13.3% | 91.2% | 100.0% | 100.0% | 100.0% |
| A, median | ±90 days | 25.6% | 96.1% | 100.0% | 100.0% | 100.0% |
| A, median | ±1 day | 68.1% | 99.6% | 100.0% | 100.0% | 100.0% |
| B, median | full span | 0.0% | 2.7% | 35.7% | 94.6% | 100.0% |
| B, median | ±90 days | 0.1% | 5.1% | 48.2% | 97.4% | 99.9% |
| B, median | ±1 day | 2.0% | 25.6% | 83.8% | 99.4% | 100.0% |

The same reading at the quartiles of each distribution (`hann16`, shortest tested length reaching 90%):

| reading | level (mHz) | full span | ±90 days | ±1 day |
|---|---|---|---|---|
| A, Q1 | 4.1 | 10 min (96.2%; 5 min is below 90%) | 10 min (98.3%; 5 min is below 90%) | 10 min (99.8%; 5 min is below 90%) |
| A, Q3 | 16.8 | 20 min (96.2%; 10 min is below 90%) | 20 min (98.9%; 10 min is below 90%) | 20 min (99.9%; 10 min is below 90%) |
| B, Q1 | 12.9 | 20 min (98.4%; 10 min is below 90%) | 20 min (99.5%; 10 min is below 90%) | 10 min (92.7%; 5 min is below 90%) |
| B, Q3 | 71.2 | not read: the level is outside the pre-set levels | not read: the level is outside the pre-set levels | not read: the level is outside the pre-set levels |

## Other readings of "streams that lock"

| streams | n | reading A median (mHz) | reading B median (mHz) |
|---|---|---|---|
| gate locks (used above) | 38 | 9.4 | 37.4 |
| confident hits: verdict "match" and within 30 s, full span or ±90 days | 30 | 8.4 | 19.0 |
| every stream with a top-1 within 30 s, full span or ±90 days, whatever tau | 48 | 11.2 | 45.7 |
| locks in the primary cell only (60 min, full span) | 22 | 13.3 | 47.9 |

## Ambiguities and limits

- PROTOCOL.md does not say which correlation to use for an extractor with a weighted matcher. `scripts/probe_noise.py` took the r that its matcher printed, which corresponds to reading A. Reading B is given because the simulation it is compared with is unweighted. At the median, reading B is 4.0 times reading A. The clip length read from the table depends on the reading, so both are reported.
- Neither reading fits the simulation exactly. Reading A can flatter v2: a weighted correlation rests on fewer effective frames than the excerpt has, and the simulation does not model that. Reading B can understate v2: it counts noisy frames in full, and v2's matcher gives them little weight.
- "Streams that lock" could also mean confident hits or every top-1 hit; the table above gives their medians.
- The level is measured only on streams that lock, so it describes recordings with usable hum. It says nothing about the Test streams that do not lock.
- sigma is an equivalent white noise. It lumps together extraction noise, smoothing and any error in the alignment, and real trace error is not the same size in every second.
- The uniqueness table uses SYNTHETIC noise on real reference excerpts. The interpolation is linear in the noise level between two tested levels; the simulation was not re-run at the measured level.
- Figure: `results/figures/m4_measured_noise.png`.
