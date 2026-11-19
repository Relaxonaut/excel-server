"""Advanced debt payoff planner for the modular Excel server.

Excel debt table (14 columns from the configured anchor):
ID | Gläubiger | Kategorie | Restschuld | Sollzins p.a. % | Mindestzahlung |
Rückstand | Schutzstufe | Priorität | Umschuldungszins p.a. % |
Umschuldungsgebühr inkl. Vorfälligkeit | Zinswechsel in Monaten |
Zins nach Wechsel p.a. % | Notiz

The engine compares strategies, protects minimum payments, supports variable
monthly budgets and one-off payments, and simulates creditor-level schedules.
All figures are estimates using monthly interest accrual (APR / 12).
"""
from __future__ import annotations

import calendar
import datetime as dt

from core.base import Field, Module
from modules._table_engine import (
    FlexibleTableMixin,
    integer,
    json_text,
    normalise,
    number,
    parse_date,
    round_eur,
)


PROTECTED_CATEGORIES = {
    "MIETE", "WOHNEN", "ENERGIE", "STROM", "GAS", "HEIZUNG",
    "UNTERHALT", "PFAENDUNG", "PFANDUNG", "GERICHT", "STRAFE",
    "STEUER", "KRANKENKASSE", "LEBENSMITTEL", "MEDIKAMENTE", "TELEFON", "INTERNET",
}
STRATEGIES = ("AVALANCHE", "SNOWBALL", "CASHFLOW", "QUICKWINS", "CUSTOM")


def _month_label(start: dt.date | None, month_index: int) -> str:
    if start is None:
        return f"Monat {month_index}"
    month = start.month - 1 + month_index - 1
    year = start.year + month // 12
    month = month % 12 + 1
    return f"{year:04d}-{month:02d}"


def _as_bool_critical(category: str, explicit: str) -> tuple[bool, bool]:
    label = normalise(explicit)
    if label in {"KRITISCH", "PRIORITAET", "PRIORITAET 1", "JA", "1"}:
        return True, False
    if label in {"NORMAL", "NEIN", "0"}:
        return False, False
    return normalise(category) in PROTECTED_CATEGORIES, True


def _parse_debts(rows):
    debts = []
    seen_ids = set()
    for index, row in enumerate(rows, start=1):
        # Column A is the ID, column B is the creditor. Ignore header-like rows.
        if not row or normalise(row[0]) in {"ID", "SCHULD_ID", "KREDIT_ID"}:
            continue
        creditor = str(row[1] or "").strip() if len(row) > 1 else ""
        if not creditor:
            continue
        debt_id = str(row[0] or f"schuld_{index}").strip()
        if debt_id in seen_ids:
            raise ValueError(f"Doppelte Schuld-ID: {debt_id}")
        seen_ids.add(debt_id)
        category = str(row[2] or "SONSTIG").strip()
        balance = number(row[3] if len(row) > 3 else None, 0, f"{creditor}: Restschuld")
        apr = number(row[4] if len(row) > 4 else None, 0, f"{creditor}: Sollzins")
        minimum = number(row[5] if len(row) > 5 else None, 0, f"{creditor}: Mindestzahlung")
        arrears = number(row[6] if len(row) > 6 else None, 0, f"{creditor}: Rückstand")
        protection_raw = str(row[7] or "").strip() if len(row) > 7 else ""
        priority = integer(row[8] if len(row) > 8 else None, 99, f"{creditor}: Priorität")
        offer_rate = number(row[9] if len(row) > 9 else None, 0, f"{creditor}: Umschuldungszins")
        offer_fee = number(row[10] if len(row) > 10 else None, 0, f"{creditor}: Umschuldungsgebühr")
        promo_months = integer(row[11] if len(row) > 11 else None, 0, f"{creditor}: Zinswechsel")
        post_promo_rate = number(row[12] if len(row) > 12 else None, 0, f"{creditor}: Zinssatz nach Wechsel")
        note = str(row[13] or "").strip() if len(row) > 13 else ""

        for label, value in (("Restschuld", balance), ("Sollzins", apr), ("Mindestzahlung", minimum),
                             ("Rückstand", arrears), ("Umschuldungszins", offer_rate),
                             ("Umschuldungsgebühr", offer_fee), ("Zinssatz nach Wechsel", post_promo_rate)):
            if value < 0:
                raise ValueError(f"{creditor}: {label} darf nicht negativ sein.")
        if apr > 100 or offer_rate > 100 or post_promo_rate > 100:
            raise ValueError(f"{creditor}: Zinssätze über 100 % bitte prüfen.")
        if promo_months < 0 or priority < 0:
            raise ValueError(f"{creditor}: Monate und Priorität dürfen nicht negativ sein.")

        critical, inferred = _as_bool_critical(category, protection_raw)
        total_balance = round_eur(balance + arrears)
        if total_balance <= 0:
            continue
        debts.append({
            "id": debt_id,
            "creditor": creditor,
            "category": category,
            "balance": total_balance,
            "initial_balance": total_balance,
            "apr": apr,
            "minimum": minimum,
            "arrears": arrears,
            "critical": critical,
            "critical_inferred": inferred,
            "priority": priority,
            "offer_rate": offer_rate,
            "offer_fee": offer_fee,
            "promo_months": promo_months,
            "post_promo_rate": post_promo_rate,
            "note": note,
        })
    return debts


def _parse_lump_sums(rows):
    events = {}
    for row in rows:
        if not row or normalise(row[0]) in {"MONAT", "MONAT_INDEX", "MONAT_NR"}:
            continue
        month = integer(row[0], 0, "Sonderzahlung: Monat")
        amount = number(row[1] if len(row) > 1 else None, 0, "Sonderzahlung: Betrag")
        label = str(row[2] or "Sonderzahlung").strip() if len(row) > 2 else "Sonderzahlung"
        if month < 1 or amount < 0:
            raise ValueError("Sonderzahlungen benötigen einen Monat ab 1 und einen nichtnegativen Betrag.")
        if amount:
            events.setdefault(month, []).append({"betrag": round_eur(amount), "grund": label})
    return events


def _parse_budget_curve(rows):
    changes = {}
    for row in rows:
        if not row or normalise(row[0]) in {"MONAT", "MONAT_INDEX", "MONAT_NR"}:
            continue
        month = integer(row[0], 0, "Budgetverlauf: Monat")
        amount = number(row[1] if len(row) > 1 else None, 0, "Budgetverlauf: Budget")
        if month < 1 or amount < 0:
            raise ValueError("Budgetverlauf benötigt Monat ab 1 und ein nichtnegatives Budget.")
        changes[month] = round_eur(amount)
    return dict(sorted(changes.items()))


def _rate_for_month(debt, month_index, override_rates=None):
    if override_rates and debt["id"] in override_rates:
        return override_rates[debt["id"]]
    if debt["promo_months"] and month_index > debt["promo_months"] and debt["post_promo_rate"]:
        return debt["post_promo_rate"]
    return debt["apr"]


def _strategy_order(active, strategy, month_index):
    critical = [debt for debt in active if debt["critical"]]
    normal = [debt for debt in active if not debt["critical"]]
    critical.sort(key=lambda d: (d["priority"], -_rate_for_month(d, month_index), d["balance"]))
    if strategy == "SNOWBALL":
        normal.sort(key=lambda d: (d["balance"], -_rate_for_month(d, month_index), d["priority"]))
    elif strategy == "CASHFLOW":
        normal.sort(key=lambda d: (-(d["minimum"] / max(d["balance"], 0.01)), -d["minimum"], d["priority"]))
    elif strategy == "QUICKWINS":
        normal.sort(key=lambda d: (0 if d["balance"] <= 1000 else 1, d["balance"] if d["balance"] <= 1000 else -_rate_for_month(d, month_index), d["priority"]))
    elif strategy == "CUSTOM":
        normal.sort(key=lambda d: (d["priority"], -_rate_for_month(d, month_index), d["balance"]))
    else:  # AVALANCHE
        normal.sort(key=lambda d: (-_rate_for_month(d, month_index), d["balance"], d["priority"]))
    return critical + normal


def _simulate(debts, base_budget, lump_events, budget_curve, strategy, max_months,
              start_date=None, rate_overrides=None):
    state = {d["id"]: {**d, "balance": d["initial_balance"], "interest_total": 0.0,
                        "paid_total": 0.0, "principal_paid": 0.0, "interest_paid": 0.0,
                        "payoff_month": None, "last_payment": 0.0}
             for d in debts}
    total_interest = 0.0
    total_paid = 0.0
    schedule = []
    min_payment_start = round_eur(sum(min(d["minimum"], d["initial_balance"]) for d in debts))
    budget = base_budget
    if not debts:
        return {"feasible": True, "status": "KEINE_SCHULDEN", "months": 0, "interest": 0.0,
                "paid": 0.0, "remaining": 0.0, "schedule": [], "debt_details": [],
                "minimum_start": 0.0, "budget_shortfall": 0.0, "freed_budget": round_eur(base_budget)}

    if base_budget < 0:
        return {"feasible": False, "status": "NEGATIVES_BUDGET", "months": None,
                "interest": None, "paid": 0.0, "remaining": round_eur(sum(d["balance"] for d in debts)),
                "schedule": [], "debt_details": [], "minimum_start": min_payment_start,
                "budget_shortfall": round_eur(abs(base_budget)), "freed_budget": 0.0}

    for month_index in range(1, max_months + 1):
        if month_index in budget_curve:
            budget = budget_curve[month_index]
        elif month_index == 1:
            budget = base_budget
        # Carry forward the latest budget override.
        for changed_month in sorted(m for m in budget_curve if m <= month_index):
            budget = budget_curve[changed_month]

        current = [item for item in state.values() if item["balance"] > 0.005]
        if not current:
            break

        monthly_budget = round_eur(budget + sum(x["betrag"] for x in lump_events.get(month_index, [])))
        month_interest = 0.0
        month_interest_by_id = {}
        for debt in current:
            rate = _rate_for_month(debt, month_index, rate_overrides)
            interest = round_eur(debt["balance"] * rate / 1200.0)
            debt["balance"] = round_eur(debt["balance"] + interest)
            debt["interest_total"] += interest
            month_interest_by_id[debt["id"]] = interest
            month_interest += interest
        month_interest = round_eur(month_interest)
        required = {d["id"]: min(d["balance"], d["minimum"]) for d in current}
        required_total = round_eur(sum(required.values()))

        if required_total > monthly_budget + 0.005:
            return {"feasible": False, "status": "MINDESTZAHLUNGEN_NICHT_TRAGFAEHIG",
                    "months": None, "interest": None, "paid": total_paid,
                    "remaining": round_eur(sum(d["balance"] for d in state.values())),
                    "schedule": schedule, "debt_details": [], "minimum_start": min_payment_start,
                    "budget_shortfall": round_eur(required_total - monthly_budget),
                    "first_month_minimums": required_total, "available_budget": monthly_budget,
                    "freed_budget": 0.0}

        payments = {d["id"]: 0.0 for d in current}
        for debt in current:
            payment = round_eur(required[debt["id"]])
            debt["balance"] = max(0.0, round_eur(debt["balance"] - payment))
            payments[debt["id"]] += payment
            debt["paid_total"] += payment
            total_paid += payment
            debt["last_payment"] = payment

        remaining_budget = round_eur(monthly_budget - required_total)
        active_after_minimums = [d for d in current if d["balance"] > 0.005]
        for debt in _strategy_order(active_after_minimums, strategy, month_index):
            if remaining_budget <= 0.005:
                break
            extra = min(debt["balance"], remaining_budget)
            extra = round_eur(extra)
            debt["balance"] = max(0.0, round_eur(debt["balance"] - extra))
            payments[debt["id"]] = round_eur(payments.get(debt["id"], 0.0) + extra)
            debt["paid_total"] += extra
            debt["last_payment"] = payments[debt["id"]]
            total_paid += extra
            remaining_budget = round_eur(remaining_budget - extra)

        for debt in current:
            paid_for_debt = payments.get(debt["id"], 0.0)
            accrued_interest = month_interest_by_id.get(debt["id"], 0.0)
            debt["interest_paid"] += min(paid_for_debt, accrued_interest)
            debt["principal_paid"] += max(0.0, paid_for_debt - accrued_interest)

        for debt in state.values():
            if debt["balance"] <= 0.005 and debt["payoff_month"] is None:
                debt["balance"] = 0.0
                debt["payoff_month"] = month_index

        month_paid = round_eur(sum(payments.values()))
        total_interest += month_interest
        schedule.append({
            "monat_index": month_index,
            "monat": _month_label(start_date, month_index),
            "budget_eur": monthly_budget,
            "mindestraten_eur": required_total,
            "zinsen_eur": month_interest,
            "zahlung_eur": month_paid,
            "restschuld_eur": round_eur(sum(d["balance"] for d in state.values())),
            "freie_rate_eur": remaining_budget,
            "zahlungen_nach_glaeubiger": {key: round_eur(value) for key, value in payments.items() if value > 0},
            "sonderzahlungen": lump_events.get(month_index, []),
        })
        if all(d["balance"] <= 0.005 for d in state.values()):
            break

    remaining = round_eur(sum(d["balance"] for d in state.values()))
    complete = remaining <= 0.005
    months = len(schedule) if complete else None
    details = []
    for debt in state.values():
        details.append({
            "id": debt["id"], "glaeubiger": debt["creditor"], "kategorie": debt["category"],
            "startschuld_eur": debt["initial_balance"], "zinskosten_eur": round_eur(debt["interest_total"]),
            "zahlungen_eur": round_eur(debt["paid_total"]), "tilgung_eur": round_eur(debt["principal_paid"]),
            "schuld_beglichen_in_monat": debt["payoff_month"],
            "schuld_beglichen_am": _month_label(start_date, debt["payoff_month"]) if debt["payoff_month"] else None,
            "restschuld_eur": round_eur(debt["balance"]), "schutzstufe": "KRITISCH" if debt["critical"] else "NORMAL",
        })
    return {
        "feasible": complete,
        "status": "SCHULDENFREI_GEPLANT" if complete else "ZEITLIMIT_ERREICHT",
        "months": months,
        "interest": round_eur(total_interest),
        "paid": round_eur(total_paid),
        "remaining": remaining,
        "schedule": schedule,
        "debt_details": details,
        "minimum_start": min_payment_start,
        "budget_shortfall": 0.0,
        "freed_budget": round_eur(schedule[-1]["freie_rate_eur"]) if schedule else round_eur(base_budget),
    }


class Schuldenabbau(FlexibleTableMixin, Module):
    id = "schuldenabbau"
    name = "Schulden-Strategieplaner"
    description = (
        "Erstellt einen konkreten Mehrgläubiger-Tilgungsplan mit Priorisierung dringender "
        "Verpflichtungen, Avalanche/Snowball/Cashflow/Quick-Win-Vergleich, variablen Budgets, "
        "Sonderzahlungen und Umschuldungsprüfung."
    )
    aktiv_standard = True

    TABLE_FIELDS = {
        "schulden_tabelle_start": 14,
        "sonderzahlungen_start": 3,
        "budgetverlauf_start": 2,
    }

    inputs = [
        Field("schulden_tabelle_start", "Schulden-Tabelle: Startzelle (14 Spalten)", "text", default="A4", example="A4", cell="A4", help="Der Zellbezug ist die obere linke Zelle deiner Schuldenliste."),
        Field("nettoeinkommen", "Monatliches Nettoeinkommen (€)", "float", default=0, example=3000, cell="B1"),
        Field("lebenshaltung", "Notwendige Lebenshaltungskosten (€)", "float", default=0, example=1900, cell="C1"),
        Field("reserve_puffer", "Monatliche Rücklage / Sicherheitspuffer (€)", "float", default=0, example=100, cell="D1"),
        Field("budget_override", "Schuldenbudget gesamt (€); 0 = automatisch", "float", default=0, example=0, cell="E1"),
        Field("strategie", "Tilgungsstrategie", "select", default="VERGLEICH", example="VERGLEICH", cell="F1", options=["VERGLEICH", "AVALANCHE", "SNOWBALL", "CASHFLOW", "QUICKWINS", "CUSTOM"]),
        Field("max_monate", "Maximale Planlaufzeit (Monate)", "int", default=360, example=360, cell="G1"),
        Field("startmonat", "Planungsstart (JJJJ-MM-TT)", "text", default="", example="2026-12-01", cell="H1"),
        Field("sonderzahlungen_start", "Sonderzahlungen: Startzelle (3 Spalten)", "text", default="Q4", example="Q4", cell="Q4", help="Spalten: Monat_Index | Betrag_EUR | Grund. Leer lassen, wenn keine Sonderzahlungen geplant sind."),
        Field("budgetverlauf_start", "Budgetverlauf: Startzelle (2 Spalten)", "text", default="T4", example="T4", cell="T4", help="Spalten: Monat_Index | gesamtes Schuldenbudget_EUR. Budget gilt ab diesem Monat fortlaufend."),
    ]
    settings = []

    def calculate(self, inputs: dict, settings: dict) -> dict:
        debts = _parse_debts(inputs.get("_schulden_tabelle_start_rows", []))
        lump_events = _parse_lump_sums(inputs.get("_sonderzahlungen_start_rows", []))
        budget_curve = _parse_budget_curve(inputs.get("_budgetverlauf_start_rows", []))
        income = number(inputs.get("nettoeinkommen"), 0, "Nettoeinkommen")
        living = number(inputs.get("lebenshaltung"), 0, "Lebenshaltungskosten")
        reserve = number(inputs.get("reserve_puffer"), 0, "Rücklage/Sicherheitspuffer")
        override = number(inputs.get("budget_override"), 0, "Budget-Override")
        if min(income, living, reserve, override) < 0:
            raise ValueError("Einkommen, Kosten, Rücklage und Budget dürfen nicht negativ sein.")
        if living + reserve > income and override <= 0:
            base_budget = round_eur(income - living - reserve)
        else:
            base_budget = round_eur(override if override > 0 else income - living - reserve)
        max_months = integer(inputs.get("max_monate"), 360, "Maximale Planlaufzeit")
        if not 1 <= max_months <= 600:
            raise ValueError("Die Planlaufzeit muss zwischen 1 und 600 Monaten liegen.")
        start_date = parse_date(inputs.get("startmonat"), "Planungsstart")
        strategy_requested = normalise(inputs.get("strategie", "VERGLEICH"))
        if strategy_requested not in {"VERGLEICH", *STRATEGIES}:
            raise ValueError("Unbekannte Strategie. Erlaubt: VERGLEICH, AVALANCHE, SNOWBALL, CASHFLOW, QUICKWINS, CUSTOM.")
        if not debts:
            return self._empty_result(base_budget)

        variants = {}
        for strategy in STRATEGIES:
            variants[strategy] = _simulate(debts, base_budget, lump_events, budget_curve,
                                           strategy, max_months, start_date)
        best_candidates = [
            (name, result) for name, result in variants.items()
            if result.get("feasible") and result.get("months") is not None
        ]
        best_name, best_result = (min(best_candidates, key=lambda x: (x[1]["interest"], x[1]["months"]))
                                  if best_candidates else (None, None))
        chosen_name = best_name if strategy_requested == "VERGLEICH" else strategy_requested
        chosen = variants[chosen_name]

        minimum_start = chosen.get("minimum_start", 0.0)
        initial_debt = round_eur(sum(d["initial_balance"] for d in debts))
        warnings = []
        inferred = [d["creditor"] for d in debts if d["critical_inferred"]]
        if inferred:
            warnings.append({"typ": "SCHUTZSTUFE_PRUEFEN", "text": "Diese Gläubiger wurden anhand der Kategorie automatisch als kritisch markiert. Bitte Schutzstufe in Spalte H prüfen.", "glaeubiger": inferred})
        if chosen.get("status") == "MINDESTZAHLUNGEN_NICHT_TRAGFAEHIG":
            warnings.append({"typ": "BUDGETLUECKE", "text": "Das verfügbare Budget deckt die eingetragenen Mindestzahlungen nicht. Keine sichere Volltilgungsstrategie berechnet.", "fehlbetrag_eur": chosen.get("budget_shortfall")})
        if chosen.get("status") == "NEGATIVES_BUDGET":
            warnings.append({"typ": "NEGATIVER_SPIELRAUM", "text": "Nach notwendigen Ausgaben und Rücklage bleibt kein positives Schuldenbudget."})
        if chosen.get("status") == "ZEITLIMIT_ERREICHT":
            warnings.append({"typ": "ZEITLIMIT", "text": "Mit dem eingetragenen Budget werden nicht alle Schulden innerhalb des Planungshorizonts getilgt."})
        if any(d["offer_rate"] > 0 for d in debts):
            warnings.append({"typ": "UMSCHULDUNG", "text": "Umschuldungsvorteile sind Modellrechnungen. In Spalte K müssen sämtliche Angebotskosten einschließlich einer eventuell anfallenden Vorfälligkeitsentschädigung stehen."})
        if any(d["promo_months"] > 0 for d in debts):
            warnings.append({"typ": "ZINSWECHSEL", "text": "Eingetragene Zinssprünge nach Aktionszeiträumen werden ab dem angegebenen Monat berücksichtigt."})

        refi_results = []
        if chosen.get("feasible"):
            baseline_interest = chosen["interest"]
            for debt in debts:
                if debt["offer_rate"] > 0 and debt["offer_rate"] < debt["apr"]:
                    alt = _simulate(debts, base_budget, lump_events, budget_curve, chosen_name,
                                    max_months, start_date, {debt["id"]: debt["offer_rate"]})
                    gross_saving = round_eur(baseline_interest - alt.get("interest", baseline_interest)) if alt.get("feasible") else 0.0
                    net_saving = round_eur(gross_saving - debt["offer_fee"])
                    refi_results.append({
                        "glaeubiger": debt["creditor"], "bisheriger_zins_pa_pct": debt["apr"],
                        "angebot_zins_pa_pct": debt["offer_rate"], "gebuehren_inkl_vorfaelligkeit_eur": debt["offer_fee"],
                        "geschaetzte_zinsersparnis_eur": gross_saving, "vorteil_nach_gebuehren_eur": net_saving,
                        "status": "ANGEBOT_PRUEFEN" if net_saving > 0 else "KEIN_MODELLVORTEIL",
                    })

        action_plan = self._actions(debts, chosen, best_name, base_budget, minimum_start)
        comparisons = []
        for name, result in variants.items():
            comparisons.append({
                "strategie": name, "status": result.get("status"), "schuldenfrei_in_monaten": result.get("months"),
                "zinskosten_eur": result.get("interest"), "restschuld_eur": result.get("remaining"),
                "budgetluecke_eur": result.get("budget_shortfall", 0),
            })

        plan = {
            "version": 1,
            "modellannahme": "Monatliche Zinsperioden mit Sollzins p.a. / 12; Zahlungen werden modellhaft am Monatsende verrechnet.",
            "strategie_ausgewaehlt": chosen_name,
            "strategie_mit_niedrigsten_modell_zinskosten": best_name,
            "status": chosen.get("status"),
            "monatliches_schuldenbudget_start_eur": base_budget,
            "tilgungsplan": chosen.get("schedule", []),
        }
        debt_plan = {
            "gläubiger": chosen.get("debt_details", []),
            "gesamtstartschuld_eur": initial_debt,
            "mindestraten_start_eur": minimum_start,
        }

        recommendation = (
            f"Modellvorschlag: {best_name}. Die Strategie hat im Vergleich die niedrigsten modellierten Zinskosten."
            if best_name else
            "Kein tragfähiger Volltilgungsplan: zuerst Budgetlücke und Zahlungsprioritäten klären."
        )
        return {
            "plan_status": chosen.get("status", "UNBEKANNT"),
            "gesamt_restschuld_start_eur": initial_debt,
            "schuldenbudget_monatlich_eur": base_budget,
            "mindestraten_gesamt_start_eur": minimum_start,
            "budget_nach_mindestzahlungen_eur": round_eur(base_budget - minimum_start),
            "budgetluecke_eur": round_eur(chosen.get("budget_shortfall", 0)),
            "zinskosten_gesamt_eur": chosen.get("interest"),
            "gesamtzahlungen_eur": chosen.get("paid"),
            "schuldenfrei_in_monaten": chosen.get("months"),
            "restschuld_nach_plan_eur": chosen.get("remaining"),
            "empfohlene_strategie": best_name or "KEINE_TRAGFAEHIGE_STRATEGIE",
            "ausgewaehlte_strategie": chosen_name,
            "empfehlung_text": recommendation,
            "gläubigerplan_json": json_text(debt_plan),
            "monatsplan_json": json_text(plan),
            "strategievergleich_json": json_text(comparisons),
            "handlungsplan_json": json_text(action_plan),
            "umschuldungscheck_json": json_text(refi_results),
            "warnungen_json": json_text(warnings),
        }

    @staticmethod
    def _empty_result(budget):
        return {
            "plan_status": "KEINE_SCHULDEN_ERFASST", "gesamt_restschuld_start_eur": 0.0,
            "schuldenbudget_monatlich_eur": budget, "mindestraten_gesamt_start_eur": 0.0,
            "budget_nach_mindestzahlungen_eur": budget, "budgetluecke_eur": 0.0,
            "zinskosten_gesamt_eur": 0.0, "gesamtzahlungen_eur": 0.0,
            "schuldenfrei_in_monaten": 0, "restschuld_nach_plan_eur": 0.0,
            "empfohlene_strategie": "KEINE", "ausgewaehlte_strategie": "KEINE",
            "empfehlung_text": "Keine Schuldenzeilen gefunden. Prüfe die Tabellen-Startzelle und die Gläubiger-Spalte.",
            "gläubigerplan_json": "[]", "monatsplan_json": "[]", "strategievergleich_json": "[]",
            "handlungsplan_json": "[]", "umschuldungscheck_json": "[]", "warnungen_json": "[]",
        }

    @staticmethod
    def _actions(debts, chosen, best_name, budget, minimums):
        actions = []
        if chosen.get("status") == "MINDESTZAHLUNGEN_NICHT_TRAGFAEHIG":
            actions.append({"prioritaet": 1, "aktion": "Zahlungsfähigkeit sofort klären", "warum": f"Das Modell zeigt eine monatliche Lücke von {chosen.get('budget_shortfall', 0):.2f} €. Nicht einfach einzelne Mindestzahlungen auslassen."})
            actions.append({"prioritaet": 2, "aktion": "Existenzielle laufende Kosten schützen", "warum": "Wohnen, Energie, Lebensmittel, notwendige Medikamente und Unterhalt vor frei disponiblen Konsumverbindlichkeiten sichern."})
            actions.append({"prioritaet": 3, "aktion": "Gläubiger frühzeitig kontaktieren und Schuldnerberatung einbeziehen", "warum": "Ratenpause, Stundung oder tragfähige Ratenvereinbarung schriftlich klären; bei Überforderung anerkannte Schuldnerberatung nutzen."})
            return actions
        critical = [d["creditor"] for d in debts if d["critical"]]
        if critical:
            actions.append({"prioritaet": 1, "aktion": "Kritische Rückstände zuerst stabilisieren", "warum": "In Spalte H markierte KRITISCH-Verbindlichkeiten werden bei Sondertilgungen vor normalen Konsumschulden bedient.", "glaeubiger": critical})
        actions.append({"prioritaet": 2, "aktion": "Mindestzahlungen für alle übrigen Verträge einplanen", "warum": f"Die anfänglichen Mindestzahlungen betragen zusammen {minimums:.2f} € pro Monat."})
        if best_name:
            actions.append({"prioritaet": 3, "aktion": f"Freies Budget nach der Strategie {best_name} bündeln", "warum": "Alle zusätzlichen Beträge werden auf das priorisierte Ziel gelenkt. Nach Tilgung einer Schuld wird die frei werdende Rate automatisch auf die nächste Schuld umgeleitet."})
        if any(d["offer_rate"] > 0 for d in debts):
            actions.append({"prioritaet": 4, "aktion": "Umschuldungsangebote gegen Gesamtkosten prüfen", "warum": "Nur Angebote mit positiver Nettoersparnis nach sämtlichen Gebühren weiterverfolgen. Vertragsbedingungen und Vorfälligkeitsentschädigung gesondert prüfen."})
        actions.append({"prioritaet": 5, "aktion": "Plan monatlich aktualisieren", "warum": "Restschulden, Zinsen, neue Raten, Sonderzahlungen und tatsächliche Lebenshaltungskosten regelmäßig aktualisieren."})
        return actions
