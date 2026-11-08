# Recording the v1 narration

The silent video is `video/gunj-v1-silent.mp4` (1:54). The script is `video/NARRATION.md`; the same words appear as captions in the video.

1. **Find a quiet room.** Shut the door and windows and switch off fans or AC for the take. Unplug the laptop charger while recording if the battery allows: charger hum is exactly the sound Gunj listens for.
2. **Set up the mic.** Use your phone's voice recorder or the laptop's Sound Recorder app. Keep the mic about a hand's width from your mouth, slightly to one side. Record five seconds, play it back, and check there is no hiss or echo.
3. **Open the video and the script.** Play `video/gunj-v1-silent.mp4` in any player at normal speed, full screen if you like. Keep `video/NARRATION.md` open on paper or a second screen if that helps; otherwise read the captions.
4. **Record in one take.** Start the recorder, then press play on the video straight away. Say nothing until the first caption appears at 0:01. Read each line when its caption appears, at a calm pace, and finish before the next caption. If you stumble, stop and start again from the top; it is under two minutes.
5. **Save and copy.** Stop after the end card. Save as wav, m4a or mp3 and copy the file to the laptop. Audio files are ignored by git, so it will not be committed by accident.

Then, in PowerShell from the repo folder:

```
powershell -ExecutionPolicy Bypass -File video\mux.ps1 "path\to\narration.m4a"
```

This writes `video/gunj-v1.mp4`: the narration loudness-normalised, starting at 0 s, cut or padded to the video's length. Watch it once to check that your voice matches the captions. If your first word comes later than 1 s into the recording, cut the extra lead with `-TrimStart`; for example, if you start speaking at 1.8 s:

```
powershell -ExecutionPolicy Bypass -File video\mux.ps1 "path\to\narration.m4a" -TrimStart 0.8
```

If the script warns that the narration is longer than the video, the end of the last line was cut: re-record a little faster.
