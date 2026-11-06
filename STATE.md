# Gunj build state (read this first after any restart)

Last updated: 7 Nov 2026, 01:30 IST, by Claude (hackathon-work session).

If the laptop was shut down or the session ended, a new Claude Code session should:
1. Open this file and `git log --oneline` in this folder.
2. Check which steps below are done by looking for their output files, not by trusting this list.
3. Restart only the unfinished step, using its command. Every long-running script is meant to skip work it has already finished.

## Decisions on record
- 7 Nov ~01:20 IST: Shrey approved downloading **audio only** from public YouTube livestreams for Gunj, and warned that the laptop may shut down mid-run.
  - Constraints: about 150 test streams, about 50 foreign negatives, plus dev and calibration streams.
  - Download only the first 65 minutes, at the lowest audio bitrate. Convert to 1 kHz mono.
  - Extract every trace needed (v2, the naive baseline, libhum), then delete the audio.
  - Keep only video IDs, hashes and the 1-Hz traces. Exception: the 1 kHz audio of the dev split (5 streams or fewer) may be kept for tuning.
  - No sign-in or cookies. Sleep between requests. Stop at the first bot check or HTTP 429.
- Court channels are excluded.
- Nothing is pushed or published until Shrey runs the push himself.

## Steps
| # | Step | Output that proves it is done | Status |
|---|---|---|---|
| 1 | Reference files downloaded and verified | `../data/enf-ref/freq_*.csv` match Mendeley SHA-256 | DONE 7 Nov 00:50 |
| 2 | Reference series + gap map | `../data/derived/grid_1s_f32.npy`, `results/reference_summary.md` | running (workflow wf_378a5581-734) |
| 3 | Core extractor, matcher, CLI, tests | `gunj/extract.py`, `gunj/match.py`, `pytest` passes | running |
| 4 | Metadata-only candidate list | `data/candidates_india.csv`, `data/candidates_negatives.csv` | running |
| 5 | Uniqueness curve (M4, reference only) | `results/uniqueness_table.csv` | waiting on 2+3 |
| 6 | PROTOCOL.md + splits | `PROTOCOL.md`, `data/splits.csv` | waiting on 2+4 |
| 7 | Hostile review of 2-6 | review notes in the workflow result | waiting |
| 8 | Delete raw CSVs once step 2 is verified (re-downloadable; hashes kept) | `../data/enf-ref` empty, `grid_1s_meta.json` holds hashes | pending |
| 9 | Dev + calibration audio harvest, extractor v2 tuned on dev, frozen | `data/harvest_manifest.csv`, git tag `extractor-v2` | pending |
| 10 | Shrey approves the PROTOCOL.md commit (pre-registration) | git commit "Pre-registration" | pending (needs Shrey) |
| 11 | Test + negative harvest (resumable manifest) | `data/harvest_manifest.csv` all rows done/failed | pending |
| 12 | Latency calibration, threshold from negatives, blind matching, gate numbers | `results/gate.md` | pending; gate Sat 7 Nov 22:00 IST |
| 13 | Results, Pages explorer, README, v1 video | `results/`, `site/`, `README.md` | pending; Shrey submits v1 by 8 Nov 21:00 |

## Resilience rules for every script
- Write outputs to `name.tmp`, then rename. A half-written file must never look finished.
- Long jobs keep a manifest or append-only CSV and skip finished items on restart. They delete leftover partial media when they start.
- Run long jobs as standalone processes that log to `logs/<step>.log`, so they don't depend on any one agent or session.
- Make a local git commit after each finished step (Claude does this, not parallel agents).
