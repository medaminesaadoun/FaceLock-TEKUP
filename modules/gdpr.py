# modules/gdpr.py
import os
import logging
from datetime import datetime
from logging.handlers import RotatingFileHandler

import config
from modules.database import erase_user, add_user, get_user, get_connection
from modules.encryption import secure_wipe

CONSENT_TEXT = """\
FaceLock Data Collection Notice
================================
FaceLock will store a 128-dimensional mathematical vector
derived from your face to authenticate you on this device.

What IS stored:
  - A numerical face embedding (128 floats)

What is NOT stored:
  - Photographs or video of your face
  - Any data sent outside this device

Your GDPR rights:
  - Delete your data at any time: Settings > Delete My Data
  - Re-enroll at any time

Data is encrypted with AES-256-GCM and is accessible only
to your Windows user account via Windows DPAPI.
"""


def get_consent_text() -> str:
    return CONSENT_TEXT


def record_consent(db_path: str, username: str, fallback: str,
                   pin_hash: str | None = None) -> None:
    add_user(
        db_path,
        username,
        datetime.utcnow().isoformat(),
        config.CONSENT_VERSION,
        fallback,
        pin_hash,
    )


def has_consent(db_path: str, username: str) -> bool:
    return get_user(db_path, username) is not None


def erase_user_data(db_path: str, key_path: str, username: str) -> None:
    erase_user(db_path, username)
    # Only destroy the shared key when no enrolled users remain — wiping it
    # earlier would permanently lock out other users on this device.
    with get_connection(db_path) as conn:
        remaining = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if remaining == 0 and os.path.exists(key_path):
        secure_wipe(key_path)


def generate_dpia(dpia_path: str, username: str) -> None:
    os.makedirs(os.path.dirname(dpia_path), exist_ok=True)
    content = f"""# Data Protection Impact Assessment (DPIA)

Generated: {datetime.utcnow().isoformat()}
User: {username}
App Version: {config.APP_VERSION}

## 1. Data Processed
- Type: Biometric face embedding (128-dimensional float64 vector)
- Purpose: Local device authentication
- Legal basis: Explicit consent (GDPR Art. 7)

## 2. Data Minimization (GDPR Art. 5(1)(c))
- Raw images: NOT stored
- Video: NOT stored
- Stored: Averaged embedding from {config.ENROLLMENT_FRAMES} enrollment frames only

## 3. Storage Limitation (GDPR Art. 5(1)(e))
- Data retained until user requests erasure
- Configurable auto-delete after inactivity (Settings)

## 4. Protection Measures (GDPR Art. 5(1)(f), Art. 32)
- Encryption: AES-256-GCM (ISO 27001)
- Key storage: Windows DPAPI — bound to this Windows user account
- Transmission: None — 100% local processing (ISO 27018)

## 5. Risk Assessment
- Unauthorised access: Mitigated by DPAPI binding + AES-256-GCM
- Data breach: Face embeddings are not reversible to images
- Residual risk: Low

## 6. Data Subject Rights (GDPR Art. 17)
- Right to erasure: Settings > Delete My Data
- Right to access: Data is local; user owns the device
- Right to portability: Not applicable (local auth only)

## 7. ISO Alignment
| Standard   | Requirement          | Status     |
|------------|----------------------|------------|
| ISO 27001  | Access control       | Implemented|
| ISO 27001  | AES-256 cryptography | Implemented|
| ISO 27018  | No cloud storage     | Implemented|
| ISO 27701  | PII minimization     | Implemented|
| ISO 29100  | Privacy by design    | Implemented|
"""
    with open(dpia_path, "w", encoding="utf-8") as f:
        f.write(content)


def setup_audit_logger(log_path: str) -> logging.Logger:
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    logger = logging.getLogger("facelock.audit")
    if not logger.handlers:
        handler = RotatingFileHandler(
            log_path,
            maxBytes=config.LOG_MAX_BYTES,
            backupCount=config.LOG_BACKUP_COUNT,
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger
