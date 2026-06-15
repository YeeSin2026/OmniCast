"""Mock CosyVoice TTS 服务器 — 用于测试 OmniCast voiceover 集成。

返回一段简单的正弦波音频，模拟 CosyVoice 的 API 响应格式：
- POST /inference_zero_shot 接受 tts_text + prompt_text + prompt_wav
- 返回 int16 PCM 字节流

使用方法：python mock_tts_server.py --port 50000
"""

import argparse
import numpy as np
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SAMPLE_RATE = 22050


def generate_sine_wave(
    text: str = "Hello",
    frequency: float = 220.0,
    duration_sec: float = 3.0,
) -> bytes:
    """生成一段带音调变化的正弦波，模拟语音音高变化。"""
    # 基于文本长度估算时长（每字约 0.4 秒）
    duration = max(2.0, len(text) * 0.35)

    t = np.linspace(0, duration, int(SAMPLE_RATE * duration), endpoint=False)
    # 模拟语音的基频变化（上升）
    freq_start = 180
    freq_end = 240
    freq = freq_start + (freq_end - freq_start) * t / duration
    phase = 2 * np.pi * np.cumsum(freq) / SAMPLE_RATE
    audio = 0.3 * np.sin(phase)
    # 加衰减包络
    envelope = np.ones_like(t)
    attack = int(0.05 * len(t))
    release = int(0.1 * len(t))
    envelope[:attack] = np.linspace(0, 1, attack)
    envelope[-release:] = np.linspace(1, 0, release)
    audio *= envelope
    # 转 int16
    return (audio * 32767).astype(np.int16).tobytes()


@app.get("/")
async def health():
    return {"status": "ok", "service": "mock-cosyvoice", "version": "test"}


@app.post("/inference_zero_shot")
@app.get("/inference_zero_shot")
async def inference_zero_shot(
    tts_text: str = Form(""),
    prompt_text: str = Form(""),
    prompt_wav: UploadFile = File(None),
):
    """返回模拟的语音音频。"""
    duration = max(2.0, len(tts_text) * 0.35)
    print(f"[mock] TTS request: text={tts_text[:50]}... ({len(tts_text)} chars)")
    print(f"[mock] Generating {duration:.1f}s sine wave audio")

    pcm_bytes = generate_sine_wave(tts_text)

    def stream_audio():
        yield pcm_bytes

    return StreamingResponse(
        stream_audio(),
        media_type="application/octet-stream",
        headers={"Content-Length": str(len(pcm_bytes))},
    )


@app.post("/inference_cross_lingual")
@app.get("/inference_cross_lingual")
async def inference_cross_lingual(
    tts_text: str = Form(""),
    prompt_wav: UploadFile = File(None),
):
    """跨语种克隆 — 同样返回模拟音频。"""
    pcm_bytes = generate_sine_wave(tts_text)
    return StreamingResponse(
        iter([pcm_bytes]),
        media_type="application/octet-stream",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=50000)
    parser.add_argument("--host", type=str, default="0.0.0.0")
    args = parser.parse_args()
    print(f"🎭 Mock CosyVoice TTS server starting on {args.host}:{args.port}")
    print(f"   Endpoints: POST /inference_zero_shot, /inference_cross_lingual")
    print(f"   Returns: int16 PCM sine wave @ {SAMPLE_RATE}Hz")
    uvicorn.run(app, host=args.host, port=args.port)
