# config.py
from pathlib import Path

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
LOG_DIR  = BASE_DIR / "logs"
DOCS_DIR = BASE_DIR / "docs"

DB_PATH          = str(DATA_DIR / "facelock.db")
KEY_PATH         = str(DATA_DIR / "facelock.key")
SETTINGS_PATH    = str(DATA_DIR / "settings.json")
TFLITE_MODEL_PATH = str(DATA_DIR / "face_detector.tflite")
LOG_PATH         = str(LOG_DIR  / "activity.log")
DPIA_PATH        = str(DOCS_DIR / "DPIA.md")

PIPE_NAME         = r"\\.\pipe\facelock_core"
PIPE_AUTHKEY_PATH = str(DATA_DIR / "pipe.key")

DEFAULT_TOLERANCE            = 0.5
CONSECUTIVE_FRAMES_REQUIRED  = 3
ENROLLMENT_FRAMES            = 20
ENROLLMENT_CAPTURE_INTERVAL  = 0.4   # seconds between accepted captures
AUTO_LOCK_TIMEOUT_SECONDS    = 60

FALLBACK_PIN     = "pin"
FALLBACK_WINDOWS = "windows"
FALLBACK_NONE    = "none"
DEFAULT_FALLBACK = FALLBACK_NONE

LOG_MAX_BYTES    = 1_000_000
LOG_BACKUP_COUNT = 3
CONSENT_VERSION  = "1.0"
APP_VERSION      = "0.1.0"
