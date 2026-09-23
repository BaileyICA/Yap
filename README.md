# Yap

Free, local, offline voice dictation for Windows — a Wispr Flow-style tool. Hold a hotkey, speak, release: your words are transcribed on your own PC (Whisper on the GPU, CPU fallback) and pasted into whatever app has focus.

## Run

### Download the Windows app

Open the repository's **Releases** page, download `Yap-Windows-x64.zip`, extract it, and run `Yap.exe`. Windows may show a SmartScreen prompt because the app is not code-signed; choose **More info → Run anyway** if you trust the download. The app stores settings, logs, history, meetings, and downloaded speaker models in `%LOCALAPPDATA%\Yap`. The speech model downloads on first launch, so allow time and disk space for that download.

Every push to `main` also builds a downloadable ZIP under the GitHub Actions run's **Artifacts**. To publish a version under **Releases**, push a version tag such as `v1.0.0`; the workflow attaches the ZIP to that release.

### Run from source

- **Start menu / Desktop → Yap** — opens the Yap window and keeps dictation in the system tray (bottom-right, next to the clock). Made by `.venv\Scripts\python create_shortcuts.py`.
- **Bottom taskbar → dog icon** — pin the Yap desktop shortcut once and it stays available even when the window is closed.
- `start.bat` — same app but with a console, handy for debugging.
- Only one copy runs at a time; launching the shortcut again brings the existing window forward.

Tray icon: green = ready, yellow = loading/processing, red = listening or meeting recording, grey = paused. Right-click for the menu (Open Yap, Record a meeting, pause, **Start with Windows**, settings, history, log, quit). Windows 11 hides new tray icons behind the `^` arrow — drag Yap's icon out onto the taskbar, or turn it on under Settings → Personalization → Taskbar → Other system tray icons.

Errors are written to `data/yap.log`.

First run downloads the speech model (~1.6 GB for `large-v3-turbo`) once; after that nothing leaves your PC.

## Use

| Action | Default |
|---|---|
| Dictate (hold) | `Ctrl + Win` |
| Hands-free start | **Double-tap** `Ctrl + Win` |
| Hands-free stop | **Double-tap** `Ctrl + Win` again |
| Hands-free (alternative) | `Ctrl + Alt + Space` toggles it |
| Cancel | `Esc` while recording |

Voice commands: "new line", "new paragraph", "scratch that" (drops what you just said in that sentence).

## Settings

Open **Yap → Settings** to choose the GPU speech model and CPU fallback model. Model changes are saved locally and take effect after restarting Yap. Advanced settings remain available in `config.json` (created on first run; restart to apply).

- `vocabulary` — names/jargon Whisper should recognise
- `replacements` — fix consistent mis-hearings (`"open ai": "OpenAI"`)
- `snippets` — say a phrase, get a block of text (`"my sign off": "Kind regards,\nBailey"`)
- `language` — `en`, `fr`, … or `auto`
- `gpu_model` / `cpu_model` — any faster-whisper model name
- `insert_method` — `paste` (fast) or `type`
- `overlay_style` — speaking visual: `wave`, `bars`, `orb`, or `dots`
- `polish` — optional AI rewrite (grammar, self-corrections, tone) using a free local LLM through [Ollama](https://ollama.com): install it, `ollama pull llama3.2:3b`, set `"enabled": true`

History of every dictation is in `data/history.jsonl`.

## Meeting notes

Open **Yap → Meetings**, enter an optional title, and click **Start recording**. Yap records your microphone and, when checked, your default Windows speaker output (for Zoom/Teams/browser calls). Click **Stop & process** when the meeting ends. Ten minutes of audio is fine; transcription and note generation happen afterward and may take several minutes, especially on CPU.

Each meeting stays in `data/meetings/<timestamp>/` with separate microphone and computer WAV files, `transcript.txt`, `notes.md`, and `meeting.json`. Yap attempts to group recurring voices as **Speaker 1**, **Speaker 2**, etc. It cannot infer people's real names from their voices; rename the speakers in the meeting detail screen, review the transcript, and save your edits. Overlapping voices, speakerphone echo, and short turns can affect attribution.

For detailed notes covering key points, decisions, action items, and open questions, install [Ollama](https://ollama.com), run `ollama pull llama3.2:3b`, and leave Ollama running. Yap will then use it locally after transcription. Without Ollama, Yap still saves the timestamped transcript and a basic highlights page. The speaker model (~26 MB) downloads once, then runs locally. A meeting left in `processing` after quitting is resumed on the next launch.

On a fresh environment, install Python dependencies with `.venv\Scripts\python -m pip install -r requirements.txt`. The dashboard and meeting capture use the same `python -m yap` launcher as dictation. Auto-start remains tray-only; opening the desktop shortcut shows the window.

## Comparing speech models (`bench.py`)

Scores Whisper turbo, Whisper large-v3 and NVIDIA Parakeet on the same audio against a reference paragraph (word error rate, plus the exact words each got wrong).

```
.venv\Scripts\python bench.py              # shows the paragraph, records 15 s, runs everything
.venv\Scripts\python bench.py --reuse      # re-run on your last recording
.venv\Scripts\python bench.py --wav x.wav  # any WAV (converted to 16 kHz mono automatically)
```

Parakeet lives in its own environment (`.venv-parakeet`, models in `models/`) so it can't disturb the app. It needs ONNX Runtime **1.24.4** (the last CUDA 12 build) to reuse the CUDA libraries in `.venv`; newer ONNX Runtime wants CUDA 13. It is not wired into Yap itself yet: `bench.py` only measures it.

## Notes

- Some elevated (admin) windows ignore synthetic paste unless Yap is also run as admin.
- The overlay pill never takes focus, so your cursor stays where it was.
