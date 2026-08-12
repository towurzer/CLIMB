"""
DRES submission and AVS session tests, driven through the real backend against the mock DRES
server (`backend/mock-dres-server.js`, port 8080).

What these can and cannot prove: the mock enforces the rules the real server documents -- a bad
session is a 401, an unknown evaluation a 404, and a submission naming a media item without
`mediaItemCollectionName` is a 400. Everything here is therefore **mock-verified**. Only a live
DRES can finally confirm the collection-name derivation.

Run with:

    node backend/mock-dres-server.js &
    (cd backend && npm start) &
    pytest -m integration tests/test_integration_dres.py
"""

import contextlib
import os
import re
import signal
import subprocess
import time
from pathlib import Path

import pytest

from conftest import get_json, post_json

pytestmark = pytest.mark.integration

USERNAME = "admin"
PASSWORD = "password"

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def connected(backend, dres):
    """
    A backend connected to the mock, with the mock reset first.

    Function-scoped and not shared: `dresState` in the controller is module-level, so a test that
    disconnects would otherwise poison every test after it. Resetting the mock also clears its
    sessions, which is exactly why the connect has to come after the reset.
    """
    post_json(f"{dres}/mock/reset", {})
    status, body = post_json(f"{backend}/climb/dres/connect", {
        "username": USERNAME, "password": PASSWORD, "dres_url": dres, "dres_name": "IVADL26",
    })
    assert status == 200, body
    return body


@contextlib.contextmanager
def spawn_backend(port, **env_extra):
    """
    A backend of our own on another port.

    Two things in the backend are module-level process state -- the DRES connection and the 30 s
    collection-name cache -- so a test that needs them empty cannot get there by resetting the
    mock. Starting a second process is the honest way to get a clean one.
    """
    env = {**os.environ, "BACKEND_PORT": str(port), **{k: str(v) for k, v in env_extra.items()}}
    server = subprocess.Popen(["node", "server.js"], cwd=REPO_ROOT / "backend", env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              start_new_session=True)
    base = f"http://localhost:{port}"
    try:
        for _ in range(50):
            try:
                if get_json(f"{base}/climb/avs/sessions", timeout=1)[0] == 200:
                    break
            except Exception:
                pass
            time.sleep(0.2)
        else:
            pytest.skip(f"could not start a second backend on {port}")
        yield base
    finally:
        os.killpg(os.getpgid(server.pid), signal.SIGTERM)
        server.wait(timeout=10)


def submissions(dres):
    _, body = get_json(f"{dres}/mock/submissions")
    return body


def submit_kis(backend, video_id="00043", start=1000, end=4000, **extra):
    return post_json(f"{backend}/climb/dres/submit/kis", {
        "video_id": video_id, "start_time_ms": start, "end_time_ms": end, **extra,
    })


# --- connect / status ---------------------------------------------------------------------------

def test_wrong_password_is_passed_through_not_swallowed(backend, dres):
    post_json(f"{dres}/mock/reset", {})
    status, body = post_json(f"{backend}/climb/dres/connect", {
        "username": USERNAME, "password": "wrong", "dres_url": dres,
    })
    assert status == 500
    assert body["dres_error"]["description"] == "Invalid credentials"


def test_connect_picks_the_named_evaluation(connected):
    assert connected["evaluation_id"] == "eval-ivadl26"
    assert connected["selected_name"] == "IVADL26"
    assert connected["defaulted"] is False


def test_connect_falls_back_to_the_first_evaluation_and_says_so(backend, dres):
    post_json(f"{dres}/mock/reset", {})
    status, body = post_json(f"{backend}/climb/dres/connect", {
        "username": USERNAME, "password": PASSWORD, "dres_url": dres,
        "dres_name": "NoSuchEvaluation",
    })
    assert status == 200
    assert body["defaulted"] is True
    assert body["evaluation_id"] == "eval-ivadl26"
    assert "not found" in body["message"]


def test_status_reports_the_connection(backend, connected):
    status, body = get_json(f"{backend}/climb/dres/status")
    assert status == 200
    assert body["connected"] is True
    assert body["evaluation_id"] == "eval-ivadl26"


# --- the mediaItemCollectionName derivation (WP11) -----------------------------------------------

def test_kis_submission_carries_the_collection_name(backend, dres, connected):
    status, body = submit_kis(backend)
    assert status == 200, body
    assert body["status"] == "success"

    delivered = submissions(dres)[-1]
    assert delivered["accepted"] is True
    assert delivered["answer"]["mediaItemName"] == "00043"
    assert delivered["answer"]["mediaItemCollectionName"] == "IVADL"


def test_a_submission_is_one_answer_in_one_answer_set(backend, dres, connected):
    """DRES grades `answers.first()` only, so AVS sends N POSTs rather than one POST of N."""
    for index in range(3):
        status, _ = submit_kis(backend, video_id=f"0004{index}")
        assert status == 200

    delivered = submissions(dres)
    assert len(delivered) == 3, "three shots must be three POSTs"
    for record in delivered:
        assert record["shape"] == {"answerSets": 1, "answersInFirstSet": 1, "maxAnswersInAnySet": 1}


def test_without_a_collection_on_the_task_dres_rejects_the_submission(dres):
    """
    The negative control: what every CLIMB submission looked like before the derivation existed.
    The mock rejects it with the real server's error, which is the whole point of the mock.

    On its own backend, because the shared one has a warm collection cache from earlier tests and
    the controller deliberately prefers a stale name to no name.
    """
    post_json(f"{dres}/mock/reset", {})
    post_json(f"{dres}/mock/task", {"collectionName": None, "mediaCollectionName": None,
                                    "mediaItemCollectionName": None, "collection": None})

    with spawn_backend(8012) as base:
        assert post_json(f"{base}/climb/dres/connect", {
            "username": USERNAME, "password": PASSWORD, "dres_url": dres, "dres_name": "IVADL26",
        })[0] == 200

        status, body = submit_kis(base)
        assert status == 400
        assert body["dres_status"] == 400
        assert "mediaItemCollectionName" in body["details"]

    delivered = submissions(dres)[-1]
    assert delivered["accepted"] is False
    assert "mediaItemCollectionName" not in delivered["answer"]


def test_the_collection_name_follows_a_task_change_rather_than_sticking(dres):
    """
    A competition switches tasks, and a task can switch collection with it. The cache is 30 s, so
    this test has to outlast it -- caching for the session would eventually submit the old name
    against the new task, which is a wrong answer that looks like a right one.

    On its own backend so the 30 s cache it leaves behind cannot decide what a later test sees.
    """
    post_json(f"{dres}/mock/reset", {})
    with spawn_backend(8013) as base:
        assert post_json(f"{base}/climb/dres/connect", {
            "username": USERNAME, "password": PASSWORD, "dres_url": dres, "dres_name": "IVADL26",
        })[0] == 200

        assert submit_kis(base)[0] == 200
        assert submissions(dres)[-1]["answer"]["mediaItemCollectionName"] == "IVADL"

        post_json(f"{dres}/mock/task", {"collectionName": "V3C1", "mediaCollectionName": "V3C1"})
        time.sleep(31)  # longer than COLLECTION_TTL_MS in dres.controller.js

        assert submit_kis(base)[0] == 200
        assert submissions(dres)[-1]["answer"]["mediaItemCollectionName"] == "V3C1"


# --- verdicts -------------------------------------------------------------------------------

@pytest.mark.parametrize("verdict", ["CORRECT", "WRONG", "UNDECIDABLE", "INDETERMINATE"])
def test_the_verdict_is_passed_back_untouched(backend, dres, connected, verdict):
    post_json(f"{dres}/mock/verdict", {"verdict": verdict, "httpStatus": 200})
    status, body = submit_kis(backend)
    assert status == 200
    assert body["verdict"] == verdict
    assert body["dres_status"] == 200


def test_a_202_is_reported_as_pending(backend, dres, connected):
    post_json(f"{dres}/mock/verdict", {"verdict": "INDETERMINATE", "httpStatus": 202})
    status, body = submit_kis(backend)
    assert status == 200
    assert body["status"] == "pending"
    assert body["status_summary"] == "Accepted - awaiting verdict"


def test_submitting_without_a_connection_is_a_401(backend, dres):
    """Point the controller at a dead server so its connect fails, then submit."""
    post_json(f"{backend}/climb/dres/connect", {
        "username": USERNAME, "password": PASSWORD, "dres_url": "http://localhost:9",
    })
    status, body = submit_kis(backend)
    assert status == 401
    assert "Not connected" in body["error"]


# --- VQA ------------------------------------------------------------------------------------

def test_vqa_text_only_needs_no_collection(backend, dres, connected):
    status, body = post_json(f"{backend}/climb/dres/submit/vqa/text", {"text_answer": "a red bus"})
    assert status == 200, body

    answer = submissions(dres)[-1]["answer"]
    assert answer["text"] == "a red bus"
    assert "mediaItemName" not in answer or answer["mediaItemName"] is None


def test_vqa_with_a_shot_gets_the_collection_name_too(backend, dres, connected):
    status, body = post_json(f"{backend}/climb/dres/submit/vqa", {
        "text_answer": "a red bus", "video_id": "00043",
        "start_time_ms": 1000, "end_time_ms": 4000,
    })
    assert status == 200, body

    answer = submissions(dres)[-1]["answer"]
    assert answer["text"] == "a red bus"
    assert answer["mediaItemCollectionName"] == "IVADL"


@pytest.mark.parametrize("path", ["/climb/dres/submit/vqa", "/climb/dres/submit/vqa/text"])
def test_vqa_refuses_an_empty_answer(backend, connected, path):
    status, body = post_json(f"{backend}{path}", {"text_answer": "   "})
    assert status == 400
    assert "VQA text is required" in body["error"]


# --- AVS sessions -----------------------------------------------------------------------------

CODE_PATTERN = re.compile(r"^[BCDFGHJKLMNPQRSTVWXYZ]{4}$")


@pytest.fixture
def avs_session(backend):
    status, body = post_json(f"{backend}/climb/avs/session", {"name": "pytest"})
    assert status == 201, body
    return body


def test_a_new_session_gets_a_four_letter_code(avs_session):
    assert CODE_PATTERN.match(avs_session["code"]), avs_session["code"]
    assert avs_session["name"] == "pytest"
    assert avs_session["scenes"] == []
    assert avs_session["counts"] == {"instances": 0, "distinctVideos": 0}
    assert 0 < avs_session["expiresInMs"] <= 5 * 60 * 1000


def test_joining_by_code_is_case_insensitive(backend, avs_session):
    code = avs_session["code"]
    status, body = post_json(f"{backend}/climb/avs/session/{code.lower()}/join", None)
    assert status == 200
    assert body["code"] == code


def test_joining_an_unknown_code_is_a_404(backend):
    status, body = post_json(f"{backend}/climb/avs/session/ZZZZ/join", None)
    assert status == 404
    assert "not found" in body["error"]


def test_a_session_appears_in_the_listing(backend, avs_session):
    status, body = get_json(f"{backend}/climb/avs/sessions")
    assert status == 200
    assert avs_session["code"] in {s["code"] for s in body["sessions"]}


@pytest.mark.parametrize("verdict,holds", [("CORRECT", True), ("INDETERMINATE", True),
                                           ("WRONG", False), ("UNDECIDABLE", False)])
def test_only_a_holding_verdict_covers_the_video(backend, dres, connected, avs_session,
                                                 verdict, holds):
    """
    CORRECT and INDETERMINATE take the video off the table; WRONG and UNDECIDABLE leave it, so its
    other shots can still be tried. Every verdict is recorded either way -- the scene is marked in
    Browse regardless, it just is not hidden.
    """
    post_json(f"{dres}/mock/verdict", {"verdict": verdict, "httpStatus": 200})
    status, _ = submit_kis(backend, video_id="00043", scene_id=4242,
                           start_frame=100, end_frame=400, avs_session=avs_session["code"])
    assert status == 200

    _, session = get_json(f"{backend}/climb/avs/session/{avs_session['code']}")
    recorded = {str(s["scene_id"]): s for s in session["scenes"]}
    assert "4242" in recorded, "the submission was not recorded into the session at all"
    assert recorded["4242"]["status"] == verdict
    assert ("00043" in session["coveredVideos"]) is holds


def test_a_submission_without_a_session_code_records_nothing(backend, dres, connected,
                                                             avs_session):
    post_json(f"{dres}/mock/verdict", {"verdict": "CORRECT", "httpStatus": 200})
    assert submit_kis(backend, scene_id=777, start_frame=1, end_frame=2)[0] == 200

    _, session = get_json(f"{backend}/climb/avs/session/{avs_session['code']}")
    assert session["scenes"] == []


def test_a_held_scene_disappears_from_search_for_the_whole_session(backend, dres, connected,
                                                                   corpus, avs_session):
    """The cross-user rule: filtering is server-side, so every client polling sees it vanish."""
    query = "a+man+talking"
    _, before = get_json(f"{backend}/climb/search?q={query}&per_page=5")
    if not before["results"]:
        pytest.skip("no results to hide")
    victim = before["results"][0]

    post_json(f"{dres}/mock/verdict", {"verdict": "CORRECT", "httpStatus": 200})
    status, _ = submit_kis(backend, video_id=victim["video_id"], scene_id=victim["scene_id"],
                           start_frame=victim["start_frame"], end_frame=victim["end_frame"],
                           avs_session=avs_session["code"])
    assert status == 200

    _, after = get_json(
        f"{backend}/climb/search?q={query}&per_page=5&avs_session={avs_session['code']}")
    assert victim["scene_id"] not in {r["scene_id"] for r in after["results"]}
    assert after["total"] == before["total"] - 1

    # ...and only inside that session. Another client with no session still sees it.
    _, unfiltered = get_json(f"{backend}/climb/search?q={query}&per_page=5")
    assert victim["scene_id"] in {r["scene_id"] for r in unfiltered["results"]}


def test_a_wrong_verdict_leaves_the_scene_searchable(backend, dres, connected, corpus,
                                                     avs_session):
    query = "a+man+talking"
    _, before = get_json(f"{backend}/climb/search?q={query}&per_page=5")
    if not before["results"]:
        pytest.skip("no results to hide")
    victim = before["results"][0]

    post_json(f"{dres}/mock/verdict", {"verdict": "WRONG", "httpStatus": 200})
    assert submit_kis(backend, video_id=victim["video_id"], scene_id=victim["scene_id"],
                      start_frame=victim["start_frame"], end_frame=victim["end_frame"],
                      avs_session=avs_session["code"])[0] == 200

    _, after = get_json(
        f"{backend}/climb/search?q={query}&per_page=5&avs_session={avs_session['code']}")
    assert victim["scene_id"] in {r["scene_id"] for r in after["results"]}


def test_activity_pushes_the_expiry_out(backend, avs_session):
    """The idle timer is per-session, not per-user: one teammate polling keeps it alive for all."""
    time.sleep(1.2)
    _, first = get_json(f"{backend}/climb/avs/session/{avs_session['code']}")
    _, second = get_json(f"{backend}/climb/avs/session/{avs_session['code']}")
    assert second["expiresInMs"] >= first["expiresInMs"]
    assert second["expiresInMs"] > 5 * 60 * 1000 - 2000


def test_the_sweeper_really_deletes_an_idle_session():
    """
    Spawns its own backend with a two-second idle window, because asserting the real five-minute
    one would mean a five-minute test. `AVS_IDLE_TIMEOUT_MS` exists for exactly this.
    """
    with spawn_backend(8011, AVS_IDLE_TIMEOUT_MS=2000, AVS_SWEEP_INTERVAL_MS=300) as base:
        status, session = post_json(f"{base}/climb/avs/session", {"name": "sweeper"}, timeout=5)
        assert status == 201
        assert session["expiresInMs"] <= 2000

        time.sleep(3.5)
        status, body = get_json(f"{base}/climb/avs/session/{session['code']}", timeout=5)
        assert status == 404, f"session survived the idle window: {body}"
