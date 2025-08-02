# !pip install fastapi uvicorn pyngrok python-multipart nest_asyncio
# !apt-get install -y ffmpeg
# !pip install openai-whisper resemblyzer pydub scikit-learn
# !pip install  pyngrok
# !pip install requests==2.32.3
#
# %%writefile Transcriber_API.py
# # Transcriber_API.py
# from fastapi import FastAPI, UploadFile, File
# import whisper
# import tempfile
# import shutil
#
# app = FastAPI()
# model = whisper.load_model("base")  # можно tiny, small и т.д.
#
#
# @app.post("/transcribe")
# async def transcribe_audio(file: UploadFile = File(...)):
#     with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
#         shutil.copyfileobj(file.file, tmp)
#         tmp_path = tmp.name
#
#     result = model.transcribe(tmp_path, word_timestamps=True)
#
#     # Возвращаем список сегментов (с текстом и временем)
#     return {"segments": result["segments"]}
#
#
# %%writefile diarization_api.py
# from fastapi import FastAPI, File, UploadFile
# from fastapi.responses import JSONResponse
# from resemblyzer import VoiceEncoder, preprocess_wav
# from sklearn.cluster import KMeans
# from pydub import AudioSegment
# import numpy as np
# import tempfile
# import uvicorn
# import os
#
# app = FastAPI()
#
# @app.post("/diarize")
# async def diarize(file: UploadFile = File(...)):
#     try:
#         # Сохраняем файл во временную директорию
#         with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
#             tmp.write(await file.read())
#             tmp_path = tmp.name
#
#         # Загружаем аудио с помощью pydub
#         audio = AudioSegment.from_file(tmp_path)
#         chunk_length_ms = 1000 * 5  # 5 секунд
#         chunks = [chunk for chunk in audio[::chunk_length_ms]]
#
#         encoder = VoiceEncoder()
#         embeddings = []
#
#         # Обрабатываем каждый кусочек
#         for i, chunk in enumerate(chunks):
#             with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as chunk_file:
#                 chunk.export(chunk_file.name, format="wav")
#                 wav = preprocess_wav(chunk_file.name)
#                 embed = encoder.embed_utterance(wav)
#                 embeddings.append(embed)
#                 os.remove(chunk_file.name)
#
#         # Кластеризация голосов
#         n_speakers = 2  # или 3 — зависит от твоих ожиданий
#         kmeans = KMeans(n_clusters=n_speakers)
#         labels = kmeans.fit_predict(embeddings)
#
#         result = []
#         for i, label in enumerate(labels):
#             start = i * 5
#             end = start + 5
#             result.append({
#                 "speaker": int(label),
#                 "start": start,
#                 "end": end
#             })
#
#         os.remove(tmp_path)
#
#         return JSONResponse(content={"diarization": result})
#
#     except Exception as e:
#         return JSONResponse(status_code=500, content={"error": str(e)})
#
# # Запуск внутри Google Colab:
# if __name__ == "__main__":
#     import nest_asyncio
#     from pyngrok import ngrok
#     nest_asyncio.apply()
#
#     port = 8000
#     public_url = ngrok.connect(port)
#     print("🔗 Публичный URL:", public_url)
#     uvicorn.run(app, port=port)



# import threading
# import subprocess
# import time
# from pyngrok import ngrok
# import nest_asyncio
# import os
#
# nest_asyncio.apply()
#
# # Запуск сервиса через uvicorn
# def run_service(module: str, port: int):
#     os.environ["PYTHONUNBUFFERED"] = "1"
#     subprocess.run([
#         "uvicorn",
#         f"{module}:app",
#         "--host", "0.0.0.0",
#         "--port", str(port),
#         "--reload"
#     ])
#
# # Запуск в отдельных потоках
# threading.Thread(target=run_service, args=("Transcriber_API", 8000), daemon=True).start()
# threading.Thread(target=run_service, args=("diarization_api", 8001), daemon=True).start()
#
# # Ожидание запуска
# time.sleep(5)
#
# # Установи свой токен ngrok
# ngrok.set_auth_token("30hT2ZW6MuDsKynhnkho5ZaDLAv_5xckLPwXAKb3Uz9tJn8xf")
#
# transcribe_tunnel = ngrok.connect(8000)
# diarization_tunnel = ngrok.connect(8001)
#
# print("Сервисы запущены:")
# print("Transcribe API:", transcribe_tunnel.public_url)
# print("Diarization API:", diarization_tunnel.public_url)
