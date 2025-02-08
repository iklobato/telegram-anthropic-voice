import os
from dotenv import load_dotenv
from pydub import AudioSegment
import torch
from transformers import pipeline
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from TTS.api import TTS
import warnings
import logging

logging.basicConfig(level=logging.INFO)
logging.getLogger("transformers").setLevel(logging.ERROR)
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("telegram").setLevel(logging.ERROR)
logging.getLogger("TTS").setLevel(logging.ERROR)

warnings.filterwarnings("ignore")
load_dotenv()

HUGGING_FACE_TOKEN = os.getenv("HUGGING_FACE_TOKEN")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

asr = pipeline(
    "automatic-speech-recognition",
    "openai/whisper-tiny",
    token=HUGGING_FACE_TOKEN,
    device=0 if torch.cuda.is_available() else -1,
)

llm = pipeline(
    "text-generation",
    "TinyLlama/TinyLlama-1.1B-Chat-v1.0",
    token=HUGGING_FACE_TOKEN,
    device_map="auto",
    max_new_tokens=100,
)

tts = TTS(model_name="tts_models/pt/cv/vits", progress_bar=False)

template = """Você é um médico respondendo uma consulta. Seja breve e direto.
Questão do paciente: {}
Resposta:"""


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if update.message.voice:
            voice = await update.message.voice.get_file()
            await voice.download_to_drive("input.ogg")
            AudioSegment.from_ogg("input.ogg").export("input.wav", format="wav")
            question = asr("input.wav")["text"]
        else:
            question = update.message.text

        logging.info(f"Question: {question}")
        response = llm(template.format(question))[0]["generated_text"]
        response = response.split("Resposta:")[-1].strip()

        tts.tts_to_file(text=response, file_path="output.wav")
        await update.message.reply_voice(voice=open("output.wav", "rb"))

    except Exception as e:
        logging.error(e)
        try:
            error_message = "Desculpe, ocorreu um erro. Por favor, tente novamente."
            tts.tts_to_file(text=error_message, file_path="error.wav")
            await update.message.reply_voice(voice=open("error.wav", "rb"))
        except Exception as e:
            logging.error(e)
            await update.message.reply_text(error_message)

    finally:
        for file in ["input.ogg", "input.wav", "output.wav", "error.wav"]:
            if os.path.exists(file):
                try:
                    os.remove(file)
                except:
                    pass


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler((filters.VOICE | filters.TEXT), handle_message))
    app.run_polling()


if __name__ == "__main__":
    main()

