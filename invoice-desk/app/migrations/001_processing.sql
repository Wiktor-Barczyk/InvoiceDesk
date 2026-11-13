-- Additive migration. Never recreate the existing V5 schema.
CREATE TABLE IF NOT EXISTS documents.processing_jobs (
    accounting_document_id bigint PRIMARY KEY REFERENCES documents.accounting_documents(id),
    mode text NOT NULL CHECK (mode IN ('ocr','ai')),
    status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','processing','ready','review','failed','cancelled')),
    attempts integer NOT NULL DEFAULT 0,
    available_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    error_message text,
    needs_split boolean NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS processing_jobs_pending_idx ON documents.processing_jobs (available_at, accounting_document_id) WHERE status='queued';
CREATE INDEX IF NOT EXISTS invoice_desk_client_queue_idx ON documents.accounting_documents (client_id, received_at DESC, id DESC) WHERE status<>'archived';
CREATE TABLE IF NOT EXISTS documents.processing_worker (
    id integer PRIMARY KEY CHECK (id=1),
    heartbeat_at timestamptz NOT NULL,
    mode text NOT NULL CHECK (mode IN ('ocr','ai'))
);
