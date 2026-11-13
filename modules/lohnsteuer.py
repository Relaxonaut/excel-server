
import datetime
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, is_dataclass
from threading import Lock

from lohnsteuer_bmf import berechne_lohnsteuer_pap

from core.base import Field, Module


def _to_dict(ergebnis) -> dict:
    if isinstance(ergebnis, dict):
        return ergebnis
    if is_dataclass(ergebnis):
        return asdict(ergebnis)
    if hasattr(ergebnis, "__dict__"):
        return dict(vars(ergebnis))
    return {"ergebnis": ergebnis}


def _berechne(i: dict, s: dict, jahr: int) -> dict:
    kinder = (
        int(i["kinder"])
        if i["kinder"] == int(i["kinder"])
        else i["kinder"]
    )

    ergebnis = berechne_lohnsteuer_pap(
        brutto_jahr=int(i["brutto_monat"]) * 12,
        steuerklasse=i["steuerklasse"],
        bundesland=i["bundesland"],
        mit_kirchensteuer=s["mit_kirchensteuer"],
        kinder=kinder,
        geburtsjahr=i["geburtsjahr"],
        kv_zusatzbeitrag=s["kv_zusatzbeitrag"],
        ist_sachsen=s["ist_sachsen"],
        jahr=jahr,
    )

    return _to_dict(ergebnis)


class Lohnsteuer(Module):

    id = "lohnsteuer"
    name = "Lohnsteuer (BMF-PAP)"
    description = "Berechnet die Lohnsteuer aus Monatsbrutto, Steuerklasse usw."

    inputs = [
        Field("brutto_monat", "Monatsbrutto", "float", example=3500, cell="G20"),
        Field("steuerklasse", "Steuerklasse", "int", example=1, cell="C20"),
        Field("bundesland", "Bundesland", "text", example="Hamburg", cell="E20"),
        Field("kinder", "Kinder", "float", default=0, example=0, cell="D20"),
        Field("geburtsjahr", "Geburtsjahr", "int", example=1990, cell="F20"),
    ]

    settings = [
        Field("kv_zusatzbeitrag", "KV-Zusatzbeitrag (%)", "float", default=2.9),
        Field("mit_kirchensteuer", "Mit Kirchensteuer", "bool", default=False),
        Field("ist_sachsen", "Sachsen (Pflegeversicherung)", "bool", default=False),
    ]

    # Eigene Instanzverwaltung innerhalb des Moduls
    _instanzen = {}
    _lock = Lock()

    def calculate(self, i: dict, s: dict) -> dict:
        return _berechne(
            dict(i),
            dict(s),
            datetime.datetime.now().year,
        )

    # Neue unabhängige Berechnungsinstanz registrieren
    def neue_instanz(
        self,
        instanz_id: str,
        inputs: dict,
        settings: dict | None = None,
    ):
        with self._lock:
            self._instanzen[instanz_id] = {
                "inputs": dict(inputs),
                "settings": dict(settings or {}),
                "ergebnis": None,
            }

    # Eine bestimmte Instanz berechnen
    def berechne_instanz(self, instanz_id: str) -> dict:
        with self._lock:
            daten = self._instanzen[instanz_id].copy()

        ergebnis = self.calculate(
            daten["inputs"],
            {
                **{
                    "kv_zusatzbeitrag": 2.9,
                    "mit_kirchensteuer": False,
                    "ist_sachsen": False,
                },
                **daten["settings"],
            },
        )

        with self._lock:
            self._instanzen[instanz_id]["ergebnis"] = ergebnis

        return ergebnis

    # Alle registrierten Instanzen parallel berechnen
    def berechne_alle(self, max_workers=None) -> dict:
        with self._lock:
            ids = list(self._instanzen.keys())

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                instanz_id: executor.submit(
                    self.berechne_instanz,
                    instanz_id,
                )
                for instanz_id in ids
            }

        return {
            instanz_id: future.result()
            for instanz_id, future in futures.items()
        }

    # Ergebnis einer Instanz abrufen
    def ergebnis(self, instanz_id: str):
        with self._lock:
            return self._instanzen[instanz_id]["ergebnis"]

    # Instanz entfernen
    def entferne_instanz(self, instanz_id: str):
        with self._lock:
            self._instanzen.pop(instanz_id, None)

