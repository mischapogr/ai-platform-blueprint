BEGIN;
ALTER TABLE kb_jobs ADD COLUMN IF NOT EXISTS attempt_token uuid;
ALTER TABLE kb_jobs ADD COLUMN IF NOT EXISTS trace_parent text;
COMMIT;
