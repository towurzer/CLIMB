"""
Teams on the shared session service: joining or creating one, the team's AVS session that every
member follows, hints between members, and the same degradation contract as plain sessions (an
unreachable service keeps the last state, a team that is gone is reported as gone).

Spawns its own service and backends, like test_integration_avs_service.py.
"""

import time

import pytest

from conftest import free_port, get_json, post_json, spawn_avs_service, spawn_backend

pytestmark = pytest.mark.integration

TOKEN = "pytest-shared-secret"


def auth(token=TOKEN):
    return {"Authorization": f"Bearer {token}"}


def _backend(service_url, user):
    return spawn_backend(free_port(), AVS_SESSION_SERVICE_URL=service_url, AVS_SESSION_TOKEN=TOKEN,
                         AVS_MIRROR_TTL_MS=0, CLIMB_USER=f"env-{user}")


@pytest.fixture
def service():
    with spawn_avs_service(free_port(), TOKEN) as (base, handle):
        yield base, handle


@pytest.fixture
def two(service):
    """Two teammates' backends on one service: (anna, ben, service_url, handle)."""
    service_url, handle = service
    with _backend(service_url, "anna") as anna, _backend(service_url, "ben") as ben:
        yield anna, ben, service_url, handle


def connect(backend, user, team="IVADL", password="pw"):
    return post_json(f"{backend}/climb/team/connect", {"team": team, "password": password, "user": user})


def team_of(backend):
    status, body = get_json(f"{backend}/climb/team")
    assert status == 200, body
    return body


def test_an_unknown_team_is_created_and_a_known_one_joined(two):
    anna, ben, _, _ = two
    status, created = connect(anna, "Anna")
    assert status == 201, created
    assert (created["name"], created["user"], created["connected"]) == ("IVADL", "Anna", True)

    # Same team whatever the spelling; the first spelling is the name
    status, joined = connect(ben, "Ben", team="ivadl")
    assert status == 200, joined
    assert joined["name"] == "IVADL"

    _, listing = get_json(f"{ben}/climb/team/list")
    assert [t["name"] for t in listing["teams"]] == ["IVADL"]
    assert listing["teams"][0]["expiresInMs"] > 24 * 3600 * 1000


def test_a_wrong_password_or_a_missing_username_is_refused(two):
    anna, ben, _, _ = two
    connect(anna, "Anna")
    status, body = connect(ben, "Ben", password="nope")
    assert status == 401 and "password" in body["error"].lower()
    status, body = connect(ben, "  ")
    assert status == 400
    assert team_of(ben)["connected"] is False


def test_members_share_the_session_and_follow_a_new_one(two):
    anna, ben, _, _ = two
    code = connect(anna, "Anna")[1]["session"]["code"]
    assert connect(ben, "Ben")[1]["session"]["code"] == code

    status, body = post_json(f"{ben}/climb/team/session", {})
    assert status == 200
    fresh = body["session"]["code"]
    assert fresh != code
    assert team_of(anna)["session"]["code"] == fresh


def test_a_teammates_submission_reaches_the_mirror_search_filters_against(two):
    anna, ben, service_url, _ = two
    code = connect(anna, "Anna")[1]["session"]["code"]
    connect(ben, "Ben")

    # Ben's backend records a delivered submission into the team's session
    post_json(f"{service_url}/avs/session/{code}/scene",
              {"scene_id": 77, "video_id": "00007", "status": "CORRECT", "user": "Ben"}, headers=auth())

    session = team_of(anna)["session"]
    assert [(s["scene_id"], s["user"]) for s in session["scenes"]] == [(77, "Ben")]
    assert session["coveredVideos"] == ["00007"]
    # The search filter reads the mirror by code; the team poll is what keeps it current
    status, mirrored = get_json(f"{anna}/climb/avs/session/{code}")
    assert status == 200 and [s["scene_id"] for s in mirrored["scenes"]] == [77]


def test_a_hint_reaches_the_team_with_its_sender(two):
    anna, ben, _, _ = two
    connect(anna, "Anna")
    connect(ben, "Ben")

    status, _ = post_json(f"{anna}/climb/team/hint", {
        "video_id": "Ambon_Apr2012_0003", "recording_id": "Ambon_Apr2012", "keyframe_id": 12,
        "scene_id": 3, "keyframe_time_ms": 4000, "note": "the turtle"})
    assert status == 200
    post_json(f"{anna}/climb/team/hint", {"video_id": "00191"})

    newest, older = team_of(ben)["hints"]
    assert (newest["user"], newest["video_id"], newest["keyframe_id"], newest["note"]) == \
           ("Anna", "00191", None, None)
    assert newest["recording_id"] == "00191"
    assert (older["recording_id"], older["keyframe_id"], older["keyframe_time_ms"], older["note"]) == \
           ("Ambon_Apr2012", 12, 4000, "the turtle")


def test_a_hint_needs_a_video(two):
    anna, _, _, _ = two
    connect(anna, "Anna")
    assert post_json(f"{anna}/climb/team/hint", {"note": "somewhere"})[0] == 400


def test_the_team_routes_need_the_service_token(service):
    service_url, _ = service
    assert get_json(f"{service_url}/teams")[0] == 401
    assert post_json(f"{service_url}/teams/connect", {"name": "x", "password": "y"})[0] == 401


def test_an_unreachable_service_keeps_the_team(two):
    anna, ben, _, handle = two
    code = connect(anna, "Anna")[1]["session"]["code"]
    post_json(f"{anna}/climb/team/hint", {"video_id": "00191"})
    handle.stop()

    team = team_of(anna)
    assert (team["connected"], team["session"]["code"], len(team["hints"]), team["collab"]) == \
           (True, code, 1, "offline")
    # Joining or listing cannot work without the service, and says so
    assert connect(ben, "Ben")[0] == 503
    assert get_json(f"{ben}/climb/team/list")[0] == 503


def test_a_restarted_service_reports_the_team_as_gone(two):
    anna, _, _, handle = two
    connect(anna, "Anna")
    handle.stop()
    handle.start()

    assert team_of(anna) == {"connected": False, "lost": True}
    assert team_of(anna)["connected"] is False


def test_an_idle_team_is_swept():
    with spawn_avs_service(free_port(), TOKEN,
                           AVS_TEAM_IDLE_TIMEOUT_MS=300, AVS_SWEEP_INTERVAL_MS=100) as (service_url, _):
        status, _ = post_json(f"{service_url}/teams/connect", {"name": "doomed", "password": "pw"},
                              headers=auth())
        assert status == 201
        # Watched through the listing, which does not count as activity
        deadline = time.time() + 5
        while time.time() < deadline:
            _, listing = get_json(f"{service_url}/teams", headers=auth())
            if not listing["teams"]:
                return
            time.sleep(0.1)
        pytest.fail("an idle team was never swept")
