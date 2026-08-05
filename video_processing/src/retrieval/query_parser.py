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
    text:"..."               OCR only, matched as a phrase
    said:"..."               transcripts only, matched as a phrase
    -video:00191             exclude a video (repeatable)
"""

import re
from dataclasses import dataclass, field

TEXT_PREFIX = re.compile(r'\btext:"([^"]*)"', re.I)
SAID_PREFIX = re.compile(r'\bsaid:"([^"]*)"', re.I)
EXCLUDE_PREFIX = re.compile(r'-video:([A-Za-z0-9_]+)')
# Legacy syntax the frontend still appends; kept so an old client does not silently search for it.
LEGACY_EXCLUDE = re.compile(r'--exclude:\s*([^\s]*)')

WORD = re.compile(r"[^\W\d_]+|\d+", re.UNICODE)

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


def parse(query: str) -> ParsedQuery:
    raw = query or ""
    remainder = raw
    exclude = []

    ocr_match = TEXT_PREFIX.search(remainder)
    ocr_phrase = ocr_match.group(1).strip() if ocr_match else None
    remainder = TEXT_PREFIX.sub(" ", remainder)

    said_match = SAID_PREFIX.search(remainder)
    asr_phrase = said_match.group(1).strip() if said_match else None
    remainder = SAID_PREFIX.sub(" ", remainder)

    for match in EXCLUDE_PREFIX.finditer(remainder):
        exclude.append(match.group(1))
    remainder = EXCLUDE_PREFIX.sub(" ", remainder)

    for match in LEGACY_EXCLUDE.finditer(remainder):
        exclude.extend(v for v in match.group(1).split(",") if v)
    remainder = LEGACY_EXCLUDE.sub(" ", remainder)

    return ParsedQuery(
        raw=raw,
        free_text=" ".join(remainder.split()),
        ocr_phrase=ocr_phrase or None,
        asr_phrase=asr_phrase or None,
        exclude_videos=_dedupe(exclude),
    )
