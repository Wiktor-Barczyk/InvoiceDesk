"""Local, conservative PL/NL/EN extraction. Suggestions always require human review."""
import io
import re
import subprocess
import tempfile
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import fitz
from PIL import Image, ImageOps

from .domain import Problem, money

MAX_PAGES = 20
Image.MAX_IMAGE_PIXELS = 35_000_000
AMOUNT = r"[-−]?(?:\d{1,3}(?:[ .\u00a0,]\d{3})+|\d+)[,.]\d{2}"
DATE = r"\d{4}-\d{2}-\d{2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{4}"


def inspect_file(raw):
    if raw.startswith(b"%PDF-"):
        try:
            with fitz.open(stream=raw, filetype="pdf") as pdf:
                if pdf.needs_pass or not 1 <= len(pdf) <= MAX_PAGES:
                    raise Problem(f"PDF musi być bez hasła i mieć od 1 do {MAX_PAGES} stron.")
                return {"mime": "application/pdf", "extension": ".pdf", "pages": len(pdf)}
        except Problem:
            raise
        except Exception:
            raise Problem("Nie można odczytać tego pliku PDF.")
    try:
        with Image.open(io.BytesIO(raw)) as im:
            if im.format not in ("JPEG", "PNG") or im.width * im.height > Image.MAX_IMAGE_PIXELS:
                raise ValueError()
            im.verify()
            return {"mime": "image/jpeg" if im.format == "JPEG" else "image/png", "extension": ".jpg" if im.format == "JPEG" else ".png", "pages": 1}
    except Exception:
        raise Problem("Wybierz poprawny PDF, JPG lub PNG (obraz do 35 mln pikseli).")


def image_bytes(path, page=1, scale=1.8, max_dimension=2500):
    with open(path, "rb") as source:
        is_pdf = source.read(5) == b"%PDF-"
    if is_pdf:
        with fitz.open(path) as pdf:
            if page < 1 or page > len(pdf):
                raise Problem("Nie ma takiej strony.", 404)
            p = pdf[page - 1]
            scale = min(scale, max_dimension / max(p.rect.width, p.rect.height))
            return p.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).tobytes("png")
    if page != 1:
        raise Problem("Nie ma takiej strony.", 404)
    with Image.open(path) as original:
        im = ImageOps.exif_transpose(original).convert("RGB")
        im.thumbnail((max_dimension, max_dimension))
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue()


def ocr_image(raw, languages):
    with tempfile.TemporaryDirectory(prefix="invoice-ocr-") as td:
        target = Path(td) / "page.png"
        target.write_bytes(raw)
        run = subprocess.run(["tesseract", str(target), "stdout", "-l", languages, "--psm", "3"],
                             capture_output=True, timeout=45, check=True)
        return run.stdout.decode("utf-8", errors="replace")


def extract(path, languages="pol+nld+eng"):
    pages, warnings, used_ocr = [], [], False
    with open(path, "rb") as source:
        is_pdf = source.read(5) == b"%PDF-"
    if is_pdf:
        with fitz.open(path) as pdf:
            for i, p in enumerate(pdf):
                content = p.get_text("text", sort=True).strip()
                if len(re.sub(r"\W", "", content)) < 50:
                    try:
                        content = ocr_image(image_bytes(path, i + 1, 2.2), languages)
                        used_ocr = True
                    except (OSError, subprocess.SubprocessError):
                        warnings.append(f"Nie odczytano skanu na stronie {i + 1}. Uzupełnij pola ręcznie.")
                pages.append({"page": i + 1, "text": content[:100000]})
    else:
        try:
            pages.append({"page": 1, "text": ocr_image(image_bytes(path), languages)[:100000]})
            used_ocr = True
        except (OSError, subprocess.SubprocessError):
            pages.append({"page": 1, "text": ""})
            warnings.append("Odczyt skanu nie powiódł się. Uzupełnij dane ręcznie.")
    fields, evidence, parser_warnings = parse_text("\n".join(p["text"] for p in pages))
    return {"engine": "pymupdf+tesseract+rules" if used_ocr else "pymupdf+rules", "version": "1.0",
            "raw": {"pages": pages, "warnings": warnings + parser_warnings, "evidence": evidence}, "fields": fields}


def parse_text(text):
    fields, evidence, warnings = {}, {}, []
    def capture(key, labels, pattern, flags=re.I | re.M):
        # Start-of-line labels prevent matching buyer VAT as invoice total.
        found = list(re.finditer(r"^\s*(?:" + labels + r")[ \t:]*\s*(?:EUR|PLN|€)?[ \t]*(?P<value>" + pattern + r")", text, flags))
        unique = {m.group("value").strip() for m in found}
        if len(unique) == 1:
            fields[key] = found[-1].group("value").strip()
            evidence[key] = found[-1].group(0).strip()[:500]
        elif len(unique) > 1:
            warnings.append(f"Znaleziono kilka wartości pola {key}. Wpisz właściwą z dokumentu.")
    capture("invoice_number", r"(?:factuurnummer|factuur\s*(?:nr\.?|nummer)|invoice\s*(?:no\.?|number|#)|numer faktury|faktura\s*(?:nr\.?)|factuur(?!datum))", r"[A-Z0-9][A-Z0-9/_.-]{1,99}")
    for key, labels in {
        "invoice_date": r"factuurdatum|invoice date|data wystawienia|data faktury",
        "due_date": r"vervaldatum|due date|termin płatności",
    }.items():
        capture(key, labels, DATE)
        if key in fields:
            value = fields.pop(key)
            for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y"):
                try:
                    fields[key] = datetime.strptime(value, fmt).date().isoformat()
                    break
                except ValueError:
                    pass
    for key, labels in {
        "net_total": r"totaal\s*(?:excl\.?\s*(?:btw)?|netto)|subtotaal|net total|total excl\.?\s*(?:vat)?|razem netto|suma netto",
        "vat_total": r"totaal\s*btw|btw\s*(?:bedrag|totaal)|vat total|total vat|razem vat|suma vat|kwota vat|btw(?:\s+\d+(?:[,.]\d+)?\s*%)?",
        "gross_total": r"totaal\s*(?:incl\.?\s*(?:btw)?|te betalen)|te betalen|total incl\.?\s*(?:vat)?|amount due|gross total|razem brutto|suma brutto|do zapłaty",
    }.items():
        capture(key, labels, AMOUNT)
        if key in fields:
            value = fields.pop(key)
            try:
                fields[key] = str(money(value))
            except Problem:
                warnings.append(f"Kwota {key} wymaga ręcznego sprawdzenia, także znaku korekty.")
    for key, labels in {
        "seller_name_snapshot": r"sprzedawca|seller|leverancier|verkoper",
        "buyer_name_snapshot": r"nabywca|buyer|afnemer|klant",
        "seller_vat_number_snapshot": r"btw(?:-id|-nummer)? leverancier|seller vat|nip sprzedawcy",
        "buyer_vat_number_snapshot": r"btw(?:-id|-nummer)? afnemer|buyer vat|nip nabywcy",
        "seller_iban_snapshot": r"iban",
    }.items():
        capture(key, "(?:" + labels + r")(?=[ \t]*(?::|\n))", r"[^\r\n]+")
    currencies = set(re.findall(r"\b(EUR|PLN|USD|GBP|CHF)\b", text.upper()))
    if len(currencies) == 1:
        fields["currency"] = currencies.pop()
    elif not currencies and "€" in text:
        fields["currency"] = "EUR"
    fields["invoice_kind"] = "credit_note" if re.search(r"creditnota|credit note|faktura korygująca", text, re.I) else "invoice"
    rates = set(re.findall(r"\b(?:BTW|VAT)\s*(\d+(?:[,.]\d+)?)\s*%", text, re.I))
    if len(rates) == 1:
        fields["suggested_vat_rate"] = rates.pop().replace(",", ".")
    if "net_total" in fields and "vat_total" in fields:
        # Explicitly one aggregate, not fabricated item descriptions.
        fields["lines"] = [{"description": "Suma faktury — sprawdź podział na pozycje", "net_amount": fields["net_total"],
                            "vat_amount": fields["vat_total"], "booking_category_id": None, "account_id": None, "vat_code_id": None}]
        warnings.append("Kwoty przeniesiono do jednej pozycji zbiorczej. Rozbij ją, jeśli faktura zawiera różne kategorie lub kody VAT.")
        if fields.get("suggested_vat_rate"):
            item_pattern = r"^[ \t]*(?P<description>[^\r\n]{3,180}?)[ \t]{2,}(?P<quantity>\d+(?:[,.]\d+)?)[ \t]{2,}(?P<unit_price>" + AMOUNT + r")[ \t]{2,}(?P<net>" + AMOUNT + r")[ \t]*$"
            items = []
            for match in re.finditer(item_pattern, text, re.M):
                try:
                    net = money(match.group("net"))
                    quantity = Decimal(match.group("quantity").replace(",", "."))
                    unit_price = money(match.group("unit_price"))
                    if (quantity * unit_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) != net:
                        continue
                    tax = (net * Decimal(fields["suggested_vat_rate"]) / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                    items.append({"description": match.group("description").strip(), "quantity": match.group("quantity").replace(",", "."),
                                  "unit_price": str(unit_price), "net_amount": str(net), "vat_amount": str(tax),
                                  "booking_category_id": None, "account_id": None, "vat_code_id": None})
                except Problem:
                    pass
            if items and sum(Decimal(x["net_amount"]) for x in items) == Decimal(fields["net_total"]) and sum(Decimal(x["vat_amount"]) for x in items) == Decimal(fields["vat_total"]):
                fields["lines"] = items
                warnings[-1] = "Rozpoznano tabelę pozycji. VAT pozycji wyliczono z jednej rozpoznanej stawki; sprawdź podział z oryginałem."
    if not fields.get("invoice_number"):
        warnings.append("Nie rozpoznano jednoznacznie numeru faktury.")
    return fields, evidence, warnings
