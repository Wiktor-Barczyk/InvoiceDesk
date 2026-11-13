"""Validation of invoice data. All money remains Decimal, including JSON boundaries."""
import re
from datetime import date
from decimal import Decimal, InvalidOperation


class Problem(Exception):
    def __init__(self, message, status=422):
        self.message, self.status = message, status


def money(value, label="Kwota"):
    raw = str(value).strip().replace("\u00a0", "").replace(" ", "")
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".") if raw.rfind(",") > raw.rfind(".") else raw.replace(",", "")
    else:
        raw = raw.replace(",", ".")
    try:
        result = Decimal(raw)
    except InvalidOperation:
        raise Problem(f"{label}: wpisz poprawną kwotę.")
    if not result.is_finite() or result < 0 or result > Decimal("9999999999999999.99"):
        raise Problem(f"{label}: kwota musi być nieujemna i mieścić się w zakresie bazy.")
    if result != result.quantize(Decimal("0.01")):
        raise Problem(f"{label}: maksymalnie dwa miejsca po przecinku.")
    return result.quantize(Decimal("0.01"))


def normalized(value):
    return re.sub(r"[\W_]", "", (value or "").casefold())


def positive_id(value, label, optional=False):
    if optional and value in (None, "", 0, "0"):
        return None
    try:
        result = int(str(value))
    except (ValueError, TypeError):
        raise Problem(f"{label}: wybierz wartość z listy.")
    if result <= 0 or result > 9223372036854775807:
        raise Problem(f"{label}: nieprawidłowy identyfikator.")
    return result


def text_field(data, name, limit, required=False):
    value = data.get(name) or ""
    if not isinstance(value, str):
        raise Problem(f"Pole {name} musi zawierać tekst.")
    value = value.strip()
    if len(value) > limit:
        raise Problem(f"Pole {name}: maksymalnie {limit} znaków.")
    if required and not value:
        raise Problem(f"Uzupełnij pole: {name}.")
    return value or None


def validate(data):
    if not isinstance(data, dict):
        raise Problem("Nieprawidłowe dane formularza.")
    result = {"client_id": positive_id(data.get("client_id"), "Klient"),
              "counterparty_id": positive_id(data.get("counterparty_id"), "Kontrahent", True)}
    for name, allowed in {
        "invoice_type": ("purchase", "sale"),
        "invoice_kind": ("invoice", "credit_note", "self_billing", "other"),
    }.items():
        if data.get(name) not in allowed:
            raise Problem(f"Wybierz poprawny rodzaj dokumentu ({name}).")
        result[name] = data[name]
    for key in ("invoice_date", "due_date"):
        if key == "due_date" and not data.get(key):
            result[key] = None
            continue
        try:
            result[key] = date.fromisoformat(data[key])
            if not 2000 <= result[key].year <= 2200:
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            raise Problem(f"Uzupełnij poprawną datę: {key}.")
    result["currency"] = str(data.get("currency", "")).upper()
    if not re.fullmatch(r"[A-Z]{3}", result["currency"]):
        raise Problem("Waluta musi być trzyliterowym kodem, np. EUR lub PLN.")
    rate = data.get("exchange_rate_to_eur")
    result["exchange_rate_to_eur"] = Decimal("1") if result["currency"] == "EUR" else None
    if result["currency"] != "EUR":
        try:
            rate = Decimal(str(rate).replace(",", "."))
            if not rate.is_finite() or rate <= 0 or rate >= Decimal("10000000000") or rate != rate.quantize(Decimal("0.0000000001")):
                raise ValueError()
            result["exchange_rate_to_eur"] = rate
        except (ValueError, InvalidOperation):
            raise Problem("Podaj kurs: ile EUR odpowiada 1 jednostce waluty faktury (maks. 10 miejsc po przecinku).")
    for key, limit in {"invoice_number": 100, "seller_name_snapshot": 255, "seller_vat_number_snapshot": 40,
                       "seller_iban_snapshot": 34, "buyer_name_snapshot": 255, "buyer_vat_number_snapshot": 40, "notes": 10000}.items():
        result[key] = text_field(data, key, limit, required=key in ("seller_name_snapshot", "buyer_name_snapshot"))
    if not result["invoice_number"] and result["invoice_kind"] != "other":
        raise Problem("Podaj numer faktury. Dokument bez numeru oznacz jako „Inny / paragon”.")
    if not result["invoice_number"] and not result["notes"]:
        raise Problem("Dla dokumentu bez numeru wpisz wyjaśnienie w uwagach.")
    for key in ("net_total", "vat_total", "gross_total"):
        result[key] = money(data.get(key), key)
    if result["gross_total"] != result["net_total"] + result["vat_total"]:
        raise Problem("Suma netto + VAT nie zgadza się z brutto. Popraw kwoty z dokumentu.")
    lines = data.get("lines")
    if not isinstance(lines, list) or not 1 <= len(lines) <= 200:
        raise Problem("Faktura musi zawierać od 1 do 200 pozycji.")
    result["lines"] = []
    for i, line in enumerate(lines, 1):
        if not isinstance(line, dict):
            raise Problem("Nieprawidłowa pozycja faktury.")
        item = {"line_number": i, "description": text_field(line, "description", 4000, True),
                "net_amount": money(line.get("net_amount"), f"Pozycja {i}: netto"),
                "vat_amount": money(line.get("vat_amount"), f"Pozycja {i}: VAT")}
        for key in ("quantity", "unit_price"):
            item[key] = None
            if line.get(key) not in (None, ""):
                try:
                    n = Decimal(str(line[key]).replace(",", "."))
                    if not n.is_finite() or n < 0 or n >= Decimal("100000000000000") or n != n.quantize(Decimal("0.0001")):
                        raise ValueError()
                    item[key] = n
                except (ValueError, InvalidOperation):
                    raise Problem(f"Pozycja {i}: {key} musi być nieujemną liczbą (maks. 4 miejsca po przecinku).")
        for key in ("booking_category_id", "account_id", "vat_code_id"):
            item[key] = positive_id(line.get(key), f"Pozycja {i}: {key}", optional=key != "vat_code_id")
        if item["booking_category_id"] is None and item["account_id"] is None:
            raise Problem(f"Pozycja {i}: wybierz kategorię / konto.")
        result["lines"].append(item)
    if sum(x["net_amount"] for x in result["lines"]) != result["net_total"] or sum(x["vat_amount"] for x in result["lines"]) != result["vat_total"]:
        raise Problem("Sumy pozycji nie zgadzają się z kwotami na fakturze.")
    if data.get("reviewed") is not True:
        raise Problem("Potwierdź sprawdzenie danych z oryginałem.")
    return result
