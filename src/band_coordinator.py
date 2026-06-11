import os
import json
import time
from dotenv import load_dotenv
from intake_agent import run_intake
from diagnostic_agent import run_diagnostic
from triage_agent import run_triage

load_dotenv()

def run_tabib_pipeline(whatsapp_message: str, patient_id: str = None) -> str:
    if not patient_id:
        patient_id = str(int(time.time()))
    print(f"\n{'='*50}\n[TABIB] Case {patient_id}\n{'='*50}")

    print("[TABIB] Step 1: Intake Agent")
    intake = run_intake(whatsapp_message)

    print("[TABIB] Step 2: Diagnostic Agent")
    diagnostic = run_diagnostic(intake)

    print("[TABIB] Step 3: Triage Agent")
    response = run_triage(intake, diagnostic)

    print(f"[TABIB] Pipeline complete")
    return response
