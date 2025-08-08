import asyncio
import logging
import os
import tempfile
import subprocess
import yt_dlp
import httpx
import uuid
import telegram.error  # Добавьте этот импорт
from telegram import Message
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from langdetect import detect, LangDetectException
from locales import locales  # Добавлен импорт локалей
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    CallbackQueryHandler,
)

# Конфигурация (остается без изменений)
FFMPEG_DIR = r"D:\Programming\ffmpeg-7.1.1-essentials_build\bin"
# os.environ["PATH"] += os.pathsep + FFMPEG_DIR

# Измените URL на локальные
# TRANSCRIBE_API_URL = "http://localhost:8000/transcribe"
# DIARIZATION_API_URL = "http://localhost:8000/diarize"

TRANSCRIBE_API_URL = "https://739fcf68dc5f.ngrok-free.app/transcribe"
DIARIZATION_API_URL = "https://739fcf68dc5f.ngrok-free.app/diarize"

API_TIMEOUT = 300  # seconds
SEGMENT_DURATION = 60  # seconds
MESSAGE_CHUNK_SIZE = 4000  # characters

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)



# Добавьте проверку доступности API при запуске
async def check_api_availability():
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(TRANSCRIBE_API_URL.replace("/transcribe", ""), timeout=5.0)
        if response.status_code != 200:
            logger.error("API транскрибации недоступно")
        # Аналогично для DIARIZATION_API_URL
    except Exception as e:
        logger.error(f"Ошибка подключения к API: {str(e)}")

# Вызовите эту функцию при запуске бота
# ================================================================================


# Добавляем функцию для получения локализованного текста
def get_string(key: str, lang: str = 'ru', **kwargs) -> str:
    """Возвращает локализованную строку по ключу"""
    lang_dict = locales.get(lang, locales['ru'])
    text = lang_dict.get(key, key)
    if kwargs:
        return text.format(**kwargs)
    return text


# ================================================================================


# Путь к шрифту — абсолютный с raw-строкой
font_path = r"C:\Users\zakco\PycharmProjects\WiseVoiceAI\DejaVuSans.ttf"

# Регистрируем шрифт
pdfmetrics.registerFont(TTFont("DejaVu", font_path))


def save_text_to_pdf(text: str, output_path: str):
    doc = SimpleDocTemplate(output_path, pagesize=A4,
                            rightMargin=50, leftMargin=50,
                            topMargin=50, bottomMargin=50)

    styles = getSampleStyleSheet()
    style = styles['Normal']
    style.fontName = 'DejaVu'  # твой зарегистрированный шрифт
    style.fontSize = 12
    style.leading = 15  # межстрочный интервал

    # Разбиваем текст на параграфы по двойному переносу строки
    paragraphs = [Paragraph(p.replace('\n', '<br />'), style) for p in text.split('\n\n')]

    elems = []
    for p in paragraphs:
        elems.append(p)
        elems.append(Spacer(1, 12))  # пробел между параграфами

    doc.build(elems)


class AudioProcessor:
    """Инкапсулирует логику работы с аудиофайлами"""

    @staticmethod
    def split_audio(input_path: str, segment_time: int = SEGMENT_DURATION) -> list[str]:
        """Разбивает аудиофайл на фрагменты"""
        output_dir = tempfile.mkdtemp(prefix="fragments_")
        output_pattern = os.path.join(output_dir, "fragment_%03d.mp3")

        # Используем абсолютный путь к ffmpeg
        ffmpeg_path = os.path.join(FFMPEG_DIR, "ffmpeg.exe")
        if not os.path.exists(ffmpeg_path):
            raise FileNotFoundError(f"FFmpeg не найден по пути: {ffmpeg_path}")

        command = [
            ffmpeg_path,
            "-i", input_path,
            "-f", "segment",
            "-segment_time", str(segment_time),
            "-c", "copy",
            output_pattern
        ]

        try:
            result = subprocess.run(command, check=True, capture_output=True, text=True)
            logger.debug(f"FFmpeg output: {result.stdout}")
        except subprocess.CalledProcessError as e:
            logger.error(f"FFmpeg error: {e.stderr}")
            raise RuntimeError("Ошибка при разделении аудио") from e

        return sorted([
            os.path.join(output_dir, f)
            for f in os.listdir(output_dir)
            if f.startswith("fragment_") and f.endswith(".mp3")
        ])

    @staticmethod
    def cleanup(files: list[str]):
        """Безопасное удаление временных файлов"""
        for path in files:
            try:
                if os.path.isfile(path):
                    os.remove(path)
                elif os.path.isdir(path):
                    for f in os.listdir(path):
                        os.remove(os.path.join(path, f))
                    os.rmdir(path)
            except Exception as e:
                logger.warning(f"Ошибка удаления {path}: {e}")


async def send_file_to_api(file_path: str, api_url: str) -> dict:
    """Асинхронная отправка файла на внешний API с повторными попытками"""
    max_retries = 3
    retry_delay = 5  # секунд

    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
                with open(file_path, "rb") as f:
                    files = {"file": (os.path.basename(file_path), f, "audio/mpeg")}
                    response = await client.post(api_url, files=files)
                response.raise_for_status()
                return response.json()

        except httpx.HTTPStatusError as e:
            logger.error(f"API error {e.response.status_code} (attempt {attempt + 1}/{max_retries}): {e.response.text}")
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay)
            else:
                return {"error": f"Ошибка API: {e.response.status_code}"}

        except (httpx.RequestError, OSError) as e:
            logger.error(f"Connection error (attempt {attempt + 1}/{max_retries}): {str(e)}")
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay)
            else:
                return {"error": f"Ошибка соединения: {str(e)}"}

        except Exception as e:
            logger.exception(f"Unexpected error (attempt {attempt + 1}/{max_retries})")
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay)
            else:
                return {"error": f"Неизвестная ошибка: {str(e)}"}


def merge_consecutive_segments(segments: list[dict]) -> list[dict]:
    """Объединяет последовательные сегменты одного спикера"""
    if not segments:
        return []

    merged = []
    current = segments[0].copy()

    for seg in segments[1:]:
        if seg["speaker"] == current["speaker"]:
            current["end"] = seg["end"]
            current["text"] += " " + seg["text"].strip()
        else:
            merged.append(current)
            current = seg.copy()

    merged.append(current)
    return merged


def format_results(segments: list[dict]) -> str:
    """Форматирует результаты в читаемый текст без временных меток"""
    return "\n\n".join(
        f"Спикер {int(seg['speaker'])}:\n{seg['text']}"
        for seg in segments
    )


def combine_transcript_with_diarization(transcript_segments, diarization_segments):
    """Комбинирует транскрипцию с диаризацией"""
    result = []

    # Для каждого сегмента диаризации находим соответствующие текстовые сегменты
    for d_seg in diarization_segments:
        speaker = d_seg["speaker"]
        d_start = d_seg["start"]
        d_end = d_seg["end"]
        speaker_text = ""

        for t_seg in transcript_segments:
            t_start = t_seg["start"]
            t_end = t_seg["end"]

            # Проверяем пересечение временных интервалов
            if t_end >= d_start and t_start <= d_end:
                speaker_text += t_seg["text"] + " "

        result.append({
            "speaker": speaker,
            "start": d_start,
            "end": d_end,
            "text": speaker_text.strip()
        })

    return result


async def process_audio_file(audio_path: str, progress_callback: callable = None) -> list[dict]:
    """Обрабатывает аудиофайл с улучшенной обработкой ошибок"""
    fragments = []
    try:
        fragments = AudioProcessor.split_audio(audio_path)
        all_segments = []
        total = len(fragments)
        processed_fragments = 0

        for i, fragment_path in enumerate(fragments):
            time_offset = i * SEGMENT_DURATION

            try:
                # Параллельная обработка с таймаутом
                transcript_data, diarization_data = await asyncio.wait_for(
                    asyncio.gather(
                        send_file_to_api(fragment_path, TRANSCRIBE_API_URL),
                        send_file_to_api(fragment_path, DIARIZATION_API_URL)
                    ),
                    timeout=API_TIMEOUT
                )

                # Проверка ошибок
                if "error" in transcript_data:
                    raise ValueError(f"Transcription error: {transcript_data['error']}")
                if "error" in diarization_data:
                    raise ValueError(f"Diarization error: {diarization_data['error']}")

                # Корректировка временных меток
                for segment in transcript_data.get("segments", []):
                    segment["start"] += time_offset
                    segment["end"] += time_offset

                for segment in diarization_data.get("diarization", []):
                    segment["start"] += time_offset
                    segment["end"] += time_offset

                # Комбинирование результатов
                combined = combine_transcript_with_diarization(
                    transcript_data.get("segments", []),
                    diarization_data.get("diarization", [])
                )
                all_segments.extend(combined)
                processed_fragments += 1

                if progress_callback and total > 0:
                    await progress_callback(processed_fragments / total)

            except Exception as e:
                logger.error(f"Ошибка обработки фрагмента {i}: {str(e)}")
                continue

        return merge_consecutive_segments(all_segments)

    except asyncio.TimeoutError:
        logger.error("Таймаут обработки аудио")
        return []
    except Exception as e:
        logger.exception("Критическая ошибка обработки файла")
        return []
    finally:
        # Очистка временных файлов
        if audio_path:
            AudioProcessor.cleanup([audio_path])
        if fragments:
            AudioProcessor.cleanup(fragments)


async def download_youtube_audio(url: str, progress_callback: callable = None) -> str:
    """Скачивание YouTube аудио с поддержкой прогресса"""
    loop = asyncio.get_running_loop()

    # Создаем очередь для передачи данных о прогрессе
    progress_queue = asyncio.Queue()

    def progress_hook(data):
        """Хук для прогресса, работающий в синхронном контексте"""
        if data['status'] == 'downloading' and progress_callback:
            # Помещаем данные в очередь вместо прямого вызова
            loop.call_soon_threadsafe(progress_queue.put_nowait, data)

    def sync_download():
        temp_dir = tempfile.gettempdir()
        unique_id = str(uuid.uuid4())
        outtmpl = os.path.join(temp_dir, f"{unique_id}.%(ext)s")

        # Проверка пути к ffmpeg
        ffmpeg_path = os.path.join(FFMPEG_DIR, "ffmpeg.exe")
        if not os.path.exists(ffmpeg_path):
            raise FileNotFoundError(f"FFmpeg не найден по пути: {ffmpeg_path}")

        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": outtmpl,
            "ffmpeg_location": FFMPEG_DIR,
            "progress_hooks": [progress_hook],
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
            }],
            "quiet": True,
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                return ydl.prepare_filename(info).replace(".webm", ".mp3").replace(".m4a", ".mp3")
        except Exception as e:
            logger.error(f"Ошибка скачивания YouTube: {str(e)}")
            raise RuntimeError(f"Ошибка скачивания видео: {str(e)}") from e

    try:
        # Запускаем скачивание в отдельном потоке
        download_task = loop.run_in_executor(None, sync_download)

        # Запускаем обработчик прогресса параллельно
        async def process_progress():
            while True:
                try:
                    # Ждем данные о прогрессе с таймаутом
                    data = await asyncio.wait_for(progress_queue.get(), timeout=1.0)
                    if progress_callback:
                        await progress_callback(data)
                except asyncio.TimeoutError:
                    # Проверяем завершена ли загрузка
                    if download_task.done():
                        break
                except Exception as e:
                    logger.warning(f"Ошибка обработки прогресса: {str(e)}")
                    break

        # Запускаем обе задачи параллельно
        _, result = await asyncio.gather(
            process_progress(),
            download_task
        )

        return result

    except Exception as e:
        logger.error(f"Ошибка в асинхронном скачивании: {str(e)}")
        raise


import time

# Глобальный словарь для хранения времени последнего обновления
LAST_UPDATE_TIMES = {}


async def update_progress(progress: float, message: Message, lang: str):
    """Обновляет сообщение с прогресс-баром с троттлингом"""
    try:
        # Троттлинг: не чаще 1 раза в 2 секунды
        current_time = time.time()
        last_update = LAST_UPDATE_TIMES.get(message.message_id, 0)

        if current_time - last_update < 2.0 and progress < 1.0:
            return

        bar_length = 10
        filled = int(progress * bar_length)
        filled_char = '🟪'
        empty_char = '⬜'
        bar = filled_char * filled + empty_char * (bar_length - filled)
        percent = int(progress * 100)

        base_text = get_string('processing_audio', lang)
        text = base_text.format(bar=bar, percent=percent)

        await message.edit_text(text)
        LAST_UPDATE_TIMES[message.message_id] = current_time

    except telegram.error.BadRequest as e:
        if "Message is not modified" in str(e):
            pass  # Игнорируем ошибку неизмененного сообщения
        else:
            logger.warning(f"Ошибка обновления прогресса: {str(e)}")
    except Exception as e:
        logger.warning(f"Не удалось обновить прогресс: {str(e)}")


# Обновляем обработчик команды /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start с автоматическим определением языка"""
    user = update.effective_user
    lang = user.language_code if user and user.language_code else 'ru'
    if lang not in locales:
        lang = 'ru'
    context.user_data['lang'] = lang

    welcome_text = get_string('welcome', lang)
    await update.message.reply_text(welcome_text)

    # =============== ДОБАВЛЕНО: Обработчик нажатия на кнопку ===============


# Обновляем обработчик кнопки
async def new_audio_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки с учетом языка"""
    query = update.callback_query
    await query.answer()
    lang = context.user_data.get('lang', 'ru')
    await query.message.reply_text(get_string('new_audio_prompt', lang))


def format_text_without_speakers(segments: list[dict]) -> str:
    """Форматирует результаты без упоминания спикеров"""
    return "\n\n".join(seg["text"] for seg in segments)


# ====================================================================================


# Обновляем обработчик текстовых сообщений
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений с определением языка"""
    url = update.message.text.strip()

    # Определяем язык сообщения
    try:
        lang = detect(url)
        if lang not in locales:
            lang = context.user_data.get('lang', 'ru')
    except LangDetectException:
        lang = context.user_data.get('lang', 'ru')
    context.user_data['lang'] = lang

    if not url.startswith("http"):
        await update.message.reply_text(get_string('invalid_link', lang))
        return

    # Создаем сообщение с прогрессом
    progress_message = await update.message.reply_text(
        get_string('downloading_video', lang).format(bar='', percent='0%')
    )

    try:
        # Обновляем прогресс при скачивании
        async def download_progress(d):
            if d.get('status') == 'downloading':
                percent = d.get('_percent_str', '0%')
                filled_char = '🟪'
                empty_char = '⬜'

                try:
                    percent_value = float(percent.strip().replace('%', ''))
                    filled = int(percent_value / 10)
                    bar = filled_char * filled + empty_char * (10 - filled)
                    text = get_string('downloading_video', lang).format(bar=bar, percent=percent)
                    await progress_message.edit_text(text)
                except:
                    text = get_string('downloading_video', lang).format(bar='', percent=percent)
                    await progress_message.edit_text(text)

        audio_path = await download_youtube_audio(url, progress_callback=download_progress)

        # Обновляем сообщение для этапа обработки
        await progress_message.edit_text(
            get_string('processing_audio', lang).format(bar='', percent='0')
        )

        # Обработка с прогрессом
        results = await process_audio_file(
            audio_path,
            progress_callback=lambda p: update_progress(p, progress_message, lang)
        )

        if not results:
            await progress_message.edit_text(get_string('no_speech', lang))
            return

        full_text_with_speakers = format_results(results)
        full_text_plain = format_text_without_speakers(results)

        # Сохраняем два PDF
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as pdf_file1, \
                tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as pdf_file2:

            pdf_path_with_speakers = pdf_file1.name
            pdf_path_plain = pdf_file2.name

            save_text_to_pdf(full_text_with_speakers, pdf_path_with_speakers)
            save_text_to_pdf(full_text_plain, pdf_path_plain)

        # Отправляем оба файла
        with open(pdf_path_with_speakers, 'rb') as f1, open(pdf_path_plain, 'rb') as f2:
            await update.message.reply_document(
                f1,
                filename="transcription_with_speakers.pdf",
                caption=get_string('caption_with_speakers', lang)
            )
            await update.message.reply_document(
                f2,
                filename="transcription_plain.pdf",
                caption=get_string('caption_plain', lang)
            )

        # Удаляем временные файлы
        os.remove(pdf_path_with_speakers)
        os.remove(pdf_path_plain)

        # Финальное сообщение
        await update.message.reply_text(get_string('done', lang))

    except Exception as e:
        error_text = get_string('error', lang).format(error=str(e))
        try:
            await progress_message.edit_text(error_text)
        except telegram.error.BadRequest:
            await update.message.reply_text(error_text)
        except Exception as edit_error:
            logger.error(f"Ошибка редактирования: {str(edit_error)}")
            await update.message.reply_text(error_text)


        except asyncio.TimeoutError:
            error_text = get_string('timeout_error', lang)
            await progress_message.edit_text(error_text)
            logger.error("Таймаут обработки запроса")
        except telegram.error.TimedOut:
            error_text = get_string('telegram_timeout', lang)
            await update.message.reply_text(error_text)
            logger.error("Таймаут Telegram API")
        except Exception as e:
            error_text = get_string('error', lang).format(error=str(e))
            try:
                await progress_message.edit_text(error_text)
            except Exception:
                await update.message.reply_text(error_text)
            logger.exception("Ошибка обработки ссылки")

        await update.message.reply_text(get_string('try_again', lang))



        await update.message.reply_text(
            get_string('try_again', lang),
        )
        logger.exception("Ошибка обработки ссылки")


# Обновляем обработчик файлов
async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик медиа-файлов с определением языка"""
    # Определяем язык по подписи или предыдущим настройкам
    lang = context.user_data.get('lang', 'ru')
    if update.message.caption:
        try:
            caption_lang = detect(update.message.caption)
            if caption_lang in locales:
                lang = caption_lang
        except LangDetectException:
            pass
    context.user_data['lang'] = lang

    file_types = {
        update.message.audio: "audio",
        update.message.voice: "voice",
        update.message.video: "video",
        update.message.document: "document"
    }

    for file_source, file_type in file_types.items():
        if file_source:
            file = file_source
            break
    else:
        await update.message.reply_text(get_string('unsupported_format', lang))
        return

    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as temp_file:
        temp_path = temp_file.name

    progress_message = await update.message.reply_text(
        get_string('processing_audio', lang).format(bar='', percent='0%')
    )

    try:
        tg_file = await file.get_file()
        await tg_file.download_to_drive(temp_path)
        await update.message.reply_text(get_string('uploading_file', lang))

        results = await process_audio_file(
            temp_path,
            progress_callback=lambda p: update_progress(p, progress_message, lang)
        )

        if not results:
            await progress_message.edit_text(get_string('no_speech', lang))
            return

        full_text_with_speakers = format_results(results)
        full_text_plain = format_text_without_speakers(results)

        # Сохраняем два PDF
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as pdf_file1, \
                tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as pdf_file2:

            pdf_path_with_speakers = pdf_file1.name
            pdf_path_plain = pdf_file2.name

            save_text_to_pdf(full_text_with_speakers, pdf_path_with_speakers)
            save_text_to_pdf(full_text_plain, pdf_path_plain)

        # Отправляем оба файла
        with open(pdf_path_with_speakers, 'rb') as f1, open(pdf_path_plain, 'rb') as f2:
            await update.message.reply_document(
                f1,
                filename="transcription_with_speakers.pdf",
                caption=get_string('caption_with_speakers', lang)
            )
            await update.message.reply_document(
                f2,
                filename="transcription_plain.pdf",
                caption=get_string('caption_plain', lang)
            )

        # Удаляем временные файлы
        os.remove(pdf_path_with_speakers)
        os.remove(pdf_path_plain)

        # Финальное сообщение
        await update.message.reply_text(get_string('done', lang))

    except Exception as e:
        error_text = get_string('error', lang).format(error=str(e))
        await progress_message.edit_text(error_text)
        logger.exception("Ошибка обработки файла")
    finally:
        AudioProcessor.cleanup([temp_path])

    import logging
    logging.basicConfig(level=logging.DEBUG)


def main():
    """Основная функция запуска бота"""
    app = ApplicationBuilder().token("7295836546:AAGWYalfQ6pkkCRPIK6LcegMDBFFM5SjAN0") \
        .read_timeout(60) \
        .write_timeout(60) \
        .pool_timeout(60) \
        .build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(
        MessageHandler(filters.AUDIO | filters.VOICE | filters.VIDEO | filters.Document.AUDIO | filters.Document.VIDEO,
                       handle_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # =============== ДОБАВЛЕНО: Обработчик кнопки ===============

    app.add_handler(CallbackQueryHandler(new_audio_handler, pattern='^new_audio$'))

    logger.info("Бот запущен")
    app.run_polling()


if __name__ == "__main__":
    main()
