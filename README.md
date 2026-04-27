# FaceLock — Facial Authentication for Windows

> GDPR-compliant, presence-based facial authentication that locks your workstation when you walk away and unlocks it when you return — no password, no button press.

---

## Overview

FaceLock watches your webcam continuously. When it detects that the enrolled user has left, it shows a lock overlay. When the user returns and looks at the camera, it recognises them and unlocks automatically. Everything runs **100% locally** — no cloud, no external servers, no internet required after installation.

---

## Features

### Core
- **Automatic presence monitoring** — locks after N consecutive seconds without a detected face (configurable: 3–30 s)
- **Face authentication** — 3 consecutive frame matches required to unlock (anti-spoofing streak)
- **Multi-user enrollment** — multiple faces can be enrolled per Windows account; any enrolled face can unlock
- **Unlock grace period** — configurable cooldown after unlock prevents immediate re-lock
- **Windows lock fallback** — calls `LockWorkStation()` if face auth times out (configurable: 30–300 s)

### Security
- **AES-256-GCM encryption** — face embeddings encrypted at rest with a random nonce per operation
- **Windows DPAPI key binding** — encryption key bound to the Windows user account; unreadable on any other machine or account
- **Bcrypt PIN hashing** — PIN fallback stored as bcrypt hash, never plaintext
- **OS-level keyboard interception** — `WH_KEYBOARD_LL` hook blocks Alt+Tab and Win key while overlay is active
- **Low-level overlay hardening** — `overrideredirect`, shortcut swallowing, periodic re-raise

### Privacy & GDPR
- **Data minimisation** — only a 128-float mathematical vector is stored, never images or video
- **Explicit consent** — GDPR notice shown before any data collection; system refuses to operate without recorded consent
- **Right to erasure** — Settings → Delete My Data wipes all records; encryption key securely overwritten if no other users remain
- **Audit log** — every authentication attempt logged with timestamp and result (rotating, 1 MB × 3 backups)
- **DPIA export** — Data Protection Impact Assessment generated on demand from Settings

### UI
- **System tray icon** — shows Active / Locked / Paused state; left-click opens dashboard
- **Dashboard** — live status, enrolled faces list, today's auth stats, recent events, camera status, active preset
- **Lock overlay** — standard mode (FaceLock branding + animated dots + PIN entry) or **hidden mode** (mimics Windows lock screen with real wallpaper, live clock, and PIN field)
- **Enrollment wizard** — 3-step: consent → fallback method → live capture (30 frames, ~18 s)
- **Settings** — Simple mode (3 presets) or Advanced mode (4 individual sliders); all changes hot-reload
- **Debug view** — live annotated camera feed showing detection boxes, distance score, and matched face name
- **GUI test runner** — 1280×720 interactive test runner with live camera feed and 128-d embedding visualisation

---

## Architecture

FaceLock runs as **three cooperating processes** communicating through a Windows named pipe:

```
┌─────────────────────┐   Named Pipe   ┌──────────────────────┐
│    Core Service     │◄──────────────►│  Session Controller  │
│                     │                │                      │
│  • Owns the camera  │◄──────────────►│  • Polls presence    │
│  • Face detection   │                │  • Triggers lock     │
│  • Face encoding    │                │  • Fallback to       │
│  • IPC pipe server  │                │    LockWorkStation() │
│  • Lock/pause state │                └──────────────────────┘
└─────────────────────┘
           ▲
           │ Named Pipe
           ▼
┌─────────────────────┐
│    Tray Process     │
│                     │
│  • System tray icon │
│  • Lock overlay     │
│  • Dashboard        │
│  • Settings         │
│  • Enrollment       │
└─────────────────────┘
```

A **Windows Job Object** (`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`) ensures all child processes are killed automatically if the launcher crashes — even on `abort()`.

---

## Face Recognition Pipeline

```
Camera frame (OpenCV)
       │
       ▼  downscaled 50% for speed
MediaPipe BlazeFace → bounding box (x, y, w, h)
       │
       ▼
dlib ResNet (via face_recognition)
       │
       ▼
128-dimensional float64 embedding
       │
       ▼
Euclidean distance ‖stored − live‖₂ ≤ tolerance
       │
3 consecutive matches → Access granted
```

**Enrollment:** 30 frames captured at ≥ 0.6 s intervals, averaged into one stored template, encrypted and saved.

---

## Requirements

- Windows 10 / 11
- Python 3.12+
- A standard USB or built-in webcam
- Internet connection (first install only, for pip packages)
- Microsoft C++ Build Tools (required by `dlib`) — [download here](https://visualstudio.microsoft.com/visual-cpp-build-tools/), select **Desktop development with C++**

---

## Installation

### One-click (recommended)

1. Clone or download the repository
2. Double-click **`FaceLock-Setup.bat`**
3. Choose **Install** from the menu

The installer will:
- Detect or install Python 3.12+ via winget
- Copy project files to `%LOCALAPPDATA%\FaceLock`
- Create a virtual environment and install all dependencies
- Register `FaceLock-CoreService` and `FaceLock-ModeA` scheduled tasks (auto-start on login)
- Create a Desktop shortcut

### Manual

```powershell
# 1. Clone
git clone https://github.com/medaminesaadoun/FaceLock-TEKUP.git
cd FaceLock-TEKUP

# 2. Create virtual environment
python -m venv facelock_env
facelock_env\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run
python main.py
```

---

## Usage

### First launch

On first launch, the enrollment wizard opens automatically:

1. **Consent** — read and accept the GDPR data collection notice
2. **Fallback method** — choose PIN (with confirm field) or Windows credentials
3. **Capture** — look at the camera for ~18 seconds while 30 frames are captured

Once enrolled, FaceLock monitors your presence and manages the lock automatically.

### Manual controls

| Action | How |
|---|---|
| Open dashboard | Left-click the tray icon |
| Pause / Resume monitoring | Tray menu → Pause, or Dashboard → Pause |
| Add another face | Dashboard → Add User |
| Re-enroll | Dashboard → Re-enroll |
| Manage enrolled faces | Dashboard → Enrolled Faces card (Rename / Delete) |
| Settings | Tray menu → Settings, or Dashboard → Settings |
| Debug view | Tray menu → Debug View |

### Command-line interface

```bash
python main.py              # launch (core service + tray + enrollment if needed)
python main.py service      # start core service only
python main.py mode-a       # start session locker only
python main.py enroll       # open enrollment wizard
python main.py tray         # start tray only
python main.py debug        # open debug view
python main.py test-runner  # open GUI test runner
python main.py install      # register scheduled tasks
python main.py uninstall    # remove scheduled tasks
```

---

## Configuration

Open **Settings** from the tray or dashboard.

### Simple mode — Security Presets

| Preset | Lock timeout | Grace period | Auth fallback | Tolerance |
|---|---|---|---|---|
| Max Security | 3 s | 5 s | 30 s | 0.40 |
| **Balanced** (default) | 5 s | 10 s | 60 s | 0.50 |
| Relaxed | 15 s | 30 s | 120 s | 0.60 |

### Advanced mode

| Setting | Range | Description |
|---|---|---|
| Tolerance | 0.30 – 0.70 | Max Euclidean distance for a face match. Lower = stricter. |
| Lock timeout | 3 – 30 s | Consecutive seconds without a face before locking. |
| Unlock grace period | 0 – 60 s | Cooldown after unlock before monitoring resumes. |
| Auth fallback timeout | 30 – 300 s | How long the overlay tries before calling LockWorkStation(). |

### Hidden mode

Enable in Settings → Lock Overlay. Requires a PIN fallback to be enrolled.

When active, the lock overlay disguises itself as the Windows lock screen — showing the real system wallpaper (blurred), a live clock, and a PIN entry field. No FaceLock branding is visible.

---

## Security & Privacy

### What is stored

| Data | Stored | Format |
|---|---|---|
| Face images / video | ❌ Never | — |
| Face embedding | ✅ Yes | 128 float64 values (AES-256-GCM encrypted) |
| PIN fallback | ✅ Yes | bcrypt hash only |
| Auth events | ✅ Yes | Timestamp + pass/fail (audit log) |
| Consent record | ✅ Yes | Timestamp + version |

### Encryption

- **Algorithm:** AES-256-GCM (NIST SP 800-38D)
- **Key size:** 256 bits, generated with `os.urandom(32)`
- **Nonce:** 96-bit random, unique per encryption operation
- **Key protection:** Windows DPAPI (`CryptProtectData`) — key is bound to the Windows user account and unreadable by other accounts or on other machines

### Known limitations

| Limitation | Severity | Note |
|---|---|---|
| No liveness detection | Medium | A high-quality photo or video could potentially spoof the system |
| User-mode overlay bypass | Medium | Task Manager can kill the process; a Windows Credential Provider DLL would be required for OS-level security |
| CPU-intensive encoding | Low | dlib ResNet takes ~100 ms per frame on CPU; GPU acceleration not implemented |

---

## GDPR Compliance

| Article | Requirement | Implementation |
|---|---|---|
| Art. 7 | Explicit consent | Consent notice before any data collection; stored with timestamp |
| Art. 9 | Special category data | Biometric processing blocked without consent record |
| Art. 17 | Right to erasure | Settings → Delete My Data; encryption key destroyed if last user |
| Art. 5(1)(c) | Data minimisation | 128-float vector only; no raw images stored |
| Art. 5(1)(e) | Storage limitation | Data kept only while enrolled; immediate erasure on request |
| Art. 32 | Security | AES-256-GCM, DPAPI, bcrypt, audit log, local processing only |

A **DPIA (Data Protection Impact Assessment)** document can be generated at any time from Settings → View / Export DPIA.

---

## Testing

### Automated (pytest)

```bash
# Unit tests only — no camera required
pytest -m "not camera"

# All tests — sit in front of the webcam
pytest
```

### GUI Test Runner

```bash
python main.py test-runner
```

Runs all 8 test cases (TC1–TC8) interactively with:
- Live camera feed alongside test execution
- Per-test checkboxes (run selected tests only)
- 128-dimensional embedding visualised as a live bar chart

| TC | Description | Type |
|---|---|---|
| TC1 | Embedding is 128-dimensional | Camera |
| TC2 | Auth passes on consecutive matches | Camera |
| TC3 | Streak resets on no face | Camera |
| TC4 | Wrong embedding rejects auth | Unit |
| TC5 | Face detected in live frame | Camera |
| TC6 | Embedding serialisation roundtrip | Camera |
| TC7 | Auth works after serialisation | Camera |
| TC8 | Same face matches within tolerance | Camera |

---

## Project Structure

```
FaceLock/
├── main.py                  # CLI entry point + process launcher
├── core_service.py          # Camera owner + IPC pipe server
├── debug_view.py            # Live annotated camera debug view
├── test_runner.py           # GUI test runner
├── FaceLock-Setup.bat       # One-click installer / uninstaller
├── requirements.txt
├── pytest.ini
│
├── modules/
│   ├── face_detector.py     # MediaPipe BlazeFace detection
│   ├── face_encoder.py      # dlib 128-d embedding extraction
│   ├── authenticator.py     # Consecutive-frame match counter
│   ├── encryption.py        # AES-256-GCM + DPAPI key management
│   ├── database.py          # SQLite CRUD (users, embeddings, audit_log)
│   ├── ipc.py               # Named-pipe server/client
│   ├── system_controller.py # Session locker (presence → lock → auth)
│   ├── gdpr.py              # Consent, erasure, DPIA generation
│   ├── user_settings.py     # JSON settings + preset profiles
│   └── notifications.py     # Windows toast notifications
│
├── ui/
│   ├── status_indicator.py  # Tray icon + lock overlay
│   ├── dashboard.py         # Dashboard window
│   ├── enrollment_window.py # Enrollment wizard
│   ├── settings_window.py   # Settings window
│   └── _theme.py            # Shared theme utilities
│
├── data/
│   └── face_detector.tflite # MediaPipe BlazeFace model
│
└── tests/
    ├── test_encoding.py     # Encryption + database unit tests
    ├── test_detection.py    # Face detection camera tests
    └── test_authentication.py # Authentication camera tests
```

---

## Tech Stack

| Component | Technology |
|---|---|
| Language | Python 3.12 |
| Face Detection | MediaPipe BlazeFace (TFLite) |
| Face Encoding | dlib ResNet via face_recognition |
| Computer Vision | OpenCV |
| Encryption | AES-256-GCM (cryptography library) |
| Key Protection | Windows DPAPI (pywin32) |
| Database | SQLite (stdlib) |
| UI | tkinter + pystray |
| Notifications | winotify |
| IPC | multiprocessing.connection (named pipe) |
| Process Lifecycle | Windows Job Objects (win32job) |
| PIN hashing | bcrypt |
| Image processing | Pillow |

---

## Uninstallation

Double-click **`FaceLock-Setup.bat`** and choose **Uninstall**. You will be prompted to:
1. Remove scheduled tasks and Desktop shortcut
2. Optionally delete `%LOCALAPPDATA%\FaceLock` including all face data (requires two confirmations)

---

## License

This project is for educational and research purposes.
