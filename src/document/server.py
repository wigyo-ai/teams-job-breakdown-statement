"""
Document Generator Service — internal HTTP server.

Exposes two endpoints:
  GET  /health    — liveness probe
  POST /generate  — render approved JBS JSON to .docx and return a signed Azure Blob SAS URL

This service is called by the Conversation Orchestrator via HTTP (internal ACA ingress only).
It is NOT exposed externally.
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from .generator import DocumentGenerator

app = FastAPI(title="JBS Document Generator", version="1.0.0")

_doc_gen = DocumentGenerator()


class GenerateRequest(BaseModel):
    jbs_json: dict


class GenerateResponse(BaseModel):
    download_url: str


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/generate", response_model=GenerateResponse)
async def generate(request: GenerateRequest):
    try:
        url = await _doc_gen.generate(request.jbs_json)
        return GenerateResponse(download_url=url)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
