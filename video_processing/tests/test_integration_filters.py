"""
Requirements against the running worker and backend: video:, -video:, tag:, text: + said: combined
at video level, MVK recordings, and the dataset restriction.

Corpus-adaptive like the rest of the integration tests: each test picks its examples out of the
database and skips when the corpus has nothing that fits.
"""

import re

import pytest

from conftest import get_json, post_json

pytestmark = pytest.mark.integration


def _search(worker, prompt, **kwargs):
    status, body = post_json(f"{worker}/api/search", {"prompt": prompt, **kwargs})
    assert status == 200, body
    return body


def _one(db, sql, params=()):
    with db.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    db.rollback()
    return row


def _videos_of(body) -> set:
    return {r["video_id"] for r in body["results"]}


def test_video_restricts_every_result_to_that_video(worker, corpus, db):
    video_id, = _one(db, "SELECT video_id FROM keyframes GROUP BY video_id "
                         "ORDER BY count(*) DESC LIMIT 1")
    body = _search(worker, f"video:{video_id} a person", top_k=20)
    assert body["results"], "a free-text search inside one video found nothing -- the ANN path " \
                            "filtered everything away instead of ranking exactly"
    assert _videos_of(body) == {video_id}
    assert body["filters"] == [{"filter": f"video:{video_id}", "videos": 1}]


def test_video_alone_lists_the_scenes_in_order(worker, corpus, db):
    video_id, scenes = _one(db, "SELECT video_id, count(*) FROM scenes GROUP BY video_id "
                                "ORDER BY 2 DESC LIMIT 1")
    body = _search(worker, f"video:{video_id}", top_k=500)
    assert _videos_of(body) == {video_id}
    assert len(body["results"]) == scenes
    starts = [r["start_ms"] for r in body["results"]]
    assert starts == sorted(starts)


def test_an_unknown_video_is_an_empty_answer_not_an_error(worker, corpus):
    body = _search(worker, "video:definitely_not_a_video a dog", top_k=20)
    assert body["results"] == []
    assert body["filters"][0]["videos"] == 0


def _recording(db):
    row = _one(db, "SELECT recording_id, array_agg(video_id ORDER BY video_id) FROM videos "
                   "WHERE collection = 'MVK' GROUP BY recording_id HAVING count(*) > 1 LIMIT 1")
    if not row:
        pytest.skip("no MVK recording with more than one clip in the corpus")
    return row


def test_excluding_one_clip_excludes_the_whole_recording(worker, corpus, db):
    recording, clips = _recording(db)
    before = _search(worker, f"video:{recording}", top_k=500)
    assert _videos_of(before) == set(clips), "video:<recording> should cover all its clips"

    after = _search(worker, f"video:{recording} -video:{clips[0]}", top_k=500)
    assert after["results"] == []


def test_a_clip_restriction_covers_its_recording(worker, corpus, db):
    recording, clips = _recording(db)
    body = _search(worker, f"video:{clips[-1]}", top_k=500)
    assert _videos_of(body) == set(clips)


def test_a_clips_filmstrip_is_its_whole_recording(backend, corpus, db):
    recording, clips = _recording(db)
    status, body = get_json(f"{backend}/climb/videos/{clips[-1]}/scenes")
    assert status == 200, body
    assert body["recording_id"] == recording
    assert [c["video_id"] for c in body["clips"]] == clips
    offsets = [c["recording_offset_ms"] for c in body["clips"]]
    assert offsets[0] == 0 and offsets == sorted(offsets)
    order = [(s["video_id"], s["shot_index"]) for s in body["scenes"]]
    assert order == sorted(order) and {v for v, _ in order} == set(clips)
    assert body["total"] == _one(db, "SELECT count(*) FROM scenes WHERE video_id = ANY(%s)",
                                 (clips,))[0]
    assert all(s["video_url"].endswith(f"/videos/{s['video_id']}.mp4") for s in body["scenes"])


def test_the_collection_restriction_holds(worker, corpus, db):
    collections = [r[0] for r in _all(db, """
        SELECT DISTINCT v.collection FROM videos v
        WHERE EXISTS (SELECT 1 FROM keyframes k JOIN keyframe_embedding e USING (keyframe_id)
                      WHERE k.video_id = v.video_id)""")]
    if len(collections) < 2:
        pytest.skip("only one collection in the corpus")
    for collection in collections:
        body = _search(worker, "water", top_k=30, collection=collection.lower())
        ids = list(_videos_of(body))
        if ids:
            got = {r[0] for r in _all(db, "SELECT DISTINCT collection FROM videos "
                                          "WHERE video_id = ANY(%s)", (ids,))}
            assert got == {collection}


def _all(db, sql, params=()):
    with db.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    db.rollback()
    return rows


def _word(text):
    words = [w for w in re.findall(r"[^\W\d_]{5,}", text or "") if w.lower() != "video"]
    return words[0] if words else None


def test_text_and_said_must_both_hold_in_the_same_video(worker, corpus, db):
    if not corpus["ocr"] or not corpus["transcripts"]:
        pytest.skip("needs OCR and transcripts")
    rows = _all(db, """
        SELECT k.video_id, t.ocr_text, s.text
        FROM keyframe_text t JOIN keyframes k USING (keyframe_id)
             JOIN transcript_segment s ON s.video_id = k.video_id
        WHERE t.ocr_text ~ '[[:alpha:]]{5,}' AND s.text ~ '[[:alpha:]]{5,}'
        LIMIT 50""")
    pair = next(((v, _word(o), _word(t)) for v, o, t in rows if _word(o) and _word(t)), None)
    if pair is None:
        pytest.skip("no video with both a usable OCR word and a usable spoken word")
    video_id, shown, spoken = pair

    body = _search(worker, f'text:"{shown}" said:"{spoken}"', top_k=100)
    assert video_id in _videos_of(body)
    shown_in, spoken_in = body["filters"]
    assert (shown_in["filter"], spoken_in["filter"]) == (f'text:"{shown}"', f'said:"{spoken}"')
    assert 1 <= spoken_in["videos"] <= shown_in["videos"]
    # Every answer has the word spoken in it. (Not checked for the OCR side with ILIKE: the OCR
    # requirement deliberately also accepts trigram matches of misread text.)
    spoken_somewhere = {r[0] for r in _all(
        db, "SELECT video_id FROM transcript_segment WHERE text ILIKE %s", (f"%{spoken}%",))}
    assert _videos_of(body) <= spoken_somewhere


def test_a_requirement_nobody_meets_names_itself(worker, corpus):
    body = _search(worker, 'a person said:"zzxqvlorptk"', top_k=20)
    assert body["results"] == []
    assert body["filters"] == [{"filter": 'said:"zzxqvlorptk"', "videos": 0}]


def test_tag_restricts_to_tagged_videos(worker, corpus, db):
    row = _one(db, "SELECT t FROM video_info, unnest(tags) t WHERE t ~ '^[a-z]{3,}$' "
                   "GROUP BY t ORDER BY count(*) DESC LIMIT 1")
    if not row:
        pytest.skip("no video info with tags")
    tag, = row
    tagged = {r[0] for r in _all(db, "SELECT video_id FROM video_info WHERE %s = ANY(tags)",
                                 (tag,))}
    body = _search(worker, f"tag:{tag} a person", top_k=50)
    assert body["results"] and _videos_of(body) <= tagged


def test_the_backend_passes_the_collection_and_the_filters(backend, corpus, db):
    video_id, collection = _one(db, "SELECT video_id, collection FROM videos LIMIT 1")
    status, body = get_json(f"{backend}/climb/search?q=video:{video_id}&collection="
                            f"{collection.lower()}")
    assert status == 200, body
    assert body["collection"] == collection
    assert body["filters"] == [{"filter": f"video:{video_id}", "videos": 1}]
    assert {r["video_id"] for r in body["results"]} == {video_id}
    assert all(r["recording_id"] for r in body["results"])


def test_the_browse_list_follows_the_dataset(backend, corpus, db):
    for collection, count in _all(db, "SELECT collection, count(*) FROM videos GROUP BY 1"):
        status, body = get_json(f"{backend}/climb/videos?per_page=100&collection={collection.lower()}")
        assert status == 200, body
        assert body["total"] == count
        listed = [v["video_id"] for v in body["videos"]]
        assert {r[0] for r in _all(db, "SELECT collection FROM videos WHERE video_id = ANY(%s)",
                                   (listed,))} <= {collection}
