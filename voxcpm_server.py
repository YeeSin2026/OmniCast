"""VoxCPM TTS HTTP 服务 — 轻量 FastAPI wrapper。

使用方式: python voxcpm_server.py --port 50000 --model openbmb/VoxCPM1.5 --device mps
"""

import argparse
import io
import logging
import os
import tempfile
import numpy as np
import soundfile as sf
import whisper
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("voxcpm-server")

app = FastAPI()
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

model = None
whisper_model = None
SAMPLE_RATE = 44100


def transcribe_audio(file_path: str) -> str:
    """用 Whisper 自动转写参考音频，作为 prompt_text。"""
    global whisper_model
    if whisper_model is None:
        logger.info("Loading Whisper tiny model for auto-transcription...")
        whisper_model = whisper.load_model("tiny")
    result = whisper_model.transcribe(file_path, language="zh")
    text = result["text"].strip()
    logger.info("Whisper 转写结果: %s", text[:80])
    return text


@app.get("/")
async def health():
    return {"status": "ok", "service": "voxcpm", "sample_rate": SAMPLE_RATE}


@app.post("/inference_zero_shot")
async def inference_zero_shot(
    tts_text: str = Form(""),
    prompt_text: str = Form(""),
    prompt_wav: UploadFile = File(...),
):
    """零样本语音克隆。prompt_text 为空时自动用 Whisper 转写参考音频。"""
    suffix = os.path.splitext(prompt_wav.filename or "audio.wav")[1] or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await prompt_wav.read())
        ref_path = tmp.name

    try:
        effective_prompt = prompt_text.strip() if prompt_text and prompt_text.strip() else ""
        if not effective_prompt:
            logger.info("prompt_text 为空，使用 Whisper 自动转写...")
            try:
                effective_prompt = transcribe_audio(ref_path)
            except Exception as e:
                logger.warning("Whisper 转写失败: %s", e)
                effective_prompt = "你好"

        if not effective_prompt:
            effective_prompt = "你好"

        logger.info("TTS: text=%d chars, prompt=「%s」", len(tts_text), effective_prompt[:50])
        wav = model.generate(
            text=tts_text,
            prompt_wav_path=ref_path,
            prompt_text=effective_prompt,
        )
        wav_int16 = (wav * 32767).clip(-32768, 32767).astype(np.int16)

        buf = io.BytesIO()
        sf.write(buf, wav_int16, SAMPLE_RATE, format="WAV", subtype="PCM_16")
        wav_bytes = buf.getvalue()

        logger.info("TTS done: %.1fs audio, %d bytes WAV", len(wav) / SAMPLE_RATE, len(wav_bytes))
        return Response(content=wav_bytes, media_type="audio/wav")

    except Exception as e:
        logger.error("TTS failed: %s", e, exc_info=True)
        return Response(content=b"", status_code=500)
    finally:
        os.unlink(ref_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=50000)
    parser.add_argument("--host", type=str, default="0.0.0.0")
    parser.add_argument("--model", type=str, default="openbmb/VoxCPM1.5")
    parser.add_argument("--device", type=str, default="mps")
    parser.add_argument("--no-denoiser", action="store_true", default=True)
    args = parser.parse_args()

    logger.info("Loading VoxCPM: %s (device=%s)...", args.model, args.device)
    from voxcpm import VoxCPM
    model = VoxCPM.from_pretrained(
        args.model, device=args.device, load_denoiser=not args.no_denoiser,
    )
    SAMPLE_RATE = model.tts_model.sample_rate
    logger.info("Ready. sample_rate=%d, port=%d", SAMPLE_RATE, args.port)

    uvicorn.run(app, host=args.host, port=args.port)
