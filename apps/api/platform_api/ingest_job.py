"""One bounded child process per source. Never prints source text or provider errors."""

import json
import sys
import uuid

from . import db, models
from .ingestion import SourceError, chunks, parse_file, website_pages
from .retrieval import vector_literal


def process(job_id):
    with db.connect() as conn:
        row = conn.execute(
            "SELECT j.id AS job_id,s.*,c.embedding_provider,c.embedding_model,"
            "c.dimensions,c.allow_external FROM kb_jobs j "
            "JOIN kb_sources s ON s.id=j.source_id "
            "JOIN kb_collections c ON c.id=s.collection_id "
            "WHERE j.id=%s AND j.status='running'",
            (job_id,),
        ).fetchone()
    if not row:
        return
    if row["kind"] == "website":
        pages = website_pages(row["locator"], max_pages=5)
    elif row["kind"] == "file":
        pages = parse_file(bytes(row["payload"]), row["locator"])
    else:
        pages = [{"text": bytes(row["payload"]).decode(), "page": None, "locator": ""}]
    parts = chunks(pages)
    collection = {
        "id": row["collection_id"],
        **{
            k: row[k]
            for k in ["embedding_provider", "embedding_model", "dimensions", "allow_external"]
        },
    }
    vectors = []
    for offset in range(0, len(parts), 32):
        batch = parts[offset : offset + 32]
        vectors.extend(models.embeddings([p["content"] for p in batch], collection))
    with db.connect() as conn:
        # Deletion during ingestion cancels persistence; no orphan index may be recreated.
        locked = conn.execute(
            "SELECT id FROM kb_sources WHERE id=%s FOR UPDATE", (row["id"],)
        ).fetchone()
        if not locked:
            return
        conn.execute("DELETE FROM kb_chunks WHERE source_id=%s", (row["id"],))
        with conn.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO kb_chunks "
                "(id,source_id,collection_id,content,page,locator,ordinal,embedding) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s::vector)",
                [
                    (
                        str(uuid.uuid4()),
                        row["id"],
                        row["collection_id"],
                        part["content"],
                        part["page"],
                        part["locator"],
                        i,
                        vector_literal(vector),
                    )
                    for i, (part, vector) in enumerate(zip(parts, vectors, strict=True))
                ],
            )
        conn.execute(
            "UPDATE kb_sources SET status='ready',error_code=NULL,chunk_count=%s WHERE id=%s",
            (len(parts), row["id"]),
        )
        conn.execute(
            "UPDATE kb_jobs SET status='ready',lease_until=NULL,error_code=NULL,updated_at=now() "
            "WHERE id=%s",
            (job_id,),
        )


if __name__ == "__main__":
    try:
        process(sys.argv[1])
    except SourceError as error:
        print(json.dumps({"code": error.code, "retryable": error.retryable}))
        sys.exit(2)
    except models.ModelError:
        print(json.dumps({"code": "PROVIDER_UNAVAILABLE", "retryable": True}))
        sys.exit(2)
    except Exception:
        print(json.dumps({"code": "INGESTION_FAILED", "retryable": True}))
        sys.exit(2)
