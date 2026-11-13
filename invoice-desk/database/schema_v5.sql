-- ============================================================================
-- Invoice Desk - schema księgowy
-- PostgreSQL schema V5 FINAL
--
-- Schemat przechowuje dane księgowe i dokumenty. Identyfikatory Odoo są
-- opcjonalnym powiązaniem z systemem zewnętrznym na poziomie integracji.
-- Korekty zaksięgowanych danych zachowują historię zapisów.
-- ============================================================================

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS documents;
CREATE SCHEMA IF NOT EXISTS accounting;
CREATE SCHEMA IF NOT EXISTS banking;
CREATE SCHEMA IF NOT EXISTS assets;
CREATE SCHEMA IF NOT EXISTS payroll;
CREATE SCHEMA IF NOT EXISTS tax;
CREATE SCHEMA IF NOT EXISTS audit;

-- ============================================================================
-- CORE - KLIENCI, OSOBY, USTAWIENIA PODATKOWE, LATA OBROTOWE
-- ============================================================================

CREATE TABLE core.staff_users (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id           UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    odoo_user_id        BIGINT NULL,
    email               VARCHAR(320) NOT NULL UNIQUE,
    full_name           VARCHAR(255) NOT NULL,
    role                VARCHAR(30) NOT NULL CHECK (role IN ('admin','accountant','reviewer','read_only')),
    password            TEXT NOT NULL DEFAULT '',
    last_login          TIMESTAMPTZ NULL,
    is_superuser        BOOLEAN NOT NULL DEFAULT FALSE,
    is_staff            BOOLEAN NOT NULL DEFAULT TRUE,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX uq_staff_users_odoo_user_id
    ON core.staff_users(odoo_user_id)
    WHERE odoo_user_id IS NOT NULL;

COMMENT ON TABLE core.staff_users IS 'Pracownicy korzystający z systemu księgowego. Nie są to klienci portalu.';

CREATE TABLE core.clients (
    id                      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id               UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    odoo_partner_id         BIGINT NOT NULL UNIQUE,
    company_name            VARCHAR(255) NOT NULL,
    legal_form              VARCHAR(30) NOT NULL CHECK (legal_form IN ('eenmanszaak','vof','bv','stichting','vereniging','other')),
    kvk_number              VARCHAR(20) NULL,
    email                   VARCHAR(320) NULL,
    phone                   VARCHAR(50) NULL,
    street                  VARCHAR(255) NULL,
    postal_code             VARCHAR(20) NULL,
    city                    VARCHAR(120) NULL,
    country_code            CHAR(2) NOT NULL DEFAULT 'NL',
    default_currency        CHAR(3) NOT NULL DEFAULT 'EUR',
    is_active               BOOLEAN NOT NULL DEFAULT TRUE,
    closed_at               TIMESTAMPTZ NULL,
    last_synced_from_odoo_at TIMESTAMPTZ NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX uq_clients_kvk_number
    ON core.clients(kvk_number)
    WHERE kvk_number IS NOT NULL;

COMMENT ON TABLE core.clients IS 'Klienci księgowi synchronizowani z Odoo. odoo_partner_id jest stałym łącznikiem z res.partner.';

CREATE TABLE core.persons (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id           UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    first_name          VARCHAR(120) NOT NULL,
    middle_name         VARCHAR(120) NULL,
    last_name           VARCHAR(160) NOT NULL,
    birth_date          DATE NULL,
    email               VARCHAR(320) NULL,
    phone               VARCHAR(50) NULL,
    bsn_encrypted       BYTEA NULL,
    bsn_hash            CHAR(64) NULL,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX uq_persons_bsn_hash
    ON core.persons(bsn_hash)
    WHERE bsn_hash IS NOT NULL;

COMMENT ON TABLE core.persons IS 'Osoby fizyczne związane z klientem: właściciel, wspólnik VOF, DGA, udziałowiec.';

CREATE TABLE core.client_person_roles (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    client_id           BIGINT NOT NULL REFERENCES core.clients(id) ON DELETE RESTRICT,
    person_id           BIGINT NOT NULL REFERENCES core.persons(id) ON DELETE RESTRICT,
    role                VARCHAR(30) NOT NULL CHECK (role IN ('owner','partner','dga','director','shareholder','contact','employee','fiscal_partner','other')),
    ownership_percent   NUMERIC(7,4) NULL CHECK (ownership_percent IS NULL OR ownership_percent BETWEEN 0 AND 100),
    valid_from          DATE NULL,
    valid_to            DATE NULL,
    is_primary          BOOLEAN NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from),
    UNIQUE(client_id, person_id, role, valid_from)
);

CREATE INDEX idx_client_person_roles_client
    ON core.client_person_roles(client_id, role);

COMMENT ON TABLE core.client_person_roles IS 'Relacja klient-firma do osób: np. Pavlo Team B.V. -> osoba -> DGA 100%.';

CREATE TABLE core.client_tax_profiles (
    client_id               BIGINT PRIMARY KEY REFERENCES core.clients(id) ON DELETE RESTRICT,
    vat_frequency           VARCHAR(20) NOT NULL DEFAULT 'quarterly' CHECK (vat_frequency IN ('monthly','quarterly','yearly','none')),
    vat_accounting_method   VARCHAR(20) NOT NULL DEFAULT 'invoice' CHECK (vat_accounting_method IN ('invoice','cash')),
    fiscal_year_start_month SMALLINT NOT NULL DEFAULT 1 CHECK (fiscal_year_start_month BETWEEN 1 AND 12),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE core.client_tax_profiles IS 'Stałe ustawienia podatkowe klienta: częstotliwość BTW, metoda VAT, początek roku obrotowego.';

CREATE TABLE core.client_tax_registrations (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    client_id           BIGINT NOT NULL REFERENCES core.clients(id) ON DELETE RESTRICT,
    registration_type   VARCHAR(30) NOT NULL CHECK (registration_type IN ('BTW','ICP','IB','VPB','LH','RSIN','OTHER')),
    registration_number VARCHAR(100) NOT NULL,
    valid_from          DATE NULL,
    valid_to            DATE NULL,
    status              VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active','inactive','pending','revoked')),
    notes               TEXT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from)
);

CREATE INDEX idx_client_tax_registrations_client
    ON core.client_tax_registrations(client_id, registration_type);

COMMENT ON TABLE core.client_tax_registrations IS 'Numery rejestracyjne podatkowe klienta wraz z historią: BTW, VPB, loonheffingen itd.';

CREATE TABLE core.client_vat_scheme_periods (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    client_id           BIGINT NOT NULL REFERENCES core.clients(id) ON DELETE RESTRICT,
    vat_scheme          VARCHAR(20) NOT NULL CHECK (vat_scheme IN ('standard','kor','other')),
    valid_from          DATE NOT NULL,
    valid_to            DATE NULL,
    notes               TEXT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (valid_to IS NULL OR valid_to >= valid_from),
    UNIQUE(client_id, vat_scheme, valid_from)
);

COMMENT ON TABLE core.client_vat_scheme_periods IS 'Historia KOR / zwykłego BTW. Nie zapisujemy KOR jako prostego pola tak/nie.';

CREATE TABLE core.client_tax_year_settings (
    client_id                       BIGINT NOT NULL REFERENCES core.clients(id) ON DELETE RESTRICT,
    tax_year                        SMALLINT NOT NULL CHECK (tax_year BETWEEN 2000 AND 2200),
    hours_criterion_met             BOOLEAN NULL,
    zelfstandigenaftrek_eligible    BOOLEAN NULL,
    startersaftrek_eligible         BOOLEAN NULL,
    startersaftrek_applied          BOOLEAN NULL,
    fiscal_partner_person_id        BIGINT NULL REFERENCES core.persons(id) ON DELETE SET NULL,
    notes                           TEXT NULL,
    created_at                      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (client_id, tax_year)
);

COMMENT ON TABLE core.client_tax_year_settings IS 'Ustawienia zależne od roku podatkowego, np. urencriterium i wykorzystanie startersaftrek.';

CREATE TABLE core.client_fiscal_years (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    client_id           BIGINT NOT NULL REFERENCES core.clients(id) ON DELETE RESTRICT,
    year_code           VARCHAR(20) NOT NULL,
    date_from           DATE NOT NULL,
    date_to             DATE NOT NULL,
    status              VARCHAR(20) NOT NULL DEFAULT 'open' CHECK (status IN ('open','closing','closed')),
    closed_at           TIMESTAMPTZ NULL,
    closed_by_staff_id  BIGINT NULL REFERENCES core.staff_users(id) ON DELETE SET NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (date_to >= date_from),
    UNIQUE(client_id, year_code)
);

COMMENT ON TABLE core.client_fiscal_years IS 'Rok obrotowy klienta. Umożliwia też później obsługę BV z nietypowym rokiem obrotowym.';

CREATE TABLE core.client_counterparties (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id           UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    client_id           BIGINT NOT NULL REFERENCES core.clients(id) ON DELETE RESTRICT,
    name                VARCHAR(255) NOT NULL,
    vat_number          VARCHAR(40) NULL,
    kvk_number          VARCHAR(20) NULL,
    iban                VARCHAR(34) NULL,
    email               VARCHAR(320) NULL,
    address             TEXT NULL,
    is_customer         BOOLEAN NOT NULL DEFAULT FALSE,
    is_supplier         BOOLEAN NOT NULL DEFAULT FALSE,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_client_counterparties_name
    ON core.client_counterparties(client_id, name);
CREATE INDEX idx_client_counterparties_vat
    ON core.client_counterparties(vat_number) WHERE vat_number IS NOT NULL;
CREATE INDEX idx_client_counterparties_iban
    ON core.client_counterparties(iban) WHERE iban IS NOT NULL;

COMMENT ON TABLE core.client_counterparties IS 'Dostawcy i odbiorcy danego klienta księgowego.';

-- ============================================================================
-- DOCUMENTS - DOKUMENTY KSIĘGOWE, PLIKI, OCR, IMPORTY
-- ============================================================================

CREATE TABLE documents.client_document_intake_channels (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    client_id           BIGINT NOT NULL REFERENCES core.clients(id) ON DELETE RESTRICT,
    channel_type        VARCHAR(20) NOT NULL CHECK (channel_type IN ('email','nextcloud','file_drop','upload','api','odoo')),
    routing_key         VARCHAR(255) NULL,
    external_reference  TEXT NULL,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX uq_document_intake_routing_key
    ON documents.client_document_intake_channels(channel_type, routing_key)
    WHERE routing_key IS NOT NULL;

COMMENT ON TABLE documents.client_document_intake_channels IS 'Kanały, po których system rozpoznaje klienta przy wpływie dokumentu: adres e-mail, folder Nextcloud itd.';

CREATE TABLE documents.stored_files (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id           UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    sha256              CHAR(64) NOT NULL UNIQUE,
    storage_backend     VARCHAR(30) NOT NULL CHECK (storage_backend IN ('nextcloud','filesystem','s3','other')),
    storage_path        TEXT NOT NULL,
    mime_type           VARCHAR(150) NULL,
    file_size_bytes     BIGINT NULL CHECK (file_size_bytes IS NULL OR file_size_bytes >= 0),
    original_file_id    BIGINT NULL REFERENCES documents.stored_files(id) ON DELETE RESTRICT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE documents.stored_files IS 'Fizyczne pliki w magazynie. SHA256 służy do wykrywania duplikatów.';

CREATE TABLE documents.accounting_documents (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id           UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    client_id           BIGINT NULL REFERENCES core.clients(id) ON DELETE RESTRICT,
    intake_channel_id   BIGINT NULL REFERENCES documents.client_document_intake_channels(id) ON DELETE SET NULL,
    document_type       VARCHAR(30) NOT NULL DEFAULT 'unknown' CHECK (document_type IN ('unknown','purchase_invoice','sales_invoice','receipt','bank_statement','contract','payment_proof','payroll','tax','other')),
    source              VARCHAR(30) NOT NULL CHECK (source IN ('email','nextcloud','upload','api','import','odoo')),
    source_reference    TEXT NULL,
    description         TEXT NULL,
    status              VARCHAR(30) NOT NULL DEFAULT 'received' CHECK (status IN ('received','ocr_processing','to_book','needs_explanation','waiting_for_documents','booked','included_in_vat','archived','rejected')),
    received_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by_staff_id BIGINT NULL REFERENCES core.staff_users(id) ON DELETE SET NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_accounting_documents_client_status
    ON documents.accounting_documents(client_id, status);
CREATE INDEX idx_accounting_documents_received
    ON documents.accounting_documents(received_at);

COMMENT ON TABLE documents.accounting_documents IS 'Logiczny dokument księgowy. Może wskazywać jeden lub kilka fizycznych plików.';

CREATE TABLE documents.accounting_document_files (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    accounting_document_id BIGINT NOT NULL REFERENCES documents.accounting_documents(id) ON DELETE CASCADE,
    stored_file_id      BIGINT NOT NULL REFERENCES documents.stored_files(id) ON DELETE RESTRICT,
    original_filename   VARCHAR(500) NOT NULL,
    file_role           VARCHAR(20) NOT NULL DEFAULT 'original' CHECK (file_role IN ('original','preview','attachment','other')),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(accounting_document_id, stored_file_id, file_role)
);

COMMENT ON TABLE documents.accounting_document_files IS 'Łączy logiczny dokument z jego plikami PDF/JPG/preview/załącznikami.';

CREATE TABLE documents.document_ocr_runs (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    accounting_document_id BIGINT NOT NULL REFERENCES documents.accounting_documents(id) ON DELETE CASCADE,
    ocr_engine          VARCHAR(80) NOT NULL,
    ocr_engine_version  VARCHAR(80) NULL,
    raw_result          JSONB NOT NULL DEFAULT '{}'::jsonb,
    extracted_fields    JSONB NOT NULL DEFAULT '{}'::jsonb,
    confidence          NUMERIC(5,4) NULL CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
    status              VARCHAR(20) NOT NULL DEFAULT 'completed' CHECK (status IN ('started','completed','failed')),
    processed_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_document_ocr_runs_document
    ON documents.document_ocr_runs(accounting_document_id, processed_at);

COMMENT ON TABLE documents.document_ocr_runs IS 'Historia kolejnych prób OCR/AI dla dokumentu. Wynik nie jest nadpisywany.';

CREATE TABLE documents.document_imports (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id           UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    client_id           BIGINT NULL REFERENCES core.clients(id) ON DELETE RESTRICT,
    import_type         VARCHAR(30) NOT NULL CHECK (import_type IN ('ods','csv','camt','mt940','bank_api','nextcloud','email','api','odoo')),
    stored_file_id      BIGINT NULL REFERENCES documents.stored_files(id) ON DELETE RESTRICT,
    original_filename   VARCHAR(500) NULL,
    file_hash           CHAR(64) NULL,
    source_template_version VARCHAR(30) NULL,
    period_start        DATE NULL,
    period_end          DATE NULL,
    status              VARCHAR(30) NOT NULL DEFAULT 'started' CHECK (status IN ('started','completed','failed','partially_completed')),
    import_details      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by_staff_id BIGINT NULL REFERENCES core.staff_users(id) ON DELETE SET NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at        TIMESTAMPTZ NULL,
    CHECK (period_end IS NULL OR period_start IS NULL OR period_end >= period_start)
);

CREATE INDEX idx_document_imports_client_created
    ON documents.document_imports(client_id, created_at);
CREATE INDEX idx_document_imports_hash
    ON documents.document_imports(file_hash) WHERE file_hash IS NOT NULL;

COMMENT ON TABLE documents.document_imports IS 'Nagłówek każdego importu danych: np. kwartalnego arkusza, CAMT, MT940 albo pobrania bankowego API. source_template_version pozwala zachować wersję arkusza, np. 1.0.';

CREATE TABLE documents.document_import_items (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_import_id  BIGINT NOT NULL REFERENCES documents.document_imports(id) ON DELETE CASCADE,
    source_key          VARCHAR(255) NOT NULL,
    source_sheet        VARCHAR(255) NULL,
    source_position     VARCHAR(100) NULL,
    imported_item_type  VARCHAR(30) NOT NULL CHECK (imported_item_type IN ('invoice','bank_transaction','document','other')),
    status              VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','imported','skipped','error')),
    error_message       TEXT NULL,
    created_record_type VARCHAR(80) NULL,
    created_record_id   BIGINT NULL,
    raw_source_data     JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(document_import_id, source_key)
);

COMMENT ON TABLE documents.document_import_items IS 'Każdy pojedynczy rekord z importu, np. jedna kolumna/faktura z ODS. Zapobiega podwójnemu importowi.';

-- ============================================================================
-- ACCOUNTING - PLAN KONT, FAKTURY, KSIĘGA GŁÓWNA, VAT
-- ============================================================================

CREATE TABLE accounting.accounts (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_code        VARCHAR(20) NOT NULL UNIQUE,
    account_name        VARCHAR(255) NOT NULL,
    account_type        VARCHAR(20) NOT NULL CHECK (account_type IN ('asset','liability','equity','income','expense')),
    parent_account_id   BIGINT NULL REFERENCES accounting.accounts(id) ON DELETE RESTRICT,
    allow_direct_posting BOOLEAN NOT NULL DEFAULT TRUE,
    allow_reconciliation BOOLEAN NOT NULL DEFAULT FALSE,
    report_group        VARCHAR(80) NULL,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_accounts_parent
    ON accounting.accounts(parent_account_id);

COMMENT ON TABLE accounting.accounts IS 'Docelowy plan kont. Z tej tabeli korzysta księga główna, bilans i W&V.';

CREATE TABLE accounting.staff_booking_categories (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    category_code       VARCHAR(50) NOT NULL UNIQUE,
    category_name       VARCHAR(255) NOT NULL,
    description         TEXT NULL,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE accounting.staff_booking_categories IS 'Proste kategorie widoczne pracownikowi, np. Telefon, Transport, Materiały, Inventaris.';

CREATE TABLE accounting.booking_category_account_mappings (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    client_id           BIGINT NULL REFERENCES core.clients(id) ON DELETE RESTRICT,
    booking_category_id BIGINT NOT NULL REFERENCES accounting.staff_booking_categories(id) ON DELETE RESTRICT,
    account_id          BIGINT NOT NULL REFERENCES accounting.accounts(id) ON DELETE RESTRICT,
    default_vat_code_id BIGINT NULL,
    priority            INTEGER NOT NULL DEFAULT 100,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE accounting.booking_category_account_mappings IS 'Mapuje prostą kategorię pracownika na prawdziwe konto księgowe; może mieć wyjątek dla konkretnego klienta.';

CREATE TABLE accounting.vat_codes (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    vat_code            VARCHAR(40) NOT NULL UNIQUE,
    vat_name            VARCHAR(255) NOT NULL,
    vat_rate_percent    NUMERIC(6,3) NULL CHECK (vat_rate_percent IS NULL OR vat_rate_percent >= 0),
    vat_direction       VARCHAR(20) NOT NULL CHECK (vat_direction IN ('input','output','both','none')),
    valid_from          DATE NULL,
    valid_to            DATE NULL,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from)
);

COMMENT ON TABLE accounting.vat_codes IS 'Kody podatkowe VAT używane podczas księgowania, niezależne od układu deklaracji BTW.';

ALTER TABLE accounting.booking_category_account_mappings
    ADD CONSTRAINT fk_booking_mapping_default_vat
    FOREIGN KEY (default_vat_code_id) REFERENCES accounting.vat_codes(id) ON DELETE RESTRICT;

CREATE UNIQUE INDEX uq_booking_mapping_global
    ON accounting.booking_category_account_mappings(booking_category_id)
    WHERE client_id IS NULL;
CREATE UNIQUE INDEX uq_booking_mapping_client
    ON accounting.booking_category_account_mappings(client_id, booking_category_id)
    WHERE client_id IS NOT NULL;

CREATE TABLE accounting.vat_declaration_mapping_rules (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    vat_code_id         BIGINT NOT NULL REFERENCES accounting.vat_codes(id) ON DELETE RESTRICT,
    declaration_type    VARCHAR(20) NOT NULL DEFAULT 'BTW' CHECK (declaration_type IN ('BTW','ICP')),
    declaration_box     VARCHAR(20) NOT NULL,
    declaration_field   VARCHAR(20) NOT NULL CHECK (declaration_field IN ('turnover','output_vat','input_vat','amount')),
    source_value        VARCHAR(20) NOT NULL CHECK (source_value IN ('taxable_amount','output_vat','input_vat')),
    multiplier          NUMERIC(10,4) NOT NULL DEFAULT 1,
    valid_from          DATE NULL,
    valid_to            DATE NULL,
    CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from),
    UNIQUE(vat_code_id, declaration_type, declaration_box, declaration_field, valid_from)
);

COMMENT ON TABLE accounting.vat_declaration_mapping_rules IS 'Mówi, do którego pola deklaracji BTW/ICP trafia dany kod VAT.';

CREATE TABLE accounting.currency_exchange_rates (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    rate_date           DATE NOT NULL,
    source_currency     CHAR(3) NOT NULL,
    target_currency     CHAR(3) NOT NULL DEFAULT 'EUR',
    exchange_rate       NUMERIC(20,10) NOT NULL CHECK (exchange_rate > 0),
    rate_source         VARCHAR(80) NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(rate_date, source_currency, target_currency, rate_source)
);

COMMENT ON TABLE accounting.currency_exchange_rates IS 'Kursy walut używane do księgowania dokumentów innych niż EUR.';

CREATE TABLE accounting.invoices (
    id                      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id               UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    client_id               BIGINT NOT NULL REFERENCES core.clients(id) ON DELETE RESTRICT,
    counterparty_id         BIGINT NULL REFERENCES core.client_counterparties(id) ON DELETE RESTRICT,
    source_import_id        BIGINT NULL REFERENCES documents.document_imports(id) ON DELETE SET NULL,
    invoice_type            VARCHAR(10) NOT NULL CHECK (invoice_type IN ('purchase','sale')),
    invoice_kind            VARCHAR(20) NOT NULL DEFAULT 'invoice' CHECK (invoice_kind IN ('invoice','credit_note','self_billing','other')),
    invoice_number          VARCHAR(100) NULL,
    invoice_date            DATE NOT NULL,
    vat_date                DATE NOT NULL,
    due_date                DATE NULL,
    currency                CHAR(3) NOT NULL DEFAULT 'EUR',
    exchange_rate_to_eur    NUMERIC(20,10) NULL CHECK (exchange_rate_to_eur IS NULL OR exchange_rate_to_eur > 0),
    net_total               NUMERIC(18,2) NOT NULL CHECK (net_total >= 0),
    vat_total               NUMERIC(18,2) NOT NULL CHECK (vat_total >= 0),
    gross_total             NUMERIC(18,2) NOT NULL CHECK (gross_total >= 0),
    seller_name_snapshot        VARCHAR(255) NULL,
    seller_vat_number_snapshot  VARCHAR(40) NULL,
    seller_iban_snapshot        VARCHAR(34) NULL,
    buyer_name_snapshot         VARCHAR(255) NULL,
    buyer_vat_number_snapshot   VARCHAR(40) NULL,
    notes                       TEXT NULL,
    status                  VARCHAR(20) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','review','approved','posted','cancelled')),
    payment_status          VARCHAR(20) NOT NULL DEFAULT 'unknown' CHECK (payment_status IN ('unknown','unpaid','partial','paid')),
    import_source_key       VARCHAR(255) NULL,
    general_ledger_entry_id BIGINT NULL,
    created_by_staff_id     BIGINT NULL REFERENCES core.staff_users(id) ON DELETE SET NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (gross_total = net_total + vat_total)
);

CREATE INDEX idx_invoices_client_date
    ON accounting.invoices(client_id, invoice_date);
CREATE INDEX idx_invoices_counterparty
    ON accounting.invoices(counterparty_id);
CREATE INDEX idx_invoices_status
    ON accounting.invoices(client_id, status);
CREATE UNIQUE INDEX uq_invoices_import_source_key
    ON accounting.invoices(client_id, import_source_key)
    WHERE import_source_key IS NOT NULL;

COMMENT ON TABLE accounting.invoices IS 'Nagłówki faktur zakupu/sprzedaży. Nazwa sprzedawcy i nabywcy oraz ich BTW-ID są zachowywane jako snapshot.';

CREATE TABLE accounting.invoice_lines (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    invoice_id          BIGINT NOT NULL REFERENCES accounting.invoices(id) ON DELETE CASCADE,
    line_number         INTEGER NOT NULL CHECK (line_number > 0),
    description         TEXT NULL,
    quantity            NUMERIC(18,4) NULL,
    unit_price          NUMERIC(18,4) NULL,
    net_amount          NUMERIC(18,2) NOT NULL CHECK (net_amount >= 0),
    vat_amount          NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (vat_amount >= 0),
    booking_category_id BIGINT NULL REFERENCES accounting.staff_booking_categories(id) ON DELETE RESTRICT,
    account_id          BIGINT NULL REFERENCES accounting.accounts(id) ON DELETE RESTRICT,
    vat_code_id         BIGINT NULL REFERENCES accounting.vat_codes(id) ON DELETE RESTRICT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(invoice_id, line_number)
);

COMMENT ON TABLE accounting.invoice_lines IS 'Pozycje faktury. Każda pozycja może mieć własne konto, kategorię pracownika i kod VAT.';

CREATE TABLE accounting.invoice_source_documents (
    invoice_id          BIGINT NOT NULL REFERENCES accounting.invoices(id) ON DELETE CASCADE,
    accounting_document_id BIGINT NOT NULL REFERENCES documents.accounting_documents(id) ON DELETE RESTRICT,
    document_role       VARCHAR(20) NOT NULL DEFAULT 'source' CHECK (document_role IN ('source','supporting','payment_proof','other')),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (invoice_id, accounting_document_id, document_role)
);

COMMENT ON TABLE accounting.invoice_source_documents IS 'Łączy fakturę z dokumentami źródłowymi. Jeden PDF może zawierać wiele faktur i odwrotnie.';


CREATE TABLE accounting.invoice_vat_number_verifications (
    id                      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    invoice_id              BIGINT NOT NULL REFERENCES accounting.invoices(id) ON DELETE CASCADE,
    party_role              VARCHAR(10) NOT NULL CHECK (party_role IN ('seller','buyer')),
    vat_number              VARCHAR(40) NOT NULL,
    country_code            CHAR(2) NULL,
    verification_source     VARCHAR(30) NOT NULL DEFAULT 'VIES',
    is_valid                BOOLEAN NULL,
    verified_name           VARCHAR(255) NULL,
    verified_address        TEXT NULL,
    verification_reference  TEXT NULL,
    raw_verification_result JSONB NOT NULL DEFAULT '{}'::jsonb,
    checked_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    checked_by_staff_id     BIGINT NULL REFERENCES core.staff_users(id) ON DELETE SET NULL
);

CREATE INDEX idx_invoice_vat_number_verifications_invoice
    ON accounting.invoice_vat_number_verifications(invoice_id, checked_at);
CREATE INDEX idx_invoice_vat_number_verifications_number
    ON accounting.invoice_vat_number_verifications(vat_number, checked_at);

COMMENT ON TABLE accounting.invoice_vat_number_verifications IS 'Historia weryfikacji BTW-ID/VIES dla sprzedawcy lub nabywcy. Odpowiada m.in. obecnej kontroli Czy aktywny NIP EU nabywcy.';

CREATE TABLE accounting.invoice_validation_checks (
    id                      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    invoice_id              BIGINT NOT NULL REFERENCES accounting.invoices(id) ON DELETE CASCADE,
    check_code              VARCHAR(80) NOT NULL,
    check_status            VARCHAR(20) NOT NULL DEFAULT 'pending'
                            CHECK (check_status IN ('pending','passed','warning','failed','not_applicable')),
    check_source            VARCHAR(20) NOT NULL DEFAULT 'system'
                            CHECK (check_source IN ('system','import','manual','external')),
    details                 JSONB NOT NULL DEFAULT '{}'::jsonb,
    checked_at              TIMESTAMPTZ NULL,
    checked_by_staff_id     BIGINT NULL REFERENCES core.staff_users(id) ON DELETE SET NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(invoice_id, check_code)
);

CREATE INDEX idx_invoice_validation_checks_status
    ON accounting.invoice_validation_checks(check_status, check_code);

COMMENT ON TABLE accounting.invoice_validation_checks IS 'Kontrole faktury. Zastępuje w arkuszu m.in. Kontrola przepisania, Kontrola wyliczeń, Duplikat, Kontrola ICP Verkoop oraz Inventaris obsłużone.';

CREATE TABLE accounting.general_ledger_entries (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id           UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    client_id           BIGINT NOT NULL REFERENCES core.clients(id) ON DELETE RESTRICT,
    fiscal_year_id      BIGINT NULL REFERENCES core.client_fiscal_years(id) ON DELETE RESTRICT,
    entry_date          DATE NOT NULL,
    entry_type          VARCHAR(20) NOT NULL CHECK (entry_type IN ('purchase','sale','bank','general','opening','closing','depreciation','vat','payroll','correction')),
    journal_code        VARCHAR(20) NULL,
    entry_number        BIGINT NULL,
    description         TEXT NOT NULL,
    reference           VARCHAR(150) NULL,
    status              VARCHAR(20) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','posted','reversed')),
    reverses_entry_id   BIGINT NULL UNIQUE REFERENCES accounting.general_ledger_entries(id) ON DELETE RESTRICT,
    created_by_staff_id BIGINT NULL REFERENCES core.staff_users(id) ON DELETE SET NULL,
    posted_by_staff_id  BIGINT NULL REFERENCES core.staff_users(id) ON DELETE SET NULL,
    posted_at           TIMESTAMPTZ NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_general_ledger_entries_client_date
    ON accounting.general_ledger_entries(client_id, entry_date);
CREATE INDEX idx_general_ledger_entries_status
    ON accounting.general_ledger_entries(client_id, status);
CREATE UNIQUE INDEX uq_general_ledger_entry_number
    ON accounting.general_ledger_entries(client_id, fiscal_year_id, journal_code, entry_number)
    WHERE fiscal_year_id IS NOT NULL AND journal_code IS NOT NULL AND entry_number IS NOT NULL;

COMMENT ON TABLE accounting.general_ledger_entries IS 'Nagłówki zapisów księgi głównej / memoriałów. To centralny moduł księgowości.';

CREATE TABLE accounting.general_ledger_entry_lines (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    general_ledger_entry_id BIGINT NOT NULL REFERENCES accounting.general_ledger_entries(id) ON DELETE CASCADE,
    line_number         INTEGER NOT NULL CHECK (line_number > 0),
    account_id          BIGINT NOT NULL REFERENCES accounting.accounts(id) ON DELETE RESTRICT,
    description         TEXT NULL,
    debit_amount        NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (debit_amount >= 0),
    credit_amount       NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (credit_amount >= 0),
    counterparty_id     BIGINT NULL REFERENCES core.client_counterparties(id) ON DELETE RESTRICT,
    invoice_line_id     BIGINT NULL REFERENCES accounting.invoice_lines(id) ON DELETE SET NULL,
    foreign_currency_amount NUMERIC(18,2) NULL,
    foreign_currency    CHAR(3) NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(general_ledger_entry_id, line_number),
    CHECK ((debit_amount > 0 AND credit_amount = 0) OR (credit_amount > 0 AND debit_amount = 0))
);

CREATE INDEX idx_general_ledger_lines_account
    ON accounting.general_ledger_entry_lines(account_id);
CREATE INDEX idx_general_ledger_lines_invoice_line
    ON accounting.general_ledger_entry_lines(invoice_line_id);

COMMENT ON TABLE accounting.general_ledger_entry_lines IS 'Linie Debet/Credit. Bilans i W&V są wyliczane właśnie z tej tabeli.';

ALTER TABLE accounting.invoices
    ADD CONSTRAINT fk_invoices_general_ledger_entry
    FOREIGN KEY (general_ledger_entry_id)
    REFERENCES accounting.general_ledger_entries(id)
    ON DELETE RESTRICT;

CREATE UNIQUE INDEX uq_invoice_general_ledger_entry
    ON accounting.invoices(general_ledger_entry_id)
    WHERE general_ledger_entry_id IS NOT NULL;

CREATE TABLE accounting.vat_transactions (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    client_id           BIGINT NOT NULL REFERENCES core.clients(id) ON DELETE RESTRICT,
    general_ledger_entry_id BIGINT NOT NULL REFERENCES accounting.general_ledger_entries(id) ON DELETE RESTRICT,
    invoice_line_id     BIGINT NULL REFERENCES accounting.invoice_lines(id) ON DELETE SET NULL,
    vat_code_id         BIGINT NOT NULL REFERENCES accounting.vat_codes(id) ON DELETE RESTRICT,
    vat_date            DATE NOT NULL,
    taxable_amount      NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (taxable_amount >= 0),
    output_vat_amount   NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (output_vat_amount >= 0),
    input_vat_amount    NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (input_vat_amount >= 0),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_vat_transactions_client_date
    ON accounting.vat_transactions(client_id, vat_date);
CREATE INDEX idx_vat_transactions_code
    ON accounting.vat_transactions(vat_code_id);

COMMENT ON TABLE accounting.vat_transactions IS 'Rejestr VAT. Obsługuje zwykły VAT oraz sytuacje, gdzie jednocześnie występuje output i input VAT, np. reverse charge.';

-- ============================================================================
-- BANKING - KONTA, OPERACJE, MATCHING, ROZRACHUNKI
-- ============================================================================

CREATE TABLE banking.client_bank_accounts (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id           UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    client_id           BIGINT NOT NULL REFERENCES core.clients(id) ON DELETE RESTRICT,
    iban                VARCHAR(34) NOT NULL,
    bank_name           VARCHAR(255) NULL,
    currency            CHAR(3) NOT NULL DEFAULT 'EUR',
    connection_provider VARCHAR(80) NULL,
    provider_account_id VARCHAR(255) NULL,
    connection_status   VARCHAR(20) NOT NULL DEFAULT 'manual' CHECK (connection_status IN ('manual','connected','expired','disabled','error')),
    consent_expires_at  TIMESTAMPTZ NULL,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(client_id, iban)
);

COMMENT ON TABLE banking.client_bank_accounts IS 'Rachunki bankowe klienta, zarówno importowane ręcznie jak i podłączone przez Open Banking.';

CREATE TABLE banking.bank_transactions (
    id                      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    client_bank_account_id  BIGINT NOT NULL REFERENCES banking.client_bank_accounts(id) ON DELETE RESTRICT,
    source_import_id        BIGINT NULL REFERENCES documents.document_imports(id) ON DELETE SET NULL,
    provider_transaction_id VARCHAR(255) NULL,
    transaction_hash        CHAR(64) NOT NULL,
    booking_date            DATE NOT NULL,
    value_date              DATE NULL,
    amount                  NUMERIC(18,2) NOT NULL CHECK (amount <> 0),
    currency                CHAR(3) NOT NULL DEFAULT 'EUR',
    counterparty_name       VARCHAR(255) NULL,
    counterparty_iban       VARCHAR(34) NULL,
    payment_description     TEXT NULL,
    raw_bank_data           JSONB NOT NULL DEFAULT '{}'::jsonb,
    processing_status       VARCHAR(20) NOT NULL DEFAULT 'unmatched' CHECK (processing_status IN ('unmatched','suggested','matched','ignored','needs_review')),
    general_ledger_entry_id BIGINT NULL UNIQUE REFERENCES accounting.general_ledger_entries(id) ON DELETE RESTRICT,
    imported_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(client_bank_account_id, transaction_hash)
);

CREATE UNIQUE INDEX uq_bank_provider_transaction
    ON banking.bank_transactions(client_bank_account_id, provider_transaction_id)
    WHERE provider_transaction_id IS NOT NULL;
CREATE INDEX idx_bank_transactions_date
    ON banking.bank_transactions(client_bank_account_id, booking_date);
CREATE INDEX idx_bank_transactions_status
    ON banking.bank_transactions(processing_status);

COMMENT ON TABLE banking.bank_transactions IS '100% surowych operacji bankowych. Operacji nie usuwamy; nieistotne oznaczamy jako ignored.';

CREATE TABLE banking.bank_invoice_match_suggestions (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    bank_transaction_id BIGINT NOT NULL REFERENCES banking.bank_transactions(id) ON DELETE CASCADE,
    invoice_id          BIGINT NOT NULL REFERENCES accounting.invoices(id) ON DELETE RESTRICT,
    suggested_amount    NUMERIC(18,2) NOT NULL CHECK (suggested_amount > 0),
    confidence          NUMERIC(5,4) NULL CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
    status              VARCHAR(20) NOT NULL DEFAULT 'suggested' CHECK (status IN ('suggested','approved','rejected')),
    approved_by_staff_id BIGINT NULL REFERENCES core.staff_users(id) ON DELETE SET NULL,
    approved_at         TIMESTAMPTZ NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(bank_transaction_id, invoice_id)
);

COMMENT ON TABLE banking.bank_invoice_match_suggestions IS 'Automatyczne propozycje dopasowania przelewów do faktur. To nie jest jeszcze rozrachunek księgowy.';

CREATE TABLE banking.bank_booking_rules (
    id                      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    client_id               BIGINT NULL REFERENCES core.clients(id) ON DELETE RESTRICT,
    rule_name               VARCHAR(255) NOT NULL,
    priority                INTEGER NOT NULL DEFAULT 100,
    counterparty_contains   VARCHAR(255) NULL,
    iban_equals             VARCHAR(34) NULL,
    description_contains    VARCHAR(255) NULL,
    target_account_id       BIGINT NOT NULL REFERENCES accounting.accounts(id) ON DELETE RESTRICT,
    vat_code_id             BIGINT NULL REFERENCES accounting.vat_codes(id) ON DELETE RESTRICT,
    action                  VARCHAR(20) NOT NULL DEFAULT 'suggest' CHECK (action IN ('suggest','auto_book','private','ignore')),
    is_active               BOOLEAN NOT NULL DEFAULT TRUE,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (counterparty_contains IS NOT NULL OR iban_equals IS NOT NULL OR description_contains IS NOT NULL)
);

CREATE INDEX idx_bank_booking_rules_client_priority
    ON banking.bank_booking_rules(client_id, priority);

COMMENT ON TABLE banking.bank_booking_rules IS 'Reguły automatycznego rozpoznawania banku: np. ODIDO -> Telefon, opłata bankowa -> Koszty bankowe.';

CREATE TABLE banking.account_reconciliations (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id           UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    client_id           BIGINT NOT NULL REFERENCES core.clients(id) ON DELETE RESTRICT,
    reconciliation_date DATE NOT NULL,
    status              VARCHAR(20) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','posted','reversed')),
    description         TEXT NULL,
    created_by_staff_id BIGINT NULL REFERENCES core.staff_users(id) ON DELETE SET NULL,
    posted_by_staff_id  BIGINT NULL REFERENCES core.staff_users(id) ON DELETE SET NULL,
    posted_at           TIMESTAMPTZ NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE banking.account_reconciliations IS 'Nagłówek prawdziwego rozliczenia należności/zobowiązań pomiędzy liniami księgi głównej.';

CREATE TABLE banking.account_reconciliation_lines (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_reconciliation_id BIGINT NOT NULL REFERENCES banking.account_reconciliations(id) ON DELETE CASCADE,
    general_ledger_line_id BIGINT NOT NULL REFERENCES accounting.general_ledger_entry_lines(id) ON DELETE RESTRICT,
    reconciled_amount   NUMERIC(18,2) NOT NULL CHECK (reconciled_amount > 0),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(account_reconciliation_id, general_ledger_line_id)
);

CREATE INDEX idx_account_reconciliation_lines_ledger_line
    ON banking.account_reconciliation_lines(general_ledger_line_id);

COMMENT ON TABLE banking.account_reconciliation_lines IS 'Linie rozrachunku N:M: jedna faktura może mieć wiele płatności, a jeden przelew może rozliczać wiele faktur.';

-- ============================================================================
-- ASSETS - ŚRODKI TRWAŁE, AUTA, AMORTYZACJA, KIA/EIA/MIA/VAMIL, LEASINGI
-- ============================================================================

CREATE TABLE assets.fixed_assets (
    id                                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id                           UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    client_id                           BIGINT NOT NULL REFERENCES core.clients(id) ON DELETE RESTRICT,
    source_invoice_line_id              BIGINT NULL REFERENCES accounting.invoice_lines(id) ON DELETE SET NULL,
    source_general_ledger_entry_id      BIGINT NULL REFERENCES accounting.general_ledger_entries(id) ON DELETE SET NULL,
    acquisition_source                  VARCHAR(30) NOT NULL DEFAULT 'purchase' CHECK (acquisition_source IN ('purchase','private_contribution','opening_balance','other')),
    asset_code                          VARCHAR(50) NULL,
    asset_name                          VARCHAR(255) NOT NULL,
    description                         TEXT NULL,
    purchase_date                       DATE NOT NULL,
    in_use_date                         DATE NULL,
    acquisition_cost                    NUMERIC(18,2) NOT NULL CHECK (acquisition_cost > 0),
    residual_value                      NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (residual_value >= 0),
    business_use_percent                NUMERIC(7,4) NOT NULL DEFAULT 100 CHECK (business_use_percent BETWEEN 0 AND 100),
    vat_deductible_percent              NUMERIC(7,4) NULL CHECK (vat_deductible_percent IS NULL OR vat_deductible_percent BETWEEN 0 AND 100),
    useful_life_months                  INTEGER NULL CHECK (useful_life_months IS NULL OR useful_life_months > 0),
    depreciation_method                 VARCHAR(30) NOT NULL DEFAULT 'linear' CHECK (depreciation_method IN ('linear','willekeurig','vamil','none')),
    asset_account_id                    BIGINT NOT NULL REFERENCES accounting.accounts(id) ON DELETE RESTRICT,
    accumulated_depreciation_account_id BIGINT NOT NULL REFERENCES accounting.accounts(id) ON DELETE RESTRICT,
    depreciation_expense_account_id     BIGINT NOT NULL REFERENCES accounting.accounts(id) ON DELETE RESTRICT,
    status                              VARCHAR(20) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','active','disposed','fully_depreciated')),
    disposed_at                         DATE NULL,
    disposal_amount                     NUMERIC(18,2) NULL CHECK (disposal_amount IS NULL OR disposal_amount >= 0),
    created_at                          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (residual_value <= acquisition_cost)
);

CREATE INDEX idx_fixed_assets_client
    ON assets.fixed_assets(client_id);

COMMENT ON TABLE assets.fixed_assets IS 'Kartoteka środków trwałych, także wniesionych prywatnie lub z salda otwarcia.';

CREATE TABLE assets.fixed_asset_vehicle_details (
    fixed_asset_id          BIGINT PRIMARY KEY REFERENCES assets.fixed_assets(id) ON DELETE CASCADE,
    license_plate           VARCHAR(20) NULL,
    vehicle_identification_number VARCHAR(50) NULL,
    first_registration_date DATE NULL,
    catalog_value           NUMERIC(18,2) NULL CHECK (catalog_value IS NULL OR catalog_value >= 0),
    fuel_type               VARCHAR(40) NULL,
    business_km_tracking    BOOLEAN NULL,
    notes                   TEXT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE assets.fixed_asset_vehicle_details IS 'Dodatkowe dane tylko dla środków trwałych będących samochodami; przydatne do bijtelling i ewidencji auta.';

CREATE TABLE assets.fixed_asset_documents (
    fixed_asset_id      BIGINT NOT NULL REFERENCES assets.fixed_assets(id) ON DELETE CASCADE,
    accounting_document_id BIGINT NOT NULL REFERENCES documents.accounting_documents(id) ON DELETE RESTRICT,
    document_role       VARCHAR(30) NOT NULL DEFAULT 'source' CHECK (document_role IN ('source','purchase','sale','valuation','photo','lease','other')),
    PRIMARY KEY (fixed_asset_id, accounting_document_id, document_role)
);

COMMENT ON TABLE assets.fixed_asset_documents IS 'Dokumenty przypięte do środka trwałego: faktura, zdjęcia, umowa leasingu, sprzedaż itd.';

CREATE TABLE assets.fixed_asset_tax_schemes (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    fixed_asset_id      BIGINT NOT NULL REFERENCES assets.fixed_assets(id) ON DELETE CASCADE,
    scheme_code         VARCHAR(30) NOT NULL CHECK (scheme_code IN ('KIA','EIA','MIA','VAMIL','WILLEKEURIG_STARTER')),
    eligibility_status  VARCHAR(20) NOT NULL DEFAULT 'unknown' CHECK (eligibility_status IN ('unknown','possible','eligible','not_eligible','applied')),
    qualifying_amount   NUMERIC(18,2) NULL CHECK (qualifying_amount IS NULL OR qualifying_amount >= 0),
    notes               TEXT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(fixed_asset_id, scheme_code)
);

COMMENT ON TABLE assets.fixed_asset_tax_schemes IS 'Ocena środka trwałego pod KIA/EIA/MIA/VAMIL/willekeurige afschrijving.';

CREATE TABLE assets.fixed_asset_depreciation_entries (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    fixed_asset_id      BIGINT NOT NULL REFERENCES assets.fixed_assets(id) ON DELETE RESTRICT,
    depreciation_date   DATE NOT NULL,
    depreciation_amount NUMERIC(18,2) NOT NULL CHECK (depreciation_amount > 0),
    depreciation_method VARCHAR(30) NOT NULL CHECK (depreciation_method IN ('linear','willekeurig','vamil','manual')),
    status              VARCHAR(20) NOT NULL DEFAULT 'planned' CHECK (status IN ('planned','approved','posted','cancelled')),
    general_ledger_entry_id BIGINT NULL UNIQUE REFERENCES accounting.general_ledger_entries(id) ON DELETE RESTRICT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(fixed_asset_id, depreciation_date)
);

COMMENT ON TABLE assets.fixed_asset_depreciation_entries IS 'Harmonogram i wykonane odpisy amortyzacyjne. Po zaksięgowaniu wskazuje zapis księgi głównej.';

CREATE TABLE assets.investment_deduction_calculations (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    client_id           BIGINT NOT NULL REFERENCES core.clients(id) ON DELETE RESTRICT,
    tax_year            SMALLINT NOT NULL CHECK (tax_year BETWEEN 2000 AND 2200),
    scheme_code         VARCHAR(20) NOT NULL CHECK (scheme_code IN ('KIA','EIA','MIA')),
    qualifying_total    NUMERIC(18,2) NOT NULL DEFAULT 0,
    calculated_deduction NUMERIC(18,2) NULL,
    status              VARCHAR(20) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','reviewed','final')),
    calculation_details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(client_id, tax_year, scheme_code)
);

CREATE TABLE assets.investment_deduction_assets (
    investment_deduction_calculation_id BIGINT NOT NULL REFERENCES assets.investment_deduction_calculations(id) ON DELETE CASCADE,
    fixed_asset_id      BIGINT NOT NULL REFERENCES assets.fixed_assets(id) ON DELETE RESTRICT,
    qualifying_amount   NUMERIC(18,2) NOT NULL CHECK (qualifying_amount >= 0),
    PRIMARY KEY (investment_deduction_calculation_id, fixed_asset_id)
);

COMMENT ON TABLE assets.investment_deduction_calculations IS 'Roczne wyliczenie KIA/EIA/MIA na podstawie wszystkich kwalifikujących środków trwałych.';
COMMENT ON TABLE assets.investment_deduction_assets IS 'Środki trwałe wchodzące do konkretnego rocznego wyliczenia KIA/EIA/MIA.';

CREATE TABLE assets.financial_contracts (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id           UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    client_id           BIGINT NOT NULL REFERENCES core.clients(id) ON DELETE RESTRICT,
    counterparty_id     BIGINT NULL REFERENCES core.client_counterparties(id) ON DELETE RESTRICT,
    contract_type       VARCHAR(30) NOT NULL CHECK (contract_type IN ('loan','financial_lease','operating_lease','mortgage','other')),
    contract_number     VARCHAR(100) NULL,
    start_date          DATE NOT NULL,
    end_date            DATE NULL,
    principal_amount    NUMERIC(18,2) NULL CHECK (principal_amount IS NULL OR principal_amount >= 0),
    interest_rate_percent NUMERIC(10,6) NULL,
    currency            CHAR(3) NOT NULL DEFAULT 'EUR',
    liability_account_id BIGINT NULL REFERENCES accounting.accounts(id) ON DELETE RESTRICT,
    interest_expense_account_id BIGINT NULL REFERENCES accounting.accounts(id) ON DELETE RESTRICT,
    fixed_asset_id      BIGINT NULL REFERENCES assets.fixed_assets(id) ON DELETE SET NULL,
    status              VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN ('draft','active','closed','cancelled')),
    notes               TEXT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (end_date IS NULL OR end_date >= start_date)
);

COMMENT ON TABLE assets.financial_contracts IS 'Leasingi, pożyczki, kredyty i inne umowy finansowe klienta.';

CREATE TABLE assets.financial_contract_schedule_items (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    financial_contract_id BIGINT NOT NULL REFERENCES assets.financial_contracts(id) ON DELETE CASCADE,
    installment_number  INTEGER NOT NULL CHECK (installment_number > 0),
    due_date            DATE NOT NULL,
    principal_amount    NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (principal_amount >= 0),
    interest_amount     NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (interest_amount >= 0),
    other_costs_amount  NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (other_costs_amount >= 0),
    total_amount        NUMERIC(18,2) NOT NULL CHECK (total_amount >= 0),
    general_ledger_entry_id BIGINT NULL UNIQUE REFERENCES accounting.general_ledger_entries(id) ON DELETE RESTRICT,
    status              VARCHAR(20) NOT NULL DEFAULT 'planned' CHECK (status IN ('planned','posted','paid','cancelled')),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(financial_contract_id, installment_number),
    CHECK (total_amount = principal_amount + interest_amount + other_costs_amount)
);

COMMENT ON TABLE assets.financial_contract_schedule_items IS 'Harmonogram rat leasingu/pożyczki z rozbiciem kapitał-odsetki-koszty.';

CREATE TABLE assets.financial_contract_documents (
    financial_contract_id BIGINT NOT NULL REFERENCES assets.financial_contracts(id) ON DELETE CASCADE,
    accounting_document_id BIGINT NOT NULL REFERENCES documents.accounting_documents(id) ON DELETE RESTRICT,
    document_role       VARCHAR(20) NOT NULL DEFAULT 'contract' CHECK (document_role IN ('contract','statement','invoice','other')),
    PRIMARY KEY (financial_contract_id, accounting_document_id, document_role)
);

COMMENT ON TABLE assets.financial_contract_documents IS 'Dokumenty przypięte do leasingu/pożyczki: umowa, harmonogram, zestawienie itd.';

-- ============================================================================
-- PAYROLL - WYNIKI Z ZEWNĘTRZNEGO SYSTEMU PŁACOWEGO
-- ============================================================================

CREATE TABLE payroll.payroll_runs (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id           UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    client_id           BIGINT NOT NULL REFERENCES core.clients(id) ON DELETE RESTRICT,
    payroll_provider    VARCHAR(80) NULL,
    period_start        DATE NOT NULL,
    period_end          DATE NOT NULL,
    gross_wages         NUMERIC(18,2) NULL,
    payroll_tax         NUMERIC(18,2) NULL,
    employer_costs      NUMERIC(18,2) NULL,
    net_wages           NUMERIC(18,2) NULL,
    general_ledger_entry_id BIGINT NULL UNIQUE REFERENCES accounting.general_ledger_entries(id) ON DELETE RESTRICT,
    status              VARCHAR(20) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','approved','posted','cancelled')),
    external_reference  VARCHAR(100) NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (period_end >= period_start)
);

COMMENT ON TABLE payroll.payroll_runs IS 'Podsumowanie okresu płacowego z Loket/innego systemu. Nie jest to własny silnik płacowy.';

CREATE TABLE payroll.payroll_documents (
    payroll_run_id      BIGINT NOT NULL REFERENCES payroll.payroll_runs(id) ON DELETE CASCADE,
    accounting_document_id BIGINT NOT NULL REFERENCES documents.accounting_documents(id) ON DELETE RESTRICT,
    document_role       VARCHAR(30) NOT NULL CHECK (document_role IN ('payroll_summary','loonaangifte','loonstrook','payment','other')),
    PRIMARY KEY (payroll_run_id, accounting_document_id, document_role)
);

COMMENT ON TABLE payroll.payroll_documents IS 'Dokumenty powiązane z okresem płacowym: loonstroken, loonaangifte, podsumowanie itd.';

-- ============================================================================
-- TAX - DEKLARACJE I BLOKADY OKRESÓW
-- ============================================================================

CREATE TABLE tax.tax_declarations (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id           UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    client_id           BIGINT NOT NULL REFERENCES core.clients(id) ON DELETE RESTRICT,
    declaration_type    VARCHAR(20) NOT NULL CHECK (declaration_type IN ('BTW','ICP','IB','VPB')),
    period_start        DATE NOT NULL,
    period_end          DATE NOT NULL,
    version_number      INTEGER NOT NULL DEFAULT 1 CHECK (version_number > 0),
    replaces_declaration_id BIGINT NULL REFERENCES tax.tax_declarations(id) ON DELETE RESTRICT,
    status              VARCHAR(20) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','reviewed','submitted','closed','corrected')),
    external_reference  VARCHAR(100) NULL,
    rounding_general_ledger_entry_id BIGINT NULL UNIQUE REFERENCES accounting.general_ledger_entries(id) ON DELETE RESTRICT,
    calculated_at       TIMESTAMPTZ NULL,
    reviewed_by_staff_id BIGINT NULL REFERENCES core.staff_users(id) ON DELETE SET NULL,
    submitted_at        TIMESTAMPTZ NULL,
    created_by_staff_id BIGINT NULL REFERENCES core.staff_users(id) ON DELETE SET NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (period_end >= period_start),
    UNIQUE(client_id, declaration_type, period_start, period_end, version_number)
);

COMMENT ON TABLE tax.tax_declarations IS 'Nagłówki deklaracji BTW/ICP/IB/VPB. Korekty powstają jako kolejne wersje, nie przez nadpisanie starej.';

CREATE TABLE tax.tax_declaration_lines (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tax_declaration_id  BIGINT NOT NULL REFERENCES tax.tax_declarations(id) ON DELETE CASCADE,
    declaration_box     VARCHAR(20) NOT NULL,
    declaration_field   VARCHAR(20) NOT NULL CHECK (declaration_field IN ('turnover','vat','amount')),
    exact_amount        NUMERIC(18,2) NOT NULL DEFAULT 0,
    submitted_amount    NUMERIC(18,0) NOT NULL DEFAULT 0,
    description         TEXT NULL,
    UNIQUE(tax_declaration_id, declaration_box, declaration_field)
);

COMMENT ON TABLE tax.tax_declaration_lines IS 'Kwoty do konkretnych pól deklaracji, z osobnym exact_amount i zaokrąglonym submitted_amount.';

CREATE TABLE tax.tax_declaration_vat_transactions (
    tax_declaration_id  BIGINT NOT NULL REFERENCES tax.tax_declarations(id) ON DELETE CASCADE,
    vat_transaction_id  BIGINT NOT NULL REFERENCES accounting.vat_transactions(id) ON DELETE RESTRICT,
    PRIMARY KEY (tax_declaration_id, vat_transaction_id)
);

COMMENT ON TABLE tax.tax_declaration_vat_transactions IS 'Ślad audytowy: które dokładnie zapisy VAT weszły do danej deklaracji.';

CREATE TABLE tax.accounting_period_locks (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    client_id           BIGINT NOT NULL REFERENCES core.clients(id) ON DELETE RESTRICT,
    lock_type           VARCHAR(20) NOT NULL CHECK (lock_type IN ('VAT','YEAR','FULL')),
    locked_through_date DATE NOT NULL,
    tax_declaration_id  BIGINT NULL REFERENCES tax.tax_declarations(id) ON DELETE RESTRICT,
    reason              TEXT NULL,
    locked_by_staff_id  BIGINT NULL REFERENCES core.staff_users(id) ON DELETE SET NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(client_id, lock_type, locked_through_date)
);

COMMENT ON TABLE tax.accounting_period_locks IS 'Blokady zamkniętych okresów. Po wysłaniu BTW nie zmieniamy danych wstecz bez korekty.';

-- ============================================================================
-- AUDIT - HISTORIA ZMIAN
-- ============================================================================

CREATE TABLE audit.change_log (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    schema_name         VARCHAR(100) NOT NULL,
    table_name          VARCHAR(100) NOT NULL,
    record_id           BIGINT NULL,
    action              VARCHAR(20) NOT NULL CHECK (action IN ('INSERT','UPDATE','DELETE','POST','REVERSE','LOGIN','OTHER')),
    staff_user_id       BIGINT NULL REFERENCES core.staff_users(id) ON DELETE SET NULL,
    before_data         JSONB NULL,
    after_data          JSONB NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_change_log_record
    ON audit.change_log(schema_name, table_name, record_id, created_at);

COMMENT ON TABLE audit.change_log IS 'Append-only historia zmian dla kontroli kto, kiedy i co zmienił.';

-- ============================================================================
-- FUNKCJE I TRIGGERY
-- ============================================================================

CREATE OR REPLACE FUNCTION core.set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION core.prevent_client_delete()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Klienta nie wolno usuwać. Ustaw is_active=false i ewentualnie closed_at.';
END;
$$;

CREATE TRIGGER trg_prevent_client_delete
BEFORE DELETE ON core.clients
FOR EACH ROW EXECUTE FUNCTION core.prevent_client_delete();

CREATE OR REPLACE FUNCTION banking.prevent_bank_transaction_delete()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Operacji bankowej nie wolno usuwać. Ustaw processing_status=ignored.';
END;
$$;

CREATE TRIGGER trg_prevent_bank_transaction_delete
BEFORE DELETE ON banking.bank_transactions
FOR EACH ROW EXECUTE FUNCTION banking.prevent_bank_transaction_delete();

CREATE OR REPLACE FUNCTION audit.prevent_change_log_modification()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit.change_log jest append-only i nie może być zmieniany ani usuwany.';
END;
$$;

CREATE TRIGGER trg_change_log_append_only
BEFORE UPDATE OR DELETE ON audit.change_log
FOR EACH ROW EXECUTE FUNCTION audit.prevent_change_log_modification();

CREATE OR REPLACE FUNCTION accounting.require_new_ledger_entry_to_be_draft()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status <> 'draft' THEN
        RAISE EXCEPTION 'Nowy zapis księgowy musi powstać jako draft.';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_new_ledger_entry_must_be_draft
BEFORE INSERT ON accounting.general_ledger_entries
FOR EACH ROW EXECUTE FUNCTION accounting.require_new_ledger_entry_to_be_draft();

CREATE OR REPLACE FUNCTION accounting.prevent_posted_ledger_line_change()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    v_entry_id BIGINT;
    v_status VARCHAR(20);
BEGIN
    v_entry_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.general_ledger_entry_id ELSE NEW.general_ledger_entry_id END;
    SELECT status INTO v_status
    FROM accounting.general_ledger_entries
    WHERE id = v_entry_id;

    IF v_status IN ('posted','reversed') THEN
        RAISE EXCEPTION 'Nie można zmieniać linii zaksięgowanej transakcji. Użyj korekty/storna.';
    END IF;

    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$;

CREATE TRIGGER trg_prevent_posted_ledger_line_change
BEFORE INSERT OR UPDATE OR DELETE ON accounting.general_ledger_entry_lines
FOR EACH ROW EXECUTE FUNCTION accounting.prevent_posted_ledger_line_change();

CREATE OR REPLACE FUNCTION accounting.validate_ledger_entry_posting()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    v_debit NUMERIC(18,2);
    v_credit NUMERIC(18,2);
    v_line_count INTEGER;
    v_invalid_accounts INTEGER;
    v_locked_through DATE;
BEGIN
    IF OLD.status = 'posted' AND NEW.status <> OLD.status THEN
        RAISE EXCEPTION 'Zaksięgowanej transakcji nie można odksięgować edycją. Użyj storna.';
    END IF;

    IF OLD.status IN ('posted','reversed') AND NEW IS DISTINCT FROM OLD THEN
        RAISE EXCEPTION 'Zaksięgowanej transakcji nie można edytować. Użyj korekty/storna.';
    END IF;

    IF OLD.status <> 'posted' AND NEW.status = 'posted' THEN
        SELECT MAX(locked_through_date)
        INTO v_locked_through
        FROM tax.accounting_period_locks
        WHERE client_id = NEW.client_id
          AND lock_type IN ('YEAR','FULL');

        IF v_locked_through IS NOT NULL AND NEW.entry_date <= v_locked_through THEN
            RAISE EXCEPTION 'Nie można księgować w zamkniętym okresie do %', v_locked_through;
        END IF;

        SELECT COALESCE(SUM(gl.debit_amount),0),
               COALESCE(SUM(gl.credit_amount),0),
               COUNT(*),
               COUNT(*) FILTER (WHERE a.id IS NULL OR a.is_active = FALSE OR a.allow_direct_posting = FALSE)
        INTO v_debit, v_credit, v_line_count, v_invalid_accounts
        FROM accounting.general_ledger_entry_lines gl
        LEFT JOIN accounting.accounts a ON a.id = gl.account_id
        WHERE gl.general_ledger_entry_id = NEW.id;

        IF v_line_count < 2 THEN
            RAISE EXCEPTION 'Zapis księgowy musi zawierać minimum 2 linie.';
        END IF;

        IF v_debit <> v_credit THEN
            RAISE EXCEPTION 'Zapis niezbilansowany: Debet %, Credit %', v_debit, v_credit;
        END IF;

        IF v_invalid_accounts > 0 THEN
            RAISE EXCEPTION 'Zapis używa nieaktywnego albo nieksięgowalnego konta.';
        END IF;

        NEW.posted_at := COALESCE(NEW.posted_at, now());
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_validate_ledger_entry_posting
BEFORE UPDATE ON accounting.general_ledger_entries
FOR EACH ROW EXECUTE FUNCTION accounting.validate_ledger_entry_posting();

CREATE OR REPLACE FUNCTION accounting.prevent_posted_ledger_entry_delete()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status IN ('posted','reversed') THEN
        RAISE EXCEPTION 'Nie można usunąć zaksięgowanej transakcji. Użyj storna.';
    END IF;
    RETURN OLD;
END;
$$;

CREATE TRIGGER trg_prevent_posted_ledger_entry_delete
BEFORE DELETE ON accounting.general_ledger_entries
FOR EACH ROW EXECUTE FUNCTION accounting.prevent_posted_ledger_entry_delete();

CREATE OR REPLACE FUNCTION accounting.prevent_posted_invoice_line_change()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    v_invoice_id BIGINT;
    v_status VARCHAR(20);
BEGIN
    v_invoice_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.invoice_id ELSE NEW.invoice_id END;
    SELECT status INTO v_status
    FROM accounting.invoices
    WHERE id = v_invoice_id;

    IF v_status = 'posted' THEN
        RAISE EXCEPTION 'Nie można zmieniać pozycji zaksięgowanej faktury.';
    END IF;

    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$;

CREATE TRIGGER trg_prevent_posted_invoice_line_change
BEFORE INSERT OR UPDATE OR DELETE ON accounting.invoice_lines
FOR EACH ROW EXECUTE FUNCTION accounting.prevent_posted_invoice_line_change();

CREATE OR REPLACE FUNCTION accounting.validate_invoice_posting()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    v_net NUMERIC(18,2);
    v_vat NUMERIC(18,2);
    v_line_count INTEGER;
    v_unassigned_lines INTEGER;
    v_ledger_status VARCHAR(20);
BEGIN
    IF OLD.status = 'posted' AND NEW IS DISTINCT FROM OLD THEN
        RAISE EXCEPTION 'Nie można edytować zaksięgowanej faktury. Utwórz korektę.';
    END IF;

    IF OLD.status <> 'posted' AND NEW.status = 'posted' THEN
        SELECT COALESCE(SUM(net_amount),0),
               COALESCE(SUM(vat_amount),0),
               COUNT(*),
               COUNT(*) FILTER (WHERE account_id IS NULL OR vat_code_id IS NULL)
        INTO v_net, v_vat, v_line_count, v_unassigned_lines
        FROM accounting.invoice_lines
        WHERE invoice_id = NEW.id;

        IF v_line_count < 1 THEN
            RAISE EXCEPTION 'Faktura nie ma pozycji.';
        END IF;

        IF v_net <> NEW.net_total OR v_vat <> NEW.vat_total THEN
            RAISE EXCEPTION 'Sumy pozycji nie zgadzają się z nagłówkiem faktury.';
        END IF;

        IF v_unassigned_lines > 0 THEN
            RAISE EXCEPTION 'Każda pozycja faktury musi mieć konto i kod VAT przed zaksięgowaniem.';
        END IF;

        IF NEW.general_ledger_entry_id IS NULL THEN
            RAISE EXCEPTION 'Faktura nie ma powiązanego zapisu księgowego.';
        END IF;

        SELECT status INTO v_ledger_status
        FROM accounting.general_ledger_entries
        WHERE id = NEW.general_ledger_entry_id;

        IF v_ledger_status IS DISTINCT FROM 'posted' THEN
            RAISE EXCEPTION 'Powiązany zapis księgowy nie jest zaksięgowany.';
        END IF;
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_validate_invoice_posting
BEFORE UPDATE ON accounting.invoices
FOR EACH ROW EXECUTE FUNCTION accounting.validate_invoice_posting();

CREATE OR REPLACE FUNCTION accounting.prevent_posted_invoice_delete()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status = 'posted' THEN
        RAISE EXCEPTION 'Nie można usunąć zaksięgowanej faktury.';
    END IF;
    RETURN OLD;
END;
$$;

CREATE TRIGGER trg_prevent_posted_invoice_delete
BEFORE DELETE ON accounting.invoices
FOR EACH ROW EXECUTE FUNCTION accounting.prevent_posted_invoice_delete();

CREATE OR REPLACE FUNCTION accounting.enforce_vat_period_lock()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    v_client_id BIGINT;
    v_vat_date DATE;
    v_locked_through DATE;
BEGIN
    v_client_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.client_id ELSE NEW.client_id END;
    v_vat_date := CASE WHEN TG_OP = 'DELETE' THEN OLD.vat_date ELSE NEW.vat_date END;

    SELECT MAX(locked_through_date)
    INTO v_locked_through
    FROM tax.accounting_period_locks
    WHERE client_id = v_client_id
      AND lock_type IN ('VAT','FULL');

    IF v_locked_through IS NOT NULL AND v_vat_date <= v_locked_through THEN
        RAISE EXCEPTION 'Okres VAT jest zablokowany do %', v_locked_through;
    END IF;

    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$;

CREATE TRIGGER trg_enforce_vat_period_lock
BEFORE INSERT OR UPDATE OR DELETE ON accounting.vat_transactions
FOR EACH ROW EXECUTE FUNCTION accounting.enforce_vat_period_lock();

-- Automatyczny updated_at na tabelach, które mają tę kolumnę.
DO $$
DECLARE
    table_full_name TEXT;
    schema_name_var TEXT;
    table_name_var TEXT;
BEGIN
    FOREACH table_full_name IN ARRAY ARRAY[
        'core.staff_users','core.clients','core.persons','core.client_tax_profiles','core.client_tax_year_settings','core.client_fiscal_years','core.client_counterparties',
        'documents.client_document_intake_channels','documents.accounting_documents',
        'accounting.accounts','accounting.staff_booking_categories','accounting.invoices','accounting.invoice_lines','accounting.invoice_validation_checks','accounting.general_ledger_entries',
        'banking.client_bank_accounts','banking.bank_booking_rules',
        'assets.fixed_assets','assets.fixed_asset_vehicle_details','assets.fixed_asset_depreciation_entries','assets.investment_deduction_calculations','assets.financial_contracts',
        'payroll.payroll_runs','tax.tax_declarations'
    ] LOOP
        schema_name_var := split_part(table_full_name, '.', 1);
        table_name_var := split_part(table_full_name, '.', 2);
        EXECUTE format(
            'CREATE TRIGGER trg_%I_updated_at BEFORE UPDATE ON %I.%I FOR EACH ROW EXECUTE FUNCTION core.set_updated_at()',
            table_name_var, schema_name_var, table_name_var
        );
    END LOOP;
END $$;

COMMIT;
