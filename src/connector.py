"""OmniVault API 连接器 — 读取知识条目。"""

import logging
import httpx
from . import settings as config

logger = logging.getLogger(__name__)


async def list_entries(search: str = "", limit: int = 30) -> list[dict]:
    """从 OmniVault 获取知识条目列表。"""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            params = {"limit": limit}
            if search:
                params["search"] = search
            resp = await client.get(
                f"{config.OMNIVAULT_API_URL}/api/videos",
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("items", data) if isinstance(data, dict) else data
    except Exception as e:
        logger.warning(f"获取条目列表失败: {e}")
        return []


async def get_entry(entry_id: int):  # -> dict | None (py3.10+)
    """获取单条知识条目详情。"""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{config.OMNIVAULT_API_URL}/api/videos/{entry_id}"
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.warning(f"获取条目 {entry_id} 失败: {e}")
        return None


async def submit_url(url: str) -> dict | None:
    """向 OmniVault 提交 URL 进行内容采集和总结。"""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{config.OMNIVAULT_API_URL}/api/submit",
                data={"urls": url},
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.warning(f"提交 URL 到 OmniVault 失败: {e}")
        return None


async def get_job_status(job_id: str) -> dict | None:
    """查询 OmniVault 任务处理状态。"""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{config.OMNIVAULT_API_URL}/api/jobs/{job_id}"
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.warning(f"查询任务 {job_id} 状态失败: {e}")
        return None


async def health_check() -> bool:
    """检查 OmniVault 是否在线。"""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{config.OMNIVAULT_API_URL}/api/videos?limit=1")
            return resp.status_code == 200
    except Exception:
        return False


async def resolve_knowledge(query: str = "", entry_id: int = 0) -> dict:
    """调用 OmniVault Knowledge Resolve API，获取完整知识上下文。

    返回 Wiki 查询 + RAG 搜索 + 相关条目的合并结果。
    """
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            params = {}
            if query:
                params["q"] = query
            if entry_id:
                params["entry_id"] = entry_id
            resp = await client.get(
                f"{config.OMNIVAULT_API_URL}/api/agent/knowledge-resolve",
                params=params,
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.warning(f"Knowledge Resolve 调用失败: {e}")
        return {
            "status": "error",
            "coverage": "low",
            "wiki": {"answer": "", "sources": [], "pages": []},
            "rag": {"results": [], "total": 0},
            "related_entries": [],
        }


async def get_style_examples(platform: str = "", limit: int = 3) -> list[dict]:
    """从 OmniCast 本地数据库获取高分历史草稿作为风格参考。

    注意：这个方法读的是 OmniCast 自己的数据库，不调 OmniVault。
    但因为 connector 负责所有「数据获取」，放这里语义上统一。
    """
    try:
        from .social_agent.store import DraftStore
        store = DraftStore()
        return store.list_high_scored(platform=platform, limit=limit)
    except Exception as e:
        logger.warning(f"获取风格参考失败: {e}")
        return []
