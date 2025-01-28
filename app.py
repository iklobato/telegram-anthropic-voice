import logging
import os
from dataclasses import dataclass
from typing import Optional, List
from pathlib import Path
import tempfile
import asyncio
import anthropic
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from pydub import AudioSegment
from transformers import pipeline
import pymongo
from datetime import datetime
from gtts import gTTS
from transformers.utils.import_utils import shutil
from functools import lru_cache


@dataclass
class BotConfig:
    name: str
    personality: str
    telegram_token: str
    anthropic_key: str
    mongodb_uri: str
    message_history_limit: int = 10
    speech_speed: float = 1.3

    @classmethod
    def from_env(cls) -> 'BotConfig':
        return cls(
            name=os.getenv("BOT_NAME", "Sophie"),
            personality=os.getenv("BOT_PERSONALITY", "You are Sophie, a friendly and helpful assistant."),
            telegram_token=os.environ["TELEGRAM_TOKEN"],
            anthropic_key=os.environ["ANTHROPIC_API_KEY"],
            mongodb_uri=os.environ["MONGODB_URI"],
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
        self.client = pymongo.MongoClient(mongodb_uri, serverSelectionTimeoutMS=5000)
        self.db = self.client.telegram_bot
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


class Bot:
    def __init__(self, config: BotConfig):
        self.config = config
        self.audio_processor = AudioProcessor(speech_speed=config.speech_speed)
        self.client = anthropic.Client(api_key=config.anthropic_key)
        self.chat_history = ChatHistory(config.mongodb_uri)
        self._setup_retries()

    def _setup_retries(self):
        self.max_retries = 3
        self.retry_delay = 1

    async def get_claude_response(self, chat_id: str, user_message: str) -> str:
        messages = self.chat_history.get_recent_messages(chat_id, self.config.message_history_limit)
        messages = [{"role": msg["role"], "content": msg["content"]} for msg in messages]
        messages.append({"role": "user", "content": user_message})

        for attempt in range(self.max_retries):
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
                if attempt == self.max_retries - 1:
                    raise
                await asyncio.sleep(self.retry_delay * (attempt + 1))

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(f"Hi! I'm {self.config.name}. Send me messages or voice notes!")

    async def handle_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = str(update.effective_chat.id)
        
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
        
        response = await self.get_claude_response(chat_id, update.message.text)
        language = update.effective_user.language_code or 'en'
        
        self.chat_history.add_message(chat_id, "user", update.message.text, language)
        self.chat_history.add_message(chat_id, "assistant", response, language)

        audio = self.audio_processor.text_to_speech(response, language)
        
        with tempfile.NamedTemporaryFile(suffix=".ogg") as audio_file:
            audio_file.write(audio)
            audio_file.seek(0)
            await asyncio.gather(
                update.message.reply_voice(voice=audio_file),
                update.message.reply_text(response)
            )

    async def handle_voice_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = str(update.effective_chat.id)
        
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        
        try:
            language = update.effective_user.language_code or 'en'
            voice = await update.message.voice.get_file()
            
            with tempfile.NamedTemporaryFile(suffix=".ogg") as voice_file:
                await voice.download_to_drive(voice_file.name)
                text = await self.audio_processor.speech_to_text(Path(voice_file.name), language)
                
                if not text:
                    await update.message.reply_text("Could not understand audio. Please try again.")
                    return

                await self.handle_text_message(
                    update._replace(message=update.message._replace(text=text)),
                    context
                )
        except Exception as e:
            logging.error(f"Voice processing error: {e}")
            await update.message.reply_text("Sorry, I couldn't process your voice message.")

    def run(self):
        application = (
            ApplicationBuilder()
            .token(self.config.telegram_token)
            .connection_pool_size(8)
            .pool_timeout(30.0)
            .read_timeout(30.0)
            .write_timeout(30.0)
            .build()
        )

        application.add_handler(CommandHandler("start", self.start_command))
        application.add_handler(MessageHandler(filters.VOICE, self.handle_voice_message))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text_message))

        application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    import dotenv
    dotenv.load_dotenv()

    logging.basicConfig(
        format='%(asctime)s - %(levelname)s - %(message)s (%(filename)s:%(lineno)d)',
        level=logging.INFO,
    )

    Bot(BotConfig.from_env()).run()
