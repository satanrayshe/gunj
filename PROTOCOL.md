**Pre-registration: approved by Shrey on Saturday 7 November 2026 at 09:05 IST and binding from the commit that adds this file.**

# Gunj evaluation protocol

- Date: Saturday 7 November 2026. Written between 03:30 and 04:00 IST.
- Author: Shrey Sati (GitHub satanrayshe), with AI coding tools (section 18).
- Status: approved 7 Nov 2026, 09:05 IST. Any later change is a deviation and goes in section 19.
- PREP section 5 planned this file for 6 Nov. It was drafted on 7 Nov before any audio for any stream in the splits existed (section 2). Dev and Calibration audio was then harvested before approval; section 19 records this. No Test or negative audio existed at approval.

Every number below comes from a file written by a script in this repo. The file is named next to the number. SYNTHETIC data is labelled SYNTHETIC wherever it appears.

## 1. Research question

How often does Indian online media carry a datable grid fingerprint, and how much audio does dating it take?

India runs one synchronous AC grid, so mains hum in a recording follows the same frequency wander everywhere in the country. Gunj extracts that wander from audio as a 1-Hz trace. It then searches a one-second log of Indian grid frequency for the moment the trace fits. This protocol fixes, before any test audio exists, how that is measured.

## 2. What exists at the time of writing

- No audio or video of any stream in `data/splits.csv` has been downloaded. A search of the repo and the data folder for audio and video files found none on 7 Nov at 03:35 IST.
- The only real audio processed for Gunj so far is the 5 Nov feasibility probe clip (section 4). Its audio is not on disk (`scripts/probe_noise.py` says so). Its printed outputs are in `docs/probe`.
- The candidate lists came from YouTube metadata only: 1,116 HTTP requests, all status 200, with 0 bot checks, 429s or sign-in walls (`results/metadata_summary.md`).
- The reference series, the gap map and the label-drift check are built (`results/reference_summary.md`).
- Extractor v1 and the matcher exist and are frozen by this file (section 7).

## 3. Data and licences

**Reference.** Mendeley Data dataset bvwh9d6227: Grid-India NERLDC PMU one-second frequency, shared by IIT Guwahati, CC BY 4.0. Five CSV files (freq_I to freq_V). The built series runs from 2020-12-20 17:21:01 to 2023-08-20 11:33:05 IST: 84,046,325 seconds, of which 83,273,631 (99.0806%) are valid (`results/reference_summary.md`). The SHA-256 of each CSV is in `../data/derived/grid_1s_meta.json`. CC BY requires attribution. The dataset's author list and version are not available offline and must be copied from the dataset page before release.

**Stream metadata.** Public YouTube pages, read with yt-dlp in metadata-only mode. Candidates: 424 Indian streams from 31 channels and 116 foreign streams from 6 channels (`results/metadata_summary.md`).

**Stream licences in the splits** (`results/splits_summary.json`):

| split | Creative Commons BY | standard YouTube licence |
|---|---|---|
| Dev | 1 | 4 |
| Calibration | 2 | 18 |
| Test | 3 | 147 |
| Negatives (real) | 9 | 41 |

No media is re-hosted. Audio or frames shown in any public material come only from Creative Commons BY streams, with attribution. For every other stream, only its video ID, metadata and derived 1-Hz trace are shown. YouTube's Terms of Service restrict downloading. Audio is downloaded only with Shrey's go-ahead, only for measurement, and is deleted after extraction (section 17).

## 4. Exclusions

- **Courts.** Court channels and court proceedings are excluded. The Gujarat High Court Live Streaming Rules 2021 bar copying and derivative use of its feeds. Channels were chosen to avoid courts, and `scripts/make_splits.py` also screens titles with a regular expression (court, judicial, tribunal and Hindi equivalents). It flagged 0 titles among the 424 Indian and 116 foreign candidates (`results/splits_summary.json`).
- **The probe clip.** PREP planned to put the 5 Nov probe clip (YouTube Gd37Os6tOqw) in Dev. It is a lecture on the Gujarat High Court channel, so it is excluded from every split. Its 5 Nov numbers stay in `docs/probe` as the disclosed feasibility probe. They are not part of any metric.
- **Nepal and Bhutan** are excluded from the negatives, because their grids may be synchronous with India's. No candidate came from either (`results/metadata_summary.md`).
- **Possible archive cap.** Streams of 11.5 h or longer are excluded. Several kept streams last 11.8 to 12.05 h, which looks like YouTube's archive length cap, and such an archive may not start at `release_timestamp`. This removed 23 Indian candidates (`results/splits_summary.json`).
- **Reference coverage.** An Indian stream is excluded if less than 90% of the reference seconds over its whole span are valid. This removed 5 candidates (`results/splits_summary.json`).
- **Other rules**, each of which removed 0 candidates this time (`results/splits_summary.json`): start time not from `release_timestamp`; not a public past live stream; shorter than 20 min; any part outside the reference series; less than 90% valid seconds over the analysed window (the first 65 min); a reference file handover within 10 min of the analysed window.

## 5. Splits and seeds

`scripts/make_splits.py` drew the splits from metadata only, with one numpy random generator and seed **2026110701**. It writes `data/splits.csv` and `results/splits_summary.json`. Re-running it on the same inputs writes a byte-identical `data/splits.csv`; this was checked twice on 7 Nov.

**SHA-256 of `data/splits.csv`: `94d13272de9f2cc3c7548d89daf02f7bdeb2fad0d55a049ae4e4eb08859af781`** (265 rows; `results/splits_summary.json`).

The SHA-256 of every input (both candidate CSVs, `results/time_axis_check.json`, the reference mask, source array and metadata) is recorded in `results/splits_summary.json`. `.gitattributes` stops git from changing the line endings of the hashed files.

Eligible Indian candidates: 396 from 30 channels. No two selected Indian streams overlap in time over their analysed windows, so every selected stream is an independent recording (`results/splits_summary.json`).

| split | target | drawn | how | source |
|---|---|---|---|---|
| Dev | up to 5 | 5 | One per venue type (legislature, lecture, religious, government event, conference), distinct channels, at least 62 min long, label-stable (section 6), verified channels only. A Creative Commons stream was preferred for the lecture slot. | `results/splits_summary.json` |
| Calibration | 20 | 20 | Label-stable, at least 62 min long, verified channels only, at most 1 per channel. 20 channels. | `results/splits_summary.json` |
| Test | about 150 | 150 | The remaining eligible streams in seeded random order, at most 10 per channel. 28 channels; the largest channel count is 10. | `results/splits_summary.json` |
| Negatives (real) | about 50 | 50 | Foreign 50 Hz streams, spread over the 6 channels by round-robin quotas, streams of at least 62 min taken first. Alternate rows go to the threshold half (25) and the report half (25). | `results/splits_summary.json` |
| Negatives (SYNTHETIC) | in addition to the 50 | 40 | 20 SYNTHETIC white-noise items and 20 SYNTHETIC hum items following a SYNTHETIC random walk. Each has its own seed and a random pseudo claimed time. Halves of 20 and 20. | `results/splits_summary.json` |

There was no shortfall against any target (`results/splits_summary.json`, `shortfalls_and_notes` is empty).

Make-up of the splits (`results/splits_summary.json`):

- **Dev:** Bihar Vidhan Sabha TV (legislature), HBCSE-TIFR (lecture, CC BY), Somnath Temple (religious), PIB India (government event), ICTS (conference). Four of the five use reference file V.
- **Calibration:** 8 lecture, 7 legislature, 2 government event, 2 religious, 1 conference. Reference file: V 14, II 3, IV 2, III 1.
- **Test:** 57 legislature, 37 lecture, 30 government event, 24 religious, 2 conference. Reference file: I 27, II 21, III 38, IV 17, V 47. 39 of the 150 are label-stable. 27 come from channels whose official status is not verified, or whose location is uncertain (Maharashtra Assembly Live, Shree Saibaba Sansthan Trust, prdlivekerala, Kerala Government, Mayapur TV). 108 streams are long enough for all four excerpt lengths, 17 for 10 to 40 min, and 25 for 10 and 20 min only.
- **Negatives (real):** GB 16 (St Paul's 11, Senedd 5), IT 11 (ICTP), LK 10, PK 10, VA 3. 35 are long enough for all four excerpt lengths. No Bangladesh, German, Dutch or Belgian source gave candidates (`results/metadata_summary.md`).

The SYNTHETIC negatives are made at extraction time with `gunj/synthetic.py`, using the seed in each row's `synthetic_seed` column. Each is 3,900 s at 1 kHz:
- `SYN-NOISE-xx`: SYNTHETIC white Gaussian noise, standard deviation 1.
- `SYN-WALK-xx`: `synthetic_hum(synthetic_enf_walk(3901, seed), sr=1000, seed=seed)` with the module's default amplitudes and noise. This is SYNTHETIC hum that follows a SYNTHETIC grid that is not India's.

## 6. Ground truth

### The reference's time labels drift

The build found that the reference's own time labels are not consistent:
- Overlapping files label the same values 172, 64, 319 and 198 s apart (I-II, II-III, III-IV, IV-V; `results/reference_build_stats.json`).
- Inside each file, a check against the grid's 15-minute scheduling clock finds labels drifting late, in steps. Between each file's first and last week the labels drift late by 172, 64, 319, 198 and 224 s for files I to V (`results/time_axis_check.json`).
- The 5 Nov probe read a "start latency" of +189 s. Most of that is file IV's label offset. On the series' labels the probe lock is 9 s before YouTube's start (`results/reference_summary.md`). The 189 s figure is therefore not used anywhere in this protocol.

So a recording's true start and its position on the reference's labels can differ by minutes. A single constant latency cannot fix that alone.

### Label-drift correction D

For each stream, `scripts/make_splits.py` recorded D, the estimated label drift (seconds, positive = labels late) at the stream's start:
1. Find the reference file in use at the stream's start (`grid_1s_src_u8.npy`).
2. Take that file's weekly `shift_s` values from `results/time_axis_check.json` and smooth them with a running median over 3 windows.
3. D is the smoothed value at the window nearest the start. `drift_window_r` is that window's profile correlation.

D is in `data/splits.csv`, column `label_drift_s`, frozen with the splits. A stream is **label-stable** if `drift_window_r` is at least 0.5. For 93 of the 150 test streams |D| is over 30 s (`results/splits_summary.json`), so the correction matters. D is itself an estimate with unknown error. Section 13 therefore also reports results without it and on the label-stable subset.

### Latency L, from the Calibration split only

L is the constant delay between YouTube's `release_timestamp` and the first audio sample of the archive, on drift-corrected reference time. It is estimated once:
1. The frozen extractor (section 7) runs on each Calibration stream's 60-min excerpt (section 8).
2. Each excerpt is searched in the ±1-day scope around its `release_timestamp` in IST.
3. For each stream, o = (implied recording start on the reference labels) − (release time in IST + D).
4. A stream counts if its confidence is at or above the frozen threshold for (60 min, ±1 day) and |o| ≤ 600 s.
5. If at least 3 streams count, L = the median of their o, rounded to the nearest second. Otherwise L = 0 and every accuracy result is labelled "latency not calibrated".
6. All o values, their interquartile range and their full range are reported. If the range of the counted o exceeds 60 s, the report says a single constant latency does not hold.

L is written to `results/latency.json` and committed before any Test search result is computed. No Dev or Test stream is used to estimate L.

### Truth for a test stream

T* = release time in IST + L + D. This is the expected start of the recording on the reference's own labels. Section 9 defines a hit against T*.

## 7. Extractor and matcher

**Extractor v1 is frozen as it stands in `gunj/extract.py`.** It uses 16 s Hann windows with a 1 s hop and a 2^18-point FFT at 1 kHz. Peaks are found for harmonics 1 to 6 within k·50 ± 0.15k Hz and refined by quadratic interpolation. Harmonics are weighted by max(median peak-to-floor − 2, 0). The trace is smoothed with a 5-point running median.

**The matcher is frozen as it stands in `gunj/match.py`:** normalised cross-correlation, windows with less than 90% valid reference seconds or a reference standard deviation under 0.003 Hz excluded, local-peak candidates, a runner-up at least 60 s from the top candidate, and `find_edits` with its current defaults.

SHA-256 at the time of writing (`results/splits_summary.json`, `frozen_code_sha256`):

| file | SHA-256 |
|---|---|
| `gunj/extract.py` | `ecca86510ed2dbcc7f8d71895949f3df09e0e80257e272d5537f8a806a6d998d` |
| `gunj/match.py` | `8810a350065256c2785720a1211fadca4c3bbe37b67a8cf44c465b9ed16f7343` |
| `gunj/synthetic.py` | `60ebdead2ce873bf2cf7d56c8d1dcfc7d2914dfc73381070852fad88bcdd4680` |
| `gunj/reference.py` | `0a8db5dd8fdac0aa9bbc9bee57fb9f7382a31fd4fff26fd75f6bd3c1edf04a4b` |

**Extractor v2** (PREP: SNR-weighted harmonics and per-frame SNR weights in the correlation):
- v2 is tuned on the 5 Dev streams only. Tuning may use Dev ground truth.
- v2 may change the extraction and may add per-frame weights to the correlation. The confidence score, the scopes, the hit rule and the threshold rule stay as written here.
- v2 is frozen before any Calibration, Test or negative audio is extracted. Freezing means a git tag `extractor-v2`, with the SHA-256 of the v2 code added to section 19.
- If the harvest of those splits has to start before v2 is frozen, the harvest extracts v1 and the naive baseline only, and v1 is the primary extractor everywhere.
- The **primary extractor** is v2 if it was frozen in time, otherwise v1. v1 is always reported beside it on the same streams.

## 8. Excerpts and search scopes

**Audio.** Only the first 65 min of each stream are downloaded, converted to 1 kHz mono.

**Excerpts.** One excerpt per stream per length. An m-minute excerpt is audio seconds [120, 120 + 60m), cut from the audio and extracted on its own. Lengths are 10, 20, 40 and 60 min. A length is run only if the stream is long enough (`excerpt_lengths_min` in `data/splits.csv`). Trace frame 0 is centred 128 s into the recording. So if the matcher aligns frame 0 with reference second p, the implied recording start is reference label p − 128 s.

**Scopes.** A scope bounds the implied recording start:
- **full:** the whole series, 84,046,325 s (`results/reference_summary.md`);
- **±90 days:** release time in IST ± 90 days, clipped to the series;
- **±1 day:** release time in IST ± 1 day, clipped to the series (the claimed-date check).

The scope centre is the uncorrected release time. The scope is wide enough that L and D do not matter for it.

**Search.** Every excerpt is searched in every scope, whether or not hum was detected. The verdict rule (section 9) applies afterwards.

## 9. Hit, confidence and verdict

- **Error:** e = implied recording start of the top candidate − T* (section 6).
- **Hit:** |e| ≤ 30 s.
- **Coarse hit (secondary, for the label problem only):** |e| ≤ 120 s and |e| ≤ 600 s are also reported. They are never substituted for the 30 s rule.
- **Confidence:** atanh(r1) − atanh(r2). r1 is the top candidate's correlation and r2 is the best candidate at least 60 s away (`gunj/match.py`). It is a margin over the best wrong candidate, not raw r.
- **Verdict:** "match" if the trace's own hum flag is set (any harmonic's median peak-to-floor ratio ≥ 3) and confidence ≥ τ for that cell. Otherwise "no verdict".
- **Outcomes per test excerpt:** confident hit (match and hit), confident wrong (match and not hit), or no verdict.

## 10. Setting the confidence threshold

- A threshold τ is set separately for each extractor, excerpt length and scope (4 lengths × 3 scopes per extractor). Null margins depend on trace length and scope size, so one value would not fit all.
- τ is set on the **threshold half** only: 25 real negatives (18 at 60 min, 21 at 40 min) plus 20 SYNTHETIC negatives (`results/splits_summary.json`, `negative_trials_per_half_and_length`).
- Rule: with n threshold-half trials in a cell, let m = floor(0.01 n). τ is the (m+1)-th largest confidence plus 0.0001. Then at most 1% of the threshold-half trials reach τ. With n under 100, m = 0, so τ sits just above the largest negative confidence.
- Search is forced on negatives, whatever their hum flag. A negative with no runner-up counts as no verdict.
- τ for every cell, with every threshold-half confidence, is written to `results/threshold.json`. The primary extractor's τ also goes into `gunj/config.json` with `confidence_threshold_calibrated: true`. Both are committed **before any Test search result is computed or looked at**. The placeholder 0.1 now in `gunj/config.json` is not used for any result.
- The false-match rate is then measured on the **report half** (section 11, M3).

What this can show: the report half has 25 real negatives at 10 and 20 min, 19 at 40 min and 17 at 60 min. Even with 0 false matches, the Wilson 95% upper bound on the real-negative false-match rate is 13.32%, 13.32%, 16.82% and 18.43%. Adding the 20 SYNTHETIC report negatives gives 7.87%, 7.87%, 8.97% and 9.41% (`results/splits_summary.json`). The 1% target can be aimed at but not demonstrated with this many negatives. The report will say so.

## 11. Metrics

The first sentence of each metric is PREP section 6's definition. The rest says how it is computed. Every rate has a Wilson 95% interval (section 13).

**M1, hum presence.** Share of streams where any harmonic's median peak-to-floor ratio is at least 3. Reported overall and by venue type, with Wilson 95% CIs.
- Computed with extractor v1 (frozen) on all the downloaded audio of each Test stream (up to 65 min). The hum-present flag used elsewhere is this one.
- Also reported, as description only, for the real negatives.

**M2, dating accuracy.** Top-1 hit rate at excerpt lengths of 10, 20, 40 and 60 min, under three search scopes: the full 32 months, plus or minus 90 days, and plus or minus 1 day (claimed-date check). Reported both over all streams and over hum-present streams, with Wilson CIs.
- Denominator: Test streams with a trace at that length. Streams whose download failed are listed in the flow table (section 16), not counted.
- Top-1 hit rate ignores the threshold. Confident-hit, confident-wrong and no-verdict rates are reported for the same cells.
- The signed error distribution is plotted for every cell.

**M3, false matches.** False-match rate on the held-out negatives at the frozen threshold (target at most 1%), plus a reliability plot of the confidence score. The score is based on the margin over the best wrong candidate, not on raw r.
- A false match is a report-half negative excerpt whose confidence reaches τ for its cell. It is reported with and without the hum-flag condition.
- Real and SYNTHETIC negatives are reported separately and together.
- Reliability plot: Test top-1 hit rate by confidence bin, bins [0, 0.025), [0.025, 0.05), [0.05, 0.1), [0.1, 0.2), [0.2, 0.4), [0.4, ∞), with counts. The negatives' confidences are drawn on the same axis.

**M4, uniqueness curve.** Real reference segments are injected with noise at the measured SNRs, to find the clip length needed for 90% unique locks at each search scope.
- Run by `scripts/uniqueness.py` with the settings in `results/uniqueness_run.json`: seed 20261107, lengths 5 to 60 min, SYNTHETIC white and 16-s-smoothed noise at σ = 0 to 0.06 Hz, a 30 s hit rule, and the same three scopes. It uses no audio.
- The "measured SNR" is the equivalent trace noise of the Test streams that lock, computed as in `scripts/probe_noise.py`. The curve is read at that noise level by interpolating between the pre-set levels. Nothing in M4 is tuned on Test results.

**M5, edit detection.** Synthetic cuts of 10 to 120 s are made in locked clips. Report the detection rate, false alarms on unedited clips, and the error in the estimated missing duration. All edits are labelled synthetic.
- Audio is deleted after extraction. So at harvest time, every Test stream with at least 62 min gets 2 SYNTHETIC cut versions of its 60-min excerpt audio, made with `gunj.synthetic.cut_audio`. Each cut's length is drawn from {10, 20, 40, 60, 90, 120} s and its position is uniform, at least 300 s from either end of the excerpt. The seeds come from the split seed and the video ID. These traces are extracted with the primary extractor and the audio is deleted.
- M5 uses only streams whose uncut 60-min excerpt is a confident hit in the full scope. Each cut trace is located in the ±1-day scope, then `find_edits` runs with its frozen defaults.
- A detection is a reported cut within ±30 s of the true cut position. The duration error is |reported jump − true cut length|. A false alarm is any edit reported on the uncut excerpt of the same streams.
- Results are given by cut length, and every one is labelled SYNTHETIC.

**M6, station (optional).** Agreement of the station's 2026 log with the metadata of this week's livestreams.
- If the station runs, this week's non-court Indian livestreams with known start times are searched against the station's own log. The same 30 s rule and latency L are used.
- The station keeps one frequency value per second and never keeps room audio.
- If the station does not run, M6 is reported as not done.

## 12. Baselines

- **Naive single-bin 50 Hz extractor:** `extract_enf_naive` in `gunj/extract.py` (frozen with v1). It runs on the same excerpts and scopes through the same matcher. It gets its own τ, set by the same rule on the same threshold-half negatives.
- **libhum:** libhum's matcher (https://github.com/RaphaelJ/libhum, LGPL) with its default settings, fed the primary extractor's traces and the same reference. It runs on the same excerpts, in the full scope, and in the narrower scopes if libhum supports a time range. The libhum version or commit is recorded. libhum is not installed at the time of writing. If it cannot be built, or cannot run within the laptop's memory limit, it is reported as "not run" with the reason.
- **Random guess (analytic):** a uniform random start in a scope lands within ±30 s of the truth with probability 61 / N, where N is the number of start positions. For a 60-min excerpt in the full scope that is 7.258 × 10⁻⁷, or 1 in 1,377,750. For ±90 days it is 1 in 254,951; for ±1 day, 1 in 2,833. Over all 150 Test streams, the expected number of chance hits at 60 min in the full scope is 0.000109 (`results/splits_summary.json`, `analytic_random_guess`).

## 13. Statistics

- **Wilson 95% interval** for k successes in n trials, z = 1.959964: centre (p + z²/2n)/(1 + z²/n), half-width z·sqrt(p(1−p)/n + z²/4n²)/(1 + z²/n), with p = k/n.
- **Primary endpoint:** the M2 top-1 hit rate of the primary extractor for 60-min excerpts in the full scope, over all Test streams with a 60-min trace (up to 108; `results/splits_summary.json`).
- **Key secondary results:** for the same cell, the confident-hit and confident-wrong rates; then the ±90-day scope; then the hum-present subset.
- Every other cell is reported as description. No significance tests are run. Comparisons with baselines are shown as paired counts on the same streams.
- **Sensitivity analyses, all pre-registered:**
  - S1: hit rule with D = 0 (no label-drift correction);
  - S2: label-stable Test streams only (39);
  - S3: excluding the 27 Test streams from unverified or location-uncertain channels;
  - S4: v1 against v2 on the same streams.

## 14. Order of work and blinding

1. Shrey approves the commit with this file and `data/splits.csv`.
2. Dev audio is harvested. v2 is tuned on Dev only and frozen (section 7).
3. Negative, Calibration and Test audio is harvested. Traces (v1, v2 if frozen, naive, and the M5 SYNTHETIC cuts) are extracted and the audio is deleted. The SYNTHETIC negatives are generated. One harvester runs at a time, in this order: the 25 threshold-half real negatives, then the Test streams in their `data/splits.csv` row order (the seeded random order of section 5), then the 25 report-half real negatives. Downloads take 2 to 16 min per stream, so not every stream will be done by the gate. Because the Test order is random and fixed here, the Test streams finished by the gate are a random sample of the Test split.
4. τ is set from the threshold-half negatives and committed (section 10).
5. L is estimated from Calibration and committed (section 6).
6. The evaluation script is committed. Only then are Test searches run and their results computed.
7. The gate (section 15), then the full results.

Nobody looks at a Test stream's search result before step 6. Hum statistics (M1) may be computed at harvest time, because they use no ground truth and set nothing.

## 15. Gate: Saturday 7 November, 22:00 IST (PREP section 4)

Stay with Gunj only if all three hold. Otherwise switch to Wet Ground.

1. At least **5 independent Test streams** lock blind to within 30 s of T* (section 6). A stream counts if, for at least one pre-registered excerpt length, its top candidate in the full scope or the ±90-day scope is a hit. Test streams do not overlap in time, so any 5 are independent.
2. Each of those locks has confidence at or above the frozen threshold τ for its own cell (extractor, excerpt length, scope; section 10).
3. YouTube audio harvesting still works: the harvest has met no bot check, CAPTCHA, sign-in wall or HTTP 429, and its most recent download succeeded.

The gate uses every Test stream processed by the decision time. `results/gate.md` lists them all, hits and misses, with N. The S1 count (D = 0) is shown beside it. If τ or L has not been committed by 22:00, the gate is not met.

## 16. Reporting every result

- A flow table: streams selected → download attempted → audio obtained → trace extracted → hum present → match → hit, with the reason for every drop.
- `results/test_results.csv`, one row per Test stream and excerpt: video ID, hum statistics, top-5 candidates, r, confidence, verdict, error. Streams with no hum, failed downloads and wrong locks are included.
- Dev results are reported separately and never pooled with Test.
- Every metric in section 11 is reported, including those that come out badly. If a planned analysis cannot be run, the report says so and why.

## 17. Data handling

- Downloads: audio only, the first 65 min, lowest audio bitrate, converted to 1 kHz mono. No sign-in, cookies or proxies. Sleeps between requests. At the first bot check, CAPTCHA, sign-in wall or HTTP 429, harvesting stops and is reported. Nothing tries to get around it.
- Kept: video IDs, metadata, the SHA-256 of each downloaded audio file, the 1-Hz traces, hum statistics and search results.
- Deleted: all downloaded media, right after extraction. Exception: the 1 kHz audio of the 5 Dev streams may be kept on the laptop for tuning. It is never committed or uploaded, and it is deleted when the project ends.
- Never re-hosted: no audio or video goes into the repo, the site or the video, except Creative Commons BY material with attribution (section 3).
- Gunj reports time and grid only, never a location within India.

## 18. AI-tool disclosure

The code, the analysis scripts and this protocol were written with AI coding assistants, working under Shrey's direction. Shrey reviews and approves the pre-registration commit. The 5 Nov feasibility probe was run before the build started and is disclosed in `docs/probe`. The README repeats this disclosure.

## 19. Deviations

Any change after Shrey approves this file goes here. Each entry gives the date and time (IST), what changed, why, and whether any Test search result had been seen.

- **7 Nov 2026, 08:30 IST, before this file was approved. Order of work (sections 14 and 6).** No Test or negative audio had been downloaded and no Test search result had been computed or seen.
  - Dev and Calibration audio were harvested before approval, although section 14 puts approval first. Shrey's 01:20 IST approval of audio downloading covered this, and the work order limited it to Dev and Calibration. `data/harvest_manifest.csv`: 5 of 5 Dev and 19 of 20 Calibration streams done. hG8oxgWwPYE failed ("This video is not available"). No bot check, sign-in wall, CAPTCHA or HTTP 429 was met.
  - v2 was tuned on Dev only (`results/v2_tuning.md`) and frozen at 05:55 IST, before any Calibration audio was extracted: annotated git tag `extractor-v2` on commit ef1d27e (it was ad9c54f until the history rewrite noted below). SHA-256 of `gunj/extract_v2.py`: `273e59d45a96e91306f8b0c3821a2315e5d7bf32076eaa60528b71d86fede6f0`. SHA-256 of `gunj/match_v2.py`: `de41f0fc50d80e7c6b810d4e92e8c8884b2076e91b60bea1e7a131e168ccd306`. The per-frame correlation weights that section 7 allows live in `gunj/match_v2.py`. `gunj/extract.py` and `gunj/match.py` are unchanged.
  - The Calibration traces were searched before tau exists, although section 14 sets tau before L. Step 4 of the L rule needs tau, so L is not set yet. `results/calibration.md` lists every o and a PROVISIONAL median that drops the tau condition; it is not L. `scripts/calibrate_latency.py` applies the full rule and writes `results/latency.json` once `results/threshold.json` exists.
  - At about 08:50 IST the four local commits were rewritten before anything was pushed. Tool attribution lines were taken out of the commit messages, and absolute local paths in `results/cli_smoke_real_ref.json` and `results/v2_tuning.json` were made relative. No code file changed, and the frozen SHA-256 values above still hold. The commits are now 4cd37fd, ef1d27e (tag `extractor-v2`), be60dff and 273f86b; they were 598aad3, ad9c54f, 442f848 and 9ae56e5.
  - Section 14 step 3 now fixes the harvest order (threshold-half negatives, then Test in row order, then report-half negatives). It was added because downloads are slower than planned, so the gate will see only part of the Test split.

## 20. How to check this file

From the repo root:

```
.venv\Scripts\python.exe scripts\make_splits.py --derived-dir ..\data\derived --verify-protocol PROTOCOL.md
```

This re-draws the splits from the same inputs and seed. It checks that this file quotes the current SHA-256 of `data/splits.csv` and of every frozen code file, and exits with status 1 if any differ.
