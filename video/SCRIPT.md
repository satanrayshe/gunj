# Demo video: shot list and voiceover (1:54)

`video/make_video.py` builds the video from the explorer in `site/` and from `video/NARRATION.md`. The start time of each narration line sets when its shot begins, and the spoken words are burned in as captions, so the pictures, the captions and the voice share one timeline.

The voiceover text after each "VO" below is copied from `video/NARRATION.md` by `scripts/fill_results.py`. The same script checks every spoken number against the results files; the table in `video/NARRATION.md` names the file behind each number. To change a line, edit `video/NARRATION.md` and run `scripts/fill_results.py`.

## Production

- Frames: screenshots of the explorer at 1920 by 1080, taken with one browser at a time (`python video/make_video.py frames`). Crossfades, captions and encoding follow (`python video/make_video.py assemble`) and write `video/gunj-v1-silent.mp4`.
- Narration: `video/tts_narration.py` speaks the lines of `video/NARRATION.md` with a synthetic voice at their start times, or Shrey records them in one take or one line at a time (`video/RECORDING.md`; `video/place_voice.py` places separately recorded lines at their start times). `video/mux.ps1` puts the track under the picture and writes `video/gunj-v1.mp4`. It refuses a narration file older than `video/NARRATION.md`, which would speak earlier lines under the current captions; `python video/make_video.py check` names any such file or narrated cut.
- No audio or video of any stream is used (PROTOCOL section 17). The only sound is the narration.
- Channel names are hidden in every frame. Video IDs and venue types are shown.
- The explorer's only motion is the slide-and-lock of the trace. Colours are those of the site: amber for the recording's trace, cool grey for the reference, green only for a candidate within 30 s of the expected start.
- The two Test streams shown are named in `video/make_video.py`: `LOCK_ID`, a blind lock, and `NV_ID`, a stream without hum. `scripts/fill_results.py` checks both against `results/test_results.csv`.

## Shots

**Line 1, 0:01 to 0:06. Title.**
- Screen: the explorer's overview: the title, the summary of the blind test, and the example stream waiting in its start state.
- VO: <!-- vo-1:start -->This is Gunj. It dates Indian recordings from mains hum.<!-- vo-1:end -->

**Line 2, 0:06 to 0:16. The hum.**
- Screen: the example recording's trace in amber against the reference in grey.
- VO: <!-- vo-2:start -->Many recordings carry a faint mains hum. Its pitch wanders with the load, the same way across India's one grid.<!-- vo-2:end -->

**Line 3, 0:16 to 0:26. The search.**
- Screen: the trace slides along the reference and locks.
- VO: <!-- vo-3:start -->Gunj reads that pitch each second and slides it along thirty-two months of public grid records, about eighty-four million seconds.<!-- vo-3:end -->

**Line 4, 0:26 to 0:36. The lock, and what kind of example it is.**
- Screen: the lock card with its candidates and margin, then the badge that names the example's split.
- VO: <!-- vo-4:start -->It fits one moment, well clear of the next best. This example is a calibration stream, not a blind result.<!-- vo-4:end -->

**Line 5, 0:36 to 0:48. The blind test.**
- Screen: the Test results panel of the overview, then its count of streams processed.
- VO: <!-- vo-5:start -->The test was pre-registered and blind. A YouTube bot check stopped downloads after a hundred and twenty-six of a hundred and fifty streams.<!-- vo-5:end -->

**Line 6, 0:48 to 1:03. How often it works, and how far off the confident verdicts are.**
- Screen: the Test results table. The highlight is on the primary endpoint first and moves to the confident verdicts when the breakdown starts.
- VO: <!-- vo-6:start -->Ninety had an hour of audio. For twenty-five, Gunj's best guess was within thirty seconds. Twenty-three verdicts were confident. Fourteen were within thirty seconds and nine were wrong.<!-- vo-6:end -->

**Line 7, 1:03 to 1:16. The reference's own clock drifts.**
- Screen: the label-drift plot, then a callout with the gate counts with and without the drift correction.
- VO: <!-- vo-7:start -->The grid dataset's own clock drifts, by up to about five minutes. Correcting it matters: thirty-eight test streams lock with it, nine without.<!-- vo-7:end -->

**Line 8, 1:16 to 1:25. How much audio a unique lock needs.**
- Screen: the uniqueness section, labelled SYNTHETIC noise on real reference data.
- VO: <!-- vo-8:start -->In a simulation with SYNTHETIC noise, read at the measured level, unique locks needed twenty to forty minutes.<!-- vo-8:end -->

**Line 9, 1:25 to 1:34. A blind Test lock.**
- Screen: the clip page of the lock example: its verdict line with the public start time and the drift correction D, then its trace.
- VO: <!-- vo-9:start -->This test stream locked blind, eight seconds from its expected start, after almost five minutes of drift correction.<!-- vo-9:end -->

**Line 10, 1:34 to 1:47. No verdict, and the false-match check.**
- Screen: the clip page of the stream without hum: its verdict line, then its Hum panel. When the last sentence starts, the overview's false-match rows for real and SYNTHETIC negatives.
- VO: <!-- vo-10:start -->This stream has no hum, so no verdict. Gunj never reports a place. On sixteen hour-long recordings from other grids, it gave one false match.<!-- vo-10:end -->

**Line 11, 1:47 to 1:54. End card.**
- Screen: the repo address, "Shrey Sati, Threws Odyssey 2026", the credit for the grid data (Mukherjee, Kalita and Kumar, Mendeley Data bvwh9d6227 v4, CC BY 4.0), and the notes that no stream audio or video is used and that simulated results are labelled SYNTHETIC.
- VO: <!-- vo-11:start -->The code, the plan and every result are on GitHub.<!-- vo-11:end -->

## Captions and the highlight

`video/make_video.py` splits each line into captions at sentence ends, splits a sentence longer than 100 characters at a comma, semicolon or colon near its middle, and joins neighbours that fit in 64 characters together. Some shots change their highlight when a particular caption of their line starts (the `ch(line, k)` calls in `video/make_video.py`). A rewrite of such a line has to keep at least that many captions, in an order that still matches the pictures.

## Left out of the video, and why

- **Stream audio at the open.** No audio of any stream is published, so the video opens silent.
- **Edit detection (M5).** M5 is scored on SYNTHETIC cuts and reported in the README. The explorer has no edit panel, so the video does not show it.
- **The live station (M6).** The station did not run.
- **The EXPLORATORY analyses.** They are post hoc and are reported in the README only.

## Length

The narration has <!-- narration-words:start -->215<!-- narration-words:end --> words for a video of 1:54. <!-- narration-pace:start -->At two words a second every line ends before the next one starts; the tightest is line 1, 10 words in 5 s (2.0 words a second)<!-- narration-pace:end -->.
