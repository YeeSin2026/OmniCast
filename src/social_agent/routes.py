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


def register_routes(app, jinja_env, render_func):
    from .store import DraftStore
    from .generator import generate_for_platforms, generate_for_platforms_v2
    from .resolver import KnowledgeResolver
    from .scorer import score_and_predict, score_title, score_tags
    from .prompts import ALL_PLATFORMS, PLATFORM_META

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

    @app.get("/api/agent/generate/{entry_id}/ranking", response_class=HTMLResponse)
    async def agent_generate_ranking(entry_id: int):
        """HTMX 懒加载：显示 AI 为这个条目选择的关联知识及理由。"""
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
            return HTMLResponse(
                '<div class="text-[13px] text-zinc-400 dark:text-zinc-500 py-3">'
                '未找到高度关联的知识条目</div>'
            )

        return HTMLResponse(render_func(
            "ranking_reasons.html",
            reasons=reasons,
            entry_id=entry_id,
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
            html = ""
            # v2 模式指示器
            if mode:
                mode_badge = {
                    "knowledge-rich": "🧠 知识驱动",
                    "style-driven": "🎨 风格驱动",
                }.get(mode, mode)
                html += f'<div class="text-[11px] text-zinc-400 mb-2">模式: {mode_badge} · 覆盖度: {coverage}</div>'
            if errors:
                html += '<div class="text-[13px] text-amber-600 dark:text-amber-400 mb-2">' + "<br>".join(errors) + "</div>"
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
                html += f'<div class="text-[13px] text-emerald-600 dark:text-emerald-400 mb-2">生成完成！共 {len(drafts)} 个平台</div>' + "".join(links)
            return HTMLResponse(html or "生成完成，但无结果")
        elif job["status"] == "failed":
            error = job["result"].get("error", "生成失败")
            return HTMLResponse(f'<div class="text-[13px] text-red-500 dark:text-red-400">❌ {error[:200]}</div>')
        else:
            return HTMLResponse(
                f'<div id="gen-poll" hx-get="/api/agent/generate/status/{job_id}" '
                f'hx-trigger="every 2s" hx-swap="outerHTML">'
                f'<span class="text-[13px] text-zinc-500 dark:text-zinc-400">'
                f'<span class="inline-block w-4 h-4 border-2 border-zinc-300 dark:border-zinc-600 border-t-zinc-500 dark:border-t-zinc-400 rounded-full animate-spin mr-2 align-middle"></span>'
                f'正在生成中，请稍候…</span></div>'
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
        """v2 生成 — 先解析知识上下文，再生成内容。

        流程：KnowledgeResolver → ContextBundle → generate_for_platforms_v2
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

        # Step 1: 解析知识上下文
        resolver = KnowledgeResolver()
        bundle = await resolver.resolve(
            topic=search_topic,
            entry_id=knowledge_id,
            platform=platform_list[0] if len(platform_list) == 1 else "",
            max_related=max_related,
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

        # Step 2: 生成（在后台线程中运行异步任务）
        job_id = uuid.uuid4().hex[:12]
        _JOBS[job_id] = {
            "status": "pending",
            "result": {},
            "bundle": bundle,  # 保存 bundle 供状态查询使用
        }

        thread = threading.Thread(
            target=_run_generation_v2,
            args=(job_id, bundle, platform_list, tone_variant, knowledge_id, generate_title, generate_tags),
            daemon=True,
        )
        thread.start()

        # 返回轮询片段（含覆盖度信息）
        coverage_badge = {
            "high": '<span class="text-[11px] px-2 py-0.5 rounded-full bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400">知识覆盖: 高</span>',
            "medium": '<span class="text-[11px] px-2 py-0.5 rounded-full bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400">知识覆盖: 中</span>',
            "low": '<span class="text-[11px] px-2 py-0.5 rounded-full bg-zinc-100 dark:bg-zinc-700 text-zinc-500 dark:text-zinc-400">风格驱动模式</span>',
        }.get(bundle.coverage, "")

        return HTMLResponse(
            f'<div id="gen-poll" hx-get="/api/agent/generate/status/{job_id}" '
            f'hx-trigger="every 2s" hx-swap="outerHTML">'
            f'<div class="flex items-center gap-3 mb-2">{coverage_badge}'
            f'<span class="text-[11px] text-zinc-400">{bundle.mode} · wiki:{len(bundle.wiki_pages)}页 · rag:{len(bundle.rag_results)}条</span></div>'
            f'<span class="text-[13px] text-zinc-500 dark:text-zinc-400">'
            f'<span class="inline-block w-4 h-4 border-2 border-zinc-300 dark:border-zinc-600 border-t-zinc-500 dark:border-t-zinc-400 rounded-full animate-spin mr-2 align-middle"></span>'
            f'正在生成中，请稍候…</span></div>'
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
        """多条目合并生成 — 用户手动选择了多条知识条目一起创作。"""
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

        # 解析知识上下文（传入多个 entry_id）
        resolver = KnowledgeResolver()
        bundle = await resolver.resolve(
            topic=search_topic,
            entry_ids=id_list,
            platform=platform_list[0] if len(platform_list) == 1 else "",
            max_related=max_related,
        )

        # 后台生成
        job_id = uuid.uuid4().hex[:12]
        _JOBS[job_id] = {"status": "pending", "result": {}, "bundle": bundle}

        thread = threading.Thread(
            target=_run_generation_v2,
            args=(job_id, bundle, platform_list, tone_variant, id_list[0], generate_title, generate_tags),
            daemon=True,
        )
        thread.start()

        coverage_badge = {
            "high": '<span class="text-[11px] px-2 py-0.5 rounded-full bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400">知识覆盖: 高</span>',
            "medium": '<span class="text-[11px] px-2 py-0.5 rounded-full bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400">知识覆盖: 中</span>',
            "low": '<span class="text-[11px] px-2 py-0.5 rounded-full bg-zinc-100 dark:bg-zinc-700 text-zinc-500 dark:text-zinc-400">风格驱动模式</span>',
        }.get(bundle.coverage, "")

        return HTMLResponse(
            f'<div id="gen-poll" hx-get="/api/agent/generate/status/{job_id}" '
            f'hx-trigger="every 2s" hx-swap="outerHTML">'
            f'<div class="flex items-center gap-3 mb-2">{coverage_badge}'
            f'<span class="text-[11px] text-zinc-400">{bundle.mode} · wiki:{len(bundle.wiki_pages)}页 · rag:{len(bundle.rag_results)}条 · 手动选择:{len(id_list)}条</span></div>'
            f'<span class="text-[13px] text-zinc-500 dark:text-zinc-400">'
            f'<span class="inline-block w-4 h-4 border-2 border-zinc-300 dark:border-zinc-600 border-t-zinc-500 dark:border-t-zinc-400 rounded-full animate-spin mr-2 align-middle"></span>'
            f'正在生成中，请稍候…</span></div>'
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
    async def api_agent_url_submit(url: str = Form(...)):
        """接收 URL，提交给 OmniVault 处理，返回进度轮询片段。

        支持直接粘贴抖音/小红书等平台的分享口令文本，自动提取其中的链接。
        """
        import re
        url = url.strip()
        if not url:
            return HTMLResponse(render_func("agent_url_status.html", error="请输入链接"))

        # 从分享口令中提取 URL（如 "0.28 复制打开抖音... https://v.douyin.com/xxx/ ..."）
        url_match = re.search(r'https?://\S+', url)
        if url_match:
            url = url_match.group(0).rstrip('.,;:!?）)')
            logger.info("从分享口令中提取链接: %s", url)
        elif not re.match(r'^https?://', url):
            return HTMLResponse(render_func(
                "agent_url_status.html",
                error="未识别到有效链接。请粘贴包含 https:// 的完整链接或平台分享口令。",
            ))

        result = await connector.submit_url(url)
        if not result:
            return HTMLResponse(render_func(
                "agent_url_status.html",
                error="OmniVault 不可用，无法处理链接。请稍后再试，或从下方知识库中选择现有条目。",
            ))

        jobs = result.get("jobs", [])
        if not jobs:
            return HTMLResponse(render_func("agent_url_status.html", error="提交失败，未返回任务。"))

        job_id = jobs[0].get("job_id", "")
        if not job_id:
            return HTMLResponse(render_func("agent_url_status.html", error="未获取到有效任务 ID。"))

        return HTMLResponse(render_func("agent_url_status.html", job_id=job_id))

    @app.get("/api/agent/url-status/{job_id}")
    async def api_agent_url_status(job_id: str):
        """轮询 OmniVault 任务状态，完成时返回带重定向的片段。"""
        job = await connector.get_job_status(job_id)
        if not job:
            return HTMLResponse(render_func(
                "agent_url_status.html",
                error="无法连接 OmniVault，请稍后重试。",
            ))

        status = job.get("status", "")
        if status in ("pending", "processing"):
            return HTMLResponse(render_func("agent_url_status.html", job_id=job_id))
        elif status == "done":
            result = job.get("result", {})
            entry_id = result.get("entry_id")
            if not entry_id:
                return HTMLResponse(render_func(
                    "agent_url_status.html",
                    error="处理完成但未获取到条目 ID，请检查 OmniVault 日志。",
                ))
            return HTMLResponse(render_func(
                "agent_url_status.html",
                done=True,
                entry_id=entry_id,
                title=result.get("title", ""),
                summary=result.get("summary_preview", ""),
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


def _run_generation_v2(job_id, bundle, platforms, tone_variant, knowledge_id, generate_title=True, generate_tags=True):
    """v2 生成后台任务 — 使用 ContextBundle。"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        from .generator import generate_for_platforms_v2
        result = loop.run_until_complete(
            generate_for_platforms_v2(bundle, platforms, tone_variant, knowledge_id, generate_title=generate_title, generate_tags=generate_tags)
        )
        _JOBS[job_id] = {"status": "done", "result": result}
    except Exception as e:
        logger.error(f"v2 生成任务失败: {e}", exc_info=True)
        _JOBS[job_id] = {"status": "failed", "result": {"error": str(e)[:500]}}
    finally:
        loop.close()
