# This is a websocket interpreter, TTS and STT disabled.
# It makes a websocket on port 8000 that sends/recieves LMC messages in *streaming* format.

### You MUST send a start and end flag with each message! For example: ###

"""
{"role": "user", "type": "message", "start": True})
{"role": "user", "type": "message", "content": "hi"})
{"role": "user", "type": "message", "end": True})
"""

###

from pynput import keyboard
from RealtimeTTS import (
    TextToAudioStream,
    OpenAIEngine,
    CoquiEngine,
    ElevenlabsEngine,
    SystemEngine,
    GTTSEngine,
)
from RealtimeSTT import AudioToTextRecorder
import time
import asyncio
import json
import os


class AsyncInterpreter:
    def __init__(self, interpreter):
        self.stt_latency = None
        self.tts_latency = None
        self.interpreter_latency = None
        self.interpreter = interpreter

        # STT
        self.stt = AudioToTextRecorder(
            model="tiny.en", spinner=False, use_microphone=False
        )
        self.stt.stop()

        # TTS
        if self.interpreter.tts == "coqui":
            engine = CoquiEngine()
        elif self.interpreter.tts == "openai":
            engine = OpenAIEngine()
        elif self.interpreter.tts == "gtts":
            engine = GTTSEngine()
        elif self.interpreter.tts == "elevenlabs":
            engine = ElevenlabsEngine(api_key=os.environ["ELEVEN_LABS_API_KEY"])
        elif self.interpreter.tts == "system":
            engine = SystemEngine()
        else:
            raise ValueError(f"Unsupported TTS engine: {self.interpreter.tts}")
        self.tts = TextToAudioStream(engine)

        self.active_chat_messages = []

        self._input_queue = asyncio.Queue()  # Queue that .input will shove things into
        self._output_queue = asyncio.Queue()  # Queue to put output chunks into
        self._last_lmc_start_flag = None  # Unix time of last LMC start flag recieved
        self._in_keyboard_write_block = (
            False  # Tracks whether interpreter is trying to use the keyboard
        )
        self.loop = asyncio.get_event_loop()

    async def _add_to_queue(self, queue, item):
        await queue.put(item)

    async def clear_queue(self, queue):
        while not queue.empty():
            await queue.get()

    async def clear_input_queue(self):
        await self.clear_queue(self._input_queue)

    async def clear_output_queue(self):
        await self.clear_queue(self._output_queue)

    async def input(self, chunk):
        """
        Expects a chunk in streaming LMC format.
        """
        if isinstance(chunk, bytes):
            # It's probably a chunk of audio
            self.stt.feed_audio(chunk)
            # print("INTERPRETER FEEDING AUDIO")

        else:

            try:
                chunk = json.loads(chunk)
            except:
                pass

            if "start" in chunk:
                # print("Starting STT")
                self.stt.start()
                self._last_lmc_start_flag = time.time()
                # self.interpreter.computer.terminal.stop() # Stop any code execution... maybe we should make interpreter.stop()?
            elif "end" in chunk:
                # print("Running OI on input")
                asyncio.create_task(self.run())
            else:
                await self._add_to_queue(self._input_queue, chunk)

    def add_to_output_queue_sync(self, chunk):
        """
        Synchronous function to add a chunk to the output queue.
        """
        # print("ADDING TO QUEUE:", chunk)
        asyncio.create_task(self._add_to_queue(self._output_queue, chunk))

    async def run(self):
        """
        Runs OI on the audio bytes submitted to the input. Will add streaming LMC chunks to the _output_queue.
        """
        self.interpreter.messages = self.active_chat_messages

        self.stt.stop()
        # message = self.stt.text()
        # print("THE MESSAGE:", message)

        # accumulates the input queue message
        input_queue = []
        while not self._input_queue.empty():
            input_queue.append(self._input_queue.get())

        # print("INPUT QUEUE:", input_queue)
        # message = [i for i in input_queue if i["type"] == "message"][0]["content"]
        start_stt = time.time()
        message = self.stt.text()
        end_stt = time.time()
        self.stt_latency = end_stt - start_stt
        print("STT LATENCY", self.stt_latency)

        def generate(message):
            last_lmc_start_flag = self._last_lmc_start_flag
            self.interpreter.messages = self.active_chat_messages
            print("message is", message)

            for chunk in self.interpreter.chat(message, display=True, stream=True):

                if self._last_lmc_start_flag != last_lmc_start_flag:
                    # self.beeper.stop()
                    break

                # self.add_to_output_queue_sync(chunk) # To send text, not just audio

                content = chunk.get("content")

                # Handle message blocks
                if chunk.get("type") == "message":
                    if content:
                        # self.beeper.stop()

                        # Experimental: The AI voice sounds better with replacements like these, but it should happen at the TTS layer
                        # content = content.replace(". ", ". ... ").replace(", ", ", ... ").replace("!", "! ... ").replace("?", "? ... ")
                        # print("yielding this", content)
                        yield content

                # Handle code blocks
                elif chunk.get("type") == "code":
                    if "start" in chunk:
                        # self.beeper.start()
                        pass

                    # Experimental: If the AI wants to type, we should type immediatly
                    if (
                        self.interpreter.messages[-1]
                        .get("content", "")
                        .startswith("computer.keyboard.write(")
                    ):
                        keyboard.controller.type(content)
                        self._in_keyboard_write_block = True
                    if "end" in chunk and self._in_keyboard_write_block:
                        self._in_keyboard_write_block = False
                        # (This will make it so it doesn't type twice when the block executes)
                        if self.interpreter.messages[-1]["content"].startswith(
                            "computer.keyboard.write("
                        ):
                            self.interpreter.messages[-1]["content"] = (
                                "dummy_variable = ("
                                + self.interpreter.messages[-1]["content"][
                                    len("computer.keyboard.write(") :
                                ]
                            )

            # Send a completion signal
            end_interpreter = time.time()
            self.interpreter_latency = end_interpreter - start_interpreter
            print("INTERPRETER LATENCY", self.interpreter_latency)
            # self.add_to_output_queue_sync({"role": "server","type": "completion", "content": "DONE"})

        # Feed generate to RealtimeTTS
        self.add_to_output_queue_sync(
            {"role": "assistant", "type": "audio", "format": "bytes.wav", "start": True}
        )
        start_interpreter = time.time()
        text_iterator = generate(message)

        self.tts.feed(text_iterator)
        self.tts.play_async(on_audio_chunk=self.on_tts_chunk, muted=False)

        while True:
            if self.tts.is_playing():
                start_tts = time.time()

                break
            await asyncio.sleep(0.1)
        while True:
            await asyncio.sleep(0.1)
            # print("is_playing", self.tts.is_playing())
            if not self.tts.is_playing():
                self.add_to_output_queue_sync(
                    {
                        "role": "assistant",
                        "type": "audio",
                        "format": "bytes.wav",
                        "end": True,
                    }
                )
                end_tts = time.time()
                self.tts_latency = end_tts - start_tts
                print("TTS LATENCY", self.tts_latency)
                self.tts.stop()
                break

    async def _on_tts_chunk_async(self, chunk):
        print(f"Adding chunk to output queue")
        await self._add_to_queue(self._output_queue, chunk)

    def on_tts_chunk(self, chunk):
        # print("ye")
        asyncio.run(self._on_tts_chunk_async(chunk))

    async def output(self):
        print("entering output method")
        value = await self._output_queue.get()
        print("output method returning")
        return value
