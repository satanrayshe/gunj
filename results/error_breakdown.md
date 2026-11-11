# Confident verdicts and top-1 candidates by distance from T*

Written by `scripts/make_figures.py` from `results/test_results.csv` (scoring run of 2026-11-11T19:16:31+05:30, `results/evaluation.json`). Every number is counted by the script from the pre-registered columns `verdict`, `hit_30s`, `hit_120s` and `hit_600s` (PROTOCOL.md section 9). The hit rule stays as pre-registered: a hit is a top-1 candidate within 30 s of T*. The 120 s and 600 s classes are the pre-registered coarse hits, reported for the label problem and never substituted for the 30 s rule.

- Rows are Test streams with a trace at that excerpt length (the M2 denominators). A verdict "match" needs the hum flag and confidence at or above tau of the cell.
- The distance classes do not overlap: within 30 s; more than 30 s and up to 120 s; more than 120 s and up to 600 s; beyond 600 s.
- For all excerpts, the three shares are cumulative (within 30 s, within 120 s, within 600 s): the top-1 hit rate and the pre-registered coarse hit rates, with Wilson 95% intervals (PROTOCOL.md sections 9 and 13). For the excerpts with verdict "match" only the counts are given: PROTOCOL.md section 11 defines no rate over confident verdicts, so a share of them would be a new, post hoc measure.
- Every verdict "match" more than 30 s from T* is a confident wrong verdict (PROTOCOL.md section 9), whatever its class.
- Check: for all 36 cells the within-30 s counts equal `top1_hit`, `confident_hit` and `confident_wrong` in `results/evaluation.json` (`m2`), and 0 rows have hit columns that disagree with `error_s`.
- Figure: `results/figures/confident_verdict_breakdown_v2.png` (extractor v2).

## Primary cell (v2, 60-min excerpts, full span)

| excerpts | n | within 30 s | 30 to 120 s | 120 to 600 s | beyond 600 s | no top-1 candidate |
|---|---|---|---|---|---|---|
| verdict "match" | 23 | 14 | 6 | 1 | 2 | 0 |
| all, whatever the verdict | 90 | 25 | 7 | 2 | 55 | 1 |

- Verdict "match", as counts: 14 within 30 s, 20 within 120 s and 21 within 600 s of 23; 9 of the 23 are confident wrong.
- All excerpts: within 30 s 25/90 (27.8%, 95% CI 19.6 to 37.8%); within 120 s 32/90 (35.6%, 95% CI 26.4 to 45.8%); within 600 s 34/90 (37.8%, 95% CI 28.5 to 48.1%).

## Extractor v2 (primary): excerpts with verdict "match" (counts)

| excerpt | scope | n | within 30 s | 30 to 120 s | 120 to 600 s | beyond 600 s |
|---|---|---|---|---|---|---|
| 10 min | full span | 19 | 17 | 1 | 1 | 0 |
| 10 min | ±90 days | 22 | 20 | 1 | 1 | 0 |
| 10 min | ±1 day | 16 | 14 | 1 | 1 | 0 |
| 20 min | full span | 21 | 19 | 1 | 1 | 0 |
| 20 min | ±90 days | 23 | 21 | 1 | 1 | 0 |
| 20 min | ±1 day | 23 | 21 | 1 | 1 | 0 |
| 40 min | full span | 24 | 16 | 6 | 1 | 1 |
| 40 min | ±90 days | 26 | 17 | 7 | 1 | 1 |
| 40 min | ±1 day | 23 | 16 | 5 | 1 | 1 |
| 60 min | full span | 23 | 14 | 6 | 1 | 2 |
| 60 min | ±90 days | 22 | 14 | 5 | 1 | 2 |
| 60 min | ±1 day | 22 | 15 | 5 | 1 | 1 |

## Extractor v2 (primary): top-1 candidate of every excerpt, whatever the verdict

| excerpt | scope | n | within 30 s | 30 to 120 s | 120 to 600 s | beyond 600 s | no top-1 candidate | within 30 s, share | within 120 s, share | within 600 s, share |
|---|---|---|---|---|---|---|---|---|---|---|
| 10 min | full span | 126 | 28 | 3 | 1 | 92 | 2 | 28/126 (22.2%, 95% CI 15.8 to 30.2%) | 31/126 (24.6%, 95% CI 17.9 to 32.8%) | 32/126 (25.4%, 95% CI 18.6 to 33.6%) |
| 10 min | ±90 days | 126 | 28 | 3 | 1 | 92 | 2 | 28/126 (22.2%, 95% CI 15.8 to 30.2%) | 31/126 (24.6%, 95% CI 17.9 to 32.8%) | 32/126 (25.4%, 95% CI 18.6 to 33.6%) |
| 10 min | ±1 day | 126 | 35 | 3 | 2 | 84 | 2 | 35/126 (27.8%, 95% CI 20.7 to 36.2%) | 38/126 (30.2%, 95% CI 22.8 to 38.7%) | 40/126 (31.7%, 95% CI 24.3 to 40.3%) |
| 20 min | full span | 126 | 31 | 4 | 1 | 89 | 1 | 31/126 (24.6%, 95% CI 17.9 to 32.8%) | 35/126 (27.8%, 95% CI 20.7 to 36.2%) | 36/126 (28.6%, 95% CI 21.4 to 37.0%) |
| 20 min | ±90 days | 126 | 31 | 4 | 1 | 89 | 1 | 31/126 (24.6%, 95% CI 17.9 to 32.8%) | 35/126 (27.8%, 95% CI 20.7 to 36.2%) | 36/126 (28.6%, 95% CI 21.4 to 37.0%) |
| 20 min | ±1 day | 126 | 40 | 4 | 3 | 78 | 1 | 40/126 (31.7%, 95% CI 24.3 to 40.3%) | 44/126 (34.9%, 95% CI 27.2 to 43.6%) | 47/126 (37.3%, 95% CI 29.4 to 46.0%) |
| 40 min | full span | 104 | 26 | 7 | 1 | 69 | 1 | 26/104 (25.0%, 95% CI 17.7 to 34.1%) | 33/104 (31.7%, 95% CI 23.6 to 41.2%) | 34/104 (32.7%, 95% CI 24.4 to 42.2%) |
| 40 min | ±90 days | 104 | 26 | 7 | 1 | 69 | 1 | 26/104 (25.0%, 95% CI 17.7 to 34.1%) | 33/104 (31.7%, 95% CI 23.6 to 41.2%) | 34/104 (32.7%, 95% CI 24.4 to 42.2%) |
| 40 min | ±1 day | 104 | 30 | 7 | 4 | 62 | 1 | 30/104 (28.8%, 95% CI 21.0 to 38.2%) | 37/104 (35.6%, 95% CI 27.0 to 45.1%) | 41/104 (39.4%, 95% CI 30.6 to 49.0%) |
| 60 min | full span | 90 | 25 | 7 | 2 | 55 | 1 | 25/90 (27.8%, 95% CI 19.6 to 37.8%) | 32/90 (35.6%, 95% CI 26.4 to 45.8%) | 34/90 (37.8%, 95% CI 28.5 to 48.1%) |
| 60 min | ±90 days | 90 | 25 | 7 | 2 | 55 | 1 | 25/90 (27.8%, 95% CI 19.6 to 37.8%) | 32/90 (35.6%, 95% CI 26.4 to 45.8%) | 34/90 (37.8%, 95% CI 28.5 to 48.1%) |
| 60 min | ±1 day | 90 | 28 | 7 | 4 | 50 | 1 | 28/90 (31.1%, 95% CI 22.5 to 41.3%) | 35/90 (38.9%, 95% CI 29.5 to 49.2%) | 39/90 (43.3%, 95% CI 33.6 to 53.6%) |

## Extractor v1 (baseline): excerpts with verdict "match" (counts)

| excerpt | scope | n | within 30 s | 30 to 120 s | 120 to 600 s | beyond 600 s |
|---|---|---|---|---|---|---|
| 10 min | full span | 6 | 6 | 0 | 0 | 0 |
| 10 min | ±90 days | 10 | 9 | 0 | 1 | 0 |
| 10 min | ±1 day | 12 | 10 | 0 | 1 | 1 |
| 20 min | full span | 15 | 11 | 2 | 0 | 2 |
| 20 min | ±90 days | 15 | 12 | 2 | 1 | 0 |
| 20 min | ±1 day | 16 | 13 | 2 | 1 | 0 |
| 40 min | full span | 18 | 11 | 4 | 1 | 2 |
| 40 min | ±90 days | 16 | 10 | 5 | 1 | 0 |
| 40 min | ±1 day | 17 | 11 | 5 | 1 | 0 |
| 60 min | full span | 15 | 11 | 2 | 1 | 1 |
| 60 min | ±90 days | 15 | 11 | 2 | 1 | 1 |
| 60 min | ±1 day | 14 | 11 | 2 | 1 | 0 |

## Extractor v1 (baseline): top-1 candidate of every excerpt, whatever the verdict

| excerpt | scope | n | within 30 s | 30 to 120 s | 120 to 600 s | beyond 600 s | no top-1 candidate | within 30 s, share | within 120 s, share | within 600 s, share |
|---|---|---|---|---|---|---|---|---|---|---|
| 10 min | full span | 126 | 10 | 0 | 1 | 113 | 2 | 10/126 (7.9%, 95% CI 4.4 to 14.0%) | 10/126 (7.9%, 95% CI 4.4 to 14.0%) | 11/126 (8.7%, 95% CI 4.9 to 15.0%) |
| 10 min | ±90 days | 126 | 12 | 0 | 1 | 111 | 2 | 12/126 (9.5%, 95% CI 5.5 to 15.9%) | 12/126 (9.5%, 95% CI 5.5 to 15.9%) | 13/126 (10.3%, 95% CI 6.1 to 16.9%) |
| 10 min | ±1 day | 126 | 19 | 1 | 2 | 102 | 2 | 19/126 (15.1%, 95% CI 9.9 to 22.4%) | 20/126 (15.9%, 95% CI 10.5 to 23.2%) | 22/126 (17.5%, 95% CI 11.8 to 25.0%) |
| 20 min | full span | 126 | 15 | 2 | 1 | 107 | 1 | 15/126 (11.9%, 95% CI 7.3 to 18.7%) | 17/126 (13.5%, 95% CI 8.6 to 20.5%) | 18/126 (14.3%, 95% CI 9.2 to 21.5%) |
| 20 min | ±90 days | 126 | 17 | 3 | 1 | 104 | 1 | 17/126 (13.5%, 95% CI 8.6 to 20.5%) | 20/126 (15.9%, 95% CI 10.5 to 23.2%) | 21/126 (16.7%, 95% CI 11.2 to 24.1%) |
| 20 min | ±1 day | 126 | 25 | 3 | 3 | 94 | 1 | 25/126 (19.8%, 95% CI 13.8 to 27.7%) | 28/126 (22.2%, 95% CI 15.8 to 30.2%) | 31/126 (24.6%, 95% CI 17.9 to 32.8%) |
| 40 min | full span | 104 | 14 | 4 | 1 | 84 | 1 | 14/104 (13.5%, 95% CI 8.2 to 21.3%) | 18/104 (17.3%, 95% CI 11.2 to 25.7%) | 19/104 (18.3%, 95% CI 12.0 to 26.8%) |
| 40 min | ±90 days | 104 | 14 | 6 | 1 | 82 | 1 | 14/104 (13.5%, 95% CI 8.2 to 21.3%) | 20/104 (19.2%, 95% CI 12.8 to 27.8%) | 21/104 (20.2%, 95% CI 13.6 to 28.9%) |
| 40 min | ±1 day | 104 | 20 | 7 | 1 | 75 | 1 | 20/104 (19.2%, 95% CI 12.8 to 27.8%) | 27/104 (26.0%, 95% CI 18.5 to 35.1%) | 28/104 (26.9%, 95% CI 19.3 to 36.2%) |
| 60 min | full span | 90 | 15 | 3 | 1 | 70 | 1 | 15/90 (16.7%, 95% CI 10.4 to 25.7%) | 18/90 (20.0%, 95% CI 13.0 to 29.4%) | 19/90 (21.1%, 95% CI 14.0 to 30.6%) |
| 60 min | ±90 days | 90 | 17 | 3 | 1 | 68 | 1 | 17/90 (18.9%, 95% CI 12.1 to 28.2%) | 20/90 (22.2%, 95% CI 14.9 to 31.8%) | 21/90 (23.3%, 95% CI 15.8 to 33.1%) |
| 60 min | ±1 day | 90 | 25 | 4 | 2 | 58 | 1 | 25/90 (27.8%, 95% CI 19.6 to 37.8%) | 29/90 (32.2%, 95% CI 23.5 to 42.4%) | 31/90 (34.4%, 95% CI 25.4 to 44.7%) |

## Extractor naive (baseline): excerpts with verdict "match" (counts)

| excerpt | scope | n | within 30 s | 30 to 120 s | 120 to 600 s | beyond 600 s |
|---|---|---|---|---|---|---|
| 10 min | full span | 3 | 0 | 0 | 0 | 3 |
| 10 min | ±90 days | 1 | 0 | 0 | 0 | 1 |
| 10 min | ±1 day | 6 | 3 | 0 | 0 | 3 |
| 20 min | full span | 8 | 4 | 1 | 0 | 3 |
| 20 min | ±90 days | 6 | 5 | 1 | 0 | 0 |
| 20 min | ±1 day | 8 | 6 | 1 | 0 | 1 |
| 40 min | full span | 11 | 9 | 1 | 0 | 1 |
| 40 min | ±90 days | 14 | 10 | 1 | 0 | 3 |
| 40 min | ±1 day | 13 | 10 | 3 | 0 | 0 |
| 60 min | full span | 10 | 8 | 2 | 0 | 0 |
| 60 min | ±90 days | 12 | 8 | 2 | 0 | 2 |
| 60 min | ±1 day | 15 | 12 | 2 | 0 | 1 |

## Extractor naive (baseline): top-1 candidate of every excerpt, whatever the verdict

| excerpt | scope | n | within 30 s | 30 to 120 s | 120 to 600 s | beyond 600 s | no top-1 candidate | within 30 s, share | within 120 s, share | within 600 s, share |
|---|---|---|---|---|---|---|---|---|---|---|
| 10 min | full span | 126 | 1 | 0 | 0 | 121 | 4 | 1/126 (0.8%, 95% CI 0.1 to 4.4%) | 1/126 (0.8%, 95% CI 0.1 to 4.4%) | 1/126 (0.8%, 95% CI 0.1 to 4.4%) |
| 10 min | ±90 days | 126 | 1 | 0 | 0 | 121 | 4 | 1/126 (0.8%, 95% CI 0.1 to 4.4%) | 1/126 (0.8%, 95% CI 0.1 to 4.4%) | 1/126 (0.8%, 95% CI 0.1 to 4.4%) |
| 10 min | ±1 day | 126 | 7 | 0 | 2 | 113 | 4 | 7/126 (5.6%, 95% CI 2.7 to 11.0%) | 7/126 (5.6%, 95% CI 2.7 to 11.0%) | 9/126 (7.1%, 95% CI 3.8 to 13.0%) |
| 20 min | full span | 126 | 8 | 1 | 0 | 115 | 2 | 8/126 (6.3%, 95% CI 3.3 to 12.0%) | 9/126 (7.1%, 95% CI 3.8 to 13.0%) | 9/126 (7.1%, 95% CI 3.8 to 13.0%) |
| 20 min | ±90 days | 126 | 12 | 1 | 0 | 111 | 2 | 12/126 (9.5%, 95% CI 5.5 to 15.9%) | 13/126 (10.3%, 95% CI 6.1 to 16.9%) | 13/126 (10.3%, 95% CI 6.1 to 16.9%) |
| 20 min | ±1 day | 126 | 19 | 1 | 0 | 104 | 2 | 19/126 (15.1%, 95% CI 9.9 to 22.4%) | 20/126 (15.9%, 95% CI 10.5 to 23.2%) | 20/126 (15.9%, 95% CI 10.5 to 23.2%) |
| 40 min | full span | 104 | 12 | 2 | 0 | 89 | 1 | 12/104 (11.5%, 95% CI 6.7 to 19.1%) | 14/104 (13.5%, 95% CI 8.2 to 21.3%) | 14/104 (13.5%, 95% CI 8.2 to 21.3%) |
| 40 min | ±90 days | 104 | 12 | 2 | 0 | 89 | 1 | 12/104 (11.5%, 95% CI 6.7 to 19.1%) | 14/104 (13.5%, 95% CI 8.2 to 21.3%) | 14/104 (13.5%, 95% CI 8.2 to 21.3%) |
| 40 min | ±1 day | 104 | 17 | 6 | 2 | 78 | 1 | 17/104 (16.3%, 95% CI 10.5 to 24.6%) | 23/104 (22.1%, 95% CI 15.2 to 31.0%) | 25/104 (24.0%, 95% CI 16.8 to 33.1%) |
| 60 min | full span | 90 | 12 | 2 | 0 | 75 | 1 | 12/90 (13.3%, 95% CI 7.8 to 21.9%) | 14/90 (15.6%, 95% CI 9.5 to 24.4%) | 14/90 (15.6%, 95% CI 9.5 to 24.4%) |
| 60 min | ±90 days | 90 | 15 | 3 | 0 | 71 | 1 | 15/90 (16.7%, 95% CI 10.4 to 25.7%) | 18/90 (20.0%, 95% CI 13.0 to 29.4%) | 18/90 (20.0%, 95% CI 13.0 to 29.4%) |
| 60 min | ±1 day | 90 | 20 | 4 | 0 | 65 | 1 | 20/90 (22.2%, 95% CI 14.9 to 31.8%) | 24/90 (26.7%, 95% CI 18.6 to 36.6%) | 24/90 (26.7%, 95% CI 18.6 to 36.6%) |

"No top-1 candidate" counts excerpts whose search could not run (a constant trace). They stay in the denominator, as in M2.
