"""视觉理解模块 — 调用本地 Gemma 4 (OMLX) 分析截图。

将截图发给视觉模型，让模型理解当前屏幕状态，
返回结构化动作指令或内容提取结果。

支持的视觉模型：
  - 本地 Gemma 4 26B (OMLX, 127.0.0.1:8000) — 首选，零成本零延迟
  - 远程 DeepSeek（无视觉能力，仅文本分析）
"""

import base64
import json
import logging
from typing import Optional

from ..summarizer import _chat_async

logger = logging.getLogger(__name__)

# ── Gemma 4 视觉接口（OMLX Ollama 兼容 API）──

OLLAMA_API = "http://127.0.0.1:8000/api/generate"
VISION_MODEL = "gemma4:26b"


async def analyze_screenshot(
    image_bytes: bytes,
    prompt: str,
    format_json: bool = True,
) -> Optional[dict]:
    """将截图发给本地视觉模型分析，返回结构化结果。

    Args:
        image_bytes: PNG 格式截图
        prompt: 告诉模型要看什么、做什么
        format_json: 是否要求模型返回 JSON

    Returns:
        解析后的 dict，失败返回 None
    """
    # 优先尝试本地 Gemma 4
    result = await _try_gemma4_vision(image_bytes, prompt, format_json)
    if result:
        return result

    # 降级：用 base64 编码后通过 DeepSeek（无视觉能力，只能靠描述）
    logger.warning("Gemma 4 不可用，降级为纯文本分析（无视觉）")
    return await _try_text_only(prompt, format_json)


async def _try_gemma4_vision(
    image_bytes: bytes,
    prompt: str,
    format_json: bool = True,
) -> Optional[dict]:
    """尝试调用本地 Gemma 4 视觉模型。"""
    import httpx

    img_base64 = base64.b64encode(image_bytes).decode("utf-8")

    full_prompt = prompt
    if format_json:
        full_prompt += "\n\n只返回 JSON，不要其他文字。确保 JSON 格式正确。"

    payload = {
        "model": VISION_MODEL,
        "prompt": full_prompt,
        "images": [img_base64],
        "stream": False,
        "options": {
            "temperature": 0.1,
            "num_predict": 2048,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(OLLAMA_API, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                response_text = data.get("response", "").strip()
                logger.debug(f"Gemma 4 视觉响应: {response_text[:200]}...")

                if format_json:
                    return _parse_json(response_text)
                return {"text": response_text}
            else:
                logger.warning(f"Gemma 4 返回 HTTP {resp.status_code}")
                return None
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        logger.warning(f"Gemma 4 连接失败: {e}")
        return None
    except Exception as e:
        logger.warning(f"Gemma 4 调用异常: {e}")
        return None


async def _try_text_only(prompt: str, format_json: bool = True) -> Optional[dict]:
    """纯文本降级方案（DeepSeek，无视觉能力）。"""
    try:
        if format_json:
            prompt += "\n\n只返回 JSON。"
        raw = await _chat_async(
            [{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=2048,
        )
        if format_json:
            return _parse_json(raw)
        return {"text": raw}
    except Exception as e:
        logger.warning(f"纯文本分析失败: {e}")
        return None


# ── 专用视觉 Prompt 模板 ──


def build_scrape_prompt(url: str, platform_name: str) -> str:
    """构建「从截图提取内容」的 prompt。

    用于替代 Playwright 的内容抓取。
    """
    return f"""你是一个视觉内容提取器。请从这张 {platform_name} 网页截图中提取以下信息：

1. **标题**：文章/视频/笔记的标题
2. **作者**：发布者用户名
3. **正文**：主要内容文字
4. **图片 URL**：如果截图中有图片链接或图片，列出它们的 src（如果可见）
5. **评论**：如果有可见的评论，提取评论文本（最多 20 条）

当前页面 URL: {url}

返回 JSON 格式：
{{
  "title": "...",
  "author": "...",
  "text_content": "...",
  "images": ["url1", "url2"],
  "comments": [{{"user": "...", "content": "...", "likes": 0}}]
}}

对于无法从截图中获取的字段，设为空字符串或空数组。"""


def build_som_click_prompt(action_description: str, elements: list) -> str:
    """构建 SoM（Set-of-Mark）风格的点击定位 prompt。

    和旧 build_click_prompt 的区别：
    - 旧：让模型预测像素坐标 → 模型不擅长坐标回归，误差大
    - 新：让模型选择元素编号 → 查表得精确坐标，准确率 94%+

    需要先调用 som.scan_elements() + som.overlay_markers() 生成带编号的截图。
    """
    from .som import build_som_prompt as _build
    return _build(action_description, elements)


def build_click_prompt(action_description: str) -> str:
    """构建「找到按钮并返回坐标」的 prompt。"""
    return f"""你是一个 GUI 操作定位器。请分析截图，找到以下操作目标的准确坐标：

任务：{action_description}

返回 JSON 格式：
{{
  "found": true/false,
  "action": "click|type|scroll|wait",
  "target": "目标元素的描述",
  "x": 数字,  // 像素 X 坐标（相对于截图左上角）
  "y": 数字,  // 像素 Y 坐标（相对于截图左上角）
  "confidence": 0.0-1.0,  // 识别置信度
  "fallback": "如果找不到目标，描述应该怎么找"
}}

注意：
- 坐标应该是元素中心点
- 如果找不到明确目标，设 found=false 并给出 fallback 建议
- 对于文本输入框，action 设为 "type"，并给出输入框中心坐标"""


def build_verify_prompt(expected: str) -> str:
    """构建「验证操作结果」的 prompt。"""
    return f"""请检查截图，确认以下操作是否成功：

预期结果：{expected}

返回 JSON：
{{
  "success": true/false,
  "confidence": 0.0-1.0,
  "evidence": "你在截图中看到了什么，支持你的判断",
  "next_action": "如果失败，建议下一步做什么"
}}"""


# ── JSON 解析 ──


def _parse_json(raw: str) -> Optional[dict]:
    """从 LLM 响应中解析 JSON。"""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    for delimiter in ["```json", "```"]:
        if delimiter in raw:
            try:
                return json.loads(raw.split(delimiter)[1].split("```")[0].strip())
            except (json.JSONDecodeError, IndexError):
                continue
    try:
        start = raw.index("{")
        end = raw.rindex("}") + 1
        return json.loads(raw[start:end])
    except (ValueError, json.JSONDecodeError):
        pass
    return None
