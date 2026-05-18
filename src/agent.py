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

    Agent,

    AgentSession,

    JobContext,

    JobProcess,

    WorkerOptions,

    RoomInputOptions,

    cli,

    llm,

)

from livekit.plugins import (

    silero,

    deepgram,

    cartesia,

    google,

    noise_cancellation,

)



logger = logging.getLogger("agent")

logging.basicConfig(level=logging.INFO)



load_dotenv(".env")



# ─────────────────────────────────────────────

# Config — edit these to change behaviour

# ─────────────────────────────────────────────

CARTESIA_VOICE_ID       = "95d51f79-c397-46f9-b49a-23763d3eaa2d"  # Your custom Riya voice

CARTESIA_MODEL          = "sonic-2"                                 # sonic-2 = fastest; sonic-3 = best quality

SIP_TRUNK_ID            = os.getenv("SIP_TRUNK_ID", "")

SIP_DOMAIN              = os.getenv("SIP_DOMAIN", "")

DEFAULT_TRANSFER_NUMBER = os.getenv("DEFAULT_TRANSFER_NUMBER", "")

WEBHOOK_URL             = os.getenv("WEBHOOK_URL", "https://events-12managment.app.n8n.cloud/webhook/voice")



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

You are Riya, a warm premium receptionist at The Beginning, a luxury event venue in Bangalore.

You are speaking to customers on a live phone call.



Business Knowledge:

{KNOWLEDGE_BASE}



Personality:

You are warm, elegant, calm, welcoming, and conversational.

You sound like a real Bangalore hospitality receptionist.

Never robotic. Never scripted. Never overly formal.



Speaking Style:

- Speak naturally in Bangalore Indian English.

- Never sound British or American.

- Keep responses short and smooth.

- Sound human and premium.

- Keep conversations flowing naturally.



Use phrases naturally:

- "Sure sir"

- "Absolutely ma'am"

- "No worries"

- "Definitely sir"

- "That sounds lovely"

- "We'd be happy to help"



Conversation Rules:

- Keep responses under 15 words.

- Ask only ONE question at a time.

- Never overload the caller with information.

- Speak naturally like a real phone conversation.

- If caller hesitates, reassure them warmly.



Language Rules:

- Always reply in the same language as the caller.

- Support English, Hindi, Kannada, Telugu, and Hinglish naturally.



Your Goals:

1. Understand event requirements naturally.

2. Collect customer name naturally.

3. Collect customer email naturally.

4. Encourage venue visits naturally.

5. Make the venue sound luxurious and premium.



Name Collection Examples:

- "May I know your good name please?"

- "Who am I speaking with today?"



Email Collection Examples:

- "Could I get your email to share venue details?"

- "What email can we reach you on sir?"

- "Please spell your email slowly sir."

- "You can say it like: harsha two four two four at gmail dot com"

- "Let me confirm your email once properly sir."

Email Confirmation Rule:

- After the caller shares an email, repeat it back naturally and ask for confirmation.

- Do not treat the email as finalized until the caller confirms it is correct.



Venue Visit Promotion:

- "The venue looks even more beautiful in person."

- "Most families prefer visiting before finalizing."

- "We can arrange a complimentary venue visit for you."

- "Our team would personally walk you around."



Pricing Rule:

Never provide exact pricing.

If customer asks pricing: "Pricing depends on guest count and setup preferences sir."

Then encourage venue visit naturally.



Grounding Rule:

Only share information from the Business Knowledge above.

If asked something you don't know, say "I'll have our team get back to you with those details sir."



Safety Rule:

Politely decline any inappropriate, illegal, or harmful requests.



Critical Rules:

- Never say you are AI.

- Never confirm bookings.

- Never invent information.

- Never speak long paragraphs.

- Never sound robotic.

""")



INITIAL_GREETING = "Hi, welcome to The Beginning. I'm Riya. What celebration are you planning today?"



# ─────────────────────────────────────────────

# Lead Storage

# ─────────────────────────────────────────────

def fresh_lead():

    return {"name": "", "email": "", "pending_email": "", "email_confirmed": False, "transcript": []}



def extract_email(text: str):

    original_text = text
    text = f" {text.lower().strip()} "

    number_words = {
        "zero": "0",
        "one": "1",
        "two": "2",
        "three": "3",
        "four": "4",
        "five": "5",
        "six": "6",
        "seven": "7",
        "eight": "8",
        "nine": "9",
    }

    for word, digit in number_words.items():
        text = re.sub(rf"\b{word}\b", digit, text)

    text = re.sub(
        r"\b(no it is|no it's|my email is|my gmail is|email is|gmail is|it is|it's|that is)\b",
        " ",
        text,
    )

    replacements = {
        " at the rate ": " @ ",
        " at ": " @ ",
        " dot ": " . ",
        " period ": " . ",
        " underscore symbol ": " _ ",
        " underscore ": " _ ",
        " dash ": " - ",
        " hyphen ": " - ",
        " g mail ": "gmail",
        " gee mail ": "gmail",
        " gmail ": "gmail",
        " yahoo mail ": "yahoo",
        " hot mail ": "hotmail",
        " outlook mail ": "outlook",
        " space ": "",
    }

    for phrase, replacement in replacements.items():
        text = text.replace(phrase, replacement)

    text = text.strip().replace(" ", "")
    text = text.replace("gmailcom", "gmail.com")
    text = text.replace("yahoocom", "yahoo.com")
    text = text.replace("hotmailcom", "hotmail.com")
    text = text.replace("outlookcom", "outlook.com")

    logger.info(f"Processed email text: {text}")

    match = re.search(
        r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
        text,
    )

    if match:
        email = match.group(0)
        logger.info(f"Extracted email: {email}")
        return email

    logger.warning(f"Could not extract email from: {original_text}")
    return None






def is_email_confirmation(text: str):
    text_lower = text.lower()
    def has_phrase(phrase: str):
        return re.search(rf"\b{re.escape(phrase)}\b", text_lower) is not None

    rejection_phrases = [
        "no",
        "wrong",
        "incorrect",
        "not correct",
        "change",
    ]
    confirmation_phrases = [
        "yes",
        "yeah",
        "correct",
        "that's correct",
        "that is correct",
        "right",
        "perfect",
        "ok",
        "okay",
        "confirmed",
    ]

    if any(has_phrase(phrase) for phrase in rejection_phrases):
        return False

    if any(has_phrase(phrase) for phrase in confirmation_phrases):
        return True

    return None


def extract_name(text: str):

    text_lower = text.lower()

    ignore_phrases = ["planning", "marriage", "wedding", "birthday", "event", "reception", "engagement"]

    patterns = [

        r"my name is ([a-zA-Z\s]+)",

        r"i am ([a-zA-Z\s]+)",

        r"this is ([a-zA-Z\s]+)",

        r"i'm ([a-zA-Z\s]+)",

        r"call me ([a-zA-Z\s]+)",

        r"myself ([a-zA-Z\s]+)",

    ]

    for pattern in patterns:

        match = re.search(pattern, text_lower)

        if match:

            name = match.group(1).strip()

            if any(word == name_word for word in ignore_phrases for name_word in name.split()):

                return None

            if len(name.split()) > 3:

                return None

            return name.title()

    return None



def send_webhook(lead: dict, phone_number=None, room_name=None):

    try:

        payload = {

            "name": lead["name"],

            "email": lead["email"],

            "phone_number": phone_number,

            "room_name": room_name,

            "transcript": lead["transcript"],

        }

        response = requests.post(

            WEBHOOK_URL,

            json=payload,

            timeout=10,

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



# ─────────────────────────────────────────────

# Assistant

# ─────────────────────────────────────────────

class Assistant(Agent):

    def __init__(self, tools: list | None = None) -> None:

        super().__init__(

            instructions=INSTRUCTIONS,

            tools=tools or [],

        )



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

    #   STT  → Deepgram Nova-3 (en-IN for Indian accent handling)

    #   LLM  → Gemini 2.5 Flash (fast + cheap)

    #   TTS  → Cartesia Sonic-2 (your custom Riya voice, ~90ms latency)

    #   VAD  → Silero (prewarmed — no cold-start delay)



    session = AgentSession(

        vad=ctx.proc.userdata["vad"],                   # prewarmed, no delay

        stt=deepgram.STT(

            model="nova-3",

            language="en-IN",                           # handles Indian accents + Hinglish

            smart_format=True,

        ),

        llm=google.LLM(

            model="gemini-2.5-flash",

            api_key=os.getenv("GEMINI_API_KEY"),

            temperature=0.7,

        ),

        tts=cartesia.TTS(

            model=CARTESIA_MODEL,

            voice=CARTESIA_VOICE_ID,

            language="en",

            speed=0.95,                                 # slightly slower = more premium feel

        ),

    )



    # ── Capture user speech for lead data + transcript ──

    @session.on("user_input_transcribed")

    def on_user_input(msg):

        text = msg.transcript

        logger.info(f"User said: {text}")



        # Append to transcript

        lead["transcript"].append({"role": "user", "text": text})



        # Extract name if not yet captured

        if not lead["name"]:

            name = extract_name(text)

            if name:

                lead["name"] = name

                logger.info(f"Captured name: {name}")



        # Extract and confirm email before finalizing the lead

        if not lead["email_confirmed"]:

            email_confirmation = None
            if lead["pending_email"]:
                email_confirmation = is_email_confirmation(text)

            if email_confirmation is True:

                lead["email"] = lead["pending_email"]

                lead["email_confirmed"] = True

                logger.info(f"Confirmed email: {lead['email']}")

            elif email_confirmation is False:

                logger.info(f"Email rejected by caller: {lead['pending_email']}")

                lead["pending_email"] = ""

                corrected_email = extract_email(text)

                if corrected_email:

                    lead["pending_email"] = corrected_email

                    logger.info(f"Captured corrected pending email: {corrected_email}")

            else:

                email = extract_email(text)

                if email:

                    lead["pending_email"] = email

                    logger.info(f"Captured pending email for confirmation: {email}")



    # ── Capture Riya's responses for transcript ──

    @session.on("agent_speech_committed")

    def on_agent_speech(msg):

        lead["transcript"].append({"role": "agent", "text": msg.transcript})

        logger.info(f"Riya said: {msg.transcript}")



    # ── Send final webhook when caller disconnects ──

    @ctx.room.on("participant_disconnected")

    def on_participant_disconnected(participant):

        logger.info("Caller disconnected — sending final webhook.")

        async def _send():

            await asyncio.to_thread(

                send_webhook,

                lead,

                phone_number,

                ctx.room.name,

            )

        asyncio.create_task(_send())



    # ── Connect to room BEFORE starting the session ──

    await ctx.connect()



    # ── Start session ────────────────────────────

    await session.start(

        room=ctx.room,

        agent=Assistant(tools=list(fnc_ctx.function_tools.values())),

        room_input_options=RoomInputOptions(

            noise_cancellation=noise_cancellation.BVCTelephony(),

        ),

    )



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

            await session.say(INITIAL_GREETING, allow_interruptions=True)



        except Exception as e:

            logger.error(f"Outbound call failed: {e}")

            await ctx.shutdown()

    else:

        # Inbound or dashboard-dispatched call — greet immediately

        await session.say(INITIAL_GREETING, allow_interruptions=True)



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
