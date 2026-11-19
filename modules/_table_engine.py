"""Shared helpers for modules that consume variable-length Excel tables.

The existing server calls Module.run_grid(grid, {"zellen": ..., "werte": ...}).
This mixin intentionally overrides that hook without requiring changes to
core.base.py. A table field's configured cell address is its top-left anchor.
"""
from __future__ import annotations

import datetime as _dt
import json
import math
import re
from typing import Any

_CELL_RE = re.compile(r"^\$?([A-Za-z]{1,3})\$?([1-9][0-9]*)$")


def _col_to_index(letters: str) -> int:
    value = 0
    for char in letters.upper():
        value = value * 26 + (ord(char) - 64)
    return value - 1


def parse_cell(address: str) -> tuple[int, int]:
    match = _CELL_RE.match(str(address or "").strip())
    if not match:
        raise ValueError(f"Ungültige Excel-Zelladresse: {address!r}")
    return int(match.group(2)) - 1, _col_to_index(match.group(1))


def read_cell(grid: list, address: str) -> Any:
    if not address:
        return None
    row_index, col_index = parse_cell(address)
    if row_index >= len(grid):
        return None
    row = grid[row_index]
    if not isinstance(row, (list, tuple)) or col_index >= len(row):
        return None
    return row[col_index]


def read_table(grid: list, anchor: str, width: int, max_rows: int = 1000) -> list[list[Any]]:
    """Read a vertical table from anchor; stop after two fully blank rows."""
    if not anchor:
        return []
    start_row, start_col = parse_cell(anchor)
    result: list[list[Any]] = []
    blank_run = 0
    upper = min(len(grid), start_row + max_rows)
    for row_index in range(start_row, upper):
        source = grid[row_index] if row_index < len(grid) else []
        if not isinstance(source, (list, tuple)):
            source = []
        row = [source[i] if i < len(source) else None for i in range(start_col, start_col + width)]
        if all(value is None or str(value).strip() == "" for value in row):
            blank_run += 1
            if blank_run >= 2:
                break
            continue
        blank_run = 0
        result.append(row)
    return result


def is_header(row: list[Any], tokens: tuple[str, ...]) -> bool:
    first = normalise(row[0] if row else "")
    return first in {normalise(token) for token in tokens}


def normalise(value: Any) -> str:
    return str(value if value is not None else "").strip().upper().replace("Ä", "AE").replace("Ö", "OE").replace("Ü", "UE").replace("ß", "SS")


def number(value: Any, default: float = 0.0, field_name: str = "Wert") -> float:
    if value is None or str(value).strip() == "":
        return default
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        result = float(value)
    else:
        raw = str(value).strip().replace("€", "").replace("%", "").replace("\u00a0", "").replace(" ", "")
        if "," in raw and "." in raw:
            # German thousands separator: 1.234,56
            raw = raw.replace(".", "").replace(",", ".")
        elif "," in raw:
            raw = raw.replace(",", ".")
        result = float(raw)
    if not math.isfinite(result):
        raise ValueError(f"{field_name} muss eine endliche Zahl sein.")
    return result


def integer(value: Any, default: int = 0, field_name: str = "Wert") -> int:
    result = number(value, float(default), field_name)
    if not result.is_integer():
        raise ValueError(f"{field_name} muss eine ganze Zahl sein.")
    return int(result)


def boolean(value: Any, default: bool = False) -> bool:
    if value is None or str(value).strip() == "":
        return default
    if isinstance(value, bool):
        return value
    return normalise(value) in {"1", "TRUE", "JA", "YES", "WAHR", "ON", "X"}


def parse_date(value: Any, field_name: str = "Datum") -> _dt.date | None:
    if value is None or str(value).strip() == "":
        return None
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # Excel serial date (1900 date system; serial 60's historical leap-year quirk is harmless here).
        if 1 <= float(value) <= 100000:
            return (_dt.date(1899, 12, 30) + _dt.timedelta(days=int(float(value))))
    if isinstance(value, _dt.date):
        return value
    text = str(value).strip()
    # .NET/Power Query JSON date form, if the connector emits one.
    dotnet_date = re.fullmatch(r"/Date\((-?\d+)(?:[+-]\d{4})?\)/", text)
    if dotnet_date:
        milliseconds = int(dotnet_date.group(1))
        return _dt.datetime.fromtimestamp(milliseconds / 1000, tz=_dt.timezone.utc).date()
    # Power Query / ISO timestamps
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return _dt.datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return _dt.datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    raise ValueError(f"{field_name}: Datum {value!r} bitte als JJJJ-MM-TT oder TT.MM.JJJJ angeben.")


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def round_eur(value: float) -> float:
    return round(float(value) + 1e-10, 2)


def cast_field(value: Any, field_type: str, default: Any = None, label: str = "Eingabe") -> Any:
    if value is None or str(value).strip() == "":
        value = default
    if field_type == "float":
        return number(value, 0.0, label)
    if field_type == "int":
        return integer(value, 0, label)
    if field_type == "bool":
        return boolean(value, bool(default))
    if value is None:
        return ""
    return value


class FlexibleTableMixin:
    """Mixin for modules with one or more Excel table anchors."""

    TABLE_FIELDS: dict[str, int] = {}

    def run_grid(self, grid: list, config: dict) -> dict:
        zellen = (config or {}).get("zellen", {}) or {}
        werte = (config or {}).get("werte", {}) or {}
        inputs: dict[str, Any] = {}

        for field in self.inputs:
            address = str(zellen.get(field.name) or field.cell or "").strip()
            if field.name in self.TABLE_FIELDS:
                inputs[field.name] = address
                inputs[f"_{field.name}_rows"] = read_table(
                    grid, address, self.TABLE_FIELDS[field.name]
                )
            else:
                raw = read_cell(grid, address) if address else None
                inputs[field.name] = cast_field(
                    raw,
                    getattr(field, "type", "text"),
                    getattr(field, "default", None),
                    getattr(field, "label", field.name),
                )

        settings: dict[str, Any] = {}
        for field in self.settings:
            raw = werte.get(field.name, getattr(field, "default", None))
            settings[field.name] = cast_field(
                raw,
                getattr(field, "type", "text"),
                getattr(field, "default", None),
                getattr(field, "label", field.name),
            )

        return self.calculate(inputs, settings)
