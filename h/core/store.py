"""
Einstellungen dauerhaft speichern.

Reihenfolge beim Laden:
1. settings.json (liegt in DATA_DIR, falls gesetzt -- z.B. /data auf einer Render-Disk)
2. Umgebungsvariable SETTINGS_JSON (Startwerte, ueberleben jeden Neustart)
"""
import json
import os
import threading
from pathlib import Path

FILE = Path(os.environ.get("DATA_DIR") or Path(__file__).resolve().parent.parent) / "settings.json"
_lock = threading.Lock()


def load() -> dict:
    if FILE.exists():
        return json.loads(FILE.read_text("utf-8"))
    seed = os.environ.get("SETTINGS_JSON", "").strip()
    if seed:
        try:
            return json.loads(seed)
        except json.JSONDecodeError:
            print("[WARNUNG] SETTINGS_JSON ist kein gueltiges JSON und wird ignoriert")
    return {}


def save_module(module_id: str, values: dict) -> None:
    with _lock:
        data = load()
        data[module_id] = values
        FILE.parent.mkdir(parents=True, exist_ok=True)
        FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
