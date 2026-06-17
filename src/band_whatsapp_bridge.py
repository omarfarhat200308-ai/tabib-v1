"""
WhatsApp → Band bridge for TABIB.

Flow per incoming message:
1. POST /webhook returns 200 immediately (Twilio 15s timeout: never block)
2. Background task:
   a. Create Band session as TIA, add TDA+TTA as participants
   b. Post "@TDA <symptoms>" with TDA mention
   c. Poll list_agent_messages for "Decision:" (45s timeout)
   d. Send decision to patient via Twilio Messages API (async push)
3. On timeout or any Band error: fall back to inline run_tabib_pipeline.
   Never fail silently — always send the patient a reply.
"""

import logging
import os
import pathlib
import sys

# Bootstrap band-sdk from the intake agent venv (no Enterprise plan needed)
_SRC = pathlib.Path(__file__).parent.resolve()
_INTAKE_SITE = (
    pathlib.Path.home()
    / "Projects/tabib-agents/intake/.venv/lib/python3.14/site-packages"
)
for _p in (_SRC, _INTAKE_SITE):
    s = str(_p)
    if s not in sys.path:
        sys.path.insert(0, s)

from dotenv import load_dotenv
load_dotenv(dotenv_path=_SRC.parent / ".env")

import httpx
from fastapi import BackgroundTasks, FastAPI, Form
from twilio.rest import Client as TwilioClient

from band_coordinator import run_tabib_pipeline
from band_orchestrator import BandOrchestrator, BandSessionError

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="TABIB WhatsApp-Band Bridge")

_twilio = TwilioClient(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
TWILIO_FROM = os.getenv("TWILIO_WHATSAPP_FROM")

TIA_KEY = os.getenv("BAND_AGENT_API_KEY")
TIA_ID = os.getenv("BAND_AGENT_ID")
TDA_ID = os.getenv("BAND_TDA_ID") or None
TTA_ID = os.getenv("BAND_TTA_ID") or None

BAND_REST_URL = "https://app.band.ai"
POLL_TIMEOUT = 45  # seconds


# ---------------------------------------------------------------------------
# Twilio send (module-level so tests can patch it)
# ---------------------------------------------------------------------------

def send_whatsapp(to: str, message: str) -> None:
    _twilio.messages.create(from_=TWILIO_FROM, to=to, body=message)
    logger.info("[BRIDGE] Sent WhatsApp to %s: %r", to, message[:100])


# ---------------------------------------------------------------------------
# Background processing
# ---------------------------------------------------------------------------

def _fallback_and_send(from_number: str, body: str) -> None:
    """Run inline TABIB pipeline and send result. Last-resort error handler."""
    try:
        result = run_tabib_pipeline(
            whatsapp_message=body,
            patient_id=from_number.replace("whatsapp:+", ""),
        )
        decision = (
            result
            if result.lstrip().startswith("Decision:")
            else f"Decision: {result}"
        )
        send_whatsapp(from_number, f"{decision} [path=fallback]")
    except Exception as exc:
        logger.error("[BRIDGE] Fallback pipeline failed for %s: %s", from_number, exc)
        send_whatsapp(
            from_number,
            "⚠️ TABIB is temporarily unavailable. Please seek in-person care if urgent.",
        )


def process_patient(from_number: str, body: str) -> None:
    """Background task: Band multi-agent loop → Twilio push. Never fails silently."""
    logger.info("[BRIDGE] Processing %s: %r", from_number, body[:80])
    try:
        orc = BandOrchestrator(
            agent_api_key=TIA_KEY,
            agent_id=TIA_ID,
            tda_id=TDA_ID,
            tta_id=TTA_ID,
        )
        session_id = orc.create_or_get_session(from_number)
        orc.post_patient_message(session_id, body)
        decision = orc.poll_for_decision(session_id, timeout=POLL_TIMEOUT)
        send_whatsapp(from_number, decision)
    except BandSessionError as exc:
        logger.error("[BRIDGE] Band error for %s: %s — running fallback", from_number, exc)
        _fallback_and_send(from_number, body)
    except Exception as exc:
        logger.error("[BRIDGE] Unexpected error for %s: %s — running fallback", from_number, exc)
        _fallback_and_send(from_number, body)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.post("/webhook")
async def whatsapp_webhook(
    background_tasks: BackgroundTasks,
    Body: str = Form(...),
    From: str = Form(...),
):
    """Twilio webhook — return 200 immediately, process in background."""
    logger.info("[BRIDGE] Webhook: From=%s Body=%r", From, Body[:80])
    background_tasks.add_task(process_patient, From, Body)
    return {"status": "received"}


@app.get("/health")
async def health():
    """Return Band agent configuration status and live peer connectivity."""
    agents = {
        "TIA": {
            "id": TIA_ID or "(not set)",
            "status": "configured" if (TIA_KEY and TIA_ID) else "missing",
        },
        "TDA": {
            "id": TDA_ID or "(not set)",
            "status": "configured" if TDA_ID else "missing",
        },
        "TTA": {
            "id": TTA_ID or "(not set)",
            "status": "configured" if TTA_ID else "missing",
        },
    }

    # Quick live check: verify TIA key can reach the Band peer registry
    band_reachable = False
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{BAND_REST_URL}/api/v1/agent/peers",
                headers={"X-API-Key": TIA_KEY or ""},
            )
            band_reachable = resp.status_code == 200
    except Exception:
        pass

    return {
        "status": "ok" if band_reachable else "degraded",
        "band_api_reachable": band_reachable,
        "twilio_from": TWILIO_FROM or "(not set)",
        "agents": agents,
    }


@app.get("/")
def root():
    return {"service": "TABIB WhatsApp-Band Bridge", "version": "1.0"}
