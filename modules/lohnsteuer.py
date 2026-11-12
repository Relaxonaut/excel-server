import datetime
from dataclasses import asdict, is_dataclass

from lohnsteuer_bmf import berechne_lohnsteuer_pap

from f.core import Field, Module


def _to_dict(ergebnis) -> dict:
    """Die Bibliothek liefert ein dict; fuer den Fall der Faelle wird alles zu einem dict."""
    if isinstance(ergebnis, dict):
        return ergebnis
    if is_dataclass(ergebnis):
        return asdict(ergebnis)
    if hasattr(ergebnis, "__dict__"):
        return dict(vars(ergebnis))
    return {"ergebnis": ergebnis}


class Lohnsteuer(Module):
    id = "lohnsteuer"
    name = "Lohnsteuer (BMF-PAP)"
    description = "Berechnet die Lohnsteuer aus Monatsbrutto, Steuerklasse usw."

    # Standard-Zellen = die aus deinem urspruenglichen Skript (im UI aenderbar)
    inputs = [
        Field("brutto_monat", "Monatsbrutto", "float", example=3500, cell="G20"),
        Field("steuerklasse", "Steuerklasse", "int", example=1, cell="C20"),
        Field("bundesland", "Bundesland", "text", example="Hamburg", cell="E20",
              help="Ausgeschrieben, z.B. Nordrhein-Westfalen"),
        Field("kinder", "Kinder", "float", default=0, example=0, cell="D20"),
        Field("geburtsjahr", "Geburtsjahr", "int", example=1990, cell="F20"),
    ]

    settings = [
        Field("kv_zusatzbeitrag", "KV-Zusatzbeitrag (%)", "float", default=2.9),
        Field("mit_kirchensteuer", "Mit Kirchensteuer", "bool", default=False),
        Field("ist_sachsen", "Sachsen (Pflegeversicherung)", "bool", default=False),
    ]
    # outputs bewusst leer: werden aus dem Rueckgabewert der Bibliothek ermittelt

    def calculate(self, i: dict, s: dict) -> dict:
        kinder = int(i["kinder"]) if i["kinder"] == int(i["kinder"]) else i["kinder"]
        ergebnis = berechne_lohnsteuer_pap(
            brutto_jahr=int(i["brutto_monat"]) * 12,
            steuerklasse=i["steuerklasse"],
            bundesland=i["bundesland"],
            mit_kirchensteuer=s["mit_kirchensteuer"],
            kinder=kinder,
            geburtsjahr=i["geburtsjahr"],
            kv_zusatzbeitrag=s["kv_zusatzbeitrag"],
            ist_sachsen=s["ist_sachsen"],
            jahr=datetime.datetime.now().year,
        )
        return _to_dict(ergebnis)
