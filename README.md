# Yap

Free, local, offline voice dictation for Windows — a Wispr Flow-style tool. Hold a hotkey, speak, release: your words are transcribed on your own PC (Whisper on the GPU, CPU fallback) and pasted into whatever app has focus.

## Run

- **Start menu / Desktop → Yap** — the normal way. No console window; it lives in the system tray (bottom-right, next to the clock). Made by `.venv\Scripts\python create_shortcuts.py`.
- `start.bat` — same app but with a console, handy for debugging.
- Only one copy runs at a time; launching a second is ignored.

Tray icon: green = ready, yellow = loading, red = listening, grey = paused. Right-click for the menu (pause, **Start with Windows**, settings, history, log, quit). Windows 11 hides new tray icons behind the `^` arrow — drag Yap's icon out onto the taskbar, or turn it on under Settings → Personalization → Taskbar → Other system tray icons.

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

## Settings (`config.json`, created on first run; restart to apply)

- `vocabulary` — names/jargon Whisper should recognise
- `replacements` — fix consistent mis-hearings (`"open ai": "OpenAI"`)
- `snippets` — say a phrase, get a block of text (`"my sign off": "Kind regards,\nBailey"`)
- `language` — `en`, `fr`, … or `auto`
- `gpu_model` / `cpu_model` — any faster-whisper model name
- `insert_method` — `paste` (fast) or `type`
- `polish` — optional AI rewrite (grammar, self-corrections, tone) using a free local LLM through [Ollama](https://ollama.com): install it, `ollama pull llama3.2:3b`, set `"enabled": true`

History of every dictation is in `data/history.jsonl`.

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
