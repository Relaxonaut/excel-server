"""
Excel-Server.

Excel schickt per Power Query den Inhalt eines benannten Bereichs (POST, JSON),
der Server rechnet alle aktiven Module und antwortet mit einer Tabelle Name/Wert.

Lokal:   uvicorn main:app --port 8000
Render:  siehe render.yaml
"""
import hmac
import importlib
import io
import os
import pkgutil
from dataclasses import asdict
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

import modules
from core import store
from core.base import Module, cell_value, coerce

API_TOKEN = os.environ.get("API_TOKEN", "")        # Schluessel fuer Excel (Header X-Token)
UI_PASSWORD = os.environ.get("UI_PASSWORD", "")    # Passwort fuer die Weboberflaeche

if os.environ.get("RENDER") and not (API_TOKEN and UI_PASSWORD):
    raise RuntimeError("Auf Render muessen API_TOKEN und UI_PASSWORD gesetzt sein (Gehaltsdaten!).")

# Alles in modules/ automatisch laden -> Module melden sich selbst an
LOAD_ERRORS: list[dict] = []     # Module, die beim Start nicht geladen werden konnten
FOUND = [i.name for i in pkgutil.iter_modules(modules.__path__)]
for name in FOUND:
    try:
        importlib.import_module(f"modules.{name}")
    except Exception as e:
        LOAD_ERRORS.append({"modul": name, "meldung": f"{type(e).__name__}: {e}"})
        print(f"[WARNUNG] Modul '{name}' nicht geladen: {type(e).__name__}: {e}")
for mod in Module.registry.values():
    mod.discover_outputs()

app = FastAPI(title="Excel-Server")


# ---------- Zugriffsschutz ----------
_basic = HTTPBasic(auto_error=False)


def _gleich(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def ui_auth(creds: HTTPBasicCredentials | None = Depends(_basic)):
    if UI_PASSWORD and not (creds and _gleich(creds.password, UI_PASSWORD)):
        raise HTTPException(401, "Passwort noetig", headers={"WWW-Authenticate": 'Basic realm="Excel-Server"'})


def token_auth(x_token: str | None = Header(default=None)):
    if API_TOKEN and not (x_token and _gleich(x_token, API_TOKEN)):
        raise HTTPException(401, "Token falsch")


# ---------- Hilfsfunktionen ----------
def config_for(mod: Module) -> dict:
    """Gespeicherte Konfiguration + Standardwerte des Moduls."""
    saved = store.load().get(mod.id, {})
    return {
        "aktiv": saved.get("aktiv", mod.aktiv_standard),
        "zellen": {f.name: saved.get("zellen", {}).get(f.name) or f.cell for f in mod.inputs},
        "werte": {f.name: saved.get("werte", {}).get(f.name, f.default) for f in mod.settings},
    }


def jsonable(v):
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return v if v == v and abs(v) != float("inf") else str(v)
    return str(v)


def run_all(grid: list) -> list[dict]:
    """Alle aktiven Module auf das Zellraster anwenden -> Liste {Name, Wert}."""
    rows = []
    for mod in Module.registry.values():
        cfg = config_for(mod)
        if not cfg["aktiv"]:
            continue
        try:
            result = mod.run_grid(grid, cfg)
        except Exception as e:
            rows.append({"Name": f"{mod.id}._fehler", "Wert": str(e)})
            continue
        for key, value in result.items():
            rows.append({"Name": f"{mod.id}.{key}", "Wert": jsonable(value)})
    return rows


# ---------- Excel-Schnittstelle ----------
@app.post("/excel/run", dependencies=[Depends(token_auth)])
async def excel_run(request: Request):
    body = await request.json()
    grid = body.get("zellen") if isinstance(body, dict) else None
    if not isinstance(grid, list):
        raise HTTPException(400, "Erwartet JSON {\"zellen\": [[...], ...]}")
    return run_all(grid)


@app.get("/healthz", response_class=PlainTextResponse)
def healthz():
    return "ok"


# ---------- Weboberflaeche ----------
@app.get("/", response_class=HTMLResponse, dependencies=[Depends(ui_auth)])
def ui():
    return (Path(__file__).parent / "ui.html").read_text("utf-8")


@app.get("/api/info", dependencies=[Depends(ui_auth)])
def info_():
    return {"token": API_TOKEN}


@app.get("/api/status", dependencies=[Depends(ui_auth)])
def status():
    """Diagnose: welche Dateien in modules/ gefunden, welche geladen, welche fehlgeschlagen sind."""
    return {"gefunden": FOUND, "geladen": list(Module.registry), "fehler": LOAD_ERRORS}


@app.get("/api/modules", dependencies=[Depends(ui_auth)])
def list_modules():
    return [
        {
            "id": m.id,
            "name": m.name,
            "description": m.description,
            "inputs": [asdict(f) for f in m.inputs],
            "settings": [asdict(f) for f in m.settings],
            "outputs": [asdict(o) for o in m.outputs],
            "config": config_for(m),
        }
        for m in Module.registry.values()
    ]


@app.put("/api/modules/{module_id}", dependencies=[Depends(ui_auth)])
async def save_module(module_id: str, request: Request):
    mod = Module.registry.get(module_id)
    if mod is None:
        raise HTTPException(404, "Modul unbekannt")
    body = await request.json()
    try:
        werte = {f.name: coerce(f, body.get("werte", {}).get(f.name)) for f in mod.settings}
        zellen = {f.name: str(body.get("zellen", {}).get(f.name, "")).strip() for f in mod.inputs}
        for adresse in zellen.values():
            if adresse:
                cell_value([], adresse)          # wirft einen Fehler bei ungueltiger Adresse
    except ValueError as e:
        raise HTTPException(400, str(e))
    store.save_module(module_id, {"aktiv": bool(body.get("aktiv")), "zellen": zellen, "werte": werte})
    return {"ok": True}


@app.get("/api/export", dependencies=[Depends(ui_auth)])
def export_config():
    """Gesamte Konfiguration -- als Render-Umgebungsvariable SETTINGS_JSON einsetzbar."""
    return {m.id: config_for(m) for m in Module.registry.values()}


@app.post("/api/test", dependencies=[Depends(ui_auth)])
async def test_file(file: UploadFile = File(...), blatt: str = Form("")):
    """Testlauf mit einer hochgeladenen .xlsx (wird nur im Speicher gelesen, nicht gespeichert)."""
    import openpyxl
    try:
        wb = openpyxl.load_workbook(io.BytesIO(await file.read()), data_only=True)
    except Exception as e:
        raise HTTPException(400, f"Datei nicht lesbar: {e}")
    ws = wb[blatt] if blatt in wb.sheetnames else wb.worksheets[0]
    grid = [list(row) for row in ws.iter_rows(values_only=True)]
    return run_all(grid)
