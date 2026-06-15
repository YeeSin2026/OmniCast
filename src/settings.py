"""OmniCast 配置 — 独立于 OmniVault。"""

import os

# LLM
LLM_API_KEY = os.getenv("LLM_API_KEY", os.getenv("DEEPSEEK_API_KEY", ""))
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")

# OmniVault 连接
OMNIVAULT_API_URL = os.getenv("OMNIVAULT_API_URL", "http://localhost:8080")

# 数据库
DB_PATH = os.getenv("OMNICAST_DB_PATH", os.path.expanduser("~/.omnicast/drafts.db"))

# CosyVoice TTS
COSYVOICE_API_URL = os.getenv("COSYVOICE_API_URL", "http://localhost:50000")
AUDIO_OUTPUT_DIR = os.getenv("AUDIO_OUTPUT_DIR", os.path.expanduser("~/.omnicast/audio"))

# 回收站自动清理天数（0=不自动清理）
AUTO_TRASH_DAYS = int(os.getenv("AUTO_TRASH_DAYS", "7"))

# 服务
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8081"))
