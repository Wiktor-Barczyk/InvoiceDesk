CREATE TABLE IF NOT EXISTS assets.invoice_desk_depreciation_plans (
    invoice_line_id bigint PRIMARY KEY REFERENCES accounting.invoice_lines(id) ON DELETE RESTRICT,
    asset_name varchar(255) NOT NULL,
    in_use_date date NOT NULL,
    acquisition_cost numeric(18,2) NOT NULL CHECK (acquisition_cost > 0),
    residual_value numeric(18,2) NOT NULL CHECK (residual_value BETWEEN 0 AND acquisition_cost),
    useful_life_months integer NOT NULL CHECK (useful_life_months BETWEEN 1 AND 600),
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    updated_by bigint REFERENCES core.staff_users(id) ON DELETE SET NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
