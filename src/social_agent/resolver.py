"""知识解析器 — 多路粗召回 + LLM 精排。

工作流程：
1. 接收话题 + entry_id
2. 三路粗召回：向量相似 + 标签关键词 + LLM 概念拆解多 query
3. 合并去重 → LLM 精排选出最佳 5 条（附带理由）
4. 组装 ContextBundle 返回给 generator
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from .. import connector
from ..llm_client import chat
from .optimizer import PerformanceOptimizer, build_optimization_addon

logger = logging.getLogger(__name__)

# 粗召回每路上限
_RECALL_LIMIT_PER_PATH = 20
# 精排候选项上限
_RANK_CANDIDATE_MAX = 50
# 精排最终选出条数
_RANK_TOP_K = 5

# LLM 概念拆解 prompt
CONCEPT_DECOMPOSE_PROMPT = """阅读以下知识条目，拆解出 3-5 个搜索关键词。
每行一个搜索词（3-8个字），不要编号、不要markdown、不要解释。
搜索词应该覆盖不同角度，用中文。

错误示范：
### 1. 关键词 — 解释
正确示范：
AI编程 设计审美
网页动效 交互体验"""

# LLM 精排 prompt
LLM_RANK_PROMPT = """你是内容策划主编. 从候选条目中选出「真正适合」和主条目一起创作的条目.
最多选 {top_k} 条, 但宁缺毋滥——不相关的坚决不要, 少于 {top_k} 条完全没问题.

标准:
- 同一话题域可互补 → 入选
- 不同但相关可对比递进 → 入选
- 完全不同话题 → 排除(即使没有更好的替代)
- 过于相似(讲同一件事) → 只选一条

输出 JSON 数组, 不要其他文字:
[{{"id": 12, "reason": "why (under 15 chars)"}}]"""


def _compute_score(rank: int, total: int, llm_ranked: bool) -> int:
    """根据排序位置计算相关性评分 (0-100)。"""
    if total <= 1:
        return 90
    if llm_ranked:
        # LLM 精排过：第1名最高分，线性递减
        return max(60, 100 - (rank - 1) * (40 // max(total, 1)))
    # 未精排（候选太少）：统一中等分数
    return 75


def _extract_tags(entry: dict) -> list[str]:
    """从条目中提取标签列表。"""
    tags = entry.get("tags", "")
    if isinstance(tags, str):
        return [t.strip() for t in tags.split(",") if t.strip()]
    if isinstance(tags, list):
        return [t.strip() for t in tags if t.strip()]
    return []


async def _llm_decompose_concepts(entry: dict) -> list[str]:
    """LLM 拆解主条目的关键概念，用于多 query 搜索。"""
    title = entry.get("title", "")[:100]
    summary = (entry.get("summary_markdown", "") or "")[:500]
    tags = entry.get("tags", "")[:100]

    prompt = f"标题：{title}\n标签：{tags}\n摘要：{summary}\n\n请拆解 3-5 个搜索词："

    try:
        result = await chat(
            [{"role": "user", "content": prompt}],
            temperature=0.3, max_tokens=200, timeout=30,
        )
        if not result:
            return []
        concepts = []
        for line in result.strip().split("\n"):
            line = line.strip()
            if not line or len(line) > 60:
                continue
            # 跳过明显的非搜索词行：以标记符号开头，或包含 markdown 格式
            if any(line.startswith(c) for c in ('#', '-', '*', '1', '2', '3', '4', '5', '扩', '目', '适', '`')):
                continue
            if '**' in line or '[' in line or ']' in line:
                continue
            # 清理残留符号
            for ch in '「」【】#*->':
                line = line.replace(ch, '')
            line = line.strip()
            if 2 <= len(line) <= 30:
                concepts.append(line)
        logger.info("LLM 概念拆解: %s", concepts[:5])
        return concepts[:5]
    except Exception as e:
        logger.warning("概念拆解失败: %s", e)
        return []


async def _multi_path_recall(primary_entry: dict) -> list[dict]:
    """三路粗召回：向量 + 标签关键词 + LLM 概念拆解多 query。"""
    if not primary_entry:
        return []

    primary_id = primary_entry.get("id", 0)
    tags = _extract_tags(primary_entry)

    # 路径 1：标签关键词搜索（用前 3 个标签）
    tag_query = " ".join(tags[:3]) if tags else ""

    # 路径 2：LLM 拆解概念
    concepts = await _llm_decompose_concepts(primary_entry)

    # 并行执行三路召回
    tasks = []

    # 路径 A：向量相似度（OmniVault 已有）
    # 直接调 list_entries 无法做向量搜索，走 OmniVault 的 get_related API
    # 但这里我们用 list_entries 的关键词搜索来模拟

    # 路径 B：标签关键词
    if tag_query:
        tasks.append(("tag", connector.list_entries(search=tag_query, limit=_RECALL_LIMIT_PER_PATH)))

    # 路径 C：LLM 拆解的每个概念分别搜索
    for concept in concepts[:4]:
        tasks.append(("concept", connector.list_entries(search=concept, limit=15)))

    if not tasks:
        return []

    # 并行执行
    results = await asyncio.gather(*[t[1] for t in tasks])

    # 合并去重
    seen = {primary_id}
    merged = []
    for (path_type, _), entries in zip(tasks, results):
        if not entries:
            continue
        for e in entries:
            eid = e.get("id")
            if eid and eid not in seen:
                seen.add(eid)
                e["_recall_path"] = path_type
                merged.append(e)
        logger.info("粗召回 [%s]: %d 条", path_type, len(entries))

    logger.info("粗召回合并去重: %d 条候选", len(merged))
    return merged[:_RANK_CANDIDATE_MAX]


async def _llm_rank(
    primary_entry: dict,
    candidates: list[dict],
    max_related: int = 5,
) -> tuple[list[dict], list[dict]]:
    """LLM 从候选中精排选出最佳的 top_k 条，附带选择理由。"""
    if not candidates:
        return [], []

    if len(candidates) <= max_related:
        # 候选项太少，不需要精排，全部保留并生成默认理由
        logger.info("候选仅 %d 条（≤%d），跳过精排", len(candidates), max_related)
        reasons = []
        total = len(candidates)
        for idx, c in enumerate(candidates):
            tags = _extract_tags(c)
            reason = "共同标签: " + ", ".join(tags[:3]) if tags else "内容相关性匹配"
            score = _compute_score(idx + 1, total, llm_ranked=False)
            c["_rank"] = idx + 1
            c["_reason"] = reason
            c["_score"] = score
            reasons.append({
                "id": c.get("id", 0),
                "title": c.get("title", "")[:60],
                "reason": reason,
                "rank": idx + 1,
                "score": score,
            })
        return candidates, reasons

    # 构建 prompt
    primary_title = primary_entry.get("title", "")[:100]
    primary_tags = primary_entry.get("tags", "")[:120]

    lines = [
        f"## 主条目\n标题：{primary_title}\n标签：{primary_tags}\n",
        f"## 候选条目（共 {len(candidates)} 条，最多选 {max_related} 条，不相关可以不选）",
    ]
    id_map = {}
    for c in candidates:
        cid = c.get("id", 0)
        id_map[str(cid)] = c
        lines.append(
            f"id={cid} | {c.get('title', '')[:80]}\n"
            f"  标签：{c.get('tags', '')[:100]}"
        )

    prompt = "\n".join(lines)
    prompt += f"\n\n请选出真正相关的条目（最多{max_related}条），不相关的不要选。输出 JSON 数组。"

    try:
        result = await chat(
            [{"role": "user", "content": prompt}],
            temperature=0.3, max_tokens=1024, timeout=60,
        )
        if not result:
            logger.warning("LLM 精排返回空")
            return candidates[:top_k], []

        # 解析 JSON
        result = result.strip()
        if result.startswith("```"):
            result = re.sub(r'^```\w*\n?', '', result)
            result = re.sub(r'\n?```$', '', result)
        logger.info("LLM 精排原始输出: %s", result[:500])
        rank_data = json.loads(result)
        # 兼容各种返回格式
        if isinstance(rank_data, list):
            picks = rank_data
        elif isinstance(rank_data, dict):
            picks = rank_data.get("picks", rank_data.get("results", []))
        else:
            picks = []
        if not isinstance(picks, list):
            picks = []

        ranked = []
        reasons = []
        for idx, pick in enumerate(picks):
            if not isinstance(pick, dict):
                continue
            cid = str(pick.get("id", pick.get("ID", "")))
            entry = id_map.get(cid)
            if entry:
                rank = idx + 1
                score = _compute_score(rank, len(picks), llm_ranked=True)
                tags = _extract_tags(entry)
                reason = pick.get("reason", pick.get("Reason", pick.get("理由", "")))
                if not reason and tags:
                    reason = "共同标签: " + ", ".join(tags[:3])
                elif not reason:
                    reason = "LLM 精排第 " + str(rank) + " 位"
                entry["_rank"] = rank
                entry["_reason"] = reason
                entry["_score"] = score
                ranked.append(entry)
                reasons.append({
                    "id": int(cid) if cid.isdigit() else 0,
                    "title": entry.get("title", "")[:60],
                    "reason": reason,
                    "rank": rank,
                    "score": score,
                })

        logger.info("LLM 精排: %d/%d 条入选", len(ranked), len(candidates))
        return ranked, reasons

    except Exception as e:
        logger.warning("LLM 精排失败: %s，取前 %d 条", e, top_k)
        return candidates[:top_k], []


@dataclass
class ContextBundle:
    """知识上下文包 — generator 的统一输入。"""

    mode: str = "knowledge-rich"
    topic: str = ""

    # knowledge-rich 模式字段
    wiki_answer: str = ""
    wiki_pages: list[dict] = field(default_factory=list)
    rag_results: list[dict] = field(default_factory=list)
    related_entries: list[dict] = field(default_factory=list)
    related_entries_full: list[dict] = field(default_factory=list)
    primary_entry: Optional[dict] = None

    # 精排结果
    ranked_entries: list[dict] = field(default_factory=list)
    ranking_reasons: list[dict] = field(default_factory=list)

    # style-driven 模式字段
    style_examples: list[dict] = field(default_factory=list)

    # 性能优化数据
    optimization_addon: str = ""
    performance_data: dict = field(default_factory=dict)

    # 元信息
    coverage: str = "low"
    coverage_assessment: str = ""

    @property
    def has_knowledge(self) -> bool:
        return self.mode == "knowledge-rich"

    @property
    def knowledge_source_count(self) -> int:
        return len(self.wiki_pages) + len(self.rag_results) + len(self.related_entries_full)


class KnowledgeResolver:
    """知识解析器。"""

    async def resolve(
        self,
        topic: str = "",
        entry_id: int = 0,
        entry_ids: list[int] | None = None,
        platform: str = "",
        max_related: int = 5,
    ) -> ContextBundle:
        """解析知识上下文。

        流程：粗召回(三路) → 合并去重 → LLM精排 → ContextBundle
        """
        # 1. 收集所有需要拉取的条目 ID
        all_entry_ids = []
        if entry_ids:
            all_entry_ids = list(entry_ids)
        elif entry_id:
            all_entry_ids = [entry_id]

        # 2. 拉取主条目 + 风格参考 + 知识解析
        fetch_tasks = [
            connector.resolve_knowledge(query=topic, entry_id=entry_id),
            connector.get_style_examples(platform=platform, limit=3),
        ]
        for eid in all_entry_ids[:6]:
            fetch_tasks.append(connector.get_entry(eid))

        results = await asyncio.gather(*fetch_tasks)
        knowledge = results[0]
        style_examples = results[1]
        fetched_entries = [r for r in results[2:] if r is not None]

        primary_entry = fetched_entries[0] if fetched_entries else None
        manual_entries = fetched_entries[1:] if len(fetched_entries) > 1 else []

        # 3. 评估覆盖度
        coverage = knowledge.get("coverage", "low")
        assessment = knowledge.get("assessment", "")

        # 4. 性能数据分析
        optimizer = PerformanceOptimizer()
        perf_analysis = optimizer.analyze(platform=platform, limit=10)
        opt_addon = build_optimization_addon(perf_analysis)

        # 5. 组装 ContextBundle
        wiki_data = knowledge.get("wiki", {})
        rag_data = knowledge.get("rag", {})

        if coverage in ("high", "medium") and primary_entry:
            # ── 多路粗召回 + LLM 精排 ──
            # 只在非手动多选时跑自动召回（手动选的条目已经覆盖了）
            if not entry_ids or len(entry_ids) <= 1:
                candidates = await _multi_path_recall(primary_entry)
                ranked_entries, ranking_reasons = await _llm_rank(primary_entry, candidates, max_related)
            else:
                ranked_entries, ranking_reasons = [], []

            # 自动关联合并：手动选的 + 精排选的，去重
            manual_ids = {e.get("id") for e in manual_entries if e.get("id")}
            auto_picks = [e for e in ranked_entries if e.get("id") not in manual_ids]

            related_entries_full = manual_entries + auto_picks

            # 拉取自动选中条目的完整内容
            auto_ids_to_fetch = {e.get("id") for e in auto_picks if e.get("id")}
            if auto_ids_to_fetch:
                fetch_tasks = [connector.get_entry(eid) for eid in auto_ids_to_fetch]
                auto_full = await asyncio.gather(*fetch_tasks)
                # 用完整内容替换摘要版本
                full_map = {e.get("id"): e for e in auto_full if e}
                for i, e in enumerate(related_entries_full):
                    if e.get("id") in full_map:
                        full = full_map[e.get("id")]
                        full["_rank"] = e.get("_rank", 0)
                        full["_reason"] = e.get("_reason", "")
                        full["_recall_path"] = e.get("_recall_path", "")
                        related_entries_full[i] = full
                logger.info("精排条目完整内容已拉取: %d 条", len(auto_full))

            bundle = ContextBundle(
                mode="knowledge-rich",
                topic=topic,
                wiki_answer=wiki_data.get("answer", ""),
                wiki_pages=wiki_data.get("pages", []),
                rag_results=rag_data.get("results", []),
                related_entries=knowledge.get("related_entries", []),
                related_entries_full=related_entries_full,
                ranked_entries=ranked_entries,
                ranking_reasons=ranking_reasons,
                primary_entry=primary_entry,
                style_examples=style_examples,
                optimization_addon=opt_addon,
                performance_data=perf_analysis,
                coverage=coverage,
                coverage_assessment=assessment,
            )
        else:
            bundle = ContextBundle(
                mode="style-driven",
                topic=topic,
                style_examples=style_examples,
                primary_entry=primary_entry,
                optimization_addon=opt_addon,
                performance_data=perf_analysis,
                coverage="low",
                coverage_assessment="知识库中没有足够的相关内容，将基于 LLM 知识和历史风格参考创作。",
            )

        logger.info(
            "知识解析完成: mode=%s, coverage=%s, sources=%d, ranked=%d",
            bundle.mode, coverage,
            bundle.knowledge_source_count, len(bundle.ranking_reasons),
        )
        return bundle
