import math
import subprocess
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image
from psycopg2.extras import execute_values

import custom_logger
from config import Config
from pipeline import paths
from pipeline.decode import candidate_stride

SELECT_SCENES = """
    SELECT scene_id, shot_index, start_frame, end_frame, start_ms, end_ms
    FROM scenes WHERE video_id = %s ORDER BY shot_index;
"""

SELECT_SELECTABLE = """
    SELECT v.video_id, v.fps
    FROM videos v
    WHERE (%s IS NULL OR v.collection = %s)
    ORDER BY v.video_id
    LIMIT %s;
"""

INSERT_KEYFRAMES = """
    INSERT INTO keyframes (scene_id, video_id, kf_index, frame_number, ts_ms)
    VALUES %s ON CONFLICT DO NOTHING
"""

COUNT_KEYFRAMES = "SELECT count(*) FROM keyframes WHERE video_id = %s;"
DELETE_KEYFRAMES = "DELETE FROM keyframes WHERE video_id = %s;"


@dataclass
class SelectionResult:
    video_id: str
    ok: bool
    scenes: int = 0
    keyframes: int = 0
    short: int = 0         # scenes too short to hold a candidate; middle frame used
    all_degenerate: int = 0
    error: str | None = None


def keyframe_count(scene_ms: int) -> int:
    conf = Config()
    k = math.ceil(max(scene_ms, 0) / 1000 / conf.KEYFRAME_SECONDS_PER)
    return max(conf.KEYFRAME_MIN_K, min(conf.KEYFRAME_MAX_K, k))


def scene_candidate_range(start_frame: int, end_frame: int, stride: int, max_candidate: int):
    """Inclusive 1-based candidate numbers whose source frame falls inside the scene."""
    lo = max(1, math.ceil(start_frame / stride) + 1)
    hi = min(max_candidate, math.floor(end_frame / stride) + 1)
    return lo, hi


def descriptor(bgr) -> np.ndarray:
    """
    Cheap visual fingerprint: colour distribution + coarse structure + edge density.
    Deliberately not a model embedding. ($$$)
    """
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1, 2], None, [8, 4, 2], [0, 180, 0, 256, 0, 256]).flatten()
    hist = np.sqrt(hist / (hist.sum() + 1e-9))  # Hellinger, so distances behave like L2

    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    dct = cv2.dct(cv2.resize(gray, (32, 32)).astype(np.float32))[:8, :8].flatten()[1:]
    phash = (dct > np.median(dct)).astype(np.float32)

    edges = float(cv2.Canny(gray, 80, 160).mean()) / 255.0
    return np.concatenate([hist, 0.25 * phash, [edges]]).astype(np.float32)


def is_degenerate(gray) -> bool:
    """
    True for fades and blank frames, but not for title cards.

    Luma and stddev alone are not enough: white text on black scores exactly like a fade, and
    V3C is full of credits, chyrons and title cards -- the frames OCR and text search want most.
    Measured over 35k keyframes, the median frame failing the luma/stddev test has *zero* Canny
    edges, while 42% have real structure; normal keyframes sit at p10 = 0.0173, an order of
    magnitude above the threshold used here.
    """
    conf = Config()
    mean, stddev = gray.mean(), gray.std()
    if not (mean < conf.DEGENERATE_LUMA_MIN or mean > conf.DEGENERATE_LUMA_MAX
            or stddev < conf.DEGENERATE_STDDEV_MIN):
        return False
    return float(cv2.Canny(gray, 80, 160).mean()) / 255.0 <= conf.DEGENERATE_EDGE_MAX


def select_diverse(vectors: np.ndarray, k: int) -> list:
    """
    Greedy farthest-point selection, seeded with the most representative frame.

    Returned in temporal order so kf_index matches the filmstrip.
    """
    n = len(vectors)
    if n <= k:
        return list(range(n))

    centroid = vectors.mean(axis=0)
    first = int(np.argmin(np.linalg.norm(vectors - centroid, axis=1)))
    chosen = [first]
    dist = np.linalg.norm(vectors - vectors[first], axis=1)

    while len(chosen) < k:
        nxt = int(np.argmax(dist))
        if nxt in chosen:
            break
        chosen.append(nxt)
        dist = np.minimum(dist, np.linalg.norm(vectors - vectors[nxt], axis=1))

    return sorted(chosen)


def extract_frame(video_path, frame_number: int, fps: float):
    """Pulls a single frame out of the 360p web video. Returns a BGR array, or None."""
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin",
         "-ss", f"{frame_number / fps:.4f}", "-i", str(video_path),
         "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"],
        capture_output=True,
    )
    if result.returncode != 0 or not result.stdout:
        return None
    return cv2.imdecode(np.frombuffer(result.stdout, np.uint8), cv2.IMREAD_COLOR)


def _resize_to_height(bgr, height: int):
    h, w = bgr.shape[:2]
    if h <= height:
        return bgr
    return cv2.resize(bgr, (max(1, round(w * height / h)), height), interpolation=cv2.INTER_AREA)


def _save_webp(bgr, path, height):
    conf = Config()
    rgb = cv2.cvtColor(_resize_to_height(bgr, height), cv2.COLOR_BGR2RGB)
    Image.fromarray(rgb).save(str(path), "WEBP", quality=conf.WEBP_QUALITY, method=conf.WEBP_METHOD)


def _write_outputs(bgr, video_id, shot_index, kf_index):
    conf = Config()
    _save_webp(bgr, paths.ensure_parent(paths.keyframe_path(video_id, shot_index, kf_index)),
               conf.KEYFRAME_HEIGHT)
    _save_webp(bgr, paths.ensure_parent(paths.thumbnail_path(video_id, shot_index, kf_index)),
               conf.THUMBNAIL_HEIGHT)


def select_for_video(video_id, fps, scenes) -> tuple:
    """
    Selects keyframes for one video's scenes. Returns (SelectionResult, rows_to_insert).

    Runs in a worker process, so it touches no database -- rows come back to the parent.

    Descriptors are computed one image at a time and the pixels thrown away; only the chosen
    frames are read a second time to be written out. Holding every candidate would mean 4,657
    decoded images for the 39-minute shot in video 00191 -- 1.8 GB here, and roughly double that
    at real V3C resolution.
    """
    stride = candidate_stride(fps)
    available = {int(p.stem): p for p in paths.candidate_frames(video_id)}
    if not available:
        return SelectionResult(video_id, False, error="no candidate frames on disk"), []

    max_candidate = max(available)
    web_video = paths.web_video_path(video_id)
    rows = []
    short = all_degenerate = 0

    for scene_id, shot_index, start_frame, end_frame, start_ms, end_ms in scenes:
        lo, hi = scene_candidate_range(start_frame, end_frame, stride, max_candidate)
        numbers = [n for n in range(lo, hi + 1) if n in available]

        if not numbers:
            # Shots under ~0.5s fall between candidates -- 5% of the 200-video set, all under
            # 0.42s. Pull their middle frame straight out of the web video: one keyframe, no
            # selection to do, and unlike borrowing a neighbouring candidate it is genuinely
            # inside the scene.
            mid_frame = (start_frame + end_frame) // 2
            bgr = extract_frame(web_video, mid_frame, fps)
            if bgr is None:
                continue
            short += 1
            _write_outputs(bgr, video_id, shot_index, 0)
            rows.append((scene_id, video_id, 0, mid_frame, int(round(mid_frame / fps * 1000))))
            continue

        usable, fallback, vectors = [], None, []
        for n in numbers:
            bgr = cv2.imread(str(available[n]), cv2.IMREAD_COLOR)
            if bgr is None:
                continue
            gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
            if is_degenerate(gray):
                stddev = float(gray.std())
                if fallback is None or stddev > fallback[1]:
                    fallback = (n, stddev)
                continue
            usable.append(n)
            vectors.append(descriptor(bgr))

        if not usable:
            # An entirely black or blank shot still gets one keyframe: the most textured frame,
            # so the scene stays browsable and its row is never keyframe-less.
            if fallback is None:
                continue
            all_degenerate += 1
            bgr = cv2.imread(str(available[fallback[0]]), cv2.IMREAD_COLOR)
            usable, vectors = [fallback[0]], [descriptor(bgr)]

        k = keyframe_count(end_ms - start_ms)
        picked = select_diverse(np.array(vectors), k)

        kf_index = 0
        for index in picked:
            n = usable[index]
            bgr = cv2.imread(str(available[n]), cv2.IMREAD_COLOR)
            if bgr is None:
                continue
            # Counter rather than enumerate(picked): a failed re-read must not leave a hole in
            # kf_index, since the on-disk path is derived from it.
            _write_outputs(bgr, video_id, shot_index, kf_index)
            frame_number = (n - 1) * stride
            rows.append((scene_id, video_id, kf_index, frame_number,
                         int(round(frame_number / fps * 1000))))
            kf_index += 1

    return (SelectionResult(video_id, True, len(scenes), len(rows), short, all_degenerate),
            rows)


def _selection_worker(args):
    video_id, fps, scenes = args
    try:
        return select_for_video(video_id, fps, scenes)
    except Exception as e:
        return SelectionResult(video_id, False, error=f"{type(e).__name__}: {e}"), []


def select_from_database(conn, collection=None, limit=None, force=False, workers=None):
    conf = Config()
    logger = custom_logger.get_logger("keyframes")
    workers = workers or conf.SELECTION_WORKERS

    with conn.cursor() as cur:
        cur.execute(SELECT_SELECTABLE, (collection, collection, limit))
        videos = cur.fetchall()

    jobs = []
    for video_id, fps in videos:
        with conn.cursor() as cur:
            if not force:
                cur.execute(COUNT_KEYFRAMES, (video_id,))
                if cur.fetchone()[0] > 0:
                    continue
            else:
                cur.execute(DELETE_KEYFRAMES, (video_id,))
            cur.execute(SELECT_SCENES, (video_id,))
            scenes = cur.fetchall()
        if scenes:
            jobs.append((video_id, fps, scenes))
    conn.commit()

    if not jobs:
        logger.info("No videos need keyframe selection.")
        return []

    logger.info(f"Selecting keyframes for {len(jobs)} video(s) with {workers} worker(s)")
    results = []

    with ProcessPoolExecutor(max_workers=workers,
                             initializer=custom_logger.setup_worker_logging) as executor:
        futures = {executor.submit(_selection_worker, job): job[0] for job in jobs}
        for future in as_completed(futures):
            result, rows = future.result()
            results.append(result)
            if not result.ok:
                logger.error(f"{result.video_id}: {result.error}")
                continue
            if rows:
                with conn.cursor() as cur:
                    execute_values(cur, INSERT_KEYFRAMES, rows, page_size=1000)
                conn.commit()

    ok = [r for r in results if r.ok]
    logger.info(
        f"Selected {sum(r.keyframes for r in ok)} keyframe(s) across {sum(r.scenes for r in ok)} "
        f"scene(s) in {len(ok)}/{len(results)} video(s); "
        f"{sum(r.short for r in ok)} short scene(s) used their middle frame, "
        f"{sum(r.all_degenerate for r in ok)} entirely blank"
    )
    return results
