import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

WORKER_URL = os.getenv("CLIMB_TEST_WORKER_URL", "http://localhost:5000")
BACKEND_URL = os.getenv("CLIMB_TEST_BACKEND_URL", "http://localhost:8000")
DRES_URL = os.getenv("CLIMB_TEST_DRES_URL", "http://localhost:8080")

HTTP_TIMEOUT = float(os.getenv("CLIMB_TEST_HTTP_TIMEOUT", "180"))


def request_json(method, url, payload=None, timeout=HTTP_TIMEOUT):
    """
    (status, parsed_body) for a JSON request.

    An error status is a return value rather than an exception: half of what these tests assert
    is that a bad request is rejected with the right code, and `pytest.raises(HTTPError)` around
    every one of those reads far worse than `assert status == 400`.
    """
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            return response.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as error:
        body = error.read()
        try:
            return error.code, json.loads(body)
        except ValueError:
            return error.code, {"raw": body.decode("utf-8", errors="ignore")}


def get_json(url, timeout=HTTP_TIMEOUT):
    return request_json("GET", url, timeout=timeout)


def post_json(url, payload, timeout=HTTP_TIMEOUT):
    return request_json("POST", url, payload, timeout=timeout)


def fetch_bytes(url, timeout=HTTP_TIMEOUT):
    """(status, content_type, body) -- for the static mounts, where the body is not JSON."""
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=timeout) as response:
            return response.status, response.headers.get("Content-Type", ""), response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers.get("Content-Type", ""), error.read()


def _service_up(url):
    try:
        status, _ = get_json(url, timeout=5)
        return status < 500
    except Exception:
        return False


@pytest.fixture(scope="session")
def worker():
    """Base URL of the search worker, or a skip if it is not running."""
    if not _service_up(f"{WORKER_URL}/api/health"):
        pytest.skip(f"search worker not reachable at {WORKER_URL}")
    return WORKER_URL


@pytest.fixture(scope="session")
def backend():
    """Base URL of the node backend, or a skip if it is not running."""
    if not _service_up(f"{BACKEND_URL}/climb/health"):
        pytest.skip(f"backend not reachable at {BACKEND_URL}")
    return BACKEND_URL


@pytest.fixture(scope="session")
def dres():
    """Base URL of the mock DRES server, or a skip if it is not running."""
    if not _service_up(f"{DRES_URL}/mock/submissions"):
        pytest.skip(f"mock DRES server not reachable at {DRES_URL}")
    return DRES_URL


@pytest.fixture(scope="session")
def db():
    """A live database connection, or a skip. Session-scoped: connecting is not free."""
    from db.connection import connect_to_database
    conn = connect_to_database()
    if conn is None:
        pytest.skip("database not reachable")
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def corpus(db):
    """
    Counts of what is actually indexed, so a test can skip rather than fail when a stage has
    not run yet. An assertion that OCR contributes is a real failure only once OCR has rows.
    """
    with db.cursor() as cur:
        cur.execute("""
            SELECT (SELECT count(*) FROM videos),
                   (SELECT count(*) FROM scenes),
                   (SELECT count(*) FROM keyframes),
                   (SELECT count(*) FROM keyframe_embedding),
                   (SELECT count(*) FROM keyframe_text),
                   (SELECT count(*) FROM keyframe_caption),
                   (SELECT count(*) FROM transcript_segment),
                   (SELECT count(*) FROM caption_embedding),
                   (SELECT count(*) FROM transcript_embedding);
        """)
        row = cur.fetchone()
    keys = ["videos", "scenes", "keyframes", "embeddings", "ocr", "captions",
            "transcripts", "caption_embeddings", "transcript_embeddings"]
    counts = dict(zip(keys, row))
    if counts["embeddings"] == 0:
        pytest.skip("no embeddings in the database -- run the embed stage first")
    return counts
