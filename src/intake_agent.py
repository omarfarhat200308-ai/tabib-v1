import os
import sys
import anthropic
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from shared_prompts.prompts import INTAKE_PROMPT

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

def run_intake(whatsapp_message: str) -> dict:
    """Takes raw WhatsApp message, returns structured patient JSON."""
    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1000,
        messages=[
            {
                "role": "user",
                "content": f"{INTAKE_PROMPT}\n\nPatient message:\n{whatsapp_message}"
            }
        ]
    )
    
    import json
    raw = response.content[0].text.strip()
    # Strip markdown fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    
    structured = json.loads(raw.strip())
    structured["agent"] = "intake"
    structured["status"] = "complete"
    print(f"[INTAKE] Structured: {json.dumps(structured, indent=2)}")
    return structured
