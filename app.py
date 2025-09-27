# app.py
import os
from typing import Optional
from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.responses import JSONResponse

API_KEY = os.getenv("GENE_API_KEY", "dev-key")


DEBT_HIGH = int(os.getenv("PRIMARY_DEBT_HIGH", "8000"))

AUTO_ESCALATE = {
    "chargeback","refund","billing","attorney","lawyer",
    "levy","lien","garnish","garnishment","lawsuit","complaint","harassment"
}

app = FastAPI(title="Gene Woo Fallback")

def has_any(text: str, keywords: set) -> bool:
    t = (text or "").lower()
    return any(k in t for k in keywords)

@app.get("/")
async def health():
    return {"ok": True, "service": "gene-woofallback"}

async def _parse(req: Request):
    payload = await req.json()
    lead = (payload or {}).get("lead") or {}
    name = lead.get("name") or "there"
    text = ((payload or {}).get("message") or {}).get("text", "")
    return text, name

def _build_response(text: str, name: str):
    # 1) auto-escalate for sensitive topics
    if has_any(text, AUTO_ESCALATE):
        return {
            "action": "escalate",
            "reply_text": None,
            "notes": "Auto-escalate keyword present",
            "escalation": {
                "summary": "Sensitive keyword detected (billing/legal/etc.)",
                "suggested": f"Hi {name}, I'm looping a specialist in now. What's the best number/time today?"
            },
            "qualified": {"band": "unknown", "has_unfiled_years": "unknown", "state_issue": "unknown"}
        }
    # 2) default = clarify to qualify (your main flow)
    return {
        "action": "reply",
        "reply_text": (
            "Thanks for the note. Quick check: how much does the IRS say you owe, "
            "and do you have any missing tax years that need to be filed? - Gene, Lexington Tax Group"
        ),
        "notes": "primary_clarify",
        "qualified": {"band": "unknown", "has_unfiled_years": "unknown", "state_issue": "unknown"}
    }

async def _auth(authorization: Optional[str]):
    if authorization != f"Bearer {API_KEY}":
        raise HTTPException(status_code=401, detail="Unauthorized")

@app.post("/gene/woofallback")
async def woofallback(req: Request, authorization: Optional[str] = Header(None)):
    await _auth(authorization)
    text, name = await _parse(req)
    return JSONResponse(_build_response(text, name))

@app.post("/gene/woofallback1")
async def woofallback1(req: Request, authorization: Optional[str] = Header(None)):
    await _auth(authorization)
    text, name = await _parse(req)
    return JSONResponse(_build_response(text, name))
