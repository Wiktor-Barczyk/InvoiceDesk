"""Generate fictional documents for the isolated demo and automated tests."""
from functools import lru_cache

import fitz


@lru_cache(maxsize=1)
def sample_invoice() -> bytes:
    """Return a synthetic invoice without addresses or tax and bank identifiers."""
    lines = (
        "SAMPLE INVOICE - NOT FOR PAYMENT",
        "",
        "Factuurnummer: FV-2026-0924",
        "Factuurdatum: 24-09-2026",
        "Vervaldatum: 24-10-2026",
        "Leverancier: Example Materials B.V.",
        "Afnemer: Example Construction B.V.",
        "",
        "Description              Qty   Unit price   Net",
        "Sample flooring          20    24,50        490,00",
        "Sample panels            12    35,00        420,00",
        "Sample fittings          8     35,00        280,00",
        "",
        "Totaal netto: EUR 1190,00",
        "BTW 21%: EUR 249,90",
        "Te betalen: EUR 1439,90",
    )
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((42, 58), "\n".join(lines), fontname="cour", fontsize=13, lineheight=1.5)
        return pdf.tobytes(no_new_id=True)
