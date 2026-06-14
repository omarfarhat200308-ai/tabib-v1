import os
import anthropic
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

INTAKE_PROMPT = """You are TABIB's Intake Agent. You receive raw WhatsApp messages from ASHA workers or PHC staff describing a patient's condition in plain language (Hindi, Telugu, or English).

Your job is to extract and structure the information into a clean JSON object.

Extract:
- patient_age (number or null)
- patient_sex (male/female/unknown)
- symptoms (list of strings)
- duration (how long symptoms have been present, string)
- vitals (any mentioned: temperature, BP, pulse, SpO2 — as a dict, null if none)
- pregnancy_status (yes/no/unknown)
- known_conditions (list of any mentioned existing conditions)
- raw_message (the original message)
- language_detected (english/hindi/telugu/mixed)

If information is missing, use null. Do not guess or infer beyond what is stated.

Respond ONLY with valid JSON. No explanation, no preamble."""

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
