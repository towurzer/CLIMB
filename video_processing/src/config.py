import os
from ast import List
from dataclasses import dataclass, field
from pathlib import Path

VIDEO_PROCESSING_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = VIDEO_PROCESSING_ROOT.parent

@dataclass
class Config:
    # --- Training settings ---
    #BATCH_SIZE: int
    #TRAIN_EPOCHS: int
    #TRAIN_DEVICE: str

    # --- Model settings ---
    #MODEL_NAME: str
    #IMAGE_SIZE: int
    #MODEL_FILE: str
    #CONF_THRESHOLD: float

    # --- Video Compression ---
    WEB_RESOLUTION = 480 # fast to process and loads instantly in web UIs.
    VIDEO_EXTENSIONS = {".mp4", ".webm", ".mkv", ".avi", ".mov"} # valid video extensions
    WEB_VIDEO_EXTENSION = ".mp4"


    # --- Paths ---
    DATA_DIR: str = str(PROJECT_ROOT / "dataset")
    DATASET_FOLDER: str = "V3C1_200"
    WEB_READY_DATASET_FOLDER: str = "web_ready"
    LOG_FOLDER: str = str(VIDEO_PROCESSING_ROOT / "logs")

    # --- Logging ---
    LOG_FILE: str = "CLIMB.log"
    COMPRESSION_CHECKPOINT_FILE: str = "compression.checkpoint"
    # Log Levels: DEBUG | INFO | WARN | ERROR | CRITICAL
    LOG_LEVEL_MIN: str = "DEBUG" # logs with a lower level will be ignored before reaching the other loggers (i.e. console / file), DO NOT TOUCH
    LOG_LEVEL_CONSOLE: str = "INFO"
    LOG_LEVEL_FILE: str = "DEBUG"

    # --- Database ---
    DB_CONTAINER_NAME: str = "climb"
    DB_CONTAINER_MOUNT_PATH: str = "/var/lib/postgresql/"

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
    help_flags: List[str] = field(default_factory=lambda: ["-h", "--help"])

    help_string = """
Usage:
  python main.py [OPTIONS]

Description:
  TODO

Options:
-c, --compress					// TODO: COMMENT
-spc, --showPostgresCommand     // TODO: COMMENT
-h, --help						Show this help message and exit

Examples:
  // TODO
"""