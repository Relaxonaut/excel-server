# modules/haushaltsbudget.py

from core.base import Field, Module


class Haushaltsbudget(Module):
    """Monatsübersicht über Einnahmen, Ausgaben und verfügbares Geld."""

    id = "haushaltsbudget"
    name = "Haushaltsbudget"
    description = (
        "Stellt monatliche Nettoeinnahmen den festen und variablen "
        "Haushaltsausgaben sowie dem geplanten Sparbetrag gegenüber."
    )
    aktiv_standard = True

    inputs = [
        Field("netto_einkommen", "Nettoeinkommen Haushalt", "float", default=0, example=3500, cell="B2", help="Monatliches Nettoeinkommen aller Haushaltsmitglieder.") ,
        Field("sonstige_einnahmen", "Sonstige Einnahmen", "float", default=0, example=150, cell="B3"),
        Field("warmmiete", "Warmmiete / Kreditrate", "float", default=0, example=1100, cell="B5"),
        Field("energie", "Strom und weitere Energie", "float", default=0, example=110, cell="B6"),
        Field("internet_telefon", "Internet und Telefon", "float", default=0, example=55, cell="B7"),
        Field("versicherungen", "Versicherungen", "float", default=0, example=120, cell="B8"),
        Field("mobilitaet_fest", "Feste Mobilitätskosten", "float", default=0, example=180, cell="B9"),
        Field("lebensmittel", "Lebensmittel", "float", default=0, example=450, cell="B11"),
        Field("drogerie_haushalt", "Drogerie und Haushalt", "float", default=0, example=70, cell="B12"),
        Field("mobilitaet_variabel", "Variable Mobilität", "float", default=0, example=80, cell="B13"),
        Field("gesundheit_kinder", "Gesundheit und Kinder", "float", default=0, example=100, cell="B14"),
        Field("freizeit", "Freizeit und Restaurants", "float", default=0, example=180, cell="B15"),
        Field("abos_sonstiges", "Abos und sonstige Ausgaben", "float", default=0, example=60, cell="B16"),
        Field("geplantes_sparen", "Geplantes Sparen / Rücklagen", "float", default=0, example=300, cell="B18"),
    ]

    settings = []

    def calculate(self, inputs: dict, settings: dict) -> dict:
        values = {key: float(inputs[key] or 0) for key in (
            "netto_einkommen", "sonstige_einnahmen", "warmmiete", "energie",
            "internet_telefon", "versicherungen", "mobilitaet_fest", "lebensmittel",
            "drogerie_haushalt", "mobilitaet_variabel", "gesundheit_kinder", "freizeit",
            "abos_sonstiges", "geplantes_sparen",
        )}
        if any(value < 0 for value in values.values()):
            raise ValueError("Einnahmen, Ausgaben und Sparbeträge dürfen nicht negativ sein.")

        einkommen = values["netto_einkommen"] + values["sonstige_einnahmen"]
        fix = sum(values[k] for k in ("warmmiete", "energie", "internet_telefon", "versicherungen", "mobilitaet_fest"))
        variabel = sum(values[k] for k in ("lebensmittel", "drogerie_haushalt", "mobilitaet_variabel", "gesundheit_kinder", "freizeit", "abos_sonstiges"))
        ausgaben = fix + variabel
        frei = einkommen - ausgaben - values["geplantes_sparen"]

        return {
            "einnahmen_gesamt": round(einkommen, 2),
            "fixkosten_gesamt": round(fix, 2),
            "variable_ausgaben": round(variabel, 2),
            "ausgaben_gesamt": round(ausgaben, 2),
            "geplantes_sparen": round(values["geplantes_sparen"], 2),
            "frei_verfuegbar": round(frei, 2),
            "sparquote_prozent": round(values["geplantes_sparen"] / einkommen * 100, 2) if einkommen else 0.0,
            "wohnkostenquote_prozent": round(values["warmmiete"] / einkommen * 100, 2) if einkommen else 0.0,
            "budget_status": "Überschuss" if frei > 0.005 else ("Ausgeglichen" if frei >= -0.005 else "Defizit"),
        }
