"""
Live integration test for BandOrchestrator.

Bootstraps the intake venv's site-packages so band-sdk is importable
without needing it installed in this project's venv.

Run from ~/Projects/tabib-v1/src/:
    python3 band_test.py
"""

import os
import sys
import pathlib

# --- path bootstrap -------------------------------------------------------
_SRC = pathlib.Path(__file__).parent.resolve()
_INTAKE_SITE = (
    pathlib.Path.home()
    / "Projects/tabib-agents/intake/.venv/lib/python3.14/site-packages"
)
for _p in (_SRC, _INTAKE_SITE):
    s = str(_p)
    if s not in sys.path:
        sys.path.insert(0, s)

# Load BAND_API_KEY from tabib-v1/.env before importing the orchestrator
from dotenv import load_dotenv
load_dotenv(dotenv_path=_SRC.parent / ".env")
# --------------------------------------------------------------------------

from band_orchestrator import BandOrchestrator, BandSessionError, BandTimeoutError

POLL_TIMEOUT = 120  # seconds — enough for the full Intake→Diagnostic→Triage chain

CASES = [
    {
        "label": "CASE 1 (dengue)",
        "phone": "test-patient-001",
        "message": (
            "28 year old male, fever 102F since 2 days, "
            "severe body aches, joint pain, small red rash on chest"
        ),
    },
    {
        "label": "CASE 2 (fever)",
        "phone": "test-patient-002",
        "message": (
            "5 year old, fever 101F for 2 days, cough, "
            "runny nose, no difficulty breathing"
        ),
    },
]


def run_case(orc: BandOrchestrator, label: str, phone: str, message: str) -> None:
    print(f"\n{'─' * 60}")
    print(f"[{label}] Starting")
    print(f"[{label}] Message: {message}")

    try:
        session_id = orc.create_or_get_session(phone)
        print(f"[{label}] Session: {session_id}")

        orc.post_patient_message(session_id, message)
        print(f"[{label}] Message posted — polling for decision (timeout={POLL_TIMEOUT}s)…")

        decision = orc.poll_for_decision(session_id, timeout=POLL_TIMEOUT)
        print(f"\n[{label}] → Decision: {decision}")
        print(f"[{label}] ✅ PASS")

    except BandTimeoutError as e:
        print(f"[{label}] ⏱  TIMEOUT — {e}")
        print(f"[{label}] ❌ FAIL (timeout)")
    except BandSessionError as e:
        reason = str(e)
        if "plan_required" in reason:
            print(f"[{label}] ⚠️  PLAN REQUIRED — Human API needs an Enterprise plan.")
            print(f"[{label}] ⚠️  SKIP (upgrade needed, not a code bug)")
        else:
            print(f"[{label}] Band API error — {reason}")
            print(f"[{label}] ❌ FAIL (session error)")
    except Exception as e:
        print(f"[{label}] Unexpected error — {type(e).__name__}: {e}")
        print(f"[{label}] ❌ FAIL (unexpected)")


def main() -> None:
    print("=" * 60)
    print("TABIB Band Integration Test")
    print("=" * 60)

    try:
        orc = BandOrchestrator()
        print(f"BandOrchestrator ready (agent_id={orc._agent_id})")
    except BandSessionError as e:
        print(f"❌ Cannot initialise BandOrchestrator: {e}")
        sys.exit(1)

    for case in CASES:
        run_case(orc, **case)

    print(f"\n{'=' * 60}")
    print("Done.")


if __name__ == "__main__":
    main()
