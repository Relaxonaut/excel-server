"""Einstellungen dauerhaft in settings.json speichern."""
import json
import threading
from pathlib import Path

FILE = Path(__file__).resolve().parent.parent / "settings.json"
_lock = threading.Lock()


def load() -> dict:
    if FILE.exists():
        return json.loads(FILE.read_text("utf-8"))
    return {}


def save_module(module_id: str, values: dict) -> None:
    with _lock:
        data = load()
        data[module_id] = values
        FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
