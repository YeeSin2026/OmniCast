"""声音样本管理 — 参考音频的保存、读取、状态查询。

参考音频只需上传一次，CosyVoice 零样本克隆会复用它。
"""

import json
import logging
import os
from datetime import datetime, timezone

from .. import settings as config

logger = logging.getLogger(__name__)

SPEAKER_INFO_FILE = os.path.join(
    os.path.dirname(config.AUDIO_OUTPUT_DIR), "speaker.json"
)
# 约 ~/.omnicast/speaker.json


def get_reference_wav_path() -> str | None:
    """获取已保存的参考音频绝对路径，未配置返回 None。"""
    try:
        if os.path.exists(SPEAKER_INFO_FILE):
            with open(SPEAKER_INFO_FILE) as f:
                info = json.load(f)
            wav_path = info.get("wav_path", "")
            if wav_path and os.path.exists(wav_path):
                return wav_path
    except (json.JSONDecodeError, IOError) as e:
        logger.warning("读取 speaker.json 失败: %s", e)
    return None


def get_prompt_text() -> str:
    """获取参考音频的文本转录（用于提高克隆质量）。"""
    try:
        if os.path.exists(SPEAKER_INFO_FILE):
            with open(SPEAKER_INFO_FILE) as f:
                info = json.load(f)
            return info.get("prompt_text", "")
    except Exception:
        pass
    return ""


def save_reference_audio(
    wav_bytes: bytes,
    prompt_text: str = "",
    filename: str = "reference_speaker.wav",
) -> str | None:
    """保存上传的参考音频和说话人元数据。

    Args:
        wav_bytes: 原始 WAV 文件字节
        prompt_text: 参考音频中说的话语（用于提高克隆质量）
        filename: 保存的文件名

    Returns:
        保存的绝对路径，失败返回 None
    """
    try:
        audio_dir = config.AUDIO_OUTPUT_DIR
        os.makedirs(audio_dir, exist_ok=True)

        wav_path = os.path.join(audio_dir, filename)
        with open(wav_path, "wb") as f:
            f.write(wav_bytes)

        # 估算时长
        duration = _estimate_duration(wav_bytes)

        speaker_info = {
            "wav_path": wav_path,
            "prompt_text": prompt_text,
            "duration_sec": duration,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        os.makedirs(os.path.dirname(SPEAKER_INFO_FILE), exist_ok=True)
        with open(SPEAKER_INFO_FILE, "w") as f:
            json.dump(speaker_info, f, ensure_ascii=False, indent=2)

        logger.info(
            "参考音频已保存: %s (%d bytes, ~%.1fs)",
            wav_path, len(wav_bytes), duration,
        )
        return wav_path

    except Exception as e:
        logger.error("保存参考音频失败: %s", e)
        return None


def has_reference_audio() -> bool:
    """是否已配置参考音频。"""
    return get_reference_wav_path() is not None


def get_speaker_status() -> dict:
    """返回说话人配置状态摘要，供前端展示。"""
    wav_path = get_reference_wav_path()
    if not wav_path:
        return {"configured": False}

    status = {"configured": True}
    try:
        if os.path.exists(SPEAKER_INFO_FILE):
            with open(SPEAKER_INFO_FILE) as f:
                info = json.load(f)
            status.update({
                "prompt_text": info.get("prompt_text", ""),
                "duration_sec": info.get("duration_sec", 0),
                "created_at": info.get("created_at", ""),
            })
    except Exception:
        pass

    return status


def _estimate_duration(wav_bytes: bytes) -> float:
    """粗略估算 WAV 音频时长（假设 16-bit 单声道 WAV）。"""
    try:
        # WAV 文件头通常 44 字节，后面是 PCM 数据
        data_size = len(wav_bytes) - 44
        if data_size > 0:
            return round(data_size / (22050 * 2), 1)
    except Exception:
        pass
    return 0.0
