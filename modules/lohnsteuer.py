
import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, is_dataclass
from threading import RLock
from typing import Optional

from lohnsteuer_bmf import berechne_lohnsteuer_pap

from core.base import Field, Module, Output


def _to_dict(ergebnis) -> dict:
    if isinstance(ergebnis, dict):
        return dict(ergebnis)

    if is_dataclass(ergebnis):
        return asdict(ergebnis)

    if hasattr(ergebnis, "__dict__"):
        return dict(vars(ergebnis))

    return {"ergebnis": ergebnis}


def _berechne(inputs: dict, settings: dict, jahr: int) -> dict:

    brutto = float(inputs["brutto_monat"])

    if brutto <= 0:
        raise ValueError("Monatsbrutto muss größer als 0 sein.")

    kinder = float(inputs["kinder"])

    if kinder < 0:
        raise ValueError("Kinder darf nicht negativ sein.")

    kinder = int(kinder) if kinder.is_integer() else kinder

    steuerklasse = int(inputs["steuerklasse"])

    if steuerklasse not in (1, 2, 3, 4, 5, 6):
        raise ValueError("Ungültige Steuerklasse.")

    ergebnis = berechne_lohnsteuer_pap(
        brutto_jahr=int(round(brutto * 12)),
        steuerklasse=steuerklasse,
        bundesland=inputs["bundesland"],
        mit_kirchensteuer=settings["mit_kirchensteuer"],
        kinder=kinder,
        geburtsjahr=int(inputs["geburtsjahr"]),
        kv_zusatzbeitrag=float(settings["kv_zusatzbeitrag"]),
        ist_sachsen=settings["ist_sachsen"],
        jahr=int(jahr),
    )

    return _to_dict(ergebnis)


class Lohnsteuer(Module):

    id = "lohnsteuer"
    name = "Lohnsteuer (BMF-PAP)"

    description = (
        "Berechnet die deutsche Lohnsteuer anhand "
        "des Bundesfinanzministeriums."
    )

    aktiv_standard = True

    inputs = [
        Field(
            "brutto_monat",
            "Monatsbrutto",
            "float",
            example=3500,
            cell="G20",
            help="Monatliches Bruttogehalt in Euro."
        ),
        Field(
            "steuerklasse",
            "Steuerklasse",
            "int",
            example=1,
            cell="C20",
        ),
        Field(
            "bundesland",
            "Bundesland",
            "select",
            example="Hamburg",
            cell="E20",
            options=[
                "Baden-Württemberg",
                "Bayern",
                "Berlin",
                "Brandenburg",
                "Bremen",
                "Hamburg",
                "Hessen",
                "Mecklenburg-Vorpommern",
                "Niedersachsen",
                "Nordrhein-Westfalen",
                "Rheinland-Pfalz",
                "Saarland",
                "Sachsen",
                "Sachsen-Anhalt",
                "Schleswig-Holstein",
                "Thüringen",
            ],
        ),
        Field(
            "kinder",
            "Kinderfreibetrag",
            "float",
            default=0,
            example=0,
            cell="D20",
        ),
        Field(
            "geburtsjahr",
            "Geburtsjahr",
            "int",
            example=1990,
            cell="F20",
        ),
    ]

    settings = [
        Field(
            "kv_zusatzbeitrag",
            "KV-Zusatzbeitrag (%)",
            "float",
            default=2.9,
        ),
        Field(
            "mit_kirchensteuer",
            "Kirchensteuer",
            "bool",
            default=False,
        ),
        Field(
            "ist_sachsen",
            "Sachsen (Pflegeversicherung)",
            "bool",
            default=False,
        ),
    ]

    outputs = []

    DEFAULT_SETTINGS = {
        "kv_zusatzbeitrag": 2.9,
        "mit_kirchensteuer": False,
        "ist_sachsen": False,
    }

    # Instanzverwaltung
    _instanzen = {}
    _lock = RLock()

    # ------------------------------------------------
    # Normale Framework-Berechnung
    # ------------------------------------------------

    def calculate(self, inputs: dict, settings: dict) -> dict:

        return _berechne(
            inputs,
            {
                **self.DEFAULT_SETTINGS,
                **settings,
            },
            datetime.datetime.now().year,
        )

    # ------------------------------------------------
    # Berechnung mit festem Steuerjahr
    # ------------------------------------------------

    def calculate_year(
        self,
        inputs: dict,
        settings: dict,
        jahr: int,
    ) -> dict:

        return _berechne(
            inputs,
            {
                **self.DEFAULT_SETTINGS,
                **settings,
            },
            jahr,
        )

    # ------------------------------------------------
    # Neue unabhängige Berechnungsinstanz
    # ------------------------------------------------

    def neue_instanz(
        self,
        instanz_id: str,
        inputs: dict,
        settings: Optional[dict] = None,
        jahr: Optional[int] = None,
    ):

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
                    **(settings or {}),
                },
                "jahr": jahr or datetime.datetime.now().year,
                "ergebnis": None,
                "fehler": None,
            }

    # ------------------------------------------------
    # Instanz aktualisieren
    # ------------------------------------------------

    def aktualisiere_instanz(
        self,
        instanz_id: str,
        inputs: Optional[dict] = None,
        settings: Optional[dict] = None,
        jahr: Optional[int] = None,
    ):

        with self._lock:

            if instanz_id not in self._instanzen:
                raise KeyError(instanz_id)

            daten = self._instanzen[instanz_id]

            if inputs:
                daten["inputs"].update(inputs)

            if settings:
                daten["settings"].update(settings)

            if jahr is not None:
                daten["jahr"] = jahr

            daten["ergebnis"] = None
            daten["fehler"] = None

    # ------------------------------------------------
    # Eine Instanz berechnen
    # ------------------------------------------------

    def berechne_instanz(self, instanz_id: str) -> dict:

        with self._lock:

            if instanz_id not in self._instanzen:
                raise KeyError(instanz_id)

            daten = self._instanzen[instanz_id].copy()

        try:

            ergebnis = self.calculate_year(
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

    # ------------------------------------------------
    # Alle Instanzen parallel berechnen
    # ------------------------------------------------

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

    # ------------------------------------------------
    # Einzelne Instanz abrufen
    # ------------------------------------------------

    def instanz(self, instanz_id: str) -> dict:

        with self._lock:

            if instanz_id not in self._instanzen:
                raise KeyError(instanz_id)

            return dict(self._instanzen[instanz_id])

    # ------------------------------------------------
    # Ergebnis abrufen
    # ------------------------------------------------

    def ergebnis(self, instanz_id: str):

        return self.instanz(instanz_id)["ergebnis"]

    # ------------------------------------------------
    # Alle Instanzen abrufen
    # ------------------------------------------------

    def alle_instanzen(self) -> dict:

        with self._lock:
            return {
                key: dict(value)
                for key, value in self._instanzen.items()
            }

    # ------------------------------------------------
    # Instanz löschen
    # ------------------------------------------------

    def entferne_instanz(self, instanz_id: str):

        with self._lock:
            return self._instanzen.pop(instanz_id, None) is not None

    # ------------------------------------------------
    # Alle Instanzen löschen
    # ------------------------------------------------

    def alle_entfernen(self):

        with self._lock:
            self._instanzen.clear()
