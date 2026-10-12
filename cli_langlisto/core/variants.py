"""Answer-variant expansion for gap keys (quality feature, H9) — pure, no I/O.

Measured on the live books: only 3/180 sampled gaps carried more than one
accepted answer, so students get wrong-marked for case, punctuation,
apostrophe, contraction and number-word differences.  :func:`expand_answers`
turns a thin authored key (``["goes"]``) into the documented variant classes.

It only generates *meaning-preserving* rewrites, so a genuinely wrong form is
never accepted (H9 pass criterion: ≥6 variant classes accepted, zero
false-accepts).

Classes covered
---------------
* case          -- ``goes`` -> ``Goes`` (and a lowercase form of mixed-case keys)
* punctuation   -- trailing ``, . ! ? ; : …`` dropped from the variant set
* apostrophes   -- typographic ``’`` normalised to straight ``'``
* contractions  -- ``don't`` <-> ``do not`` (curated two-way map)
* numbers       -- ``50`` <-> ``fifty``, ``twenty-one`` <-> ``21`` (0..100)
* whitespace    -- runs collapsed, trimmed

Opt-out: item flag ``expand_variants: false`` (or per gap ``variants: false``)
keeps the key exactly as authored.
"""

import re

MAX_VARIANTS = 12

_TRAILING_PUNCT = ".,!?;:\u2026\u00a1\u00bf"
_APOSTROPHE_TRANS = str.maketrans({
    "\u2019": "'", "\u2018": "'", "\u02bc": "'", "\u00b4": "'", "`": "'",
})

# Short form -> long form (lowercased, straight apostrophes).
_SHORT_TO_LONG = {
    "i'm": "i am", "i've": "i have", "i'll": "i will", "i'd": "i would",
    "you're": "you are", "you've": "you have", "you'll": "you will", "you'd": "you would",
    "he's": "he is", "she's": "she is", "it's": "it is", "that's": "that is",
    "there's": "there is", "here's": "here is", "what's": "what is",
    "we're": "we are", "we've": "we have", "we'll": "we will", "we'd": "we would",
    "they're": "they are", "they've": "they have", "they'll": "they will", "they'd": "they would",
    "isn't": "is not", "aren't": "are not", "wasn't": "was not", "weren't": "were not",
    "don't": "do not", "doesn't": "does not", "didn't": "did not",
    "can't": "cannot", "cannot": "can not", "won't": "will not",
    "hasn't": "has not", "haven't": "have not", "hadn't": "had not",
    "shouldn't": "should not", "wouldn't": "would not", "couldn't": "could not",
    "mustn't": "must not", "needn't": "need not", "let's": "let us",
}
# Long form -> preferred short form (reverse direction).  Curated so the
# compression is unambiguous.
_LONG_TO_SHORT = {
    "i am": "i'm", "i have": "i've", "i will": "i'll", "i would": "i'd",
    "you are": "you're", "you have": "you've", "you will": "you'll",
    "he is": "he's", "she is": "she's", "it is": "it's", "that is": "that's",
    "there is": "there's", "we are": "we're", "we have": "we've",
    "we will": "we'll", "they are": "they're", "they have": "they've",
    "is not": "isn't", "are not": "aren't", "was not": "wasn't", "were not": "weren't",
    "do not": "don't", "does not": "doesn't", "did not": "didn't",
    "will not": "won't", "has not": "hasn't", "have not": "haven't",
    "should not": "shouldn't", "would not": "wouldn't", "could not": "couldn't",
    "let us": "let's",
}

_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven",
         "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen",
         "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_ONES_WORDS = {w: i for i, w in enumerate(_ONES)}
_TENS_WORDS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
               "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_TENS_BY_VALUE = {v: w for w, v in _TENS_WORDS.items()}

_TOKEN_RE = re.compile(r"^([^\w]*)([\w'\-]+)([^\w]*)$", re.UNICODE)


def normalize_apostrophes(text):
    return str(text).translate(_APOSTROPHE_TRANS)


def _clean_ws(text):
    return " ".join(str(text).split())


def _strip_trailing(text):
    return text.rstrip(_TRAILING_PUNCT)


def int_to_words(n):
    """Cardinal number 0..100 -> English words (hyphenated tens-ones)."""
    if 0 <= n <= 19:
        return _ONES[n]
    if n in _TENS_BY_VALUE:
        return _TENS_BY_VALUE[n]
    if 20 <= n <= 99:
        tens, ones = divmod(n, 10)
        return f"{_TENS_BY_VALUE[tens * 10]}-{_ONES[ones]}"
    if n == 100:
        return "one hundred"
    return None


def _number_token_variants(token):
    """Token-level digit <-> word variants (keeps surrounding punctuation)."""
    m = _TOKEN_RE.match(token)
    if not m:
        return []
    prefix, core, suffix = m.groups()
    out = []
    low = core.lower()
    if low.isdigit() and len(low) <= 3:
        words = int_to_words(int(low))
        if words:
            out.append(prefix + words + suffix)
            out.append(prefix + words.replace("-", " ") + suffix)
    elif low in _ONES_WORDS or low in _TENS_WORDS:
        out.append(prefix + str(_ONES_WORDS.get(low, _TENS_WORDS.get(low))) + suffix)
    elif "-" in low:
        head, _, tail = low.partition("-")
        if head in _TENS_WORDS and tail in _ONES_WORDS:
            out.append(prefix + str(_TENS_WORDS[head] + _ONES_WORDS[tail]) + suffix)
    return [o for o in out if o != token]


def _number_phrase_variants(text):
    """Phrase-level number swaps: single tokens + ``twenty one`` bigrams."""
    tokens = text.split(" ")
    out = []
    for i, tok in enumerate(tokens):
        for nv in _number_token_variants(tok):
            out.append(" ".join(tokens[:i] + [nv] + tokens[i + 1:]))
        if i + 1 < len(tokens):
            a, b = tok.lower().strip(_TRAILING_PUNCT), tokens[i + 1].lower().strip(_TRAILING_PUNCT)
            if a in _TENS_WORDS and b in _ONES_WORDS and _ONES_WORDS[b] != 0:
                digits = str(_TENS_WORDS[a] + _ONES_WORDS[b])
                out.append(" ".join(tokens[:i] + [digits] + tokens[i + 2:]))
    return out


def _pattern(text):
    """Word-boundary regex for a short/long contraction form."""
    esc = re.escape(text).replace(r"\ ", r"\s+")
    return re.compile(r"(?<![A-Za-z])" + esc + r"(?![A-Za-z])", re.IGNORECASE)


def _contraction_variants(text):
    out = []
    for short, long_ in _SHORT_TO_LONG.items():
        if _pattern(short).search(text):
            out.append(_pattern(short).sub(long_, text))
    for long_, short in _LONG_TO_SHORT.items():
        if _pattern(long_).search(text):
            out.append(_pattern(long_).sub(short, text))
    return out


def expand_answer_variants(answer, enabled=True, limit=MAX_VARIANTS):
    """Expand ONE authored answer into its accepted variant list.

    Returns a deterministic, deduplicated list; the original form is always
    first so ``correct_indices: [0]`` stays meaningful.
    """
    base = _clean_ws(normalize_apostrophes(answer))
    if not base:
        return []
    variants = [base]
    if not enabled:
        return variants

    def add(value):
        value = _clean_ws(value)
        if value and value not in variants and len(variants) < limit:
            variants.append(value)
        # case alternate of a derived form (e.g. "i'm" -> "I'm")
        if value and value[:1].islower():
            cap = value[:1].upper() + value[1:]
            if cap not in variants and len(variants) < limit:
                variants.append(cap)

    # case
    add(base.lower())
    if base[:1].islower():
        add(base[:1].upper() + base[1:])
    # trailing punctuation
    for v in list(variants):
        stripped = _strip_trailing(v)
        if stripped:
            add(stripped)
    # contractions (both directions) over the current snapshot
    for v in list(variants):
        for cand in _contraction_variants(v):
            add(cand)
    # number words <-> digits over the current snapshot
    for v in list(variants):
        for cand in _number_phrase_variants(v):
            add(cand)
    return variants


def expand_answers(answers, enabled=True, limit=MAX_VARIANTS, cap=24):
    """Expand a list of authored answers; order preserved, deduplicated."""
    out = []
    for a in answers or []:
        if a is None:
            continue
        for v in expand_answer_variants(a, enabled=enabled, limit=limit):
            if v not in out and len(out) < cap:
                out.append(v)
    return out
