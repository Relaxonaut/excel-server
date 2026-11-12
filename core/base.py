"""Basisklassen. Wer ein neues Modul schreibt, erbt von `Module`."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass
class Field:
    """Ein Eingabewert (aus einer Excel-Zelle) ODER eine Einstellung (im UI)."""
    name: str                          # technischer Name, z.B. "brutto_monat"
    label: str                         # Anzeigename im UI
    type: str = "text"                 # text | int | float | bool | select
    default: Any = None                # Wert, wenn nichts angegeben ist
    example: Any = None                # Beispielwert (fuer die automatische Ausgabe-Erkennung)
    options: list[str] | None = None   # nur bei type="select"
    help: str = ""                     # kleiner Hilfetext im UI
    cell: str = ""                     # Standard-Zelle in Excel (nur bei inputs), z.B. "G20"


@dataclass
class Output:
    """Ein Ergebniswert, den Excel abrufen kann."""
    name: str
    label: str
    type: str = "number"               # number | text


_CELL = re.compile(r"^\s*\$?([A-Za-z]{1,3})\$?(\d+)\s*$")


def cell_value(grid: list[list[Any]], address: str) -> Any:
    """Liest eine Zelle wie 'G20' aus dem Zellraster (Liste von Zeilen, Start bei A1)."""
    m = _CELL.match(address or "")
    if not m:
        raise ValueError(f"'{address}' ist keine gueltige Zelladresse (Beispiel: G20)")
    col = 0
    for ch in m.group(1).upper():
        col = col * 26 + (ord(ch) - 64)
    row = int(m.group(2))
    if row < 1:
        return None
    try:
        return grid[row - 1][col - 1]
    except IndexError:
        return None


def coerce(field: Field, raw: Any) -> Any:
    """Macht aus dem rohen Wert (Excel/UI) den passenden Python-Typ."""
    if raw is None or raw == "":
        if field.default is not None:
            return field.default
        raise ValueError(f"'{field.name}' fehlt")
    if field.type == "int":
        return int(float(str(raw).replace(",", ".")))
    if field.type == "float":
        return float(str(raw).replace(",", "."))
    if field.type == "bool":
        return str(raw).strip().lower() in ("1", "true", "wahr", "ja", "yes", "on")
    return str(raw)


class Module:
    """
    Basisklasse fuer alle Funktionen.

    Eine Unterklasse mit gesetzter `id` meldet sich automatisch an --
    UI-Formulare und Excel-Formeln entstehen dann von selbst.
    """
    registry: dict[str, "Module"] = {}

    id: str = ""                       # eindeutig; Kleinbuchstaben, Zahlen, _
    name: str = ""
    description: str = ""
    aktiv_standard: bool = True        # ist das Modul von Anfang an eingeschaltet?
    inputs: list[Field] = []           # Werte aus Excel-Zellen
    settings: list[Field] = []         # globale Einstellungen im UI
    outputs: list[Output] = []         # leer lassen = automatisch aus dem Ergebnis ermittelt

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.id:
            Module.registry[cls.id] = cls()

    def calculate(self, inputs: dict, settings: dict) -> dict:
        """Hier steckt die Logik. Rueckgabe: {ausgabename: wert}."""
        raise NotImplementedError

    def run(self, raw_inputs: dict, raw_settings: dict) -> dict:
        inputs = {f.name: coerce(f, raw_inputs.get(f.name)) for f in self.inputs}
        settings = {f.name: coerce(f, raw_settings.get(f.name)) for f in self.settings}
        return self.calculate(inputs, settings)

    def run_grid(self, grid: list[list[Any]], config: dict) -> dict:
        """Liest die Eingaben anhand der konfigurierten Zelladressen aus dem Raster und rechnet."""
        zellen = config.get("zellen", {})
        raw = {}
        for f in self.inputs:
            adresse = zellen.get(f.name) or f.cell
            wert = cell_value(grid, adresse) if adresse else None
            if (wert is None or wert == "") and f.default is None:
                raise ValueError(f"{f.label}: Zelle {adresse or '(keine angegeben)'} ist leer")
            raw[f.name] = wert
        return self.run(raw, config.get("werte", {}))

    def discover_outputs(self) -> None:
        """Keine Outputs deklariert? Dann einmal mit Beispielwerten rechnen und die Namen uebernehmen."""
        if self.outputs:
            return
        try:
            example = {f.name: f.example for f in self.inputs}
            result = self.run(example, {})
            self.outputs = [
                Output(k, k, "number" if isinstance(v, (int, float)) and not isinstance(v, bool) else "text")
                for k, v in result.items()
            ]
        except Exception as e:
            print(f"[WARNUNG] Outputs von '{self.id}' nicht ermittelbar: {e}")
