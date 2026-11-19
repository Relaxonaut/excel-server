# modules/haushaltsaufteilung.py

from core.base import Field, Module


class Haushaltsaufteilung(Module):
    """Verteilt gemeinsame Haushaltskosten auf zwei Personen."""

    id = "haushaltsaufteilung"
    name = "Gemeinsame Haushaltskosten aufteilen"
    description = (
        "Teilt gemeinsame Kosten hälftig, nach Einkommensanteil oder "
        "mit einem frei gewählten Anteil für Person A auf."
    )
    aktiv_standard = True

    inputs = [
        Field("netto_person_a", "Nettoeinkommen Person A", "float", default=2500, example=2500, cell="N2"),
        Field("netto_person_b", "Nettoeinkommen Person B", "float", default=2000, example=2000, cell="N3"),
        Field("gemeinsame_kosten", "Gemeinsame Monatskosten", "float", default=1800, example=1800, cell="N4"),
        Field("aufteilung", "Aufteilungsmethode", "select", default="Einkommensanteil", example="Einkommensanteil", cell="N5", options=["Einkommensanteil", "50/50", "Individueller Anteil"]),
        Field("anteil_person_a_prozent", "Anteil Person A (%)", "float", default=50, example=55, cell="N6", help="Wird nur bei 'Individueller Anteil' verwendet."),
    ]

    settings = []

    def calculate(self, inputs: dict, settings: dict) -> dict:
        income_a = float(inputs["netto_person_a"] or 0)
        income_b = float(inputs["netto_person_b"] or 0)
        costs = float(inputs["gemeinsame_kosten"] or 0)
        custom_a = float(inputs["anteil_person_a_prozent"] or 0)
        if min(income_a, income_b, costs) < 0:
            raise ValueError("Einkommen und gemeinsame Kosten dürfen nicht negativ sein.")

        method = inputs["aufteilung"]
        if method == "Einkommensanteil":
            total_income = income_a + income_b
            share_a = (income_a / total_income) if total_income > 0 else 0.5
        elif method == "50/50":
            share_a = 0.5
        elif method == "Individueller Anteil":
            if not 0 <= custom_a <= 100:
                raise ValueError("Der individuelle Anteil für Person A muss zwischen 0 und 100 Prozent liegen.")
            share_a = custom_a / 100
        else:
            raise ValueError("Bitte eine gültige Aufteilungsmethode auswählen.")

        share_b = 1 - share_a
        payment_a = costs * share_a
        payment_b = costs * share_b
        return {
            "anteil_person_a_prozent": round(share_a * 100, 2),
            "anteil_person_b_prozent": round(share_b * 100, 2),
            "zahlung_person_a": round(payment_a, 2),
            "zahlung_person_b": round(payment_b, 2),
            "rest_person_a": round(income_a - payment_a, 2),
            "rest_person_b": round(income_b - payment_b, 2),
        }
