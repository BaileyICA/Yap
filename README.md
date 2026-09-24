# Yap

Free, local, offline voice dictation for Windows — a Wispr Flow-style tool. Hold a hotkey, speak, release: your words are transcribed on your own PC (NVIDIA Parakeet on the GPU, CPU fallback; Whisper optional) and pasted into whatever app has focus.

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

First run downloads the speech model once (Parakeet: ~2.5 GB for the GPU, ~650 MB int8 for CPU); after that nothing leaves your PC.

To use an NVIDIA GPU when running from source, swap ONNX Runtime for its CUDA 12 build:

```
.venv\Scripts\python -m pip uninstall -y onnxruntime
.venv\Scripts\python -m pip install -r requirements-gpu.txt
```

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

Open **Yap → Settings** to choose the speech engine (Parakeet or Whisper) and the Whisper models. Engine and model changes take effect after restarting Yap. Advanced settings remain available in `config.json` (created on first run; restart to apply).

- `engine` — `parakeet` (default; fastest and most accurate for English) or `whisper` (other languages)
- `parakeet_model` — `nemo-parakeet-tdt-0.6b-v2` (English) or `nemo-parakeet-tdt-0.6b-v3` (25 European languages)
- `vocabulary` — names/jargon to spell your way. Near-misses in a transcript are snapped to these (`Greymont` → `Graymont`, `Lamin-X` → `Laminex`); Whisper also uses them as a hint
- `numbers_as_digits` — write spoken numbers as digits (`the fourteenth` → `the 14th`, `four and a half percent` → `4.5%`, `twenty twenty six` → `2026`); numbers under ten stay words unless they carry a unit (`5 pm`, `3%`)
- `replacements` — fix consistent mis-hearings (`"open ai": "OpenAI"`)
- `snippets` — say a phrase, get a block of text (`"my sign off": "Kind regards,\nBailey"`)
- `language` — `en`, `fr`, … or `auto`
- `gpu_model` / `cpu_model` — any faster-whisper model name (Whisper engine only)
- `insert_method` — `paste` (fast) or `type`
- `overlay_style` — speaking visual: `wave`, `bars`, `orb`, or `dots`
- `polish` — optional AI rewrite (grammar, self-corrections, tone) using a free local LLM through [Ollama](https://ollama.com): install it, `ollama pull llama3.2:3b`, set `"enabled": true`

History of every dictation is in `data/history.jsonl`.

## Teaching Yap your words

**Settings → Dictionary** lists your vocabulary and learned fixes; add or remove entries there and they apply to the next dictation. To teach Yap from a real mistake, open **History**, click the pencil on a dictation, and correct it. Yap compares your version with what it wrote and offers each changed word as a fix (`Taze → Teys`); names are added to the vocabulary too, so similar mis-hearings get caught as well. Swaps of everyday words (`their → there`) are left unticked, since those are usually grammar edits rather than mis-hearings. Neither speech model is retrained: Yap applies your dictionary to the text after transcription, for dictation and meeting transcripts alike.

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

Parakeet runs from `.venv` (or `.venv-parakeet` if present), with models in `models/`. On the GPU it needs ONNX Runtime **1.24.4** (the last CUDA 12 build; see `requirements-gpu.txt`); newer ONNX Runtime wants CUDA 13. The bench scores the raw model output, before Yap's dictionary is applied.

## Notes

- Some elevated (admin) windows ignore synthetic paste unless Yap is also run as admin.
- The overlay pill never takes focus, so your cursor stays where it was.
