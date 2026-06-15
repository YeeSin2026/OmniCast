"""OmniCast Agent 路由 — 独立于 OmniVault 的页面 + API。"""

import asyncio
import json
import logging
import threading
import uuid
from datetime import datetime, timezone

from fastapi import Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi import HTTPException

from .. import connector
from .. import settings

logger = logging.getLogger(__name__)

# 简单的内存 job 跟踪
_JOBS: dict[str, dict] = {}

# ── 关联知识缓存（按 entry_id → 渲染后的 HTML），持久化到文件 ──
import os as _os
_RANKING_CACHE: dict[int, str] = {}
_RANKING_CACHE_FILE = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(__file__))), 'data', 'ranking_cache.json')


def _load_ranking_cache():
    """启动时从文件恢复缓存。"""
    global _RANKING_CACHE
    try:
        if _os.path.exists(_RANKING_CACHE_FILE):
            import json as _json
            with open(_RANKING_CACHE_FILE) as f:
                raw = _json.load(f)
                _RANKING_CACHE = {int(k): v for k, v in raw.items()}
            logger.info("关联知识缓存已恢复: %d 条", len(_RANKING_CACHE))
    except Exception:
        pass


def _save_ranking_cache():
    """缓存更新后写文件。"""
    try:
        import json as _json
        _os.makedirs(_os.path.dirname(_RANKING_CACHE_FILE), exist_ok=True)
        with open(_RANKING_CACHE_FILE, 'w') as f:
            _json.dump(_RANKING_CACHE, f, ensure_ascii=False)
    except Exception:
        pass


def register_routes(app, jinja_env, render_func):
    from .store import DraftStore
    from .generator import generate_for_platforms, generate_for_platforms_v2
    from .resolver import KnowledgeResolver
    from .scorer import score_and_predict, score_title, score_tags
    from .prompts import ALL_PLATFORMS, PLATFORM_META, ALL_EMOTIONS, EMOTIONS

    draft_store = DraftStore()

    # ═══════════════════════════════════════════
    # 页面路由
    # ═══════════════════════════════════════════

    @app.get("/", response_class=HTMLResponse)
    @app.get("/agent", response_class=HTMLResponse)
    async def agent_home(request: Request, search: str = Query("")):
        entries = await connector.list_entries(search=search, limit=30)
        drafts = draft_store.list_recent(limit=15)
        return HTMLResponse(render_func(
            "agent_home.html",
            request=request,
            entries=entries,
            drafts=drafts,
            platforms_meta=PLATFORM_META,
            search=search,
            ov_url=settings.OMNIVAULT_API_URL,
        ))

    @app.get("/agent/generate/{entry_id}", response_class=HTMLResponse)
    async def agent_generate_page(request: Request, entry_id: int):
        entry = await connector.get_entry(entry_id)
        if not entry:
            raise HTTPException(404, "知识条目未找到")
        existing = draft_store.list_by_knowledge(entry_id)
        existing_platforms = {d["platform"] for d in existing}
        return HTMLResponse(render_func(
            "agent_generate.html",
            request=request,
            entry=entry,
            ranking_reasons=[],
            all_platforms=ALL_PLATFORMS,
            platforms_meta=PLATFORM_META,
            existing_platforms=existing_platforms,
        ))

    # ── 启动时恢复持久化缓存 ──
    _load_ranking_cache()

    @app.get("/api/agent/generate/{entry_id}/ranking", response_class=HTMLResponse)
    async def agent_generate_ranking(entry_id: int, force: int = Query(0)):
        """HTMX 懒加载：显示 AI 为这个条目选择的关联知识及理由。

        结果缓存到内存 + 文件，重启后仍有效。
        force=1: 强制重新分析（用户手动点击"重新关联"）
        """
        # 缓存命中（非强制模式）
        if not force and entry_id in _RANKING_CACHE:
            return HTMLResponse(_RANKING_CACHE[entry_id])

        entry = await connector.get_entry(entry_id)
        if not entry:
            return HTMLResponse('<div class="text-[13px] text-zinc-400">条目未找到</div>')

        resolver = KnowledgeResolver()
        bundle = await resolver.resolve(
            topic=entry.get("title", ""),
            entry_id=entry_id,
            max_related=5,
        )
        reasons = bundle.ranking_reasons
        if not reasons:
            html = (
                '<div class="text-[13px] text-zinc-400 dark:text-zinc-500 py-3">'
                '未找到高度关联的知识条目</div>'
            )
            _RANKING_CACHE[entry_id] = html
            _save_ranking_cache()
            return HTMLResponse(html)

        html = render_func("ranking_reasons.html", reasons=reasons, entry_id=entry_id)
        _RANKING_CACHE[entry_id] = html
        _save_ranking_cache()
        return HTMLResponse(html)

    @app.get("/agent/benchmark", response_class=HTMLResponse)
    async def agent_benchmark_page(request: Request, entry_id: int = Query(0)):
        """文案对标页面 — 可传入已提取的参考条目 ID。"""
        entry = None
        if entry_id:
            entry = await connector.get_entry(entry_id)
        return HTMLResponse(render_func(
            "agent_benchmark.html",
            request=request,
            entry=entry,
            entry_id=entry_id,
            all_platforms=ALL_PLATFORMS,
            platforms_meta=PLATFORM_META,
            all_emotions=ALL_EMOTIONS,
            emotions=EMOTIONS,
        ))

    @app.get("/agent/generate-multi", response_class=HTMLResponse)
    async def agent_generate_multi_page(request: Request, ids: str = Query("")):
        """多条目合并创作页面 — 用户手动选择了多条知识条目。"""
        id_list = []
        for part in ids.split(","):
            part = part.strip()
            if part.isdigit():
                id_list.append(int(part))

        if not id_list:
            raise HTTPException(400, "请至少选择一条知识条目")

        # 并行拉取所有条目详情
        tasks = [connector.get_entry(eid) for eid in id_list[:6]]
        results = await asyncio.gather(*tasks)
        entries = [r for r in results if r is not None]

        if not entries:
            raise HTTPException(404, "所选知识条目均未找到")

        return HTMLResponse(render_func(
            "agent_generate_multi.html",
            request=request,
            entries=entries,
            ids=ids,
            all_platforms=ALL_PLATFORMS,
            platforms_meta=PLATFORM_META,
        ))

    @app.get("/agent/drafts", response_class=HTMLResponse)
    async def agent_drafts_page(
        request: Request,
        status: str = Query(""),
        platform: str = Query(""),
    ):
        s = status if status in ("draft", "approved") else None
        p = platform if platform in ALL_PLATFORMS else None
        all_drafts = draft_store.list_recent(limit=100, status=s, platform=p)

        # 按 knowledge_id 分组
        groups = {}  # knowledge_id → {"entry_title": ..., "drafts": [...]}
        for d in all_drafts:
            kid = d.get("knowledge_id", 0)
            if kid not in groups:
                # 尝试获取知识条目标题
                entry = None
                try:
                    entry = await connector.get_entry(kid)
                except Exception:
                    pass
                groups[kid] = {
                    "knowledge_id": kid,
                    "entry_title": entry.get("title", f"知识 #{kid}") if entry else f"知识 #{kid}",
                    "drafts": [],
                }
            groups[kid]["drafts"].append(d)

        # 按每组最新草稿时间倒序
        sorted_groups = sorted(
            groups.values(),
            key=lambda g: max(d.get("updated_at", "") for d in g["drafts"]),
            reverse=True,
        )

        return HTMLResponse(render_func(
            "agent_drafts.html",
            request=request,
            groups=sorted_groups,
            platforms_meta=PLATFORM_META,
            all_platforms=ALL_PLATFORMS,
            current_status=status,
            current_platform=platform,
        ))

    @app.get("/agent/draft/{draft_id}", response_class=HTMLResponse)
    async def agent_draft_detail(request: Request, draft_id: int):
        import re as _re
        draft = draft_store.get_by_id(draft_id)
        if not draft:
            raise HTTPException(404, "草稿未找到")
        entry = await connector.get_entry(draft["knowledge_id"])

        # 解析内容：标题 / 标签 / 正文
        raw = draft.get("content_text", "")
        lines = raw.split("\n")
        tag_lines = []
        body_lines = []
        for line in lines:
            s = line.strip()
            if s.startswith("#") and not s.startswith("##") and len(s) < 80:
                tag_lines.append(s)
            else:
                body_lines.append(line)

        title_line = ""
        content_lines = []
        for line in body_lines:
            s = line.strip()
            if not title_line and s:
                title_line = s
            elif title_line:
                content_lines.append(line)
        body_text = "\n".join(content_lines).strip()

        # 清洗特殊字符（用于展示）
        clean = body_text
        # 去掉所有 markdown 标记
        clean = _re.sub(r'\*\*(.+?)\*\*', r'\1', clean)  # 加粗
        clean = _re.sub(r'\*(.+?)\*', r'\1', clean)       # 斜体
        clean = clean.replace('**', '').replace('__', '').replace('`', '')
        # 去掉画面导演标注
        clean = _re.sub(r'\[画面[：:][^\]]*\]', '', clean)
        clean = _re.sub(r'\[字幕[：:][^\]]*\]', '', clean)
        clean = _re.sub(r'\[停[^\]]*\]', '', clean)
        # 去掉 markdown 链接
        clean = _re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', clean)
        # 去掉行首标记符号
        clean = _re.sub(r'^[#>*-]\s+', '', clean, flags=_re.MULTILINE)
        # 合并多余空行
        clean = _re.sub(r'\n{3,}', '\n\n', clean)
        clean = clean.strip()

        return HTMLResponse(render_func(
            "agent_detail.html",
            request=request,
            draft=draft,
            entry=entry,
            platform_meta=PLATFORM_META.get(draft["platform"], {}),
            title_line=title_line,
            body_text=clean,
            tag_lines=tag_lines,
        ))

    # ═══════════════════════════════════════════
    # API 路由
    # ═══════════════════════════════════════════

    @app.post("/api/agent/generate")
    async def api_agent_generate(
        request: Request,
        knowledge_id: int = Form(...),
        tone_variant: str = Form("standard"),
        custom_instructions: str = Form(""),
    ):
        entry = await connector.get_entry(knowledge_id)
        if not entry:
            raise HTTPException(404, "知识条目未找到（OmniVault 是否在线？）")

        form_data = await request.form()
        platform_vals = form_data.getlist("platforms")
        platform_list = [p for p in platform_vals if p in ALL_PLATFORMS]
        if not platform_list:
            platform_list = list(ALL_PLATFORMS)

        if custom_instructions:
            entry["_custom_instructions"] = custom_instructions

        job_id = uuid.uuid4().hex[:12]
        _JOBS[job_id] = {"status": "pending", "result": {}}

        thread = threading.Thread(
            target=_run_generation,
            args=(job_id, entry, platform_list, tone_variant, knowledge_id),
            daemon=True,
        )
        thread.start()

        return HTMLResponse(
            f'<div id="gen-poll" hx-get="/api/agent/generate/status/{job_id}" '
            f'hx-trigger="every 2s" hx-swap="outerHTML">'
            f'<span class="text-[13px] text-zinc-500 dark:text-zinc-400">'
            f'<span class="inline-block w-4 h-4 border-2 border-zinc-300 dark:border-zinc-600 border-t-zinc-500 dark:border-t-zinc-400 rounded-full animate-spin mr-2 align-middle"></span>'
            f'正在生成中，请稍候…</span></div>'
        )

    @app.get("/api/agent/generate/status/{job_id}")
    async def api_agent_generate_status(job_id: str):
        job = _JOBS.get(job_id)
        if not job:
            return HTMLResponse('<div class="text-[13px] text-red-500 dark:text-red-400">任务未找到</div>')

        if job["status"] == "done":
            errors = job["result"].get("errors", [])
            drafts = job["result"].get("drafts", {})
            mode = job["result"].get("mode", "")
            coverage = job["result"].get("coverage", "")
            bundle = job.get("bundle")
            html = ""
            # v2 模式 & 覆盖度指示器
            if mode:
                mode_badge = {
                    "knowledge-rich": "🧠 知识驱动",
                    "style-driven": "🎨 风格驱动",
                }.get(mode, mode)
                html += f'<div class="text-[11px] text-zinc-400 mb-2">模式: {mode_badge} · 覆盖度: {coverage}</div>'
            # 知识来源详情
            if bundle:
                html += f'<div class="text-[11px] text-zinc-400 mb-2">wiki:{len(bundle.wiki_pages)}页 · rag:{len(bundle.rag_results)}条 · 关联:{len(bundle.related_entries_full)}条</div>'
            if errors:
                html += '<div class="text-[13px] text-amber-600 dark:text-amber-400 mb-2">' + "<br>".join(errors) + "</div>"
            # 对标分析结果展示
            analysis = job.get("analysis")
            if analysis:
                dims = analysis.get("dimensions", {})
                if dims:
                    dim_labels = {"HP": "钩子", "ER": "情感", "SR": "共振", "QL": "金句", "NA": "叙事", "AB": "受众", "TS": "分享"}
                    dim_rows = []
                    for key, label in dim_labels.items():
                        d = dims.get(key, {})
                        score = d.get("score", "?")
                        technique = d.get("technique", "")[:40]
                        dim_rows.append(
                            f'<div class="flex items-center gap-2 text-[11px]">'
                            f'<span class="text-zinc-400 w-8">{label}</span>'
                            f'<span class="font-mono text-zinc-600 dark:text-zinc-300">{score}/5</span>'
                            f'<span class="text-zinc-400 truncate">{technique}</span>'
                            f'</div>'
                        )
                    html += (
                        f'<div class="mb-3 p-3 bg-zinc-50 dark:bg-zinc-800/50 rounded-lg">'
                        f'<div class="text-[12px] font-medium text-zinc-600 dark:text-zinc-300 mb-1.5">'
                        f'📊 参考文案 7 维分析 · 综合 {analysis.get("overall_score", "?")}/5 · {analysis.get("domain", "")}</div>'
                        f'{"".join(dim_rows)}'
                        f'</div>'
                    )
            if drafts:
                links = []
                for platform, draft_id in drafts.items():
                    meta = PLATFORM_META.get(platform, {})
                    links.append(
                        f'<a href="/agent/draft/{draft_id}" '
                        f'class="inline-block px-3 py-1.5 bg-zinc-100 dark:bg-zinc-800 '
                        f'text-zinc-700 dark:text-zinc-300 rounded-lg text-[13px] '
                        f'hover:bg-zinc-200 dark:hover:bg-zinc-700 transition-colors mr-2 mb-2">'
                        f'{meta.get("icon","")} {meta.get("name", platform)} 草稿 #{draft_id}</a>'
                    )
                html += f'<div class="text-[13px] text-emerald-600 dark:text-emerald-400 mb-2">✅ 对标生成完成！共 {len(drafts)} 个平台</div>' + "".join(links)
            return HTMLResponse(html or "生成完成，但无结果")
        elif job["status"] == "failed":
            error = job["result"].get("error", "生成失败")
            return HTMLResponse(f'<div class="text-[13px] text-red-500 dark:text-red-400">❌ {error[:200]}</div>')
        else:
            # 根据阶段显示不同的提示文案
            phase = job.get("phase", "generating")
            phase_messages = {
                "preparing": "正在连接知识库，准备分析…",
                "resolving": "🧠 正在分析知识关联，连接 LLM 中…",
                "generating": "✍️ 正在生成社媒内容，LLM 创作中…",
                "analyzing": "🔍 正在 7 维拆解参考文案的写作技法…",
                "processing": "📡 正在抓取页面内容，提取文案中…",
                "deep_extracting": "⛏️ 正在深度采集视频完整文案（字幕/口播）…",
            }
            msg = phase_messages.get(phase, "正在生成中，请稍候…")
            return HTMLResponse(
                f'<div id="gen-poll" hx-get="/api/agent/generate/status/{job_id}" '
                f'hx-trigger="every 2s" hx-swap="outerHTML">'
                f'<div class="flex items-center gap-2">'
                f'<span class="inline-block w-4 h-4 border-2 border-zinc-300 dark:border-zinc-600 border-t-zinc-500 dark:border-t-zinc-400 rounded-full animate-spin"></span>'
                f'<span class="text-[13px] text-zinc-500 dark:text-zinc-400">{msg}</span>'
                f'</div></div>'
            )

    # ═══════════════════════════════════════════
    #  v2 API: 知识驱动 / 风格驱动 生成
    # ═══════════════════════════════════════════

    @app.post("/api/agent/generate-v2")
    async def api_agent_generate_v2(
        request: Request,
        knowledge_id: int = Form(...),
        topic: str = Form(""),
        tone_variant: str = Form("passionate"),
        max_related: int = Form(5),
        selected_ids: str = Form(""),
        generate_title: bool = Form(False),
        generate_tags: bool = Form(False),
    ):
        """v2 生成 — 立即返回轮询片段，解析+生成在后台线程中执行。

        避免同步等待 KnowledgeResolver（含 LLM 调用）导致客户端无反馈。
        """
        entry = await connector.get_entry(knowledge_id)
        if not entry:
            raise HTTPException(404, "知识条目未找到（OmniVault 是否在线？）")

        form_data = await request.form()
        platform_vals = form_data.getlist("platforms")
        platform_list = [p for p in platform_vals if p in ALL_PLATFORMS]
        if not platform_list:
            platform_list = list(ALL_PLATFORMS)

        # 用条目标题作为话题
        search_topic = topic or entry.get("title", "")

        # 校验 knowledge_id 存在性后立即创建 job，所有耗时操作在后台线程完成
        job_id = uuid.uuid4().hex[:12]
        _JOBS[job_id] = {
            "status": "pending",
            "phase": "preparing",
            "result": {},
        }

        thread = threading.Thread(
            target=_run_generation_v2_full,
            args=(job_id, search_topic, knowledge_id, None, platform_list,
                  tone_variant, generate_title, generate_tags, max_related,
                  platform_list[0] if len(platform_list) == 1 else "",
                  selected_ids),
            daemon=True,
        )
        thread.start()

        return HTMLResponse(
            f'<div id="gen-poll" hx-get="/api/agent/generate/status/{job_id}" '
            f'hx-trigger="every 2s" hx-swap="outerHTML">'
            f'<div class="flex items-center gap-2">'
            f'<span class="inline-block w-4 h-4 border-2 border-zinc-300 dark:border-zinc-600 border-t-zinc-500 dark:border-t-zinc-400 rounded-full animate-spin"></span>'
            f'<span class="text-[13px] text-zinc-500 dark:text-zinc-400">正在连接知识库，准备生成…</span>'
            f'</div></div>'
        )

    @app.post("/api/agent/generate-multi")
    async def api_agent_generate_multi(
        request: Request,
        knowledge_ids: str = Form(""),
        topic: str = Form(""),
        tone_variant: str = Form("passionate"),
        max_related: int = Form(5),
        generate_title: bool = Form(False),
        generate_tags: bool = Form(False),
    ):
        """多条目合并生成 — 立即返回轮询片段，解析+生成在后台线程中执行。"""
        # 解析 ID 列表
        id_list = []
        for part in knowledge_ids.split(","):
            part = part.strip()
            if part.isdigit():
                id_list.append(int(part))

        if not id_list:
            raise HTTPException(400, "请至少选择一条知识条目")

        form_data = await request.form()
        platform_vals = form_data.getlist("platforms")
        platform_list = [p for p in platform_vals if p in ALL_PLATFORMS]
        if not platform_list:
            platform_list = list(ALL_PLATFORMS)

        # 用第一个条目的标题作为话题
        first_entry = await connector.get_entry(id_list[0])
        search_topic = topic or (first_entry.get("title", "") if first_entry else "")

        # 创建 job 后立即返回，所有耗时操作在后台线程完成
        job_id = uuid.uuid4().hex[:12]
        _JOBS[job_id] = {
            "status": "pending",
            "phase": "preparing",
            "result": {},
        }

        thread = threading.Thread(
            target=_run_generation_v2_full,
            args=(job_id, search_topic, id_list[0], id_list, platform_list,
                  tone_variant, generate_title, generate_tags, max_related,
                  platform_list[0] if len(platform_list) == 1 else "",
                  ""),  # selected_ids 在 multi 模式下不适用
            daemon=True,
        )
        thread.start()

        return HTMLResponse(
            f'<div id="gen-poll" hx-get="/api/agent/generate/status/{job_id}" '
            f'hx-trigger="every 2s" hx-swap="outerHTML">'
            f'<div class="flex items-center gap-2">'
            f'<span class="inline-block w-4 h-4 border-2 border-zinc-300 dark:border-zinc-600 border-t-zinc-500 dark:border-t-zinc-400 rounded-full animate-spin"></span>'
            f'<span class="text-[13px] text-zinc-500 dark:text-zinc-400">正在连接知识库，准备生成…</span>'
            f'</div></div>'
        )

    # ═══════════════════════════════════════════
    #  文案对标 API
    # ═══════════════════════════════════════════

    @app.post("/api/agent/benchmark")
    async def api_agent_benchmark(
        request: Request,
        reference_entry_id: int = Form(0),
        reference_text: str = Form(""),
        user_materials: str = Form(...),
        tone_variant: str = Form("passionate"),
    ):
        """文案对标生成 — 分析参考文案后对标创作。

        两种输入方式：
        1. reference_entry_id: 从 OmniVault 获取已提取的参考条目
        2. reference_text: 直接粘贴参考文案
        """
        # 获取参考文案（优先取原始文案 raw_content，AI总结不含写作技法）
        reference_content = ""
        if reference_entry_id:
            entry = await connector.get_entry(reference_entry_id)
            if not entry:
                raise HTTPException(404, "参考条目未找到（OmniVault 是否在线？）")
            reference_content = entry.get("raw_content", "") or entry.get("summary_markdown", "") or ""
        elif reference_text.strip():
            reference_content = reference_text.strip()

        if not reference_content:
            raise HTTPException(400, "请提供参考链接（先提取文案）或直接粘贴参考文案")

        if not user_materials.strip():
            raise HTTPException(400, "请提供你的资料（品牌/产品介绍、卖点等）")

        form_data = await request.form()
        platform_vals = form_data.getlist("platforms")
        platform_list = [p for p in platform_vals if p in ALL_PLATFORMS]
        if not platform_list:
            platform_list = list(ALL_PLATFORMS)

        job_id = uuid.uuid4().hex[:12]
        _JOBS[job_id] = {
            "status": "pending",
            "phase": "analyzing",
            "result": {},
        }

        thread = threading.Thread(
            target=_run_benchmark_generation,
            args=(job_id, reference_content, user_materials, platform_list,
                  tone_variant, reference_entry_id),
            daemon=True,
        )
        thread.start()

        return HTMLResponse(
            f'<div id="gen-poll" hx-get="/api/agent/generate/status/{job_id}" '
            f'hx-trigger="every 2s" hx-swap="outerHTML">'
            f'<div class="flex items-center gap-2">'
            f'<span class="inline-block w-4 h-4 border-2 border-zinc-300 dark:border-zinc-600 border-t-zinc-500 dark:border-t-zinc-400 rounded-full animate-spin"></span>'
            f'<span class="text-[13px] text-zinc-500 dark:text-zinc-400">🔍 正在分析参考文案的 7 维写作技法…</span>'
            f'</div></div>'
        )

    # ═══════════════════════════════════════════
    #  发布追踪 API（反馈闭环）
    # ═══════════════════════════════════════════

    @app.post("/api/agent/publish")
    async def api_agent_publish(
        draft_id: int = Form(...),
        publish_url: str = Form(""),
        views: int = Form(0),
        likes: int = Form(0),
        shares: int = Form(0),
        comments: int = Form(0),
    ):
        """记录发布结果 — 这是反馈闭环的入口。

        发布后调用此接口，数据将用于优化未来内容创作。
        """
        metrics = {
            "views": views,
            "likes": likes,
            "shares": shares,
            "comments": comments,
            "engagement_rate": round((likes + shares + comments) / max(views, 1) * 100, 2),
        }
        ok = draft_store.record_publish(draft_id, publish_url, metrics)
        return {"ok": ok, "draft_id": draft_id, "metrics": metrics}

    @app.get("/api/agent/performance")
    async def api_agent_performance(platform: str = Query(""), limit: int = Query(10)):
        """获取已发布内容的性能摘要。"""
        items = draft_store.get_performance_summary(platform=platform, limit=limit)
        return {"items": items, "total": len(items)}

    # ═══════════════════════════════════════════
    # 直接 URL 输入（独立于知识库选题）
    # ═══════════════════════════════════════════

    @app.post("/api/agent/url-submit")
    async def api_agent_url_submit(url: str = Form(...), return_to: str = Form("")):
        """接收 URL，本地抓取 + LLM 提取内容，返回进度轮询片段。

        独立运行，不依赖 OmniVault。
        支持粘贴各大平台分享口令/分享文本，自动从中提取链接。"""
        import re
        url = url.strip()
        if not url:
            return HTMLResponse(render_func("agent_url_status.html", error="请输入链接"))

        # ── 已知短链/社媒域名（不含协议时也能识别）──
        KNOWN_DOMAINS = [
            # 国内
            r'v\.douyin\.com', r'www\.douyin\.com',
            r'xhslink\.com', r'www\.xiaohongshu\.com',
            r'v\.kuaishou\.com', r'www\.kuaishou\.com',
            r'b23\.tv', r'www\.bilibili\.com', r'bilibili\.com',
            r't\.cn', r'weibo\.com', r'm\.weibo\.cn',
            r'mp\.weixin\.qq\.com',
            r'www\.zhihu\.com', r'zhihu\.com', r'zh\.sh',
            r'www\.douban\.com', r'douban\.com',
            r'www\.huya\.com', r'www\.douyu\.com',
            r'www\.meituan\.com', r'www\.dianping\.com',
            # 海外
            r'youtu\.be', r'www\.youtube\.com', r'youtube\.com', r'm\.youtube\.com',
            r'www\.instagram\.com', r'instagram\.com',
            r'x\.com', r'twitter\.com', r'mobile\.twitter\.com', r't\.co',
            r'www\.tiktok\.com', r'vt\.tiktok\.com', r'tiktok\.com', r'm\.tiktok\.com',
            r'www\.linkedin\.com', r'linkedin\.com',
            r'www\.facebook\.com', r'facebook\.com', r'fb\.com', r'fb\.watch',
            r'www\.reddit\.com', r'reddit\.com', r'redd\.it',
            r'www\.pinterest\.com', r'pin\.it',
            r'www\.twitch\.tv', r'twitch\.tv', r'clips\.twitch\.tv',
            r'medium\.com',
            r'open\.spotify\.com',
            r'discord\.com', r'discord\.gg',
            r'www\.snapchat\.com', r'snapchat\.com',
            r'www\.threads\.net', r'threads\.net',
            r'www\.quora\.com', r'quora\.com',
            r't\.me', r'telegram\.me',
            r'www\.whatsapp\.com', r'wa\.me',
            r'github\.com', r'gist\.github\.com',
            r'news\.ycombinator\.com',
            r'substack\.com', r'[\w-]+\.substack\.com',
            r'www\.producthunt\.com',
            r'www\.behance\.net',
            r'dribbble\.com',
            r'vimeo\.com', r'www\.vimeo\.com',
        ]

        extracted = ""

        # ═══════════════════════════════════════
        # 策略 1: 完整 https?:// URL
        # ═══════════════════════════════════════
        url_match = re.search(r'https?://\S+', url)
        if url_match:
            extracted = url_match.group(0).rstrip('.,;:!?）)】」》')

        # ═══════════════════════════════════════
        # 策略 2: 已知短链/社媒域名，不带协议
        # ═══════════════════════════════════════
        if not extracted:
            for domain_pattern in KNOWN_DOMAINS:
                m = re.search(domain_pattern + r'(?:/\S*)?', url)
                if m:
                    raw = m.group(0).rstrip('.,;:!?）)】」》')
                    extracted = 'https://' + raw
                    break

        # ═══════════════════════════════════════
        # 策略 3: 纯 URL（用户直接贴了链接但缺协议）
        # ═══════════════════════════════════════
        if not extracted:
            if url.startswith('www.') or re.match(r'^[A-Za-z0-9-]+\.[a-z]{2,}/', url):
                extracted = 'https://' + url.split()[0].rstrip('.,;:!?）)】」》')

        # ═══════════════════════════════════════
        # 策略 4: 泛域名匹配
        # ═══════════════════════════════════════
        if not extracted:
            domain_match = re.search(
                r'(?:^|\s)'
                r'((?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+'
                r'[a-z]{2,}'
                r'(?:/[^\s，。！？、一-鿿]*)?)',
                url
            )
            if domain_match:
                raw = domain_match.group(1).rstrip('.,;:!?）)】」》')
                if '/' in raw or any(
                    raw.endswith('.' + tld) or '.' + tld + '/' in raw
                    for tld in ['com', 'cn', 'net', 'org', 'tv', 'io', 'app', 'xyz', 'link']
                ):
                    extracted = 'https://' + raw

        if not extracted:
            return HTMLResponse(render_func(
                "agent_url_status.html",
                error=(
                    "未识别到有效链接。请确认分享口令中包含完整链接。"
                    "💡 提示：在抖音 App 中点击「分享 → 复制链接」（不是复制口令），"
                    "粘贴的文本应包含 https://v.douyin.com/... 地址。"
                ),
            ))

        logger.info("从分享口令中提取链接: %s", extracted[:120])

        result = await connector.submit_url(extracted)
        if not result:
            return HTMLResponse(render_func(
                "agent_url_status.html",
                error="提取服务暂不可用，请稍后重试或直接粘贴文案。",
            ))

        jobs = result.get("jobs", [])
        if not jobs:
            return HTMLResponse(render_func("agent_url_status.html", error="提交失败，未返回任务。"))

        job_id = jobs[0].get("job_id", "")
        if not job_id:
            return HTMLResponse(render_func("agent_url_status.html", error="未获取到有效任务 ID。"))

        return HTMLResponse(render_func("agent_url_status.html", job_id=job_id, return_to=return_to))

    @app.get("/api/agent/url-status/{job_id}")
    async def api_agent_url_status(job_id: str, return_to: str = Query("")):
        """轮询提取任务状态。对标页完成时不跳转，改局部更新预览。"""
        job = await connector.get_job_status(job_id)
        if not job:
            return HTMLResponse(render_func(
                "agent_url_status.html",
                error="提取任务未找到，请重新提交链接。",
            ))

        status = job.get("status", "")
        if status in ("pending", "processing", "deep_extracting"):
            return HTMLResponse(render_func("agent_url_status.html", job_id=job_id, return_to=return_to))
        elif status == "done":
            result = job.get("result", {})
            entry_id = result.get("entry_id")
            if not entry_id:
                return HTMLResponse(render_func(
                    "agent_url_status.html",
                    error="处理完成但未获取到条目 ID。",
                ))

            # ── 对标页：拉取完整条目，渲染预览片段，不跳转 ──
            if return_to == "benchmark":
                entry = await connector.get_entry(entry_id)
                raw = entry.get("raw_content", "") if entry else ""
                summary = entry.get("summary_markdown", "") if entry else ""

                # raw_content 可能为空（OmniVault 某些情况下只产出 summary）
                if not raw and summary:
                    raw = summary
                if not raw:
                    return HTMLResponse(render_func(
                        "agent_url_status.html",
                        error=f"提取完成但未获取到文案内容（条目 #{entry_id}）。请确认链接是视频/文章页面，或尝试直接粘贴文案。",
                    ))

                import json as _json
                benchmark_done_html = render_func(
                    "_benchmark_extract_done.html",
                    entry_id=entry_id,
                    title=entry.get("title", "") if entry else result.get("title", ""),
                    preview=raw,
                    char_count=len(raw),
                    platform=entry.get("platform", "") if entry else "",
                    author=entry.get("author", "") if entry else "",
                    tags=entry.get("tags", "") if entry else "",
                    raw_content_json=_json.dumps(raw, ensure_ascii=False),
                )
                return HTMLResponse(render_func(
                    "agent_url_status.html",
                    done=True,
                    entry_id=entry_id,
                    return_to=return_to,
                    benchmark_done_html=benchmark_done_html,
                ))
            # ── 内容工厂：跳转到生成页 ──
            return HTMLResponse(render_func(
                "agent_url_status.html",
                done=True,
                entry_id=entry_id,
                title=result.get("title", ""),
                summary=result.get("summary_preview", ""),
                return_to=return_to,
            ))
        else:
            result = job.get("result", {})
            error_msg = result.get("error", f"任务状态异常（{status}）")
            return HTMLResponse(render_func(
                "agent_url_status.html",
                error=f"链接处理失败: {error_msg[:200]}",
            ))

    @app.post("/api/agent/draft/{draft_id}/approve")
    async def api_agent_draft_approve(draft_id: int):
        ok = draft_store.update_status(draft_id, "approved")
        if not ok:
            raise HTTPException(404, "草稿未找到")
        return {"ok": True}

    @app.post("/api/agent/draft/{draft_id}/reject")
    async def api_agent_draft_reject(draft_id: int):
        ok = draft_store.update_status(draft_id, "rejected")
        if not ok:
            raise HTTPException(404, "草稿未找到")
        return {"ok": True}

    @app.post("/api/agent/draft/{draft_id}/edit")
    async def api_agent_draft_edit(
        draft_id: int,
        content_text: str = Form(...),
        title: str = Form(""),
    ):
        draft = draft_store.get_by_id(draft_id)
        if not draft:
            raise HTTPException(404, "草稿未找到")
        ok = draft_store.update_status(draft_id, draft["status"], content_text)
        if not ok:
            raise HTTPException(500, "更新失败")
        return {"ok": True}

    @app.post("/api/agent/draft/{draft_id}/regenerate")
    async def api_agent_draft_regenerate(draft_id: int):
        old = draft_store.get_by_id(draft_id)
        if not old:
            raise HTTPException(404, "草稿未找到")
        entry = await connector.get_entry(old["knowledge_id"])
        if not entry:
            raise HTTPException(404, "知识条目未找到")

        job_id = uuid.uuid4().hex[:12]
        _JOBS[job_id] = {"status": "pending", "result": {}}

        thread = threading.Thread(
            target=_run_generation,
            args=(job_id, entry, [old["platform"]], old.get("tone_variant", "standard"), old["knowledge_id"]),
            daemon=True,
        )
        thread.start()

        return JSONResponse({"status": "ok", "job_id": job_id, "platform": old["platform"]})

    # ═══════════════════════════════════════════
    # 评分 + 预测
    # ═══════════════════════════════════════════

    @app.post("/api/agent/draft/{draft_id}/score")
    async def api_agent_draft_score(draft_id: int):
        draft = draft_store.get_by_id(draft_id)
        if not draft:
            raise HTTPException(404, "草稿未找到")
        result = await score_and_predict(draft["content_text"], draft["platform"])
        scores = result["scores"]
        prediction = result["prediction"]
        if not scores.get("_error"):
            draft_store.save_score(draft_id, scores, prediction)
        return JSONResponse({"scores": scores, "prediction": prediction})

    @app.get("/api/agent/entry-preview/{entry_id}", response_class=HTMLResponse)
    async def entry_preview(entry_id: int):
        """HTMX 懒加载：返回知识条目的内容预览片段（已清洗 markdown）。"""
        import re as _re
        entry = await connector.get_entry(entry_id)
        if not entry:
            return HTMLResponse('<span class="text-red-400">条目未找到</span>')

        summary = entry.get("summary_markdown", "") or ""
        # 清洗 markdown 特殊字符，保留纯文本
        summary = _re.sub(r'^#+\s+', '', summary, flags=_re.MULTILINE)   # 标题
        summary = _re.sub(r'\*\*(.+?)\*\*', r'\1', summary)               # 加粗
        summary = _re.sub(r'\*(.+?)\*', r'\1', summary)                   # 斜体
        summary = _re.sub(r'`(.+?)`', r'\1', summary)                     # 行内代码
        summary = _re.sub(r'\[(.+?)\]\(.+?\)', r'\1', summary)            # 链接
        summary = _re.sub(r'^>\s*', '', summary, flags=_re.MULTILINE)     # 引用
        summary = _re.sub(r'^[-*+]\s+', '· ', summary, flags=_re.MULTILINE)  # 无序列表
        summary = _re.sub(r'^\d+\.\s+', '', summary, flags=_re.MULTILINE) # 有序列表
        summary = _re.sub(r'^---+$', '', summary, flags=_re.MULTILINE)    # 分割线
        summary = _re.sub(r'\n{3,}', '\n\n', summary)                     # 多余空行

        tags = entry.get("tags", "")
        author = entry.get("author", "")
        comments = entry.get("comments", [])

        lines = ['<div class="space-y-1.5">']
        if author:
            lines.append(f'<span class="text-[10px] text-zinc-400">作者: {author}</span>')
        if tags:
            tags_str = " ".join(f'<span class="text-[9px] px-1.5 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-zinc-500">{t.strip()}</span>' for t in tags.split(",")[:5])
            lines.append(f'<div class="flex flex-wrap gap-1">{tags_str}</div>')
        if summary:
            # 预览展示前 2000 字（可滚动），LLM 实际收到全文
            preview_text = summary[:2000]
            lines.append(
                f'<div class="text-[12px] text-zinc-600 dark:text-zinc-300 whitespace-pre-wrap max-h-[250px] overflow-y-auto">'
                f'{preview_text}'
                f'{"..." if len(summary) > 2000 else ""}'
                f'</div>'
            )
            lines.append(
                f'<p class="text-[10px] text-zinc-400 mt-1">'
                f'📤 LLM 收到全文（{len(summary)} 字{f" + {len(comments)} 条评论" if comments else ""}）'
                f'</p>'
            )
        if comments and isinstance(comments, list) and len(comments) > 0:
            lines.append(f'<p class="text-[10px] text-zinc-400">💬 {len(comments)} 条评论</p>')
        lines.append('</div>')
        return HTMLResponse("\n".join(lines))

    @app.get("/api/agent/draft/{draft_id}/score")
    async def api_agent_draft_get_score(draft_id: int):
        sc = draft_store.get_score(draft_id)
        return JSONResponse(sc or {"error": "暂无评分"})

    @app.get("/agent/draft/{draft_id}/score-row", response_class=HTMLResponse)
    async def agent_draft_score_row(draft_id: int):
        sc = draft_store.get_score(draft_id)
        if not sc:
            return HTMLResponse('<div class="text-sm text-gray-400 py-6 text-center">📊 点击下方按钮进行 AI 打分</div>')
        return HTMLResponse(render_func("agent_score_row.html", scores=sc, draft_id=draft_id))

    # ── 分项打分：标题 / 标签 ──

    @app.post("/api/agent/draft/{draft_id}/score-title")
    async def api_score_title(draft_id: int):
        draft = draft_store.get_by_id(draft_id)
        if not draft: raise HTTPException(404, "草稿未找到")
        raw = draft["content_text"]
        first_line = raw.strip().split("\n")[0] if raw else ""
        result = await score_title(first_line, draft["platform"])
        return JSONResponse(result)

    @app.post("/api/agent/draft/{draft_id}/score-tags")
    async def api_score_tags(draft_id: int):
        draft = draft_store.get_by_id(draft_id)
        if not draft: raise HTTPException(404, "草稿未找到")
        raw = draft["content_text"]
        # 提取标签行
        tag_lines = [l.strip() for l in raw.split("\n") if l.strip().startswith("#")]
        tags_text = " ".join(tag_lines) if tag_lines else ""
        result = await score_tags(tags_text, draft["platform"])
        return JSONResponse(result)

    # ── 单独编辑标题 / 标签 ──

    @app.post("/api/agent/draft/{draft_id}/edit-title")
    async def api_edit_draft_title(draft_id: int, title: str = Form(...)):
        draft = draft_store.get_by_id(draft_id)
        if not draft: raise HTTPException(404, "草稿未找到")
        raw = draft["content_text"]
        lines = raw.split("\n")
        replaced = False
        for i, line in enumerate(lines):
            if line.strip() and not line.strip().startswith("#"):
                lines[i] = title
                replaced = True
                break
        if not replaced:
            lines.insert(0, title)
        new_content = "\n".join(lines)
        ok = draft_store.update_status(draft_id, draft["status"], new_content)
        return {"ok": ok}

    @app.post("/api/agent/draft/{draft_id}/edit-tags")
    async def api_edit_draft_tags(draft_id: int, tags: str = Form(...)):
        draft = draft_store.get_by_id(draft_id)
        if not draft: raise HTTPException(404, "草稿未找到")
        raw = draft["content_text"]
        lines = raw.split("\n")
        # 去掉所有以 # 开头的标签行
        body_lines = [l for l in lines if not (l.strip().startswith("#") and len(l.strip()) < 80)]
        # 追加新标签（每个空格分隔的词如果没 # 就加 #）
        new_tags = []
        for t in tags.split():
            t = t.strip()
            if t:
                new_tags.append(t if t.startswith("#") else "#" + t)
        body_lines.append(" ".join(new_tags))
        new_content = "\n".join(body_lines)
        ok = draft_store.update_status(draft_id, draft["status"], new_content)
        return {"ok": ok}

    # ── 删除草稿 ──

    @app.delete("/api/agent/draft/{draft_id}")
    async def api_delete_draft(draft_id: int):
        ok = draft_store.delete_by_id(draft_id)
        if not ok: raise HTTPException(404, "草稿未找到")
        return {"ok": True}

    @app.delete("/api/agent/drafts/group/{knowledge_id}")
    async def api_delete_draft_group(knowledge_id: int):
        """删除某个知识条目下的所有草稿（移入回收站）。"""
        drafts = draft_store.list_by_knowledge(knowledge_id)
        deleted = 0
        for d in drafts:
            if draft_store.delete_by_id(d["id"]):
                deleted += 1
        logger.info("删除草稿组 knowledge_id=%d: %d 条", knowledge_id, deleted)
        return {"ok": True, "deleted": deleted}

    # ── 回收站 ──

    @app.get("/agent/trash", response_class=HTMLResponse)
    async def trash_page(request: Request):
        """回收站页面。"""
        # 自动清理过期条目
        auto_days = settings.AUTO_TRASH_DAYS
        if auto_days > 0:
            cleaned = draft_store.clean_expired_trash(auto_days)
            if cleaned:
                logger.info("自动清理回收站: %d 条（超过 %d 天）", cleaned, auto_days)

        trash_items = draft_store.list_trash(limit=100)
        return HTMLResponse(render_func(
            "trash.html",
            request=request,
            items=trash_items,
            auto_days=auto_days,
            platforms_meta=PLATFORM_META,
        ))

    @app.post("/api/agent/draft/{draft_id}/restore")
    async def api_restore_draft(draft_id: int):
        ok = draft_store.restore_from_trash(draft_id)
        if not ok: raise HTTPException(404, "草稿未找到或不在回收站")
        return {"ok": True}

    @app.delete("/api/agent/draft/{draft_id}/permanent")
    async def api_permanent_delete(draft_id: int):
        ok = draft_store.permanent_delete(draft_id)
        if not ok: raise HTTPException(404, "草稿未找到")
        return {"ok": True}

    @app.post("/api/agent/trash/empty")
    async def api_empty_trash():
        count = draft_store.empty_trash()
        return {"ok": True, "deleted": count}

    @app.post("/api/agent/trash/auto-days")
    async def api_set_auto_trash_days(days: int = Form(7)):
        """设置回收站自动清理天数。"""
        settings.AUTO_TRASH_DAYS = days
        cleaned = draft_store.clean_expired_trash(days)
        return {"ok": True, "days": days, "cleaned_now": cleaned}

    # ═══════════════════════════════════════════
    # OmniVault 连接状态
    # ═══════════════════════════════════════════

    @app.get("/api/health")
    async def api_health():
        ov_ok = await connector.health_check()
        return {
            "status": "ok",
            "omnicast": "running",
            "omnivault": "connected" if ov_ok else "disconnected",
        }


def _run_generation(job_id, entry, platforms, tone_variant, knowledge_id):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        from .generator import generate_for_platforms
        result = loop.run_until_complete(
            generate_for_platforms(entry, platforms, tone_variant, knowledge_id)
        )
        _JOBS[job_id] = {"status": "done", "result": result}
    except Exception as e:
        logger.error(f"生成任务失败: {e}", exc_info=True)
        _JOBS[job_id] = {"status": "failed", "result": {"error": str(e)[:500]}}
    finally:
        loop.close()


def _run_generation_v2_full(job_id, topic, entry_id, entry_ids, platforms, tone_variant, knowledge_id, generate_title, generate_tags, max_related, platform_for_style, selected_ids=""):
    """v2 完整生成流程 — 知识解析 + 内容生成，全部在后台线程执行。

    这样 API 可以立即返回轮询片段，用户不会因为 resolver 的 LLM 调用而长时间无反馈。
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        # ── 阶段 1: 知识解析 ──
        _JOBS[job_id]["phase"] = "resolving"
        from .resolver import KnowledgeResolver
        resolver = KnowledgeResolver()
        bundle = loop.run_until_complete(
            resolver.resolve(
                topic=topic,
                entry_id=entry_id,
                entry_ids=entry_ids,
                platform=platform_for_style,
                max_related=max_related,
            )
        )

        # 按用户勾选的 selected_ids 过滤关联条目
        if selected_ids:
            keep_ids = set()
            for part in selected_ids.split(","):
                part = part.strip()
                if part.isdigit():
                    keep_ids.add(int(part))
            if keep_ids:
                bundle.related_entries_full = [
                    e for e in bundle.related_entries_full
                    if e.get("id") in keep_ids
                ]
                logger.info("用户勾选过滤: 保留 %d/%d 条", len(bundle.related_entries_full), len(keep_ids))

        _JOBS[job_id]["bundle"] = bundle

        # ── 阶段 2: 内容生成 ──
        _JOBS[job_id]["phase"] = "generating"
        from .generator import generate_for_platforms_v2
        result = loop.run_until_complete(
            generate_for_platforms_v2(
                bundle, platforms, tone_variant, knowledge_id,
                generate_title=generate_title, generate_tags=generate_tags,
            )
        )
        _JOBS[job_id] = {
            "status": "done",
            "phase": "done",
            "result": result,
            "bundle": bundle,
        }
    except Exception as e:
        logger.error(f"v2 生成任务失败: {e}", exc_info=True)
        _JOBS[job_id] = {
            "status": "failed",
            "phase": "failed",
            "result": {"error": str(e)[:500]},
        }
    finally:
        loop.close()


def _run_benchmark_generation(job_id, reference_content, user_materials, platforms, tone_variant, knowledge_id):
    """后台线程：文案对标生成 — 7维分析 + 对标创作。"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        # ── 阶段 1: 对标分析 ──
        _JOBS[job_id]["phase"] = "analyzing"
        from .generator import generate_benchmarked_copy
        result = loop.run_until_complete(
            generate_benchmarked_copy(
                reference_content=reference_content,
                user_materials=user_materials,
                platforms=platforms,
                tone_variant=tone_variant,
                knowledge_id=knowledge_id,
            )
        )
        # 提取分析结果用于状态展示
        analysis = result.get("analysis", {})
        _JOBS[job_id] = {
            "status": "done",
            "phase": "done",
            "result": result,
            "analysis": analysis,
        }
    except Exception as e:
        logger.error(f"对标生成任务失败: {e}", exc_info=True)
        _JOBS[job_id] = {
            "status": "failed",
            "phase": "failed",
            "result": {"error": str(e)[:500]},
        }
    finally:
        loop.close()
