import os
from typing import List
from dataclasses import dataclass, field
from pathlib import Path

VIDEO_PROCESSING_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = VIDEO_PROCESSING_ROOT.parent

@dataclass
class Config:
    # --- Model parameters ---
    MODEL_NAME: str = "google/siglip2-large-patch16-384"
    EMBEDDING_BATCH_SIZE: int = 16

    # --- Search parameters ---
    SEARCH_TOP_K: int = 30

    # --- Video Compression ---
    WEB_RESOLUTION = 480 # fast to process and loads instantly in web UIs.
    VIDEO_EXTENSIONS = {".mp4", ".webm", ".mkv", ".avi", ".mov"} # valid video extensions
    WEB_VIDEO_EXTENSION = ".mp4"
    COMPRESSION_PARALLEL =  True # whether to use multiprocessing for video compression
    # --- Paths ---
    DATA_DIR: str = str(PROJECT_ROOT / "dataset")
    DATASET_FOLDER: str = "V3C1_200"
    SCENES_DIR: str = "scenes_v3c1_200/scenes_v3c1_200"
    WEB_READY_DATASET_FOLDER: str = "web_ready"
    LOG_FOLDER: str = str(VIDEO_PROCESSING_ROOT / "logs")
    KEYFRAME_FOLDER :str = "keyframes"

    # --- Logging ---
    LOG_FILE: str = "CLIMB.log"
    COMPRESSION_CHECKPOINT_FILE: str = "compression.checkpoint"
    # Log Levels: DEBUG | INFO | WARN | ERROR | CRITICAL
    LOG_LEVEL_MIN: str = "DEBUG" # logs with a lower level will be ignored before reaching the other loggers (i.e. console / file), DO NOT TOUCH
    LOG_LEVEL_CONSOLE: str = "DEBUG"
    LOG_LEVEL_FILE: str = "DEBUG"

    # --- Database ---
    DB_CONTAINER_NAME: str = "climb"
    DB_CONTAINER_MOUNT_PATH: str = "/var/lib/postgresql/"
    DB_HOST: str = "localhost"
    DB_PORT: str = "5432"

    @property
    def db_name(self) -> str:
        value = os.getenv("POSTGRES_DB_NAME")
        if not value:
            raise ValueError("The property 'POSTGRES_DB_NAME' is required for database operations, please add it to your .env file in the project root directory")
        return value

    @property
    def db_password(self) -> str:
        value = os.getenv("POSTGRES_PASSWORD")
        if not value:
            raise ValueError("The property 'POSTGRES_PASSWORD' is required for database operations, please add it to your .env file in the project root directory")
        return value



@dataclass
class CLIConfig:
    compression_flags: List[str] = field(default_factory=lambda: ["-c", "--compress"])
    database_container_creation_flag: List[str] = field(default_factory=lambda: ["-spc", "--showPostgresCommand"])
    extract_keyframes: List[str] = field(default_factory=lambda: ["-ek", "--extractKeyframes"])
    extract_embeddings: List[str] = field(default_factory=lambda: ["-ee", "--extractEmbeddings"])
    help_flags: List[str] = field(default_factory=lambda: ["-h", "--help"])

    help_string = """
Usage:
  python main.py [OPTIONS]

Description:
  TODO

Options:
-c, --compress					// TODO: COMMENT
-spc, --showPostgresCommand     // TODO: COMMENT
-ek, --extractKeyframes        // TODO: COMMENT
-ee, --extractEmbeddings       // TODO: COMMENT
-h, --help						Show this help message and exit

Examples:
  // TODO
"""