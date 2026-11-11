# Figures

Written by `scripts/make_figures.py` from the files named below (scoring run of 2026-11-11T19:16:31+05:30, `results/evaluation.json`). Every number drawn is in `results/figures/figures.json`. PNGs are 1800 px wide on a white background. No audio or video is used or shown.

Colours: amber is Gunj's primary extractor (v2); green marks top-1 candidates within 30 s of T* (hits) and nothing else; grey marks baselines and reference data. In the breakdown figure, darker steps of amber mark larger distances from T*. T* = release time + L + D (PROTOCOL.md section 6).

## `m2_signed_error_v2.png`

M2, extractor v2 (primary): signed error e of the top-1 candidate for every excerpt length (rows) and search scope (columns). The axis is linear inside the ±30 s hit band and logarithmic outside it, and every bin has the same drawn width. Green bars are hits. Each panel title gives N (Test streams with a trace at that length) and the number of hits; 60 min, full span: N = 90, 25 hits.

Source: `results/test_results.csv` (columns `error_s`, `hit_30s`).

## `m3_reliability_v2_60min_full.png`

M3 reliability plot for the primary cell, as PROTOCOL.md section 11 defines it: Test top-1 hit rate by confidence bin, with counts (hits / excerpts) and Wilson 95% intervals, and below it every excerpt's confidence on the same axis: Test hits, Test misses, the real report-half negatives (filled grey) and the SYNTHETIC report-half negatives (open grey). The dashed line is tau of the cell. Negatives drawn: 16 real and 20 SYNTHETIC.

Source: `results/test_results.csv` (`confidence`, `hit_30s`) and `results/false_matches.json` (report-half negatives, `tau`).

## `confident_verdict_breakdown_v2.png`

Top: excerpts with verdict "match" for every cell of extractor v2, split by the distance of the top-1 candidate from T*. Bottom: the same split for every excerpt, whatever the verdict. Only "within 30 s" is a hit. In the primary cell (60 min, full span) 23 excerpts have a verdict "match": 14 within 30 s, 6 from 30 to 120 s, 1 from 120 to 600 s and 2 beyond 600 s.

Source: `results/test_results.csv` (columns `verdict`, `hit_30s`, `hit_120s`, `hit_600s`); table in `results/error_breakdown.md`.

## `m4_measured_noise.png`

M4 at the measured noise level. Top: equivalent trace noise of the 38 Test streams that lock, one dot per stream, under reading B (unweighted r, the computation of scripts/probe_noise.py and the pre-registered reading; median 37.4 mHz) and reading A (EXPLORATORY (post hoc): r as scored, median 9.4 mHz), against the pre-set levels of the M4 simulation. Bottom: the uniqueness table (real reference excerpts with SYNTHETIC hann16 noise) read at each median by linear interpolation.

Source: `results/m4_measured.json`, from `results/test_results.csv`, `results/gate_frozen.json`, `results/search_cache/test`, `traces/test`, the built reference and `results/uniqueness_table.csv`.

## `m3_reliability_v2_all_cells.png`

The same reliability plot for all 12 cells of extractor v2 (4 excerpt lengths by 3 scopes). The two rows of dots under each panel are the real (filled) and SYNTHETIC (open) report-half negatives of that cell.

Source: `results/test_results.csv` and `results/false_matches.json`.

## `m2_signed_error_v1.png`

M2, extractor v1 (baseline): signed error e of the top-1 candidate for every excerpt length (rows) and search scope (columns). The axis is linear inside the ±30 s hit band and logarithmic outside it, and every bin has the same drawn width. Green bars are hits. Each panel title gives N (Test streams with a trace at that length) and the number of hits; 60 min, full span: N = 90, 15 hits.

Source: `results/test_results.csv` (columns `error_s`, `hit_30s`).

## `m2_signed_error_naive.png`

M2, extractor naive (baseline): signed error e of the top-1 candidate for every excerpt length (rows) and search scope (columns). The axis is linear inside the ±30 s hit band and logarithmic outside it, and every bin has the same drawn width. Green bars are hits. Each panel title gives N (Test streams with a trace at that length) and the number of hits; 60 min, full span: N = 90, 12 hits.

Source: `results/test_results.csv` (columns `error_s`, `hit_30s`).

## `exploratory_offset_verdicts_v2.png`

EXPLORATORY (post hoc, not pre-registered). Confident verdicts of extractor v2 in the full span: those within 30 s of T* (green) beside those 30 to 600 s from it (amber), by the stream's drift correction abs(D), label stability, reference source file, excerpt length and venue type. Counts and medians only.

Source: `results/test_results.csv` and `data/splits.csv` (`ref_src_file`); tables in `results/exploratory_offset_verdicts.md`.

## M3 reliability table, primary cell (v2, 60 min, full span)

| confidence bin | Test excerpts | top-1 within 30 s | hit rate (Wilson 95%) |
|---|---|---|---|
| [0, 0.025) | 46 | 0 | 0/46 (0.0%, 95% CI 0.0 to 7.7%) |
| [0.025, 0.05) | 9 | 1 | 1/9 (11.1%, 95% CI 2.0 to 43.5%) |
| [0.05, 0.1) | 4 | 2 | 2/4 (50.0%, 95% CI 15.0 to 85.0%) |
| [0.1, 0.2) | 8 | 7 | 7/8 (87.5%, 95% CI 52.9 to 97.8%) |
| [0.2, 0.4) | 4 | 3 | 3/4 (75.0%, 95% CI 30.1 to 95.4%) |
| [0.4, inf) | 18 | 12 | 12/18 (66.7%, 95% CI 43.7 to 83.7%) |

Test excerpts without a confidence (the search could not run): 1 of 90. tau of the cell: 0.0834.

| report-half negatives in this cell | n | largest confidence | at or above tau | at or above tau with the hum flag |
|---|---|---|---|---|
| real foreign 50 Hz streams | 16 | 0.1339 | 1 | 1 |
| SYNTHETIC noise and hum | 20 | 0.0394 | 0 | 0 |

Report-half negatives searched by `scripts/evaluate.py`: 24 real and 20 SYNTHETIC (`results/evaluation.json`, `m3_counts`). A negative has a row in a cell only if its stream is long enough for that excerpt length.

## M3 reliability table, every cell of v2

Each bin shows hits / Test excerpts. The last two columns give the report-half negatives of the cell as n (number at or above tau). The false-match rates with their intervals are in `results/false_matches.md`, written by `scripts/evaluate.py`.

| excerpt | scope | tau | [0, 0.025) | [0.025, 0.05) | [0.05, 0.1) | [0.1, 0.2) | [0.2, 0.4) | [0.4, inf) | no confidence | real negatives | SYNTHETIC negatives |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 10 min | full span | 0.1645 | 1/70 | 1/24 | 2/4 | 6/6 | 5/5 | 13/15 | 2 | 24 (1) | 20 (0) |
| 10 min | ±90 days | 0.1645 | 1/70 | 1/22 | 1/5 | 6/6 | 6/6 | 13/15 | 2 | 24 (1) | 20 (0) |
| 10 min | ±1 day | 0.4292 | 3/62 | 2/19 | 3/11 | 4/7 | 6/6 | 17/19 | 2 | 24 (0) | 20 (0) |
| 20 min | full span | 0.2233 | 2/74 | 1/16 | 1/4 | 6/7 | 6/7 | 15/17 | 1 | 24 (0) | 20 (0) |
| 20 min | ±90 days | 0.2233 | 1/76 | 0/8 | 3/10 | 4/5 | 7/8 | 16/18 | 1 | 24 (0) | 20 (0) |
| 20 min | ±1 day | 0.3279 | 0/63 | 4/15 | 5/10 | 3/6 | 8/8 | 20/23 | 1 | 24 (0) | 20 (0) |
| 40 min | full span | 0.0622 | 2/67 | 3/6 | 1/3 | 4/5 | 5/7 | 11/15 | 1 | 18 (1) | 20 (2) |
| 40 min | ±90 days | 0.0637 | 1/63 | 1/6 | 3/5 | 4/5 | 6/9 | 11/15 | 1 | 18 (0) | 20 (1) |
| 40 min | ±1 day | 0.3763 | 3/53 | 1/11 | 2/6 | 2/4 | 6/7 | 16/22 | 1 | 18 (0) | 20 (0) |
| 60 min | full span | 0.0834 | 0/46 | 1/9 | 2/4 | 7/8 | 3/4 | 12/18 | 1 | 16 (1) | 20 (0) |
| 60 min | ±90 days | 0.1412 | 0/47 | 0/5 | 3/5 | 4/7 | 6/7 | 12/18 | 1 | 16 (0) | 20 (0) |
| 60 min | ±1 day | 0.2334 | 1/40 | 1/9 | 0/3 | 3/6 | 7/8 | 16/23 | 1 | 16 (0) | 20 (1) |
