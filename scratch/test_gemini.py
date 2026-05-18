import dotenv
import os
import asyncio
from livekit.plugins import google
from livekit.agents import llm

dotenv.load_dotenv(".env")

async def main():
    api_key = os.getenv("GEMINI_API_KEY")
    my_llm = google.LLM(api_key=api_key, model="gemini-2.5-flash")
    chat_ctx = llm.ChatContext()
    chat_ctx.add_message(content="Hello! Who are you?", role="user")
    
    async with my_llm.chat(chat_ctx=chat_ctx) as stream:
        async for chunk in stream:
            if chunk.delta and chunk.delta.content:
                print(chunk.delta.content, end="", flush=True)
    print()

if __name__ == "__main__":
    asyncio.run(main())
