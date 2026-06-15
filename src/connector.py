"""OmniCast 数据连接器 — 本地提取 + OmniVault 深度采集。

提取策略：图文类本地抓取，视频类提交 OmniVault 后异步轮询（不阻塞）。
"""

import asyncio
import logging
import threading
import time

import httpx

from . import settings as config

logger = logging.getLogger(__name__)

# ── 提取 job 跟踪（内存）──
_EXTRACT_JOBS: dict[str, dict] = {}

# ── 需要深度采集的平台 ──
DEEP_EXTRACT_PLATFORMS = {
    "douyin", "kuaishou", "bilibili", "youtube", "tiktok",
    "xiaohongshu", "instagram", "weibo", "twitch",
}


async def submit_url(url: str) -> dict | None:
    """提交 URL 进行内容提取。视频类走 OmniVault（异步轮询），图文类本地提取。

    Returns:
        {"jobs": [{"job_id": "xxx"}]}
    """
    import uuid

    from .content_extractor import _detect_platform
    from .social_agent.store import DraftStore

    store = DraftStore()
    platform = _detect_platform(url)

    # 检查是否已提取过
    try:
        conn = store._get_conn()
        row = conn.execute(
            "SELECT id, raw_content FROM extracted_content WHERE source_url=?",
            (url,),
        ).fetchone()
        conn.close()
        if row and row["raw_content"]:
            job_id = uuid.uuid4().hex[:12]
            _EXTRACT_JOBS[job_id] = {
                "status": "done",
                "result": {
                    "entry_id": row["id"],
                    "title": "",
                    "summary_preview": "",
                },
            }
            return {"jobs": [{"job_id": job_id, "cached": True}]}
    except Exception:
        pass

    job_id = uuid.uuid4().hex[:12]
    is_video = platform in DEEP_EXTRACT_PLATFORMS

    if is_video:
        # ── 视频平台：提交 OmniVault，异步轮询 ──
        ov_job_id = await _submit_to_omnivault(url)
        if not ov_job_id:
            _EXTRACT_JOBS[job_id] = {
                "status": "failed",
                "result": {"error": "OmniVault 暂不可用，请稍后重试或直接粘贴文案"},
            }
            return {"jobs": [{"job_id": job_id}]}

        _EXTRACT_JOBS[job_id] = {
            "status": "processing",
            "phase": "deep_extracting",
            "ov_job_id": ov_job_id,
            "url": url,
            "result": {},
        }
        logger.info("视频提取已提交 OmniVault: job_id=%s ov_job_id=%s", job_id, ov_job_id)
    else:
        # ── 图文平台：本地提取（后台线程）──
        _EXTRACT_JOBS[job_id] = {
            "status": "processing",
            "phase": "processing",
            "url": url,
            "result": {},
        }
        thread = threading.Thread(
            target=_run_local_extraction,
            args=(job_id, url, store),
            daemon=True,
        )
        thread.start()

    return {"jobs": [{"job_id": job_id}]}


async def get_job_status(job_id: str) -> dict | None:
    """查询提取任务状态。视频类透传查询 OmniVault 进度。"""
    job = _EXTRACT_JOBS.get(job_id)
    if not job:
        return None

    # 已完成/已失败：直接返回
    if job["status"] in ("done", "failed"):
        return job

    # 有 OmniVault job 的视频提取：查询 OmniVault 进度
    ov_job_id = job.get("ov_job_id")
    if ov_job_id:
        await _poll_omnivault(job_id, ov_job_id)

    return _EXTRACT_JOBS.get(job_id)


async def get_entry(entry_id: int):  # -> dict | None
    """获取条目详情。先查本地提取库，没有再去 OmniVault。"""
    from .social_agent.store import DraftStore

    store = DraftStore()
    local = store.get_extracted(entry_id)
    if local:
        return {
            "id": local["id"],
            "title": local["title"],
            "author": local["author"],
            "platform": local["platform"],
            "tags": local["tags"],
            "summary_markdown": local["raw_content"],
            "raw_content": local["raw_content"],
            "source_url": local["source_url"],
            "created_at": local["created_at"],
            "_source": "local",
        }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{config.OMNIVAULT_API_URL}/api/videos/{entry_id}"
            )
            resp.raise_for_status()
            entry = resp.json()
            if entry and not isinstance(entry.get("detail"), str):
                entry["_source"] = "omnivault"
                return entry
    except Exception as e:
        logger.warning(f"获取 OmniVault 条目 {entry_id} 失败: {e}")

    return None


async def list_entries(search: str = "", limit: int = 30) -> list[dict]:
    """列出知识条目。合并本地提取 + OmniVault。"""
    from .social_agent.store import DraftStore

    store = DraftStore()
    local_entries = store.list_extracted(search=search, limit=limit)
    items = []
    for e in local_entries:
        items.append({
            "id": e["id"],
            "title": e["title"],
            "author": e["author"],
            "platform": e["platform"],
            "tags": e["tags"],
            "source_url": e["source_url"],
            "created_at": e["created_at"],
            "_source": "local",
        })

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
            ov_items = data.get("items", data) if isinstance(data, dict) else data
            for e in ov_items:
                e["_source"] = "omnivault"
                items.append(e)
    except Exception as e:
        logger.warning(f"获取 OmniVault 条目列表失败: {e}")

    seen_urls = set()
    deduped = []
    for item in sorted(items, key=lambda x: x.get("created_at", ""), reverse=True):
        url = item.get("source_url", f"local:{item['id']}")
        if url not in seen_urls:
            seen_urls.add(url)
            deduped.append(item)

    return deduped[:limit]


async def health_check() -> bool:
    """检查 OmniVault 是否在线。"""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{config.OMNIVAULT_API_URL}/api/videos?limit=1")
            return resp.status_code == 200
    except Exception:
        return False


async def resolve_knowledge(query: str = "", entry_id: int = 0) -> dict:
    """调用 OmniVault Knowledge Resolve API。"""
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
    """从本地数据库获取高分历史草稿作为风格参考。"""
    try:
        from .social_agent.store import DraftStore
        store = DraftStore()
        return store.list_high_scored(platform=platform, limit=limit)
    except Exception as e:
        logger.warning(f"获取风格参考失败: {e}")
        return []


# ═══════════════════════════════════════
#  内部函数
# ═══════════════════════════════════════


async def _submit_to_omnivault(url: str) -> str | None:
    """向 OmniVault 提交 URL，返回 OmniVault job_id。"""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{config.OMNIVAULT_API_URL}/api/submit",
                data={"urls": url},
            )
            resp.raise_for_status()
            result = resp.json()
            jobs = result.get("jobs", [])
            if jobs:
                return jobs[0].get("job_id")
    except Exception as e:
        logger.warning("提交 OmniVault 失败: %s", e)
    return None


async def _poll_omnivault(our_job_id: str, ov_job_id: str):
    """查询一次 OmniVault job 状态，完成时自动拉取并保存。"""
    from .social_agent.store import DraftStore

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{config.OMNIVAULT_API_URL}/api/jobs/{ov_job_id}",
            )
            resp.raise_for_status()
            ov_job = resp.json()
            ov_status = ov_job.get("status", "")

            if ov_status == "done":
                ov_result = ov_job.get("result", {})
                entry_id = ov_result.get("entry_id")
                if not entry_id:
                    _EXTRACT_JOBS[our_job_id] = {
                        "status": "failed",
                        "result": {"error": "OmniVault 未返回条目 ID"},
                    }
                    return

                # 拉取完整条目
                entry_resp = await client.get(
                    f"{config.OMNIVAULT_API_URL}/api/videos/{entry_id}",
                    timeout=15,
                )
                entry_resp.raise_for_status()
                entry = entry_resp.json()

                if not entry:
                    _EXTRACT_JOBS[our_job_id] = {
                        "status": "failed",
                        "result": {"error": "OmniVault 条目为空"},
                    }
                    return

                # 字段兼容 + 清洗特殊字符
                from .content_extractor import clean_content
                raw = clean_content(entry.get("raw_content", "") or "")
                summary = clean_content(entry.get("summary_markdown", "") or "")
                if not raw and summary:
                    raw = summary
                if not raw:
                    _EXTRACT_JOBS[our_job_id] = {
                        "status": "failed",
                        "result": {"error": "提取完成但文案内容为空"},
                    }
                    return

                # 存入本地
                store = DraftStore()
                local_id = store.save_extracted({
                    "source_url": entry.get("source_url", _EXTRACT_JOBS[our_job_id]["url"]),
                    "title": entry.get("title", ""),
                    "author": entry.get("author", ""),
                    "platform": entry.get("platform", ""),
                    "raw_content": raw,
                    "tags": entry.get("tags", ""),
                })

                _EXTRACT_JOBS[our_job_id] = {
                    "status": "done",
                    "result": {
                        "entry_id": local_id,
                        "title": entry.get("title", ""),
                        "summary_preview": raw[:150],
                    },
                }
                logger.info("OmniVault 提取完成: ov_job=%s local_id=%d words=%d", ov_job_id, local_id, len(raw))

            elif ov_status == "failed":
                err = ov_job.get("result", {}).get("error", "OmniVault 处理失败")
                _EXTRACT_JOBS[our_job_id] = {
                    "status": "failed",
                    "result": {"error": str(err)[:200]},
                }
            # pending/processing → 保持 processing 状态，下次轮询再查

    except Exception as e:
        logger.warning("查询 OmniVault 状态异常: %s", e)
        # 不标记失败，下次轮询重试


def _run_local_extraction(job_id: str, url: str, store):
    """后台线程：本地图文提取。"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        from .content_extractor import extract_content

        data = loop.run_until_complete(extract_content(url))

        if data.get("_error"):
            _EXTRACT_JOBS[job_id] = {
                "status": "failed",
                "result": {"error": data["_error"]},
            }
            return

        raw = data.get("raw_content", "") or ""
        if not raw:
            _EXTRACT_JOBS[job_id] = {
                "status": "failed",
                "result": {"error": "提取完成但文案内容为空，请尝试直接粘贴文案"},
            }
            return

        entry_id = store.save_extracted(data)
        _EXTRACT_JOBS[job_id] = {
            "status": "done",
            "result": {
                "entry_id": entry_id,
                "title": data.get("title", ""),
                "summary_preview": raw[:150],
            },
        }
        logger.info("本地提取完成: url=%s entry_id=%d words=%d", url[:60], entry_id, len(raw))
    except Exception as e:
        logger.error(f"本地提取异常: {e}", exc_info=True)
        _EXTRACT_JOBS[job_id] = {
            "status": "failed",
            "result": {"error": str(e)[:200]},
        }
    finally:
        loop.close()
