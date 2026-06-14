import os
import sys
import json
import anthropic
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from shared_prompts.prompts import DIAGNOSTIC_PROMPT

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

def run_diagnostic(intake_data: dict) -> dict:
    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1000,
        messages=[{"role": "user", "content": f"{DIAGNOSTIC_PROMPT}\n\nPatient data:\n{json.dumps(intake_data, indent=2)}"}]
    )
    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    diagnostic = json.loads(raw.strip())
    diagnostic["agent"] = "diagnostic"
    print(f"[DIAGNOSTIC] Done: {json.dumps(diagnostic, indent=2)}")
    return diagnostic
