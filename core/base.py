
"""Basisklassen für alle Excel-Server-Module."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass
class Field:
    name: str
    label: str
    type: str = "text"
    default: Any = None
    example: Any = None
    options: list[str] | None = None
    help: str = ""
    cell: str = ""


@dataclass
class Output:
    name: str
    label: str
    type: str = "number"


_CELL = re.compile(r"^\s*\$?([A-Za-z]{1,3})\$?(\d+)\s*$")


def cell_value(grid: list[list[Any]], address: str) -> Any:
    m = _CELL.match(address or "")

    if not m:
        raise ValueError(
            f"'{address}' ist keine gültige Zelladresse (Beispiel: G20)"
        )

    col = 0

    for ch in m.group(1).upper():
        col = col * 26 + ord(ch) - 64

    row = int(m.group(2))

    if row < 1:
        return None

    try:
        return grid[row - 1][col - 1]
    except IndexError:
        return None


def valid_cell(address: str) -> bool:
    return bool(_CELL.match(address or ""))


def coerce(field: Field, raw: Any) -> Any:
    if raw is None or raw == "":
        if field.default is not None:
            return field.default
        raise ValueError(f"'{field.name}' fehlt")

    if field.type == "int":
        return int(float(str(raw).replace(",", ".")))

    if field.type == "float":
        return float(str(raw).replace(",", "."))

    if field.type == "bool":
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in (
            "1", "true", "wahr", "ja", "yes", "on"
        )

    if field.type == "select":
        value = str(raw)
        if field.options and value not in field.options:
            raise ValueError(
                f"Ungültige Auswahl für '{field.name}': {value}"
            )
        return value

    return str(raw)


class Module:
    registry: dict[str, "Module"] = {}

    id: str = ""
    name: str = ""
    description: str = ""
    aktiv_standard: bool = True

    inputs: list[Field] = []
    settings: list[Field] = []
    outputs: list[Output] = []

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

        if cls.id:
            Module.registry[cls.id] = cls()

    def calculate(self, inputs: dict, settings: dict) -> dict:
        raise NotImplementedError

    def run(self, raw_inputs: dict, raw_settings: dict) -> dict:

        inputs = {
            f.name: coerce(f, raw_inputs.get(f.name))
            for f in self.inputs
        }

        settings = {
            f.name: coerce(f, raw_settings.get(f.name))
            for f in self.settings
        }

        return self.calculate(inputs, settings)

    def run_grid(self, grid: list[list[Any]], config: dict) -> dict:

        zellen = config.get("zellen", {})
        raw = {}

        for field in self.inputs:

            adresse = zellen.get(field.name) or field.cell

            wert = cell_value(grid, adresse) if adresse else None

            if (wert is None or wert == "") and field.default is None:
                raise ValueError(
                    f"{field.label}: "
                    f"Zelle {adresse or '(keine angegeben)'} ist leer"
                )

            raw[field.name] = wert

        return self.run(raw, config.get("werte", {}))

    def run_instances(
        self,
        grid: list[list[Any]],
        config: dict,
    ) -> dict:

        """
        Führt beliebig viele Berechnungsinstanzen eines Moduls aus.

        Jede Instanz besitzt eigene Excel-Zellzuordnungen.
        Die Settings werden global übernommen.
        """

        results = {}

        instances = config.get("berechnungen", [])

        for instance in instances:

            if not instance.get("aktiv", True):
                continue

            instance_id = instance["id"]

            try:

                result = self.run_grid(
                    grid,
                    {
                        "zellen": instance.get("zellen", {}),
                        "werte": config.get("werte", {}),
                    },
                )

                for key, value in result.items():

                    results[
                        f"{instance_id}.{key}"
                    ] = value

            except Exception as error:

                results[
                    f"{instance_id}._fehler"
                ] = str(error)

        return results

    def discover_outputs(self) -> None:

        if self.outputs:
            return

        try:

            example = {
                f.name: f.example
                for f in self.inputs
            }

            result = self.run(example, {})

            self.outputs = [
                Output(
                    k,
                    k,
                    "number"
                    if isinstance(v, (int, float))
                    and not isinstance(v, bool)
                    else "text",
                )
                for k, v in result.items()
            ]

        except Exception as error:

            print(
                f"[WARNUNG] Outputs von '{self.id}' "
                f"nicht ermittelbar: {error}"
            )
