import logging
import os
from dataclasses import dataclass
from typing import Optional
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


@dataclass
class BotConfig:
    name: str
    personality: str
    telegram_token: str
    anthropic_key: str
    mongodb_uri: str

    @classmethod
    def from_env(cls) -> 'BotConfig':
        return cls(
            name="Sophie",
            personality="You are Sophie, a friendly and helpful assistant.",
            telegram_token=os.environ["TELEGRAM_TOKEN"],
            anthropic_key=os.environ["ANTHROPIC_API_KEY"],
            mongodb_uri=os.environ["MONGODB_URI"],
        )


class AudioProcessor:

    
    def __init__(self) -> None:
        self.device = "cpu"
        if not shutil.which('ffmpeg'):
            raise RuntimeError("ffmpeg not found. Please install ffmpeg first.")
            
        self.stt = pipeline(
            "automatic-speech-recognition",
            model="openai/whisper-small",
            device=self.device,
        )

    async def speech_to_text(self, audio_path: Path, user_language: str = 'en') -> Optional[str]:
        audio = AudioSegment.from_ogg(str(audio_path))
        with tempfile.NamedTemporaryFile(suffix=".wav") as wav_file:
            audio.export(wav_file.name, format="wav")
            result = await asyncio.to_thread(
                self.stt,
                wav_file.name,
                batch_size=8,
                generate_kwargs={"language": user_language}
            )
            return result["text"] if result else None

    def text_to_speech(self, text: str, language: str = 'en') -> Optional[bytes]:
        with tempfile.NamedTemporaryFile(suffix=".mp3") as mp3_file:
            tts = gTTS(text=text, lang=language, slow=False)
            tts.save(mp3_file.name)
            audio = AudioSegment.from_mp3(mp3_file.name)
            
            with tempfile.NamedTemporaryFile(suffix=".ogg") as ogg_file:
                audio.export(ogg_file.name, format="ogg")
                with open(ogg_file.name, "rb") as f:
                    return f.read()

class ChatHistory:
    def __init__(self, mongodb_uri: str):
        self.client = pymongo.MongoClient(mongodb_uri)
        self.db = self.client.telegram_bot
        self.collection = self.db.chat_histories

    def add_message(self, chat_id: str, role: str, content: str):
        self.collection.insert_one({
            "chat_id": chat_id,
            "role": role,
            "content": content,
            "timestamp": datetime.utcnow()
        })

    def get_recent_messages(self, chat_id: str, limit: int = 10):
        messages = self.collection.find(
            {"chat_id": chat_id},
            {"_id": 0, "role": 1, "content": 1}
        ).sort("timestamp", -1).limit(limit)
        if messages.retrieved:
            return list(messages)
        return []


class Bot:
    def __init__(self, config: BotConfig) -> None:
        self.config = config
        self.audio_processor = AudioProcessor()
        self.client = anthropic.Client(api_key=config.anthropic_key)
        self.chat_history = ChatHistory(config.mongodb_uri)

    async def get_claude_response(self, chat_id: str, user_message: str) -> str:
        recent_messages = self.chat_history.get_recent_messages(chat_id)
        
        messages = [
            {
                "role": msg["role"],
                "content": msg["content"]
            }
            for msg in recent_messages
        ]
        
        messages.append({
            "role": "user",
            "content": user_message
        })

        response = await asyncio.to_thread(
            self.client.messages.create,
            model="claude-3-opus-20240229",
            max_tokens=1024,
            messages=messages,
            system=self.config.personality
        )
        
        response_text = response.content[0].text
        
        self.chat_history.add_message(chat_id, "user", user_message)
        self.chat_history.add_message(chat_id, "assistant", response_text)
        
        return response_text

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(
            f"Hi! I'm {self.config.name}. Send me messages or documents!"
        )

    async def handle_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id,
            action="typing"
        )

        chat_id = str(update.effective_chat.id)
        response = await self.get_claude_response(chat_id, update.message.text)
        language = update.effective_user.language_code or 'en'
        audio = self.audio_processor.text_to_speech(response, language)

        with tempfile.NamedTemporaryFile(suffix=".ogg") as audio_file:
            audio_file.write(audio)
            audio_file.seek(0)
            await update.message.reply_voice(voice=audio_file)
            await update.message.reply_text(response)

    async def handle_voice_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.audio_processor:
            await update.message.reply_text(
                "Voice processing is currently unavailable. Please send text messages instead."
            )
            return
        
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id,
            action="typing"
        )
            
        try:
            language = update.effective_user.language_code or 'en'
            
            voice = await update.message.voice.get_file()
            with tempfile.NamedTemporaryFile(suffix=".ogg") as voice_file:
                await voice.download_to_drive(voice_file.name)
                text = await self.audio_processor.speech_to_text(
                    Path(voice_file.name),
                    user_language=language
                )
                if text:
                    await self.handle_text_message(update._replace(message=update.message._replace(text=text)), context)
                else:
                    await update.message.reply_text(
                        "Could not understand audio. Please try again."
                    )
        except Exception as e:
            logging.error(f"Error handling voice message: {e}")
            await update.message.reply_text(
                "Sorry, I encountered an error processing your voice message."
            )
    
    async def handle_document_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id,
            action="typing"
        )
        try:
            document = await update.message.document.get_file()
            with tempfile.NamedTemporaryFile(
                suffix=Path(document.file_path).suffix
            ) as doc_file:
                await document.download_to_drive(doc_file.name)
                with open(doc_file.name, 'r', encoding='utf-8') as f:
                    file_content = f.read()

            prompt = f"Here's a document I'd like you to analyze:\n\n{file_content}"
            await self.handle_text_message(update, context)
        except Exception as e:
            logging.error(f"Error handling document: {e}")
            await update.message.reply_text(
                "Sorry, I encountered an error processing your document."
            )

    def run(self) -> None:
        logging.info("Starting bot")
        
        application = (
            ApplicationBuilder()
            .token(self.config.telegram_token)
            .connection_pool_size(8)
            .connect_timeout(30.0)
            .read_timeout(30.0)
            .write_timeout(30.0)
            .build()
        )

        application.add_handler(CommandHandler("start", self.start_command))
        application.add_handler(MessageHandler(filters.VOICE, self.handle_voice_message))
        application.add_handler(MessageHandler(filters.Document.ALL, self.handle_document_message))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text_message))

        try:
            application.run_polling(allowed_updates=Update.ALL_TYPES)
        except Exception as e:
            logging.error(f"Error running bot: {e}")
            raise


if __name__ == "__main__":
    import dotenv
    dotenv.load_dotenv()
    
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s (%(filename)s:%(lineno)d)',
        level=logging.INFO
    )
    
    try:
        Bot(BotConfig.from_env()).run()
    except Exception as e:
        logging.error(f"Fatal error: {e}")
        raise
