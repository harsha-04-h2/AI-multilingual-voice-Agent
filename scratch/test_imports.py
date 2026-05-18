import os
import asyncio
from dotenv import load_dotenv
from livekit.plugins.openai import LLM
from livekit.agents.llm import ChatContext

# Load environment
load_dotenv(".env")

async def test_openai_fireworks():
    llm = LLM(
        model="accounts/fireworks/models/deepseek-v4-pro",
        base_url="https://api.fireworks.ai/inference/v1",
        api_key=os.getenv("FIREWORKS_API_KEY"),
    )
    
    ctx = ChatContext()
    ctx.add_message(role="user", content="Hello, tell me a 1-sentence welcome greeting.")
    
    stream = llm.chat(chat_ctx=ctx)
    async for chunk in stream:
        if chunk.delta and chunk.delta.content:
            print(chunk.delta.content, end="", flush=True)
    print()

asyncio.run(test_openai_fireworks())
