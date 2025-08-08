# # === 1. Установка зависимостей ===
# !pip install fastapi uvicorn pyngrok git+https://github.com/openai/whisper.git resemblyzer pydub scikit-learn
# !ngrok config add-authtoken "310LpleNCae70UVJY8gr6p9WHy6_6K9Kqww3id5sm1Lc5Ferb"
# # === 2. Импорт модулей ===
# from fastapi import FastAPI, UploadFile, File
# import whisper
# import tempfile
# import shutil
# import os
# from resemblyzer import VoiceEncoder, preprocess_wav
# from sklearn.cluster import KMeans
# from pydub import AudioSegment
# from pydub.utils import make_chunks
# import numpy as np
# import uvicorn
# from pyngrok import ngrok
# import nest_asyncio
#
# # Для совместимости FastAPI с Colab
# nest_asyncio.apply()
#
# # === 3. Создание FastAPI приложения ===
# app = FastAPI()
#
# # === Транскрибация ===
# model = whisper.load_model("small")
#
# @app.post("/transcribe")
# async def transcribe_audio(file: UploadFile = File(...)):
#     suffix = os.path.splitext(file.filename)[1]
#     with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
#         shutil.copyfileobj(file.file, tmp)
#         tmp_path = tmp.name
#
#     result = model.transcribe(tmp_path)
#     os.remove(tmp_path)
#
#     return {
#         "text": result["text"],
#         "segments": result.get("segments", [])
#     }
#
# # === Диаризация ===
# CHUNK_LENGTH_MS = 5000
#
# @app.post("/diarize")
# async def diarize(file: UploadFile = File(...), n_speakers: int = 2):
#     with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as temp_audio:
#         contents = await file.read()
#         temp_audio.write(contents)
#         temp_audio_path = temp_audio.name
#
#     audio = AudioSegment.from_file(temp_audio_path)
#     chunks = make_chunks(audio, CHUNK_LENGTH_MS)
#
#     encoder = VoiceEncoder()
#     embeddings = []
#     chunk_timestamps = []
#
#     for i, chunk in enumerate(chunks):
#         with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as temp_chunk:
#             temp_chunk_path = temp_chunk.name
#             chunk.export(temp_chunk_path, format="wav")
#
#         try:
#             wav = preprocess_wav(temp_chunk_path)
#             if len(wav) == 0:
#                 continue
#             embed = encoder.embed_utterance(wav)
#             embeddings.append(embed)
#             chunk_timestamps.append({
#                 "start": round(i * CHUNK_LENGTH_MS / 1000, 1),
#                 "end": round((i + 1) * CHUNK_LENGTH_MS / 1000, 1)
#             })
#         finally:
#             os.remove(temp_chunk_path)
#
#     os.remove(temp_audio_path)
#     embeddings = np.array(embeddings)
#
#     kmeans = KMeans(n_clusters=n_speakers, random_state=0).fit(embeddings)
#     labels = kmeans.labels_
#
#     result = []
#     for ts, label in zip(chunk_timestamps, labels):
#         result.append({
#             "speaker": int(label) + 1,
#             "start": ts["start"],
#             "end": ts["end"]
#         })
#
#     return {"diarization": result}
#
# # === 4. Запуск сервера с ngrok ===
# # Открываем туннель
# public_url = ngrok.connect(8000)
# print("🚀 Публичный URL:", public_url)
#
# # Запуск FastAPI
# uvicorn.run(app, host="0.0.0.0", port=8000)
