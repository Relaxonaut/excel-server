# modules/fixkosten.py

from core.base import Field, Module


class Fixkosten(Module):
    """Rechnet wiederkehrende Zahlungen auf Monats- und Jahreswerte um."""

    id = "fixkosten"
    name = "Fixkosten und Jahreszahlungen"
    description = (
        "Berechnet den monatlichen Rücklagenbedarf für wiederkehrende "
        "Kosten wie Versicherungen, Beiträge, Abos oder jährliche Rechnungen."
    )
    aktiv_standard = True

    inputs = [
        Field("kostenart", "Kostenart", "select", default="Versicherung", example="Versicherung", cell="D2", options=["Versicherung", "Abo", "Beitrag", "Energie", "Kfz", "Steuer", "Sonstiges"]),
        Field("betrag", "Zahlbetrag in Euro", "float", default=0, example=600, cell="D3"),
        Field("intervall", "Zahlungsintervall", "select", default="jährlich", example="jährlich", cell="D4", options=["wöchentlich", "monatlich", "vierteljährlich", "halbjährlich", "jährlich"]),
    ]

    settings = []

    def calculate(self, inputs: dict, settings: dict) -> dict:
        betrag = float(inputs["betrag"] or 0)
        if betrag < 0:
            raise ValueError("Der Zahlbetrag darf nicht negativ sein.")

        factors = {
            "wöchentlich": 52.0,
            "monatlich": 12.0,
            "vierteljährlich": 4.0,
            "halbjährlich": 2.0,
            "jährlich": 1.0,
        }
        intervall = inputs["intervall"]
        if intervall not in factors:
            raise ValueError("Bitte ein gültiges Zahlungsintervall auswählen.")

        jahreskosten = betrag * factors[intervall]
        monatlich = jahreskosten / 12.0
        return {
            "kostenart": str(inputs["kostenart"]),
            "jahreskosten": round(jahreskosten, 2),
            "monatlicher_ruecklagenbedarf": round(monatlich, 2),
            "durchschnitt_pro_woche": round(jahreskosten / 52.0, 2),
        }
