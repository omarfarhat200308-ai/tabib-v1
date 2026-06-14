import os
import sys
import json
import anthropic
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from shared_prompts.prompts import TRIAGE_PROMPT

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

def run_triage(intake_data: dict, diagnostic_data: dict) -> str:
    combined = {"patient": intake_data, "diagnostic_assessment": diagnostic_data}
    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1000,
        messages=[{"role": "user", "content": f"{TRIAGE_PROMPT}\n\nCase data:\n{json.dumps(combined, indent=2)}"}]
    )
    result = response.content[0].text.strip()
    print(f"[TRIAGE] Done:\n{result}")
    return result
