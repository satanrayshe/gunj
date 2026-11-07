# Demo video v1: shot list and voiceover (about 105 s)

Adapted from PREP section 7 to what exists on 7 Nov 2026 and to what the explorer site shows. Every bracketed number names the file and field it comes from. A number that already exists is written in; a Test number reads `[pending: ...]` until the gate is frozen (8 Nov 2026, 14:00 IST) and `scripts/fill_results.py` has filled the README. Copy Test numbers from the filled README results section, never from an INTERIM run.

## Production

- Screen capture of the explorer site at 1080p, one clip page at a time. Close the browser after each capture (the laptop has little free memory).
- Voiceover: a text-to-speech voice in Indian English, read from the VO lines below. The end card says the voice is synthetic.
- Captions burned in from the VO lines. No music, or a very quiet bed.
- **No stream audio at all in v1.** No audio or video of any stream is published (PROTOCOL section 17). The cold open is silent.
- The site's only motion is the slide-and-lock (about 1.2 s). Record the sequence once with motion on; the reduced-motion version is not needed for the video.
- Colours as on the site: amber for the recording's trace, cool grey for the reference, green only on a confirmed lock.

## Choosing the clips (after the gate)

- **Featured lock:** a Test stream marked `LOCK` in `results/gate.md`, preferably at 60 min in the full span. If one of the 3 Creative Commons BY Test streams (`data/splits.csv`, `license_cc`) is among the locks, use it. Its numbers come from its rows in `results/test_results.csv` (extractor `v2`, the chosen `excerpt_min` and `scope`).
- **If no Test lock is on the site yet** (for example, a recording before the gate): use the site's hero clip (`site/data/overview.json`, `hero`) and caption its split on screen (Dev or Calibration). Never present it as a Test result. Calibration streams set L, so a Calibration lock is not blind.
- **No-hum example:** a Test stream with `hum_present_v1_full` = False in `results/test_results.csv`.
- **Other-grid example:** a real report-half negative from `results/false_matches.json` whose confidence is below tau, if the site has a page for negatives. Otherwise a second Test stream whose `verdict_reason` is "confidence below tau".
- Each Test clip page shows the stream's longest excerpt in the full span, while `results/gate.md` counts a lock at any length in the full or ±90-day scope. A gate LOCK can therefore appear on its page as "MATCH, WRONG LOCK" or "no verdict" (HDfWJP4QYo0 did in the interim export). Feature a stream whose page shows a green MATCH.
- Do not show channel names that place a stream in a state or city. Video IDs and venue types are fine. The explorer prints the channel name next to the video ID on clip pages and in the hero note, so crop it out of the frame.

## Shot list and voiceover

**0:00 to 0:04. Cold open (silent).**
- Screen: the featured clip page with the metadata panel hidden and the lock card blank.
- Caption: "No date. No metadata. When was this recorded?"
- VO: none.

**0:04 to 0:16. The hum.**
- Screen: the top panel, the six harmonic tracks from 50 to 300 Hz read from the stream's trace file (`traces/test/<video_id>.npz`, `v2__full__hfreq` and `v2__full__hsnr`). The harmonic with the highest SNR is drawn in amber. There is no spectrogram, because no audio is kept. The explorer has no harmonic-track panel yet (checked 8 Nov 00:10 IST). Until one is added, film the clip page's Hum panel (median peak-to-floor ratio per harmonic, from the same trace file) instead.
- VO: "Equipment near mains power picks up a faint hum. Its pitch wanders with the load on the grid, and India is one grid, so it wanders the same way everywhere."

**0:16 to 0:26. The search.**
- Screen: the amber trace lifts off and slides along the grey strip of the whole reference while the site's counter runs to the number of start positions searched [`site/data/overview.json`, `hero.searches.full.positions_searched`; 83,207,007 for the current Calibration hero, computed by `scripts/export_site_data.py`]. The whole reference is [84,046,325 s: `results/reference_stats.json`, `span.seconds`], from [20 Dec 2020 to 20 Aug 2023: `results/reference_stats.json`, `span.t0_ist` and `span.t_end_ist`]; the site's strip shows only year ticks, so put the two dates in a caption.
- VO: "Gunj turns that wander into one number per second, and searches eighty-four million seconds of public grid records for the place where it fits."
- Check: "eighty-four million" is 84,046,325 rounded down to the million.

**0:26 to 0:40. The lock.**
- Screen: the trace locks on the matched reference window (the 1.2 s slide-and-lock). The lock card shows:
  - start time in IST to the second [pending: `results/test_results.csv`, `top1_start_ist`];
  - the top five candidates with r [pending: `results/test_results.csv`, `top5_start_ist`, `top5_r`];
  - confidence and its threshold [pending: `results/test_results.csv`, `confidence`, `tau`];
  - then the public start time, corrected by L and D, slides in beside it [pending: `results/test_results.csv`, `true_start_ist`], with the error [pending: `results/test_results.csv`, `error_s`].
- The card turns green only if `verdict` is "match" and `hit_30s` is True.
- VO: "The best fit starts at [pending: time from `results/test_results.csv`, `top1_start_ist`]. The runners-up and the margin are on the card. YouTube's public start time, corrected, agrees within [pending: absolute value of `results/test_results.csv`, `error_s`] seconds."

**0:40 to 0:52. The reference's own clock drifts.**
- Screen: the label-drift panel, or `results/time_axis_check.png` if the site has no such panel.
- Caption: "Locks at the gate with the drift correction: [pending: `results/gate_frozen.json`, `gate.by_extractor.v2.n_streams_with_lock`]. Without it: [pending: `results/gate_frozen.json`, `gate.by_extractor.v2.n_streams_with_lock_D0`]."
- VO: "The grid log's own clock also runs late, by up to [319: `results/time_axis_check.json`, largest `per_file.*.last_window_shift_s`] seconds by the end of a file. Gunj measures the drift from the grid's fifteen-minute schedule and corrects it."

**0:52 to 1:00. When it cannot tell.**
- Screen: the no-hum example's card, "No hum: no verdict". Then the other-grid example's card, "Below threshold: no verdict".
- VO: "No hum, or a recording from another grid? Then Gunj gives no verdict."

**1:00 to 1:27. How often it works.**
- Screen: the results table builds row by row, then the uniqueness curve (`results/uniqueness_curve.png`) with the label "SYNTHETIC noise on real reference data".
  - Test streams with audio [pending: README results, M1 denominator, from `results/test_results.csv`].
  - Hum present [pending: M1, `results/test_results.csv`].
  - Dated within 30 s, 60-min excerpts, full span [pending: `results/evaluation.json`, `m2.v2.60.full.top1_hit`, with its Wilson 95% CI].
  - Confident but wrong, same cell [pending: `results/evaluation.json`, `m2.v2.60.full.confident_wrong`].
  - False matches on held-out real negatives [pending: `results/evaluation.json`, `m3.v2.60.full.real.false_match`].
  - Uniqueness: shortest tested length for 90% unique locks over the full span at probe-like SYNTHETIC noise [40 min, 422 of 450 trials: `results/uniqueness_table.csv`, scope `full`, `hann16`, 40 mHz]; the length before it, 20 min, gave [121 of 450: same file].
- VO: "Across [pending: N, streams with audio, `results/test_results.csv`] Indian livestreams, chosen and scored by a plan fixed in advance, hum was present in [pending: M1 percent, `results/test_results.csv`] percent. With an hour of audio, Gunj dated [pending: M2 percent, `results/evaluation.json`, `m2.v2.60.full.top1_hit`] percent within thirty seconds, with [pending: count, `results/evaluation.json`, `m2.v2.60.full.confident_wrong`] confident mistakes. In a simulation with synthetic noise, a unique lock over the whole record needed between twenty and forty minutes of audio."
- Read each percentage with the one decimal the README shows.

**1:27 to 1:45. Limits and the ask.**
- Screen: the reference strip, ending at [20 Aug 2023: `results/reference_stats.json`, `span.t_end_ist`], then the end card.
- VO: "The open record ends in August 2023, so newer recordings cannot be checked yet. Grid-India measures the frequency every second. If that record were published, any Indian recording with hum could be checked the same way."
- End card:
  - [pending: public repo URL]
  - "Shrey Sati, Threws Odyssey 2026"
  - "Grid data: Mukherjee, Kalita and Kumar, Mendeley Data bvwh9d6227 v4, CC BY 4.0"
  - "Built with AI coding assistants under Shrey's direction. Synthetic voiceover. SYNTHETIC data labelled."

## Beats from PREP left out of v1, and why

- **Three seconds of Creative Commons lecture audio at the open.** v1 publishes no audio at all.
- **Edit detection (PREP 0:38 to 0:55).** M5 has SYNTHETIC cut traces but no scored result yet. Add it to the final video only if `results/` holds an M5 result, and keep the "SYNTHETIC edit for testing" label on screen.
- **The live station (PREP 1:15 to 1:30).** The station did not run (M6 not done).

## VO word count

208 words before the bracketed values are filled in, about 2 words per second over 105 s. No segment needs more than 2.5 words per second.
