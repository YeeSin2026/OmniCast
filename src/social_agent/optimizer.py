"""内容优化器 — 基于发布数据反馈优化未来创作。

核心理念：
  发布 → 收集数据（views/likes/shares/comments）→ 分析「什么有效」
  → 将洞察注入下一轮生成的 prompt → 内容越来越好

工作流程：
  1. 获取历史已发布内容 + 评分 + 表现数据
  2. 对比高分 vs 低分内容，提取差异模式
  3. 生成「优化简报」，注入 ContextBundle
  4. Generator 在生成时参考简报调整策略
"""

import json
import logging
from typing import Optional

from .store import DraftStore

logger = logging.getLogger(__name__)


class PerformanceOptimizer:
    """基于发布数据的内容优化器。"""

    def __init__(self):
        self.store = DraftStore()

    def analyze(self, platform: str = "", limit: int = 10) -> dict:
        """分析历史表现数据，生成优化简报。

        Args:
            platform: 目标平台（为空则分析所有平台）
            limit: 分析的样本数量

        Returns:
            {insights: [...], top_patterns: [...], avoid_patterns: [...], summary: "..."}
        """
        items = self.store.get_performance_summary(platform=platform, limit=limit)

        if not items:
            return {
                "has_data": False,
                "summary": "暂无发布数据，无法进行性能优化。随着内容发布和数据积累，系统将自动学习。",
                "insights": [],
            }

        # 筛选有评分数据的
        scored = [i for i in items if i.get("composite") is not None]

        insights = []

        # 1. 评分维度分析：哪些维度的高分与高互动相关
        if len(scored) >= 3:
            dimension_insight = self._analyze_dimensions(scored)
            if dimension_insight:
                insights.append(dimension_insight)

        # 2. 内容长度分析
        length_insight = self._analyze_length(items)
        if length_insight:
            insights.append(length_insight)

        # 3. 标题模式分析
        if len(scored) >= 2:
            title_insight = self._analyze_title_patterns(scored)
            if title_insight:
                insights.append(title_insight)

        # 4. 生成 top/avoid 模式
        top_patterns = self._extract_top_patterns(scored[:5])
        avoid_patterns = self._extract_avoid_patterns(scored[-3:]) if len(scored) >= 5 else []

        summary = self._generate_summary(items, scored)

        return {
            "has_data": True,
            "sample_count": len(items),
            "scored_count": len(scored),
            "insights": insights,
            "top_patterns": top_patterns,
            "avoid_patterns": avoid_patterns,
            "summary": summary,
        }

    def _analyze_dimensions(self, scored: list[dict]) -> Optional[str]:
        """分析评分维度与表现的关系。"""
        # 计算每个维度的平均分
        dims = ["er", "sr", "hp", "ql", "na", "ab", "ts"]
        top_half = scored[: len(scored) // 2] if len(scored) >= 4 else scored[:1]
        bottom_half = scored[len(scored) // 2 :]

        top_avgs = {}
        bottom_avgs = {}
        for dim in dims:
            top_vals = [i.get(dim, 0) or 0 for i in top_half]
            bottom_vals = [i.get(dim, 0) or 0 for i in bottom_half]
            top_avgs[dim] = sum(top_vals) / max(len(top_vals), 1)
            bottom_avgs[dim] = sum(bottom_vals) / max(len(bottom_vals), 1)

        # 找出差异最大的维度
        gaps = {}
        for dim in dims:
            gaps[dim] = top_avgs[dim] - bottom_avgs[dim]

        biggest_gap = max(gaps, key=gaps.get)

        dim_labels = {
            "er": "情感共鸣(ER)",
            "sr": "社会共振(SR)",
            "hp": "钩子强度(HP)",
            "ql": "金句密度(QL)",
            "na": "叙事性(NA)",
            "ab": "受众广度(AB)",
            "ts": "分享冲动(TS)",
        }

        if gaps[biggest_gap] > 0.5:
            return (
                f"高分内容在「{dim_labels.get(biggest_gap, biggest_gap)}」维度上"
                f"平均高出 {gaps[biggest_gap]:.1f} 分。建议在新内容中重点强化此维度。"
            )
        return None

    def _analyze_length(self, items: list[dict]) -> Optional[str]:
        """分析内容长度与互动的关系。"""
        lengths = []
        for item in items:
            text = item.get("content_text", "")
            if text:
                lengths.append(len(text))

        if len(lengths) < 2:
            return None

        avg_len = sum(lengths) / len(lengths)
        return (
            f"历史内容平均长度 {avg_len:.0f} 字。"
            f"建议在目标平台的字符限制内保持相似密度。"
        )

    def _analyze_title_patterns(self, scored: list[dict]) -> Optional[str]:
        """分析高分标题的共同特征。"""
        top_titles = [i.get("title", "") for i in scored[:3] if i.get("title")]

        # 简单模式检测
        has_question = any("?" in t or "？" in t for t in top_titles)
        has_number = any(any(c.isdigit() for c in t) for t in top_titles)
        has_howto = any(("如何" in t or "怎么" in t or "怎样" in t) for t in top_titles)

        patterns = []
        if has_question:
            patterns.append("疑问句式")
        if has_number:
            patterns.append("含数字")
        if has_howto:
            patterns.append("How-to 教程型")

        if patterns:
            return f"高分标题常见模式: {', '.join(patterns)}。可在新标题中尝试使用。"
        return None

    def _extract_top_patterns(self, top_items: list[dict]) -> list[str]:
        """从高分内容中提取可复用的模式。"""
        patterns = []
        for item in top_items[:3]:
            title = item.get("title", "")[:60]
            one_liner = item.get("one_liner", "")[:100]
            if one_liner:
                patterns.append(f"[{title}] {one_liner}")
        return patterns[:3]

    def _extract_avoid_patterns(self, bottom_items: list[dict]) -> list[str]:
        """从低分内容中提取应避免的模式。"""
        patterns = []
        for item in bottom_items[:2]:
            title = item.get("title", "")[:60]
            one_liner = item.get("one_liner", "")[:100]
            if one_liner:
                patterns.append(f"[{title}] 问题: {one_liner}")
        return patterns[:2]

    def _generate_summary(self, all_items: list[dict], scored: list[dict]) -> str:
        """生成优化摘要。"""
        total_published = len(all_items)
        total_scored = len(scored)

        if total_scored == 0:
            return (
                f"已有 {total_published} 条内容发布，但尚未评分。"
                f"建议对发布内容进行评分以获得优化建议。"
            )

        if scored:
            top_score = max(i.get("composite", 0) or 0 for i in scored)
            avg_score = sum(i.get("composite", 0) or 0 for i in scored) / len(scored)
            return (
                f"基于 {total_published} 条已发布内容分析："
                f"平均评分 {avg_score:.1f}/10，最高 {top_score:.1f}/10。"
            )

        return "数据积累中，优化建议将随数据增加而改善。"


def build_optimization_addon(analysis: dict) -> str:
    """将优化分析结果转为可注入 prompt 的文本。"""
    if not analysis.get("has_data"):
        return ""

    parts = ["\n## 性能优化简报（基于历史发布数据自动生成）\n"]
    parts.append(f"分析样本: {analysis.get('sample_count', 0)} 条已发布内容\n")

    if analysis.get("summary"):
        parts.append(f"\n{analysis['summary']}\n")

    insights = analysis.get("insights", [])
    if insights:
        parts.append("\n### 关键洞察")
        for ins in insights:
            parts.append(f"- {ins}")

    top = analysis.get("top_patterns", [])
    if top:
        parts.append("\n### 高分内容参考")
        for p in top:
            parts.append(f"- {p}")

    avoid = analysis.get("avoid_patterns", [])
    if avoid:
        parts.append("\n### 应避免的模式")
        for p in avoid:
            parts.append(f"- {p}")

    parts.append(
        "\n请在创作时参考以上数据。这不是死板的规则，而是数据驱动的方向性建议。"
    )

    return "\n".join(parts)
