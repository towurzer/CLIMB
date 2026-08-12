import contextlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

REPO_ROOT = Path(__file__).resolve().parents[2]

WORKER_URL = os.getenv("CLIMB_TEST_WORKER_URL", "http://localhost:5000")
BACKEND_URL = os.getenv("CLIMB_TEST_BACKEND_URL", "http://localhost:8000")
DRES_URL = os.getenv("CLIMB_TEST_DRES_URL", "http://localhost:8080")

HTTP_TIMEOUT = float(os.getenv("CLIMB_TEST_HTTP_TIMEOUT", "180"))


def request_json(method, url, payload=None, timeout=HTTP_TIMEOUT, headers=None):
    """
    (status, parsed_body) for a JSON request.

    An error status is a return value rather than an exception: half of what these tests assert
    is that a bad request is rejected with the right code, and `pytest.raises(HTTPError)` around
    every one of those reads far worse than `assert status == 400`.
    """
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request_headers = {"Content-Type": "application/json"} if data is not None else {}
    request_headers.update(headers or {})
    request = urllib.request.Request(url, data=data, method=method, headers=request_headers)
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
    except urllib.error.URLError as error:
        # A refused connection is a legitimate expected outcome now that tests kill services on
        # purpose to check how the backend degrades. 0 is "no HTTP answer at all".
        return 0, {"error": str(error.reason)}


def get_json(url, timeout=HTTP_TIMEOUT, headers=None):
    return request_json("GET", url, timeout=timeout, headers=headers)


def post_json(url, payload, timeout=HTTP_TIMEOUT, headers=None):
    return request_json("POST", url, payload, timeout=timeout, headers=headers)


def free_port():
    """An unused port, so parallel or repeated runs do not collide on a hardcoded one."""
    with socket.socket() as sock:
        sock.bind(("localhost", 0))
        return sock.getsockname()[1]


def _await_http(url, what, attempts=60, delay=0.2):
    for _ in range(attempts):
        with contextlib.suppress(Exception):
            if get_json(url, timeout=1)[0] == 200:
                return True
        time.sleep(delay)
    pytest.skip(f"could not start {what}")


def _terminate(process, sig=signal.SIGTERM):
    with contextlib.suppress(ProcessLookupError):
        os.killpg(os.getpgid(process.pid), sig)
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=10)


def _spawn(argv, cwd, env_extra):
    return subprocess.Popen(
        argv, cwd=cwd, env={**os.environ, **{k: str(v) for k, v in env_extra.items()}},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )


@contextlib.contextmanager
def spawn_backend(port, **env_extra):
    """
    A backend of our own on another port.

    Several things in the backend are module-level process state -- the DRES connection, the 30 s
    collection-name cache, and now the AVS session mirror -- so a test that needs them empty cannot
    get there by resetting anything. Starting a second process is the honest way to get a clean one.
    """
    server = _spawn(["node", "server.js"], REPO_ROOT / "backend", {"BACKEND_PORT": port, **env_extra})
    base = f"http://localhost:{port}"
    try:
        _await_http(f"{base}/climb/avs/sessions", f"a second backend on {port}")
        yield base
    finally:
        _terminate(server)


@contextlib.contextmanager
def spawn_avs_service(port, token, **env_extra):
    """
    The shared AVS session service, on its own port.

    Yields (base_url, handle). The handle exposes stop()/start() so a test can take the service away
    mid-flight -- degrading gracefully when it vanishes is a behaviour worth asserting, not just
    hoping for. Restarting it starts empty: the service keeps sessions in memory only.
    """
    service_dir = REPO_ROOT / "climb-avs-service"
    if not (service_dir / "node_modules").exists():
        pytest.skip("climb-avs-service dependencies are not installed (run npm install there)")

    env = {"AVS_SESSION_PORT": port, "AVS_SESSION_TOKEN": token, **env_extra}
    base = f"http://localhost:{port}"

    class Handle:
        def __init__(self):
            self.process = None

        def start(self):
            self.process = _spawn(["node", "server.js"], service_dir, env)
            _await_http(f"{base}/health", f"the AVS session service on {port}")

        def stop(self, sig=signal.SIGTERM):
            if self.process is not None:
                _terminate(self.process, sig)
                self.process = None

    handle = Handle()
    handle.start()
    try:
        yield base, handle
    finally:
        handle.stop()


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
        # 0 is "no HTTP answer at all", which request_json returns instead of raising so that tests
        # can assert on a service being down. It must still read as "not up" here, or every fixture
        # that means to skip would instead let its tests run against nothing.
        return 0 < status < 500
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
