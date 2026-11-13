-- One operational category/account is sufficient for a portal invoice.
-- Existing ledger data and foreign keys are retained for other modules.
ALTER TABLE accounting.invoice_lines ALTER COLUMN account_id DROP NOT NULL;
COMMENT ON COLUMN accounting.invoice_lines.booking_category_id IS
'Wspólna kategoria / konto portalu, niezależna od stawki i kierunku kodu VAT.';
COMMENT ON COLUMN accounting.invoice_lines.account_id IS
'Opcjonalne powiązanie ze starszym planem kont księgi głównej; portal nie wymaga go do zatwierdzenia faktury.';
