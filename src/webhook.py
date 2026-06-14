import os
from fastapi import FastAPI, Form
from twilio.rest import Client
from dotenv import load_dotenv
from band_coordinator import run_tabib_pipeline

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

app = FastAPI(title="TABIB Diagnostic API")

twilio_client = Client(
    os.getenv("TWILIO_ACCOUNT_SID"),
    os.getenv("TWILIO_AUTH_TOKEN")
)

TWILIO_FROM = os.getenv("TWILIO_WHATSAPP_FROM")

def send_whatsapp(to: str, message: str):
    twilio_client.messages.create(
        from_=TWILIO_FROM,
        to=to,
        body=message
    )

@app.post("/webhook")
async def whatsapp_webhook(
    Body: str = Form(...),
    From: str = Form(...)
):
    print(f"[WEBHOOK] From {From}: {Body}")
    send_whatsapp(From, "⏳ TABIB is analyzing... Please wait 30 seconds.")
    try:
        response = run_tabib_pipeline(
            whatsapp_message=Body,
            patient_id=From.replace("whatsapp:+", "")
        )
        send_whatsapp(From, response)
    except Exception as e:
        print(f"[WEBHOOK] Error: {e}")
        send_whatsapp(From, "⚠️ TABIB error. Please try again.")
    return {"status": "ok"}

@app.get("/")
def health():
    return {"status": "TABIB is running"}