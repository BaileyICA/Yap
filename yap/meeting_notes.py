"""Turn saved meeting audio into speaker-labelled transcript and reviewable notes."""

import difflib
import json
import os
import re
import urllib.request

import numpy as np

from .meetings import read_wav, save_meeting
from .speakers import assign_speakers


def _stamp(seconds):
    whole = max(0, int(seconds))
    return f"{whole // 60:02d}:{whole % 60:02d}"


def _transcribe_track(model, audio, source, cfg, beam_size):
    if len(audio) < 16000 or float(np.sqrt(np.mean(audio ** 2))) < 0.001:
        return []
    language = cfg.get("language", "en")
    segments, _ = model.transcribe(
        audio, language=None if language == "auto" else language,
        beam_size=beam_size,
        vad_filter=True, vad_parameters={"min_silence_duration_ms": 500},
        condition_on_previous_text=False, word_timestamps=True,
        initial_prompt=", ".join(cfg.get("vocabulary", [])) or None,
    )
    words = []
    for segment in segments:
        if segment.words:
            for w in segment.words:
                if w.word.strip():
                    words.append((max(0.0, w.start), max(w.start, w.end), w.word))
        elif segment.text.strip():
            words.append((segment.start, segment.end, segment.text))
    turns = []
    group = []
    for word in words:
        if group and (word[0] - group[-1][1] > 0.7 or word[1] - group[0][0] > 4.5):
            turns.append(_turn(group, source))
            group = []
        group.append(word)
    if group:
        turns.append(_turn(group, source))
    return turns


def _turn(words, source):
    return {"start": round(words[0][0], 2), "end": round(words[-1][1], 2),
            "text": "".join(w[2] for w in words).strip(), "source": source}


def _deduplicate(turns):
    """Remove audio picked up on both the mic and the computer loopback."""
    kept = []
    for turn in sorted(turns, key=lambda x: (x["start"], x["source"] != "computer")):
        normalized = re.sub(r"\W+", "", turn["text"].lower())
        duplicate = False
        for prior in kept[-8:]:
            if prior["source"] == turn["source"] or abs(prior["start"] - turn["start"]) > 3:
                continue
            other = re.sub(r"\W+", "", prior["text"].lower())
            if normalized and other and difflib.SequenceMatcher(None, normalized, other).ratio() > 0.78:
                duplicate = True
                break
        if not duplicate:
            kept.append(turn)
    return sorted(kept, key=lambda x: x["start"])


def transcript_text(turns, names=None):
    names = names or {}
    return "\n".join(f"[{_stamp(t['start'])}] {names.get(t['speaker'], t['speaker'])}: {t['text']}" for t in turns)


def _ask_ollama(transcript, cfg):
    meeting_cfg = cfg.get("meeting_notes", {})
    url = meeting_cfg.get("ollama_url", cfg["polish"]["url"]).rstrip("/") + "/api/chat"
    model = meeting_cfg.get("ollama_model", "llama3.2:3b")
    system = (
        "You write faithful meeting notes from a speaker-labelled transcript. "
        "Use only facts present in the transcript. Preserve exact speaker labels and timestamps when possible. "
        "Do not invent names, decisions, dates, or owners. If uncertain, say so. "
        "Return Markdown with these headings: Summary, Key points, Decisions, Action items, Open questions. "
        "Every bullet under Key points, Decisions, Action items, and Open questions must start with "
        "the exact source timestamp and speaker label, for example '- [02:14] Speaker 2: ...'. "
        "For action items state owner and due date only when explicitly said. Keep substantive details."
    )
    body = json.dumps({"model": model, "stream": False, "messages": [
        {"role": "system", "content": system}, {"role": "user", "content": transcript}],
        "options": {"temperature": 0.1, "num_ctx": 8192}}).encode()
    request = urllib.request.Request(url, body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=meeting_cfg.get("timeout_seconds", 300)) as response:
        result = json.load(response)["message"]["content"].strip()
    if not result:
        raise RuntimeError("The local notes model returned no text.")
    return result


def _ground_bullets(notes, turns):
    """Tie model bullets to a transcript turn when its source is clear."""
    def words(text):
        return set(re.findall(r"[a-z0-9']+", text.lower())) - {
            "the", "a", "an", "to", "of", "and", "for", "it", "is", "will", "be", "we", "i"}

    grounded = []
    valid_anchors = {(_stamp(t["start"]), t["speaker"]) for t in turns}
    for line in notes.splitlines():
        match = re.match(r"^(\s*[-*]\s+)(.*)$", line)
        if not match:
            grounded.append(line)
            continue
        content = match[2]
        anchor = re.match(r"\[(\d{2}:\d{2})\]\s+(Speaker \d+|Unclear speaker):\s*", content)
        if anchor and (anchor[1], anchor[2]) in valid_anchors:
            grounded.append(line)
            continue
        if anchor:
            content = content[anchor.end():]
        terms = words(content)
        best = None
        score = 0
        for turn in turns:
            source = words(turn["text"])
            overlap = len(terms & source) / max(1, len(terms))
            fuzzy = difflib.SequenceMatcher(None, content.lower(), turn["text"].lower()).ratio()
            candidate = max(overlap, fuzzy)
            if candidate > score:
                best, score = turn, candidate
        prefix = (f"[{_stamp(best['start'])}] {best['speaker']}: " if best and score >= 0.55
                  else "[Source unclear] ")
        grounded.append(match[1] + prefix + content)
    return "\n".join(grounded)


def _basic_notes(turns):
    if not turns:
        return "# Summary\n\nNo clear speech was detected. Check the saved audio.\n"

    def item(turn):
        return f"- [{_stamp(turn['start'])}] {turn['speaker']}: {turn['text']}"

    decision = re.compile(r"\b(decid(?:e|ed)|agree(?:d)?|settled|approved|go with|chosen|final decision)\b", re.I)
    action = re.compile(r"\b(i will|we will|we need to|need to|will send|will do|action item|follow up|by (?:monday|tuesday|wednesday|thursday|friday|tomorrow|next week))\b", re.I)
    question = re.compile(r"\?|\b(what do we|how do we|when will|who will|can you|could you|should we)\b", re.I)
    decisions = [t for t in turns if decision.search(t["text"])]
    actions = [t for t in turns if action.search(t["text"])]
    questions = [t for t in turns if question.search(t["text"])]
    candidates = [t for t in turns if len(t["text"].split()) >= 6]
    scored = sorted(candidates, key=lambda t: (3 * bool(decision.search(t["text"])) +
                                              2 * bool(action.search(t["text"])) +
                                              bool(question.search(t["text"])) +
                                              min(len(t["text"].split()), 25) / 25), reverse=True)
    highlights = sorted(scored[:20], key=lambda t: t["start"])
    duration = _stamp(max(t["end"] for t in turns))
    speakers = len({t["speaker"] for t in turns if t["speaker"] != "Unclear speaker"})
    lines = ["# Summary", "", f"{duration} of transcribed audio with {speakers} estimated speaker(s).",
             "Detailed AI synthesis needs Ollama; these are transcript-based highlights for review.",
             "", "# Key points", ""]
    lines += [item(t) for t in highlights] or ["- No substantial speech detected."]
    for heading, found in (("Possible decisions", decisions), ("Possible action items", actions),
                           ("Open questions", questions)):
        lines += ["", f"# {heading}", ""]
        lines += [item(t) for t in found[:20]] or ["- None clearly stated in the transcript."]
    return "\n".join(lines)


def generate_notes(turns, cfg, log=print):
    if not turns:
        return _basic_notes(turns), "basic"
    if cfg.get("meeting_notes", {}).get("use_ollama", True):
        try:
            return _ground_bullets(_ask_ollama(transcript_text(turns), cfg), turns), "ollama"
        except Exception as exc:
            log(f"Local AI notes unavailable: {exc}")
    return _basic_notes(turns), "basic"


def process_meeting(folder, model, cfg, progress=print, log=print, beam_size=1):
    with open(os.path.join(folder, "meeting.json"), encoding="utf-8") as f:
        data = json.load(f)
    try:
        tracks = {}
        turns = []
        for source, filename in (("microphone", "microphone.wav"), ("computer", "computer.wav")):
            path = os.path.join(folder, filename)
            if not os.path.exists(path):
                continue
            progress(f"Transcribing {source} audio...")
            audio = read_wav(path)
            tracks[source] = audio
            source_turns = _transcribe_track(model, audio, source, cfg, beam_size)
            offset = data.get("computer_offset", 0) if source == "computer" else 0
            for turn in source_turns:
                turn["start"] = max(0, round(turn["start"] + offset, 2))
                turn["end"] = max(turn["start"], round(turn["end"] + offset, 2))
            turns.extend(source_turns)
        turns = _deduplicate(turns)
        progress("Matching speakers...")
        turns, attribution = assign_speakers(turns, tracks, log,
                                             {"computer": data.get("computer_offset", 0)})
        progress("Writing meeting notes...")
        notes, notes_engine = generate_notes(turns, cfg, log)
        data.update({"status": "ready", "turns": turns, "speaker_names": {}, "attribution": attribution,
                     "notes": notes, "notes_engine": notes_engine})
        save_meeting(folder, data)
        with open(os.path.join(folder, "transcript.txt"), "w", encoding="utf-8") as f:
            f.write(transcript_text(turns) + "\n")
        with open(os.path.join(folder, "notes.md"), "w", encoding="utf-8") as f:
            f.write(notes + "\n")
        progress("Meeting notes ready")
        return data
    except Exception as exc:
        data["status"] = "error"
        data["error"] = f"{type(exc).__name__}: {exc}"
        save_meeting(folder, data)
        raise
