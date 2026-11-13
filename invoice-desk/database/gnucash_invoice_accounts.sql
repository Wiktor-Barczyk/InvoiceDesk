-- Optional Invoice Desk chart, based on a GnuCash chart.
-- Use for this portal's invoice items; settlement accounts stay out of the picker.
CREATE TABLE IF NOT EXISTS accounting.invoice_desk_account_catalog (
    account_id BIGINT NOT NULL REFERENCES accounting.accounts(id) ON DELETE RESTRICT,
    invoice_type VARCHAR(8) NOT NULL CHECK (invoice_type IN ('purchase', 'sale')),
    PRIMARY KEY (account_id, invoice_type)
);

CREATE TEMP TABLE invoice_desk_chart (
    code TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, direction TEXT NOT NULL
) ON COMMIT DROP;
INSERT INTO invoice_desk_chart VALUES
    ('002', 'Inventaris', 'asset', 'purchase'),
    ('041', 'Lening', 'liability', 'purchase'),
    ('144', 'Vooruitbetaalde kosten', 'asset', 'purchase'),
    ('430', 'Transportkosten', 'expense', 'purchase'),
    ('440', 'Algemene kosten', 'expense', 'purchase'),
    ('450', 'Representatiekosten', 'expense', 'purchase'),
    ('460', 'Kosten BTW Verlegd', 'expense', 'purchase'),
    ('490', 'EU kosten', 'expense', 'purchase'),
    ('492210', 'Rente leningen', 'expense', 'purchase'),
    ('700', 'Vorrad goederen', 'asset', 'purchase'),
    ('800', 'Inkoopwaarde Verkopen', 'expense', 'purchase'),
    ('820', 'Verkopen 9% BTW', 'income', 'sale'),
    ('830', 'Verkopen 21% BTW', 'income', 'sale'),
    ('840', 'Verlegd BTW Verkoop', 'income', 'sale'),
    ('850', 'Verkoop BTW vrijgesteld', 'income', 'sale'),
    ('860', 'EU ICP Verkoop', 'income', 'sale');

INSERT INTO accounting.accounts (account_code, account_name, account_type)
SELECT code, name, kind FROM invoice_desk_chart
ON CONFLICT (account_code) DO NOTHING;

DO $$
DECLARE
    conflicts TEXT;
BEGIN
    SELECT string_agg(s.code, ', ' ORDER BY s.code) INTO conflicts
    FROM invoice_desk_chart s
    JOIN accounting.accounts a ON a.account_code = s.code
    WHERE a.account_type <> s.kind
       OR lower(btrim(regexp_replace(a.account_name, '^' || s.code || '[[:space:]]+', ''))) <> lower(s.name)
       OR NOT a.is_active OR NOT a.allow_direct_posting;
    IF conflicts IS NOT NULL THEN
        RAISE EXCEPTION 'Konflikt istniejących kont GnuCash: %. Nie zmieniono kartoteki.', conflicts;
    END IF;
END
$$;

INSERT INTO accounting.invoice_desk_account_catalog (account_id, invoice_type)
SELECT a.id, s.direction FROM invoice_desk_chart s
JOIN accounting.accounts a ON a.account_code = s.code
ON CONFLICT DO NOTHING;

-- 0% and exemption remain distinct; retain the IDs of existing VAT entries.
INSERT INTO accounting.vat_codes (vat_code, vat_name, vat_rate_percent, vat_direction)
VALUES ('PORTAL_0', 'VAT 0%', 0, 'both'), ('PORTAL_9', 'VAT 9%', 9, 'both'),
       ('PORTAL_21', 'VAT 21%', 21, 'both'), ('PORTAL_EXEMPT', 'VAT zwolniony', NULL, 'both')
ON CONFLICT (vat_code) DO UPDATE SET vat_name = EXCLUDED.vat_name, is_active = TRUE
WHERE vat_codes.vat_rate_percent IS NOT DISTINCT FROM EXCLUDED.vat_rate_percent
  AND vat_codes.vat_direction = 'both'
  AND vat_codes.valid_from IS NULL AND vat_codes.valid_to IS NULL;

DO $$
BEGIN
    IF (SELECT count(*) FROM accounting.vat_codes WHERE is_active AND vat_direction = 'both'
        AND valid_from IS NULL AND valid_to IS NULL AND (
            (vat_code = 'PORTAL_0' AND vat_rate_percent = 0)
         OR (vat_code = 'PORTAL_9' AND vat_rate_percent = 9)
         OR (vat_code = 'PORTAL_21' AND vat_rate_percent = 21)
         OR (vat_code = 'PORTAL_EXEMPT' AND vat_rate_percent IS NULL))) <> 4 THEN
        RAISE EXCEPTION 'Konflikt konfiguracji kodów VAT. Nie zmieniono kartoteki.';
    END IF;
END
$$;
