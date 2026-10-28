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
    # The visual model for machines with a GPU and the one for machines without. Both default to
    # SigLIP2 large, so `embed` embeds once and every machine searches with large. Naming a bigger
    # model in CLIMB_EMBED_MODEL adds a second pass after large, and machines with a GPU then
    # search with it (see search_model): the text tower runs on every query, which a CPU machine
    # keeps doing with large.
    KIS_MODEL_NAME: str = os.getenv("CLIMB_EMBED_MODEL") or "google/siglip2-large-patch16-384"
    KIS_CPU_MODEL_NAME: str = (os.getenv("CLIMB_EMBED_MODEL_CPU")
                               or "google/siglip2-large-patch16-384")
    EMBEDDING_BATCH_SIZE: int = int(os.getenv("CLIMB_EMBED_BATCH") or 16)
    # Threads decoding the next keyframes while the model works on this batch. On the laptop,
    # decoding ran at 522 keyframes/s with one thread, 1137 with two, 1310 with four and 1049
    # with eight.
    EMBED_LOADERS: int = int(os.getenv("CLIMB_EMBED_LOADERS") or 4)
    SEARCH_TOP_K: int = 48

    # --- Decode stage ---
    VIDEO_EXTENSIONS = {".mp4", ".webm", ".mkv", ".avi", ".mov"}  # valid video extensions
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
    # paddle only: text detection runs on the frame upscaled to this short side (see PaddleReader).
    OCR_DET_MIN_SIDE: int = int(os.getenv("CLIMB_OCR_DET_MIN_SIDE") or 736)
    # 'auto' prefers paddle and falls back to rapidocr-onnxruntime, so when a GPU is available it uses
    # the better model without configuration.
    OCR_BACKEND: str = os.getenv("CLIMB_OCR_BACKEND") or "auto"
    # 'all' = every keyframe, 'shot' = only kf_index 0.
    # OCR defaults to 'all': WP4 picks keyframes *by visual difference*, so the frames skipped by
    # 'shot' are exactly the ones most likely to show different text
    OCR_SCOPE: str = os.getenv("CLIMB_OCR_SCOPE") or "all"
    CAPTION_SCOPE: str = os.getenv("CLIMB_CAPTION_SCOPE") or "shot"
    CAPTION_MODEL: str = os.getenv("CLIMB_CAPTION_MODEL") or "Qwen/Qwen3-VL-4B-Instruct"
    CAPTION_BATCH_SIZE: int = int(os.getenv("CLIMB_CAPTION_BATCH") or 8)
    CAPTION_MAX_TOKENS: int = 64
    CAPTION_IMAGE_SPLITTING: bool = (os.getenv("CLIMB_CAPTION_IMAGE_SPLITTING") or "0") \
        .lower() in ("1", "true", "yes") # true takes 5xlonger without any benefit since there will be no new details found through upscaling
    CAPTION_PROMPT: str = ("Describe this video frame in one sentence. Name the objects, people "
                           "and setting, and how they relate to each other.")
    ASR_MODEL: str = os.getenv("CLIMB_ASR_MODEL") or "large-v3"
    ASR_BEAM_SIZE: int = 1
    # When a segment counts as a Whisper hallucination rather than speech (pipeline/asr.py,
    # flag_hallucinations). Set from a probe over 61 MVK clips (every segment invented) and 14 V3C
    # videos with large-v3-turbo; changing them is `python main.py asr --reclassify`, no new
    # transcription. Whisper's no_speech_prob is stored but not used: it was 0.0 for every segment.
    # Above 2.4 Whisper itself treats a window as a repetition loop; the probe's highest was 2.34.
    ASR_MAX_COMPRESSION_RATIO: float = float(os.getenv("CLIMB_ASR_MAX_COMPRESSION_RATIO") or 2.4)
    # A segment with no other speech this close before or after it is isolated.
    ASR_ISOLATION_MS: int = int(os.getenv("CLIMB_ASR_ISOLATION_MS") or 30000)
    # An isolated segment below this mean log probability is dropped. The invented MVK segments
    # were -1.21 to -0.32, the V3C ones that may be real speech -0.63 and above.
    ASR_ISOLATED_MIN_AVG_LOGPROB: float = float(os.getenv("CLIMB_ASR_ISOLATED_MIN_AVG_LOGPROB")
                                                or -0.8)
    # The phrases Whisper makes from noise and music. An isolated one is dropped whatever its
    # confidence; inside a conversation it is kept, since people do say "Thank you." Compared
    # lowercased with whitespace collapsed.
    ASR_HALLUCINATION_PHRASES: tuple = (
        "thank you.", "thank you", "thank you!", "thanks for watching!", "thank you for watching!",
        "thank you for watching.", "you", "oh", "music", "we'll see you next time.",
        "продолжение следует...", "obrigado.", "ご視聴ありがとうございました",
        "subtitles by the amara.org community",
    )
    # Cross-lingual text encoder for OCR strings and transcripts. V3C signage and speech are
    # multilingual while the queries are English, so this has to be a multilingual model.
    TEXT_MODEL: str = os.getenv("CLIMB_TEXT_MODEL") or "intfloat/multilingual-e5-large"
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
    # A search restricted to at most this many videos (video:, or what text:/said:/tag: leave over)
    # ranks by exact distance over just their embeddings instead of the ANN index. ~150 keyframes
    # per V3C video, so 300 videos is ~45k exact distances: measured 170 ms on the laptop CPU for
    # 45k 1024-d halfvecs (23 ms for 4.5k), about what embedding the query costs there anyway.
    EXACT_SCOPE_MAX_VIDEOS: int = int(os.getenv("CLIMB_EXACT_SCOPE_MAX_VIDEOS") or 300)
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
    # The V3C uploader's title, tags and description, matched against the query. A video-level
    # prior: it re-orders keyframes the other signals found, never adds any. 1.0 like the others
    # until WP9 can measure it.
    RRF_WEIGHT_INFO: float = float(os.getenv("CLIMB_W_INFO") or 1.0)
    # Query words that are tags of a video (retrievers.tags). Video-level like info, re-ordering
    # what the frame-level signals found; 1.0 until WP9 can measure it.
    RRF_WEIGHT_TAGS: float = float(os.getenv("CLIMB_W_TAGS") or 1.0)
    # How many videos the info and tags retrievers rank.
    INFO_DEPTH: int = int(os.getenv("CLIMB_INFO_DEPTH") or 500)

    # --- Temporal queries (see retrieval/temporal.py) ---
    # Query-side only: no schema, no embeddings, nothing to re-index.
    TEMPORAL_DEFAULT_DELTA_MS: int = int(os.getenv("CLIMB_TEMPORAL_DELTA_MS") or 60_000)
    TEMPORAL_MAX_DELTA_MS: int = int(os.getenv("CLIMB_TEMPORAL_MAX_DELTA_MS") or 600_000)
    TEMPORAL_MAX_STAGES: int = int(os.getenv("CLIMB_TEMPORAL_MAX_STAGES") or 4)
    # Deeper than a normal search on purpose. A chain only exists where *every* stage
    # independently surfaced a hit in the same video, so stage depth is what buys recall here --
    # and it is the only knob that does, short of rescoring whole videos.
    TEMPORAL_STAGE_DEPTH: int = int(os.getenv("CLIMB_TEMPORAL_STAGE_DEPTH") or 500)
    TEMPORAL_STAGE_TOP_K: int = int(os.getenv("CLIMB_TEMPORAL_STAGE_TOP_K") or 1000)
    # A long video can contain dozens of legal chains; without a cap it owns the whole grid.
    TEMPORAL_MAX_PER_VIDEO: int = int(os.getenv("CLIMB_TEMPORAL_MAX_PER_VIDEO") or 3)

    # How many query vectors to keep. Embedding a query string is the single most expensive part of
    # a search on my CPU only laptop, on GPU it's not that important but caching is not slower than not doing it.
    QUERY_EMBED_CACHE_SIZE: int = int(os.getenv("CLIMB_QUERY_EMBED_CACHE") or 512)

    # --- Per-collection visual models ---
    # A second visual model for one collection, searching next to SigLIP2 when the dataset
    # dropdown names that collection: PeskaVLP (surgical video lectures) for the hysterectomies.
    # `embed` embeds it for its collection only. Empty switches it off. MVK had BioCLIP 2.5 until
    # it was measured on MSC's described MVK clips (README 1.7).
    COLLECTION_MODELS_RAW: str = (os.getenv("CLIMB_COLLECTION_MODELS")
                                  if os.getenv("CLIMB_COLLECTION_MODELS") is not None
                                  else "LHE:camma/peskavlp")
    # The domain model's signal in the fusion, next to SigLIP2's RRF_WEIGHT_VISUAL. On GynSurg's
    # labelled frames 1.0 gave PeskaVLP + SigLIP2 54% of the first page and 2.0 56%, not
    # significantly more; 0.5 was significantly worse (README 1.7).
    RRF_WEIGHT_DOMAIN: float = float(os.getenv("CLIMB_W_DOMAIN") or 1.0)

    def embed_models(self) -> list:
        """
        The visual models `embed` embeds with, each once. The CPU one goes first: every machine
        can search with it, so a run stopped during the second pass still leaves one complete.
        """
        return list(dict.fromkeys([self.KIS_CPU_MODEL_NAME, self.KIS_MODEL_NAME]))

    def search_model(self, dev: str) -> str:
        """The visual model a search on this device prefers (see choose_visual_model)."""
        return self.KIS_CPU_MODEL_NAME if dev == "cpu" else self.KIS_MODEL_NAME

    def asr_hallucination_phrases(self) -> frozenset:
        return frozenset(" ".join(p.lower().split()) for p in self.ASR_HALLUCINATION_PHRASES)

    def collection_of(self, model_name: str) -> str | None:
        """The collection a domain model is for, or None for a model every collection uses."""
        return next((c for c, m in self.collection_models().items() if m == model_name), None)

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
            "info": self.RRF_WEIGHT_INFO,
            "tags": self.RRF_WEIGHT_TAGS,
        }

    DECODE_WORKERS: int = int(os.getenv("CLIMB_DECODE_WORKERS") or 6)
    # Videos per decode -> select -> purge round in `run`. Candidate frames are ~130 MB per hour of
    # video and only exist between decode and purge, so this is what bounds work/ (200 V3C videos
    # are ~29 h, ~4 GB). See runner.process_in_batches.
    PROCESS_BATCH: int = int(os.getenv("CLIMB_PROCESS_BATCH") or 200)
    FFMPEG_THREADS: int = int(os.getenv("CLIMB_FFMPEG_THREADS") or 2)

    # --- Paths ---
    # Root of the source collections: videos, scene files, info JSONs. The layout under it is in
    # /datasets.json, see pipeline/datasets.py. Nothing is ever written here.
    SOURCE_ROOT: str = os.getenv("CLIMB_SOURCE_ROOT") or ""
    LOG_FOLDER: str = str(VIDEO_PROCESSING_ROOT / "logs")
    MIGRATIONS_DIR: str = str(VIDEO_PROCESSING_ROOT / "migrations")

    #   CLIMB_MEDIA_DIR  persistent -- keyframes, thumbnails
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
    DB_CONTAINER_NAME: str = os.getenv("CLIMB_DB_CONTAINER") or "climb"
    # The volume the cluster lives in; another name is another, empty database.
    DB_VOLUME: str = os.getenv("CLIMB_DB_VOLUME") or "postgres_data"
    # The official postgres image (which pgvector/pgvector is built on) puts PGDATA at
    # /var/lib/postgresql/data. Mounting the parent instead leaves the cluster inside the
    # container's own filesystem, so the volume holds nothing and the data dies with the container.
    DB_CONTAINER_MOUNT_PATH: str = "/var/lib/postgresql/data"
    DB_IMAGE: str = "docker.io/pgvector/pgvector:pg17"
    # The container default is 64 MB, and a parallel HNSW build wants a shared segment the size of
    # maintenance_work_mem (PG_BUILD_MAINTENANCE_WORK_MEM), so `index build` dies with "No space
    # left on device" and falls back to a serial build. A limit, not a reservation.
    DB_SHM_SIZE: str = "8g"

    # --- Vector index ---
    # m=16 / ef_construction=64 are pgvector's defaults and are adequate for binary vectors.
    HNSW_M: int = 16
    HNSW_EF_CONSTRUCTION: int = 64
    # How many candidates the ANN stage hands to the exact rerank. This is the recall/latency dial.
    # Measured on 35,558 keyframes over the 90 TRECVID AVS 2019-21 topics, mean recall@20 against
    # exhaustive exact search, median latency (EXPERIMENTS.md, 2026-10-28):
    #     1000 -> 0.956 at 17 ms       2000 -> 0.983 at 33 ms
    #     4000 -> 0.994 at 63 ms      35558 -> 1.000 at 73 ms
    # The ceiling is binary quantization, not the index: the ANN stage ranks by hamming distance on
    # 1-bit vectors, so the true nearest neighbours by cosine are not all inside its top-N. 2000
    # because it moves a whole search on the laptop CPU from 442 to 469 ms, most of which is the
    # text encoding; 4000 doubles the ANN again for the last 1%, which is shit for competition.
    # Anything above HNSW_EF_SEARCH_MAX, this default included, runs as an iterative scan (see
    # retrievers._ann_rerank).
    ANN_OVERSAMPLE: int = int(os.getenv("CLIMB_ANN_OVERSAMPLE") or 2000)
    HNSW_EF_SEARCH_MAX: int = 1000
    HNSW_MAX_SCAN_TUPLES: int = int(os.getenv("CLIMB_HNSW_MAX_SCAN_TUPLES") or 100_000)

    # --- Postgres tuning: build profile ---
    PG_BUILD_MAINTENANCE_WORK_MEM: str = "8GB"
    PG_BUILD_PARALLEL_WORKERS: int = 7

    # --- Postgres tuning: serve profile ---
    PG_SERVE_SHARED_BUFFERS: str = "4GB"
    PG_SERVE_WORK_MEM: str = "64MB"
    PG_SERVE_EFFECTIVE_CACHE_SIZE: str = "8GB"
    PG_SERVE_HNSW_EF_SEARCH: int = 1000 # hnsw.ef_search caps the rows an HNSW scan returns, silently; 1000 is pgvector's maximum, past it the retrievers switch on the iterative scan.

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

