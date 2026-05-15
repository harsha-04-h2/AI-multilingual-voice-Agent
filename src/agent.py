import logging
import textwrap
from dotenv import load_dotenv

from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    WorkerOptions,
    cli,
    inference,
    room_io,
)

from livekit.plugins import (
    ai_coustics,
    silero,
)

logger = logging.getLogger("agent")
logging.basicConfig(level=logging.INFO)

# Load environment variables
load_dotenv(".env")

# Load knowledge base
with open("knowledgebase.md", "r", encoding="utf-8") as f:
    KNOWLEDGE_BASE = f.read()


class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(
            llm=inference.LLM(
                model="openai/gpt-4o-mini"
            ),
            instructions=textwrap.dedent(
                f"""
You are a helpful AI voice receptionist for ClickWave AI.

Use the following business knowledge while answering users:

{KNOWLEDGE_BASE}

Rules:
- Always reply in the SAME language the user speaks.
- If the user speaks Telugu, reply in Telugu.
- If the user speaks Kannada, reply in Kannada.
- If the user speaks Hindi, reply in Hindi.
- If the user speaks English, reply in English.
- If the user speaks Hinglish, reply naturally in Hinglish.

Behavior:
- Keep responses short and conversational.
- Speak naturally like a human receptionist.
- Be friendly, confident, and helpful.
- Do not use markdown or bullet points in responses.

Voice Style:
- Sound natural and warm.
- Avoid robotic responses.
- Keep replies concise for voice conversations.
"""
            ),
        )


server = AgentServer()


def prewarm(proc: JobProcess):
    logger.info("Loading Silero VAD...")
    proc.userdata["vad"] = silero.VAD.load()
    logger.info("Silero VAD loaded")


server.setup_fnc = prewarm


@server.rtc_session(agent_name="my-agent")
async def my_agent(ctx: JobContext):

    logger.info("NEW AGENT SESSION")

    await ctx.connect()

    stt = inference.STT(
        model="deepgram/nova-3",
        language="multi"
    )

    tts = inference.TTS(
        model="cartesia/sonic-3",
        voice="9626c31c-bec5-4cca-baa8-f8ba9e84c8bc"
    )

    session = AgentSession(
        stt=stt,
        tts=tts,
        vad=silero.VAD.load(),
        preemptive_generation=True,
    )

    await session.start(
        agent=Assistant(),
        room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=ai_coustics.audio_enhancement(
                    model=ai_coustics.EnhancerModel.QUAIL_VF_S
                ),
            ),
        ),
    )

    await session.say(
        "Hello, I am connected and working.",
        allow_interruptions=True,
    )

    logger.info("Agent is active")


worker_options = WorkerOptions(
    entrypoint_fnc=my_agent,
    agent_name="my-agent",
)


if __name__ == "__main__":
    cli.run_app(worker_options)

