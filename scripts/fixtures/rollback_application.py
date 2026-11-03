"""Synthetic database operations executed inside the reference/candidate image."""

import inspect
import json
import sys
import uuid
from pathlib import Path

from platform_api import db, ingest_job, knowledge

Path("/tmp/rollback-password").write_text("synthetic-benchmark-only")  # noqa: S108
operation = json.loads(sys.argv[1])
if operation["kind"] == "write":
    collection_id = operation.get("collection_id")
    if not collection_id:
        collection = knowledge.create_collection(knowledge.CollectionInput(name="Rollback fixture"))
        collection_id = str(collection["id"])
    source_id, job_id, token = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    fenced = len(inspect.signature(ingest_job.process).parameters) > 1
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO kb_sources(id,collection_id,title,kind,payload,status) "
            "VALUES (%s,%s,'Synthetic rollback','text',%s,'running')",
            (source_id, collection_id, operation["text"].encode()),
        )
        conn.execute(
            "INSERT INTO kb_jobs(id,source_id,status,lease_until) "
            "VALUES (%s,%s,'running',now()+interval '240 seconds')",
            (job_id, source_id),
        )
        if fenced:
            conn.execute("UPDATE kb_jobs SET attempt_token=%s WHERE id=%s", (token, job_id))
    if fenced:
        ingest_job.process(job_id, token)
    else:
        ingest_job.process(job_id)
    result = {"collection_id": collection_id}
elif operation["kind"] == "query":
    result = knowledge.chat(
        knowledge.ChatInput(
            question=operation["question"],
            collection_id=operation["collection_id"],
        )
    )
elif operation["kind"] == "local-metadata":
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO kb_collections(id,name,embedding_provider,embedding_model,dimensions,"
            "allow_external,embedding_endpoint,embedding_digest,embedding_profile) "
            "VALUES (%s,'Synthetic local rollback boundary','ollama','synthetic-not-invoked',"
            "768,false,'http://ollama:11434',%s,'nomic-v1')",
            (uuid.uuid4(), "a" * 64),
        )
    result = {"created": True}
elif operation["kind"] == "providers":
    with db.connect() as conn:
        rows = conn.execute("SELECT DISTINCT embedding_provider FROM kb_collections").fetchall()
        result = [row["embedding_provider"] for row in rows]
else:
    raise ValueError("Unknown test operation")
print(json.dumps(result, default=str))
