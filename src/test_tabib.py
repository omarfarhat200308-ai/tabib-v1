from band_coordinator import run_tabib_pipeline

test_message = """
35 year old pregnant woman, 8 months pregnant.
High fever since 2 days, severe headache,
swelling on face and hands.
No fetal movement since morning.
"""

if __name__ == "__main__":
    print("\nTABIB TEST — High Risk Pregnancy Case")
    result = run_tabib_pipeline(test_message, patient_id="TEST001")
    print(f"\n{'='*50}\nFINAL WHATSAPP RESPONSE:\n{'='*50}\n{result}")
