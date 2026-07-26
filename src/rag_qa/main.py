from fastapi import FastAPI

app = FastAPI(title="enterprise-rag-qa")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
