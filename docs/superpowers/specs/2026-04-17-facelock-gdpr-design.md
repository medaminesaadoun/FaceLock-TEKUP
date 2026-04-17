# FaceLock — GDPR-Compliant Face Authentication App for Windows
**Date:** 2026-04-17  
**Status:** Approved  
**Platform:** Windows (Python)  
**C2 Roadmap Note:** A native Windows Credential Provider (C++ COM DLL) is a planned future extension. All current Python architecture is designed with a named pipe integration point to support this.

---

## 1. Purpose

FaceLock is a local, GDPR-compliant facial recognition authentication application for Windows. It authenticates Windows users via facial recognition with strictly on-device processing — no biometric data ever leaves the machine.

**Core constraints:**
- 100% local processing — no cloud calls, no external APIs
- GDPR Article 9 compliant — biometric data is special category data
- ISO 27001 / 27018 / 27701 / 29100 aligned
- Multi-user: each Windows user account has isolated enrollment and data

---

## 2. Architecture

### 2.1 Overview

Two-process architecture. The core service owns the camera and recognition engine and exposes a named pipe. All mode clients are thin consumers of that pipe.

```
┌─────────────────────────────────────────────────┐
│  CORE SERVICE (background, Task Scheduler)       │
│  - Owns camera + MediaPipe detector              │
│  - face_recognition embeddings + matching        │
│  - AES-256-GCM encrypted SQLite storage          │
│  - GDPR layer (consent, audit, erasure)          │
│  - Named pipe server  \\.\pipe\facelock_core     │
└──────────────────────┬──────────────────────────┘
                       │ IPC (named pipe)
          ┌────────────┼────────────┐
          │            │            │
   ┌──────▼──────┐ ┌───▼──────┐ ┌──▼─────────┐
   │ Session     │ │ Startup  │ │ App Guard  │
   │ Locker (A)  │ │ Gate(C1) │ │    (B)     │
   │ pywin32     │ │fullscreen│ │ wraps app  │
   │ WTS events  │ │ overlay  │ │ launch     │
   └─────────────┘ └──────────┘ └────────────┘
                    System tray (control all modes)
```

### 2.2 Project Layout

```
FaceLock/
├── main.py                  # Entry point (--service / --mode flags)
├── core_service.py          # Core process: named pipe server + engine
├── config.py                # Configuration settings
├── requirements.txt
├── README.md
│
├── modules/
│   ├── __init__.py
│   ├── camera_handler.py    # Webcam capture (existing)
│   ├── face_detector.py     # MediaPipe presence check (existing)
│   ├── face_encoder.py      # 128-d embeddings + matching
│   ├── authenticator.py     # Auth logic + named pipe client
│   ├── database.py          # SQLite + AES-256-GCM encrypted storage
│   ├── system_controller.py # Modes A, B, C1 + session monitoring
│   ├── encryption.py        # DPAPI key wrapping + AES-256-GCM
│   ├── gdpr.py              # Consent, audit log, erasure
│   └── ipc.py               # Named pipe server/client wrapper
│
├── ui/
│   ├── __init__.py
│   ├── enrollment_window.py # Enrollment wizard (consent → capture → confirm)
│   ├── settings_window.py   # Settings + GDPR controls
│   └── status_indicator.py  # System tray icon
│
├── data/
│   ├── facelock.db          # SQLite database (AES-256-GCM encrypted embeddings)
│   ├── facelock.key         # Fernet key wrapped with Windows DPAPI
│   └── face_detector.tflite # MediaPipe model (existing)
│
├── logs/
│   └── activity.log         # Auth events only — no biometric data
│
├── docs/
│   └── DPIA.md              # Data Protection Impact Assessment (auto-generated at enrollment)
│
└── tests/
    ├── test_detection.py
    ├── test_encoding.py
    └── test_authentication.py
```

---

## 3. Core Biometric Engine

### 3.1 Two-Stage Pipeline

Detection (fast) gates recognition (expensive) to keep CPU usage low during idle monitoring.

**Stage 1 — MediaPipe face detector** (`face_detector.py`):
- Runs every frame at ~30fps
- BlazeFace Full Range TFLite model
- Returns bounding box and presence boolean
- Frames with no face are discarded before reaching Stage 2

**Stage 2 — face_recognition encoder** (`face_encoder.py`):
- Runs only when MediaPipe confirms face presence (~3-5fps on CPU)
- Extracts 128-d float embedding via `face_recognition.face_encodings()`
- Compares against stored embedding using `face_recognition.face_distance()`
- Auth passes when distance stays below configured threshold for 3 consecutive frames; counter resets on any single failed frame

### 3.2 Enrollment Flow

1. MediaPipe confirms face is present and centered in frame
2. Capture 5 frames, extract 128-d embedding from each
3. Average the 5 embeddings into one representative vector
4. Encrypt with AES-256-GCM and store in `facelock.db`
5. Raw frames discarded immediately after encoding — never written to disk
6. Enrollment fails if any frame contains zero faces or multiple faces

### 3.3 Recognition Flow

1. MediaPipe detects face presence (Stage 1)
2. Extract 128-d embedding from confirmed frame (Stage 2)
3. Load encrypted embedding from DB, decrypt in memory
4. Compute `face_recognition.face_distance()` — lower = more similar
5. Auth passes when distance < threshold for 3 consecutive frames (counter resets on any single failed frame)
6. Embedding zeroed from memory immediately after comparison

**Default threshold:** 0.5 (configurable per user in `config.py`)

---

## 4. GDPR Compliance

### 4.1 GDPR Articles

| Article | Requirement | Implementation |
|---|---|---|
| Art. 5(1)(c) | Data minimization | 128-d embedding only — raw frames never persisted |
| Art. 5(1)(e) | Storage limitation | Configurable retention policy; auto-delete after N days inactivity |
| Art. 5(1)(f) | Integrity & confidentiality | AES-256-GCM authenticated encryption; HMAC on audit log |
| Art. 7 | Explicit consent | Consent screen shown before any camera access at enrollment |
| Art. 17 | Right to erasure | `gdpr.erase_user_data()` — secure-wipes embedding, consent, audit in one transaction |
| Art. 32 | Security of processing | AES-256-GCM, DPAPI key binding, memory-cleared embeddings post-use |
| Art. 35 | DPIA | Generated at first enrollment, saved to `docs/DPIA.md` |

### 4.2 ISO Standards

| Standard | Requirement | Implementation |
|---|---|---|
| ISO 27001 | Access control | Local auth only; DPAPI binds key to Windows user account; no network socket |
| ISO 27001 | Cryptography | AES-256-GCM via `cryptography.hazmat.primitives.ciphers.aead.AESGCM` |
| ISO 27018 | No cloud storage | No outbound connections; verified at startup |
| ISO 27701 | PII minimization | Embeddings only stored; documented in DPIA |
| ISO 29100 | Privacy by design | Consent-first enrollment; encryption mandatory, not optional |

### 4.3 GDPR Mechanisms

**Consent (`gdpr.py`):**
- Enrollment wizard shows plain-language consent screen before any camera access
- Consent record written to `users` table: windows_username, timestamp, app_version, purpose
- No consent = enrollment blocked entirely

**Right to Erasure (`gdpr.py`):**
- `gdpr.erase_user_data(username)` deletes embedding row, consent record, and audit entries in one atomic SQLite transaction
- File-level: `facelock.key` overwritten with zeros before deletion (secure wipe)
- Accessible via "Delete my data" button in `settings_window.py`

**Audit Log (`activity.log`):**
- Records: timestamp, windows_username, result (pass/fail), mode
- No biometric data, no embeddings, no images in logs
- Log rotation: 1MB max per file, 3 files retained

**Retention Policy:**
- Configurable in `config.py`: auto-delete embeddings after N days of inactivity
- Default: no auto-delete (user must manually erase)

---

## 5. Encrypted Storage

### 5.1 Encryption Stack

```
256-bit key  ←  os.urandom(32)
     │
     ▼
DPAPI wrap   ←  CryptProtectData (pywin32) — bound to Windows user account
     │
     ▼
data/facelock.key  (DPAPI-wrapped AES-256-GCM key)

At write time:
  nonce = os.urandom(12)
  ciphertext = AESGCM(key).encrypt(nonce, embedding_bytes, associated_data)
  stored as: nonce + ciphertext in embeddings column
```

**Why DPAPI:** Binds the encryption key to the current Windows user account. Even with physical access to `facelock.db` and `facelock.key`, data cannot be decrypted without authenticating as that Windows user.

### 5.2 Database Schema (`facelock.db`)

```sql
CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    windows_username TEXT UNIQUE NOT NULL,
    consent_timestamp TEXT NOT NULL,
    consent_version TEXT NOT NULL,
    fallback_method TEXT NOT NULL  -- 'pin' | 'windows' | 'none' (overrides config.py default)
);

CREATE TABLE embeddings (
    id INTEGER PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    encrypted_embedding BLOB NOT NULL,  -- nonce + AES-256-GCM ciphertext
    created_at TEXT NOT NULL,
    last_used_at TEXT
);

CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY,
    timestamp TEXT NOT NULL,
    windows_username TEXT NOT NULL,
    result TEXT NOT NULL,  -- 'pass' | 'fail'
    mode TEXT NOT NULL     -- 'session_lock' | 'startup' | 'app_guard'
);
```

### 5.3 Memory Safety

- Embeddings held in `bytearray` after decryption
- Zero-filled immediately after comparison: `ctypes.memset(id(buf), 0, len(buf))`
- No embedding data written to `activity.log`

---

## 6. Mode Integrations (`system_controller.py`)

### Mode A — Session Locker

- `WTSRegisterSessionNotification` (pywin32) listens for `WTS_SESSION_LOCK` / `WTS_SESSION_UNLOCK`
- On lock: starts face scanning loop, shows lock overlay
- On 3 consecutive successful frames: dismisses overlay, unlocks session
- On no face detected for configurable timeout (default 60s): calls `LockWorkStation()`

### Mode C1 — Startup Gate

- Registered via Task Scheduler at `ONLOGON` trigger
- Fullscreen always-on-top tkinter overlay shown immediately on login
- Blocks all input until face recognized or fallback used
- On success: overlay destroyed, normal desktop access restored

### Mode B — App Guard

- Invoked as: `python main.py --guard "path/to/app.exe"`
- Shows auth overlay before launching target app
- On success: spawns target as subprocess
- On 3 failed attempts: exits without launching target

### Fallback Handling (all modes)

| Method | Implementation |
|---|---|
| `pin` | bcrypt hash stored in `users` table; prompt shown after 3 failed face attempts |
| `windows` | `CredUIPromptForWindowsCredentials` via pywin32 |
| `none` | Overlay remains locked indefinitely |

Fallback method configured per user in `config.py`.

---

## 7. UI Components

All UI built with tkinter (built-in, no extra dependency).

### `enrollment_window.py` — Enrollment Wizard

**Step 1 — Consent:** Plain-language explanation of what is collected, why, and data rights. "I Agree" required to proceed.  
**Step 2 — Capture:** Live camera feed with face alignment guide. Captures 5 frames automatically when face is centered and stable.  
**Step 3 — Confirm:** Shows what was stored (embedding only), displays DPIA summary, data rights reminder.

### `settings_window.py` — Settings + GDPR Controls

- Active modes toggle (A / B / C1)
- Recognition tolerance slider (0.4–0.6)
- Fallback method selector
- Retention policy (auto-delete after N days)
- "Delete my data" button with confirmation dialog
- Re-enroll button

### Lock Overlay (inside `status_indicator.py`)

- Fullscreen, always-on-top, no taskbar entry
- Live camera feed showing detector view
- Status messages: "Scanning...", "Not recognized", "Unlocked"
- Fallback button appears after 3 failed attempts

### `status_indicator.py` — System Tray

- Icon reflects current state: monitoring / locked / idle / error
- Right-click menu: Open Settings, Enroll, Pause, Exit

---

## 8. IPC Protocol (`ipc.py`)

Named pipe: `\\.\pipe\facelock_core`  
Transport: `multiprocessing.connection` (Python built-in)

**Commands:**

```python
# Recognize current camera frame
{"command": "recognize"} → {"result": True, "distance": 0.38}

# Enroll current user
{"command": "enroll"} → {"result": True} | {"result": False, "error": "multiple_faces"}

# Erase user data
{"command": "erase", "username": "DELL"} → {"result": True}

# Health check
{"command": "ping"} → {"result": "pong"}
```

Mode clients retry 3 times with 1s delay if service is not running, then fall back to configured fallback method.

---

## 9. Error Handling

| Scenario | Behavior |
|---|---|
| Camera not found / disconnected | Service logs error, tray icon turns red, modes trigger fallback |
| Core service not running | Clients retry 3x/1s, then show fallback prompt |
| Corrupt / tampered database | SQLite `PRAGMA integrity_check` runs at startup; failure → force re-enrollment, log security event |
| DPAPI decryption fails | Auth blocked, error logged — no silent fallback |
| Recognition timeout (>10s no face) | Falls back to configured fallback |
| Enrollment with multiple faces | Enrollment rejected — exactly one face required |

---

## 10. Testing

All TC1–TC8 are live integration tests requiring a physical webcam.

| TC | Test File | Description | Expected Result |
|---|---|---|---|
| TC1 | `test_encoding.py` | Enroll new user with 5 live frames | Embedding stored in DB; no images saved; verify with hex editor |
| TC2 | `test_authentication.py` | Authorized user in good light | Session unlocks (distance < threshold for 3 frames) |
| TC3 | `test_authentication.py` | Different (unknown) person | Session remains locked |
| TC4 | `test_authentication.py` | No face for 60 seconds | Auto-lock triggered |
| TC5 | `test_detection.py` | Authorized user in dim / bright lighting | Face detected and recognized |
| TC6 | `test_encoding.py` | Enroll without glasses, test with glasses (and vice versa) | Still recognized |
| TC7 | `test_authentication.py` | Two enrolled users, switch between them | Each unlocks only for themselves |
| TC8 | `test_encoding.py` | Open `facelock.db` in hex editor after enrollment | Embedding column is unreadable ciphertext |

---

## 11. Future Roadmap

**C2 — Native Windows Credential Provider:**
- C++ COM DLL implementing `ICredentialProvider` / `ICredentialProviderCredential`
- Registered under `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\Credential Providers`
- Communicates with the Python core service via the existing `\\.\pipe\facelock_core` named pipe
- Enables face recognition at the actual Windows login / lock screen prompt
- Requires: C++ / Windows SDK knowledge, code signing, VM for safe testing
