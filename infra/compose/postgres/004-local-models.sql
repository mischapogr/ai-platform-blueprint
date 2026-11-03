BEGIN;
ALTER TABLE kb_collections DROP CONSTRAINT IF EXISTS kb_collections_embedding_provider_check;
ALTER TABLE kb_collections ADD CONSTRAINT kb_collections_embedding_provider_check
  CHECK (embedding_provider IN ('offline','openai','ollama'));
ALTER TABLE kb_collections DROP CONSTRAINT IF EXISTS kb_collections_dimensions_check;
ALTER TABLE kb_collections ADD CONSTRAINT kb_collections_dimensions_check
  CHECK (dimensions BETWEEN 1 AND 4096);
ALTER TABLE kb_collections ADD COLUMN IF NOT EXISTS embedding_endpoint text;
ALTER TABLE kb_collections ADD COLUMN IF NOT EXISTS embedding_digest text;
ALTER TABLE kb_collections ADD COLUMN IF NOT EXISTS embedding_profile text NOT NULL DEFAULT 'plain';
ALTER TABLE kb_usage ALTER COLUMN input_tokens DROP NOT NULL;
ALTER TABLE kb_usage ALTER COLUMN output_tokens DROP NOT NULL;
COMMIT;
