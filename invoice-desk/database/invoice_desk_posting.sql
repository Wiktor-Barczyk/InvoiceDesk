ALTER TABLE core.staff_users DROP CONSTRAINT IF EXISTS staff_users_role_check;
ALTER TABLE core.staff_users ADD CONSTRAINT staff_users_role_check
CHECK (role IN ('admin','chief_accountant','accountant','reviewer','read_only'));

INSERT INTO accounting.accounts(account_code, account_name, account_type, allow_reconciliation)
VALUES ('130','Debiteuren','asset',true),
       ('140','Crediteuren','liability',true),
       ('150','Te vorderen BTW','asset',false),
       ('151','Te betalen BTW','liability',false)
ON CONFLICT (account_code) DO NOTHING;

CREATE TABLE IF NOT EXISTS accounting.invoice_desk_corrections (
    previous_invoice_id bigint PRIMARY KEY REFERENCES accounting.invoices(id) ON DELETE RESTRICT,
    replacement_invoice_id bigint UNIQUE NOT NULL REFERENCES accounting.invoices(id) ON DELETE RESTRICT,
    reversal_entry_id bigint UNIQUE NOT NULL REFERENCES accounting.general_ledger_entries(id) ON DELETE RESTRICT,
    reason text NOT NULL,
    staff_id bigint REFERENCES core.staff_users(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (previous_invoice_id <> replacement_invoice_id)
);
