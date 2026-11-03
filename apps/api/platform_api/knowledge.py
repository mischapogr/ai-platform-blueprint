"""Authenticated collection-scoped knowledge and model API."""

import hmac
import os
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field, field_validator

from . import db, jobs, local_models, models, telemetry
from .auth import issue_session, require_session, same_origin, secret
from .ingestion import MAX_BYTES
from .retrieval import embed, vector_literal

session_router = APIRouter(prefix="/api")
router = APIRouter(prefix="/api", dependencies=[Depends(require_session)])
RETRIEVAL_SCORE_CUTOFF = 0.1


@session_router.post("/session")
async def login(request: Request, response: Response):
    same_origin(request)
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "Expected a JSON access key") from None
    supplied = body.get("token", "") if isinstance(body, dict) else ""
    if not isinstance(supplied, str) or not supplied or not hmac.compare_digest(supplied, secret()):
        raise HTTPException(401, "Invalid workspace access key")
    response.set_cookie(
        "platform_session",
        issue_session(),
        httponly=True,
        secure=os.getenv("WORKSPACE_COOKIE_SECURE", "0") == "1",
        samesite="strict",
        max_age=8 * 3600,
        path="/api",
    )
    return {"authenticated": True}


@router.delete("/session")
def logout(response: Response):
    response.delete_cookie("platform_session", path="/api")
    return {"authenticated": False}


@router.get("/status")
def status():
    with db.connect() as conn:
        conn.execute("SELECT 1 FROM kb_collections LIMIT 1")
    return {
        "provider_configured": models.key_available(),
        "models": models.chat_models(),
        "local_provider_configured": local_models.configured(),
        "local_models": local_models.chat_models() if local_models.configured() else [],
        "credit_balance": None,
        "default_provider": "offline",
    }


class CollectionInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    embedding_provider: Literal["offline", "openai", "ollama"] = "offline"
    allow_external: bool = False

    @field_validator("name")
    @classmethod
    def not_blank(cls, value):
        if not value.strip():
            raise ValueError("A name is required")
        return value.strip()


@router.get("/collections")
def collections():
    with db.connect() as conn:
        return conn.execute(
            "SELECT c.*, (SELECT count(*) FROM kb_sources s WHERE s.collection_id=c.id) "
            "AS source_count,(SELECT count(*) FROM kb_chunks ch WHERE ch.collection_id=c.id) "
            "AS chunk_count FROM kb_collections c ORDER BY c.created_at"
        ).fetchall()


@router.post("/collections", status_code=201)
def create_collection(payload: CollectionInput):
    if payload.embedding_provider == "openai" and not payload.allow_external:
        raise HTTPException(
            400, "OpenAI embeddings require explicit external-processing permission"
        )
    if payload.embedding_provider == "openai" and not models.key_available():
        raise HTTPException(400, "Configure the local provider key before creating an OpenAI index")
    collection_id = str(uuid.uuid4())
    model = "hash-v1" if payload.embedding_provider == "offline" else "text-embedding-3-small"
    dims = 128 if payload.embedding_provider == "offline" else 1536
    local = {}
    if payload.embedding_provider == "ollama":
        try:
            local = local_models.embedding_config()
        except models.ModelError as error:
            raise HTTPException(400, str(error)) from None
        model, dims = local["model"], local["dimensions"]
    with db.connect() as conn:
        return conn.execute(
            "INSERT INTO kb_collections (id,name,description,embedding_provider,"
            "embedding_model,dimensions,allow_external,embedding_endpoint,embedding_digest,"
            "embedding_profile) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "RETURNING *",
            (
                collection_id,
                payload.name,
                payload.description,
                payload.embedding_provider,
                model,
                dims,
                payload.allow_external,
                local.get("endpoint"),
                local.get("digest"),
                local.get("profile", "plain"),
            ),
        ).fetchone()


def get_collection(collection_id):
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM kb_collections WHERE id=%s", (collection_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Collection not found")
    return row


@router.delete("/collections/{collection_id}")
def delete_collection(collection_id: uuid.UUID):
    get_collection(collection_id)
    with db.connect() as conn:
        conn.execute("DELETE FROM kb_collections WHERE id=%s", (collection_id,))
    return {"deleted": True}


@router.get("/collections/{collection_id}/sources")
def sources(collection_id: uuid.UUID):
    get_collection(collection_id)
    with db.connect() as conn:
        return conn.execute(
            "SELECT s.id,s.title,s.kind,s.locator,s.status,s.error_code,s.chunk_count,"
            "s.created_at,j.attempts FROM kb_sources s LEFT JOIN kb_jobs j ON j.source_id=s.id "
            "WHERE s.collection_id=%s ORDER BY s.created_at DESC",
            (collection_id,),
        ).fetchall()


class SourceInput(BaseModel):
    kind: Literal["website", "text"]
    title: str = Field(min_length=1, max_length=160)
    url: str = Field(default="", max_length=2000)
    text: str = Field(default="", max_length=16000)


@router.post("/collections/{collection_id}/sources", status_code=202)
def add_source(collection_id: uuid.UUID, payload: SourceInput):
    get_collection(collection_id)
    if payload.kind == "website":
        from urllib.parse import urlsplit

        try:
            target = urlsplit(payload.url)
            valid = (
                target.scheme == "https"
                and target.hostname
                and not target.username
                and not target.password
                and target.port in (None, 443)
            )
        except ValueError:
            valid = False
        if not valid:
            raise HTTPException(400, "A public HTTPS URL without credentials is required")
        locator = payload.url
        data = None
    else:
        if not payload.text.strip():
            raise HTTPException(400, "Text is empty")
        locator = ""
        data = payload.text.encode()
    return jobs.create_source(collection_id, payload.title, payload.kind, locator, data)


@router.post("/collections/{collection_id}/upload", status_code=202)
async def upload(collection_id: uuid.UUID, file: Annotated[UploadFile, File()]):
    get_collection(collection_id)
    filename = (file.filename or "upload").replace("\\", "/").rsplit("/", 1)[-1][:180]
    if not filename.lower().endswith((".pdf", ".txt", ".md")):
        raise HTTPException(400, "Supported files: PDF, UTF-8 TXT and Markdown")
    data = await file.read(MAX_BYTES + 1)
    await file.close()
    if not data or len(data) > MAX_BYTES:
        raise HTTPException(413, "Use a non-empty file up to 10 MiB")
    return jobs.create_source(collection_id, filename, "file", filename, data)


@router.delete("/sources/{source_id}")
def delete_source(source_id: uuid.UUID):
    with db.connect() as conn:
        deleted = conn.execute(
            "DELETE FROM kb_sources WHERE id=%s RETURNING id", (source_id,)
        ).fetchone()
    if not deleted:
        raise HTTPException(404, "Source not found")
    return {"deleted": True}


@router.post("/sources/{source_id}/retry", status_code=202)
def retry_source(source_id: uuid.UUID):
    with db.connect() as conn:
        row = conn.execute(
            "SELECT id,status FROM kb_sources WHERE id=%s FOR UPDATE", (source_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Source not found")
        if row["status"] != "failed":
            raise HTTPException(409, "Only failed jobs can be retried")
        conn.execute(
            "UPDATE kb_jobs SET status='queued',attempts=0,error_code=NULL,"
            "available_at=now(),lease_until=NULL,attempt_token=NULL WHERE source_id=%s",
            (source_id,),
        )
        conn.execute(
            "UPDATE kb_sources SET status='queued',error_code=NULL WHERE id=%s", (source_id,)
        )
    jobs.notify()
    return {"status": "queued"}


@router.get("/sources/{source_id}/preview")
def preview(source_id: uuid.UUID):
    with db.connect() as conn:
        return conn.execute(
            "SELECT content,page,locator,ordinal FROM kb_chunks WHERE source_id=%s "
            "ORDER BY ordinal LIMIT 8",
            (source_id,),
        ).fetchall()


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=3000)


class ChatInput(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    collection_id: uuid.UUID | None = None
    mode: Literal["knowledge", "chat"] = "knowledge"
    provider: Literal["offline", "openai", "ollama"] = "offline"
    model: str = Field(default="gpt-6-luna", max_length=100)
    external_consent: bool = False
    history: list[Message] = Field(default_factory=list, max_length=8)
    top_k: int = Field(default=4, ge=1, le=5)

    @field_validator("question")
    @classmethod
    def meaningful(cls, value):
        embed(value)
        return value


@telemetry.traced("retrieval.query")
def retrieve(question, collection, top_k):
    vector = vector_literal(models.embeddings([question], collection, purpose="query")[0])
    with db.connect() as conn:
        return conn.execute(
            "WITH scoped AS MATERIALIZED (SELECT ch.*,s.title FROM kb_chunks ch "
            "JOIN kb_sources s ON s.id=ch.source_id "
            "WHERE ch.collection_id=%s AND s.status='ready') "
            "SELECT id,source_id,title,content,page,locator,1-(embedding <=> %s::vector) AS score "
            "FROM scoped WHERE 1-(embedding <=> %s::vector)>=%s "
            "ORDER BY embedding <=> %s::vector,id LIMIT %s",
            (collection["id"], vector, vector, RETRIEVAL_SCORE_CUTOFF, vector, top_k),
        ).fetchall()


@router.post("/chat")
def chat(payload: ChatInput):
    collection = get_collection(payload.collection_id) if payload.collection_id else None
    if payload.mode == "knowledge" and collection is None:
        raise HTTPException(400, "Select one collection for knowledge chat")
    needs_provider = payload.provider == "openai" or (
        payload.mode == "knowledge" and collection and collection["embedding_provider"] == "openai"
    )
    if needs_provider:
        if not payload.external_consent:
            raise HTTPException(400, "Explicit consent to send data to the provider is required")
        if collection and not collection["allow_external"]:
            raise HTTPException(403, "This collection is local-only")
        if not models.key_available():
            raise HTTPException(503, "Provider key is not configured locally")
    embedding_calls = int(
        bool(
            collection
            and payload.mode == "knowledge"
            and collection["embedding_provider"] in {"openai", "ollama"}
        )
    )
    try:
        passages = (
            retrieve(payload.question, collection, payload.top_k)
            if payload.mode == "knowledge"
            else []
        )
        if payload.mode == "knowledge" and not passages:
            return {
                "answer": (
                    "No matching passages found in this collection. "
                    "Add sources or rephrase your question."
                ),
                "sources": [],
                "provider_calls": embedding_calls,
                "usage": None,
                "mode": "no-match",
            }
        if payload.provider == "offline":
            answer = "\n\n".join(f"[{i}] {p['content']}" for i, p in enumerate(passages, 1))
            if payload.mode == "chat":
                answer = (
                    "Choose a model provider for general chat, or select Knowledge mode to search."
                )
            usage = None
        else:
            target = collection or {"id": None, "allow_external": True}
            answer, usage = models.generate(
                payload.question,
                [m.model_dump() for m in payload.history],
                passages,
                target,
                payload.model,
                provider=payload.provider,
                knowledge=payload.mode == "knowledge",
            )
        return {
            "answer": answer,
            "sources": passages,
            "usage": usage,
            "mode": "offline-extractive" if payload.provider == "offline" else payload.provider,
            "provider_calls": int(payload.provider != "offline") + embedding_calls,
        }
    except models.ModelError as error:
        raise HTTPException(502, str(error)) from None


@router.get("/usage")
def usage():
    with db.connect() as conn:
        return conn.execute(
            "SELECT model,operation,sum(input_tokens) AS input_tokens,"
            "sum(output_tokens) AS output_tokens,count(*) AS calls "
            "FROM kb_usage GROUP BY model,operation ORDER BY model,operation"
        ).fetchall()
