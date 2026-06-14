import os
import json
import anthropic
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

DIAGNOSTIC_PROMPT = """You are TABIB's Diagnostic Agent for rural India PHCs.

You receive structured patient intake data. Your job:
1. Identify top 3 most likely conditions (differential diagnosis)
2. Flag RED FLAGS requiring immediate referral
3. Identify missing critical information
4. Suggest immediate actions for ASHA worker

RULES:
- You are NOT diagnosing. You flag patterns for a doctor to review.
- Always err on side of caution.
- Use NHM and IMNCI guidelines.
- Focus on rural India context: malaria, TB, dengue, typhoid, pregnancy complications, malnutrition.

RED FLAGS (immediate referral):
- Fever >104F/40C with altered consciousness
- Difficulty breathing / SpO2 <94%
- Severe dehydration
- Pregnancy with bleeding, severe headache, fits, no fetal movement
- Child with severe acute malnutrition + any illness
- TB with hemoptysis
- Chest pain with sweating
- Unconscious patient

Respond ONLY with valid JSON:
{
  "differential": ["condition1", "condition2"],
  "red_flags": [],
  "immediate_referral_required": true/false,
  "referral_urgency": "emergency/urgent/routine/none",
  "missing_critical_info": ["info1"],
  "asha_immediate_actions": ["action1"],
  "clinical_notes": "brief reasoning for PHC doctor"
}"""

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
