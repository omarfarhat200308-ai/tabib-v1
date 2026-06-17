"""
Integration test for band_whatsapp_bridge.

Simulates a Twilio POST to /webhook with the dengue case.
Twilio send is mocked so no real WhatsApp message is sent.
The Band loop (or inline fallback) runs for real — Decision: expected in output.

Run from tabib-v1/src/:
    python3 test_whatsapp_bridge.py
"""

import pathlib
import sys

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

# Must import after env is loaded
from unittest.mock import patch

from fastapi.testclient import TestClient

import band_whatsapp_bridge as bridge

DENGUE_CASE = (
    "28 year old male, fever 39.5°C for 3 days, severe headache, "
    "pain behind eyes, joint pain, small red spots on arms. "
    "No vomiting. No medications."
)

FROM_NUMBER = "whatsapp:+919849828813"


def main() -> None:
    print("=" * 60)
    print("TABIB WhatsApp-Band Bridge — Integration Test")
    print("=" * 60)
    print(f"Test case : {DENGUE_CASE}")
    print(f"From      : {FROM_NUMBER}")
    print(f"TIA       : {bridge.TIA_ID}")
    print(f"TDA       : {bridge.TDA_ID or '(not set)'}")
    print(f"TTA       : {bridge.TTA_ID or '(not set)'}")
    print(f"Timeout   : {bridge.POLL_TIMEOUT}s")
    print("=" * 60)

    sent_messages: list[dict] = []

    def capture_send(to: str, message: str) -> None:
        print(f"\n[MOCK TWILIO] → {to}")
        print(f"[MOCK TWILIO] Body:\n{message}")
        sent_messages.append({"to": to, "message": message})

    client = TestClient(bridge.app, raise_server_exceptions=True)

    with patch("band_whatsapp_bridge.send_whatsapp", side_effect=capture_send):
        print("\n[TEST] POST /webhook ...")
        resp = client.post(
            "/webhook",
            data={"From": FROM_NUMBER, "Body": DENGUE_CASE},
        )
        print(f"[TEST] HTTP {resp.status_code}: {resp.json()}")
        # Background task runs synchronously inside TestClient before returning

    print("\n" + "─" * 60)
    if not sent_messages:
        print("❌ FAIL — No WhatsApp message was sent (background task silent?)")
        sys.exit(1)

    final_msg = sent_messages[-1]["message"]
    print(f"Full response ({len(sent_messages)} message(s) sent):\n{final_msg}")

    if "Decision:" in final_msg:
        print("\n✅ PASS — 'Decision:' found in response")
    else:
        print("\n❌ FAIL — 'Decision:' not found in response")
        sys.exit(1)


if __name__ == "__main__":
    main()
