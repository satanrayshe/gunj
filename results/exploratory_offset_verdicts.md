# EXPLORATORY (post hoc): confident verdicts 30 to 600 s from T*

**This analysis was not pre-registered.** It was written after the Test results were known, to describe the confident verdicts that are more than 30 s but at most 600 s from T*. It is description only: counts and medians, no significance test, no new threshold, and no change to any result. A verdict more than 30 s from T* stays a confident wrong verdict.

Written by `scripts/make_figures.py` from `results/test_results.csv` (scoring run of 2026-11-11T19:16:31+05:30) and `data/splits.csv` (`ref_src_file`). Extractor v2, full span, excerpts with verdict "match". One verdict is one Test stream at one excerpt length, so a stream can appear at up to 4 lengths. Figure: `results/figures/exploratory_offset_verdicts_v2.png`.

## Primary cell (v2, 60-min excerpts, full span), one verdict per stream

| group | verdicts | streams | median abs(D) in s (range) | median abs(e) in s | label-stable / not |
|---|---|---|---|---|---|
| within 30 s (hit) | 14 | 14 | 60.5 (0 to 293) | 8 | 6 / 8 |
| 30 to 600 s from T* | 7 | 7 | 67 (8 to 200) | 44 | 2 / 5 |
| beyond 600 s | 2 | 2 | 8 (8 to 8) | 1,440,909 | 1 / 1 |

Reference source file, 60-min excerpts (verdicts):

| group | I | II | III | IV | V |
|---|---|---|---|---|---|
| within 30 s (hit) | 1 | 2 | 3 | 3 | 5 |
| 30 to 600 s from T* | 0 | 1 | 2 | 2 | 2 |
| beyond 600 s | 0 | 1 | 0 | 0 | 1 |

Venue type, 60-min excerpts (verdicts):

| group | conference | government event | lecture | legislature | religious |
|---|---|---|---|---|---|
| within 30 s (hit) | 0 | 0 | 1 | 10 | 3 |
| 30 to 600 s from T* | 0 | 0 | 2 | 2 | 3 |
| beyond 600 s | 0 | 0 | 0 | 1 | 1 |

## By excerpt length (full span)

| excerpt | group | verdicts | streams | median abs(D) in s (range) | median abs(e) in s | label-stable / not |
|---|---|---|---|---|---|---|
| 10 min | within 30 s (hit) | 17 | 17 | 62 (4 to 293) | 10 | 5 / 12 |
| 10 min | 30 to 600 s from T* | 2 | 2 | 80.5 (51 to 110) | 316 | 1 / 1 |
| 10 min | beyond 600 s | 0 | 0 | n/a | n/a | 0 / 0 |
| 20 min | within 30 s (hit) | 19 | 19 | 67 (4 to 293) | 10 | 3 / 16 |
| 20 min | 30 to 600 s from T* | 2 | 2 | 29.5 (8 to 51) | 301 | 2 / 0 |
| 20 min | beyond 600 s | 0 | 0 | n/a | n/a | 0 / 0 |
| 40 min | within 30 s (hit) | 16 | 16 | 60.5 (0 to 293) | 8.5 | 6 / 10 |
| 40 min | 30 to 600 s from T* | 7 | 7 | 67 (8 to 200) | 32 | 3 / 4 |
| 40 min | beyond 600 s | 1 | 1 | 8 (8 to 8) | 5,299 | 0 / 1 |
| 60 min | within 30 s (hit) | 14 | 14 | 60.5 (0 to 293) | 8 | 6 / 8 |
| 60 min | 30 to 600 s from T* | 7 | 7 | 67 (8 to 200) | 44 | 2 / 5 |
| 60 min | beyond 600 s | 2 | 2 | 8 (8 to 8) | 1,440,909 | 1 / 1 |

tau differs by cell (PROTOCOL.md section 10), so the number of confident verdicts at each length reflects the threshold of that cell as well as the excerpt.

## Distinct streams (full span, any excerpt length)

| streams | n | median abs(D) in s (range) | label-stable / not |
|---|---|---|---|
| at least one confident verdict 30 to 600 s from T* | 10 | 77 (8 to 200) | 3 / 7 |
| every confident verdict within 30 s | 23 | 62 (0 to 293) | 7 / 16 |

## The streams with a confident verdict 30 to 600 s from T*

Signed error e of the top-1 candidate in seconds at each excerpt length (full span). `match` marks a confident verdict; a value without it had no verdict at that length; `none` means the stream has no excerpt of that length.

| stream | venue | file | D (s) | label-stable | 10 min | 20 min | 40 min | 60 min |
|---|---|---|---|---|---|---|---|---|
| 3j8-qlHHnIE | religious | IV | 67 | no | +18 match | +18 match | +18 match | +44 match |
| 8gsXSa4UzaQ | legislature | III | 87 | no | +12538150 | +26 match | +31 match | +33 match |
| BsDB4Y5hLeU | lecture | III | 109 | no | -32 | -32 | -32 match | -32 match |
| FU27mso1GaE | religious | V | -8 | yes | -28 match | -30 match | -31 match | -32 match |
| HDfWJP4QYo0 | religious | V | -8 | yes | -30 match | -31 match | -32 match | -2876519 match |
| YoJlliek2Sg | religious | IV | 67 | no | +18 match | +18 match | +46 match | +46 match |
| fPDBSEhmgnk | legislature | V | 200 | no | -107 | -105 | -104 match | -104 |
| oQc1M0eaXa4 | lecture | II | 51 | yes | +572 match | +571 match | +571 match | +571 match |
| tqMHkxbSMLs | legislature | V | 200 | no | -60563752 | -17784788 | -104 | -102 match |
| u4-kZVwUUAg | legislature | V | 110 | no | -60 match | -60 | -69637823 | -68802789 |

5 of these 10 streams have a top-1 candidate within 30 s of T* at another excerpt length.

## Limits

- The groups are small, and verdicts of one stream at several lengths are not independent.
- T* rests on YouTube's release time, one latency L and the drift estimate D. An offset of tens of seconds can come from any of them or from the lock, and this table cannot tell which.
- These numbers describe the pre-registered results. They do not support any claim about a rule that was not pre-registered.
