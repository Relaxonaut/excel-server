# modules/schuldenabbau.py

from core.base import Field, Module


class Schuldenabbau(Module):
    """Simuliert die Tilgung einer Schuld mit gleichbleibender Monatsrate."""

    id = "schuldenabbau"
    name = "Schuldenabbau-Rechner"
    description = (
        "Berechnet Tilgungsdauer und voraussichtliche Zinskosten für einen "
        "Kredit oder Dispokredit bei gleichbleibender monatlicher Zahlung."
    )
    aktiv_standard = True

    inputs = [
        Field("restschuld", "Aktuelle Restschuld", "float", default=5000, example=5000, cell="L2"),
        Field("zinssatz_pa_prozent", "Sollzins p.a. (%)", "float", default=8, example=8, cell="L3"),
        Field("monatsrate", "Monatliche Zahlung", "float", default=200, example=200, cell="L4"),
    ]

    settings = []

    def calculate(self, inputs: dict, settings: dict) -> dict:
        balance = float(inputs["restschuld"] or 0)
        annual_rate = float(inputs["zinssatz_pa_prozent"] or 0)
        payment = float(inputs["monatsrate"] or 0)
        if balance < 0 or payment < 0:
            raise ValueError("Restschuld und Monatsrate dürfen nicht negativ sein.")
        if annual_rate < 0 or annual_rate > 100:
            raise ValueError("Der Sollzins muss zwischen 0 und 100 Prozent liegen.")
        if balance == 0:
            return {"monate_bis_schuldenfrei": 0, "zinskosten_gesamt": 0.0, "rueckzahlung_gesamt": 0.0, "zinsen_erster_monat": 0.0, "tilgung_erster_monat": 0.0, "tilgungsstatus": "Schuldenfrei"}
        if payment == 0:
            return {"monate_bis_schuldenfrei": None, "zinskosten_gesamt": None, "rueckzahlung_gesamt": None, "zinsen_erster_monat": round(balance * annual_rate / 1200, 2), "tilgung_erster_monat": 0.0, "tilgungsstatus": "Keine monatliche Zahlung"}

        monthly_rate = annual_rate / 1200
        first_interest = balance * monthly_rate
        first_principal = max(payment - first_interest, 0.0)
        if monthly_rate > 0 and payment <= first_interest:
            return {"monate_bis_schuldenfrei": None, "zinskosten_gesamt": None, "rueckzahlung_gesamt": None, "zinsen_erster_monat": round(first_interest, 2), "tilgung_erster_monat": round(first_principal, 2), "tilgungsstatus": "Rate deckt die Zinsen nicht"}

        total_interest = 0.0
        total_paid = 0.0
        first_interest_actual = 0.0
        first_principal_actual = 0.0
        months = 0
        while balance > 0 and months < 1200:
            interest = balance * monthly_rate
            due = balance + interest
            paid = min(payment, due)
            principal = paid - interest
            if months == 0:
                first_interest_actual = interest
                first_principal_actual = principal
            total_interest += interest
            total_paid += paid
            balance = max(balance - principal, 0.0)
            months += 1

        complete = balance <= 0.005
        return {
            "monate_bis_schuldenfrei": months if complete else None,
            "zinskosten_gesamt": round(total_interest, 2) if complete else None,
            "rueckzahlung_gesamt": round(total_paid, 2) if complete else None,
            "zinsen_erster_monat": round(first_interest_actual, 2),
            "tilgung_erster_monat": round(first_principal_actual, 2),
            "tilgungsstatus": "Schuldenfrei" if complete else "Laufzeit über 100 Jahre oder nicht amortisierbar",
        }
