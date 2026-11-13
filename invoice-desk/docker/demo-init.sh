#!/usr/bin/env bash
# Initialize a fresh PostgreSQL demo volume with the public schema.
invoice_demo_sql() {
    psql --no-psqlrc -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" "$@"
}

invoice_demo_sql -f /invoice-desk/database/schema_v5.sql
for migration in /invoice-desk/migrations/*.sql; do
    invoice_demo_sql --single-transaction -f "$migration"
done
for extension in invoice_desk_posting invoice_desk_inventaris invoice_desk_inventaris_events gnucash_invoice_accounts; do
    invoice_demo_sql --single-transaction -f "/invoice-desk/database/${extension}.sql"
done
