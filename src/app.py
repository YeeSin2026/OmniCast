"""OmniCast FastAPI 应用 — 独立于 OmniVault。"""

import logging
from pathlib import Path

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader

from . import settings as config
from .activation import is_activated, activate as do_activate

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


# ═══════════════════════════════════════════
#  激活码中间件
# ═══════════════════════════════════════════

@app.middleware("http")
async def activation_middleware(request: Request, call_next):
    """未激活 → 激活页；已激活未配置 → 配置页。"""
    # 始终放行的路径
    always_allowed = ["/favicon.ico"]
    if any(request.url.path == p for p in always_allowed):
        return await call_next(request)
    if request.url.path.startswith("/static"):
        return await call_next(request)

    # 未激活 → 只放行激活相关
    if not is_activated():
        if request.url.path in ("/activate", "/api/activate"):
            return await call_next(request)
        if request.url.path.startswith("/api/"):
            from fastapi.responses import JSONResponse
            return JSONResponse(
                {"error": "activation_required", "message": "请先激活 · Activation required"},
                status_code=403,
            )
        return HTMLResponse(_render("activate.html"), status_code=403)

    # 已激活但未配置 → 只放行配置相关
    if not config.is_configured():
        if request.url.path in ("/setup", "/api/setup"):
            return await call_next(request)
        return RedirectResponse("/setup", status_code=302)

    return await call_next(request)


# ── 激活路由（在注册其他路由之前）──

@app.get("/activate")
async def activate_page(request: Request):
    if is_activated():
        return RedirectResponse("/")
    return HTMLResponse(_render("activate.html"))


@app.post("/api/activate")
async def activate_api(key: str = Form(...)):
    if is_activated():
        return {"ok": True, "message": "已激活"}
    if do_activate(key):
        return {"ok": True, "message": "激活成功"}
    return {"ok": False, "error": "激活码无效 · Invalid key"}


# ── 配置路由 ──

@app.get("/setup")
async def setup_page(request: Request):
    """Web 配置页面。"""
    from .activation import is_activated as _activated
    return HTMLResponse(_render("setup.html", config=config.get_all(), activated=_activated()))


@app.post("/api/setup")
async def setup_save_api(data: dict):
    """保存配置。"""
    allowed_keys = {"LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "OMNIVAULT_API_URL"}
    filtered = {k: v for k, v in data.items() if k in allowed_keys and v}
    if not filtered.get("LLM_API_KEY"):
        return {"ok": False, "error": "LLM API Key 不能为空"}
    ok = config.save_config(filtered)
    return {"ok": ok}


# ── 注册 Agent 路由 ──
from .social_agent.routes import register_routes
register_routes(app, jinja_env, _render)

# ── 注册 VoiceOver 路由（TTS 声音克隆） ──
from .voiceover.routes import register_routes as register_voiceover_routes
register_voiceover_routes(app, jinja_env, _render)

logger.info(f"OmniCast 启动，OmniVault 地址: {config.OMNIVAULT_API_URL}")
