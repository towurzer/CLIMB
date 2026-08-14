"""
Query parsing.

The cases that matter are the ones where a plausible simpler implementation would be silently
wrong: splitting a quoted phrase that contains `>>`, treating a bare `(120)` as milliseconds, or
letting `A >>` become a two-stage sequence whose empty half matches nothing.
"""

import pytest
from retrieval.query_parser import distinctive_tokens, parse, parse_temporal

DEFAULT_DELTA = 30_000
MAX_DELTA = 600_000
MAX_STAGES = 4


def temporal(query, default=DEFAULT_DELTA, maximum=MAX_DELTA, stages=MAX_STAGES):
    return parse_temporal(query, default, maximum, stages)


# --- field extraction --------------------------------------------------------------------------

def test_plain_query_is_all_free_text():
    parsed = parse("a dog runs across a beach")
    assert parsed.free_text == "a dog runs across a beach"
    assert parsed.ocr_phrase is None
    assert parsed.asr_phrase is None
    assert parsed.has_content
    assert not parsed.targeted


def test_text_prefix_is_lifted_out_of_free_text():
    parsed = parse('text:"Boulangerie Dupont"')
    assert parsed.ocr_phrase == "Boulangerie Dupont"
    assert parsed.free_text == ""
    # Nothing left to search generally, so this is a targeted OCR query.
    assert parsed.targeted


def test_said_prefix_is_lifted_out_of_free_text():
    parsed = parse('said:"ladies and gentlemen"')
    assert parsed.asr_phrase == "ladies and gentlemen"
    assert parsed.free_text == ""
    assert parsed.targeted


def test_prefix_alongside_free_text_is_not_targeted():
    parsed = parse('text:"Dupont" a man walks past')
    assert parsed.ocr_phrase == "Dupont"
    assert parsed.free_text == "a man walks past"
    assert not parsed.targeted


def test_empty_query_has_no_content():
    assert not parse("").has_content
    assert not parse("   ").has_content


def test_exclusion_only_query_has_no_content():
    # Excluding a video is not something to search for.
    parsed = parse("-video:00191")
    assert parsed.exclude_videos == ["00191"]
    assert not parsed.has_content


def test_exclusions_are_collected_and_deduped():
    parsed = parse("a dog -video:00191 -video:00042 -video:00191")
    assert parsed.exclude_videos == ["00191", "00042"]
    assert parsed.free_text == "a dog"


def test_legacy_exclude_syntax_still_understood():
    # The frontend still appends this form; dropping it would silently search for the literal text.
    parsed = parse("a dog --exclude:00191,00042")
    assert parsed.exclude_videos == ["00191", "00042"]
    assert parsed.free_text == "a dog"


def test_legacy_exclude_list_is_separated_by_comma_and_space():
    # The exact string the exclude button writes -- `join(", ")`, spaces and all. This test used to
    # exist only in the no-space form, which is a form the frontend has never produced: the pattern
    # stopped at the first space, so only 00083 was excluded and the other two stayed in the query.
    parsed = parse("a skier doing a backflip --exclude: 00083, 00140, 00004")
    assert parsed.exclude_videos == ["00083", "00140", "00004"]
    # And the ids must not survive as search terms: as free text they reach the text embedding, and
    # `00004` is a token the OCR retriever will happily go looking for.
    assert parsed.free_text == "a skier doing a backflip"


def test_legacy_exclude_leaves_the_rest_of_the_query_alone():
    parsed = parse("a dog --exclude: 00191, 00042 chasing a car")
    assert parsed.exclude_videos == ["00191", "00042"]
    assert parsed.free_text == "a dog chasing a car"


def test_legacy_exclude_marker_without_a_list_is_not_a_search_term():
    parsed = parse("a dog --exclude:")
    assert parsed.exclude_videos == []
    assert parsed.free_text == "a dog"


# --- distinctive tokens ------------------------------------------------------------------------

def test_proper_nouns_win_when_present():
    tokens = distinctive_tokens("a man walks past a bakery with a sign reading Boulangerie Dupont")
    assert tokens == ["boulangerie", "dupont"]


def test_falls_back_to_content_words_when_all_lowercase():
    tokens = distinctive_tokens("a man walks past a bakery")
    # Stopwords and sub-3-character words are dropped; "man" is a stopword here on purpose.
    assert "bakery" in tokens
    assert "walks" in tokens
    assert "a" not in tokens
    assert "man" not in tokens


def test_first_word_capital_is_not_a_proper_noun():
    # Sentence case is not evidence of anything, so the i > 0 guard must hold.
    tokens = distinctive_tokens("Bakery with a sign")
    assert tokens == ["bakery", "sign"]


def test_tokens_are_deduped_in_order():
    assert distinctive_tokens("bakery bakery bridge bakery") == ["bakery", "bridge"]


def test_no_words_yields_no_tokens():
    assert distinctive_tokens("") == []
    assert distinctive_tokens("!!! ???") == []


# --- >> splitting ------------------------------------------------------------------------------

def test_plain_query_is_a_single_stage_sequence():
    query = temporal("a dog runs")
    assert len(query.stages) == 1
    assert not query.is_temporal
    assert query.gaps_ms == []


def test_two_stages_split_on_separator():
    query = temporal("a dog runs >> a car drives")
    assert query.is_temporal
    assert [s.free_text for s in query.stages] == ["a dog runs", "a car drives"]
    assert query.gaps_ms == [DEFAULT_DELTA]


def test_separator_inside_quotes_does_not_split():
    # The whole point of the hand-written scanner rather than a regex split.
    query = temporal('text:"a >> b"')
    assert not query.is_temporal
    assert query.stages[0].ocr_phrase == "a >> b"


def test_quoted_separator_and_a_real_one_coexist():
    query = temporal('text:"a >> b" >> a dog runs')
    assert query.is_temporal
    assert len(query.stages) == 2
    assert query.stages[0].ocr_phrase == "a >> b"
    assert query.stages[1].free_text == "a dog runs"


def test_three_stage_chain_has_two_gaps():
    query = temporal("a >> b >> c")
    assert len(query.stages) == 3
    assert len(query.gaps_ms) == len(query.stages) - 1


# --- gap parsing -------------------------------------------------------------------------------

@pytest.mark.parametrize("annotation,expected", [
    ("(d120)", 120_000),   # documented form
    ("(120)", 120_000),    # bare number is seconds -- what a VBS hint is written in
    ("(120s)", 120_000),
    ("(500ms)", 500),
    ("( d 120 s )", 120_000),  # whitespace tolerant
])
def test_gap_annotations(annotation, expected):
    query = temporal(f"a dog >>{annotation} a car")
    assert query.gaps_ms == [expected]


def test_unannotated_gap_uses_the_default():
    assert temporal("a >> b").gaps_ms == [DEFAULT_DELTA]


def test_gap_is_clamped_to_the_maximum():
    query = temporal("a >>(9999) b")
    assert query.gaps_ms == [MAX_DELTA]


def test_gaps_are_per_separator_not_shared():
    query = temporal("a >>(10) b >>(20) c")
    assert query.gaps_ms == [10_000, 20_000]


# --- dropped empty stages ----------------------------------------------------------------------

def test_trailing_separator_does_not_create_a_stage():
    # `A >>` is a search for A. A second empty stage would match nothing and return nothing.
    query = temporal("a dog runs >>")
    assert not query.is_temporal
    assert len(query.stages) == 1
    assert query.gaps_ms == []


def test_leading_separator_does_not_create_a_stage():
    query = temporal(">> a dog runs")
    assert not query.is_temporal
    assert query.stages[0].free_text == "a dog runs"


def test_empty_middle_stage_is_dropped_and_its_gap_goes_with_it():
    query = temporal("a >>(10) >>(20) c")
    assert len(query.stages) == 2
    # The dropped stage took its own annotation with it; the surviving gap is the one that
    # annotated the stage that survived. This is why the delta rides with the following segment.
    assert query.gaps_ms == [20_000]


def test_entirely_empty_query_still_yields_one_stage():
    # Callers treat a plain query as the degenerate sequence, so stages must never be empty.
    query = temporal("")
    assert len(query.stages) == 1
    assert not query.stages[0].has_content


def test_stage_count_is_capped():
    query = temporal("a >> b >> c >> d >> e >> f", stages=4)
    assert len(query.stages) == 4
    assert len(query.gaps_ms) == 3


# --- exclusions are global ---------------------------------------------------------------------

def test_exclusion_on_one_stage_applies_to_the_whole_sequence():
    query = temporal("a dog -video:00191 >> a car")
    assert query.exclude_videos == ["00191"]


def test_exclusions_from_several_stages_are_merged_and_deduped():
    query = temporal("a -video:00191 >> b -video:00042 >> c -video:00191")
    assert query.exclude_videos == ["00191", "00042"]


def test_legacy_exclusions_survive_a_sequence_query():
    # The exclude button appends its list to whatever is in the box, sequence syntax included, so
    # the suffix lands on the last stage and still has to apply to all of them.
    query = temporal("a dog >> a car --exclude: 00191, 00042")
    assert query.exclude_videos == ["00191", "00042"]
    assert [stage.free_text for stage in query.stages] == ["a dog", "a car"]
