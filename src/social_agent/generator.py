"""内容生成引擎 — 并行调用 LLM 为多平台生成社媒内容。

v1: 基于单条知识条目生成（向后兼容）
v2: 基于 KnowledgeResolver → ContextBundle 生成（知识驱动 / 风格驱动）
benchmark: 文案对标生成（7维分析 + 对标创作）
"""

import asyncio
import logging
from typing import Callable, Optional

from ..llm_client import chat
from .prompts import (
    build_generation_messages,
    build_generation_messages_v2,
    ALL_PLATFORMS,
    PLATFORM_NAMES_CN,
)
from .store import DraftStore, ContentDraft

logger = logging.getLogger(__name__)


async def generate_for_platforms(
    knowledge_entry: dict,
    platforms: list[str],
    tone_variant: str = "passionate",
    knowledge_id: int = 0,
    progress_callback: Optional[Callable] = None,
    generate_title: bool = True,
    generate_tags: bool = True,
) -> dict:
    """为多个平台并行生成社媒内容。"""
    store = DraftStore()

    async def _gen_one(platform: str) -> dict:
        try:
            messages = build_generation_messages(
                knowledge_entry, platform, tone_variant,
                generate_title=generate_title,
                generate_tags=generate_tags,
            )
            content = await chat(
                messages,
                temperature=0.8,
                max_tokens=4096,
            )
            if not content or len(content.strip()) < 20:
                raise ValueError(f"生成内容过短: {len(content)} 字符")

            lines = [l.strip() for l in content.split("\n") if l.strip()]
            title = lines[0] if lines else ""
            if len(title) > 80:
                title = title[:80] + "..."

            import json
            draft = ContentDraft(
                knowledge_id=knowledge_id,
                platform=platform,
                title=title,
                content_text=content.strip(),
                tone_variant=tone_variant,
                metadata_json=json.dumps({"char_count": len(content)}),
            )
            draft_id = store.save(draft)
            logger.info(
                f"生成完成 [{PLATFORM_NAMES_CN.get(platform, platform)}]: "
                f"draft_id={draft_id}, {len(content)} 字符"
            )
            return {"platform": platform, "draft_id": draft_id, "error": ""}
        except Exception as e:
            logger.error(f"生成失败 [{platform}]: {e}")
            return {"platform": platform, "draft_id": 0, "error": str(e)[:200]}

    tasks = [_gen_one(p) for p in platforms]
    results = await asyncio.gather(*tasks)

    drafts = {}
    errors = []
    for r in results:
        if r["draft_id"]:
            drafts[r["platform"]] = r["draft_id"]
        if r["error"]:
            errors.append(f"{r['platform']}: {r['error']}")

    if errors:
        logger.warning(f"部分平台生成失败: {'; '.join(errors)}")

    return {"drafts": drafts, "errors": errors}


# ═══════════════════════════════════════════
#  v2: 知识驱动 / 风格驱动 双模式生成
# ═══════════════════════════════════════════


async def generate_for_platforms_v2(
    bundle,  # ContextBundle
    platforms: list[str],
    tone_variant: str = "passionate",
    knowledge_id: int = 0,
    progress_callback: Optional[Callable] = None,
    generate_title: bool = True,
    generate_tags: bool = True,
) -> dict:
    """v2 为多个平台并行生成内容 — 使用 ContextBundle。"""
    store = DraftStore()

    async def _gen_one(platform: str) -> dict:
        try:
            messages = build_generation_messages_v2(
                bundle, platform, tone_variant,
                generate_title=generate_title,
                generate_tags=generate_tags,
            )
            content = await chat(
                messages,
                temperature=0.8,
                max_tokens=4096,
            )
            if not content or len(content.strip()) < 20:
                raise ValueError(f"生成内容过短: {len(content)} 字符")

            lines = [l.strip() for l in content.split("\n") if l.strip()]
            title = lines[0] if lines else ""
            if len(title) > 80:
                title = title[:80] + "..."

            import json
            draft = ContentDraft(
                knowledge_id=knowledge_id,
                platform=platform,
                title=title,
                content_text=content.strip(),
                tone_variant=tone_variant,
                metadata_json=json.dumps({
                    "char_count": len(content),
                    "mode": bundle.mode,
                    "coverage": bundle.coverage,
                    "wiki_sources": len(bundle.wiki_pages),
                    "rag_sources": len(bundle.rag_results),
                    "related_entries": len(bundle.related_entries_full),
                    "style_refs": len(bundle.style_examples),
                }),
            )
            draft_id = store.save(draft)
            logger.info(
                f"v2 生成完成 [{PLATFORM_NAMES_CN.get(platform, platform)}]: "
                f"draft_id={draft_id}, mode={bundle.mode}, {len(content)} 字符"
            )
            return {"platform": platform, "draft_id": draft_id, "error": ""}
        except Exception as e:
            logger.error(f"v2 生成失败 [{platform}]: {e}")
            return {"platform": platform, "draft_id": 0, "error": str(e)[:200]}

    tasks = [_gen_one(p) for p in platforms]
    results = await asyncio.gather(*tasks)

    drafts = {}
    errors = []
    for r in results:
        if r["draft_id"]:
            drafts[r["platform"]] = r["draft_id"]
        if r["error"]:
            errors.append(f"{r['platform']}: {r['error']}")

    if errors:
        logger.warning(f"v2 部分平台生成失败: {'; '.join(errors)}")

    return {
        "drafts": drafts,
        "errors": errors,
        "mode": bundle.mode,
        "coverage": bundle.coverage,
    }


# ═══════════════════════════════════════════
#  文案对标生成
# ═══════════════════════════════════════════


async def generate_benchmarked_copy(
    reference_content: str,
    user_materials: str,
    platforms: list[str],
    tone_variant: str = "passionate",
    knowledge_id: int = 0,
    generate_title: bool = True,
    generate_tags: bool = True,
) -> dict:
    """文案对标生成 — 两步走：7维分析 + 对标创作。

    Args:
        reference_content: 参考文案全文（从对标视频提取）
        user_materials: 用户提供的自有资料
        platforms: 目标平台列表
        tone_variant: 情绪基调
        knowledge_id: 关联的知识条目 ID（参考条目）
        generate_title: 是否生成标题
        generate_tags: 是否生成标签

    Returns:
        {"drafts": {platform: draft_id}, "errors": [...], "analysis": {...}}
    """
    from .prompts import (
        build_benchmark_analysis_messages,
        build_benchmark_generation_messages,
        PLATFORM_NAMES_CN,
    )

    store = DraftStore()

    # ── Step 1: 7 维对标分析 ──
    logger.info("对标分析开始，参考文案 %d 字", len(reference_content))
    analysis_result = {}
    try:
        analysis_messages = build_benchmark_analysis_messages(reference_content)
        analysis_raw = await chat(
            analysis_messages,
            temperature=0.4,
            max_tokens=4096,
        )
        # 解析 JSON
        import json
        analysis_raw = analysis_raw.strip()
        if analysis_raw.startswith("```"):
            analysis_raw = analysis_raw.split("\n", 1)[-1]
            if analysis_raw.endswith("```"):
                analysis_raw = analysis_raw[:-3]
        analysis_result = json.loads(analysis_raw)
        logger.info(
            "对标分析完成: domain=%s, overall_score=%s, top_3=%s",
            analysis_result.get("domain", "?"),
            analysis_result.get("overall_score", "?"),
            len(analysis_result.get("top_3_techniques", [])),
        )
    except Exception as e:
        logger.error(f"对标分析失败: {e}", exc_info=True)
        # 分析失败仍然尝试生成，使用空分析结果
        analysis_result = {
            "overview": f"分析失败: {str(e)[:100]}",
            "domain": "未知",
            "target_audience": "未知",
            "dimensions": {},
            "overall_score": 0,
            "top_3_techniques": [],
            "style_signature": "未知",
            "_error": True,
        }

    # ── Step 2: 对标创作（并行多平台）──
    async def _gen_one(platform: str) -> dict:
        try:
            gen_messages = build_benchmark_generation_messages(
                analysis_result=analysis_result,
                user_materials=user_materials,
                platform=platform,
                tone_variant=tone_variant,
                generate_title=generate_title,
                generate_tags=generate_tags,
            )
            content = await chat(
                gen_messages,
                temperature=0.8,
                max_tokens=4096,
            )
            if not content or len(content.strip()) < 20:
                raise ValueError(f"生成内容过短: {len(content)} 字符")

            lines = [l.strip() for l in content.split("\n") if l.strip()]
            title = lines[0] if lines else ""
            if len(title) > 80:
                title = title[:80] + "..."

            import json
            draft = ContentDraft(
                knowledge_id=knowledge_id,
                platform=platform,
                title=title,
                content_text=content.strip(),
                tone_variant=tone_variant,
                metadata_json=json.dumps({
                    "char_count": len(content),
                    "type": "benchmark",
                    "user_materials": user_materials[:500],
                    "analysis_overview": analysis_result.get("overview", "")[:200],
                    "ref_domain": analysis_result.get("domain", ""),
                    "ref_overall_score": analysis_result.get("overall_score", 0),
                }),
            )
            draft_id = store.save(draft)
            logger.info(
                f"对标生成完成 [{PLATFORM_NAMES_CN.get(platform, platform)}]: "
                f"draft_id={draft_id}, {len(content)} 字符"
            )
            return {"platform": platform, "draft_id": draft_id, "error": ""}
        except Exception as e:
            logger.error(f"对标生成失败 [{platform}]: {e}")
            return {"platform": platform, "draft_id": 0, "error": str(e)[:200]}

    tasks = [_gen_one(p) for p in platforms]
    results = await asyncio.gather(*tasks)

    drafts = {}
    errors = []
    for r in results:
        if r["draft_id"]:
            drafts[r["platform"]] = r["draft_id"]
        if r["error"]:
            errors.append(f"{r['platform']}: {r['error']}")

    if errors:
        logger.warning(f"对标生成部分平台失败: {'; '.join(errors)}")

    return {
        "drafts": drafts,
        "errors": errors,
        "analysis": analysis_result,
    }
