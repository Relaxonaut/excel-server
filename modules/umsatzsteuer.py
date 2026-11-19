"""Flexible German VAT ledger and period evaluation.

Excel table (8 columns from the configured anchor):
Datum | Beleg_ID | Vorgang | Betrag | Betragstyp | Steuersatz_% | Vorsteuerabzug | Notiz

Vorgang: AUSGANG, EINGANG, IG_LIEFERUNG, IG_ERWERB, AUSFUHR,
REVERSE_CHARGE, EINFUHR, STEUERFREI.
The user classifies transactions; the module does not decide whether a legal
reverse-charge or exemption condition is actually met.
"""
from __future__ import annotations

from datetime import date

from core.base import Field, Module
from modules._table_engine import (
    FlexibleTableMixin,
    is_header,
    json_text,
    normalise,
    number,
    parse_date,
    round_eur,
)


class Umsatzsteuer(FlexibleTableMixin, Module):
    id = "umsatzsteuer"
    name = "Umsatzsteuer-Engine"
    description = (
        "Wertet beliebig viele Ausgangs- und Eingangsbelege aus, berechnet "
        "Umsatzsteuer, Vorsteuer, Reverse-Charge-/innergemeinschaftliche "
        "Erwerbsvorgänge und die modellierte Zahllast je Zeitraum."
    )
    aktiv_standard = True

    TABLE_FIELDS = {"buchungen_start": 8}

    inputs = [
        Field("buchungen_start", "Buchungstabelle: Startzelle (8 Spalten)", "text", default="A16", example="A16", cell="A16", help="Spalten: Datum | Beleg-ID | Vorgang | Betrag | NETTO/BRUTTO | Steuersatz | Vorsteuer JA/NEIN | Notiz"),
        Field("zeitraum_von", "Zeitraum von (JJJJ-MM-TT; leer = offen)", "text", default="", example="2026-11-01", cell="B13"),
        Field("zeitraum_bis", "Zeitraum bis (JJJJ-MM-TT; leer = offen)", "text", default="", example="2026-11-30", cell="C13"),
        Field("kleinunternehmer", "Kleinunternehmerregelung nach § 19 UStG aktiv", "bool", default=False, example=False, cell="D13"),
    ]
    settings = []

    def calculate(self, inputs: dict, settings: dict) -> dict:
        rows = inputs.get("_buchungen_start_rows", [])
        start = parse_date(inputs.get("zeitraum_von"), "Zeitraum von")
        end = parse_date(inputs.get("zeitraum_bis"), "Zeitraum bis")
        if start and end and start > end:
            raise ValueError("Zeitraum von darf nicht nach Zeitraum bis liegen.")
        small_business = bool(inputs.get("kleinunternehmer", False))

        outgoing_net = 0.0
        outgoing_vat = 0.0
        incoming_net = 0.0
        deductible_input_vat = 0.0
        reverse_charge_vat = 0.0
        reverse_charge_deductible = 0.0
        intra_eu_base = 0.0
        intra_eu_supply = 0.0
        export_turnover = 0.0
        import_input_vat = 0.0
        exempt_turnover = 0.0
        by_rate = {}
        details = []
        warnings = []
        included_count = 0
        ignored_count = 0

        for row_number, row in enumerate(rows, start=1):
            if not row or is_header(row, ("Datum", "Beleg_ID", "Beleg-ID")):
                continue
            date_value = parse_date(row[0] if len(row) > 0 else None, f"Buchungszeile {row_number}: Datum")
            if start and date_value and date_value < start:
                ignored_count += 1
                continue
            if end and date_value and date_value > end:
                ignored_count += 1
                continue
            if (start or end) and date_value is None:
                warnings.append({"zeile": row_number, "typ": "DATUM_FEHLT", "text": "Buchung ohne Datum wurde trotz gesetztem Zeitraum berücksichtigt."})
            doc_id = str(row[1] or f"Beleg_{row_number}").strip() if len(row) > 1 else f"Beleg_{row_number}"
            transaction_type = normalise(row[2] if len(row) > 2 else "")
            amount = number(row[3] if len(row) > 3 else None, 0, f"{doc_id}: Betrag")
            amount_type = normalise(row[4] if len(row) > 4 else "NETTO") or "NETTO"
            rate = number(row[5] if len(row) > 5 else None, 0, f"{doc_id}: Steuersatz")
            deductible = normalise(row[6] if len(row) > 6 else "NEIN") in {"JA", "J", "YES", "TRUE", "1", "X"}
            note = str(row[7] or "").strip() if len(row) > 7 else ""
            if rate < 0 or rate > 100:
                raise ValueError(f"{doc_id}: Steuersatz muss zwischen 0 und 100 % liegen.")
            if transaction_type in {"IG_LIEFERUNG", "AUSFUHR", "STEUERFREI"} and rate != 0:
                raise ValueError(f"{doc_id}: {transaction_type} muss mit 0 % erfasst werden; rechtliche Voraussetzungen separat prüfen.")
            if amount_type not in {"NETTO", "BRUTTO"}:
                raise ValueError(f"{doc_id}: Betragstyp muss NETTO oder BRUTTO sein.")
            allowed_types = {"AUSGANG", "EINGANG", "IG_LIEFERUNG", "IG_ERWERB", "AUSFUHR", "REVERSE_CHARGE", "EINFUHR", "STEUERFREI"}
            if transaction_type not in allowed_types:
                raise ValueError(f"{doc_id}: Vorgang {transaction_type!r} nicht bekannt. Erlaubt: {', '.join(sorted(allowed_types))}.")

            if small_business and transaction_type == "AUSGANG":
                # Under the selected § 19 mode, the customer amount is not split
                # into a VAT component. Eligibility must be checked separately.
                net = amount
                tax = 0.0
            elif amount_type == "BRUTTO" and rate > 0:
                net = amount / (1.0 + rate / 100.0)
                tax = amount - net
            else:
                net = amount
                tax = amount * rate / 100.0
            net = round_eur(net)
            tax = round_eur(tax)
            output_tax = 0.0
            input_tax = 0.0
            classification = transaction_type

            if transaction_type == "AUSGANG":
                outgoing_net += net
                if not small_business:
                    output_tax = tax
                    outgoing_vat += tax
                else:
                    warnings.append({"zeile": row_number, "typ": "KLEINUNTERNEHMER_AUSGANG", "text": "Ausgangsumsatz im Kleinunternehmermodus ohne Umsatzsteuerausweis gerechnet."})
            elif transaction_type == "EINGANG":
                incoming_net += net
                if deductible and not small_business:
                    input_tax = tax
                    deductible_input_vat += tax
            elif transaction_type == "IG_LIEFERUNG":
                intra_eu_supply += net
                output_tax = 0.0
                warnings.append({"zeile": row_number, "typ": "IG_LIEFERUNG_PRUEFEN", "text": "Innergemeinschaftliche Lieferung: Voraussetzungen, USt-IdNr., Nachweise und Meldungen manuell prüfen."})
            elif transaction_type == "AUSFUHR":
                export_turnover += net
                output_tax = 0.0
                warnings.append({"zeile": row_number, "typ": "AUSFUHR_PRUEFEN", "text": "Ausfuhrlieferung: Ausfuhrnachweis und Voraussetzungen der Steuerbefreiung manuell prüfen."})
            elif transaction_type == "IG_ERWERB":
                intra_eu_base += net
                output_tax = tax
                reverse_charge_vat += tax
                if deductible and not small_business:
                    input_tax = tax
                    reverse_charge_deductible += tax
            elif transaction_type == "REVERSE_CHARGE":
                output_tax = tax
                reverse_charge_vat += tax
                if deductible and not small_business:
                    input_tax = tax
                    reverse_charge_deductible += tax
            elif transaction_type == "EINFUHR":
                if deductible and not small_business:
                    input_tax = tax
                    import_input_vat += tax
            elif transaction_type == "STEUERFREI":
                exempt_turnover += net
                output_tax = 0.0

            included_count += 1
            rate_key = f"{rate:g}%"
            bucket = by_rate.setdefault(rate_key, {"steuersatz_pct": rate, "bemessungsgrundlage_eur": 0.0,
                                                   "ausgangssteuer_eur": 0.0, "vorsteuer_eur": 0.0,
                                                   "vorgaenge": 0})
            bucket["bemessungsgrundlage_eur"] += net
            bucket["ausgangssteuer_eur"] += output_tax
            bucket["vorsteuer_eur"] += input_tax
            bucket["vorgaenge"] += 1
            details.append({
                "datum": date_value.isoformat() if date_value else None,
                "beleg_id": doc_id,
                "vorgang": transaction_type,
                "bemessungsgrundlage_eur": net,
                "steuersatz_pct": rate,
                "berechnete_steuer_eur": tax,
                "umsatzsteuer_ausgang_eur": round_eur(output_tax),
                "abziehbare_vorsteuer_eur": round_eur(input_tax),
                "vorsteuerabzug_markiert": deductible,
                "notiz": note,
            })
            if transaction_type in {"IG_ERWERB", "REVERSE_CHARGE"}:
                warnings.append({"zeile": row_number, "typ": "REVERSE_CHARGE_PRUEFEN", "text": f"{transaction_type}: prüfen, ob der konkrete Umsatz tatsächlich unter die einschlägige Vorschrift fällt."})

        outgoing_net = round_eur(outgoing_net)
        outgoing_vat = round_eur(outgoing_vat)
        incoming_net = round_eur(incoming_net)
        deductible_input_vat = round_eur(deductible_input_vat)
        reverse_charge_vat = round_eur(reverse_charge_vat)
        reverse_charge_deductible = round_eur(reverse_charge_deductible)
        import_input_vat = round_eur(import_input_vat)
        total_input = round_eur(deductible_input_vat + reverse_charge_deductible + import_input_vat)
        total_output = round_eur(outgoing_vat + reverse_charge_vat)
        payable = round_eur(total_output - total_input)
        for bucket in by_rate.values():
            for key in ("bemessungsgrundlage_eur", "ausgangssteuer_eur", "vorsteuer_eur"):
                bucket[key] = round_eur(bucket[key])

        if small_business:
            warnings.append({"typ": "§19", "text": "Kleinunternehmermodus: Ausgangsumsätze ohne Umsatzsteuer; Eingangsrechnungen ohne Vorsteuerabzug. Reverse-Charge-/innergemeinschaftliche Erwerbsvorgänge können dennoch Steuer auslösen. Die Anwendbarkeit des § 19 UStG muss separat geprüft werden."})
        if ignored_count:
            warnings.append({"typ": "ZEITRAUM", "text": f"{ignored_count} Buchungen lagen außerhalb des ausgewählten Zeitraums."})
        if not rows:
            warnings.append({"typ": "KEINE_DATEN", "text": "Keine Buchungen gefunden. Prüfe Tabellen-Startzelle und benannten Excel-Datenbereich."})

        return {
            "buchungen_ausgewertet": included_count,
            "buchungen_ausserhalb_zeitraum": ignored_count,
            "ausgangsumsatz_netto_eur": outgoing_net,
            "umsatzsteuer_ausgang_eur": outgoing_vat,
            "eingangsumsatz_netto_eur": incoming_net,
            "vorsteuer_abziehbar_eur": total_input,
            "reverse_charge_steuer_eur": reverse_charge_vat,
            "innergemeinschaftlicher_erwerb_netto_eur": round_eur(intra_eu_base),
            "innergemeinschaftliche_lieferungen_netto_eur": round_eur(intra_eu_supply),
            "ausfuhrlieferungen_netto_eur": round_eur(export_turnover),
            "steuerfreie_umsatze_netto_eur": round_eur(exempt_turnover),
            "umsatzsteuer_gesamt_eur": total_output,
            "zahllast_eur": payable if payable > 0 else 0.0,
            "vorsteuerueberhang_eur": -payable if payable < 0 else 0.0,
            "saldo_umsatzsteuer_minus_vorsteuer_eur": payable,
            "kleinunternehmermodus": small_business,
            "zeitraum_von": start.isoformat() if start else "offen",
            "zeitraum_bis": end.isoformat() if end else "offen",
            "steuersatz_auswertung_json": json_text(by_rate),
            "buchungsdetails_json": json_text(details),
            "pruefhinweise_json": json_text(warnings),
        }
