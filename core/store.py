
"""In-Memory-Konfigurationsspeicher für Render Free."""

import copy
import json
import os
import threading

_lock = threading.RLock()


def _initial_data():
    seed = os.environ.get("SETTINGS_JSON", "").strip()

    if not seed:
        return {}

    try:
        data = json.loads(seed)

        if not isinstance(data, dict):
            raise ValueError("SETTINGS_JSON muss ein JSON-Objekt sein")

        return data

    except (json.JSONDecodeError, ValueError) as error:
        print(f"[WARNUNG] SETTINGS_JSON ungültig: {error}")
        return {}


_data = _initial_data()


def load() -> dict:
    with _lock:
        return copy.deepcopy(_data)


def save_module(module_id: str, values: dict) -> None:
    with _lock:
        _data[module_id] = copy.deepcopy(values)


def replace(data: dict) -> None:
    global _data

    if not isinstance(data, dict):
        raise ValueError("Konfiguration muss ein JSON-Objekt sein")

    with _lock:
        _data = copy.deepcopy(data)


def export() -> dict:
    return load()
