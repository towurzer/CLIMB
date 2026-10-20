"""
`main.py check`: what a run needs, checked before any compute is spent on it.

Each finding is OK, WARN or FAIL. FAIL means a stage would crash or quietly produce wrong output;
WARN means it runs, but slower or without something. Exits non-zero on any FAIL.

Nothing here downloads a model or writes anywhere except one probe file in each output directory.
"""

import hashlib
import importlib.metadata
import importlib.util
import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from config import Config
from pipeline import datasets

OK, WARN, FAIL = "OK", "WARN", "FAIL"

# Measured on the 31-video HDD sample (2026-10-15), per hour of video: keyframes + thumbnails
# (kept), candidate frames (one batch at a time) and audio (kept in work/ until asr has run).
MEDIA_MB_PER_HOUR = 33
CANDIDATE_MB_PER_HOUR = 130
AUDIO_MB_PER_HOUR = 4
# Container default is 64 MB; a parallel HNSW build wants maintenance_work_mem of shared memory.
MIN_SHM_BYTES = 1024 ** 3
# binary_quantize() arrived in pgvector 0.7.0.
MIN_PGVECTOR = (0, 7, 0)
SAMPLE_VIDEOS_PER_COLLECTION = 3
# Scene files are allowed to end a few frames off the container's count.
FRAME_TOLERANCE = 5


@dataclass
class Report:
    findings: list = field(default_factory=list)

    def add(self, level, area, message):
        self.findings.append((level, area, message))
        print(f"  {level:4s}  {area:10s} {message}", flush=True)

    @property
    def failed(self) -> bool:
        return any(level == FAIL for level, _, _ in self.findings)


def _run(command, timeout=60):
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None


# --- tools -----------------------------------------------------------------------------------

def check_tools(report):
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            report.add(FAIL, "tools", f"{tool} is not on PATH")
            return
    version = _run(["ffmpeg", "-hide_banner", "-version"])
    first = (version.stdout.splitlines() or ["ffmpeg"])[0] if version else "ffmpeg"
    report.add(OK, "tools", first.split(" Copyright")[0])

    # decode writes JPEG candidates and Opus audio; a minimal ffmpeg build can lack either, and
    # then every single decode fails.
    encoders = _run(["ffmpeg", "-hide_banner", "-encoders"])
    listed = encoders.stdout if encoders else ""
    for encoder in ("mjpeg", "libopus"):
        if f" {encoder} " not in listed:
            report.add(FAIL, "tools", f"ffmpeg has no {encoder} encoder, which decode needs")


# --- python packages, GPU, models --------------------------------------------------------------

def check_packages(report):
    required = {"torch": "every GPU stage", "transformers": "embed, caption, embed-text",
                "torchvision": "caption (fails with 'Unrecognized image processor' without it)",
                "faster_whisper": "asr", "cv2": "select", "PIL": "select",
                "psycopg2": "everything"}
    for module, needed_by in required.items():
        if importlib.util.find_spec(module) is None:
            report.add(FAIL, "python", f"{module} is not installed, needed by {needed_by}")

    paddle = importlib.util.find_spec("paddleocr") is not None
    rapid = importlib.util.find_spec("rapidocr_onnxruntime") is not None
    pinned = (Config.OCR_BACKEND or "auto").lower()
    paddle_version = importlib.metadata.version("paddleocr") if paddle else ""
    if paddle and int(paddle_version.split(".")[0]) < 3:
        # The 2.x constructor arguments raise in 3.x and the other way round; the reader is 3.x.
        report.add(FAIL, "python", f"paddleocr {paddle_version} is installed, the OCR stage needs "
                                   "3.x: pip install 'paddleocr>=3'")
    elif paddle and importlib.util.find_spec("paddle") is None:
        report.add(FAIL if pinned == "paddle" else WARN, "python",
                   "paddleocr is installed without paddlepaddle, so OCR falls back to rapidocr: "
                   "install paddlepaddle-gpu for this CUDA version")
    elif pinned == "paddle" and not paddle or pinned == "rapidocr" and not rapid:
        report.add(FAIL, "python", f"CLIMB_OCR_BACKEND={pinned} but it is not installed")
    elif not (paddle or rapid):
        report.add(FAIL, "python", "no OCR backend: pip install rapidocr-onnxruntime")
    elif not paddle:
        report.add(WARN, "python", "OCR runs on rapidocr; paddleocr reads better if it installs "
                                   "here (needs Python <= 3.13)")
    else:
        report.add(OK, "python", "packages present, OCR backend: paddle")


def check_gpu(report):
    try:
        import torch
    except ImportError:
        return
    if torch.cuda.is_available():
        count = torch.cuda.device_count()
        names = ", ".join(torch.cuda.get_device_name(i) for i in range(count))
        memory = min(torch.cuda.get_device_properties(i).total_memory for i in range(count))
        report.add(OK, "gpu", f"{count} CUDA device(s): {names}, {memory / 1024 ** 3:.0f} GB")
        if count > 1:
            report.add(OK, "gpu", f"one process uses one GPU; run {count} with "
                                  f"CUDA_VISIBLE_DEVICES=i and --shard i --shards {count}")
    else:
        report.add(WARN, "gpu", "no CUDA device, the GPU stages run on CPU "
                                "(embedding alone is ~0.4 images/s there)")


def _cached(repo) -> bool:
    from huggingface_hub import try_to_load_from_cache
    return isinstance(try_to_load_from_cache(repo, "config.json"), str)


def _reachable(repo) -> bool:
    from huggingface_hub import HfApi
    try:
        HfApi().model_info(repo, timeout=15)
        return True
    except Exception:
        return False


def check_models(report):
    if importlib.util.find_spec("huggingface_hub") is None:
        return
    repos = {"embed": Config.KIS_MODEL_NAME, "caption": Config.CAPTION_MODEL,
             "embed-text": Config.TEXT_MODEL}
    try:
        from faster_whisper.utils import _MODELS
        repos["asr"] = _MODELS.get(Config.ASR_MODEL, Config.ASR_MODEL)
    except ImportError:
        pass

    for stage, repo in repos.items():
        if _cached(repo):
            report.add(OK, "models", f"{stage}: {repo} is cached")
        elif _reachable(repo):
            report.add(OK, "models", f"{stage}: {repo} will download on first use")
        else:
            report.add(FAIL, "models", f"{stage}: {repo} is neither cached nor reachable on the "
                                       f"Hugging Face hub")


# --- source collections ----------------------------------------------------------------------

def _frame_count(path) -> int | None:
    result = _run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets",
                   "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", str(path)],
                  timeout=120)
    try:
        return int(result.stdout.strip().split(",")[0])
    except (AttributeError, ValueError, IndexError):
        return None


def _last_scene_frame(text) -> int | None:
    for line in reversed(text.strip().splitlines()):
        fields = line.replace(",", " ").split()
        if len(fields) >= 2 and fields[1].isdigit():
            return int(fields[1])
    return None


def _check_scene_match(report, dataset, videos, paired):
    """
    A scene file that belongs to another video still parses. Comparing the last scene with the
    video's real frame count, on a few videos, is what catches a wrong or renamed archive.
    """
    sample = sorted(paired)
    sample = [sample[i * (len(sample) - 1) // max(1, SAMPLE_VIDEOS_PER_COLLECTION - 1)]
              for i in range(min(SAMPLE_VIDEOS_PER_COLLECTION, len(sample)))]
    texts = dataset.scenes.read_many(sample)
    for video_id in sample:
        frames = _frame_count(videos[video_id])
        last = _last_scene_frame(texts.get(video_id, ""))
        if frames is None or last is None:
            report.add(WARN, dataset.name, f"{video_id}: could not compare scenes with the video")
        elif last >= frames + FRAME_TOLERANCE:
            report.add(FAIL, dataset.name, f"{video_id}: last scene ends at frame {last}, the video "
                                           f"has {frames} -- these scene files do not belong to "
                                           f"these videos")
        else:
            report.add(OK, dataset.name, f"{video_id}: scenes end at {last} of {frames} frames")


def check_sources(report, collection=None):
    if not Config.SOURCE_ROOT:
        report.add(WARN, "sources", "CLIMB_SOURCE_ROOT is not set; only absolute CLIMB_<NAME>_* "
                                    "overrides can resolve")
    elif not Path(os.path.expanduser(Config.SOURCE_ROOT)).is_dir():
        report.add(FAIL, "sources", f"CLIMB_SOURCE_ROOT={Config.SOURCE_ROOT} does not exist "
                                    f"(drive not mounted?)")
        return

    wanted = [datasets.load(collection)] if collection else datasets.all_datasets()
    usable = 0
    for dataset in wanted:
        videos = dataset.video_files()
        if not videos and dataset.scenes is None:
            level = FAIL if collection else WARN
            report.add(level, dataset.name, f"not on this machine, looked for videos in "
                                            f"{dataset.videos} -- skipped")
            continue
        if dataset.scenes is None:
            report.add(FAIL, dataset.name, f"{len(videos):,} videos but no scene files, looked in "
                                           f"{', '.join(dataset.scene_candidates)}")
            continue

        scene_ids = dataset.scenes.ids()
        paired = set(videos) & scene_ids
        if videos and not paired:
            report.add(FAIL, dataset.name, f"{len(videos):,} videos and {len(scene_ids):,} scene "
                                           f"files, but no names in common ({dataset.scenes.path})")
            continue
        usable += 1
        kind = "archive" if dataset.scenes.is_archive else "folder"
        report.add(OK, dataset.name, f"{len(videos):,} videos, {len(paired):,} with scenes "
                                     f"({kind} {dataset.scenes.path})")
        if videos and len(paired) < len(videos):
            report.add(WARN, dataset.name, f"{len(videos) - len(paired):,} video(s) have no scene "
                                           f"file and will be skipped, e.g. "
                                           f"{sorted(set(videos) - paired)[:3]}")
        if paired:
            _check_scene_match(report, dataset, videos, paired)

        if dataset.info_candidates:
            if dataset.info is None:
                report.add(WARN, dataset.name, f"no info files, looked in "
                                               f"{', '.join(dataset.info_candidates)}")
            elif dataset.info.is_archive:
                report.add(WARN, dataset.name, f"info from {dataset.info.path}: no tags or "
                                               f"categories in that version")
            else:
                report.add(OK, dataset.name, f"info folder {dataset.info.path}")

    if not usable:
        report.add(FAIL, "sources", "no collection can be ingested on this machine")


# --- output directories ----------------------------------------------------------------------

def _free_bytes(path) -> int:
    path = Path(path)
    while not path.exists():
        path = path.parent
    return shutil.disk_usage(path).free


def check_outputs(report, videos=0, hours=None):
    root = Path(os.path.expanduser(Config.SOURCE_ROOT)).resolve() if Config.SOURCE_ROOT else None
    for label, directory in (("media", Config.MEDIA_DIR), ("work", Config.WORK_DIR)):
        path = Path(os.path.expanduser(directory)).resolve()
        if root and (path == root or root in path.parents):
            report.add(FAIL, "output", f"CLIMB_{label.upper()}_DIR={path} is inside "
                                       f"CLIMB_SOURCE_ROOT; outputs never go into the sources")
            continue
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / f".climb-check-{uuid.uuid4().hex}"
            probe.write_bytes(b"")
            probe.unlink()
        except OSError as e:
            report.add(FAIL, "output", f"cannot write to {label} dir {path}: {e}")
            continue
        report.add(OK, "output", f"{label} dir {path}, {_free_bytes(path) / 1024 ** 3:,.0f} GB free")

    if hours:
        media_needed = hours * MEDIA_MB_PER_HOUR * 1024 ** 2
        if _free_bytes(Config.MEDIA_DIR) < media_needed:
            report.add(WARN, "output", f"keyframes for {hours:,.0f} h of video need ~"
                                       f"{media_needed / 1024 ** 3:,.0f} GB in the media dir")
    # One batch of candidates at a time, plus every video's audio until asr runs. Average video
    # length from what is ingested; before anything is, V3C's ~9 minutes.
    batch_hours = Config.PROCESS_BATCH * (hours / videos if hours and videos else 0.15)
    work_needed = (batch_hours * CANDIDATE_MB_PER_HOUR + (hours or 0) * AUDIO_MB_PER_HOUR) * 1024 ** 2
    if _free_bytes(Config.WORK_DIR) < work_needed:
        report.add(WARN, "output", f"the work dir needs ~{work_needed / 1024 ** 3:,.1f} GB: a batch "
                                   f"of {Config.PROCESS_BATCH} videos' candidate frames plus the "
                                   f"audio of all of them until asr; lower CLIMB_PROCESS_BATCH or "
                                   f"run asr sooner")


# --- database --------------------------------------------------------------------------------

def _version_tuple(text) -> tuple:
    return tuple(int(p) for p in text.split(".")[:3] if p.isdigit())


def check_database(report) -> tuple:
    """Returns (videos, hours) ingested so far, for the space estimate."""
    from db.connection import connect_to_database
    from db.migrate import applied_migrations, discover_migrations

    conf = Config()
    try:
        conn = connect_to_database()
    except ValueError as e:
        report.add(FAIL, "database", str(e))
        return 0, None
    if conn is None:
        report.add(FAIL, "database", f"cannot connect to {conf.db_host}:{conf.db_port}/"
                                     f"{conf.db_name} -- is the container running? "
                                     f"(podman start {conf.DB_CONTAINER_NAME})")
        return 0, None

    videos, hours = 0, None
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector';")
            row = cur.fetchone()
        if row and _version_tuple(row[0]) < MIN_PGVECTOR:
            report.add(FAIL, "database", f"pgvector {row[0]} has no binary_quantize(), "
                                         f"needs >= 0.7 ({conf.DB_IMAGE})")

        try:
            applied = applied_migrations(conn)
            files = [(v, n, hashlib.sha256(p.read_text(encoding="utf-8").encode("utf-8"))
                      .hexdigest()) for v, n, p in discover_migrations()]
        except Exception as e:
            conn.rollback()
            report.add(FAIL, "database", f"cannot read the migrations: {e}")
            return 0, None
        # The same comparison `migrate` makes: an edited file means this database and the code
        # disagree about the schema, and `migrate` will refuse to run.
        edited = [f"{v} {n}" for v, n, checksum in files if v in applied and applied[v] != checksum]
        if edited:
            report.add(FAIL, "database", f"migration(s) edited after they were applied here: "
                                         f"{', '.join(edited)}")
            return 0, None
        pending = [f"{v} {n}" for v, n, _ in files if v not in applied]
        if pending:
            report.add(FAIL, "database", f"{len(pending)} migration(s) not applied ({pending[0]}"
                                         f"{', ...' if len(pending) > 1 else ''}): "
                                         f"python main.py migrate")
            return 0, None

        with conn.cursor() as cur:
            cur.execute("SELECT name FROM embedding_model;")
            registered = {r[0] for r in cur.fetchall()}
            for setting, model in (("KIS_MODEL_NAME", conf.KIS_MODEL_NAME),
                                   ("CLIMB_TEXT_MODEL", conf.TEXT_MODEL)):
                if model not in registered:
                    report.add(FAIL, "database", f"{setting}={model} is not in embedding_model; "
                                                 f"its stage refuses to run")
            cur.execute("SELECT count(*), coalesce(sum(duration_ms), 0) / 3600000.0 FROM videos;")
            videos, hours = cur.fetchone()
        report.add(OK, "database", f"{conf.db_host}:{conf.db_port}/{conf.db_name}, schema "
                                   f"current, {videos:,} videos ingested")
        hours = float(hours) or None
    finally:
        conn.close()

    _check_container_shm(report, conf)
    return videos, hours


def _check_container_shm(report, conf):
    """Only when the database is a local container; looked up by the port it publishes."""
    engine = shutil.which("podman") or shutil.which("docker")
    if engine is None or conf.db_host not in ("localhost", "127.0.0.1"):
        return
    listing = _run([engine, "ps", "--format", "{{.Names}}\t{{.Ports}}"])
    if listing is None or listing.returncode != 0:
        return
    name = next((line.split("\t")[0] for line in listing.stdout.splitlines()
                 if f":{conf.db_port}->5432" in line), None)
    if name is None:
        return
    result = _run([engine, "inspect", "--format", "{{.HostConfig.ShmSize}}", name])
    if result is None or result.returncode != 0:
        return
    try:
        shm = int(result.stdout.strip())
    except ValueError:
        return
    if shm < MIN_SHM_BYTES:
        report.add(WARN, "database", f"container {name} has {shm / 1024 ** 2:.0f} "
                                     f"MB /dev/shm; `index build` falls back to a slow serial "
                                     f"build. Recreate it with --shm-size=8g")


def run_checks(collection=None) -> bool:
    """Prints every finding. Returns True when nothing failed."""
    report = Report()
    print("CLIMB pre-run check\n")
    check_tools(report)
    check_packages(report)
    check_gpu(report)
    check_models(report)
    check_sources(report, collection)
    videos, hours = check_database(report)
    check_outputs(report, videos, hours)

    warnings = sum(1 for level, _, _ in report.findings if level == WARN)
    failures = sum(1 for level, _, _ in report.findings if level == FAIL)
    print(f"\n{failures} problem(s), {warnings} warning(s)."
          + (" Fix the FAIL lines before running anything." if failures else ""))
    return not report.failed
