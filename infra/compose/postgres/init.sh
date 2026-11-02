#!/bin/sh
set -eu
app_password=$(cat /run/secrets/app_password)
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=app_password="$app_password" <<'SQL'
CREATE EXTENSION IF NOT EXISTS vector;
CREATE ROLE platform_app LOGIN PASSWORD :'app_password';
CREATE TABLE documents (
  id text PRIMARY KEY,
  title text NOT NULL,
  content text NOT NULL,
  embedding vector(128) NOT NULL
);
GRANT CONNECT ON DATABASE platform TO platform_app;
GRANT USAGE ON SCHEMA public TO platform_app;
GRANT SELECT, INSERT ON documents TO platform_app;
SQL
