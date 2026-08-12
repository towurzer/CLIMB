"""
AVS collaboration through the shared session service (`climb-avs-service`).

`test_integration_dres.py` covers AVS against whatever backend the developer has running, which is
normally the in-process store. These tests cover the other mode: a real session service on its own
port, with backends pointed at it. That is the configuration used at a competition, where every
teammate runs their own local stack and only session bookkeeping is shared.

What is actually being asserted here is the degradation contract, because that is the part that is
easy to get wrong and expensive to discover late:

  * search never depends on the service (it filters a local mirror, synchronously)
  * submitting never depends on it either
  * 404 ("the session is gone") and 503 ("we cannot reach the service") stay distinct
  * a verdict already known is never downgraded by a straggling write from another machine
  * a restart of the service starts empty (state is in memory, on purpose)

Everything here spawns its own processes, so it needs no running backend -- only node and an
installed `climb-avs-service/node_modules`. The DRES-facing tests additionally need the mock:

    node backend/mock-dres-server.js &
    pytest -m integration tests/test_integration_avs_service.py
"""

import http.server
import json
import re
import threading
import time

import pytest

from conftest import (
    free_port,
    get_json,
    post_json,
    spawn_avs_service,
    spawn_backend,
)

pytestmark = pytest.mark.integration

TOKEN = "pytest-shared-secret"
CODE_PATTERN = re.compile(r"^[BCDFGHJKLMNPQRSTVWXYZ]{4}$")

# The backend only asks the service again once its mirror is older than this. Zero here so every
# read refreshes: with a real TTL these tests would race it, and "did the mirror happen to still be
# fresh?" is timing rather than behaviour. Production keeps a TTL so a chatty frontend cannot
# multiply traffic to the service.
MIRROR_TTL_MS = 0


def auth(token=TOKEN):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def ready_embedding_service():
    """
    A stand-in for the Python worker that just says "ready".

    Needed because the interesting health tier is the one where *only* collaboration is broken. With
    no worker running the backend correctly reports 'degraded' instead, which would hide whether the
    'collab_offline' tier works at all.
    """
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"search_engine_ready": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("localhost", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://localhost:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def service():
    """A session service on its own port, plus the handle to stop and restart it."""
    with spawn_avs_service(free_port(), TOKEN) as (base, handle):
        yield base, handle


@pytest.fixture
def shared(service):
    """One backend wired to the service. Yields (backend_url, service_url, service_handle)."""
    service_url, handle = service
    with spawn_backend(free_port(), AVS_SESSION_SERVICE_URL=service_url,
                       AVS_SESSION_TOKEN=TOKEN, AVS_MIRROR_TTL_MS=MIRROR_TTL_MS,
                       CLIMB_USER="pytest-a") as backend:
        yield backend, service_url, handle


def new_session(backend, name="pytest"):
    status, body = post_json(f"{backend}/climb/avs/session", {"name": name})
    assert status == 201, body
    return body


def record_on_service(service_url, code, scene_id, **fields):
    """Write straight to the service, standing in for another teammate's backend."""
    payload = {"scene_id": scene_id, "video_id": "00007", "start_frame": 10, "end_frame": 90,
               "status": "CORRECT", **fields}
    return post_json(f"{service_url}/avs/session/{code}/scene", payload, headers=auth())


def wait_for_mirror(backend, code, predicate, timeout=5.0):
    """
    Poll the backend until its mirror satisfies `predicate`, or give up.

    Polling rather than sleeping a fixed amount: the mirror refreshes on a TTL, so the exact number
    of round trips is timing, not behaviour, and asserting on it would make these tests flaky.
    """
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        status, body = get_json(f"{backend}/climb/avs/session/{code}")
        last = (status, body)
        if status == 200 and predicate(body):
            return body
        time.sleep(0.1)
    pytest.fail(f"mirror never satisfied the predicate; last response was {last}")


# --- the service's own contract ------------------------------------------------------------------

def test_the_service_refuses_every_call_without_the_token(service):
    service_url, _ = service
    assert get_json(f"{service_url}/avs/sessions")[0] == 401
    assert get_json(f"{service_url}/avs/sessions", headers=auth("wrong"))[0] == 401
    # A token of a different length must not crash the constant-time compare.
    assert get_json(f"{service_url}/avs/sessions", headers=auth("x"))[0] == 401
    assert get_json(f"{service_url}/avs/sessions", headers=auth())[0] == 200


def test_health_needs_no_token_so_the_backend_can_cheaply_probe_it(service):
    service_url, _ = service
    status, body = get_json(f"{service_url}/health")
    assert status == 200
    assert body["status"] == "ok"


def test_a_terminal_verdict_is_not_downgraded_by_a_later_indeterminate(shared):
    """
    Several backends write independently, so writes arrive out of order. A straggling "not judged
    yet" must not erase a verdict another machine already got back from DRES -- that would un-hide a
    scene the team has finished with.
    """
    backend, service_url, _ = shared
    code = new_session(backend)["code"]

    record_on_service(service_url, code, 5150, status="CORRECT")
    status, body = record_on_service(service_url, code, 5150, status="INDETERMINATE")

    assert status == 200
    assert [s["status"] for s in body["scenes"]] == ["CORRECT"]


def test_a_status_only_write_keeps_the_frame_range(shared):
    """The frame range is what identifies the scene, so a partial write must not blank it."""
    backend, service_url, _ = shared
    code = new_session(backend)["code"]

    record_on_service(service_url, code, 61, start_frame=10, end_frame=90, status="INDETERMINATE")
    _, body = post_json(f"{service_url}/avs/session/{code}/scene",
                        {"scene_id": 61, "status": "CORRECT"}, headers=auth())

    scene = body["scenes"][0]
    assert (scene["start_frame"], scene["end_frame"]) == (10, 90)
    assert scene["video_id"] == "00007"


def test_recording_into_an_unknown_session_is_a_404(service):
    service_url, _ = service
    assert record_on_service(service_url, "ZZZZ", 1)[0] == 404


def test_a_scene_needs_an_id(service):
    service_url, _ = service
    status, body = post_json(f"{service_url}/avs/session/ZZZZ/scene", {"video_id": "00007"},
                             headers=auth())
    assert status == 400
    assert "scene_id" in body["error"]


# --- the backend in shared mode ------------------------------------------------------------------

def test_the_backend_reports_the_service_in_its_health(shared):
    backend, _, _ = shared
    status, body = get_json(f"{backend}/climb/health")
    assert status == 200
    assert body["avs_collab"] == {"configured": True, "reachable": True,
                                  "detail": "AVS session service reachable"}


def test_a_session_created_through_one_backend_is_visible_to_another(service):
    """The whole point: two teammates on separate local stacks, one exclusion list."""
    service_url, _ = service
    with spawn_backend(free_port(), AVS_SESSION_SERVICE_URL=service_url, AVS_SESSION_TOKEN=TOKEN,
                       AVS_MIRROR_TTL_MS=MIRROR_TTL_MS, CLIMB_USER="pytest-a") as backend_a, \
         spawn_backend(free_port(), AVS_SESSION_SERVICE_URL=service_url, AVS_SESSION_TOKEN=TOKEN,
                       AVS_MIRROR_TTL_MS=MIRROR_TTL_MS, CLIMB_USER="pytest-b") as backend_b:
        created = new_session(backend_a, name="cross-machine")
        code = created["code"]
        assert CODE_PATTERN.match(code)

        status, joined = post_json(f"{backend_b}/climb/avs/session/{code}/join", None)
        assert status == 200, joined
        assert joined["name"] == "cross-machine"
        assert joined["collab"] == "online"


def test_a_scene_recorded_by_one_backend_reaches_the_other(service):
    service_url, _ = service
    with spawn_backend(free_port(), AVS_SESSION_SERVICE_URL=service_url, AVS_SESSION_TOKEN=TOKEN,
                       AVS_MIRROR_TTL_MS=MIRROR_TTL_MS, CLIMB_USER="pytest-a") as backend_a, \
         spawn_backend(free_port(), AVS_SESSION_SERVICE_URL=service_url, AVS_SESSION_TOKEN=TOKEN,
                       AVS_MIRROR_TTL_MS=MIRROR_TTL_MS, CLIMB_USER="pytest-b") as backend_b:
        code = new_session(backend_a)["code"]
        assert post_json(f"{backend_b}/climb/avs/session/{code}/join", None)[0] == 200

        record_on_service(service_url, code, 4242, video_id="00043", status="CORRECT")

        for backend in (backend_a, backend_b):
            body = wait_for_mirror(backend, code, lambda b: len(b["scenes"]) == 1)
            assert body["coveredVideos"] == ["00043"]
            assert body["counts"] == {"instances": 1, "distinctVideos": 1}


def test_the_submitting_user_is_recorded(shared):
    """CLIMB_USER, so 'who took this one?' has an answer once several people share a session."""
    backend, service_url, _ = shared
    code = new_session(backend)["code"]
    record_on_service(service_url, code, 12, user="teammate")

    body = wait_for_mirror(backend, code, lambda b: len(b["scenes"]) == 1)
    assert body["scenes"][0]["user"] == "teammate"


# --- degradation: the part that must not surprise anyone at a competition ------------------------

def test_a_known_session_keeps_working_when_the_service_dies(shared):
    """
    The exclusion list is not thrown away because the network hiccuped. The last known snapshot is
    still the best information available, and the team keeps working from it.
    """
    backend, service_url, handle = shared
    code = new_session(backend)["code"]
    record_on_service(service_url, code, 99, video_id="00043", status="CORRECT")
    wait_for_mirror(backend, code, lambda b: len(b["scenes"]) == 1)

    handle.stop()

    status, body = get_json(f"{backend}/climb/avs/session/{code}")
    assert status == 200, body
    assert len(body["scenes"]) == 1, "the mirror was discarded when the service went away"
    assert body["coveredVideos"] == ["00043"]
    assert body["collab"] == "offline"


def test_an_unknown_session_is_503_not_404_while_the_service_is_down(shared):
    """
    The distinction the whole design turns on. 404 tells a client its session expired and makes it
    drop everything; 503 says we could not ask. Collapsing them loses good state over bad wifi.
    """
    backend, _, handle = shared
    handle.stop()

    status, body = get_json(f"{backend}/climb/avs/session/QQQQ")
    assert status == 503, body
    assert body["collab"] == "offline"
    # The operator needs to know what still works, or they stop trusting the tool.
    assert "unaffected" in body["hint"]


def test_a_genuinely_unknown_code_is_still_404_while_the_service_is_up(shared):
    backend, _, _ = shared
    status, body = get_json(f"{backend}/climb/avs/session/QQQQ")
    assert status == 404, body
    assert "not found" in body["error"]


def test_listing_says_503_rather_than_pretending_nobody_has_a_session(shared):
    backend, _, handle = shared
    assert get_json(f"{backend}/climb/avs/sessions")[0] == 200
    handle.stop()

    status, body = get_json(f"{backend}/climb/avs/sessions")
    assert status == 503
    assert body["sessions"] == []


def test_creating_a_session_fails_loudly_when_the_service_is_down(shared):
    """The one AVS route with nothing to fall back on: a new session needs the shared service."""
    backend, _, handle = shared
    handle.stop()
    status, _ = post_json(f"{backend}/climb/avs/session", {"name": "doomed"})
    assert status == 503


def test_search_still_answers_while_the_service_is_down(shared):
    """
    Search must never wait on, or fail because of, the session service: it filters a local mirror
    synchronously. Asserted through the real search route with a session code attached.
    """
    backend, service_url, handle = shared
    code = new_session(backend)["code"]
    handle.stop()

    status, body = get_json(f"{backend}/climb/search?q=a+man+talking&per_page=5"
                            f"&avs_session={code}")
    if status == 500:
        pytest.skip("search needs the worker and database, which are not part of this test")
    assert status == 200, body
    assert "results" in body


def test_the_backend_recovers_by_itself_when_the_service_comes_back(shared):
    """
    Nobody has to restart a backend to get collaboration back.

    The session itself does not come back -- the service holds it in memory -- so recovery is
    asserted on a session created after the restart, and on the old code turning into a definite 404
    instead of the stale-serving 503 it gave while the service was away.
    """
    backend, _, handle = shared
    gone = new_session(backend)["code"]
    handle.stop()
    assert get_json(f"{backend}/climb/avs/session/{gone}")[1]["collab"] == "offline"

    handle.start()

    deadline = time.time() + 10
    while time.time() < deadline:
        status, body = get_json(f"{backend}/climb/avs/session/{gone}")
        if status == 404 and body["collab"] == "online":
            break
        time.sleep(0.2)
    else:
        pytest.fail(f"the backend never reached the service again; last was {status} {body}")

    fresh = new_session(backend, name="after-restart")["code"]
    assert wait_for_mirror(backend, fresh, lambda b: b["collab"] == "online")["code"] == fresh


def _await_collab_reachable(backend, expected, timeout=10):
    """The probe is cached for a few seconds, so this needs a moment rather than an instant assert."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, body = get_json(f"{backend}/climb/health")
        if body["avs_collab"]["reachable"] is expected:
            return body
        time.sleep(0.5)
    pytest.fail(f"health never reported avs_collab.reachable == {expected}")


def test_the_health_probe_flips_to_unreachable_and_back(shared):
    backend, _, handle = shared
    handle.stop()
    body = _await_collab_reachable(backend, False)

    # The tier only reads 'collab_offline' when nothing worse is wrong. These tests spawn a bare
    # backend with no embedding worker, and a dead search engine legitimately outranks dead
    # collaboration, so the tier is only asserted when the worker happens to be running.
    if body["embedding_service"]["ready"]:
        assert body["status"] == "collab_offline", \
            "an unreachable session service must not be reported as 'degraded' -- search still works"
    else:
        assert body["status"] == "degraded"

    handle.start()
    _await_collab_reachable(backend, True)


def test_only_collaboration_down_is_its_own_tier(service, ready_embedding_service):
    """
    The tier that drives the header light: everything healthy except the session service.

    It has to be distinguishable from 'degraded', because the operator loses nothing but teammate
    sync -- search, browse and submit all still work. Reporting that as 'degraded' (orange) would
    mark a working tool as broken, and a status light nobody believes is worse than none.
    """
    service_url, handle = service
    with spawn_backend(free_port(), AVS_SESSION_SERVICE_URL=service_url, AVS_SESSION_TOKEN=TOKEN,
                       AVS_MIRROR_TTL_MS=MIRROR_TTL_MS,
                       SEARCH_ENGINE_URL=ready_embedding_service) as backend:
        _, body = get_json(f"{backend}/climb/health")
        assert body["status"] == "ok", body
        assert body["embedding_service"]["ready"] is True

        handle.stop()
        body = _await_collab_reachable(backend, False)

        assert body["status"] == "collab_offline"
        assert body["embedding_service"]["ready"] is True, \
            "search is fine; only collaboration went away"
        assert "unaffected" in body["avs_collab"]["detail"]

        handle.start()
        body = _await_collab_reachable(backend, True)
        assert body["status"] == "ok"


def test_an_unconfigured_service_is_never_reported_as_offline(ready_embedding_service):
    """Solo mode is not a degradation: with no service configured there is nothing to be offline."""
    with spawn_backend(free_port(), SEARCH_ENGINE_URL=ready_embedding_service) as backend:
        _, body = get_json(f"{backend}/climb/health")
        assert body["status"] == "ok"
        assert body["avs_collab"]["configured"] is False
        assert body["avs_collab"]["reachable"] is True


# --- lifetime ------------------------------------------------------------------------------------

def test_a_restart_of_the_service_starts_empty(shared):
    """
    Documented behaviour, not an accident: state is in memory only, so a restart is a wipe. An AVS
    task is five minutes long -- start the service before it, do not restart it during it.
    """
    backend, service_url, handle = shared
    code = new_session(backend, name="ephemeral")["code"]
    record_on_service(service_url, code, 4242, video_id="00043", status="CORRECT")
    wait_for_mirror(backend, code, lambda b: len(b["scenes"]) == 1)

    handle.stop()
    handle.start()

    status, _ = get_json(f"{service_url}/avs/session/{code}", headers=auth())
    assert status == 404


def test_an_idle_session_is_swept():
    """The sweeper still works; only its window changed. Forced short rather than waiting 2 h."""
    with spawn_avs_service(free_port(), TOKEN,
                           AVS_IDLE_TIMEOUT_MS=300, AVS_SWEEP_INTERVAL_MS=100) as (service_url, _):
        status, body = post_json(f"{service_url}/avs/session", {"name": "doomed"}, headers=auth())
        assert status == 201
        code = body["code"]

        # Watched through the listing, which deliberately does not count as interaction. Polling
        # GET /avs/session/{code} instead would keep the session alive with the very requests meant
        # to observe it dying -- that endpoint *is* the heartbeat.
        deadline = time.time() + 5
        while time.time() < deadline:
            _, listing = get_json(f"{service_url}/avs/sessions", headers=auth())
            if code not in {s["code"] for s in listing["sessions"]}:
                return
            time.sleep(0.1)
        pytest.fail("an idle session was never swept")


def test_polling_a_session_keeps_it_alive():
    """The other half of the same rule: the poll is what stops a session idling out."""
    with spawn_avs_service(free_port(), TOKEN,
                           AVS_IDLE_TIMEOUT_MS=800, AVS_SWEEP_INTERVAL_MS=100) as (service_url, _):
        _, body = post_json(f"{service_url}/avs/session", {"name": "kept"}, headers=auth())
        code = body["code"]

        # Well past the idle window, but polled throughout.
        for _ in range(16):
            assert get_json(f"{service_url}/avs/session/{code}", headers=auth())[0] == 200
            time.sleep(0.1)
