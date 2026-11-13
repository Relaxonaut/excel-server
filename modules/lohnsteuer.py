
# modules/lohnsteuer.py

from lohnsteuer_bmf import berechne_lohnsteuer_pap

from core.base import Field, Module


class Lohnsteuer(Module):

    id = "lohnsteuer"
    name = "Lohnsteuer (BMF-PAP)"
    description = (
        "Berechnet Lohnsteuer, Solidaritätszuschlag und "
        "gegebenenfalls Kirchensteuer anhand des BMF-PAP."
    )

    aktiv_standard = True

    # -----------------------------------------------
    # Excel-Eingaben
    # -----------------------------------------------

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

    # -----------------------------------------------
    # Globale Einstellungen
    # -----------------------------------------------

    settings = [
        Field(
            "kv_zusatzbeitrag",
            "KV-Zusatzbeitrag (%)",
            "float",
            default=2.9,
        ),
        Field(
            "mit_kirchensteuer",
            "Mit Kirchensteuer",
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

    # -----------------------------------------------
    # Berechnung
    # -----------------------------------------------

    def calculate(self, inputs: dict, settings: dict) -> dict:

        brutto = float(inputs["brutto_monat"])
        steuerklasse = int(inputs["steuerklasse"])
        kinder = float(inputs["kinder"])

        if brutto <= 0:
            raise ValueError("Monatsbrutto muss größer als 0 sein.")

        if kinder < 0:
            raise ValueError("Kinderfreibetrag darf nicht negativ sein.")

        if steuerklasse not in range(1, 7):
            raise ValueError("Ungültige Steuerklasse.")

        if not 0 <= settings["kv_zusatzbeitrag"] <= 100:
            raise ValueError("Ungültiger KV-Zusatzbeitrag.")

        kinderwert = (
            int(kinder)
            if kinder.is_integer()
            else kinder
        )

        ergebnis = berechne_lohnsteuer_pap(
            brutto_jahr=int(round(brutto * 12)),
            steuerklasse=steuerklasse,
            bundesland=inputs["bundesland"],
            mit_kirchensteuer=settings["mit_kirchensteuer"],
            kinder=kinderwert,
            geburtsjahr=int(inputs["geburtsjahr"]),
            kv_zusatzbeitrag=settings["kv_zusatzbeitrag"],
            ist_sachsen=settings["ist_sachsen"],
            jahr=2026,
        )

        if isinstance(ergebnis, dict):
            return ergebnis

        from dataclasses import asdict, is_dataclass

        if is_dataclass(ergebnis):
            return asdict(ergebnis)

        if hasattr(ergebnis, "__dict__"):
            return vars(ergebnis)

        return {"ergebnis": ergebnis}
