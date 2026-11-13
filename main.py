
"""
Excel-Server mit Mehrfachberechnungen.

- Beliebig viele Berechnungen pro Modul
- Individuelle Excel-Zellzuordnungen
- Individuelle Aktivierung jeder Berechnung
- Gemeinsame Moduleinstellungen
- Eindeutige Excel-Ergebnisnamen
- Konfiguration im Arbeitsspeicher
- Optional SETTINGS_JSON als Startkonfiguration

Start:
uvicorn main:app --host 0.0.0.0 --port 8000
"""

import copy
import hmac
import importlib
import io
import json
import os
import pkgutil

from pathlib import Path
from dataclasses import asdict

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

from core.base import Module, cell_value, coerce


# ==================================================
# KONFIGURATION
# ==================================================

API_TOKEN = os.environ.get("API_TOKEN", "")
UI_PASSWORD = os.environ.get("UI_PASSWORD", "")

if os.environ.get("RENDER") and not (API_TOKEN and UI_PASSWORD):
    raise RuntimeError(
        "Auf Render muessen API_TOKEN und UI_PASSWORD gesetzt sein."
    )


# ==================================================
# IN-MEMORY SPEICHER
# ==================================================

def load_initial_config():
    raw = os.environ.get("SETTINGS_JSON", "").strip()

    if not raw:
        return {}

    try:
        result = json.loads(raw)

        if not isinstance(result, dict):
            raise ValueError("SETTINGS_JSON muss ein JSON-Objekt sein.")

        return result

    except Exception as e:
        print(f"[WARNUNG] SETTINGS_JSON ungueltig: {e}")
        return {}


CONFIG = load_initial_config()


# ==================================================
# MODULE AUTOMATISCH LADEN
# ==================================================

LOAD_ERRORS = []

FOUND = [
    item.name
    for item in pkgutil.iter_modules(modules.__path__)
]

for name in FOUND:
    try:
        importlib.import_module(f"modules.{name}")

    except Exception as e:
        LOAD_ERRORS.append({
            "modul": name,
            "meldung": f"{type(e).__name__}: {e}",
        })

        print(
            f"[WARNUNG] Modul {name} konnte nicht geladen werden: {e}"
        )


for mod in Module.registry.values():
    mod.discover_outputs()


app = FastAPI(title="Excel-Server")


# ==================================================
# AUTHENTIFIZIERUNG
# ==================================================

_basic = HTTPBasic(auto_error=False)


def _gleich(a: str, b: str) -> bool:
    return hmac.compare_digest(
        a.encode(),
        b.encode()
    )


def ui_auth(
    creds: HTTPBasicCredentials | None = Depends(_basic)
):
    if UI_PASSWORD and not (
        creds and _gleich(creds.password, UI_PASSWORD)
    ):
        raise HTTPException(
            401,
            "Passwort notwendig",
            headers={
                "WWW-Authenticate": 'Basic realm="Excel-Server"'
            },
        )


def token_auth(
    x_token: str | None = Header(default=None)
):
    if API_TOKEN and not (
        x_token and _gleich(x_token, API_TOKEN)
    ):
        raise HTTPException(401, "Token falsch")


# ==================================================
# STANDARD-BERECHNUNG ERSTELLEN
# ==================================================

def default_instance(mod: Module) -> dict:
    return {
        "id": "1",
        "name": "Berechnung 1",
        "aktiv": True,
        "zellen": {
            field.name: field.cell or ""
            for field in mod.inputs
        },
    }


# ==================================================
# MODULKONFIGURATION
# ==================================================

def config_for(mod: Module) -> dict:

    saved = CONFIG.get(mod.id, {})

    if not isinstance(saved, dict):
        saved = {}

    werte = {}

    for field in mod.settings:
        werte[field.name] = saved.get(
            "werte", {}
        ).get(
            field.name,
            field.default
        )

    # Bestehende Berechnungen übernehmen
    if "berechnungen" in saved:

        berechnungen = copy.deepcopy(
            saved.get("berechnungen") or []
        )

    else:
        # Alte Einzelkonfiguration migrieren
        instance = default_instance(mod)

        instance["zellen"].update(
            saved.get("zellen", {})
        )

        berechnungen = [instance]

    # Fehlende Zellzuordnungen ergänzen
    for instance in berechnungen:

        instance.setdefault("id", str(id(instance)))
        instance.setdefault("name", "Berechnung")
        instance.setdefault("aktiv", True)
        instance.setdefault("zellen", {})

        for field in mod.inputs:
            if not instance["zellen"].get(field.name):
                instance["zellen"][field.name] = field.cell or ""

    return {
        "aktiv": saved.get(
            "aktiv",
            mod.aktiv_standard
        ),
        "werte": werte,
        "berechnungen": berechnungen,

        # Kompatibilität mit alter Konfiguration
        "zellen": (
            berechnungen[0]["zellen"]
            if berechnungen
            else {}
        ),
    }


# ==================================================
# JSON-VERTRÄGLICHE WERTE
# ==================================================

def jsonable(value):

    if value is None:
        return None

    if isinstance(value, (bool, int, str)):
        return value

    if isinstance(value, float):

        if value == value and abs(value) != float("inf"):
            return value

        return str(value)

    return str(value)


# ==================================================
# ALLE BERECHNUNGEN AUSFÜHREN
# ==================================================

def run_all(grid: list) -> list[dict]:

    rows = []

    for mod in Module.registry.values():

        cfg = config_for(mod)

        # Gesamtes Modul deaktiviert
        if not cfg["aktiv"]:
            continue

        berechnungen = cfg["berechnungen"]

        for index, instance in enumerate(berechnungen, start=1):

            # Einzelne Berechnung deaktiviert
            if not instance.get("aktiv", True):
                continue

            # Eindeutiger Name für Excel
            prefix = (
                f"{mod.id}.berechnung_{index}"
            )

            try:

                result = mod.run_grid(
                    grid,
                    {
                        "zellen": instance["zellen"],
                        "werte": cfg["werte"],
                    },
                )

                for key, value in result.items():

                    rows.append({
                        "Name": f"{prefix}.{key}",
                        "Wert": jsonable(value),
                    })

            except Exception as e:

                rows.append({
                    "Name": f"{prefix}._fehler",
                    "Wert": str(e),
                })

    return rows


# ==================================================
# EXCEL-SCHNITTSTELLE
# ==================================================

@app.post(
    "/excel/run",
    dependencies=[Depends(token_auth)]
)
async def excel_run(request: Request):

    try:
        body = await request.json()

    except Exception:
        raise HTTPException(400, "Ungueltiges JSON")

    grid = (
        body.get("zellen")
        if isinstance(body, dict)
        else None
    )

    if not isinstance(grid, list):
        raise HTTPException(
            400,
            'Erwartet JSON {"zellen": [[...], [...]]}'
        )

    return run_all(grid)


# ==================================================
# HEALTH CHECK
# ==================================================

@app.get(
    "/healthz",
    response_class=PlainTextResponse
)
def healthz():
    return "ok"


# ==================================================
# WEBOBERFLÄCHE
# ==================================================

@app.get(
    "/",
    response_class=HTMLResponse,
    dependencies=[Depends(ui_auth)]
)
def ui():

    return (
        Path(__file__).parent / "ui.html"
    ).read_text("utf-8")


# ==================================================
# API INFORMATION
# ==================================================

@app.get(
    "/api/info",
    dependencies=[Depends(ui_auth)]
)
def info():
    return {
        "token": API_TOKEN
    }


# ==================================================
# STATUS
# ==================================================

@app.get(
    "/api/status",
    dependencies=[Depends(ui_auth)]
)
def status():

    return {
        "gefunden": FOUND,
        "geladen": list(Module.registry),
        "fehler": LOAD_ERRORS,
    }


# ==================================================
# MODULE AUFLISTEN
# ==================================================

@app.get(
    "/api/modules",
    dependencies=[Depends(ui_auth)]
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


# ==================================================
# MODUL SPEICHERN
# ==================================================

@app.put(
    "/api/modules/{module_id}",
    dependencies=[Depends(ui_auth)]
)
async def save_module(
    module_id: str,
    request: Request
):

    mod = Module.registry.get(module_id)

    if mod is None:
        raise HTTPException(
            404,
            "Modul unbekannt"
        )

    try:

        body = await request.json()

        # Globale Einstellungen
        werte = {}

        for field in mod.settings:

            raw = body.get(
                "werte", {}
            ).get(
                field.name,
                field.default
            )

            werte[field.name] = coerce(
                field,
                raw
            )

        # Mehrfachberechnungen
        raw_instances = body.get(
            "berechnungen",
            []
        )

        if not isinstance(raw_instances, list):
            raise ValueError(
                "berechnungen muss eine Liste sein."
            )

        berechnungen = []
        verwendete_ids = set()

        for index, instance in enumerate(
            raw_instances,
            start=1
        ):

            instance_id = str(
                instance.get("id") or index
            )

            if instance_id in verwendete_ids:
                raise ValueError(
                    f"Doppelte Berechnungs-ID: {instance_id}"
                )

            verwendete_ids.add(instance_id)

            zellen = {}

            for field in mod.inputs:

                adresse = str(
                    instance.get(
                        "zellen", {}
                    ).get(
                        field.name,
                        field.cell or ""
                    )
                ).strip()

                if adresse:
                    # Zelladresse validieren
                    cell_value([], adresse)

                zellen[field.name] = adresse

            berechnungen.append({

                "id": instance_id,

                "name": str(
                    instance.get(
                        "name",
                        f"Berechnung {index}"
                    )
                ),

                "aktiv": bool(
                    instance.get("aktiv", True)
                ),

                "zellen": zellen,
            })

        # Gesamtkonfiguration
        config = {

            "aktiv": bool(
                body.get(
                    "aktiv",
                    mod.aktiv_standard
                )
            ),

            "werte": werte,

            "berechnungen": berechnungen,

            # Kompatibilität
            "zellen": (
                berechnungen[0]["zellen"]
                if berechnungen
                else {}
            ),
        }

        # Im Arbeitsspeicher aktualisieren
        CONFIG[module_id] = config

        return {
            "ok": True,
            "anzahl": len(berechnungen),
            "gespeichert": True,
        }

    except HTTPException:
        raise

    except Exception as e:

        raise HTTPException(
            400,
            str(e)
        )


# ==================================================
# KONFIGURATION EXPORT
# ==================================================

@app.get(
    "/api/export",
    dependencies=[Depends(ui_auth)]
)
def export_config():

    return {
        mod.id: config_for(mod)
        for mod in Module.registry.values()
    }


# ==================================================
# EXCEL-DATEI TESTEN
# ==================================================

@app.post(
    "/api/test",
    dependencies=[Depends(ui_auth)]
)
async def test_file(
    file: UploadFile = File(...),
    blatt: str = Form("")
):

    import openpyxl

    try:

        content = await file.read()

        wb = openpyxl.load_workbook(
            io.BytesIO(content),
            data_only=True
        )

    except Exception as e:

        raise HTTPException(
            400,
            f"Datei nicht lesbar: {e}"
        )

    if blatt and blatt in wb.sheetnames:
        ws = wb[blatt]
    else:
        ws = wb.worksheets[0]

    grid = [
        list(row)
        for row in ws.iter_rows(
            values_only=True
        )
    ]

    return run_all(grid)
