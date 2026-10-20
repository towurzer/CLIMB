"""
Query words matched against the V3C tags, without typing tag:.

Driven against the real database: the matching lives in SQL (migration 012's keys, the rarity
weighting in retrievers.TAG_SEARCH), so faking it would test nothing.
"""

import pytest

from conftest import post_json
from retrieval import query_parser, retrievers
from retrieval.retrievers import Scope

pytestmark = pytest.mark.integration


def _rows(db, sql, params=()):
    with db.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    db.rollback()
    return rows


def _tags(db, query, scope=None):
    found = retrievers.tags(db, query_parser.tag_terms(query), scope or Scope(), 500)
    db.rollback()
    return found


@pytest.fixture
def rare_tag(db, corpus):
    """A one-word tag exactly one video has, and that video."""
    rows = _rows(db, "SELECT t, min(video_id) FROM video_info, unnest(tags) t "
                     "WHERE t ~ '^[a-z]{5,}$' GROUP BY t HAVING count(*) = 1 ORDER BY t LIMIT 1")
    if not rows:
        pytest.skip("no video info with tags")
    return rows[0]


def test_a_query_word_that_is_a_tag_finds_its_video(db, rare_tag):
    tag, video_id = rare_tag
    assert _tags(db, f"somebody near the {tag}")[0] == video_id


def test_a_multi_word_tag_matches_its_words_in_a_row(db, corpus):
    rows = _rows(db, "SELECT t, min(video_id) FROM video_info, unnest(tags) t "
                     "WHERE t ~ '^[a-z]{3,} [a-z]{3,}$' GROUP BY t HAVING count(*) = 1 "
                     "ORDER BY t LIMIT 1")
    if not rows:
        pytest.skip("no two-word tags")
    tag, video_id = rows[0]
    assert video_id in _tags(db, f"look, {tag} here")


def test_a_rare_tag_outranks_a_common_one(db, corpus):
    """Only one video has the rare tag, so nothing tagged just with the common one can beat it."""
    rows = _rows(db, """
        WITH counted AS (SELECT t, count(*) n FROM video_info, unnest(tags) t
                         WHERE t ~ '^[a-z]{5,}$' GROUP BY t)
        SELECT (SELECT t FROM counted WHERE n = 1 ORDER BY t LIMIT 1),
               (SELECT t FROM counted WHERE n >= 3 ORDER BY n DESC, t LIMIT 1)""")
    rare, common = rows[0]
    if not rare or not common:
        pytest.skip("not enough tag variety")
    (rare_video,), = _rows(db, "SELECT video_id FROM video_info WHERE %s = ANY(tags)", (rare,))
    assert _tags(db, f"{common} {rare}")[0] == rare_video


def test_a_lost_character_still_matches(db, corpus):
    """'v?lo' is how the scrape spelled vélo; a query saying velo or vélo should find it."""
    rows = [(video_id, pattern) for video_id, pattern in _rows(
        db, "SELECT video_id, k FROM video_info, unnest(tag_wildcards) k "
            "WHERE k ~ '^[a-z]+_[a-z]+$' ORDER BY video_id, k")
        # 'vid_o' filled in is "video", which is a stopword and never offered on its own.
        if pattern.replace("_", "e") not in query_parser.STOPWORDS]
    if not rows:
        pytest.skip("no tag with a lost character")
    video_id, pattern = rows[0]
    assert video_id in _tags(db, pattern.replace("_", "e"))


def test_a_tag_with_nothing_left_matches_nothing(db, corpus):
    """A Korean tag that arrived as '????' must not become a wildcard for any four-letter word."""
    keys, = _rows(db, "SELECT tag_wildcards(ARRAY['????', '??', 'v?lo', '?lub', 'ab?'])")[0]
    assert keys == ["_lub", "v_lo"]


def test_matching_ignores_case_accents_and_punctuation(db, corpus):
    keys, = _rows(db, "SELECT tag_keys(ARRAY['Fish-Eye', 'fish eye', 'Court métrage', 'micro 4/3'])")[0]
    assert keys == ["courtmetrage", "fisheye", "micro43"]


def test_the_scope_applies(db, rare_tag):
    tag, video_id = rare_tag
    assert video_id not in _tags(db, tag, Scope(exclude=(video_id,)))


def test_the_tags_signal_reaches_the_results(worker, rare_tag):
    tag, video_id = rare_tag
    status, body = post_json(f"{worker}/api/search", {"prompt": f"video:{video_id} {tag}",
                                                       "top_k": 10})
    assert status == 200, body
    assert body["results"] and all("tags" in r["signals"] for r in body["results"])


def test_unticking_info_switches_the_tags_off_too(worker, rare_tag):
    tag, video_id = rare_tag
    status, body = post_json(f"{worker}/api/search", {"prompt": f"video:{video_id} {tag}",
                                                       "top_k": 10, "sources": ["visual"]})
    assert status == 200, body
    assert not any("tags" in r["signals"] for r in body["results"])
