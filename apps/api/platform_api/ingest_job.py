"""One bounded child process per source. Never prints source text or provider errors."""

import json
import sys
import uuid

from . import db, jobs, models, telemetry
from .ingestion import SourceError, chunks, parse_file, website_pages
from .retrieval import vector_literal


@telemetry.traced("ingestion.process")
def process(job_id, token):
    with db.connect() as conn:
        row = conn.execute(
            "SELECT j.id AS job_id,s.*,c.embedding_provider,c.embedding_model,"
            "c.dimensions,c.allow_external,c.embedding_endpoint,c.embedding_digest,"
            "c.embedding_profile FROM kb_jobs j "
            "JOIN kb_sources s ON s.id=j.source_id "
            "JOIN kb_collections c ON c.id=s.collection_id "
            "WHERE j.id=%s AND j.status='running' AND j.attempt_token=%s "
            "AND j.lease_until>clock_timestamp()",
            (job_id, token),
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
            for k in [
                "embedding_provider",
                "embedding_model",
                "dimensions",
                "allow_external",
                "embedding_endpoint",
                "embedding_digest",
                "embedding_profile",
            ]
        },
    }
    vectors = []
    for offset in range(0, len(parts), 32):
        batch = parts[offset : offset + 32]
        vectors.extend(models.embeddings([p["content"] for p in batch], collection))
    with db.connect() as conn:
        # Lock ownership before any writes. Deletion and a reclaimed lease cancel
        # persistence, including an attempt that began before a manual retry.
        if not jobs.lock_attempt(conn, job_id, token):
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
            "UPDATE kb_jobs SET status='ready',lease_until=NULL,attempt_token=NULL,"
            "error_code=NULL,updated_at=now() "
            "WHERE id=%s",
            (job_id,),
        )


if __name__ == "__main__":
    try:
        import os

        telemetry.configure("platform-worker-child")
        with telemetry.span(
            "ingestion.attempt",
            parent=os.getenv("PLATFORM_TRACE_PARENT"),
            **{"job.id": sys.argv[1]},
        ):
            process(sys.argv[1], sys.argv[2])
    except SourceError as error:
        print(json.dumps({"code": error.code, "retryable": error.retryable}))
        sys.exit(2)
    except models.ModelError:
        print(json.dumps({"code": "PROVIDER_UNAVAILABLE", "retryable": True}))
        sys.exit(2)
    except Exception:
        print(json.dumps({"code": "INGESTION_FAILED", "retryable": True}))
        sys.exit(2)
    finally:
        telemetry.shutdown()
