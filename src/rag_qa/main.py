import os

from fastapi import FastAPI

from rag_qa.env import load_env

load_env()  # .env fills gaps for local runs; real env vars win (SPEC-001 KD-6)

app = FastAPI(title="enterprise-rag-qa")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


def _wire_ask() -> None:
    """Mount /ask when the environment can actually serve it.

    Kept lazy and guarded so `/healthz` — and the whole test suite — never
    require a database, an embedding key, or a generation key just to import the
    app (SPEC-001's scaffold contract).
    """
    if not (os.environ.get("DATABASE_URL") and os.environ.get("ANTHROPIC_API_KEY")):
        return

    from rag_qa.db.engine import create_engine, create_session_factory
    from rag_qa.generation.api import build_router
    from rag_qa.generation.clients.anthropic import AnthropicClient
    from rag_qa.generation.service import Generator
    from rag_qa.ingest.embedder import OpenAIEmbeddingClient
    from rag_qa.retrieval.service import Retriever

    factory = create_session_factory(create_engine())
    retriever = Retriever(factory, OpenAIEmbeddingClient())
    generator = Generator(AnthropicClient(), session_factory=factory)
    app.include_router(build_router(retriever, generator))


_wire_ask()
