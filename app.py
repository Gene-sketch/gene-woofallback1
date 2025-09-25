# app.py
import os
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

@app.post("/gene/woofallback")
async def woofallback(req: Request, authorization: str | None = Header(None)):
    # simple bearer check so randoms can’t hit your endpoint
    if authorization != f"Bearer {API_KEY}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    payload = await req.json()
    text = ((payload or {}).get("message") or {}).get("text", "")

    # 1) auto-escalate for sensitive topics (billing/legal, etc.)
    if has_any(text, AUTO_ESCALATE):
        return JSONResponse({
            "action": "escalate",
            "reply_text": None,
            "notes": "Auto-escalate keyword present",
            "escalation": {
                "summary": "Sensitive keyword detected (billing/legal/etc.)",
                "suggested": "Hi there, I’m looping a specialist in now. What’s the best number/time today?"
            },
            "qualified": {"band": "unknown", "has_unfiled_years": "unknown", "state_issue": "unknown"}
        })

    # 2) default = clarify to qualify (your main flow)
    return JSONResponse({
        "action": "reply",
        "reply_text": (
            "Thanks for the note—quick check so I point you the right way: "
            f"about how much does the IRS say you owe—over or under ${DEBT_HIGH:,}? —Gene, Lexington Tax Group"
        ),
        "notes": "primary_clarify",
        "qualified": {"band": "unknown", "has_unfiled_years": "unknown", "state_issue": "unknown"}
    })
