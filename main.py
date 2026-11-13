
"""
Excel-Server mit beliebig vielen Berechnungsinstanzen.

Lokal:
    uvicorn main:app --reload --port 8000

Render:
    API_TOKEN und UI_PASSWORD als Environment Variablen setzen.
    SETTINGS_JSON enthält die exportierte Konfiguration.
"""

import hmac
import importlib
import io
import os
import pkgutil
import re
from dataclasses import asdict
from pathlib import Path

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

import modules
from core import store
from core.base import Module, coerce, valid_cell


API_TOKEN = os.environ.get("API_TOKEN", "")
UI_PASSWORD = os.environ.get("UI_PASSWORD", "")

if os.environ.get("RENDER") and not (API_TOKEN and UI_PASSWORD):
    raise RuntimeError(
        "Auf Render müssen API_TOKEN und UI_PASSWORD gesetzt sein."
    )


# ============================================
# MODULE LADEN
# ============================================

LOAD_ERRORS = []

FOUND = [
    item.name
    for item in pkgutil.iter_modules(modules.__path__)
]

for name in FOUND:

    try:
        importlib.import_module(f"modules.{name}")

    except Exception as error:

        LOAD_ERRORS.append({
            "modul": name,
            "meldung": f"{type(error).__name__}: {error}",
        })

        print(
            f"[WARNUNG] Modul '{name}' nicht geladen: {error}"
        )


for mod in Module.registry.values():
    mod.discover_outputs()


app = FastAPI(title="Excel-Server")


# ============================================
# AUTHENTIFIZIERUNG
# ============================================

_basic = HTTPBasic(auto_error=False)


def _gleich(a: str, b: str) -> bool:
    return hmac.compare_digest(
        a.encode(),
        b.encode(),
    )


def ui_auth(
    creds: HTTPBasicCredentials | None = Depends(_basic),
):

    if UI_PASSWORD and not (
        creds and _gleich(creds.password, UI_PASSWORD)
    ):

        raise HTTPException(
            401,
            "Passwort benötigt",
            headers={
                "WWW-Authenticate": 'Basic realm="Excel-Server"'
            },
        )


def token_auth(
    x_token: str | None = Header(default=None),
):

    if API_TOKEN and not (
        x_token and _gleich(x_token, API_TOKEN)
    ):

        raise HTTPException(401, "Token falsch")


# ============================================
# HILFSFUNKTIONEN
# ============================================

_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def config_for(mod: Module) -> dict:

    saved = store.load().get(mod.id, {})

    default_zellen = {
        field.name: field.cell
        for field in mod.inputs
    }

    # Alte Konfigurationen automatisch übernehmen.

    instances = saved.get("berechnungen")

    if instances is None:

        instances = [{
            "id": "berechnung_1",
            "name": "Berechnung 1",
            "aktiv": True,
            "zellen": {
                **default_zellen,
                **saved.get("zellen", {}),
            },
        }]

    normalized = []

    for index, instance in enumerate(instances):

        if not isinstance(instance, dict):
            continue

        instance_id = instance.get(
            "id",
            f"berechnung_{index + 1}",
        )

        if not _ID_PATTERN.fullmatch(str(instance_id)):
            instance_id = f"berechnung_{index + 1}"

        normalized.append({
            "id": str(instance_id),
            "name": instance.get(
                "name",
                f"Berechnung {index + 1}",
            ),
            "aktiv": bool(instance.get("aktiv", True)),
            "zellen": {
                **default_zellen,
                **instance.get("zellen", {}),
            },
        })

    werte = {}

    for field in mod.settings:

        werte[field.name] = saved.get(
            "werte", {}
        ).get(field.name, field.default)

    return {
        "aktiv": saved.get("aktiv", mod.aktiv_standard),
        "werte": werte,
        "berechnungen": normalized,
    }


def jsonable(value):

    if value is None or isinstance(value, (bool, int, str)):
        return value

    if isinstance(value, float):

        if value == value and abs(value) != float("inf"):
            return value

        return str(value)

    return str(value)


def run_all(grid: list) -> list[dict]:

    rows = []

    for mod in Module.registry.values():

        cfg = config_for(mod)

        if not cfg["aktiv"]:
            continue

        results = mod.run_instances(grid, cfg)

        for key, value in results.items():

            rows.append({
                "Name": f"{mod.id}.{key}",
                "Wert": jsonable(value),
            })

    return rows


def validate_config(mod: Module, body: dict) -> dict:

    if not isinstance(body, dict):
        raise ValueError("Ungültige Konfiguration")

    # Globale Settings validieren.

    raw_settings = body.get("werte", {})

    werte = {}

    for field in mod.settings:

        raw = raw_settings.get(
            field.name,
            field.default,
        )

        werte[field.name] = coerce(field, raw)

    # Berechnungsinstanzen validieren.

    instances = body.get("berechnungen", [])

    if not isinstance(instances, list):
        raise ValueError("berechnungen muss eine Liste sein")

    normalized = []
    used_ids = set()

    for index, instance in enumerate(instances):

        if not isinstance(instance, dict):
            raise ValueError("Ungültige Berechnung")

        instance_id = str(
            instance.get("id", "")
        ).strip()

        if not _ID_PATTERN.fullmatch(instance_id):
            raise ValueError(
                f"Ungültige Berechnungs-ID: {instance_id}"
            )

        if instance_id in used_ids:
            raise ValueError(
                f"Berechnungs-ID doppelt vorhanden: {instance_id}"
            )

        used_ids.add(instance_id)

        name = str(
            instance.get("name", f"Berechnung {index + 1}")
        ).strip()

        if not name:
            name = f"Berechnung {index + 1}"

        raw_cells = instance.get("zellen", {})

        if not isinstance(raw_cells, dict):
            raise ValueError("Zellzuordnungen müssen ein Objekt sein")

        zellen = {}

        for field in mod.inputs:

            address = str(
                raw_cells.get(field.name, field.cell or "")
            ).strip()

            if address and not valid_cell(address):
                raise ValueError(
                    f"{name}: Ungültige Zelladresse "
                    f"bei {field.label}: {address}"
                )

            zellen[field.name] = address

        normalized.append({
            "id": instance_id,
            "name": name,
            "aktiv": bool(instance.get("aktiv", True)),
            "zellen": zellen,
        })

    return {
        "aktiv": bool(body.get("aktiv", True)),
        "werte": werte,
        "berechnungen": normalized,
    }


# ============================================
# EXCEL API
# ============================================

@app.post(
    "/excel/run",
    dependencies=[Depends(token_auth)],
)
async def excel_run(request: Request):

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Ungültiges JSON")

    grid = body.get("zellen") if isinstance(body, dict) else None

    if not isinstance(grid, list):
        raise HTTPException(
            400,
            'Erwartet JSON {"zellen": [[...], ...]}',
        )

    return run_all(grid)


@app.get(
    "/healthz",
    response_class=PlainTextResponse,
)
def healthz():
    return "ok"


# ============================================
# UI
# ============================================

@app.get(
    "/",
    response_class=HTMLResponse,
    dependencies=[Depends(ui_auth)],
)
def ui():

    return (
        Path(__file__).parent / "ui.html"
    ).read_text("utf-8")


@app.get(
    "/api/info",
    dependencies=[Depends(ui_auth)],
)
def info_():

    return {"token": API_TOKEN}


@app.get(
    "/api/status",
    dependencies=[Depends(ui_auth)],
)
def status():

    return {
        "gefunden": FOUND,
        "geladen": list(Module.registry),
        "fehler": LOAD_ERRORS,
    }


@app.get(
    "/api/modules",
    dependencies=[Depends(ui_auth)],
)
def list_modules():

    result = []

    for mod in Module.registry.values():

        result.append({
            "id": mod.id,
            "name": mod.name,
            "description": mod.description,
            "inputs": [
                asdict(field)
                for field in mod.inputs
            ],
            "settings": [
                asdict(field)
                for field in mod.settings
            ],
            "outputs": [
                asdict(output)
                for output in mod.outputs
            ],
            "config": config_for(mod),
        })

    return result


@app.put(
    "/api/modules/{module_id}",
    dependencies=[Depends(ui_auth)],
)
async def save_module(
    module_id: str,
    request: Request,
):

    mod = Module.registry.get(module_id)

    if mod is None:
        raise HTTPException(404, "Modul unbekannt")

    try:

        body = await request.json()

        config = validate_config(mod, body)

        store.save_module(module_id, config)

        return {
            "ok": True,
            "anzahl": len(config["berechnungen"]),
        }

    except (ValueError, TypeError) as error:

        raise HTTPException(400, str(error))


@app.get(
    "/api/export",
    dependencies=[Depends(ui_auth)],
)
def export_config():

    return {
        mod.id: config_for(mod)
        for mod in Module.registry.values()
    }


@app.post(
    "/api/import",
    dependencies=[Depends(ui_auth)],
)
async def import_config(request: Request):

    try:

        body = await request.json()

        if not isinstance(body, dict):
            raise ValueError("JSON muss ein Objekt sein")

        validated = {}

        for module_id, config in body.items():

            mod = Module.registry.get(module_id)

            if mod is None:
                raise ValueError(
                    f"Unbekanntes Modul: {module_id}"
                )

            validated[module_id] = validate_config(
                mod,
                config,
            )

        for module_id, config in validated.items():
            store.save_module(module_id, config)

        return {
            "ok": True,
            "module": len(validated),
        }

    except (ValueError, TypeError) as error:

        raise HTTPException(400, str(error))


# ============================================
# EXCEL DATEI TESTEN
# ============================================

@app.post(
    "/api/test",
    dependencies=[Depends(ui_auth)],
)
async def test_file(
    file: UploadFile = File(...),
    blatt: str = Form(""),
):

    import openpyxl

    try:

        wb = openpyxl.load_workbook(
            io.BytesIO(await file.read()),
            data_only=True,
        )

    except Exception as error:

        raise HTTPException(
            400,
            f"Datei nicht lesbar: {error}",
        )

    ws = (
        wb[blatt]
        if blatt in wb.sheetnames
        else wb.worksheets[0]
    )

    grid = [
        list(row)
        for row in ws.iter_rows(values_only=True)
    ]

    return run_all(grid)
