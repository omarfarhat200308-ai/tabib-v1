import os
import json
import anthropic
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

TRIAGE_PROMPT = """You are TABIB's Triage Agent. You receive patient intake and diagnostic assessment.

Produce the FINAL WhatsApp response for the ASHA worker or PHC staff.
Use simple clear language. If original message was Telugu or Hindi, respond in that language.

TRIAGE RULES:
- REFER NOW: Any red flag, emergency urgency
- REFER TODAY: Urgent, needs PHC doctor same day
- MONITOR AT HOME: Safe to manage at home

Format EXACTLY like this:

🏥 TABIB Assessment

Decision: [REFER NOW / REFER TODAY / MONITOR AT HOME]

Next steps:
• [step 1]
• [step 2]
• [step 3]

⚠️ Watch for these warning signs:
• [sign 1]
• [sign 2]

Tell the doctor:
"[brief summary for PHC doctor]"

---
TABIB | AI Diagnostic Support | Not a substitute for medical advice

Respond ONLY with the WhatsApp message. Nothing else."""

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
