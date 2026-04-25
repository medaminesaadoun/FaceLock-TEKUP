# modules/user_settings.py
import json
import os

# Preset profiles — shared by settings_window and dashboard.
PRESETS: dict[str, dict] = {
    "Max Security": {
        "tolerance": 0.40,
        "lock_timeout": 3,
        "unlock_grace": 5,
        "auth_fallback_timeout": 30,
    },
    "Balanced": {
        "tolerance": 0.50,
        "lock_timeout": 5,
        "unlock_grace": 10,
        "auth_fallback_timeout": 60,
    },
    "Relaxed": {
        "tolerance": 0.60,
        "lock_timeout": 15,
        "unlock_grace": 30,
        "auth_fallback_timeout": 120,
    },
}

_DEFAULTS: dict = {
    "tolerance": 0.5,
    "hidden_mode": False,          # when True, overlay mimics Windows lock screen
    "lock_timeout": 5,             # seconds without a face before locking
    "unlock_grace": 10,            # seconds after unlock before monitoring resumes
    "auth_fallback_timeout": 60,   # seconds of face auth before Windows lock
    "settings_mode": "simple",     # "simple" shows presets, "advanced" shows sliders
}


def load(path: str) -> dict:
    try:
        with open(path) as f:
            data = json.load(f)
        return {**_DEFAULTS, **data}
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(_DEFAULTS)


def save(path: str, settings: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(settings, f, indent=2)


def get_tolerance(path: str) -> float:
    return float(load(path).get("tolerance", _DEFAULTS["tolerance"]))


def get_active_preset(path: str) -> str:
    """Return the preset name matching current settings, or 'Custom'."""
    s = load(path)
    for name, vals in PRESETS.items():
        if (
            abs(s.get("tolerance", 0) - vals["tolerance"]) < 0.01
            and all(s.get(k) == v for k, v in vals.items() if k != "tolerance")
        ):
            return name
    return "Custom"
