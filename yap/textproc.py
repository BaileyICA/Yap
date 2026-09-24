import difflib
import json
import re
import urllib.request

_FILLERS = re.compile(r"\b(?:u+m+|u+h+|e+r+m*|a+h+m*|h+m+|m+h*m+)\b[,.]?\s*", re.IGNORECASE)
_SCRATCH = re.compile(r"(?:[^.!?\n]*?[,;]?\s*)\b(?:scratch that|strike that|delete that)\b[.,!?]?\s*", re.IGNORECASE)
_COMMANDS = [
    (re.compile(r"[,.]?\s*\bnew paragraph\b[.,]?\s*", re.IGNORECASE), "\n\n"),
    (re.compile(r"[,.]?\s*\b(?:new line|newline|next line)\b[.,]?\s*", re.IGNORECASE), "\n"),
]

# A dictation that opens with this removes the previous dictation: "Scratch that." / "Scratch that, meet Friday."
_UNDO = re.compile(r"^\W*(?:scratch|strike|delete|undo)\s+that\b[\s.,;:!?-]*", re.IGNORECASE)


def undo_command(text):
    """(True, what's left to type) if the dictation starts with "scratch that", else (False, text)."""
    m = _UNDO.match(text)
    return (True, text[m.end():]) if m else (False, text)


# "vanilla, no wait, actually chocolate": the cue plus an optional softener after it.
# Loose cues ("make that", "I meant"...) only count after punctuation so ordinary prose survives.
_CORRECTION = re.compile(
    r"(?:[,;:.!?]?\s*\b(?:no,?\s+wait|wait,?\s+no)|[,;:.!?]\s*\b(?:no,?\s+no|actually,?\s+no|no\s+actually|or\s+rather|make\s+that|i\s+meant))"
    r"\b[,.!?]?\s*(?:(?:actually|i\s+mean|make\s+(?:it|that))\b,?\s*)?",
    re.IGNORECASE,
)
_BOUNDARY = re.compile(r"[,;:.!?\n]")
_SENTENCE_END = re.compile(r"[.!?\n]")
_LIST_NAME = r"(?:\b(to[- ]?do|[a-z]+)\s+)?\blist\b"
_NOT_A_NAME = {"a", "an", "the", "my", "our", "your", "this", "that", "new", "same", "whole", "of", "to", "on"}
# "grocery list, could you add apples, ..." / "shopping list: eggs, ..."
_LIST_FIRST = re.compile(
    _LIST_NAME + r"[\s,:;-]*(?:(?:(?:could|can|would|will)\s+you|please|and|i\s+need\s+to|we\s+need\s+to|let'?s)\s+)*"
    r"(?:(?:add|put|buy|get|grab|pick\s+up|include|write\s+down|with|of|is|are|has)\b[\s,:]*)?",
    re.IGNORECASE,
)
# "add apples, bananas and milk to my shopping list"
_LIST_LAST = re.compile(
    r"\b(?:add|put|write\s+down)\s+([^.!?\n]+?)\s+(?:to|on(?:to)?)\s+(?:the|my|our|a)\s+" + _LIST_NAME,
    re.IGNORECASE,
)


def _apply_corrections(text):
    """Swap the phrase before a correction cue for what follows it."""
    in_list = re.search(r"\blist\b", text, re.IGNORECASE) is not None
    pos = 0
    while m := _CORRECTION.search(text, pos):
        before, after = text[:m.start()], text[m.end():]
        cut = max((b.end() for b in _BOUNDARY.finditer(before)), default=0)
        seg = before[cut:]
        words = seg.split()
        if not words or not after.strip():
            pos = m.end()  # nothing to correct, or nothing to correct it with
            continue
        lead = seg[:len(seg) - len(seg.lstrip())]
        # In a list the whole item is replaced. In prose, line the fix up with the words it
        # shares ("at 5 pm, no wait, 6 pm" -> "at 6 pm"), else swap just the last word
        # ("meet on tuesday, no wait, wednesday" -> "meet on wednesday").
        keep = 0 if in_list else _align(words, _BOUNDARY.split(after, 1)[0].split())
        head = before[:cut] + lead + (" ".join(words[:keep]) + " " if keep else "")
        text = head + after
        pos = len(head)
    return text


def _align(words, fix):
    """How many leading words of `words` survive being corrected by `fix`."""
    norm = lambda w: w.lower().strip(".,!?'\"")  # noqa: E731
    for k, w in enumerate(fix):
        for j in range(len(words) - 1, -1, -1):
            if norm(words[j]) == norm(w):
                return max(j - k, 0)
    return len(words) - 1


def _list_title(name):
    if not name or name.lower() in _NOT_A_NAME:
        return "List"
    name = re.sub(r"(?i)^to[- ]?do$", "To-do", name)
    return name[0].upper() + name[1:].lower() + " list"


def _split_items(body):
    items = [i.strip(" .!?-") for i in re.split(r"\s*[,;]\s*", body)]
    items = [re.sub(r"(?i)^(?:and|or|also|plus|then)\s+", "", i) for i in items if i]
    if len(items) >= 2 and " and " in items[-1]:
        items[-1:] = items[-1].rsplit(" and ", 1)  # "apples, bananas and milk"
    elif len(items) == 1 and " and " in items[0]:
        items = items[0].rsplit(" and ", 1)
    items = [i.strip() for i in items if i.strip()]
    if len(items) < 2 or any(len(i.split()) > 6 for i in items):
        return None  # reads like prose, not a list
    return [i[0].upper() + i[1:] for i in items]


def format_list(text):
    """Turn a spoken list ("grocery list, add apples, bananas and milk") into bullet points."""
    for m in _LIST_FIRST.finditer(text):
        end = _SENTENCE_END.search(text, m.end())
        end = end.start() if end else len(text)
        items = _split_items(text[m.end():end])
        if items:
            return _join_list(text, m.start(), end, _list_title(m.group(1)), items)
    m = _LIST_LAST.search(text)
    if m:
        items = _split_items(m.group(1))
        if items:
            return _join_list(text, m.start(), m.end(), _list_title(m.group(2)), items)
    return text


def _join_list(text, start, end, title, items):
    # The lead-in sentence ("so could you make a grocery list") becomes the title.
    starts = [s.end() for s in _SENTENCE_END.finditer(text, 0, start)]
    before = text[:starts[-1] if starts else 0].strip()
    after = text[end:].lstrip(" .!?,;").strip()
    block = title + ":\n" + "\n".join("- " + i for i in items)
    return "\n\n".join(p for p in (before, block, after) if p)


def _phrase_regex(phrase):
    return re.compile(r"(?<!\w)" + r"[\s,.-]*".join(map(re.escape, phrase.split())) + r"(?!\w)[.,!?]?", re.IGNORECASE)


def apply_map(text, mapping):
    for phrase, repl in mapping.items():
        text = _phrase_regex(phrase).sub(lambda _m, r=repl: r, text)
    return text


# Everyday words are never snapped to a vocabulary term or suggested as a learned fix:
# "cloud" must stay "cloud" even with "Claude" in the dictionary.
_COMMON = set("""
a about after again all also am an and any are as at back be because been before being but by can come
could day did do does done down each even first for from get give go going good got had has have he her
here him his how i if in into is it its just know last let like look made make many may me more most my
new no not now of off on one only or other our out over people right said same say see she should so
some take tell than that the their them then there these they thing think this those time to too two up
us use very want was way we well went were what when where which while who why will with work would
year yes yet you your
cloud clod code coat cold called cord court crowd flow floor glow grow lamb late light line might mind
more nice note open part plan read real ready rest ride right road say see seem send set side sight
site sort sound start state still stop sure talk team test text than thank then those though told
try turn wait walk wall wash watch water week while white whole word world write wrong
""".split())
_WORD = re.compile(r"[^\W_]+(?:['’][^\W_]+)*")
_JOINER = re.compile(r"[\s-]+")


def _letters(text):
    return re.sub(r"[\W_]+", "", text).lower()


def _sound(text):
    """Rough phonetic key: consonant skeleton after common English spelling swaps."""
    s = _letters(text)
    for a, b in (("sch", "sk"), ("ph", "f"), ("ck", "k"), ("wh", "w"), ("kn", "n"), ("wr", "r"),
                 ("gh", "g"), ("qu", "kw"), ("dg", "j"), ("q", "k"), ("x", "ks"), ("z", "s")):
        s = s.replace(a, b)
    s = re.sub(r"c(?=[eiy])", "s", s).replace("c", "k")
    s = s[:1] + re.sub(r"[aeiouyhw]", "", s[1:])
    return re.sub(r"(.)\1+", r"\1", s)


def _similar(a, b):
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def _vocab_score(heard, term, sentence_start):
    """How sure we are that `heard` is a mis-hearing of vocabulary `term` (0 = not a match)."""
    h, t = _letters(heard), _letters(term)
    if h == t:
        return 1.0  # same letters, different spacing/case: "open AI" -> "OpenAI"
    if len(h) < 4 or not 0.7 <= len(h) / len(t) <= 1.4:
        return 0.0
    if any(c.isupper() for c in term) and not any(c.isupper() for c in heard):
        return 0.0  # speech models capitalise names they don't know; lowercase means an ordinary word
    heard_words = _WORD.findall(heard)
    if all(w.lower() in _COMMON for w in heard_words) or h == t + "s":
        return 0.0
    if len(heard_words) > len(term.split()) and any(w.lower() in _COMMON for w in heard_words):
        return 0.0  # don't swallow a neighbour: "to Sarah" is not a mis-hearing of "Sarah"
    score = (_similar(h, t) + _similar(_sound(heard), _sound(term))) / 2
    # Sentence-initial words are capitalised anyway, so that hint is missing there.
    return score if score >= (0.9 if sentence_start else 0.8) else 0.0


def apply_vocabulary(text, vocabulary):
    """Snap near-miss spellings to the user's vocabulary: "Greymont" -> "Graymont", "Lamin-X" -> "Laminex"."""
    terms = [t.strip() for t in vocabulary if len(_letters(t)) >= 3]
    if not terms:
        return text
    words = list(_WORD.finditer(text))
    out, pos, i = [], 0, 0
    while i < len(words):
        before = text[:words[i].start()].rstrip()
        sentence_start = not before or before[-1] in ".!?\n"
        best = (0.0, 0, None)
        for n in (1, 2, 3):  # a term can be heard as more or fewer words than it has
            span = words[i:i + n]
            if len(span) < n or any(not _JOINER.fullmatch(text[a.end():b.start()]) for a, b in zip(span, span[1:])):
                break
            heard = text[span[0].start():span[-1].end()]
            suffix = re.search(r"['’]s$", heard)
            if suffix:
                heard = heard[:suffix.start()]
            for term in terms:
                score = _vocab_score(heard, term, sentence_start)
                if score > best[0]:
                    best = (score, n, term + (suffix.group() if suffix else ""))
        score, n, replacement = best
        if score:
            out.append(text[pos:words[i].start()] + replacement)
            pos = words[i + n - 1].end()
            i += n
        else:
            i += 1
    return "".join(out) + text[pos:]


def fix_words(text, cfg):
    """The user's dictionary: explicit replacements first, then vocabulary spellings."""
    text = apply_map(text, cfg["replacements"])
    return apply_vocabulary(text, cfg["vocabulary"])


def learn_corrections(before, after):
    """Word swaps between a transcript and the user's fixed version, as candidate replacement rules.

    Returns [{"heard", "written", "suggested"}]; `suggested` is False for swaps of everyday words
    ("their" -> "there"), which are usually grammar fixes rather than mis-hearings.
    """
    a, b = list(_WORD.finditer(before)), list(_WORD.finditer(after))
    matcher = difflib.SequenceMatcher(None, [m.group() for m in a], [m.group() for m in b], autojunk=False)
    rules = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "replace" or i2 - i1 > 3 or j2 - j1 > 3:
            continue
        # Equal-length swaps are word-for-word fixes; learn each word on its own.
        pairs = (zip(range(i1, i2), range(j1, j2)) if i2 - i1 == j2 - j1 else [(i1, j1)])
        for i, j in pairs:
            ie, je = (i + 1, j + 1) if i2 - i1 == j2 - j1 else (i2, j2)
            heard = before[a[i].start():a[ie - 1].end()]
            written = after[b[j].start():b[je - 1].end()]
            everyday = all(m.group().lower() in _COMMON for m in a[i:ie])
            if everyday and heard.lower() == written.lower():
                continue  # just a capital at a new sentence start
            rules.append({"heard": heard, "written": written, "suggested": not everyday})
    return rules


# ---- spoken numbers -> digits ("four and a half percent" -> "4.5%") ----
_UNITS = {w: n for n, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * n for n, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split(), 2)}
_SCALES = {"thousand": 10 ** 3, "million": 10 ** 6, "billion": 10 ** 9, "trillion": 10 ** 12}
_ORDINAL_UNITS = {w: n for n, w in enumerate(
    "first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth thirteenth "
    "fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth".split(), 1)}
_ORDINAL_TENS = {w: 10 * n for n, w in enumerate(
    "twentieth thirtieth fortieth fiftieth sixtieth seventieth eightieth ninetieth".split(), 2)}
_MONTHS = set("january february march april may june july august september october november december".split())
_NUM_WORD = re.compile(r"[A-Za-z]+")
_NUM_GAP = re.compile(r" +|-")
# Under ten stays a word ("one of them", "two options") unless a unit makes it a quantity.
_UNIT_AFTER = re.compile(
    r"\s*(?:%|[ap]\.?\s?m\b|o'clock\b|percent\b|per cent\b|dollars?\b|cents?\b|degrees?\b|kilo\w*|"
    r"km\b|kms\b|kg\b|kgs\b|mph\b|met(?:re|er)s?\b|centimet\w+|millimet\w+|cm\b|mm\b|grams?\b|"
    r"lit(?:re|er)s?\b|ml\b|[kmgt]b\b|[kmg]?hz\b)", re.IGNORECASE)


def _read_number(words, i, text):
    """Parse one spoken number starting at words[i]. Returns (value, next index, ordinal, scale word)."""
    total = group = 0
    last = scale = None
    ordinal = False
    j = i
    while j < len(words) and not ordinal:
        if j > i and not _NUM_GAP.fullmatch(text[words[j - 1].end():words[j].start()]):
            break
        w = words[j].group().lower()
        after_group = last in (None, "hundred", "scale", "and")
        if (w in _UNITS or w in _ORDINAL_UNITS) and (after_group or (last == "tens" and _UNITS.get(w, _ORDINAL_UNITS.get(w)) < 10)):
            ordinal = w in _ORDINAL_UNITS
            n = _ORDINAL_UNITS[w] if ordinal else _UNITS[w]
            group += n
            last = "unit"
        elif (w in _TENS or w in _ORDINAL_TENS) and after_group:
            ordinal = w in _ORDINAL_TENS
            group += _ORDINAL_TENS[w] if ordinal else _TENS[w]
            last = "tens"
        elif w in ("hundred", "hundredth") and last in ("unit", "tens") and 0 < group < 100:
            group *= 100
            ordinal, last = w == "hundredth", "hundred"
        elif w.rstrip("th") in _SCALES and last in ("unit", "tens", "hundred") and group:
            ordinal = w.endswith("th")
            total, group, last, scale = total + group * _SCALES[w.rstrip("th")], 0, "scale", w.rstrip("th")
        elif w == "and" and last in ("hundred", "scale") and j + 1 < len(words) and \
                (words[j + 1].group().lower() in _UNITS or words[j + 1].group().lower() in _TENS):
            last = "and"  # "one hundred and five"
        else:
            break
        j += 1
    if last == "and":
        j -= 1
    return total + group, j, ordinal, scale


def _ordinal(n):
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _read_cents(words, text, at):
    """(cents, end) for " and fifty cents" starting at text position `at`, else None."""
    lead = re.match(r",? and ", text[at:], re.I)
    if not lead:
        return None
    k = next((n for n, w in enumerate(words) if w.start() == at + lead.end()), None)
    if k is None:
        return None
    value, j, ordinal, scale = _read_number(words, k, text)
    tail = re.match(r" cents?\b", text[words[j - 1].end():], re.I) if j > k else None
    if not tail or ordinal or scale or not 1 <= value <= 99:
        return None
    return value, words[j - 1].end() + tail.end()


def format_numbers(text):
    """Write spoken numbers as digits the way Whisper does: 14th, 4.5%, $20, 2026, 3 pm."""
    words = list(_NUM_WORD.finditer(text))
    out, pos, i = [], 0, 0
    while i < len(words):
        value, j, ordinal, scale = _read_number(words, i, text)
        if j == i:
            i += 1
            continue
        start, end = words[i].start(), words[j - 1].end()
        rest = text[end:]
        fraction = ""
        spoken = j - i
        if not ordinal:
            m = re.match(r"( point((?: (?:zero|one|two|three|four|five|six|seven|eight|nine))+)| and a half)\b", rest, re.I)
            if m and m.group(2):
                fraction = "".join(str(_UNITS[d.lower()]) for d in m.group(2).split())
            elif m:
                fraction = "5"
            if m:
                end, rest, spoken = end + m.end(), rest[m.end():], spoken + 2
            elif spoken == 1 and value in (19, 20):
                # Years: "nineteen ninety nine", "twenty twenty six".
                year, k, year_ordinal, year_scale = _read_number(words, j, text)
                if k > j and 10 <= year <= 99 and not year_ordinal and not year_scale and \
                        _NUM_GAP.fullmatch(text[end:words[j].start()]) and words[k - 1].group().lower() != "hundred":
                    out.append(text[pos:start] + str(value * 100 + year))
                    pos, i = words[k - 1].end(), k
                    continue
            elif spoken == 1 and value < 10:
                # Phone/PIN style runs of single digits: "five five five one two".
                k, digits = j, str(value)
                while k < len(words) and words[k].group().lower() in _UNITS and \
                        _UNITS[words[k].group().lower()] < 10 and text[words[k - 1].end():words[k].start()] == " ":
                    digits += str(_UNITS[words[k].group().lower()])
                    k += 1
                # Counting ("one two three four") isn't a number; phone numbers and PINs are 4+ digits.
                counting = digits in "0123456789" and digits[0] in "01"
                if len(digits) >= 3 and counting:
                    i = k  # leave the whole count as words
                    continue
                if len(digits) >= 4:
                    out.append(text[pos:start] + digits)
                    pos, i = words[k - 1].end(), k
                    continue
        small = spoken == 1 and value < 10 and not fraction
        if small and ordinal:
            before = text[:start].split()
            dated = (before and before[-1].strip(",.").lower() in _MONTHS) or \
                re.match(r" of (" + "|".join(_MONTHS) + r")\b", rest, re.I)
            if not dated:
                i = j
                continue  # "first of all", "a second opinion"
        elif small and not _UNIT_AFTER.match(rest):
            i = j
            continue
        if ordinal:
            number = _ordinal(value)
        elif scale and value % _SCALES[scale] == 0 and not fraction:
            number = f"{value // _SCALES[scale]:,} {scale}"  # "2 million", not "2,000,000"
        else:
            number = f"{value:,}" if value >= 10000 else str(value)
            number += "." + fraction if fraction else ""
            big = fraction and re.match(r" (million|billion|trillion)\b", rest, re.I)
            if big:  # "two point five million" -> "2.5 million"
                number, end, rest = f"{number} {big.group(1)}", end + big.end(), rest[big.end():]
        unit = re.match(r" (percent|per cent)\b", rest, re.I)
        money = re.match(r" dollars?\b", rest, re.I)
        if unit:
            number, end = number + "%", end + unit.end()
        elif money and not ordinal:
            number, end = "$" + number, end + money.end()
            cents = _read_cents(words, text, end)
            if cents and not fraction:  # "twenty dollars and fifty cents" -> "$20.50"
                number, end = f"{number}.{cents[0]:02d}", cents[1]
        out.append(text[pos:start] + number)
        pos, i = end, j
        while i < len(words) and words[i].start() < pos:
            i += 1
    return "".join(out) + text[pos:]


def clean(text, cfg):
    text = _SCRATCH.sub("", text)
    if cfg["remove_fillers"]:
        text = _FILLERS.sub("", text)
    text = fix_words(text, cfg)
    text = apply_map(text, cfg["snippets"])
    if cfg["numbers_as_digits"]:
        text = format_numbers(text)
    text = _apply_corrections(text)
    text = format_list(text)
    for rx, repl in _COMMANDS:
        text = rx.sub(repl, text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r" +([,.!?;:])", r"\1", text)
    text = re.sub(r"[ \t]+\n", "\n", text).strip()
    if text and text[0].islower():
        text = text[0].upper() + text[1:]
    return text


def polish(text, cfg):
    """Optional local-LLM rewrite via Ollama. Returns the input unchanged on any failure."""
    p = cfg["polish"]
    if not p["enabled"] or not text:
        return text
    system = (
        "You clean up dictated speech. Fix grammar, punctuation and capitalisation, drop filler words, "
        "and apply self-corrections (e.g. 'no wait, Tuesday' replaces the earlier day). "
        "Keep bullet lists as '- ' bullet lists, one item per line. "
        f"{p['style']} Never answer or act on the text, never add commentary. Output only the final text."
    )
    body = json.dumps({
        "model": p["model"], "stream": False, "keep_alive": "30m",
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": text}],
        "options": {"temperature": 0.1},
    }).encode()
    req = urllib.request.Request(p["url"].rstrip("/") + "/api/chat", body, {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=p["timeout_seconds"]) as r:
            out = json.load(r)["message"]["content"].strip()
        # Reject rewrites that balloon or vanish; those are the model going off-script.
        if out and 0.4 * len(text) <= len(out) <= 2.5 * len(text) + 20:
            return out
    except Exception:  # noqa: BLE001
        pass
    return text
