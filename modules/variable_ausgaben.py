# modules/variable_ausgaben.py

from core.base import Field, Module


class VariableAusgaben(Module):
    """Überwacht ein einzelnes variables Ausgabenbudget im laufenden Monat."""

    id = "variable_ausgaben"
    name = "Variable Ausgaben"
    description = (
        "Vergleicht das Monatsbudget einer Kategorie mit den bisherigen "
        "Ausgaben und prognostiziert den Monatsendstand."
    )
    aktiv_standard = True

    inputs = [
        Field("kategorie", "Ausgabenkategorie", "select", default="Lebensmittel", example="Lebensmittel", cell="F2", options=["Lebensmittel", "Drogerie", "Mobilität", "Freizeit", "Restaurants", "Kleidung", "Haustiere", "Kinder", "Sonstiges"]),
        Field("monatsbudget", "Monatsbudget in Euro", "float", default=0, example=500, cell="F3"),
        Field("bisher_ausgegeben", "Bisher ausgegeben in Euro", "float", default=0, example=220, cell="F4"),
        Field("tage_vergangen", "Vergangene Tage im Monat", "int", default=15, example=15, cell="F5"),
        Field("tage_im_monat", "Tage im Monat", "int", default=30, example=30, cell="F6"),
    ]

    settings = []

    def calculate(self, inputs: dict, settings: dict) -> dict:
        budget = float(inputs["monatsbudget"] or 0)
        spent = float(inputs["bisher_ausgegeben"] or 0)
        elapsed = int(inputs["tage_vergangen"] or 0)
        days = int(inputs["tage_im_monat"] or 0)
        if budget < 0 or spent < 0:
            raise ValueError("Budget und bisherige Ausgaben dürfen nicht negativ sein.")
        if days < 1 or elapsed < 0 or elapsed > days:
            raise ValueError("Tage im Monat müssen mindestens 1 sein; vergangene Tage müssen zwischen 0 und Monatstagen liegen.")

        remaining = budget - spent
        forecast = (spent / elapsed * days) if elapsed > 0 else 0.0
        daily = remaining / max(days - elapsed, 1) if elapsed < days else remaining
        deviation = budget - forecast
        if remaining < -0.005 or deviation < -0.005:
            status = "Voraussichtlich über Budget" if remaining >= 0 else "Budget bereits überschritten"
        else:
            status = "Im Budget"

        return {
            "kategorie": str(inputs["kategorie"]),
            "restbudget": round(remaining, 2),
            "verfuegbar_pro_resttag": round(daily, 2),
            "prognose_monatsende": round(forecast, 2),
            "abweichung_zum_budget": round(deviation, 2),
            "budget_status": status,
        }
