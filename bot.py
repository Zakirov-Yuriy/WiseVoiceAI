import logging
import os
import tempfile
import subprocess
import yt_dlp
import httpx
import requests

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# путь к папке, где лежат ffmpeg и ffprobe
ffmpeg_dir = r"D:\Programming\ffmpeg-7.1.1-essentials_build\bin"

# добавляем в системную переменную PATH (временно для скрипта)
os.environ["PATH"] += os.pathsep + ffmpeg_dir

# 🔗 Укажи адреса своих API (ngrok)
TRANSCRIBE_API_URL = "https://46f11c67f92c.ngrok-free.app/transcribe"
DIARIZATION_API_URL = "https://88eb9d6863e2.ngrok-free.app/diarize"

logging.basicConfig(level=logging.INFO)


def format_diarization(diarization):
    lines = []
    for segment in diarization:
        speaker = segment["speaker"]  # у тебя уже спикеры с 1 или 0, можно оставить как есть
        start = segment["start"]
        end = segment["end"]
        lines.append(f"🗣 Спикер {speaker}: {int(start)}–{int(end)} сек")
    return "\n".join(lines)


def check_ffprobe(ffmpeg_dir):
    ffprobe_path = os.path.join(ffmpeg_dir, "ffprobe.exe")
    try:
        subprocess.run([ffprobe_path, "-version"], check=True)
        print("ffprobe найден и работает")
    except Exception as e:
        print("Ошибка с ffprobe:", e)


check_ffprobe(ffmpeg_dir)


# --- send_file_to_api -----------------------------------
async def send_file_to_api(file_path: str):
    async with httpx.AsyncClient() as client:
        with open(file_path, "rb") as f:
            files = {"file": (os.path.basename(file_path), f, "audio/mpeg")}
            try:
                response = await client.post(TRANSCRIBE_API_URL, files=files)
            except httpx.RequestError as e:
                return {"error": f"Ошибка соединения с API: {e}"}

            if response.status_code != 200:
                return {"error": f"Ошибка API: {response.status_code} — {response.text}"}

            try:
                return response.json()
            except ValueError:
                return {"error": "Ошибка: некорректный или пустой ответ от API"}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Отправь мне аудио, видео или ссылку (в т.ч. YouTube) — я всё транскрибирую 🧠")

# Объединение сегментов одного спикера
def merge_consecutive_segments(segments):
    if not segments:
        return []

    merged = []
    current = segments[0].copy()

    for seg in segments[1:]:
        if seg["speaker"] == current["speaker"]:
            # объединяем
            current["end"] = seg["end"]
            current["text"] += " " + seg["text"]
        else:
            # сохраняем и начинаем новый
            merged.append(current)
            current = seg.copy()

    merged.append(current)
    return merged


import uuid


# 📹 Обработка ссылки (в т.ч. YouTube)
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    if not url.startswith("http"):
        return await update.message.reply_text("Отправьте ссылку или медиа-файл.")

    await update.message.reply_text("🔗 Скачиваю и обрабатываю видео...")

    result = download_and_transcribe_youtube(url, DIARIZATION_API_URL)

    if "error" in result:
        return await update.message.reply_text(f"❌ Ошибка: {result['error']}")

    combined = result.get("combined", [])
    if not combined:
        return await update.message.reply_text("🤷 Ничего не распознано.")

    full_text = ""
    for seg in combined:
        full_text += f"🗣 Спикер {seg['speaker']}:\n{seg['text']}\n\n"

    # Разбиваем текст на части по 4000 символов (чуть меньше лимита)
    for i in range(0, len(full_text), 4000):
        await update.message.reply_text(full_text[i:i + 4000])


# 1. Отправляем в API транскрибации
async def get_transcript(file_path: str):
    async with httpx.AsyncClient() as client:
        with open(file_path, "rb") as f:
            files = {"file": (os.path.basename(file_path), f, "audio/mpeg")}
            response = await client.post(TRANSCRIBE_API_URL, files=files)

        # ✅ Проверка, что API вернул успешный ответ
        if response.status_code != 200:
            return {"error": f"Ошибка API: {response.status_code} — {response.text}"}

        try:
            result = response.json()
        except ValueError:
            # ✅ Ошибка парсинга JSON: скорее всего API вернул пустой/обрывочный ответ
            return {"error": "Ошибка: пустой или некорректный ответ от API"}

        return result


def combine_transcript_with_diarization(transcript_segments, diarization_segments):
    result = []
    used_segments = set()  # ⬅️ хранение уже добавленных текстов по start-end

    for d in diarization_segments:
        speaker = d["speaker"]
        d_start = d["start"]
        d_end = d["end"]

        speaker_text = ""

        for t in transcript_segments:
            t_start = t["start"]
            t_end = t["end"]

            # Проверка на пересечение по времени
            if t_end >= d_start and t_start <= d_end:
                key = (t_start, t_end, t["text"])

                if key not in used_segments:
                    speaker_text += t["text"].strip() + " "
                    used_segments.add(key)

        result.append({
            "speaker": speaker,
            "start": d_start,
            "end": d_end,
            "text": speaker_text.strip()
        })

    return result

def download_and_transcribe_youtube(url: str, diarization_url: str):
    temp_dir = tempfile.gettempdir()
    unique_id = str(uuid.uuid4())
    outtmpl = os.path.join(temp_dir, unique_id + ".%(ext)s")

    try:
        ffmpeg_dir = r"D:\Programming\ffmpeg-7.1.1-essentials_build\bin"
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": outtmpl,
            "ffmpeg_location": ffmpeg_dir,
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
            "quiet": True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(url, download=True)
            audio_path = ydl.prepare_filename(info_dict)
            audio_path = os.path.splitext(audio_path)[0] + ".mp3"

        if not os.path.exists(audio_path):
            return {"error": "Файл не найден после скачивания"}

        # Отправляем файл в оба API
        with open(audio_path, "rb") as f:
            files = {"file": (os.path.basename(audio_path), f, "audio/mpeg")}
            diarization_response = requests.post(DIARIZATION_API_URL, files=files)
            diarization_data = diarization_response.json()

        with open(audio_path, "rb") as f:
            files = {"file": (os.path.basename(audio_path), f, "audio/mpeg")}
            transcript_response = requests.post(TRANSCRIBE_API_URL, files=files)
            transcript_data = transcript_response.json()

        print("DEBUG diarization:", diarization_data)
        print("DEBUG transcript:", transcript_data)

        if "error" in diarization_data or "segments" not in transcript_data:
            return {"error": "Ошибка в одном из API"}

        # Объединяем транскрипцию с диаризацией
        combined = combine_transcript_with_diarization(
            transcript_data.get("segments", []),
            diarization_data.get("diarization", [])
        )
        combined = merge_consecutive_segments(combined)

        return {"combined": combined}

    except Exception as e:
        return {"error": str(e)}

    finally:
        if os.path.exists(audio_path):
            os.remove(audio_path)


#🔽 Загрузка и обработка медиа-файла
async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file = update.message.audio or update.message.voice or update.message.video or update.message.document
    if not file:
        return await update.message.reply_text("Файл не поддерживается.")

    with tempfile.NamedTemporaryFile(delete=False) as temp:
        file_path = temp.name

    tg_file = await file.get_file()
    await tg_file.download_to_drive(file_path)

    await update.message.reply_text("📤 Загружаю и обрабатываю файл...")

    try:
        # Получаем текст и диаризацию
        transcript_data = await get_transcript(file_path)
        diarization_data = await send_file_to_api(file_path, DIARIZATION_API_URL)

        if "error" in transcript_data or "error" in diarization_data:
            await update.message.reply_text("❌ Ошибка обработки аудио")
            return

        combined = combine_transcript_with_diarization(
            transcript_data["segments"],
            diarization_data["diarization"]
        )
        combined = merge_consecutive_segments(combined)

        full_text = ""
        for seg in combined:
            full_text += f"🗣 Спикер {seg['speaker']}:\n{seg['text']}\n\n"

        await update.message.reply_text(full_text or "🤷 Ничего не распознано.")

    finally:
        os.remove(file_path)



def main():
    app = ApplicationBuilder().token("7618300935:AAFnmKhqc3Bxm6edtjLcgZnIU5yUHa0h1O8").build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.AUDIO | filters.VOICE | filters.VIDEO | filters.Document.ALL, handle_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("Бот запущен")
    app.run_polling()


if __name__ == "__main__":
    main()
