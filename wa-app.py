import logging
import os
from dataclasses import dataclass
from typing import Optional, List
from pathlib import Path
import tempfile
import asyncio
import anthropic
from heyoo import WhatsApp
from pymongo import MongoClient
from datetime import datetime
from gtts import gTTS
from pydub import AudioSegment
from transformers import pipeline
from transformers.utils.import_utils import shutil
from functools import lru_cache


@dataclass
class BotConfig:
    name: str
    personality: str
    whatsapp_token: str
    whatsapp_number_id: str
    anthropic_key: str
    mongodb_uri: str
    verify_token: str
    message_history_limit: int = 10
    speech_speed: float = 1.3

    @classmethod
    def from_env(cls) -> 'BotConfig':
        return cls(
            name=os.getenv("BOT_NAME", "Sophie"),
            personality=os.getenv("BOT_PERSONALITY", "You are Sophie, a friendly and helpful assistant."),
            whatsapp_token=os.environ["WHATSAPP_TOKEN"],
            whatsapp_number_id=os.environ["WHATSAPP_NUMBER_ID"],
            anthropic_key=os.environ["ANTHROPIC_API_KEY"],
            mongodb_uri=os.environ["MONGODB_URI"],
            verify_token=os.environ["VERIFY_TOKEN"]
        )


class AudioProcessor:
    def __init__(self, speech_speed: float = 1.3) -> None:
        if not shutil.which('ffmpeg'):
            raise RuntimeError("ffmpeg not found")
            
        self.speech_speed = speech_speed
        self.stt = pipeline("automatic-speech-recognition", model="openai/whisper-tiny", device="cpu")

    async def speech_to_text(self, audio_path: Path, user_language: str = 'en') -> Optional[str]:
        audio = AudioSegment.from_ogg(str(audio_path))
        with tempfile.NamedTemporaryFile(suffix=".wav") as wav_file:
            audio.export(wav_file.name, format="wav")
            result = await asyncio.to_thread(self.stt, wav_file.name, batch_size=4)
            return result["text"] if result else None

    def text_to_speech(self, text: str, language: str = 'en') -> Optional[bytes]:
        with tempfile.NamedTemporaryFile(suffix=".mp3") as mp3_file:
            tts = gTTS(text=text, lang=language, slow=False)
            tts.save(mp3_file.name)
            audio = AudioSegment.from_mp3(mp3_file.name)
            audio = audio.speedup(playback_speed=self.speech_speed)

            with tempfile.NamedTemporaryFile(suffix=".ogg") as ogg_file:
                audio.export(ogg_file.name, format="ogg", parameters=["-q:a", "4"])
                return ogg_file.read()


class ChatHistory:
    def __init__(self, mongodb_uri: str, ttl_days: int = 30):
        self.client = MongoClient(mongodb_uri, serverSelectionTimeoutMS=5000)
        self.db = self.client.whatsapp_bot
        self.collection = self.db.chat_histories
        
        self.collection.create_index([("chat_id", 1), ("timestamp", -1)])
        self.collection.create_index("timestamp", expireAfterSeconds=ttl_days * 24 * 60 * 60)

    @lru_cache(maxsize=100)
    def get_chat_language(self, chat_id: str) -> str:
        result = self.collection.find_one(
            {"chat_id": chat_id},
            {"language": 1}
        )
        return result.get("language", "en") if result else "en"

    def add_message(self, chat_id: str, role: str, content: str, language: str = "en"):
        try:
            self.collection.insert_one({
                "chat_id": chat_id,
                "role": role,
                "content": content,
                "language": language,
                "timestamp": datetime.utcnow(),
            })
        except Exception as e:
            logging.error(f"MongoDB error: {e}")

    def get_recent_messages(self, chat_id: str, limit: int = 10) -> List[dict]:
        cursor = self.collection.find(
            {"chat_id": chat_id},
            {"role": 1, "content": 1}
        ).sort("timestamp", -1).limit(limit)
        
        messages = list(cursor)
        messages.reverse()
        return messages


class WhatsAppBot:
    def __init__(self, config: BotConfig):
        self.config = config
        self.messenger = WhatsApp(config.whatsapp_token, phone_number_id=config.whatsapp_number_id)
        self.audio_processor = AudioProcessor(speech_speed=config.speech_speed)
        self.client = anthropic.Client(api_key=config.anthropic_key)
        self.chat_history = ChatHistory(config.mongodb_uri)

    async def get_claude_response(self, chat_id: str, user_message: str) -> str:
        messages = self.chat_history.get_recent_messages(chat_id, self.config.message_history_limit)
        messages = [{"role": msg["role"], "content": msg["content"]} for msg in messages]
        messages.append({"role": "user", "content": user_message})

        try:
            response = await asyncio.to_thread(
                self.client.messages.create,
                model="claude-3-opus-20240229",
                max_tokens=1024,
                messages=messages,
                system=self.config.personality,
            )
            return response.content[0].text
        except Exception as e:
            logging.error(f"Claude API error: {e}")
            return "I apologize, but I'm having trouble processing your request right now."

    async def handle_message(self, data: dict):
        try:
            # Extract message data
            message = data['entry'][0]['changes'][0]['value']['messages'][0]
            chat_id = message['from']
            
            # Handle different message types
            if 'text' in message:
                await self.handle_text_message(chat_id, message['text']['body'])
            elif 'voice' in message:
                await self.handle_voice_message(chat_id, message['voice'])
            else:
                self.messenger.send_message("I can only process text and voice messages.", chat_id)

        except Exception as e:
            logging.error(f"Message handling error: {e}")
            self.messenger.send_message(
                "Sorry, I encountered an error processing your message.", 
                chat_id
            )

    async def handle_text_message(self, chat_id: str, text: str):
        try:
            # Get Claude's response
            response = await self.get_claude_response(chat_id, text)
            
            # Save to chat history
            self.chat_history.add_message(chat_id, "user", text)
            self.chat_history.add_message(chat_id, "assistant", response)
            
            # Convert response to voice
            audio = self.audio_processor.text_to_speech(response)
            
            # Send both text and voice responses
            self.messenger.send_message(response, chat_id)
            if audio:
                with tempfile.NamedTemporaryFile(suffix=".ogg") as audio_file:
                    audio_file.write(audio)
                    audio_file.seek(0)
                    self.messenger.send_audio(
                        audio_file.name,
                        chat_id,
                        f"Voice response from {self.config.name}"
                    )

        except Exception as e:
            logging.error(f"Text message handling error: {e}")
            self.messenger.send_message(
                "Sorry, I encountered an error processing your message.", 
                chat_id
            )

    async def handle_voice_message(self, chat_id: str, voice_message: dict):
        try:
            # Download voice message
            voice_url = voice_message['media_url']
            with tempfile.NamedTemporaryFile(suffix=".ogg") as voice_file:
                # Download audio file (implementation depends on how WhatsApp provides the audio)
                # You'll need to implement the download logic based on WhatsApp's API
                
                # Convert speech to text
                text = await self.audio_processor.speech_to_text(Path(voice_file.name))
                
                if not text:
                    self.messenger.send_message(
                        "I couldn't understand the audio. Could you please try again?",
                        chat_id
                    )
                    return

                # Process as text message
                await self.handle_text_message(chat_id, text)

        except Exception as e:
            logging.error(f"Voice message handling error: {e}")
            self.messenger.send_message(
                "Sorry, I had trouble processing your voice message.",
                chat_id
            )

    def verify_webhook(self, token: str) -> bool:
        return token == self.config.verify_token


if __name__ == "__main__":
    import dotenv
    from flask import Flask, request
    
    dotenv.load_dotenv()
    
    logging.basicConfig(
        format='%(asctime)s - %(levelname)s - %(message)s (%(filename)s:%(lineno)d)',
        level=logging.INFO,
    )

    app = Flask(__name__)
    bot = WhatsAppBot(BotConfig.from_env())

    @app.route("/webhook", methods=["GET"])
    def verify_webhook():
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")

        if mode and token and mode == "subscribe" and bot.verify_webhook(token):
            return challenge, 200
        return "Forbidden", 403

    @app.route("/webhook", methods=["POST"])
    async def webhook():
        if request.is_json:
            data = request.get_json()
            await bot.handle_message(data)
            return "OK", 200
        return "Bad Request", 400

    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
