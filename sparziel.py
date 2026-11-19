# modules/sparziel.py

from core.base import Field, Module


class Sparziel(Module):
    """Berechnet die Dauer bis zum Sparziel inklusive optionaler Verzinsung."""

    id = "sparziel"
    name = "Sparziel-Rechner"
    description = (
        "Schätzt, nach wie vielen Monaten ein Sparziel mit regelmäßigen "
        "Einzahlungen und angenommener jährlicher Verzinsung erreicht wird."
    )
    aktiv_standard = True

    inputs = [
        Field("zielbetrag", "Gewünschter Zielbetrag", "float", default=10000, example=10000, cell="J2"),
        Field("aktuelles_guthaben", "Aktuelles Guthaben", "float", default=1000, example=1000, cell="J3"),
        Field("monatliche_sparrate", "Monatliche Sparrate", "float", default=250, example=250, cell="J4"),
        Field("zins_pa_prozent", "Angenommener Zinssatz p.a. (%)", "float", default=0, example=2, cell="J5", help="Planungsannahme, keine Zinszusage. Steuern und Gebühren sind nicht berücksichtigt."),
    ]

    settings = []

    def calculate(self, inputs: dict, settings: dict) -> dict:
        target = float(inputs["zielbetrag"] or 0)
        balance = float(inputs["aktuelles_guthaben"] or 0)
        contribution = float(inputs["monatliche_sparrate"] or 0)
        annual_rate = float(inputs["zins_pa_prozent"] or 0)
        if target <= 0:
            raise ValueError("Das Sparziel muss größer als 0 sein.")
        if balance < 0 or contribution < 0:
            raise ValueError("Guthaben und Sparrate dürfen nicht negativ sein.")
        if annual_rate < 0 or annual_rate > 100:
            raise ValueError("Der Zinssatz muss zwischen 0 und 100 Prozent liegen.")

        if balance >= target:
            return {
                "fehlbetrag": 0.0,
                "monate_bis_ziel": 0,
                "endguthaben_im_zielmonat": round(balance, 2),
                "zinsannahme_monatlich_prozent": round(((1 + annual_rate / 100) ** (1 / 12) - 1) * 100, 4),
            }
        if contribution == 0 and (annual_rate == 0 or balance == 0):
            months_needed = None
            final_balance = balance
        else:
            monthly_rate = (1 + annual_rate / 100) ** (1 / 12) - 1
            months_needed = None
            final_balance = balance
            for month in range(1, 1201):
                final_balance = final_balance * (1 + monthly_rate) + contribution
                if final_balance >= target:
                    months_needed = month
                    break
            if months_needed is None:
                final_balance = None

        return {
            "fehlbetrag": round(max(target - balance, 0), 2),
            "monate_bis_ziel": months_needed,
            "endguthaben_im_zielmonat": round(final_balance, 2) if final_balance is not None else None,
            "zinsannahme_monatlich_prozent": round(((1 + annual_rate / 100) ** (1 / 12) - 1) * 100, 4),
        }
