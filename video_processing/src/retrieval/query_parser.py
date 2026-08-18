"""
Turns a search box string into a structured query.

The important part is what goes to the *lexical* retriever. Handing it the whole sentence does not
work: `websearch_to_tsquery` builds a conjunction of every word, so "a man walks past a bakery with
a sign reading Boulangerie Dupont" becomes twelve ANDed terms and matches nothing at all -- not
even a sign that literally reads BOULANGERIE DUPONT. Measured, not theorised.

So free text is reduced to its distinctive tokens and ORed. Capitalised words in the middle of a
sentence are treated as the distinctive ones when present, because that is what a proper noun looks
like and proper nouns are exactly what OCR contributes that nothing else can.

Syntax:
    plain words              every retriever
    text:"..." / text:...    OCR only, matched as a phrase
    said:"..." / said:...    transcripts only, matched as a phrase
    -video:00191             exclude a video (repeatable)
    A >> B                   A, then B within the default window, in the same video
    A >>(d120) B             the same, with a 120 second window

The quotes are optional: the colon is what makes it an operator. Unquoted, the phrase runs to the
end of the stage or to the next operator, so `said: after the earthquake` is the whole sentence and
`text:BANK said:we are live` is two phrases. Quotes are still the way to put a phrase and free text
on the *same* stage -- `text:"Dupont" a man walks past` searches for both, where `text:Dupont a man
walks past` is one long phrase. Typing an operator and getting a free-text search instead was a
silent failure: the word `said` went to the lexical retriever, at four times the weight of the
visual one, and hunted for signs reading "said".

`>>` chains: `A >> B >> C` is three stages and two independent windows. Each stage is a complete
query in its own right -- `text:"Boulangerie" >> a dog runs past` is a legal sequence -- because a
stage is scored by the full fused search, not by the visual retriever alone.
"""

import re
from dataclasses import dataclass, field

# Where an unquoted phrase has to stop: the next operator, or the end of the stage. `>>` never
# appears here because stages are split before parse() sees them.
_NEXT_OPERATOR = r'(?=\s+(?:text:|said:|-video:|--exclude:)|$)'


def _phrase_prefix(keyword: str):
    """
    `keyword:"a phrase"` or the bare `keyword:a phrase`.

    The quoted branch is first and deliberately carries no terminator: it already knows where it
    ends, so `text:"Dupont" a man walks past` leaves the rest as free text. The bare branch cannot
    contain a quote, which is what keeps the two from fighting over the same string.
    """
    return re.compile(rf'\b{keyword}:\s*(?:"([^"]*)"|([^"]*?){_NEXT_OPERATOR})', re.I)


TEXT_PREFIX = _phrase_prefix("text")
SAID_PREFIX = _phrase_prefix("said")
EXCLUDE_PREFIX = re.compile(r'-video:([A-Za-z0-9_]+)')
# Legacy syntax the frontend still appends; kept so an old client does not silently search for it.
#
# The list is comma separated *with spaces*, the frontend writes `--exclude: 00083, 00140, 00004`.
LEGACY_EXCLUDE = re.compile(r'--exclude:\s*([A-Za-z0-9_]*(?:\s*,\s*[A-Za-z0-9_]+)*)')

WORD = re.compile(r"[^\W\d_]+|\d+", re.UNICODE)

TEMPORAL_SEPARATOR = ">>"
# The window annotating a separator: (d120), (120), (d120s), (d500ms). A bare number is seconds,
# which is the unit anyone typing a VBS hint is thinking in.
TEMPORAL_DELTA = re.compile(r"\s*\(\s*d?\s*(\d+)\s*(ms|s)?\s*\)")

STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "in", "on", "at", "to", "for", "with", "by", "from",
    "is", "are", "was", "were", "be", "been", "being", "as", "that", "this", "these", "those",
    "it", "its", "he", "she", "they", "them", "his", "her", "their", "there", "here",
    "some", "someone", "somebody", "something", "person", "people", "man", "woman", "men", "women",
    "shows", "showing", "shown", "seen", "sees", "video", "shot", "scene", "frame", "clip",
    "while", "during", "then", "into", "onto", "over", "under", "near", "next", "past", "up",
    "down", "out", "off", "about", "very", "more", "most", "also", "not", "no",
}

MIN_TOKEN_LENGTH = 3


@dataclass
class ParsedQuery:
    raw: str
    free_text: str = ""
    ocr_phrase: str | None = None
    asr_phrase: str | None = None
    exclude_videos: list = field(default_factory=list)

    @property
    def has_free_text(self) -> bool:
        return bool(self.free_text.strip())

    @property
    def targeted(self) -> bool:
        """True when the user asked for one specific retriever rather than a general search."""
        return (self.ocr_phrase is not None or self.asr_phrase is not None) and not self.has_free_text

    @property
    def has_content(self) -> bool:
        """False when nothing here would run a retriever, only exclusions, or nothing at all."""
        return bool(self.has_free_text or self.ocr_phrase or self.asr_phrase)


@dataclass
class TemporalQuery:
    """
    One or more stages that must occur in this order, in the same video.

    A plain query is the single-stage case, so the engine has one code path to parse and only
    branches on `is_temporal`.
    """
    raw: str
    stages: list = field(default_factory=list)
    # One window per gap, so len(gaps_ms) == len(stages) - 1.
    gaps_ms: list = field(default_factory=list)
    # Global: an exclusion written on any stage excludes the video from all of them.
    exclude_videos: list = field(default_factory=list)

    @property
    def is_temporal(self) -> bool:
        return len(self.stages) > 1


def distinctive_tokens(text: str) -> list:
    """
    The words worth handing to a lexical index.

    Prefers mid-sentence capitalised words when there are any -- in "a man walks past a bakery
    with a sign reading Boulangerie Dupont" that is exactly {Boulangerie, Dupont}, which is the
    part OCR can actually match. Falls back to all content words when the query is lowercase.
    """
    words = WORD.findall(text)
    if not words:
        return []

    proper = [w for i, w in enumerate(words)
              if i > 0 and w[:1].isupper() and w.lower() not in STOPWORDS]
    if proper:
        return _dedupe(w.lower() for w in proper)

    return _dedupe(w.lower() for w in words
                   if len(w) >= MIN_TOKEN_LENGTH and w.lower() not in STOPWORDS)


def _dedupe(values) -> list:
    seen, out = set(), []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _phrase(match) -> str | None:
    """The quoted group or the bare one, whichever branch fired."""
    if not match:
        return None
    quoted, bare = match.group(1), match.group(2)
    return (quoted if quoted is not None else bare).strip()


def parse(query: str) -> ParsedQuery:
    raw = query or ""
    remainder = raw
    exclude = []

    ocr_phrase = _phrase(TEXT_PREFIX.search(remainder))
    remainder = TEXT_PREFIX.sub(" ", remainder)

    asr_phrase = _phrase(SAID_PREFIX.search(remainder))
    remainder = SAID_PREFIX.sub(" ", remainder)

    for match in EXCLUDE_PREFIX.finditer(remainder):
        exclude.append(match.group(1))
    remainder = EXCLUDE_PREFIX.sub(" ", remainder)

    for match in LEGACY_EXCLUDE.finditer(remainder):
        exclude.extend(v for v in (p.strip() for p in match.group(1).split(",")) if v)
    remainder = LEGACY_EXCLUDE.sub(" ", remainder)

    return ParsedQuery(
        raw=raw,
        free_text=" ".join(remainder.split()),
        ocr_phrase=ocr_phrase or None,
        asr_phrase=asr_phrase or None,
        exclude_videos=_dedupe(exclude),
    )


def _split_stages(text: str) -> list:
    """
    Splits on `>>` outside double quotes, returning [(segment, delta_ms | None)].

    Quote-aware, and scanned rather than regex-split, because `text:"a >> b"` is a phrase that
    happens to contain the separator. A bare split would cut the phrase in half and search for two
    things nobody asked for -- and it would do it silently.

    The delta rides with the segment *after* the separator it annotates, which is what makes the
    bookkeeping survive an empty stage being dropped later.
    """
    segments = []
    start, index, delta = 0, 0, None
    in_quotes = False

    while index < len(text):
        if text[index] == '"':
            in_quotes = not in_quotes
            index += 1
            continue
        if not in_quotes and text.startswith(TEMPORAL_SEPARATOR, index):
            segments.append((text[start:index], delta))
            index += len(TEMPORAL_SEPARATOR)
            match = TEMPORAL_DELTA.match(text, index)
            delta = _delta_ms(match) if match else None
            index = match.end() if match else index
            start = index
            continue
        index += 1

    segments.append((text[start:], delta))
    return segments


def _delta_ms(match) -> int:
    value = int(match.group(1))
    return value if match.group(2) == "ms" else value * 1000


def parse_temporal(query: str, default_delta_ms: int, max_delta_ms: int,
                   max_stages: int) -> TemporalQuery:
    """
    Parses a whole search box, sequence syntax included.

    Always returns at least one stage, so a caller can treat a plain query as the degenerate
    sequence and only branch on `is_temporal`.
    """
    raw = query or ""
    parsed = [(parse(text), delta) for text, delta in _split_stages(raw)]

    exclude = [video for stage, _ in parsed for video in stage.exclude_videos]

    # An empty stage is not a stage: `A >>` is a search for A, not a sequence whose second half
    # matches nothing and therefore returns nothing at all.
    kept = [(stage, delta) for stage, delta in parsed if stage.has_content]
    if not kept:
        kept = parsed[:1]
    kept = kept[:max_stages]

    gaps = [min(default_delta_ms if delta is None else delta, max_delta_ms)
            for _, delta in kept[1:]]

    return TemporalQuery(raw=raw, stages=[stage for stage, _ in kept], gaps_ms=gaps,
                         exclude_videos=_dedupe(exclude))
