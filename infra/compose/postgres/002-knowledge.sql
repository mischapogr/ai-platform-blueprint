BEGIN;
CREATE TABLE IF NOT EXISTS kb_collections (
  id uuid PRIMARY KEY,
  name text NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
  description text NOT NULL DEFAULT '',
  embedding_provider text NOT NULL CHECK (embedding_provider IN ('offline', 'openai')),
  embedding_model text NOT NULL,
  dimensions integer NOT NULL CHECK (dimensions IN (128, 1536)),
  allow_external boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (embedding_provider <> 'openai' OR allow_external)
);
CREATE TABLE IF NOT EXISTS kb_sources (
  id uuid PRIMARY KEY,
  collection_id uuid NOT NULL REFERENCES kb_collections(id) ON DELETE CASCADE,
  title text NOT NULL,
  kind text NOT NULL CHECK (kind IN ('text','file','website')),
  locator text NOT NULL DEFAULT '',
  payload bytea,
  status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','ready','failed')),
  error_code text,
  chunk_count integer NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(id, collection_id)
);
CREATE TABLE IF NOT EXISTS kb_chunks (
  id uuid PRIMARY KEY,
  source_id uuid NOT NULL,
  collection_id uuid NOT NULL,
  content text NOT NULL,
  page integer,
  locator text NOT NULL DEFAULT '',
  ordinal integer NOT NULL,
  embedding vector NOT NULL,
  FOREIGN KEY (source_id, collection_id) REFERENCES kb_sources(id, collection_id) ON DELETE CASCADE,
  UNIQUE(source_id, ordinal)
);
CREATE INDEX IF NOT EXISTS kb_chunks_collection_idx ON kb_chunks(collection_id);
CREATE TABLE IF NOT EXISTS kb_jobs (
  id uuid PRIMARY KEY,
  source_id uuid NOT NULL UNIQUE REFERENCES kb_sources(id) ON DELETE CASCADE,
  status text NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','ready','failed')),
  attempts integer NOT NULL DEFAULT 0,
  available_at timestamptz NOT NULL DEFAULT now(),
  lease_until timestamptz,
  error_code text,
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS kb_usage (
  id bigserial PRIMARY KEY,
  collection_id uuid REFERENCES kb_collections(id) ON DELETE SET NULL,
  operation text NOT NULL,
  model text NOT NULL,
  input_tokens integer NOT NULL,
  output_tokens integer NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
GRANT SELECT, INSERT, UPDATE, DELETE ON kb_collections, kb_sources, kb_chunks, kb_jobs TO platform_app;
GRANT SELECT, INSERT ON kb_usage TO platform_app;
GRANT USAGE, SELECT ON SEQUENCE kb_usage_id_seq TO platform_app;
COMMIT;
