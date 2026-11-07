# Extractor v2 tuning plan (written before any Dev result)

Written 7 Nov 2026 at 05:33 IST (00:03 UTC). At that time the Dev harvest had started, but no Dev audio had been converted, no trace had been extracted and no search had been run. Nothing below was chosen after seeing a result.

## Data

- The 5 Dev streams in `data/splits.csv` only. Their 1 kHz audio is kept under `media/keep` (not in git).
- The reference series (`../data/derived`, memory-mapped).
- No Calibration, Test or negative audio is used. None exists yet.

## Expected start for a Dev stream

E = release time in IST + D, with D = `label_drift_s` from `data/splits.csv`. L is not known yet (it comes from Calibration), so L = 0 here. To allow for an unknown L, the "true region" is every implied start within E ± 600 s. That is the same ±600 s tolerance PROTOCOL section 6 uses when estimating L.

## Objective J (higher is better)

For each Dev stream and each excerpt length m in {10, 20, 40, 60} min (20 cells):

1. Extract the m-minute excerpt (audio seconds [120, 120 + 60m)) with the candidate.
2. Correlate it with the reference at every position in the ±1-day scope around the release time (the candidate's own correlation: weighted if it uses frame weights, else the frozen matcher).
3. r_true = the highest r whose implied start lies in the true region.
4. r_other = the highest local-peak r (a local peak is the maximum within ±60 s, as in `gunj/match.py`) at least 60 s from r_true's position, anywhere in the ±1-day scope.
5. margin = atanh(r_true) − atanh(r_other). It is positive when the lock nearest the expected time beats every other lock, and it equals the PROTOCOL confidence when that lock is the top candidate.

J = the mean over the 20 cells of the margin clipped to [−1, 1]. The clip stops one very strong stream from deciding alone.

Reported beside J, but not used to choose: the number of cells where the top candidate is in the true region, each top candidate's offset from E, and the 60-min full-scope top candidate for v1 and the chosen v2.

## Candidates (a fixed two-stage search)

Base B0: band ±0.15 Hz per harmonic unit (as v1), harmonic gate 3 (a harmonic whose median peak-to-floor ratio is under 3 is noise only and gets weight 0), per-frame weight max(snr − 1, 0), 5-point running median, no correlation weights.

- Stage A: A1 = B0; A2 = B0 + per-frame weights in the correlation; A3 = B0 with band ±0.30 Hz; A4 = B0 + correlation weights + band ±0.30 Hz.
- Stage B: from the Stage A winner, change one setting at a time: frame weight power 2; running median 1 point (none); running median 9 points; harmonic consensus 0.03 Hz; harmonic gate 2; band ±0.45 Hz if the winner uses ±0.30, otherwise ±0.22 Hz.

The chosen v2 is the candidate with the highest J over both stages. If several candidates are within 0.01 of the best J, the one listed first wins (the simpler one). v1 (frozen, from the harvested traces) and the naive baseline are scored the same way for comparison. If the chosen v2 does not beat v1's J, `results/v2_tuning.md` says so plainly, and the choice of primary extractor goes back to Shrey.
