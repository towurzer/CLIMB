import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

VIDEO_PROCESSING_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = VIDEO_PROCESSING_ROOT.parent

load_dotenv(PROJECT_ROOT / ".env")

@dataclass
class Config:
    # --- KIS Model parameters ---
    KIS_MODEL_NAME: str = "google/siglip2-large-patch16-384"
    EMBEDDING_BATCH_SIZE: int = int(os.getenv("CLIMB_EMBED_BATCH") or 16)
    SEARCH_TOP_K: int = 48

    # --- Decode stage ---
    VIDEO_EXTENSIONS = {".mp4", ".webm", ".mkv", ".avi", ".mov"}  # valid video extensions
    WEB_VIDEO_EXTENSION = ".mp4"
    WEB_VIDEO_HEIGHT: int = 360  # never upscales; a source shorter than this is left alone
    WEB_VIDEO_CRF: int = 32
    WEB_AUDIO_BITRATE: str = "48k"
    # set CLIMB_X264_PRESET=superfast to trade ~23% more disk for ~1.5x faster encoding.
    WEB_VIDEO_PRESET: str = os.getenv("CLIMB_X264_PRESET") or "veryfast"
    CANDIDATE_FPS: int = 2
    CANDIDATE_HEIGHT: int = 384  # matches SigLIP2's 384px input
    CANDIDATE_JPEG_QUALITY: int = 3  # ffmpeg -q:v scale, 2 (best) to 31 (worst)
    # Audio exists only to be fed to Whisper, which resamples to 16 kHz mono anyway.
    AUDIO_SAMPLE_RATE: int = 16000
    AUDIO_BITRATE: str = "16k"

    # --- Keyframe selection (see pipeline/keyframe_selection.py) ---
    # k = clamp(ceil(shot_seconds / KEYFRAME_SECONDS_PER), MIN, MAX).
    KEYFRAME_SECONDS_PER: int = 4
    KEYFRAME_MIN_K: int = 2
    KEYFRAME_MAX_K: int = 32
    KEYFRAME_HEIGHT: int = 384   # detail panel + model input
    THUMBNAIL_HEIGHT: int = 160  # result grid
    WEBP_QUALITY: int = 80
    # libwebp encoder effort. OpenCV hardcodes 4; measured on 384px frames, method=2 is 2.4x
    # faster for 4% more bytes (12.9ms/11.9KB vs 30.7ms/11.4KB), (the testing took ages, but
    # running the whole thing would have taken longer) which is why these are written
    # through PIL. Encoding dominates this stage, so it is worth the 4%.
    WEBP_METHOD: int = 2
    # Fades and black frames are everywhere in V3C and make useless keyframes.
    DEGENERATE_LUMA_MIN: int = 16
    DEGENERATE_LUMA_MAX: int = 240
    DEGENERATE_STDDEV_MIN: int = 8
    # Escape hatch so title cards and credits are not mistaken for fades
    DEGENERATE_EDGE_MAX: float = 0.002
    SELECTION_WORKERS: int = int(os.getenv("CLIMB_SELECTION_WORKERS") or 6)

    # --- GPU stages  ---
    # Drop batch sizes via env
    OCR_BATCH_SIZE: int = int(os.getenv("CLIMB_OCR_BATCH") or 32)
    OCR_MIN_CONFIDENCE: float = 0.5
    # 'auto' prefers paddle and falls back to rapidocr-onnxruntime, so when a GPU is available it uses
    # the better model without configuration.
    OCR_BACKEND: str = os.getenv("CLIMB_OCR_BACKEND") or "auto"
    # 'all' = every keyframe, 'shot' = only kf_index 0.
    # OCR defaults to 'all': WP4 picks keyframes *by visual difference*, so the frames skipped by
    # 'shot' are exactly the ones most likely to show different text
    OCR_SCOPE: str = os.getenv("CLIMB_OCR_SCOPE") or "all"
    CAPTION_SCOPE: str = os.getenv("CLIMB_CAPTION_SCOPE") or "shot"
    CAPTION_MODEL: str = os.getenv("CLIMB_CAPTION_MODEL") or "Qwen/Qwen2-VL-2B-Instruct"
    CAPTION_BATCH_SIZE: int = int(os.getenv("CLIMB_CAPTION_BATCH") or 8)
    CAPTION_MAX_TOKENS: int = 64
    CAPTION_IMAGE_SPLITTING: bool = (os.getenv("CLIMB_CAPTION_IMAGE_SPLITTING") or "0") \
        .lower() in ("1", "true", "yes") # true takes 5xlonger without any benefit since there will be no new details found through upscaling
    CAPTION_PROMPT: str = ("Describe this video frame in one sentence. Name the objects, people "
                           "and setting, and how they relate to each other.")
    ASR_MODEL: str = os.getenv("CLIMB_ASR_MODEL") or "large-v3-turbo"
    ASR_BEAM_SIZE: int = 1
    # Cross-lingual text encoder for OCR strings and transcripts. V3C signage and speech are
    # multilingual while the queries are English, so this has to be a multilingual model.
    TEXT_MODEL: str = os.getenv("CLIMB_TEXT_MODEL") or "intfloat/multilingual-e5-base"
    TEXT_BATCH_SIZE: int = int(os.getenv("CLIMB_TEXT_BATCH") or 128)
    TEXT_MAX_TOKENS: int = 256

    # --- Fetch stage ---
    # How the collection is reachable is a property of the server, not of this pipeline, so the
    # transfer is a command template rather than a hardcoded tool.
    FETCH_COMMAND: str = os.getenv("CLIMB_FETCH_COMMAND") or "rsync -a --partial {source} {dest}"
    FETCH_BATCH: int = int(os.getenv("CLIMB_FETCH_BATCH") or 50)

    # --- Retrieval (see retrieval/) ---
    # How deep each retriever goes before fusion. Deeper costs little and lets a signal that ranks
    # a scene poorly still contribute.
    RETRIEVER_DEPTH: int = int(os.getenv("CLIMB_RETRIEVER_DEPTH") or 200)
    ASR_SEGMENT_LIMIT: int = 500
    # ts_rank_cd with normalisation 32 is in (0,1). The floor stops OCR noise that happens to
    # spell a real word from being promoted by the heavy lexical weight below. Tune with WP9.
    OCR_MIN_RANK: float = float(os.getenv("CLIMB_OCR_MIN_RANK") or 0.01)
    OCR_TRIGRAM_THRESHOLD: float = 0.45

    # RRF weights. OCR is weighted far above the rest deliberately: an exact match on a proper noun
    # is close to proof, and it is the one thing embeddings cannot represent at all. The others are
    # comparable to each other, so they stay at 1.
    RRF_WEIGHT_VISUAL: float = float(os.getenv("CLIMB_W_VISUAL") or 1.0)
    RRF_WEIGHT_OCR: float = float(os.getenv("CLIMB_W_OCR") or 4.0)
    RRF_WEIGHT_CAPTION: float = float(os.getenv("CLIMB_W_CAPTION") or 1.0)
    RRF_WEIGHT_TRANSCRIPT: float = float(os.getenv("CLIMB_W_TRANSCRIPT") or 1.0)
    # Explicit text:"..." / said:"..." searches outrank everything -- the user told us what to find.
    RRF_WEIGHT_PHRASE: float = float(os.getenv("CLIMB_W_PHRASE") or 8.0)

    # --- Temporal queries (see retrieval/temporal.py) ---
    # Query-side only: no schema, no embeddings, nothing to re-index.
    TEMPORAL_DEFAULT_DELTA_MS: int = int(os.getenv("CLIMB_TEMPORAL_DELTA_MS") or 30_000)
    TEMPORAL_MAX_DELTA_MS: int = int(os.getenv("CLIMB_TEMPORAL_MAX_DELTA_MS") or 600_000)
    TEMPORAL_MAX_STAGES: int = int(os.getenv("CLIMB_TEMPORAL_MAX_STAGES") or 4)
    # Deeper than a normal search on purpose. A chain only exists where *every* stage
    # independently surfaced a hit in the same video, so stage depth is what buys recall here --
    # and it is the only knob that does, short of rescoring whole videos.
    TEMPORAL_STAGE_DEPTH: int = int(os.getenv("CLIMB_TEMPORAL_STAGE_DEPTH") or 500)
    TEMPORAL_STAGE_TOP_K: int = int(os.getenv("CLIMB_TEMPORAL_STAGE_TOP_K") or 1000)
    # A long video can contain dozens of legal chains; without a cap it owns the whole grid.
    TEMPORAL_MAX_PER_VIDEO: int = int(os.getenv("CLIMB_TEMPORAL_MAX_PER_VIDEO") or 3)

    # --- Per-collection visual models ---
    COLLECTION_MODELS_RAW: str = os.getenv("CLIMB_COLLECTION_MODELS") or ""

    def collection_models(self) -> dict:
        mapping = {}
        for entry in self.COLLECTION_MODELS_RAW.split(","):
            entry = entry.strip()
            if not entry or ":" not in entry:
                continue
            collection, model = entry.split(":", 1)
            mapping[collection.strip().upper()] = model.strip()
        return mapping

    def rrf_weights(self) -> dict:
        return {
            "visual": self.RRF_WEIGHT_VISUAL,
            "ocr": self.RRF_WEIGHT_OCR,
            "caption": self.RRF_WEIGHT_CAPTION,
            "transcript": self.RRF_WEIGHT_TRANSCRIPT,
            "ocr_phrase": self.RRF_WEIGHT_PHRASE,
            "asr_phrase": self.RRF_WEIGHT_PHRASE,
        }

    DECODE_WORKERS: int = int(os.getenv("CLIMB_DECODE_WORKERS") or 6)
    FFMPEG_THREADS: int = int(os.getenv("CLIMB_FFMPEG_THREADS") or 2)

    # --- Paths ---
    DATA_DIR: str = str(PROJECT_ROOT / "dataset")
    DATASET_FOLDER: str = "V3C1_200"
    SCENES_DIR: str = "scenes_v3c1_204/scenes_v3c1_204"
    # Which shard the videos in DATASET_FOLDER belong to.
    COLLECTION: str = "V3C1"
    LOG_FOLDER: str = str(VIDEO_PROCESSING_ROOT / "logs")
    MIGRATIONS_DIR: str = str(VIDEO_PROCESSING_ROOT / "migrations")

    #   CLIMB_MEDIA_DIR  ~510 GB, persistent -- 360p video, keyframes, thumbnails
    #   CLIMB_WORK_DIR   transient -- raw downloads, candidate frames, extracted audio
    MEDIA_DIR: str = os.getenv("CLIMB_MEDIA_DIR") or str(PROJECT_ROOT / "dataset" / "media")
    WORK_DIR: str = os.getenv("CLIMB_WORK_DIR") or str(PROJECT_ROOT / "dataset" / "work")

    # --- Logging ---
    LOG_FILE: str = "CLIMB.log"
    ERROR_FILE: str = "CLIMB_ERROR.log"
    # Log Levels: DEBUG | INFO | WARN | ERROR | CRITICAL
    LOG_LEVEL_MIN: str = "DEBUG"  # logs with a lower level will be ignored before reaching the other loggers (i.e. console / file), DO NOT TOUCH
    LOG_LEVEL_CONSOLE: str = "INFO"
    LOG_LEVEL_FILE: str = "DEBUG"
    LOG_LEVEL_ERROR: str = "WARN"

    # --- Database ---
    DB_CONTAINER_NAME: str = "climb"
    # The official postgres image (which pgvector/pgvector is built on) puts PGDATA at
    # /var/lib/postgresql/data. Mounting the parent instead leaves the cluster inside the
    # container's own filesystem, so the volume holds nothing and the data dies with the container.
    DB_CONTAINER_MOUNT_PATH: str = "/var/lib/postgresql/data"
    DB_IMAGE: str = "docker.io/pgvector/pgvector:pg17"

    # --- Vector index ---
    # m=16 / ef_construction=64 are pgvector's defaults and are adequate for binary vectors.
    HNSW_M: int = 16
    HNSW_EF_CONSTRUCTION: int = 64
    # How many candidates the ANN stage hands to the exact rerank. This is the recall/latency dial.
    # Measured on 7,543 keyframes, recall@20 against exhaustive exact search:
    #     1000 -> 15/20 at  29 ms      2000 -> 18/20 at  54 ms
    #     4000 -> 20/20 at  91 ms      7543 -> 20/20 at 108 ms
    # The ceiling is binary quantization, not the index: the ANN stage ranks by hamming distance on
    # 1-bit vectors, so the true nearest neighbours by cosine are not all inside its top-N. Left at
    # 1000 because tripling query latency is shit for competition
    ANN_OVERSAMPLE: int = int(os.getenv("CLIMB_ANN_OVERSAMPLE") or 1000)
    HNSW_EF_SEARCH_MAX: int = 1000
    HNSW_MAX_SCAN_TUPLES: int = int(os.getenv("CLIMB_HNSW_MAX_SCAN_TUPLES") or 100_000)

    # --- Postgres tuning: build profile ---
    PG_BUILD_MAINTENANCE_WORK_MEM: str = "8GB"
    PG_BUILD_PARALLEL_WORKERS: int = 7

    # --- Postgres tuning: serve profile ---
    PG_SERVE_SHARED_BUFFERS: str = "4GB"
    PG_SERVE_WORK_MEM: str = "64MB"
    PG_SERVE_EFFECTIVE_CACHE_SIZE: str = "8GB"
    PG_SERVE_HNSW_EF_SEARCH: int = 1000 # hnsw.ef_search must be >= the oversample LIMIT or pgvector silently returns fewer rows.

    @property
    def db_user(self) -> str:
        value = os.getenv("POSTGRES_USER")
        if not value:
            return "postgres"
        return value

    @property
    def db_host(self) -> str:
        value = os.getenv("DB_HOST")
        if not value:
            return "localhost"
        return value

    @property
    def db_port(self) -> str:
        value = os.getenv("DB_PORT")
        if not value:
            return "5432"
        return value

    @property
    def db_name(self) -> str:
        value = os.getenv("POSTGRES_DB_NAME")
        if not value:
            raise ValueError(
                "The property 'POSTGRES_DB_NAME' is required for database operations, please add it to your .env file in the project root directory")
        return value

    @property
    def db_password(self) -> str:
        value = os.getenv("POSTGRES_PASSWORD")
        if not value:
            raise ValueError(
                "The property 'POSTGRES_PASSWORD' is required for database operations, please add it to your .env file in the project root directory")
        return value

    # --- Search Engine URL ---
    @property
    def search_engine_url(self) -> str:
        value = os.getenv("SEARCH_ENGINE_URL")
        if not value:
            raise ValueError(
                "The property 'SEARCH_ENGINE_URL' is required to run the search engine, please add it to your .env file in the project root directory")
        return value

    @property
    def search_engine_port(self) -> int:
        value = os.getenv("SEARCH_ENGINE_PORT")
        if not value:
            raise ValueError(
                "The property 'SEARCH_ENGINE_PORT' is required to run the search engine, please add it to your .env file in the project root directory")
        try:
            return int(value)
        except ValueError as e:
            raise ValueError(
                "SEARCH_ENGINE_PORT must be an integer in your .env file, e.g. SEARCH_ENGINE_PORT=5000") from e

