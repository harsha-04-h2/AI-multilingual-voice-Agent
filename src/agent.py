import logging
import textwrap
import asyncio
import requests
import re
import os
import json

from dotenv import load_dotenv

from livekit import api
from livekit.agents import (
    JobContext,
    JobProcess,
    WorkerOptions,
    RoomInputOptions,
    cli,
    llm,
)
from livekit.agents.voice import Agent as VoiceAgent
from livekit.plugins import (
    silero,
    google,
    noise_cancellation,
)

logger = logging.getLogger("agent")
logging.basicConfig(level=logging.INFO)

load_dotenv(".env")

# ─────────────────────────────────────────────
# Config — edit these to change behaviour
# ─────────────────────────────────────────────
SIP_TRUNK_ID            = "b72420b9-3c6d-4300-bdb8-80c021d63f08"
SIP_DOMAIN              = os.getenv("SIP_DOMAIN", "")
DEFAULT_TRANSFER_NUMBER = os.getenv("DEFAULT_TRANSFER_NUMBER", "")
WEBHOOK_URL             = "https://n8n-i5hg.srv1696036.hstgr.cloud/webhook/voiceai"

# ─────────────────────────────────────────────
# Knowledge Base
# ─────────────────────────────────────────────
try:
    with open("knowledgebase.md", "r", encoding="utf-8") as f:
        KNOWLEDGE_BASE = f.read()
except FileNotFoundError:
    KNOWLEDGE_BASE = ""
    logger.warning("knowledgebase.md not found.")

# ─────────────────────────────────────────────
# System Prompt
# ─────────────────────────────────────────────
INSTRUCTIONS = textwrap.dedent(f"""
You are Riya, a warm and premium receptionist at The Beginning, a luxury 
boutique wedding resort in Bangalore.
You are speaking to customers on a live phone call.

Business Knowledge:
{KNOWLEDGE_BASE}

PERSONALITY:
You are warm, elegant, calm, and genuinely excited about this venue.
You sound like a real Bangalore hospitality professional — not a robot.
React differently every time. Surprise callers with warmth and spontaneity.

SPEAKING STYLE:
- Natural Indian English — Bangalore flavour. Never British or American.
- Use Indian expressions freely — "arey", "yaar", "na", "only", "itself"
- Short responses — under 20 words per turn
- One thought at a time. Never dump information.
- Sound genuinely in love with this venue

CONVERSATION FLOW — FOLLOW THIS ORDER STRICTLY:

Step 1 — GREET
Greet warmly and ask for their name immediately.

Step 2 — COLLECT PHONE NUMBER
As soon as you have their name, ask for their WhatsApp number.
Frame it as wanting to send them details on WhatsApp.
Only accept 10-digit Indian mobile numbers starting with 6, 7, 8, or 9.

Step 3 — UNDERSTAND THEIR NEED
Ask what they are planning — wedding, birthday, corporate, etc.

Step 4 — ANSWER THEIR QUESTIONS
Answer all FAQs using ONLY the Business Knowledge section.
Frame answers in your own warm natural words — never read out a list.
Keep each answer to 1–2 sentences max.

ANSWERING RULES:
- Capacity: give numbers warmly with excitement
- Spaces: describe briefly and make it sound magical
- Catering/decoration: confirm yes, details during visit
- Pricing: NEVER give any number — say pricing depends on guest count 
  and setup, then push for visit
- Rooms: 15 plush rooms, 7-star bridal suite, ~70 guests total
- Parking: 300+ cars, completely free
- Location: Near Sri Sri Ravishankar Vidya Mandir, Mullur, close to Wipro

Step 5 — PUSH FOR SITE VISIT
At a natural moment push for a complimentary on-site visit.
"Honestly the venue looks even more stunning in person — photos don't 
do it justice at all yaar"
"Most families who come just fall in love immediately"
"Completely free, zero commitment — just come have a look!"

Step 6 — WARM CLOSE
Once questions are answered:
- Confirm WhatsApp follow-up
- One final site visit push
- Warm enthusiastic goodbye
- Call end_call immediately after goodbye

GUARDRAILS:
- PRICING: Never give any number, range, or estimate. Ever.
- WHATSAPP: Always mention WhatsApp as the follow-up channel only.
- SITE VISIT: Every call must end with a site visit push.
- KNOWLEDGE: Only answer from Business Knowledge. Never invent facts.
- BOOKING: Never confirm any booking on the call.
- IDENTITY: Never say you are AI. If asked: "I'm Riya from The Beginning!"
- LANGUAGE: Always reply in the caller's language — English, Hindi, 
  Kannada, Telugu, Hinglish all supported.

CALL ENDING RULE:
Only end after: name collected + phone collected + questions answered + 
WhatsApp mentioned + site visit pushed + warm goodbye delivered.
Call end_call immediately after goodbye.
""")

INITIAL_GREETING = "Hi, welcome to The Beginning! I'm Riya. May I know your good name please?"

# ─────────────────────────────────────────────
# Lead Storage
# ─────────────────────────────────────────────
def fresh_lead():
    return {"name": "", "phone": "", "sent": False}

def text_to_digits(text: str) -> str:
    # Lowercase and clean punctuation
    cleaned_text = re.sub(r'[^\w\s]', ' ', text.lower())
    words = cleaned_text.split()
    
    word_map = {
        "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
        "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
        "oh": "0", "o": "0",
        "ek": "1", "do": "2", "teen": "3", "chaar": "4", "char": "4",
        "paanch": "5", "panch": "5", "chhe": "6", "che": "6", "saat": "7",
        "aath": "8", "ath": "8", "nau": "9", "shunya": "0", "shoonya": "0"
    }
    
    digits = []
    i = 0
    while i < len(words):
        word = words[i]
        
        if word == "double":
            if i + 1 < len(words):
                next_word = words[i+1]
                next_digit = word_map.get(next_word, next_word)
                if next_digit.isdigit() and len(next_digit) == 1:
                    digits.append(next_digit * 2)
                    i += 2
                    continue
            digits.append("double")
            i += 1
        elif word == "triple":
            if i + 1 < len(words):
                next_word = words[i+1]
                next_digit = word_map.get(next_word, next_word)
                if next_digit.isdigit() and len(next_digit) == 1:
                    digits.append(next_digit * 3)
                    i += 2
                    continue
            digits.append("triple")
            i += 1
        elif word in word_map:
            digits.append(word_map[word])
            i += 1
        elif word.isdigit():
            digits.append(word)
            i += 1
        else:
            sub_digits = "".join(c for c in word if c.isdigit())
            if sub_digits:
                digits.append(sub_digits)
            i += 1
            
    return "".join(digits)

def format_whatsapp_number(phone: str) -> str:
    if not phone:
        return ""
    # Strips any +, spaces, dashes, or brackets from the input
    cleaned = re.sub(r'[\+\s\-\(\)\[\]]', '', phone)
    
    # If the number starts with +91 or 91, remove it cleanly
    if cleaned.startswith("91") and len(cleaned) > 10:
        cleaned = cleaned[2:]
        
    # If the number is just 10 digits, prepend 91
    return "91" + cleaned

async def extract_lead_with_ai(transcript_history: list) -> dict:
    """Uses Gemini to extract name and phone from full conversation history"""
    try:
        conversation_text = "\n".join(transcript_history[-20:])  # last 20 messages

        import google.generativeai as genai
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        model = genai.GenerativeModel("gemini-1.5-flash")

        prompt = f"""Extract the customer's name and phone number from this call transcript.

Transcript:
{conversation_text}

Rules:
- Only extract what the CUSTOMER said, not the agent
- Phone must be a 10 digit Indian mobile number starting with 6, 7, 8, or 9
- Name should be the customer's actual name only
- If not found return empty string

Respond ONLY in this exact JSON format with no other text:
{{"name": "extracted name or empty string", "phone": "10 digit number or empty string"}}"""

        response = model.generate_content(prompt)
        text = response.text.strip().replace("```json", "").replace("```", "").strip()
        data = json.loads(text)
        return {
            "name": data.get("name", "").strip().title(),
            "phone": format_whatsapp_number(data.get("phone", "")) if data.get("phone") else ""
        }
    except Exception as e:
        logger.error(f"AI extraction failed: {e}")
        return {"name": "", "phone": ""}

def send_webhook(lead: dict):
    try:
        formatted_phone = format_whatsapp_number(lead["phone"])
        response = requests.post(
            WEBHOOK_URL,
            json={"name": lead["name"], "phone": formatted_phone},
            timeout=5,
        )
        logger.info(f"Webhook sent: {response.status_code}")
        return True
    except Exception as e:
        logger.error(f"Webhook failed: {e}")
        return False

# ─────────────────────────────────────────────
# Call Transfer Tool
# ─────────────────────────────────────────────
class TransferFunctions(llm.ToolContext):
    def __init__(self, ctx: JobContext, phone_number: str = None):
        super().__init__(tools=[])
        self.ctx = ctx
        self.phone_number = phone_number

    @llm.function_tool(description="Transfer the call to a human agent or another number.")
    async def transfer_call(self, destination: str = None):
        if not destination:
            destination = DEFAULT_TRANSFER_NUMBER
            if not destination:
                return "Error: No default transfer number configured."

        if "@" not in destination:
            if SIP_DOMAIN:
                clean = destination.replace("tel:", "").replace("sip:", "")
                destination = f"sip:{clean}@{SIP_DOMAIN}"
            else:
                if not destination.startswith(("tel:", "sip:")):
                    destination = f"tel:{destination}"
        elif not destination.startswith("sip:"):
            destination = f"sip:{destination}"

        participant_identity = None
        if self.phone_number:
            participant_identity = f"sip_{self.phone_number}"
        else:
            for p in self.ctx.room.remote_participants.values():
                participant_identity = p.identity
                break

        if not participant_identity:
            return "Failed to transfer: could not identify the caller."

        try:
            await self.ctx.api.sip.transfer_sip_participant(
                api.TransferSIPParticipantRequest(
                    room_name=self.ctx.room.name,
                    participant_identity=participant_identity,
                    transfer_to=destination,
                    play_dialtone=False,
                )
            )
            logger.info(f"Call transferred to {destination}")
            return "Transfer initiated successfully."
        except Exception as e:
            logger.error(f"Transfer failed: {e}")
            return f"Error executing transfer: {e}"

    @llm.function_tool(description="End the call and hang up after the conversation is complete.")
    async def end_call(self):
        logger.info("Agent ending the call.")
        try:
            participant_identity = None
            if self.phone_number:
                participant_identity = f"sip_{self.phone_number}"
            else:
                for p in self.ctx.room.remote_participants.values():
                    participant_identity = p.identity
                    break

            if participant_identity:
                await self.ctx.api.sip.transfer_sip_participant(
                    api.TransferSIPParticipantRequest(
                        room_name=self.ctx.room.name,
                        participant_identity=participant_identity,
                        transfer_to="tel:+0",  # invalid destination forces a clean hangup
                        play_dialtone=False,
                    )
                )
        except Exception:
            pass  # hang up attempt done — disconnect room either way
        finally:
            try:
                self.ctx.shutdown()  # not a coroutine in livekit-agents 1.x
            except Exception:
                pass
        return "Call ended."

# ─────────────────────────────────────────────
# Prewarm — runs BEFORE calls come in
# ─────────────────────────────────────────────
def prewarm(proc: JobProcess):
    logger.info("Prewarming Silero VAD...")
    proc.userdata["vad"] = silero.VAD.load()
    logger.info("Silero VAD ready")

# ─────────────────────────────────────────────
# Main Entrypoint
# ─────────────────────────────────────────────
async def entrypoint(ctx: JobContext):
    logger.info(f"Connecting to room: {ctx.room.name}")

    # ── Parse metadata ──────────────────────────
    phone_number = None
    config_dict  = {}

    try:
        if ctx.job.metadata:
            data = json.loads(ctx.job.metadata)
            phone_number = data.get("phone_number")
            config_dict  = data
    except Exception:
        pass

    try:
        if ctx.room.metadata:
            data = json.loads(ctx.room.metadata)
            if data.get("phone_number"):
                phone_number = data["phone_number"]
            config_dict.update(data)
    except Exception:
        logger.warning("No valid JSON metadata in room.")

    # ── Lead tracking ───────────────────────────
    lead = fresh_lead()

    # ── Tools ───────────────────────────────────
    fnc_ctx = TransferFunctions(ctx, phone_number)

    # ── Pipeline ────────────────────────────────
    session = VoiceAgent(
        instructions=INSTRUCTIONS,
        llm=google.beta.realtime.RealtimeModel(
            model="gemini-2.0-flash-exp",
            voice="Aoede",
            instructions=INSTRUCTIONS,
            api_key=os.getenv("GEMINI_API_KEY"),
        ),
        vad=ctx.proc.userdata["vad"],
        tools=[fnc_ctx],
    )

    # ── Capture speech for lead data ────────────
    transcript_history = []

    @session.on("user_input_transcribed")
    def on_user_input(msg):
        text = msg.transcript

        # Only process final (completed) transcripts, not interim partials
        if not getattr(msg, 'is_final', True):
            return

        logger.info(f"User said (final): {text}")

        # Build conversation history
        transcript_history.append(f"Customer: {text}")

        # Only run extraction if we still need name or phone
        if lead["name"] and lead["phone"]:
            return
        if lead["sent"]:
            return

        async def _extract_and_send():
            # extract_lead_with_ai is async — call directly with await, NOT asyncio.to_thread
            result = await extract_lead_with_ai(list(transcript_history))

            if result["name"] and not lead["name"]:
                lead["name"] = result["name"]
                logger.info(f"AI captured name: {{lead['name']}}")

            if result["phone"] and not lead["phone"]:
                lead["phone"] = result["phone"]
                logger.info(f"AI captured phone: {{lead['phone']}}")

            if lead["name"] and lead["phone"] and not lead["sent"]:
                lead["sent"] = True
                logger.info(f"Sending webhook — Name: {{lead['name']}} Phone: {{lead['phone']}}")

                # send_webhook is sync/blocking — asyncio.to_thread is correct here
                success = await asyncio.to_thread(send_webhook, lead)
                if not success:
                    lead["sent"] = False
                    return

                await asyncio.sleep(2)

                await session.say(
                    f"Arey {{lead['name']}}, it was such a pleasure talking to you yaar! "
                    f"We will send you the site visit invite on WhatsApp right away. "
                    f"Cannot wait to see you and your family at The Beginning — it is going to be absolutely stunning! "
                    f"Have a beautiful day ahead — speak soon, bye bye!",
                    allow_interruptions=False,
                )

                await asyncio.sleep(2)
                await fnc_ctx.end_call()

        asyncio.create_task(_extract_and_send())

    # ── Send webhook when caller disconnects if not already sent ──
    @ctx.room.on("participant_disconnected")
    def on_participant_disconnected(participant):
        logger.info("Caller disconnected — sending final webhook.")
        if (lead["name"] or lead["phone"]) and not lead["sent"]:
            lead["sent"] = True
            async def _send():
                try:
                    success = await asyncio.to_thread(send_webhook, lead)
                    logger.info(f"Final webhook send status: {success}")
                except Exception as e:
                    logger.error(f"Final webhook failed: {e}")
            asyncio.create_task(_send())

    # FIX: connect to room BEFORE starting the session
    await ctx.connect()

    # ── Start session ────────────────────────────
    await session.start(ctx.room)

    # ── Outbound dialing logic ───────────────────
    should_dial = False
    if phone_number:
        user_already_here = any(
            f"sip_{phone_number}" in p.identity or "sip_" in p.identity
            for p in ctx.room.remote_participants.values()
        )
        if not user_already_here:
            should_dial = True
            logger.info("User not in room — initiating dial-out.")
        else:
            logger.info("User already in room — skipping dial.")

    if should_dial:
        try:
            logger.info(f"Dialing {phone_number}...")
            await ctx.api.sip.create_sip_participant(
                api.CreateSIPParticipantRequest(
                    room_name=ctx.room.name,
                    sip_trunk_id=SIP_TRUNK_ID,
                    sip_call_to=phone_number,
                    participant_identity=f"sip_{phone_number}",
                    wait_until_answered=True,
                )
            )
            logger.info("Call answered — greeting caller.")
            try:
                await session.say(INITIAL_GREETING, allow_interruptions=True)
            except AttributeError:
                pass # MultimodalAgent might not support say

        except Exception as e:
            logger.error(f"Outbound call failed: {e}")
            await ctx.shutdown()  # FIX: added missing await
    else:
        # Inbound or dashboard-dispatched call — greet immediately
        try:
            await session.say(INITIAL_GREETING, allow_interruptions=True)
        except AttributeError:
            pass

    logger.info("Riya is live and listening.")

# ─────────────────────────────────────────────
# Run
# ─────────────────────────────────────────────
if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
            agent_name="my-agent",
            num_idle_processes=1,
        )
    )