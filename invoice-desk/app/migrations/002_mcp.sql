-- Additive and repeatable. No invoice values or document files are modified.
ALTER TABLE documents.processing_jobs DROP CONSTRAINT IF EXISTS processing_jobs_mode_check;
ALTER TABLE documents.processing_jobs ADD CONSTRAINT processing_jobs_mode_check CHECK (mode IN ('ocr','ai','mcp'));
CREATE TABLE IF NOT EXISTS documents.mcp_grants (
    id bigserial PRIMARY KEY,
    staff_id bigint NOT NULL REFERENCES core.staff_users(id),
    client_id text NOT NULL,
    resource text NOT NULL,
    access_hash text NOT NULL UNIQUE,
    refresh_hash text NOT NULL UNIQUE,
    access_expires timestamptz NOT NULL,
    refresh_expires timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    revoked boolean NOT NULL DEFAULT false
);
CREATE TABLE IF NOT EXISTS documents.mcp_codes (
    code_hash text PRIMARY KEY,
    staff_id bigint NOT NULL REFERENCES core.staff_users(id),
    client_id text NOT NULL,
    resource text NOT NULL,
    redirect_uri text NOT NULL,
    challenge text NOT NULL,
    expires_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS documents.mcp_claims (
    accounting_document_id bigint PRIMARY KEY REFERENCES documents.accounting_documents(id),
    grant_id bigint NOT NULL REFERENCES documents.mcp_grants(id),
    token_hash text NOT NULL,
    document_version timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    manifest jsonb NOT NULL,
    seen_pages jsonb NOT NULL DEFAULT '[]',
    result_digest text,
    completed_at timestamptz
);
