from fastapi import FastAPI

from rag_qa.env import load_env

load_env()  # .env fills gaps for local runs; real env vars win (SPEC-001 KD-6)

app = FastAPI(title="enterprise-rag-qa")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
