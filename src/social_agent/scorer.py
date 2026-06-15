"""AI布道内容打分引擎 — 7维评分 + 盲预测 + 复盘。改编自 Cheat on Content。"""

import asyncio
import json
import logging
from typing import Optional

from ..llm_client import chat

logger = logging.getLogger(__name__)

RUBRIC_SYSTEM = """你是 AI 布道内容的专业评委。你的任务是对一篇社媒内容进行 7 维评分。

每个维度 0-5 整数分。只输出 JSON，不要解释。

### ER — Emotional Resonance（情感共鸣，权重 ×1.5）
内容能否让读者产生一种具体的、能命名的情感？
0: 纯信息传递 | 3: 一般共鸣 | 5: 锐利的自我识别

### SR — Social Resonance（社会共振，权重 ×1.5）
触及当下的、有争议的或结构性的社会/行业模式？
0: 纯技术层面 | 3: 触到现象但无新视角 | 5: 命名了读者认识但无语言形容的模式

### HP — Hook Potential（钩子强度，权重 ×1.5）
前 3 秒/前 1 句能不能逼读者继续看下去？
0: 通用开场 | 3: 具体承诺或反直觉断言 | 5: 读者无法停止处理的场景

### QL — Quotable Lines（金句密度，权重 ×1.0）
至少 2-3 行能被截图独立传播？
0: 全是叙述 | 3: 有一句 | 5: 多句分布在全文

### NA — Narrativity（叙事性，权重 ×1.0）
有铺垫→升级→收束的弧线？
0: 列表式 | 3: 松散主线 | 5: 紧凑三幕，结尾 payoff 开场已埋好

### AB — Audience Breadth（受众广度，权重 ×1.0）
0: 极小众(研究员) | 3: 中等(从业者) | 5: 普世(普通人的AI好奇/焦虑)

### TS — Topic Shareability（分享冲动，权重 ×1.0）
转发是否暴露转发者的处境？
1: 暴露焦虑/被淘汰恐惧 | 3: 安全中性 | 5: 转发即表演(身份信号)

## 综合分
composite = (ER×1.5 + SR×1.5 + HP×1.5 + QL + NA + AB + TS) / 8.5 × 2.0

## 输出
{"ER": int, "SR": int, "HP": int, "QL": int, "NA": int, "AB": int, "TS": int, "composite": float, "one_liner": "一句话总结核心优势或致命伤"}"""

PREDICT_SYSTEM = """你是社媒内容传播预测专家。基于以下内容+评分预测传播表现。

Bucket 定义（中小账号）:
bottom: <1000 | base: 1000-5000 | hit: 5000-50000 | viral: 50000-500000 | mega: >500000

输出 JSON:
{"headline_bucket": "hit", "central_estimate": 15000, "distribution": {"bottom": 10, "base": 30, "hit": 40, "viral": 15, "mega": 5}, "reason": "核心理由", "risk_factor": "最大风险"}"""


def compute_composite(scores: dict) -> float:
    er = scores.get("ER", 0)
    sr = scores.get("SR", 0)
    hp = scores.get("HP", 0)
    ql = scores.get("QL", 0)
    na = scores.get("NA", 0)
    ab = scores.get("AB", 0)
    ts = scores.get("TS", 0)
    raw = (er * 1.5 + sr * 1.5 + hp * 1.5 + ql + na + ab + ts) / 8.5
    return round(raw * 2.0, 1)


async def score_content(content_text: str, platform: str = "") -> dict:
    platform_hint = f"\n这是一篇发布在 {platform} 上的内容。请根据该平台的特点调整评分预期。" if platform else ""
    messages = [
        {"role": "system", "content": RUBRIC_SYSTEM},
        {"role": "user", "content": f"请对以下内容进行 7 维评分。{platform_hint}\n\n---\n{content_text[:6000]}\n---\n\n只输出 JSON。"},
    ]
    try:
        result = await chat(messages, temperature=0.3, max_tokens=1024)
        result = result.strip()
        if result.startswith("```"):
            result = result.split("\n", 1)[-1]
            if result.endswith("```"):
                result = result[:-3]
        scores = json.loads(result.strip())
        scores["composite"] = compute_composite(scores)
        return scores
    except Exception as e:
        logger.warning(f"打分失败: {e}")
        return {"ER": 0, "SR": 0, "HP": 0, "QL": 0, "NA": 0, "AB": 0, "TS": 0, "composite": 0, "one_liner": f"打分失败: {str(e)[:80]}", "_error": True}


async def predict_performance(content_text: str, scores: dict, platform: str = "") -> dict:
    messages = [
        {"role": "system", "content": PREDICT_SYSTEM},
        {"role": "user", "content": f"请预测以下 {platform} 内容的传播表现。\n\n## 7维评分\n{json.dumps(scores, ensure_ascii=False, indent=2)}\n\n## 内容\n{content_text[:5000]}\n\n只输出 JSON。"},
    ]
    try:
        result = await chat(messages, temperature=0.5, max_tokens=512)
        result = result.strip()
        if result.startswith("```"):
            result = result.split("\n", 1)[-1]
            if result.endswith("```"):
                result = result[:-3]
        return json.loads(result.strip())
    except Exception as e:
        logger.warning(f"预测失败: {e}")
        return {"headline_bucket": "unknown", "central_estimate": 0, "distribution": {}, "reason": f"预测失败: {str(e)[:80]}", "risk_factor": "N/A", "_error": True}


async def score_and_predict(content_text: str, platform: str = "") -> dict:
    scores = await score_content(content_text, platform)
    if not scores.get("_error"):
        prediction = await predict_performance(content_text, scores, platform)
    else:
        prediction = {"_error": True, "reason": "打分失败，跳过预测"}
    return {"scores": scores, "prediction": prediction}
