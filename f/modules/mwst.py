
from f.core.base import Field, Module, Output


class Mehrwertsteuer(Module):
    id = "mwst"
    name = "Mehrwertsteuer"
    description = "Beispielmodul: Netto -> Steuer und Brutto"

    inputs = [Field("netto", "Nettobetrag", "float", example=100)]
    settings = [Field("satz", "Steuersatz (%)", "float", default=19)]
    outputs = [Output("steuer", "Steuerbetrag"), Output("brutto", "Bruttobetrag")]

    def calculate(self, i, s):
        steuer = i["netto"] * s["satz"] / 100
        return {"steuer": round(steuer, 2), "brutto": round(i["netto"] + steuer, 2)}
