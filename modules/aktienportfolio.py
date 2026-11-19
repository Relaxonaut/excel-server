"""Transaction-based securities portfolio engine with FIFO lots and rebalancing.

Transaction table (10 columns):
Datum | Wertpapier_ID | Name | Vorgang | Stück | Kurs/Betrag | Gebühren |
FX_EUR_pro_Fremdwährung | Quellensteuer_EUR | Notiz

Quote/target table (5 columns):
Wertpapier_ID | Kurs_EUR | Anlageklasse | Name | Zielgewicht_%

Vorgang: KAUF, VERKAUF, DIVIDENDE, GEBUEHR, SPLIT.
All trading prices and fees use the transaction currency; FX converts them to EUR.
"""
from __future__ import annotations

import datetime as dt
import math

from core.base import Field, Module
from modules._table_engine import (
    FlexibleTableMixin,
    integer,
    is_header,
    json_text,
    normalise,
    number,
    parse_date,
    round_eur,
)


def _xirr(cashflows):
    """Solve an annualized money-weighted return using a bracketed bisection."""
    flows = [(d, float(v)) for d, v in cashflows if v is not None and abs(float(v)) > 1e-9]
    if len(flows) < 2 or not any(v < 0 for _, v in flows) or not any(v > 0 for _, v in flows):
        return None
    flows.sort(key=lambda x: x[0])
    origin = flows[0][0]
    timed = [((d - origin).days / 365.0, v) for d, v in flows]

    def npv(rate):
        if rate <= -1:
            return float("inf")
        return sum(value / ((1.0 + rate) ** years) for years, value in timed)

    low, high = -0.999, 10.0
    f_low, f_high = npv(low), npv(high)
    # Expand positive side for unusually high returns.
    while f_low * f_high > 0 and high < 1000:
        high *= 2
        f_high = npv(high)
    if f_low * f_high > 0:
        return None
    for _ in range(180):
        mid = (low + high) / 2
        f_mid = npv(mid)
        if abs(f_mid) < 1e-8:
            return mid
        if f_low * f_mid <= 0:
            high, f_high = mid, f_mid
        else:
            low, f_low = mid, f_mid
    return (low + high) / 2


def _month_growth(pv, monthly_payment, annual_rate, months):
    r = annual_rate / 100.0 / 12.0
    if abs(r) < 1e-12:
        return round_eur(pv + monthly_payment * months)
    factor = (1 + r) ** months
    return round_eur(pv * factor + monthly_payment * ((factor - 1) / r))


class Aktienportfolio(FlexibleTableMixin, Module):
    id = "aktienportfolio"
    name = "Aktien- und Depot-Engine"
    description = (
        "Verarbeitet Transaktionen beliebiger Länge mit FIFO-Lots, realisierten und "
        "unrealisierten Ergebnissen, Dividenden, Fremdwährungen, XIRR, Zielquoten, "
        "Rebalancing und langfristigen Sparplan-Szenarien."
    )
    aktiv_standard = True

    TABLE_FIELDS = {"transaktionen_start": 10, "kurse_start": 5}

    inputs = [
        Field("transaktionen_start", "Transaktionsbuch: Startzelle (10 Spalten)", "text", default="A28", example="A28", cell="A28", help="Datum | ID/ISIN | Name | KAUF/VERKAUF/DIVIDENDE/GEBUEHR/SPLIT | Stück | Kurs/Betrag | Gebühren | FX nach EUR | Quellensteuer EUR | Notiz"),
        Field("kurse_start", "Kurs- und Zielgewichtstabelle: Startzelle (5 Spalten)", "text", default="L28", example="L28", cell="L28", help="ID/ISIN | aktueller Kurs EUR | AKTIE/AKTIENFONDS/MISCHFONDS/SONSTIG | Name | Zielgewicht %"),
        Field("bewertungsstichtag", "Bewertungsstichtag (JJJJ-MM-TT; leer = letztes Transaktionsdatum)", "text", default="", example="2026-11-20", cell="J25"),
        Field("sparrate_monatlich", "Geplante zusätzliche Sparrate pro Monat (€)", "float", default=0, example=250, cell="K25"),
        Field("prognose_jahre", "Sparplan-Prognose (Jahre)", "int", default=10, example=10, cell="K26"),
        Field("rendite_erwartet_pct", "Renditeannahme p.a. (%) – Szenario, keine Prognose", "float", default=5, example=5, cell="K27"),
    ]
    settings = []

    def calculate(self, inputs: dict, settings: dict) -> dict:
        raw_transactions = inputs.get("_transaktionen_start_rows", [])
        raw_quotes = inputs.get("_kurse_start_rows", [])
        transactions = []
        last_transaction_date = None
        for row_no, row in enumerate(raw_transactions, start=1):
            if not row or is_header(row, ("Datum", "Wertpapier_ID", "Wertpapier-ID", "ISIN")):
                continue
            date_value = parse_date(row[0] if len(row) > 0 else None, f"Transaktion {row_no}: Datum")
            if date_value is None:
                raise ValueError(f"Transaktion {row_no}: Datum fehlt.")
            last_transaction_date = max(last_transaction_date or date_value, date_value)
            asset_id = str(row[1] or "").strip().upper() if len(row) > 1 else ""
            if not asset_id:
                raise ValueError(f"Transaktion {row_no}: Wertpapier-ID/ISIN fehlt.")
            name = str(row[2] or asset_id).strip() if len(row) > 2 else asset_id
            action = normalise(row[3] if len(row) > 3 else "")
            quantity = number(row[4] if len(row) > 4 else None, 0, f"{asset_id}: Stück")
            price_or_amount = number(row[5] if len(row) > 5 else None, 0, f"{asset_id}: Kurs/Betrag")
            fees = number(row[6] if len(row) > 6 else None, 0, f"{asset_id}: Gebühren")
            fx = number(row[7] if len(row) > 7 else None, 1, f"{asset_id}: FX")
            withholding = number(row[8] if len(row) > 8 else None, 0, f"{asset_id}: Quellensteuer")
            note = str(row[9] or "").strip() if len(row) > 9 else ""
            if action not in {"KAUF", "VERKAUF", "DIVIDENDE", "GEBUEHR", "SPLIT"}:
                raise ValueError(f"Transaktion {row_no}: Vorgang {action!r} ist nicht zulässig.")
            if fx <= 0 or fees < 0 or withholding < 0:
                raise ValueError(f"Transaktion {row_no}: FX muss > 0 sein; Gebühren und Quellensteuer dürfen nicht negativ sein.")
            if action in {"KAUF", "VERKAUF"} and (quantity <= 0 or price_or_amount < 0):
                raise ValueError(f"Transaktion {row_no}: Kauf/Verkauf benötigt positive Stückzahl und nichtnegativen Kurs.")
            if action == "SPLIT" and price_or_amount <= 0 and quantity <= 0:
                raise ValueError(f"Transaktion {row_no}: SPLIT benötigt den Splitfaktor in Spalte F oder E.")
            transactions.append({
                "date": date_value, "asset_id": asset_id, "name": name, "action": action,
                "quantity": quantity, "price": price_or_amount, "fees": fees, "fx": fx,
                "withholding": withholding, "note": note, "row_no": row_no,
            })
        transactions.sort(key=lambda x: (x["date"], x["row_no"]))

        quotes = {}
        for row_no, row in enumerate(raw_quotes, start=1):
            if not row or is_header(row, ("Wertpapier_ID", "Wertpapier-ID", "ISIN")):
                continue
            asset_id = str(row[0] or "").strip().upper() if len(row) > 0 else ""
            if not asset_id:
                continue
            price = number(row[1] if len(row) > 1 else None, 0, f"{asset_id}: aktueller Kurs")
            asset_type = normalise(row[2] if len(row) > 2 else "AKTIE") or "AKTIE"
            if asset_type not in {"AKTIE", "AKTIENFONDS", "MISCHFONDS", "ANLEIHE", "SONSTIG"}:
                raise ValueError(f"{asset_id}: Anlageklasse muss AKTIE, AKTIENFONDS, MISCHFONDS, ANLEIHE oder SONSTIG sein.")
            name = str(row[3] or asset_id).strip() if len(row) > 3 else asset_id
            target = number(row[4] if len(row) > 4 else None, 0, f"{asset_id}: Zielgewicht")
            if price < 0 or not 0 <= target <= 100:
                raise ValueError(f"{asset_id}: Kurs darf nicht negativ und Zielgewicht muss 0–100 % sein.")
            quotes[asset_id] = {"price": price, "type": asset_type, "name": name, "target": target}

        valuation_date = parse_date(inputs.get("bewertungsstichtag"), "Bewertungsstichtag") or last_transaction_date
        future_transactions = [tx for tx in transactions if valuation_date and tx["date"] > valuation_date]
        if future_transactions:
            transactions = [tx for tx in transactions if tx["date"] <= valuation_date]
        lots = {}
        names = {}
        realized = {}
        dividend_gross = 0.0
        dividend_net = 0.0
        withholding_total = 0.0
        external_fees = 0.0
        dividend_fees = 0.0
        gross_buys = 0.0
        sale_proceeds = 0.0
        cashflows = []
        action_ledger = []

        for tx in transactions:
            asset_id = tx["asset_id"]
            names[asset_id] = tx["name"]
            quotes.setdefault(asset_id, {"price": None, "type": "AKTIE", "name": tx["name"], "target": 0.0})
            asset_lots = lots.setdefault(asset_id, [])
            date_value = tx["date"]
            fx = tx["fx"]
            if tx["action"] == "KAUF":
                total_cost = round_eur(tx["quantity"] * tx["price"] * fx + tx["fees"] * fx)
                unit_cost = total_cost / tx["quantity"]
                asset_lots.append({"date": date_value.isoformat(), "quantity": tx["quantity"], "unit_cost_eur": unit_cost})
                gross_buys += total_cost
                cashflows.append((date_value, -total_cost))
                action_ledger.append({"datum": date_value.isoformat(), "id": asset_id, "vorgang": "KAUF", "stück": tx["quantity"], "cashflow_eur": -total_cost})
            elif tx["action"] == "VERKAUF":
                available = sum(lot["quantity"] for lot in asset_lots)
                if tx["quantity"] > available + 1e-8:
                    raise ValueError(f"{asset_id}: Verkauf von {tx['quantity']:g} Stück, aber nur {available:g} Stück laut FIFO-Bestand vorhanden.")
                remaining_to_sell = tx["quantity"]
                cost_basis = 0.0
                while remaining_to_sell > 1e-8:
                    lot = asset_lots[0]
                    take = min(lot["quantity"], remaining_to_sell)
                    cost_basis += take * lot["unit_cost_eur"]
                    lot["quantity"] -= take
                    remaining_to_sell -= take
                    if lot["quantity"] <= 1e-8:
                        asset_lots.pop(0)
                proceeds = round_eur(tx["quantity"] * tx["price"] * fx - tx["fees"] * fx)
                gain = round_eur(proceeds - cost_basis)
                sale_proceeds += proceeds
                realized[asset_id] = round_eur(realized.get(asset_id, 0.0) + gain)
                cashflows.append((date_value, proceeds))
                action_ledger.append({"datum": date_value.isoformat(), "id": asset_id, "vorgang": "VERKAUF", "stück": tx["quantity"], "verkaufserlös_eur": proceeds, "fifo_anschaffungskosten_eur": round_eur(cost_basis), "realisierter_gewinn_eur": gain})
            elif tx["action"] == "DIVIDENDE":
                gross = round_eur(tx["price"] * fx)
                fee_eur = round_eur(tx["fees"] * fx)
                net = round_eur(gross - fee_eur - tx["withholding"])
                dividend_gross += gross
                dividend_net += net
                withholding_total += tx["withholding"]
                dividend_fees += fee_eur
                cashflows.append((date_value, net))
                action_ledger.append({"datum": date_value.isoformat(), "id": asset_id, "vorgang": "DIVIDENDE", "brutto_eur": gross, "quellensteuer_eur": round_eur(tx["withholding"]), "netto_eur": net})
            elif tx["action"] == "GEBUEHR":
                fee_eur = round_eur(tx["price"] * fx + tx["fees"] * fx)
                external_fees += fee_eur
                cashflows.append((date_value, -fee_eur))
                action_ledger.append({"datum": date_value.isoformat(), "id": asset_id, "vorgang": "GEBUEHR", "gebuehr_eur": fee_eur})
            elif tx["action"] == "SPLIT":
                factor = tx["price"] if tx["price"] > 0 else tx["quantity"]
                if factor <= 0:
                    raise ValueError(f"{asset_id}: ungültiger Splitfaktor.")
                for lot in asset_lots:
                    lot["quantity"] *= factor
                    lot["unit_cost_eur"] /= factor
                action_ledger.append({"datum": date_value.isoformat(), "id": asset_id, "vorgang": "SPLIT", "faktor": factor})

        position_rows = []
        current_value_total = 0.0
        current_value_complete = True
        total_cost_remaining = 0.0
        unrealized_total = 0.0
        priced_assets = 0
        total_units = 0
        target_sum = sum(q["target"] for q in quotes.values())
        for asset_id, asset_lots in lots.items():
            quantity = sum(lot["quantity"] for lot in asset_lots)
            if quantity <= 1e-8:
                continue
            cost_basis = sum(lot["quantity"] * lot["unit_cost_eur"] for lot in asset_lots)
            quote = quotes.get(asset_id, {"price": None, "type": "AKTIE", "name": names.get(asset_id, asset_id), "target": 0})
            price = quote["price"]
            market_value = round_eur(quantity * price) if price is not None else None
            unrealized = round_eur(market_value - cost_basis) if market_value is not None else None
            total_cost_remaining += cost_basis
            if market_value is None:
                current_value_complete = False
            else:
                current_value_total += market_value
                unrealized_total += unrealized
                priced_assets += 1
            total_units += quantity
            position_rows.append({
                "wertpapier_id": asset_id,
                "name": quote["name"] or names.get(asset_id, asset_id),
                "anlageklasse": quote["type"],
                "stueck": round(quantity, 8),
                "fifo_anschaffungskosten_eur": round_eur(cost_basis),
                "aktueller_kurs_eur": round(price, 6) if price is not None else None,
                "marktwert_eur": market_value,
                "unrealisierter_gewinn_eur": unrealized,
                "realisierter_gewinn_eur": round_eur(realized.get(asset_id, 0.0)),
                "zielgewicht_pct": quote["target"],
            })
        current_value_total = round_eur(current_value_total)
        total_cost_remaining = round_eur(total_cost_remaining)
        unrealized_total = round_eur(unrealized_total) if current_value_complete else None
        realized_total = round_eur(sum(realized.values()))
        dividend_gross = round_eur(dividend_gross)
        dividend_net = round_eur(dividend_net)
        external_fees = round_eur(external_fees)
        withholding_total = round_eur(withholding_total)
        total_gain = round_eur(realized_total + (unrealized_total or 0.0) + dividend_net - external_fees) if current_value_complete else None

        if current_value_complete and valuation_date:
            cashflows.append((valuation_date, current_value_total))
            money_weighted_return = _xirr(cashflows)
        else:
            money_weighted_return = None

        rebalancing = []
        concentration_warnings = []
        if current_value_total > 0 and current_value_complete:
            for position in position_rows:
                if position["marktwert_eur"] is None:
                    continue
                weight = position["marktwert_eur"] / current_value_total * 100.0
                position["istgewicht_pct"] = round(weight, 2)
                position["zielwert_eur"] = round_eur(current_value_total * position["zielgewicht_pct"] / 100.0)
                difference = round_eur(position["zielwert_eur"] - position["marktwert_eur"])
                position["abweichung_eur"] = difference
                position["rebalancing_stueck_approx"] = round(difference / position["aktueller_kurs_eur"], 6) if position["aktueller_kurs_eur"] else None
                rebalancing.append({
                    "wertpapier_id": position["wertpapier_id"], "name": position["name"],
                    "istgewicht_pct": round(weight, 2), "zielgewicht_pct": position["zielgewicht_pct"],
                    "zielwert_eur": position["zielwert_eur"], "abweichung_eur": difference,
                    "ungefaehre_stueckveraenderung": position["rebalancing_stueck_approx"],
                    "signal": "KAUFPRUEFEN" if difference > 5 else "VERKAUFPRUEFEN" if difference < -5 else "IM_BAND",
                })
                if weight >= 35:
                    concentration_warnings.append({"wertpapier_id": position["wertpapier_id"], "istgewicht_pct": round(weight, 2), "hinweis": "Einzelposition erreicht mindestens 35 % des bewerteten Portfolios."})
        if quotes and abs(target_sum - 100.0) > 1.0:
            concentration_warnings.append({"hinweis": f"Die eingetragenen Zielgewichte summieren sich auf {target_sum:.2f} % statt ungefähr 100 %."})
        if not current_value_complete:
            concentration_warnings.append({"hinweis": "Mindestens eine gehaltene Position hat keinen Kurs. Marktwert, Gesamtgewinn, XIRR und Rebalancing werden daher nicht vollständig ausgewiesen."})
        if future_transactions:
            concentration_warnings.append({"hinweis": f"{len(future_transactions)} Transaktion(en) nach dem Bewertungsstichtag wurden nicht berücksichtigt."})

        monthly_saving = number(inputs.get("sparrate_monatlich"), 0, "Sparrate")
        years = integer(inputs.get("prognose_jahre"), 10, "Prognosejahre")
        annual_return = number(inputs.get("rendite_erwartet_pct"), 5, "Renditeannahme")
        if monthly_saving < 0 or not 1 <= years <= 60 or not -99 <= annual_return <= 100:
            raise ValueError("Sparrate darf nicht negativ sein; Prognose 1–60 Jahre; Renditeannahme -99 bis 100 %.")
        forecast_base = current_value_total if current_value_complete else 0.0
        months = years * 12
        forecast = {
            "jahre": years,
            "startwert_eur": forecast_base,
            "sparrate_monatlich_eur": round_eur(monthly_saving),
            "annahme_rendite_pa_pct": annual_return,
            "negativ_szenario_eur": _month_growth(forecast_base, monthly_saving, max(-99.0, annual_return - 2.5), months),
            "basis_szenario_eur": _month_growth(forecast_base, monthly_saving, annual_return, months),
            "positiv_szenario_eur": _month_growth(forecast_base, monthly_saving, min(100.0, annual_return + 2.5), months),
            "hinweis": "Nominale Modellrechnung mit monatlicher Verzinsung und Sparraten am Monatsende; keine Renditeprognose, keine Steuern, Gebühren oder Inflation.",
        }
        if not transactions:
            concentration_warnings.append({"hinweis": "Keine Transaktionen gefunden. Prüfe die Transaktions-Startzelle."})

        return {
            "transaktionen_anzahl": len(transactions),
            "positionen_anzahl": len([p for p in position_rows if p["stueck"] > 0]),
            "bewertungsstichtag": valuation_date.isoformat() if valuation_date else "nicht verfügbar",
            "marktwert_eur": current_value_total if current_value_complete else None,
            "fifo_anschaffungskosten_rest_eur": total_cost_remaining,
            "unrealisierter_gewinn_eur": unrealized_total,
            "realisierter_gewinn_eur": realized_total,
            "dividenden_brutto_eur": dividend_gross,
            "dividenden_netto_eur": dividend_net,
            "quellensteuer_einbehalten_eur": withholding_total,
            "sonstige_gebuehren_eur": external_fees,
            "dividendengebuehren_eur": round_eur(dividend_fees),
            "brutto_kaufvolumen_eur": round_eur(gross_buys),
            "verkaufserloese_netto_eur": round_eur(sale_proceeds),
            "gesamtgewinn_inkl_dividenden_eur": total_gain,
            "xirr_pa_pct": round(money_weighted_return * 100, 4) if money_weighted_return is not None else None,
            "kursabdeckung_positionen": f"{priced_assets}/{len(position_rows)}",
            "zielgewichte_summe_pct": round(target_sum, 2),
            "positionen_json": json_text(position_rows),
            "rebalancing_json": json_text(rebalancing),
            "transaktionsjournal_json": json_text(action_ledger),
            "prognose_json": json_text(forecast),
            "risikohinweise_json": json_text(concentration_warnings),
        }
