# app.py
import os, re
from typing import Optional
from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.responses import JSONResponse

# Read API key from env ONLY (never hardcode secrets)
API_KEY = os.getenv("GENE_API_KEY", "dev-key")

# Thresholds
DEBT_HIGH = int(os.getenv("PRIMARY_DEBT_HIGH", "8000"))      # qualify at/above this
SECONDARY_LOW = int(os.getenv("SECONDARY_DEBT_LOW", "6000")) # ask unfiled years if under this

AUTO_ESCALATE = {
    "chargeback","refund","billing","attorney","lawyer",
    "levy","lien","garnish","garnishment","lawsuit","complaint","harassment"
}

app = FastAPI(title="Gene Woo Fallback")

def has_any(text: str, keywords: set) -> bool:
    t = (text or "").lower()
    return any(k in t for k in keywords)

def parse_amount(text: str) -> Optional[int]:
    """
    Parse amounts like: 12000, 12,000, 12k, $12k, $12,000, 8 k
    Returns integer dollars or None.
    """
    if not text:
        return None
    t = text.lower().replace(",", "").replace(" ", "")
    m = re.search(r'\$?(\d+)(k)?', t)
    if not m:
        return None
    num = int(m.group(1))
    if m.group(2):  # has 'k'
        num *= 1000
    return num

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
    # 1) Auto-escalate for sensitive topics
    if has_any(text, AUTO_ESCALATE):
        return {
            "action": "escalate",
            "reply_text": None,
            "notes": "auto_escalate_keyword",
            "escalation": {
                "summary": "Sensitive keyword detected (billing/legal/etc.)",
                "suggested": f"Hi {name}, I'm looping a specialist in now. What's the best number/time today?"
            },
            "qualified": {"band": "unknown", "has_unfiled_years": "unknown", "state_issue": "unknown"}
        }

    # 2) If an amount is present, decide path
    amount = parse_amount(text)
    if amount is not None:
        if amount >= DEBT_HIGH:
            return {
                "action": "qualified",
                "reply_text": "Great, thanks — I’ll send the booking link now.",
                "notes": "auto_qualified_by_amount",
                "qualified": {"band": "over_threshold", "amount": amount, "has_unfiled_years": "unknown", "state_issue": "unknown"}
            }
        if amount < SECONDARY_LOW:
            return {
                "action": "reply",
                "reply_text": "Thanks. Do you have any missing tax years that need to be filed? - Gene, Lexington Tax Group",
                "notes": "followup_missing_years_under_threshold",
                "qualified": {"band": "under_secondary", "amount": amount, "has_unfiled_years": "unknown", "state_issue": "unknown"}
            }
        # Mid band
        return {
            "action": "reply",
            "reply_text": "Thanks for the details. Do you prefer I send a 10-minute booking link, or keep going by text?",
            "notes": "mid_band_next_step",
            "qualified": {"band": "mid_band", "amount": amount, "has_unfiled_years": "unknown", "state_issue": "unknown"}
        }

    # 3) Default clarify (your two-question line)
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
async def woofallback(req: Request, authorization:
