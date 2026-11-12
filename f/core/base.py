"""Basisklassen. Wer ein neues Modul schreibt, erbt von `Module`."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Field:
    """Ein Eingabewert (kommt aus einer Excel-Zelle) ODER eine Einstellung (steht im UI)."""
    name: str                      # technischer Name, z.B. "brutto_monat"
    label: str                     # Anzeigename im UI
    type: str = "text"             # text | int | float | bool | select
    default: Any = None            # Wert, wenn nichts angegeben wird
    example: Any = None            # Beispielwert fuer den Test-Link im UI
    options: list[str] | None = None   # nur bei type="select"
    help: str = ""                 # kleiner Hilfetext im UI


@dataclass
class Output:
    """Ein Ergebniswert, den Excel abrufen kann (pro Excel-Zelle ein Wert)."""
    name: str
    label: str
    type: str = "number"           # number | text


def coerce(field: Field, raw: Any) -> Any:
    """Macht aus dem rohen Text (aus URL/UI) den richtigen Python-Typ."""
    if raw is None or raw == "":
        if field.default is not None:
            return field.default
        raise ValueError(f"'{field.name}' fehlt")
    if field.type == "int":
        return int(float(str(raw).replace(",", ".")))
    if field.type == "float":
        return float(str(raw).replace(",", "."))   # Excel sendet deutsche Kommazahlen
    if field.type == "bool":
        return str(raw).strip().lower() in ("1", "true", "wahr", "ja", "yes", "on")
    return str(raw)


class Module:
    """
    Basisklasse fuer alle Funktionen.

    Eine Unterklasse mit gesetzter `id` registriert sich automatisch --
    das UI und die Excel-Formeln entstehen dann von selbst.
    """
    registry: dict[str, "Module"] = {}

    id: str = ""                   # eindeutig, nur Kleinbuchstaben/Zahlen/_
    name: str = ""
    description: str = ""
    inputs: list[Field] = []       # pro Aufruf aus Excel-Zellen
    settings: list[Field] = []     # global, im UI einstellbar
    outputs: list[Output] = []     # leer lassen = automatisch aus dem Ergebnis ermittelt

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.id:
            Module.registry[cls.id] = cls()

    def calculate(self, inputs: dict, settings: dict) -> dict:
        """Hier steckt die eigentliche Logik. Rueckgabe: {ausgabename: wert}."""
        raise NotImplementedError

    def run(self, raw_inputs: dict, raw_settings: dict) -> dict:
        inputs = {f.name: coerce(f, raw_inputs.get(f.name)) for f in self.inputs}
        settings = {f.name: coerce(f, raw_settings.get(f.name)) for f in self.settings}
        return self.calculate(inputs, settings)

    def discover_outputs(self) -> None:
        """Falls keine Outputs deklariert sind: einmal mit Beispielwerten rechnen und Namen uebernehmen."""
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
