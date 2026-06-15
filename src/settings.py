"""OmniCast 配置 — 环境变量为默认值，Web UI 设置覆盖写入 config.json。"""

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# ── 配置文件路径 ──
CONFIG_FILE = Path(os.environ.get("OMNICAST_CONFIG_FILE", "/data/config.json"))


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


# ── 从配置文件读取覆写值 ──
def _load_config_overrides() -> dict:
    """读取 config.json 中的用户设置。"""
    try:
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def save_config(data: dict) -> bool:
    """保存用户配置到文件，并立即刷新模块级变量。"""
    global LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, OMNIVAULT_API_URL
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        clean = {k: v for k, v in data.items() if v}
        with open(CONFIG_FILE, 'w') as f:
            json.dump(clean, f, ensure_ascii=False, indent=2)
        _overrides.clear()
        _overrides.update(clean)

        # 立即刷新模块变量
        LLM_API_KEY = _get("LLM_API_KEY", "LLM_API_KEY", "")
        LLM_BASE_URL = _get("LLM_BASE_URL", "LLM_BASE_URL", "https://api.deepseek.com")
        LLM_MODEL = _get("LLM_MODEL", "LLM_MODEL", "deepseek-chat")
        OMNIVAULT_API_URL = _get("OMNIVAULT_API_URL", "OMNIVAULT_API_URL", "http://localhost:8080")

        logger.info("配置已保存并刷新: %s", list(clean.keys()))
        return True
    except Exception as e:
        logger.error("保存配置失败: %s", e)
        return False


def is_configured() -> bool:
    """检查是否已完成初始配置（LLM key 必须设置）。"""
    return bool(LLM_API_KEY)


# ── 运行时覆写（启动时加载一次）──
_overrides: dict = {}
_overrides = _load_config_overrides()


def _get(key: str, env_key: str, default: str = "") -> str:
    """优先级：config.json > 环境变量 > 默认值。"""
    if key in _overrides and _overrides[key]:
        return _overrides[key]
    return _env(env_key, default)


# ═══════════════════════════════════════
#  配置项
# ═══════════════════════════════════════

LLM_API_KEY = _get("LLM_API_KEY", "LLM_API_KEY", "")
LLM_BASE_URL = _get("LLM_BASE_URL", "LLM_BASE_URL", "https://api.deepseek.com")
LLM_MODEL = _get("LLM_MODEL", "LLM_MODEL", "deepseek-chat")

OMNIVAULT_API_URL = _get("OMNIVAULT_API_URL", "OMNIVAULT_API_URL", "http://localhost:8080")

DB_PATH = _get("OMNICAST_DB_PATH", "OMNICAST_DB_PATH", os.path.expanduser("~/.omnicast/drafts.db"))

COSYVOICE_API_URL = _get("COSYVOICE_API_URL", "COSYVOICE_API_URL", "http://localhost:50000")
AUDIO_OUTPUT_DIR = _get("AUDIO_OUTPUT_DIR", "AUDIO_OUTPUT_DIR", os.path.expanduser("~/.omnicast/audio"))

AUTO_TRASH_DAYS = int(_get("AUTO_TRASH_DAYS", "AUTO_TRASH_DAYS", "7"))

HOST = _get("HOST", "HOST", "0.0.0.0")
PORT = int(_get("PORT", "PORT", "8081"))


def get_all() -> dict:
    """返回当前所有配置（掩码处理敏感字段）。"""
    return {
        "LLM_API_KEY": _mask(LLM_API_KEY),
        "LLM_BASE_URL": LLM_BASE_URL,
        "LLM_MODEL": LLM_MODEL,
        "OMNIVAULT_API_URL": OMNIVAULT_API_URL,
    }


def _mask(val: str) -> str:
    """掩码敏感字段，如 sk-xxx...abc。"""
    if not val or len(val) < 8:
        return val
    return val[:4] + "***" + val[-4:]
