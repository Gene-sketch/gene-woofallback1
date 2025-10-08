# app.py
import os
import re
from typing import Optional, Tuple

from fastapi import FastAPI, Request, Header, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse

# httpx is optional; used only if you set WOO_WEBHOOK_URL
try:
    import httpx  # type: ignore
except Exception:  # pragma: no cover
    httpx = None  # fallback if not installed

# ----------------------------
# Config via environment vars
# ----------------------------
SERVICE_NAME  = os.getenv("SERVICE_NAME", "gene-woofallback")
API_KEY       = os.getenv("GENE_API_KEY", "dev-key")

DEBT_HIGH     = int(os.getenv("PRIMARY_DEBT_HIGH", "8000"))   # auto-qualify at/above this
SECONDARY_LOW = int(os.getenv("SECONDARY_DEBT_LOW", "6000"))  # ask/check unfiled when under this
MID_APPT_LOW  = int(os.getenv("MID_APPT_LOW", "5000"))        # 5–7k mid band
MID_APPT_HIGH = int(os.getenv("MID_APPT_HIGH", "7000"))

# Campaign label Woo should switch to when appointment is booked
CAMPAIGN_NAME = os.getenv("CAMPAIGN_BOOKED_NAME", "1st Trade Scheduled")

# Optional: Gene can also POST the decision to a Woo webhook you control
WOO_WEBHOOK_URL   = os.getenv("WOO_WEBHOOK_URL", "")
WOO_WEBHOOK_TOKEN = os.getenv("WOO_WEBHOOK_TOKEN", "")

# Keywords that should immediately escalate to a human
AUTO_ESCALATE = {
    "chargeback", "refund", "billing", "attorney", "lawyer",
    "levy", "lien", "garnish", "garnishment", "lawsuit", "complaint", "harassment"
}

app = FastAPI(title="Gene Woo Fallback")

# ----------------------------
# Helpers
# ----------------------------
def has_any(text: str, keywords: set) -> bool:
    t = (text or "").lower()
    return any(k in t for k in keywords)

def parse_amount(text: str) -> Optional[int]:
    """Parse amounts like: 12000, 12,000, 12k, $12k, $12,000 -> int dollars"""
    if not text:
        return None
    t = text.lower().replace(",", "").strip()
    m = re.search(r'(\$?\d+)\s*(k)?', t)
    if not m:
        return None
    num = int(m.group(1).lstrip("$"))
    if m.group(2):  # 'k'
        num *= 1000
    return num

def detect_unfiled(text: str) -> bool:
    """Detect phrases that suggest missing/unfiled tax years."""
    if not text:
        return False
    t = text.lower()
    patterns = [
        r"\bunfiled\b",
        r"\bmissing\s+(?:tax\s+)?years?\b",
        r"\b(?:not|haven'?t|didn'?t)\s+file(d)?\b",
        r"\bbehind\s+on\s+filing\b",
        r"\bback\s+(?:returns?|years?)\b",
    ]
    return any(re.search(p, t) for p in patterns)

def detect_no_unfiled(text: str) -> bool:
    """Detect they are up to date / no missing years."""
    if not text:
        return False
    t = text.lower()
    patterns = [
        r"\bno\s+(?:missing\s+)?(?:tax\s+)?years?\b",
        r"\ball\s+(?:returns?|years?)\s+filed\b",
        r"\bup\s*to\s*date\s+on\s+filing\b",
        r"\beverything\s+is\s+filed\b",
        r"\ball\s+filed\b",
        r"\bcurrent\s+on\s+filing\b",
    ]
    return any(re.search(p, t) for p in patterns)

def detect_state_issue(text: str) -> bool:
    """Detect references to state tax agencies/issues."""
    if not text:
        return False
    t = text.lower()
    patterns = [
        r"\bstate\s+tax(es)?\b",
        r"\bdepartment\s+of\s+revenue\b",
        r"\bdor\b",
        r"\bfranchise\s+tax\s+board\b",
        r"\bftb\b",
        r"\bedd\b",
        r"\bdtf\b",
    ]
    return any(re.search(p, t) for p in patterns)

def first_name(name: str) -> str:
    n = (name or "").strip()
    return n.split()[0] if n else "there"

# ----------------------------
# Routes
# ----------------------------
@app.get("/")
async def health():
    return {"ok": True, "service": SERVICE_NAME}

async def _parse(req: Request) -> Tuple[dict, str, str]:
    payload = await req.json()
    lead = (payload or {}).get("lead") or {}
    name = lead.get("name") or "there"
    text = ((payload or {}).get("message") or {}).get("text", "")
    return payload, text, name

def _build_response(payload: dict, text: str, name: str):
    # 1) Auto-escalate for sensitive topics
    if has_any(text, AUTO_ESCALATE):
        return {
            "action": "escalate",
            "reply_text": None,
            "notes": "auto_escalate_keyword",
            "escalation": {
                "summary": "Sensitive keyword detected (billing/legal/etc.)",
                "suggested": f"Hi {first_name(name)}, I'm looping a specialist in now. What's the best number/time today?"
            },
            "qualified": {"band": "unknown", "has_unfiled_years": "unknown", "state_issue": "unknown"}
        }

    # 2) Extract signals
    amount = parse_amount(text)
    unfiled = detect_unfiled(text)
    no_unfiled = detect_no_unfiled(text)
    state_flag = "yes" if detect_state_issue(text) else "unknown"

    # Woo can pass last known amount back to us on the next call
    context = (payload or {}).get("context") or {}
    last_amount = context.get("last_amount")
    if isinstance(last_amount, (float, int)):
        last_amount = int(last_amount)

    # 3) Non-qualify self-help: under threshold AND no unfiled
    amt_for_decision = amount if amount is not None else last_amount
    if (amt_for_decision is not None) and (amt_for_decision < SECONDARY_LOW) and no_unfiled:
        msg = (
            f"Hi {first_name(name)}, due to the amount you owe the fees for service may outweigh the savings. "
            "I recommend contacting the IRS directly and requesting a First-Time Penalty Abatement, then the Fresh Start "
            "Streamlined Installment Agreement. That will usually be the smallest payment without submitting full financials "
            "to the IRS with the best terms. Below is the IRS contact information (also on your latest notice). Thank you.\n\n"
            "www.irs.gov\n800-829-1040"
        )
        return {
            "action": "reply",
            "reply_text": msg,
            "notes": "disqualify_self_help",
            "route": "irs_self_help",
            "workflow": {
                "crm": {"system": "Velocify", "status": "Self Help Provided"}
            },
            "qualified": {"band": "under_secondary", "amount": amt_for_decision, "has_unfiled_years": "no", "state_issue": state_flag}
        }

    # 4) Amount-aware paths
    if amount is not None:
        # A) Over threshold => Book via Woo
        if amount >= DEBT_HIGH:
            return {
                "action": "qualified",
                "reply_text": "Great, thanks - we will get you scheduled now to review options, including IRS Fresh Start savings programs, and check any state issues if that applies.",
                "notes": "auto_qualified_by_amount",
                "route": "woo_booking",
                "handoff": {"to": "woo", "type": "appointment_request", "reason": "over_threshold"},
                "workflow": {
                    "schedule_in_woo": True,
                    "campaign": {"name": CAMPAIGN_NAME, "action": "switch"},
                    "crm": {"system": "Velocify", "status": "AI Appointment Scheduled"}
                },
                "qualified": {"band": "over_threshold", "amount": amount, "has_unfiled_years": "unknown", "state_issue": state_flag}
            }

        # B) 5–7k AND unfiled => Book via Woo
        if MID_APPT_LOW <= amount <= MID_APPT_HIGH and unfiled:
            return {
                "action": "qualified",
                "reply_text": "Got it - we will get you scheduled now to review options, including IRS Fresh Start savings programs, and any state issues if that applies.",
                "notes": "qualified_mid_with_unfiled",
                "route": "woo_booking",
                "handoff": {"to": "woo", "type": "appointment_request", "reason": "mid_with_unfiled"},
                "workflow": {
                    "schedule_in_woo": True,
                    "campaign": {"name": CAMPAIGN_NAME, "action": "switch"},
                    "crm": {"system": "Velocify", "status": "AI Appointment Scheduled"}
                },
                "qualified": {"band": "mid_with_unfiled", "amount": amount, "has_unfiled_years": "yes", "state_issue": state_flag}
            }

        # C) Under secondary low -> if filing status unknown, ask about missing years
        if amount < SECONDARY_LOW:
            return {
                "action": "reply",
                "reply_text": "Thanks. Do you have any missing tax years that need to be filed? - Gene, Lexington Tax Group",
                "notes": "followup_missing_years_under_threshold",
                "qualified": {"band": "under_secondary", "amount": amount, "has_unfiled_years": "unknown", "state_issue": state_flag}
            }

        # D) Mid band without unfiled mention -> nudge to booking via Woo
        return {
            "action": "reply",
            "reply_text": "Thanks for the details - we will get you scheduled for a quick 10-minute call to review options, including IRS Fresh Start savings programs, and any state issues if that applies.",
            "notes": "mid_band_send_booking",
            "route": "woo_booking",
            "handoff": {"to": "woo", "type": "appointment_request", "reason": "mid_band"},
            "workflow": {
                "schedule_in_woo": True,
                "campaign": {"name": CAMPAIGN_NAME, "action": "switch"},
                "crm": {"system": "Velocify", "status": "AI Appointment Scheduled"}
            },
            "qualified": {"band": "mid_band", "amount": amount, "has_unfiled_years": "unknown", "state_issue": state_flag}
        }

    # 5) Default clarify (combined question + value prop)
    return {
        "action": "reply",
        "reply_text": (
            "Thanks for the note. Quick check: how much does the IRS say you owe, "
            "and do you have any missing tax years that need to be filed? We can also discuss IRS Fresh Start savings options "
            "and any state issues if that applies. - Gene, Lexington Tax Group"
        ),
        "notes": "primary_clarify",
        "qualified": {"band": "unknown", "has_unfiled_years": "unknown", "state_issue": "unknown"}
    }

async def _auth(authorization: Optional[str]):
    if authorization != f"Bearer {API_KEY}":
        raise HTTPException(status_code=401, detail="Unauthorized")

async def _post_to_woo_async(payload: dict, decision: dict) -> None:
    """Background: forward Gene's decision to Woo (if configured)."""
    if not WOO_WEBHOOK_URL or httpx is None:
        return
    try:
        headers = {"Content-Type": "application/json"}
        if WOO_WEBHOOK_TOKEN:
            headers["Authorization"] = f"Bearer {WOO_WEBHOOK_TOKEN}"
        body = {"source": "gene-woofallback", "decision": decision, "payload": payload}
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(WOO_WEBHOOK_URL, json=body, headers=headers)
    except Exception:
        # swallow errors; Gene should still reply to the lead
        pass

@app.post("/gene/woofallback")
async def woofallback(req: Request, authorization: Optional[str] = Header(None), background_tasks: BackgroundTasks = None):
    await _auth(authorization)
    payload, text, name = await _parse(req)
    decision = _build_response(payload, text, name)
    if decision.get("route") == "woo_booking" and WOO_WEBHOOK_URL and background_tasks is not None:
        background_tasks.add_task(_post_to_woo_async, payload, decision)
        decision["forward_queued"] = True
    return JSONResponse(decision)

@app.post("/gene/woofallback1")
async def woofallback1(req: Request, authorization: Optional[str] = Header(None), background_tasks: BackgroundTasks = None):
    await _auth(authorization)
    payload, text, name = await _parse(req)
    decision = _build_response(payload, text, name)
    if decision.get("route") == "woo_booking" and WOO_WEBHOOK_URL and background_tasks is not None:
        background_tasks.add_task(_post_to_woo_async, payload, decision)
        decision["forward_queued"] = True
    return JSONResponse(decision)
