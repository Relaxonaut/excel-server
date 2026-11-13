
import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, is_dataclass
from threading import RLock
from typing import Optional

from lohnsteuer_bmf import berechne_lohnsteuer_pap

from core.base import Field, Module


def _to_dict(ergebnis) -> dict:
    if isinstance(ergebnis, dict):
        return dict(ergebnis)

    if is_dataclass(ergebnis):
        return asdict(ergebnis)

    if hasattr(ergebnis, "__dict__"):
        return dict(vars(ergebnis))

    return {"ergebnis": ergebnis}


def _berechne(inputs: dict, settings: dict, jahr: int) -> dict:
    kinder = float(inputs.get("kinder", 0))

    if kinder < 0:
        raise ValueError("Kinderfreibetrag darf nicht negativ sein.")

    kinderwert = int(kinder) if kinder.is_integer() else kinder

    brutto = float(inputs["brutto_monat"])

    if brutto < 0:
        raise ValueError("Monatsbrutto darf nicht negativ sein.")

    if brutto <= 0:
        raise ValueError("Monatsbrutto muss größer als 0 sein.")

    ergebnis = berechne_lohnsteuer_pap(
        brutto_jahr=int(round(brutto * 12)),
        steuerklasse=int(inputs["steuerklasse"]),
        bundesland=inputs["bundesland"],
        mit_kirchensteuer=bool(settings["mit_kirchensteuer"]),
        kinder=kinderwert,
        geburtsjahr=int(inputs["geburtsjahr"]),
        kv_zusatzbeitrag=float(settings["kv_zusatzbeitrag"]),
        ist_sachsen=bool(settings["ist_sachsen"]),
        jahr=int(jahr),
    )

    return _to_dict(ergebnis)


class Lohnsteuer(Module):

    id = "lohnsteuer"
    name = "Lohnsteuer (BMF-PAP)"
    description = (
        "Berechnet die deutsche Lohnsteuer "
        "auf Basis des BMF-Programmablaufplans."
    )

    DEFAULT_SETTINGS = {
        "kv_zusatzbeitrag": 2.9,
        "mit_kirchensteuer": False,
        "ist_sachsen": False,
    }

    inputs = [
        Field("brutto_monat", "Monatsbrutto", "float",
              example=3500, cell="G20"),
        Field("steuerklasse", "Steuerklasse", "int",
              example=1, cell="C20"),
        Field("bundesland", "Bundesland", "text",
              example="Hamburg", cell="E20"),
        Field("kinder", "Kinder", "float",
              default=0, example=0, cell="D20"),
        Field("geburtsjahr", "Geburtsjahr", "int",
              example=1990, cell="F20"),
    ]

    settings = [
        Field("kv_zusatzbeitrag", "KV-Zusatzbeitrag (%)",
              "float", default=2.9),
        Field("mit_kirchensteuer", "Mit Kirchensteuer",
              "bool", default=False),
        Field("ist_sachsen", "Sachsen (Pflegeversicherung)",
              "bool", default=False),
    ]

    def __init__(self):
        super().__init__()
        self._instanzen = {}
        self._lock = RLock()

    # -------------------------------------------------
    # Einzelberechnung
    # -------------------------------------------------

    def calculate(self, i: dict, s: dict) -> dict:
        settings = {
            **self.DEFAULT_SETTINGS,
            **dict(s or {}),
        }

        return _berechne(
            dict(i),
            settings,
            datetime.datetime.now().year,
        )

    # -------------------------------------------------
    # Neue Instanz erstellen
    # -------------------------------------------------

    def neue_instanz(
        self,
        instanz_id: str,
        inputs: dict,
        settings: Optional[dict] = None,
        jahr: Optional[int] = None,
    ) -> dict:

        if not instanz_id:
            raise ValueError("Instanz-ID darf nicht leer sein.")

        with self._lock:
            if instanz_id in self._instanzen:
                raise ValueError(
                    f"Instanz '{instanz_id}' existiert bereits."
                )

            self._instanzen[instanz_id] = {
                "inputs": dict(inputs),
                "settings": {
                    **self.DEFAULT_SETTINGS,
                    **dict(settings or {}),
                },
                "jahr": jahr or datetime.datetime.now().year,
                "ergebnis": None,
                "fehler": None,
            }

        return self.instanz(instanz_id)

    # -------------------------------------------------
    # Instanz ändern
    # -------------------------------------------------

    def aktualisiere_instanz(
        self,
        instanz_id: str,
        inputs: Optional[dict] = None,
        settings: Optional[dict] = None,
        jahr: Optional[int] = None,
    ):

        with self._lock:
            if instanz_id not in self._instanzen:
                raise KeyError(f"Instanz '{instanz_id}' nicht gefunden.")

            daten = self._instanzen[instanz_id]

            if inputs is not None:
                daten["inputs"].update(inputs)

            if settings is not None:
                daten["settings"].update(settings)

            if jahr is not None:
                daten["jahr"] = jahr

            daten["ergebnis"] = None
            daten["fehler"] = None

    # -------------------------------------------------
    # Einzelne Instanz berechnen
    # -------------------------------------------------

    def berechne_instanz(self, instanz_id: str) -> dict:

        with self._lock:
            if instanz_id not in self._instanzen:
                raise KeyError(f"Instanz '{instanz_id}' nicht gefunden.")

            daten = {
                "inputs": dict(self._instanzen[instanz_id]["inputs"]),
                "settings": dict(self._instanzen[instanz_id]["settings"]),
                "jahr": self._instanzen[instanz_id]["jahr"],
            }

        try:
            ergebnis = _berechne(
                daten["inputs"],
                daten["settings"],
                daten["jahr"],
            )

            with self._lock:
                if instanz_id in self._instanzen:
                    self._instanzen[instanz_id]["ergebnis"] = ergebnis
                    self._instanzen[instanz_id]["fehler"] = None

            return ergebnis

        except Exception as exc:
            with self._lock:
                if instanz_id in self._instanzen:
                    self._instanzen[instanz_id]["fehler"] = str(exc)

            raise

    # -------------------------------------------------
    # Alle Instanzen parallel berechnen
    # -------------------------------------------------

    def berechne_alle(self, max_workers=None) -> dict:

        with self._lock:
            ids = list(self._instanzen.keys())

        ergebnisse = {}

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self.berechne_instanz, iid): iid
                for iid in ids
            }

            for future in as_completed(futures):
                iid = futures[future]

                try:
                    ergebnisse[iid] = {
                        "success": True,
                        "ergebnis": future.result(),
                    }
                except Exception as exc:
                    ergebnisse[iid] = {
                        "success": False,
                        "fehler": str(exc),
                    }

        return ergebnisse

    # -------------------------------------------------
    # Instanz abrufen
    # -------------------------------------------------

    def instanz(self, instanz_id: str) -> dict:

        with self._lock:
            if instanz_id not in self._instanzen:
                raise KeyError(f"Instanz '{instanz_id}' nicht gefunden.")

            daten = self._instanzen[instanz_id]

            return {
                "id": instanz_id,
                "inputs": dict(daten["inputs"]),
                "settings": dict(daten["settings"]),
                "jahr": daten["jahr"],
                "ergebnis": daten["ergebnis"],
                "fehler": daten["fehler"],
            }

    # -------------------------------------------------
    # Ergebnis abrufen
    # -------------------------------------------------

    def ergebnis(self, instanz_id: str):

        return self.instanz(instanz_id)["ergebnis"]

    # -------------------------------------------------
    # Alle Instanzen abrufen
    # -------------------------------------------------

    def alle_instanzen(self) -> dict:

        with self._lock:
            ids = list(self._instanzen.keys())

        return {
            iid: self.instanz(iid)
            for iid in ids
        }

    # -------------------------------------------------
    # Instanz löschen
    # -------------------------------------------------

    def entferne_instanz(self, instanz_id: str):

        with self._lock:
            if instanz_id not in self._instanzen:
                return False

            del self._instanzen[instanz_id]
            return True

    # -------------------------------------------------
    # Alle Instanzen löschen
    # -------------------------------------------------

    def alle_entfernen(self):

        with self._lock:
            self._instanzen.clear()
