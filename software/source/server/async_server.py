import asyncio
import traceback
import json
from fastapi import FastAPI, WebSocket, Header
from fastapi.responses import PlainTextResponse
from uvicorn import Config, Server
from interpreter import interpreter as base_interpreter
from .async_interpreter import AsyncInterpreter
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Dict, Any
from openai import OpenAI
from pydantic import BaseModel
import argparse
import os

# import sentry_sdk

base_interpreter.system_message = (
    "You are a helpful assistant that can answer questions and help with tasks."
)
base_interpreter.computer.import_computer_api = False
base_interpreter.llm.model = "groq/llama3-8b-8192"
base_interpreter.llm.api_key = os.environ["GROQ_API_KEY"]
base_interpreter.llm.supports_functions = False
base_interpreter.auto_run = True

os.environ["STT_RUNNER"] = "server"
os.environ["TTS_RUNNER"] = "server"

# Parse command line arguments for port number
"""
parser = argparse.ArgumentParser(description="FastAPI server.")
parser.add_argument("--port", type=int, default=8000, help="Port to run on.")
args = parser.parse_args()
"""
base_interpreter.tts = "coqui"


async def main(server_host, server_port):
    interpreter = AsyncInterpreter(base_interpreter)

    app = FastAPI()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],  # Allow all methods (GET, POST, etc.)
        allow_headers=["*"],  # Allow all headers
    )

    @app.get("/ping")
    async def ping():
        return PlainTextResponse("pong")

    @app.post("/load_chat")
    async def load_chat(messages: List[Dict[str, Any]]):
        interpreter.interpreter.messages = messages
        interpreter.active_chat_messages = messages
        print("🪼🪼🪼🪼🪼🪼 Messages loaded: ", interpreter.active_chat_messages)
        return {"status": "success"}

    @app.websocket("/")
    async def websocket_endpoint(websocket: WebSocket):
        await websocket.accept()
        try:

            async def receive_input():
                while True:
                    if websocket.client_state == "DISCONNECTED":
                        break

                    data = await websocket.receive()

                    if isinstance(data, bytes):
                        await interpreter.input(data)
                    elif "bytes" in data:
                        await interpreter.input(data["bytes"])
                        # print("RECEIVED INPUT", data)
                    elif "text" in data:
                        # print("RECEIVED INPUT", data)
                        await interpreter.input(data["text"])

            async def send_output():
                while True:
                    output = await interpreter.output()

                    if isinstance(output, bytes):
                        # print(f"Sending {len(output)} bytes of audio data.")
                        await websocket.send_bytes(output)
                        # we dont send out bytes rn, no TTS

                    elif isinstance(output, dict):
                        # print("sending text")
                        await websocket.send_text(json.dumps(output))

            await asyncio.gather(send_output(), receive_input())
        except Exception as e:
            print(f"WebSocket connection closed with exception: {e}")
            traceback.print_exc()
        finally:
            if not websocket.client_state == "DISCONNECTED":
                await websocket.close()

    print(f"Starting server on {server_host}:{server_port}")
    config = Config(app, host=server_host, port=server_port, lifespan="on")
    server = Server(config)
    await server.serve()

    class Rename(BaseModel):
        input: str

    @app.post("/rename-chat")
    async def rename_chat(body_content: Rename, x_api_key: str = Header(None)):
        print("RENAME CHAT REQUEST in PY 🌙🌙🌙🌙")
        input_value = body_content.input
        client = OpenAI(
            # defaults to os.environ.get("OPENAI_API_KEY")
            api_key=x_api_key,
        )
        try:
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {
                        "role": "user",
                        "content": f"Given the following chat snippet, create a unique and descriptive title in less than 8 words. Your answer must not be related to customer service.\n\n{input_value}",
                    }
                ],
                temperature=0.3,
                stream=False,
            )
            print(response)
            completion = response["choices"][0]["message"]["content"]
            return {"data": {"content": completion}}
        except Exception as e:
            print(f"Error: {e}")
            traceback.print_exc()
            return {"error": str(e)}


if __name__ == "__main__":
    asyncio.run(main())
