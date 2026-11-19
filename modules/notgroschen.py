# modules/notgroschen.py

from math import ceil

from core.base import Field, Module


class Notgroschen(Module):
    """Plant eine Liquiditätsreserve anhand der notwendigen Monatsausgaben."""

    id = "notgroschen"
    name = "Notgroschen"
    description = (
        "Ermittelt die gewünschte Notfallreserve, den noch fehlenden Betrag "
        "und die voraussichtliche Ansparzeit."
    )
    aktiv_standard = True

    inputs = [
        Field("notwendige_monatskosten", "Notwendige Monatskosten", "float", default=1500, example=1500, cell="H2"),
        Field("aktueller_notgroschen", "Bereits vorhandene Rücklage", "float", default=0, example=2500, cell="H3"),
        Field("ziel_monate", "Abdeckung in Monaten", "int", default=3, example=3, cell="H4"),
        Field("monatliche_sparrate", "Monatliche Einzahlung", "float", default=100, example=200, cell="H5"),
    ]

    settings = []

    def calculate(self, inputs: dict, settings: dict) -> dict:
        costs = float(inputs["notwendige_monatskosten"] or 0)
        current = float(inputs["aktueller_notgroschen"] or 0)
        months = int(inputs["ziel_monate"] or 0)
        saving = float(inputs["monatliche_sparrate"] or 0)
        if costs < 0 or current < 0 or saving < 0:
            raise ValueError("Kosten, Rücklage und Sparrate dürfen nicht negativ sein.")
        if months < 1 or months > 60:
            raise ValueError("Das Reserve-Ziel muss zwischen 1 und 60 Monaten liegen.")

        target = costs * months
        gap = max(target - current, 0.0)
        months_to_goal = 0 if gap <= 0 else (ceil(gap / saving) if saving > 0 else None)

        return {
            "zielbetrag": round(target, 2),
            "fehlender_betrag": round(gap, 2),
            "fortschritt_prozent": round(min(current / target * 100, 100.0), 2) if target else 100.0,
            "monate_bis_ziel": months_to_goal,
            "monatliche_sparrate": round(saving, 2),
        }
