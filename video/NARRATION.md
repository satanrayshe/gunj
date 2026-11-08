# Gunj v1 demo: narration

Read this aloud while the silent video (`video/gunj-v1-silent.mp4`) plays. Each line has the time, in minutes and seconds, at which its caption appears on screen. Start the line when its caption appears and finish before the next one. A calm pace is about two words a second.

"Gunj" is said "goonj".

1. **0:01** This is Gunj. It dates Indian recordings from mains hum.
2. **0:06** Many recordings carry a faint hum from the mains. Its pitch wanders with the load, the same way across India's one grid.
3. **0:16** Gunj reads that pitch once a second and slides it along thirty-two months of public grid records, about eighty-four million seconds.
4. **0:26** It fits one moment, well clear of the next best. This example is a calibration stream, not a blind result.
5. **0:36** The real test was planned in advance and scored blind. YouTube's bot check stopped downloads after a hundred and twenty-six of a hundred and fifty streams.
6. **0:48** Of the ninety streams with an hour of audio, Gunj's best guess was within thirty seconds for about one in four; forty-three percent when hum was present. Confident verdicts: fourteen right, nine wrong.
7. **1:03** The grid dataset's own clock drifts, by up to about five minutes. Correcting it is what makes this work: thirty-eight test streams lock with it, nine without.
8. **1:16** In a simulation with synthetic noise, forty minutes of audio was enough for a unique lock nine times in ten.
9. **1:25** This test stream locked blind, eight seconds from its expected start, after almost five minutes of drift correction.
10. **1:34** This one has no hum, so Gunj gives no verdict. It reports a time, never a place. With downloads stopped, the false-match check used synthetic recordings only.
11. **1:47** The code, the plan and every result are on GitHub.

The video ends at **1:54**.

Word count: 234.

## Where each number comes from

| said | value | source |
|---|---|---|
| thirty-two months | 20 Dec 2020 to 20 Aug 2023 | `results/reference_stats.json` |
| about eighty-four million seconds | 84,046,325 s | `results/reference_stats.json` |
| well clear of the next best (line 4) | r 0.9986 against 0.9226, confidence 2.013 against tau 0.0834; Calibration stream aTL452sxXOA | `site/data/overview.json`, `hero` |
| a hundred and twenty-six of a hundred and fifty | 126 of 150 Test streams had audio when the bot check stopped the harvest | `results/gate.md`, PROTOCOL section 19 |
| ninety streams with an hour of audio | n = 90 for 60-min excerpts | `results/evaluation.json` |
| about one in four within thirty seconds | 25/90 (27.8%), top-1 within 30 s, v2, 60 min, full span | `results/evaluation.json` |
| forty-three percent when hum was present | 16/37 (43.2%), same cell | `results/evaluation.json` |
| fourteen right, nine wrong | confident hit 14/90, confident wrong 9/90, same cell | `results/evaluation.json` |
| up to about five minutes | largest per-file drift 319 s | `results/time_axis_check.json` |
| thirty-eight with the correction, nine without | gate count: 38 of 126 streams lock at any excerpt length (full or ±90 days), 9 with D = 0 | `results/gate_frozen.json`, `results/gate.md` |
| forty minutes, nine times in ten | 422/450 (93.8%) unique locks at 40 min, full span, 40 mHz smoothed SYNTHETIC noise | `results/uniqueness_table.csv` |
| eight seconds, almost five minutes | Test stream 5LW0lKI1jx8: e = -8 s, D = +293 s | `results/test_results.csv` |
| false-match check, synthetic only | 0/20 SYNTHETIC report-half negatives; 0 real ones harvested | `results/evaluation.json` (`m3`, `m3_counts`) |
