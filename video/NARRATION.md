# Gunj demo video: narration

Read this aloud while the silent video (`video/gunj-v1-silent.mp4`) plays. Each line has the time, in minutes and seconds, at which its caption appears on screen. Start the line when its caption appears and finish before the next one. A calm pace is about two words a second.

"Gunj" is said "goonj".

1. **0:01** This is Gunj. It dates Indian recordings from mains hum.
2. **0:06** Many recordings carry a faint mains hum. Its pitch wanders with the load, the same way across India's one grid.
3. **0:16** Gunj reads that pitch each second and slides it along thirty-two months of public grid records, about eighty-four million seconds.
4. **0:26** It fits one moment, well clear of the next best. This example is a calibration stream, not a blind result.
5. **0:36** The test was pre-registered and blind. A YouTube bot check stopped downloads after a hundred and twenty-six of a hundred and fifty streams.
6. **0:48** Ninety had an hour of audio. For twenty-five, Gunj's best guess was within thirty seconds. Twenty-three verdicts were confident. Fourteen were within thirty seconds and nine were wrong.
7. **1:03** The grid dataset's own clock drifts, by up to about five minutes. Correcting it matters: thirty-eight test streams lock with it, nine without.
8. **1:16** In a simulation with SYNTHETIC noise, read at the measured level, unique locks needed twenty to forty minutes.
9. **1:25** This test stream locked blind, eight seconds from its expected start, after almost five minutes of drift correction.
10. **1:34** This stream has no hum, so no verdict. Gunj never reports a place. On sixteen hour-long recordings from other grids, it gave one false match.
11. **1:47** The code, the plan and every result are on GitHub.

The video ends at **1:54**.

Word count: <!-- narration-words:start -->215<!-- narration-words:end -->.

## Where each number comes from

`scripts/fill_results.py` writes the word count above and the table below. It reads each value from the file named in the last column, and it stops without writing anything if a spoken number in the lines above differs from that file.

<!-- narration-sources:start -->
| line | said | value | source |
|---|---|---|---|
| 3 | thirty-two months | 20 Dec 2020 to 20 Aug 2023 | `results/reference_stats.json` |
| 3 | about eighty-four million seconds | 84,046,325 s | `results/reference_stats.json` |
| 4 | well clear of the next best; a calibration stream | r 0.9986 against 0.9226, confidence 2.013 against tau 0.0834; Calibration stream aTL452sxXOA, shown as an example only | `site/data/overview.json`, `hero` |
| 5 | a hundred and twenty-six of a hundred and fifty | 126 of 150 selected Test streams had audio when the bot check stopped the harvest | `results/evaluation.json`; PROTOCOL section 19 |
| 6 | ninety had an hour of audio | n = 90 Test streams with a 60-min excerpt | `results/evaluation.json` |
| 6 | for twenty-five, the best guess was within thirty seconds | 25/90 (27.8%, 95% CI 19.6 to 37.8%): top-1 start within 30 s of T*, v2, 60 min, full span | `results/evaluation.json` |
| 6 | twenty-three verdicts were confident | 23 of the 90 excerpts have the verdict "match" (hum flag set, confidence at or above tau) | `results/error_breakdown.json` |
| 6 | fourteen were within thirty seconds and nine were wrong | 14 confident hits (within 30 s) and 9 confident wrong verdicts (more than 30 s off; PROTOCOL section 9): 6 from 30 to 120 s, 1 from 120 to 600 s, 2 beyond 600 s, off by 5,299 s (88 minutes) and 2,876,519 s (33.3 days) | `results/evaluation.json` (`m2`); `results/error_breakdown.json`; `results/test_results.csv`, `error_s` |
| 7 | up to about five minutes | largest label drift by the end of a file: 319 s | `results/time_axis_check.json` |
| 7 | thirty-eight with the correction, nine without | gate count: 38 of 126 streams lock at some excerpt length (full span or ±90 days), 9 with D = 0 | `results/gate_frozen.json` |
| 8 | twenty to forty minutes | reading B, as pre-registered (unweighted r, the computation of scripts/probe_noise.py): median noise 37.4 mHz over the 38 Test streams that lock; with SYNTHETIC hann16 noise over the full span, 40 min is the shortest tested length with at least 90% unique locks (94.6%) and 20 min gives 35.7%; the EXPLORATORY (post hoc) reading A (r as scored) is not spoken: 10 min at 9.4 mHz | `results/m4_measured.json` |
| 9 | eight seconds | Test stream 5LW0lKI1jx8: e = -8 s (v2, 60 min, full span) | `results/test_results.csv` |
| 9 | almost five minutes | the same stream: D = +293 s | `results/test_results.csv`, `label_drift_s` |
| 10 | no hum, no verdict | Test stream eEJdGpEayes: M1 hum flag off, and no verdict in any of its 12 v2 searches | `results/test_results.csv` |
| 10 | sixteen recordings from other grids, one false match | 1/16 (6.2%, 95% CI 1.1 to 28.3%) real foreign 50 Hz streams at or above tau (v2, 60 min, full span); SYNTHETIC negatives 0/20 (0.0%, 95% CI 0.0 to 16.1%); 24 of 25 real report-half negatives were harvested | `results/evaluation.json` (`m3`, `m3_counts`) |
<!-- narration-sources:end -->
