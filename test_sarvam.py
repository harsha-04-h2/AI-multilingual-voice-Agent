from sarvamai import SarvamAI
import os
from dotenv import load_dotenv

load_dotenv(".env.local")

client = SarvamAI(
    api_subscription_key=os.getenv("SARVAM_API_KEY")
)

print("Sarvam connected successfully")