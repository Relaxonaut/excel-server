"""
Excel-Server: Excel ruft per WEBDIENST()-Formel diesen Server auf.

Start:  uvicorn main:app --host 0.0.0.0 --port 8000 --no-access-log
UI:     http://<server>:8000/
"""
import importlib
import pkgutil
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

from f import modules
from f.core import store
from f.core.base import Module, coerce

# Alles in modules/ automatisch laden -> Module registrieren sich selbst
for info in pkgutil.iter_modules(modules.__path__):
    try:
        importlib.import_module(f"modules.{info.name}")
    except Exception as e:
        print(f"[WARNUNG] Modul '{info.name}' nicht geladen: {e}")
for mod in Module.registry.values():
    mod.discover_outputs()

app = FastAPI(title="Excel-Server")
CACHE: dict = {}   # gleiche Anfrage = gleiche Antwort (Excel fragt pro Zelle einzeln)


@app.get("/", response_class=HTMLResponse)
def ui():
    return (Path(__file__).parent / "ui.html").read_text("utf-8")


@app.get("/api/modules")
def list_modules():
    saved = store.load()
    return [
        {
            "id": m.id,
            "name": m.name,
            "description": m.description,
            "inputs": [asdict(f) for f in m.inputs],
            "settings": [asdict(f) for f in m.settings],
            "outputs": [asdict(o) for o in m.outputs],
            "values": {f.name: saved.get(m.id, {}).get(f.name, f.default) for f in m.settings},
        }
        for m in Module.registry.values()
    ]


@app.put("/api/settings/{module_id}")
async def save_settings(module_id: str, request: Request):
    mod = Module.registry.get(module_id)
    if mod is None:
        raise HTTPException(404, "Modul unbekannt")
    body = await request.json()
    try:
        clean = {f.name: coerce(f, body.get(f.name)) for f in mod.settings}
    except ValueError as e:
        raise HTTPException(400, str(e))
    store.save_module(module_id, clean)
    CACHE.clear()
    return {"ok": True}


@app.get("/calc/{module_id}/{output}", response_class=PlainTextResponse)
def calc(module_id: str, output: str, request: Request):
    mod = Module.registry.get(module_id)
    if mod is None:
        return f"FEHLER: Modul '{module_id}' unbekannt"

    params = dict(request.query_params)
    key = (module_id, tuple(sorted(params.items())))
    if key not in CACHE:
        try:
            result = mod.run(params, store.load().get(module_id, {}))
        except Exception as e:
            return f"FEHLER: {e}"
        if len(CACHE) > 5000:
            CACHE.clear()
        CACHE[key] = result
    result = CACHE[key]

    if output == "_alle":   # zum Debuggen im Browser
        return "\n".join(f"{k} = {v}" for k, v in result.items())
    if output not in result:
        return f"FEHLER: Ausgabe '{output}' gibt es nicht (vorhanden: {', '.join(result)})"
    return str(result[output])
