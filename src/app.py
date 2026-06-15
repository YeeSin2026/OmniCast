"""OmniCast FastAPI 应用 — 独立于 OmniVault。"""

import json
import logging
import os
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from jinja2 import Environment, FileSystemLoader

from . import settings as config

# ── 日志 ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger("omnicast")

# ── FastAPI ──
app = FastAPI(title="OmniCast", version="1.0.0")

TEMPLATE_DIR = Path(__file__).parent / "templates"
jinja_env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=True)


def _render(name: str, **kwargs) -> str:
    return jinja_env.get_template(name).render(**kwargs)


# ── 注册 Agent 路由 ──
from .social_agent.routes import register_routes
register_routes(app, jinja_env, _render)

# ── 注册 VoiceOver 路由（TTS 声音克隆） ──
from .voiceover.routes import register_routes as register_voiceover_routes
register_voiceover_routes(app, jinja_env, _render)

logger.info(f"OmniCast 启动，OmniVault 地址: {config.OMNIVAULT_API_URL}")
