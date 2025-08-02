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
TRANSCRIBE_API_URL = "https://hay-brazilian-ma-bulk.trycloudflare.com/transcribe"
DIARIZATION_API_URL = "https://handbook-movement-error-king.trycloudflare.com/diarize"

logging.basicConfig(level=logging.INFO)


def split_audio(input_path, segment_time=30):
    output_dir = os.path.join(os.path.dirname(input_path), "fragments")
    os.makedirs(output_dir, exist_ok=True)
    output_pattern = os.path.join(output_dir, "fragment_%03d.mp3")

    command = [
        os.path.join(ffmpeg_dir, "ffmpeg.exe"),
        "-i", input_path,
        "-f", "segment",
        "-segment_time", str(segment_time),
        "-c", "copy",
        output_pattern
    ]
    subprocess.run(command, check=True)

    fragments = sorted([
        os.path.join(output_dir, f)
        for f in os.listdir(output_dir)
        if f.startswith("fragment_")
    ])
    return fragments


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
async def send_file_to_api(file_path: str, api_url: str):
    async with httpx.AsyncClient() as client:
        with open(file_path, "rb") as f:
            files = {"file": (os.path.basename(file_path), f, "audio/mpeg")}
            try:
                response = await client.post(api_url, files=files)
            except httpx.RequestError as e:
                return {"error": f"Ошибка соединения с API: {e}"}

            if response.status_code != 200:
                return {"error": f"Ошибка API: {response.status_code} — {response.text}"}

            try:
                return response.json()
            except ValueError:
                return {"error": "Ошибка: некорректный ответ от API"}


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

    # Убрать второй аргумент DIARIZATION_API_URL
    result = download_and_transcribe_youtube(url)

    if "error" in result:
        return await update.message.reply_text(f"❌ Ошибка: {result['error']}")

    combined = result.get("combined", [])
    if not combined:
        return await update.message.reply_text("🤷 Ничего не распознано.")

    full_text = ""
    for seg in combined:
        full_text += f"🗣 Спикер {seg['speaker']}:\n{seg['text']}\n\n"

    # Разбиваем текст на части по 4000 символов
    for i in range(0, len(full_text), 4000):
        await update.message.reply_text(full_text[i:i + 4000])


def download_and_transcribe_youtube(url: str):
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

        # Создаем папку для фрагментов
        fragment_dir = os.path.join(os.path.dirname(audio_path), "fragments")
        os.makedirs(fragment_dir, exist_ok=True)
        output_pattern = os.path.join(fragment_dir, "fragment_%03d.mp3")

        # Разбиваем аудио на фрагменты по 30 секунд
        command = [
            os.path.join(ffmpeg_dir, "ffmpeg.exe"),
            "-i", audio_path,
            "-f", "segment",
            "-segment_time", "30",
            "-c", "copy",
            output_pattern
        ]
        subprocess.run(command, check=True)

        fragments = sorted([
            os.path.join(fragment_dir, f)
            for f in os.listdir(fragment_dir)
            if f.startswith("fragment_") and f.endswith(".mp3")
        ])

        if not fragments:
            return {"error": "Не удалось разделить аудио на фрагменты"}

        all_segments = []

        # Обрабатываем каждый фрагмент
        for i, fragment_path in enumerate(fragments):
            # Смещение времени для текущего фрагмента
            time_offset = i * 30

            # Отправляем фрагмент на транскрибацию
            with open(fragment_path, "rb") as f:
                transcript_response = requests.post(TRANSCRIBE_API_URL, files={"file": f})
                transcript_data = transcript_response.json()

            # Отправляем фрагмент на диаризацию
            with open(fragment_path, "rb") as f:
                diarization_response = requests.post(DIARIZATION_API_URL, files={"file": f})
                diarization_data = diarization_response.json()

            # Если получили ошибку - пропускаем фрагмент
            if "segments" not in transcript_data or "diarization" not in diarization_data:
                logging.error(
                    f"Ошибка обработки фрагмента {i}: {transcript_data.get('error', '')} {diarization_data.get('error', '')}")
                continue

            # Корректируем временные метки
            for segment in transcript_data.get("segments", []):
                segment["start"] += time_offset
                segment["end"] += time_offset

            for segment in diarization_data.get("diarization", []):
                segment["start"] += time_offset
                segment["end"] += time_offset

            # Объединяем результаты фрагмента
            combined = combine_transcript_with_diarization(
                transcript_data.get("segments", []),
                diarization_data.get("diarization", [])
            )
            all_segments.extend(combined)

        # Объединяем последовательные сегменты
        merged = merge_consecutive_segments(all_segments)
        return {"combined": merged}

    except Exception as e:
        logging.exception("Ошибка при обработке YouTube видео")
        return {"error": str(e)}

    finally:
        # Очистка временных файлов
        if os.path.exists(audio_path):
            os.remove(audio_path)
        # Удаление фрагментов
        fragment_dir = os.path.join(os.path.dirname(audio_path), "fragments")
        if os.path.exists(fragment_dir):
            for f in os.listdir(fragment_dir):
                os.remove(os.path.join(fragment_dir, f))
            os.rmdir(fragment_dir)



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


def download_and_transcribe_youtube(url: str):
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

        # Создаем папку для фрагментов
        fragment_dir = os.path.join(os.path.dirname(audio_path), "fragments")
        os.makedirs(fragment_dir, exist_ok=True)
        output_pattern = os.path.join(fragment_dir, "fragment_%03d.mp3")

        # Разбиваем аудио на фрагменты по 30 секунд
        command = [
            os.path.join(ffmpeg_dir, "ffmpeg.exe"),
            "-i", audio_path,
            "-f", "segment",
            "-segment_time", "30",
            "-c", "copy",
            output_pattern
        ]
        subprocess.run(command, check=True)

        fragments = sorted([
            os.path.join(fragment_dir, f)
            for f in os.listdir(fragment_dir)
            if f.startswith("fragment_") and f.endswith(".mp3")
        ])

        if not fragments:
            return {"error": "Не удалось разделить аудио на фрагменты"}

        all_segments = []

        # Обрабатываем каждый фрагмент
        for i, fragment_path in enumerate(fragments):
            # Смещение времени для текущего фрагмента
            time_offset = i * 30

            # Отправляем фрагмент на транскрибацию
            with open(fragment_path, "rb") as f:
                transcript_response = requests.post(TRANSCRIBE_API_URL, files={"file": f})
                transcript_data = transcript_response.json()

            # Отправляем фрагмент на диаризацию
            with open(fragment_path, "rb") as f:
                diarization_response = requests.post(DIARIZATION_API_URL, files={"file": f})
                diarization_data = diarization_response.json()

            # Если получили ошибку - пропускаем фрагмент
            if "segments" not in transcript_data or "diarization" not in diarization_data:
                logging.error(
                    f"Ошибка обработки фрагмента {i}: {transcript_data.get('error')} {diarization_data.get('error')}")
                continue

            # Корректируем временные метки
            for segment in transcript_data.get("segments", []):
                segment["start"] += time_offset
                segment["end"] += time_offset

            for segment in diarization_data.get("diarization", []):
                segment["start"] += time_offset
                segment["end"] += time_offset

            # Объединяем результаты фрагмента
            combined = combine_transcript_with_diarization(
                transcript_data.get("segments", []),
                diarization_data.get("diarization", [])
            )
            all_segments.extend(combined)

        # Объединяем последовательные сегменты
        merged = merge_consecutive_segments(all_segments)
        return {"combined": merged}

    except Exception as e:
        logging.exception("Ошибка при обработке YouTube видео")
        return {"error": str(e)}

    finally:
        # Очистка временных файлов
        if os.path.exists(audio_path):
            os.remove(audio_path)
        # Удаление фрагментов
        fragment_dir = os.path.join(os.path.dirname(audio_path), "fragments")
        if os.path.exists(fragment_dir):
            for f in os.listdir(fragment_dir):
                os.remove(os.path.join(fragment_dir, f))
            os.rmdir(fragment_dir)

#🔽 Загрузка и обработка медиа-файла
# 🔽 Загрузка и обработка медиа-файла
async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file = update.message.audio or update.message.voice or update.message.video or update.message.document
    if not file:
        return await update.message.reply_text("Файл не поддерживается.")

    # Создаем временный файл
    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as temp:
        file_path = temp.name

    # Скачиваем файл
    tg_file = await file.get_file()
    await tg_file.download_to_drive(file_path)

    await update.message.reply_text("📤 Загружаю и обрабатываю файл...")

    try:
        # Создаем папку для фрагментов
        fragment_dir = os.path.join(os.path.dirname(file_path), "fragments")
        os.makedirs(fragment_dir, exist_ok=True)
        output_pattern = os.path.join(fragment_dir, "fragment_%03d.mp3")

        # Разбиваем аудио на фрагменты по 30 секунд
        command = [
            os.path.join(ffmpeg_dir, "ffmpeg.exe"),
            "-i", file_path,
            "-f", "segment",
            "-segment_time", "30",
            "-c", "copy",
            output_pattern
        ]
        subprocess.run(command, check=True)

        fragments = sorted([
            os.path.join(fragment_dir, f)
            for f in os.listdir(fragment_dir)
            if f.startswith("fragment_") and f.endswith(".mp3")
        ])

        if not fragments:
            await update.message.reply_text("❌ Не удалось разделить аудио на фрагменты")
            return

        all_segments = []

        # Обрабатываем каждый фрагмент
        for i, fragment_path in enumerate(fragments):
            # Смещение времени для текущего фрагмента
            time_offset = i * 30

            # Получаем транскрипцию фрагмента
            transcript_data = await send_file_to_api(fragment_path, TRANSCRIBE_API_URL)
            # Получаем диаризацию фрагмента
            diarization_data = await send_file_to_api(fragment_path, DIARIZATION_API_URL)

            # Если получили ошибку - пропускаем фрагмент
            if "segments" not in transcript_data or "diarization" not in diarization_data:
                logging.error(
                    f"Ошибка обработки фрагмента {i}: {transcript_data.get('error')} {diarization_data.get('error')}")
                continue

            # Корректируем временные метки
            for segment in transcript_data.get("segments", []):
                segment["start"] += time_offset
                segment["end"] += time_offset

            for segment in diarization_data.get("diarization", []):
                segment["start"] += time_offset
                segment["end"] += time_offset

            # Объединяем результаты фрагмента
            combined = combine_transcript_with_diarization(
                transcript_data.get("segments", []),
                diarization_data.get("diarization", [])
            )
            all_segments.extend(combined)

        # Объединяем последовательные сегменты
        merged = merge_consecutive_segments(all_segments)

        # Формируем итоговый текст
        full_text = ""
        for seg in merged:
            full_text += f"🗣 Спикер {seg['speaker']}:\n{seg['text']}\n\n"

        # Отправляем результат частями
        if full_text:
            for i in range(0, len(full_text), 4000):
                await update.message.reply_text(full_text[i:i + 4000])
        else:
            await update.message.reply_text("🤷 Ничего не распознано.")

    except Exception as e:
        logging.exception("Ошибка при обработке файла")
        await update.message.reply_text(f"❌ Произошла ошибка: {str(e)}")

    finally:
        # Очистка временных файлов
        if os.path.exists(file_path):
            os.remove(file_path)
        # Удаление фрагментов
        fragment_dir = os.path.join(os.path.dirname(file_path), "fragments")
        if os.path.exists(fragment_dir):
            for f in os.listdir(fragment_dir):
                os.remove(os.path.join(fragment_dir, f))
            os.rmdir(fragment_dir)


def main():
    app = ApplicationBuilder().token("7618300935:AAFnmKhqc3Bxm6edtjLcgZnIU5yUHa0h1O8").build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.AUDIO | filters.VOICE | filters.VIDEO | filters.Document.ALL, handle_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("Бот запущен")
    app.run_polling()


if __name__ == "__main__":
    main()
