"""VoxCPM TTS HTTP 客户端 — 异步调用本地 VoxCPM 服务。

参考 src/connector.py 的异步 httpx 模式。
VoxCPM 服务直接返回 WAV 字节，无需 PCM 转换。
"""

import logging
import httpx
from .. import settings as config

logger = logging.getLogger(__name__)

INFERENCE_URL = f"{config.COSYVOICE_API_URL}/inference_zero_shot"


async def synthesize_speech(
    tts_text: str,
    prompt_text: str,
    prompt_wav_path: str,
    timeout: int = 300,
) -> bytes | None:
    """调用 VoxCPM 零样本语音克隆合成。

    Args:
        tts_text: 待合成的文案内容
        prompt_text: 参考音频的文本转录（可为空）
        prompt_wav_path: 参考音频文件路径（本地绝对路径）
        timeout: 请求超时秒数

    Returns:
        WAV 音频字节，失败返回 None
    """
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            with open(prompt_wav_path, "rb") as f:
                files = {"prompt_wav": f}
                data = {
                    "tts_text": tts_text,
                    "prompt_text": prompt_text,
                }
                resp = await client.post(INFERENCE_URL, data=data, files=files)

            if resp.status_code != 200:
                logger.error("VoxCPM HTTP 错误 %d", resp.status_code)
                return None

            wav_data = resp.content

            if not wav_data or len(wav_data) < 100:
                logger.warning("VoxCPM 返回音频过短: %d bytes", len(wav_data) if wav_data else 0)
                return None

            logger.info(
                "VoxCPM 合成成功: %d 字文本 → %d bytes WAV",
                len(tts_text), len(wav_data),
            )
            return wav_data

    except httpx.ConnectError:
        logger.error("无法连接 VoxCPM 服务，请确认 voxcpm_server.py 已启动在 %s", config.COSYVOICE_API_URL)
    except httpx.TimeoutException:
        logger.error("VoxCPM TTS 请求超时 (%ds)", timeout)
    except FileNotFoundError:
        logger.error("参考音频文件不存在: %s", prompt_wav_path)
    except Exception as e:
        logger.error("VoxCPM TTS 调用异常: %s", e)

    return None


async def check_health() -> bool:
    """检查 VoxCPM 服务是否可达。"""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{config.COSYVOICE_API_URL}/")
            return resp.status_code < 500
    except Exception:
        return False
