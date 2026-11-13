ALTER TABLE assets.invoice_desk_depreciation_plans
    ADD COLUMN IF NOT EXISTS notes varchar(1000),
    ADD COLUMN IF NOT EXISTS loss_type varchar(10),
    ADD COLUMN IF NOT EXISTS loss_date date;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid='assets.invoice_desk_depreciation_plans'::regclass
          AND conname='invoice_desk_depreciation_loss_valid'
    ) THEN
        ALTER TABLE assets.invoice_desk_depreciation_plans
            ADD CONSTRAINT invoice_desk_depreciation_loss_valid CHECK (
                (loss_type IS NULL AND loss_date IS NULL)
                OR (loss_type IS NOT NULL AND loss_type IN ('stolen','lost')
                    AND loss_date IS NOT NULL AND loss_date>=in_use_date)
            );
    END IF;
END $$;
