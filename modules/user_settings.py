# modules/user_settings.py
import json
import os

_DEFAULTS: dict = {
    "tolerance": 0.5,
    "absence_threshold": 5,
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
