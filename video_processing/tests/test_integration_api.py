"""
End-to-end tests against the running stack: search worker (5000), node backend (8000), the
static media mounts, and the database behind both.

These are marked `integration` and are excluded from the default run, because they need a
database with an indexed corpus in it. Run them with:

    pytest -m integration

Anything that depends on a stage having produced rows skips rather than fails when that stage
has not run yet -- "OCR contributes to fusion" is a real failure only once OCR has output.
"""

import random
import re

import pytest

from conftest import fetch_bytes, get_json, post_json

pytestmark = pytest.mark.integration

# Queries chosen to spread across the signals rather than to be answerable: a broadcast corpus
# has news anchors, crowds and captions in it, and every one of these is free text so the
# visual, ocr, caption and transcript retrievers all run.
PROBE_QUERIES = [
    "a news anchor in a newsroom",
    "a man talking to the camera",
    "a crowd of people outdoors",
    "a car driving on a road",
    "a woman dancing",
]


def _search(worker, prompt, **kwargs):
    status, body = post_json(f"{worker}/api/search", {"prompt": prompt, **kwargs})
    assert status == 200, body
    return body


def _first_result_with(results, key):
    return next((r for r in results if r.get(key)), None)


# --- worker -------------------------------------------------------------------------------------

def test_worker_health_reports_ready(worker):
    status, body = get_json(f"{worker}/api/health")
    assert status == 200
    assert body["status"] == "ok"
    assert body["search_engine_ready"] is True
    assert body["device"] in {"cpu", "cuda"}


def test_worker_rejects_a_request_without_prompt(worker):
    """
    The field is `prompt`. A client sending `query` gets a 422 naming the missing field -- worth
    pinning, because a silent empty result set here would be indistinguishable from "no matches".
    """
    status, body = post_json(f"{worker}/api/search", {"query": "a man talking"})
    assert status == 422
    assert any(entry.get("loc", [])[-1:] == ["prompt"] for entry in body["detail"])


def test_worker_returns_one_row_per_keyframe(worker, corpus):
    """
    A page is keyframes, not scenes, so the same scene may legitimately appear several times --
    that repetition is the ranking saying it is confident. What must never repeat is a keyframe.
    """
    body = _search(worker, "a man talking to the camera", top_k=40)
    results = body["results"]
    assert results, "no results for a generic query against a populated corpus"

    keyframe_ids = [r["keyframe_id"] for r in results]
    assert len(keyframe_ids) == len(set(keyframe_ids)), "the same keyframe appeared twice"

    for result in results:
        assert result["keyframe_id"] is not None
        assert result["video_id"]
        assert result["score"] > 0
        assert result["signals"]
        # Every row carries its whole shot, so the strip under the player can scrub it without
        # going back to browse.
        assert result["keyframes"], "result carries no filmstrip"
        assert result["keyframe_id"] in {k["keyframe_id"] for k in result["keyframes"]}


def test_worker_does_not_pin_the_top_of_the_ranking_to_the_first_keyframe(worker, corpus):
    """
    Captions only exist for kf_index 0 (CAPTION_SCOPE defaults to `shot`). Fusing on keyframes
    without expanding a caption hit across its scene hands kf 0 a second retriever's worth of
    score that no sibling frame can earn.

    Deliberately a small top_k. The bias is a matter of degree and it bites hardest at the very
    top: measured on the skier query, before the fix the top 15 were 15/15 kf_index 0 while the
    top 40 were only 82% -- so a 40-result sample sees "plenty of variety" and misses it entirely.
    The top of page one is also the only part an operator reads under competition time.

    kf 0 is ~40% of all keyframes in the corpus (14,345 scenes over 35,558 keyframes), so a
    two-thirds ceiling still leaves generous headroom over chance.
    """
    results = _search(worker, "a skier jumping off a ramp", top_k=15)["results"]
    assert results

    first_frames = [r for r in results if r["kf_index"] == 0]
    share = len(first_frames) / len(results)
    assert share <= 0.67, (
        f"kf_index 0 is {share:.0%} of the top {len(results)} "
        f"({len(first_frames)}/{len(results)}) -- captions are pinning the ranking to it"
    )


def test_worker_honours_top_k(worker, corpus):
    body = _search(worker, "a man talking to the camera", top_k=5)
    assert len(body["results"]) <= 5


def test_worker_exclude_drops_that_video(worker, corpus):
    body = _search(worker, "a man talking to the camera", top_k=40)
    victim = body["results"][0]["video_id"]

    filtered = _search(worker, "a man talking to the camera", top_k=40, exclude=[victim])
    assert all(r["video_id"] != victim for r in filtered["results"])


def test_worker_excludes_every_video_written_into_the_prompt(worker, corpus):
    """
    The exclude button appends `--exclude: 00083, 00140, 00004` to the query text, and for a long
    time that suffix was the only carrier -- so this is the path that actually ran in the UI.

    `--exclude:\\s*([^\\s]*)` stopped at the first space: video one was excluded, videos two and
    three were dropped, and their ids stayed in the query where they reached the text embedding and
    came back as OCR tokens. Excluding a second video did not merely fail, it moved the results.
    Three videos, because a one-video list passed the old pattern perfectly.
    """
    query = "a man talking to the camera"
    victims = []
    for result in _search(worker, query, top_k=40)["results"]:
        if result["video_id"] not in victims:
            victims.append(result["video_id"])
        if len(victims) == 3:
            break
    assert len(victims) == 3, "corpus has fewer than three videos in the top 40"

    suffix = ", ".join(victims)
    filtered = _search(worker, f"{query} --exclude: {suffix}", top_k=40)["results"]

    survivors = {r["video_id"] for r in filtered} & set(victims)
    assert not survivors, f"excluded {victims} but {sorted(survivors)} came back"


def test_worker_similar_returns_neighbours(worker, corpus, db):
    with db.cursor() as cur:
        cur.execute("SELECT keyframe_id FROM keyframe_embedding ORDER BY keyframe_id LIMIT 1;")
        keyframe_id = cur.fetchone()[0]

    status, body = post_json(f"{worker}/api/similar", {"keyframe_id": keyframe_id, "top_k": 10})
    assert status == 200, body
    results = body["results"]
    assert results, "find-similar returned nothing for a keyframe that has an embedding"

    assert keyframe_id not in {r["keyframe_id"] for r in results}, "a keyframe is its own neighbour"
    assert len({r["keyframe_id"] for r in results}) == len(results), "the same keyframe twice"

    ranks = [r["signals"]["similar"] for r in results]
    assert ranks == sorted(ranks), f"neighbours out of distance order: {ranks}"


# --- backend ------------------------------------------------------------------------------------

def test_backend_health_sees_the_worker(backend, worker):
    status, body = get_json(f"{backend}/climb/health")
    assert status == 200
    assert body["status"] == "ok"
    assert body["embedding_service"]["reachable"] is True
    assert body["embedding_service"]["ready"] is True


def test_backend_search_requires_q(backend):
    status, body = get_json(f"{backend}/climb/search")
    assert status == 400
    assert "q" in body["error"]


def test_backend_search_pages_without_repeating_keyframes(backend, corpus):
    status, first = get_json(f"{backend}/climb/search?q=a+man+talking&page=1&per_page=10")
    assert status == 200, first
    if first["total"] <= 10:
        pytest.skip("corpus too small to page")

    status, second = get_json(f"{backend}/climb/search?q=a+man+talking&page=2&per_page=10")
    assert status == 200, second

    assert first["page"] == 1 and second["page"] == 2
    assert first["total"] == second["total"]
    assert first["has_more"] is True
    assert len(first["results"]) == 10

    # Keyframes, not scenes: a shot can straddle the page boundary now, and should.
    overlap = ({r["keyframe_id"] for r in first["results"]}
               & {r["keyframe_id"] for r in second["results"]})
    assert not overlap, f"pages 1 and 2 share keyframes: {overlap}"


def test_backend_search_serves_the_second_call_from_cache(backend, corpus):
    query = "the cache probe query"
    get_json(f"{backend}/climb/search?q={query.replace(' ', '+')}")
    _, again = get_json(f"{backend}/climb/search?q={query.replace(' ', '+')}")
    assert again["cached"] is True


def test_backend_search_results_carry_playable_metadata(backend, corpus):
    _, body = get_json(f"{backend}/climb/search?q=a+man+talking&per_page=5")
    for result in body["results"]:
        # Absolute URLs: mediaPaths.js builds them from BACKEND_URL so the frontend can be served
        # from a different origin than the media.
        assert "/thumbs/" in result["thumbnail_url"]
        assert "/kf/" in result["keyframe_url"]
        assert result["start_time_ms"] is not None
        assert result["end_time_ms"] >= result["start_time_ms"]
        assert result["fps"] and result["fps"] > 0


def test_backend_similar_rejects_a_non_integer_keyframe(backend):
    status, body = get_json(f"{backend}/climb/search/similar/not-a-number")
    assert status == 400
    assert "keyframe_id" in body["error"]


def test_backend_similar_returns_results(backend, corpus, db):
    with db.cursor() as cur:
        cur.execute("SELECT keyframe_id FROM keyframe_embedding ORDER BY keyframe_id LIMIT 1;")
        keyframe_id = cur.fetchone()[0]

    status, body = get_json(f"{backend}/climb/search/similar/{keyframe_id}?per_page=5")
    assert status == 200, body
    assert body["source_keyframe"] == keyframe_id
    assert body["results"]


def test_backend_lists_videos_with_covers(backend, corpus):
    status, body = get_json(f"{backend}/climb/videos?per_page=100")
    assert status == 200
    assert body["total"] == corpus["videos"]
    assert body["videos"]
    for video in body["videos"]:
        assert video["num_scenes"] >= 0
        assert video["duration_sec"] > 0
        if video["num_scenes"] and corpus["keyframes"]:
            assert video["thumbnail_url"], f"{video['video_id']} has scenes but no cover frame"


def test_backend_video_details_and_missing_video(backend, corpus):
    _, listing = get_json(f"{backend}/climb/videos?per_page=1")
    video_id = listing["videos"][0]["video_id"]

    status, body = get_json(f"{backend}/climb/videos/{video_id}")
    assert status == 200
    assert body["video_id"] == video_id
    assert "/videos/" in body["video_url"]
    assert body["fps"] > 0

    status, body = get_json(f"{backend}/climb/videos/definitely-not-a-video")
    assert status == 404
    assert "No such video" in body["error"]


def test_backend_video_scenes_match_the_database(backend, corpus, db):
    _, listing = get_json(f"{backend}/climb/videos?per_page=1")
    video_id = listing["videos"][0]["video_id"]

    status, body = get_json(f"{backend}/climb/videos/{video_id}/scenes")
    assert status == 200
    with db.cursor() as cur:
        cur.execute("SELECT count(*) FROM scenes WHERE video_id = %s;", (video_id,))
        expected = cur.fetchone()[0]

    assert body["total"] == expected
    assert len(body["scenes"]) == expected, "unpaginated scenes must return the whole video"
    assert [s["shot_index"] for s in body["scenes"]] == sorted(s["shot_index"] for s in body["scenes"])


def test_backend_paginated_scenes_page_by_scene(backend, corpus, db):
    """
    Paging the filmstrip has to count scenes, because `total` counts scenes and the frontend
    divides one by the other. Paging the scene x keyframe join instead returns fewer scenes than
    asked for and splits a scene across a page boundary.
    """
    with db.cursor() as cur:
        cur.execute("""
            SELECT s.video_id
            FROM scenes s JOIN keyframes k ON k.scene_id = s.scene_id
            GROUP BY s.video_id
            HAVING count(*) > count(DISTINCT s.scene_id) AND count(DISTINCT s.scene_id) > 12
            ORDER BY s.video_id LIMIT 1;
        """)
        row = cur.fetchone()
    if not row:
        pytest.skip("no video with multi-keyframe scenes and enough scenes to page")
    video_id = row[0]

    _, first = get_json(f"{backend}/climb/videos/{video_id}/scenes?page=1&per_page=10")
    _, second = get_json(f"{backend}/climb/videos/{video_id}/scenes?page=2&per_page=10")

    assert len(first["scenes"]) == 10, "a page of 10 scenes must contain 10 scenes"
    overlap = {s["scene_id"] for s in first["scenes"]} & {s["scene_id"] for s in second["scenes"]}
    assert not overlap, f"scene {overlap} appears on both pages -- split across the boundary"


# --- static media mounts --------------------------------------------------------------------

def test_static_mounts_serve_real_images(backend, corpus):
    if not corpus["keyframes"]:
        pytest.skip("no keyframes")
    _, body = get_json(f"{backend}/climb/search?q=a+man+talking&per_page=1")
    result = body["results"][0]

    for url in (result["thumbnail_url"], result["keyframe_url"]):
        status, content_type, payload = fetch_bytes(url)
        assert status == 200, f"{url} -> {status}"
        assert content_type.startswith("image/"), f"{url} served as {content_type}"
        # paths.py writes .webp for both sizes; RIFF....WEBP is the container's magic.
        assert payload[:4] == b"RIFF" and payload[8:12] == b"WEBP", f"{url} is not a WebP"
        assert len(payload) > 500


def test_static_mount_serves_the_web_video(backend, corpus):
    _, listing = get_json(f"{backend}/climb/videos?per_page=1")
    _, details = get_json(f"{backend}/climb/videos/{listing['videos'][0]['video_id']}")

    status, content_type, payload = fetch_bytes(details["video_url"])
    assert status == 200, f"{details['video_url']} -> {status}"
    assert "video" in content_type or "mp4" in content_type
    assert len(payload) > 10_000


def test_static_mount_404s_for_something_that_is_not_there(backend):
    status, _, _ = fetch_bytes(f"{backend}/kf/nope/nope/nope.jpg")
    assert status == 404


# --- fusion signals ---------------------------------------------------------------------------

def test_every_retriever_contributes_to_fusion(worker, corpus, db):
    """
    All four RRF signals have to actually reach the fused list -- the first time OCR, caption and
    transcript have had rows to contribute at all.

    One probe is built from a word that is genuinely on screen somewhere in the corpus. Without it
    the OCR leg of this test passes or fails on whether the generic probes happen to collide with
    burned-in text, which is luck rather than a property of the system.
    """
    probes = list(PROBE_QUERIES)
    grounded = _distinctive_token(db, "keyframe_text", "ocr_text") if corpus["ocr"] else None
    if grounded:
        probes.append(f"a shot with {grounded} written in it")

    seen = set()
    for prompt in probes:
        seen.update(_search(worker, prompt, top_k=40)["signals"])

    assert "visual" in seen
    for signal, count_key, stage in [("ocr", "ocr", "ocr"),
                                     ("caption", "caption_embeddings", "caption + embed-text"),
                                     ("transcript", "transcript_embeddings", "asr + embed-text")]:
        if not corpus[count_key]:
            continue
        assert signal in seen, (
            f"'{signal}' never contributed although {stage} has produced "
            f"{corpus[count_key]} rows; probes were {probes}"
        )


def _distinctive_token(db, table, column, pattern=r"[A-Za-z]{5,}"):
    with db.cursor() as cur:
        cur.execute(f"SELECT {column} FROM {table} WHERE length({column}) BETWEEN 6 AND 200 LIMIT 400;")
        for (text,) in cur.fetchall():
            for token in re.findall(pattern, text or ""):
                if token.lower() not in {"http", "https", "video"}:
                    return token
    return None


def test_an_explicit_ocr_phrase_fires_the_phrase_retriever(worker, corpus, db):
    if not corpus["ocr"]:
        pytest.skip("no OCR rows")
    token = _distinctive_token(db, "keyframe_text", "ocr_text")
    if not token:
        pytest.skip("no usable OCR token in the corpus")

    body = _search(worker, f'text:"{token}"', top_k=20)
    assert "ocr_phrase" in body["signals"], f'text:"{token}" did not reach the phrase retriever'
    assert body["results"]


def test_an_explicit_speech_phrase_fires_the_asr_retriever(worker, corpus, db):
    if not corpus["transcripts"]:
        pytest.skip("no transcript rows")
    token = _distinctive_token(db, "transcript_segment", "text")
    if not token:
        pytest.skip("no usable transcript token in the corpus")

    body = _search(worker, f'said:"{token}"', top_k=20)
    assert "asr_phrase" in body["signals"], f'said:"{token}" did not reach the ASR retriever'
    assert body["results"]


def test_a_natural_language_query_can_reach_the_lexical_ocr_retriever(worker, corpus, db):
    """
    The WP7 bug: `websearch_to_tsquery` ANDed every word, so a sentence containing a word that is
    genuinely on screen still matched nothing. The tokens must be ORed.
    """
    if not corpus["ocr"]:
        pytest.skip("no OCR rows")
    token = _distinctive_token(db, "keyframe_text", "ocr_text")
    if not token:
        pytest.skip("no usable OCR token in the corpus")

    body = _search(worker, f"a scene showing the {token} sign somewhere in it", top_k=20)
    assert "ocr" in body["signals"], (
        f"a sentence containing the on-screen word '{token}' did not reach the lexical OCR "
        f"retriever -- the tsquery is ANDing again"
    )


# --- temporal ---------------------------------------------------------------------------------

def _temporal(worker, prompt, stages):
    body = _search(worker, prompt, top_k=10)
    assert body["temporal"] is not None, f"{prompt!r} was not parsed as a sequence"
    assert len(body["temporal"]["stages"]) == stages
    return body


def test_two_stage_sequence_returns_chains(worker, corpus):
    body = _temporal(worker, "a man talking >> a crowd of people", 2)
    if not body["results"]:
        pytest.skip("no two-stage chain in this corpus")
    for chain in body["results"]:
        assert len(chain["temporal_partners"]) == 1
        assert len(chain["temporal_gaps_ms"]) == 1
        assert chain["temporal_partners"][0]["video_id"] == chain["video_id"]


def test_sequence_partners_are_in_time_order(worker, corpus):
    body = _temporal(worker, "a man talking >> a crowd of people", 2)
    if not body["results"]:
        pytest.skip("no two-stage chain in this corpus")
    for chain in body["results"]:
        times = [chain["start_ms"]] + [p["start_ms"] for p in chain["temporal_partners"]]
        assert times == sorted(times), f"chain out of order: {times}"
        assert all(gap > 0 for gap in chain["temporal_gaps_ms"])


def test_an_explicit_gap_is_honoured(worker, corpus):
    body = _temporal(worker, "a man talking >>(d120) a crowd of people", 2)
    if not body["results"]:
        pytest.skip("no chain within the 120 s window")
    for chain in body["results"]:
        assert chain["temporal_gaps_ms"][0] < 120_000


def test_a_tighter_gap_cannot_return_more_chains(worker, corpus):
    wide = _temporal(worker, "a man talking >>(d120) a crowd of people", 2)
    tight = _temporal(worker, "a man talking >>(d5) a crowd of people", 2)
    assert len(tight["results"]) <= len(wide["results"])


def test_three_stage_chain(worker, corpus):
    body = _temporal(worker, "a man talking >> a crowd of people >> a car driving", 3)
    for chain in body["results"]:
        assert len(chain["temporal_partners"]) == 2
        assert len(chain["temporal_gaps_ms"]) == 2
        times = [chain["start_ms"]] + [p["start_ms"] for p in chain["temporal_partners"]]
        assert times == sorted(times)


def test_a_phrase_stage_can_anchor_a_sequence(worker, corpus, db):
    if not corpus["ocr"]:
        pytest.skip("no OCR rows")
    token = _distinctive_token(db, "keyframe_text", "ocr_text")
    if not token:
        pytest.skip("no usable OCR token in the corpus")

    body = _temporal(worker, f'text:"{token}" >> a man talking', 2)
    assert any(signal.startswith("s0:ocr_phrase") for signal in body["signals"]), body["signals"]


# --- the ANN index ------------------------------------------------------------------------------

MIN_ROWS_FOR_ANN = 2000  # below this a sequential scan is the planner being right


def test_the_ann_index_is_used_for_the_visual_query(db, corpus):
    """
    The query has to be shaped exactly as `retrievers.VISUAL_ANN` shapes it: the vector as a bind
    parameter (a column reference silently loses the index) and model_id inline so the partial
    index predicate is provable.
    """
    from db.index_ops import ann_index_name
    from retrieval.retrievers import VISUAL_ANN

    with db.cursor() as cur:
        cur.execute("""
            SELECT e.model_id, m.dims, count(*)
            FROM keyframe_embedding e JOIN embedding_model m ON m.model_id = e.model_id
            GROUP BY e.model_id, m.dims ORDER BY count(*) DESC LIMIT 1;
        """)
        model_id, dims, rows = cur.fetchone()

        cur.execute("SELECT embedding FROM keyframe_embedding WHERE model_id = %s LIMIT 1;",
                    (model_id,))
        vector = cur.fetchone()[0]

        # model_id inlined, exactly as the retriever does it, so the WHERE of the partial index
        # can be matched; everything else stays a bind parameter.
        sql = VISUAL_ANN.replace("%(model_id)s", str(model_id))
        cur.execute("EXPLAIN " + sql,
                    {"dims": dims, "query": vector, "oversample": 100})
        plan = "\n".join(line for (line,) in cur.fetchall())

    if rows < MIN_ROWS_FOR_ANN and "Seq Scan" in plan:
        pytest.skip(f"only {rows} embeddings -- a sequential scan is the correct plan here")

    assert ann_index_name(model_id) in plan, f"HNSW index not used:\n{plan}"
    assert "Seq Scan" not in plan, f"fell back to a sequential scan:\n{plan}"


class _RecordingCursor:
    """Forwards everything to a real cursor, remembering the SQL it was asked to run."""

    def __init__(self, inner, log):
        self._inner, self._log = inner, log

    def execute(self, sql, params=None):
        self._log.append((sql, params))
        return self._inner.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def __enter__(self):
        self._inner.__enter__()
        return self

    def __exit__(self, *exc):
        return self._inner.__exit__(*exc)


class _RecordingConnection:
    def __init__(self, inner):
        self._inner, self.statements = inner, []

    def cursor(self, *a, **kw):
        return _RecordingCursor(self._inner.cursor(*a, **kw), self.statements)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _biggest_model(cur):
    cur.execute("""
        SELECT e.model_id, m.dims, count(*)
        FROM keyframe_embedding e JOIN embedding_model m ON m.model_id = e.model_id
        GROUP BY e.model_id, m.dims ORDER BY count(*) DESC LIMIT 1;
    """)
    return cur.fetchone()


def _off_corpus_vector(dims, seed):
    """
    A query vector that is *not* one of the stored embeddings.

    This matters more than it looks. Querying with a vector taken from the table is the easy
    case -- HNSW lands on the exact match and its immediate neighbourhood even with a tiny
    ef_search, so a truncated candidate set still scores well and the test proves nothing. A real
    query is a *text* embedding, which sits nowhere near any single image vector, and that is the
    case where a 40-candidate cap actually loses the true neighbours. Seeded, so it is deterministic.
    """
    rng = random.Random(seed)
    return "[" + ",".join(f"{rng.gauss(0, 1):.5f}" for _ in range(dims)) + "]"


def test_visual_raises_ef_search_to_cover_the_oversample(db, corpus):
    """
    `hnsw.ef_search` is a hard cap on how many rows an HNSW scan returns, and it defaults to 40 --
    so `LIMIT 1000` against the index quietly yields 40 unless someone raises it. There is no
    error and no warning; the reranker is simply handed 40 candidates instead of 1000, and it only
    starts happening once the corpus is big enough for the planner to prefer the index.

    Asserted on the statements `visual()` actually issues, so it fails if the SET is dropped,
    rather than on pgvector's behaviour, which would pass no matter what our code did.
    """
    from config import Config
    from retrieval import retrievers

    with db.cursor() as cur:
        model_id, dims, rows = _biggest_model(cur)
    db.rollback()
    if rows < MIN_ROWS_FOR_ANN:
        pytest.skip(f"only {rows} embeddings -- the planner would pick a seq scan anyway")

    conf = Config()
    spy = _RecordingConnection(db)
    retrievers.visual(spy, _off_corpus_vector(dims, 0), model_id, dims, [], None, limit=20)
    db.rollback()

    sets = [(sql, params) for sql, params in spy.statements if "ef_search" in sql.lower()]
    assert sets, ("visual() never set hnsw.ef_search, so the ANN scan is capped at pgvector's "
                  "default of 40 candidates regardless of ANN_OVERSAMPLE")
    _, params = sets[0]
    assert params[0] == min(conf.ANN_OVERSAMPLE, conf.HNSW_EF_SEARCH_MAX), (
        f"ef_search set to {params[0]}, which does not cover ANN_OVERSAMPLE={conf.ANN_OVERSAMPLE}")


def test_the_ann_path_agrees_with_exhaustive_search(db, corpus):
    """
    The end-to-end property the ef_search fix protects: oversample-then-rerank should land on
    nearly the same scenes as scanning every row and sorting exactly.

    Asserted as an *advantage over the broken configuration, measured in the same run*, rather
    than as an absolute number. Recall falls as the corpus grows -- 19.2/20 at 2.9k embeddings,
    15.0/20 at 7.5k -- because a fixed 1000-candidate oversample is a shrinking fraction of the
    whole, and the ceiling is binary quantization rather than the index. An absolute threshold
    would therefore start failing partway through the corpus for a system working exactly as
    designed. The *gap* between the two arms is what tracks the bug: 3.0/20 vs 15.0/20 at 7.5k,
    and it widens with scale rather than narrowing.
    """
    from config import Config
    from retrieval import retrievers
    from retrieval.retrievers import VISUAL_ANN, VISUAL_RERANK

    with db.cursor() as cur:
        model_id, dims, rows = _biggest_model(cur)
    db.rollback()
    if rows < MIN_ROWS_FOR_ANN:
        pytest.skip(f"only {rows} embeddings -- ANN and exact are the same thing at this size")

    scores, broken = [], []
    for seed in (0, 1, 2):
        vector = _off_corpus_vector(dims, seed)
        with db.cursor() as cur:
            # Ground truth: every row, sorted by the exact distance, no index involved.
            cur.execute(f"""
                SELECT keyframe_id FROM keyframe_embedding WHERE model_id = {model_id}
                ORDER BY embedding::halfvec(%(dims)s) <=> %(query)s::halfvec(%(dims)s)
            """, {"dims": dims, "query": vector})
            exact_ids = [r[0] for r in cur.fetchall()]
        exact20 = [s for s, _ in retrievers._resolve_scenes(db, exact_ids, [], None, 20)]
        db.rollback()

        got20 = [s for s, _ in retrievers.visual(db, vector, model_id, dims, [], None, limit=20)]
        db.rollback()
        scores.append(len(set(got20) & set(exact20)))

        # The same query with ef_search left at pgvector's default -- the bug reproduced live, so
        # the comparison stays valid at any corpus size.
        with db.cursor() as cur:
            cur.execute("SET LOCAL hnsw.ef_search = 40")
            cur.execute(VISUAL_ANN.replace("%(model_id)s", str(model_id)),
                        {"dims": dims, "query": vector, "oversample": Config().ANN_OVERSAMPLE})
            ids = [r[0] for r in cur.fetchall()]
            cur.execute(VISUAL_RERANK,
                        {"ids": ids, "model_id": model_id, "dims": dims, "query": vector})
            broken_ids = [r[0] for r in cur.fetchall()]
        broken20 = [s for s, _ in retrievers._resolve_scenes(db, broken_ids, [], None, 20)]
        db.rollback()
        broken.append(len(set(broken20) & set(exact20)))

    mean, mean_broken = sum(scores) / len(scores), sum(broken) / len(broken)
    assert mean >= mean_broken + 6, (
        f"visual() recall@20 is {mean:.1f}/20 ({scores}) against exhaustive search, while the "
        f"deliberately broken ef_search=40 configuration scores {mean_broken:.1f}/20 ({broken}). "
        f"The fix should be far ahead; if they are close, the candidate set is truncated again.")


def test_every_expected_index_exists(db, corpus):
    from db.index_ops import index_status
    missing = [name for name, exists, _ in index_status(db) if not exists]
    assert not missing, f"indexes missing: {missing}"


# --- corpus health ------------------------------------------------------------------------------

def test_known_damaged_videos_are_flagged(db, corpus):
    """00016 and 00024 are corrupt H.264 in the V3C1 subset; they must index, and be marked."""
    with db.cursor() as cur:
        cur.execute("SELECT video_id, damaged FROM videos WHERE video_id IN ('00016', '00024');")
        found = dict(cur.fetchall())
    if not found:
        pytest.skip("neither known-damaged video is in this corpus yet")

    for video_id, damaged in found.items():
        with db.cursor() as cur:
            cur.execute("SELECT count(*) FROM scenes WHERE video_id = %s;", (video_id,))
            scenes = cur.fetchone()[0]
        assert scenes > 0, f"{video_id} produced no scenes at all"
        assert damaged is True, f"{video_id} is known corrupt but videos.damaged is {damaged}"


def test_every_scene_that_has_keyframes_has_them_on_disk(db, corpus):
    """A keyframe row whose file is missing is a broken thumbnail in the grid."""
    from pathlib import Path

    from pipeline import paths

    with db.cursor() as cur:
        cur.execute("""
            SELECT k.video_id, s.shot_index, k.kf_index
            FROM keyframes k JOIN scenes s ON s.scene_id = k.scene_id
            ORDER BY random() LIMIT 40;
        """)
        sample = cur.fetchall()

    missing = [(v, s, i) for v, s, i in sample
               if not Path(paths.keyframe_path(v, s, i)).exists()]
    assert not missing, f"{len(missing)}/{len(sample)} sampled keyframes are not on disk: {missing[:5]}"
