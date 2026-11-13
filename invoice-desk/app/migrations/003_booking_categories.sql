-- Operational categories/accounts. VAT rates belong to VAT codes.
UPDATE accounting.staff_booking_categories
SET category_name=regexp_replace(category_name, '^(21|9|0)%[[:space:]]*', '')
WHERE category_name ~ '^(21|9|0)%[[:space:]]*';
WITH labels(name) AS (VALUES
 ('Verlegd netto'),('Verlegd podatek (Btw)'),('Transportkosten'),
 ('Transportkosten podatek (Btw)'),('Transportkosten'),('Transportkosten'),
 ('Representatiekosten'),('Inventaris'),('Inventaris'),('Inventaris'),
 ('netto'),('podatek (Btw)'),('netto'),('podatek (Btw)'),
 ('netto'),('Vrijgesteld netto'),('EU Kosten'),('EU ICP Verkoop')
)
INSERT INTO accounting.staff_booking_categories(category_code,category_name)
SELECT 'ODS_'||substr(md5(name),1,20),name FROM labels
WHERE NOT EXISTS (SELECT 1 FROM accounting.staff_booking_categories c
                  WHERE lower(btrim(c.category_name))=lower(labels.name))
ON CONFLICT(category_code) DO NOTHING;
