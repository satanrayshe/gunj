# Gunj build state (read this first after any restart)

Last updated: 7 Nov 2026, 22:45 IST, by the coordinator session.

If the laptop was shut down or the session ended, a new coding session should:
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
| 1 | Reference files downloaded and verified | hashes in `../data/derived/grid_1s_meta.json` | DONE 7 Nov 00:50 |
| 2 | Reference series + gap map | `../data/derived/grid_1s_f32.npy`, `results/reference_summary.md` | DONE 7 Nov 05:20 (commit 4cd37fd) |
| 3 | Core extractor, matcher, CLI, tests | `gunj/extract.py`, `gunj/match.py`, `pytest` passes | DONE (55 tests) |
| 4 | Metadata-only candidate list | `data/candidates_india.csv`, `data/candidates_negatives.csv` | DONE (424 + 116) |
| 5 | Uniqueness curve (M4, reference only) | `results/uniqueness_table.csv` | DONE |
| 6 | PROTOCOL.md + splits | `PROTOCOL.md`, `data/splits.csv` | DONE, uncommitted until Shrey approves |
| 7 | Hostile review of 2-6 | review notes | review agent failed (network); coordinator checks passed: tests, hashes, no media, no courts |
| 8 | Delete raw CSVs once step 2 is verified (re-downloadable; hashes kept) | `../data/enf-ref` empty; keep `../data/derived/_parsed` (needed for label drift) | DONE 7 Nov 05:20 |
| 9 | Dev + calibration audio harvest, extractor v2 tuned on dev, frozen | `data/harvest_manifest.csv`, git tag `extractor-v2`, `results/v2_tuning.md` | DONE 7 Nov 08:26. Dev 5/5 (FLAC kept in `media/keep`). v2 = candidate B1, tag `extractor-v2` on ef1d27e at 05:55. Calibration 19/20 (hG8oxgWwPYE not available on YouTube). 0 bot checks or 429s; 77 yt-dlp requests in all |
| 10 | Shrey approves the PROTOCOL.md commit (pre-registration) | git commit "Pre-registration" | DONE: Shrey wrote "approved" at 09:05 IST on 7 Nov; commit 2485706 |
| 11 | Test + negative harvest (resumable manifest) | `data/harvest_manifest.csv` all rows done/failed | RUNNING since 09:05 IST on 7 Nov (detached). To restart after a shutdown, run `powershell -NoProfile -ExecutionPolicy Bypass -File scripts
un_stage3.ps1` as a detached process. It follows PROTOCOL section 14 step 3: SYNTHETIC negatives, then threshold-half negatives, then Test in row order with `--cuts`, then report-half negatives. Re-run the same command after any shutdown. Progress is in `logs/run_stage3.log` and `logs/harvest_<step>.log` |
| 12 | Threshold, latency, blind matching, gate numbers | `results/threshold.json`, `results/latency.json`, `results/gate.md` | tau committed 22:35 (beadde1), L = 2 s committed 22:35 (84ac47d), evaluate.py frozen 22:40 (6d010bb). GATE MOVED to Sun 8 Nov 14:00 IST (PROTOCOL section 19, 7 Nov 22:40). Scoring chain running detached since 22:40: practice on Calibration, then the real Test run (`logs/run_eval_chain.ps1`, log `logs/run_eval.log`). Re-run the real scoring hourly with `.venv\Scripts\python.exe scripts\evaluate.py --workers 2` (safe to kill and re-run). The first full run started after 14:10 on 8 Nov writes `results/gate_frozen.json`; commit it with gate.md |
| 13 | Results, Pages explorer, README, v1 video | `results/`, `site/`, `README.md` | pending; Shrey submits v1 by 8 Nov 21:00 |

## Resilience rules for every script
- Write outputs to `name.tmp`, then rename. A half-written file must never look finished.
- Long jobs keep a manifest or append-only CSV and skip finished items on restart. They delete leftover partial media when they start.
- Run long jobs as standalone processes that log to `logs/<step>.log`, so they don't depend on any one agent or session.
- Make a local git commit after each finished step (the coordinator session does this, never parallel helpers).

## Deviations so far
- 10:00 IST: the Test harvest uses `--order-seed 2026110701`, because `data/splits.csv` rows are in date order (PROTOCOL section 19). The runner that started at 09:05 lacked the flag, so it was stopped at 09:57. Its neg_threshold harvester was left running, and a watcher starts the updated `run_stage3.ps1` when that harvester ends.
- 10:18 to 11:38 IST: the laptop lost its internet connection. Every download failed and a premature tau was written and then deleted (PROTOCOL section 19, 11:50).
- 12:15 IST: harvester fixes from a review (rate-limit wording, private videos, ffmpeg segment skips, network waits, stop after 4 unexplained failures). Outage-charged attempts were given back by `scripts/repair_outage_attempts.py` (PROTOCOL section 19, 12:15; commit 3936ca8). Runner and set_threshold restarted at 12:18 (runner pid 68204 since 16:36 after an exit-5 stop at 14:57, set_threshold pid 58684 since 12:30, on commit 1e26989).
- If set_threshold exits 2 saying a real threshold negative "used up its retries", it is the coordinator's decision: retry that stream, or rerun with `--accept-excluded` and record the decision in PROTOCOL section 19.
- If the harvester exits 5 (4 unexplained failures in a row), read `logs/harvest_<step>.log` before restarting: it may be a new YouTube limit message.

## Before any push (Shrey runs the push)
- `git log --format=%B | grep -ci 'cl[a]ude'` and `git grep -i -l 'cl[a]ude'` must both come back empty, apart from one known false hit: a physicist's name in a lecture title in `data/meta_flat/theory_tifr.json` (YouTube metadata, not attribution). Commits carry no Co-Authored-By trailer.
- `git grep -n -E 'Users[\/]+Shrey|AppData'` must come back empty: no absolute local paths in results files.
- No media in git: `git ls-files | grep -iE '\.(flac|wav|mp3|m4a|webm|mp4|opus)$'` must come back empty.
- On 7 Nov at 08:50 IST the local history was rewritten to remove the attribution trailers and the local paths. The pre-rewrite tip is kept on branch `backup/pre-trailer-strip` and in `refs/original/`; delete both before pushing.
