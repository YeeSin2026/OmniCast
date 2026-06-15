"""OmniCast 本地内容提取器 — 不依赖 OmniVault，独立抓取并提取文案。

流程：httpx 抓取页面 → 清洗 HTML → LLM 提取结构化内容 → 清洗特殊字符
"""

import json
import logging
import re

import httpx

from .llm_client import chat

logger = logging.getLogger(__name__)


def clean_content(text: str) -> str:
    """清洗文案中的特殊字符，保留正常的文本内容。

    处理：不可见控制字符、HTML 实体、零宽字符、多余空行、每行首尾空白。
    """
    if not text:
        return ""

    # 1. HTML 实体
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    text = text.replace('&quot;', '"').replace('&#39;', "'").replace('&nbsp;', ' ')
    text = re.sub(r'&#x?[0-9a-fA-F]+;', ' ', text)
    text = text.replace('&apos;', "'").replace('&rsquo;', "'").replace('&lsquo;', "'")
    text = text.replace('&rdquo;', '"').replace('&ldquo;', '"').replace('&mdash;', '—')

    # 2. 不可见控制字符（保留 \n \t）
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', text)
    text = text.replace('\r\n', '\n').replace('\r', '\n')

    # 3. 零宽字符
    text = re.sub(r'[​-‏ - ⁠-⁯﻿]', '', text)

    # 4. 每行去首尾空白，保留空行
    lines = [l.strip() for l in text.split('\n')]
    # 去掉开头和结尾的空行
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()

    # 5. 合并连续空行（最多保留一个空行）
    cleaned = []
    prev_empty = False
    for line in lines:
        if not line:
            if not prev_empty:
                cleaned.append('')
                prev_empty = True
        else:
            cleaned.append(line)
            prev_empty = False

    return '\n'.join(cleaned)

# ── LLM 提取 Prompt ──

EXTRACTION_SYSTEM = """你是一个内容提取器。从网页文本中提取以下信息，只输出 JSON，不要任何额外文字。

## 需要提取的字段

{
  "title": "视频标题/文章标题",
  "author": "作者/创作者名称",
  "platform": "平台名 (douyin/xiaohongshu/kuaishou/bilibili/weibo/weixin/youtube/instagram/tiktok/twitter/linkedin/threads/reddit/zhihu/web)",
  "raw_content": "正文/口播文案/帖子原文。必须完整提取原文，不要做任何改写、总结或删减。如果是视频口播文案提取口播全文；如果是文章提取正文全文。这个字段是最重要的。",
  "tags": "标签/话题（逗号分隔）",
  "source_url": "原始 URL"
}

## 规则

1. raw_content 必须完整保留原文，一字不改。这是用来做文案分析的，总结会丢失写作技法。
2. 从 URL 和页面内容推断 platform。
3. 如果字段找不到对应内容，填空字符串。
4. 只输出 JSON 对象，JSON 前后不要有任何文字或 markdown 标记。"""


# ── 平台检测 ──

PLATFORM_PATTERNS = [
    ("douyin", [r"douyin\.com", r"v\.douyin", r"iesdouyin"]),
    ("xiaohongshu", [r"xiaohongshu", r"xhslink"]),
    ("kuaishou", [r"kuaishou"]),
    ("bilibili", [r"bilibili", r"b23\.tv"]),
    ("weibo", [r"weibo", r"t\.cn"]),
    ("weixin", [r"weixin", r"mp\.weixin", r"wechat"]),
    ("zhihu", [r"zhihu", r"zh\.sh"]),
    ("douban", [r"douban"]),
    ("youtube", [r"youtube", r"youtu\.be"]),
    ("instagram", [r"instagram"]),
    ("tiktok", [r"tiktok"]),
    ("twitter", [r"twitter", r"x\.com", r"t\.co"]),
    ("linkedin", [r"linkedin"]),
    ("reddit", [r"reddit", r"redd\.it"]),
    ("threads", [r"threads\.net"]),
    ("pinterest", [r"pinterest", r"pin\.it"]),
    ("facebook", [r"facebook", r"fb\.com", r"fb\.watch"]),
    ("twitch", [r"twitch", r"clips\.twitch"]),
    ("medium", [r"medium\.com"]),
    ("spotify", [r"spotify"]),
    ("telegram", [r"t\.me", r"telegram"]),
    ("github", [r"github\.com"]),
    ("substack", [r"substack"]),
    ("vimeo", [r"vimeo"]),
    ("quora", [r"quora"]),
    ("snapchat", [r"snapchat"]),
]


def _detect_platform(url: str) -> str:
    for platform, patterns in PLATFORM_PATTERNS:
        for p in patterns:
            if re.search(p, url, re.IGNORECASE):
                return platform
    return "web"


def _clean_html(html: str) -> str:
    """把 HTML 清洗为纯文本，保留基本结构。"""
    # 去掉 script / style / 注释
    html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<!--.*?-->', '', html, flags=re.DOTALL)

    # 块级元素换行
    for tag in ['br', 'p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'tr', 'section', 'article', 'blockquote']:
        html = re.sub(rf'</?{tag}[^>]*>', '\n', html, flags=re.IGNORECASE)

    # 去掉所有剩余标签
    html = re.sub(r'<[^>]+>', '', html)

    # 解码常见 HTML 实体
    html = html.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    html = html.replace('&quot;', '"').replace('&#39;', "'").replace('&nbsp;', ' ')
    html = re.sub(r'&#x?[0-9a-fA-F]+;', ' ', html)

    # 压缩空白
    html = re.sub(r'[ \t]+', ' ', html)
    html = re.sub(r'\n\s*\n+', '\n\n', html)

    return html.strip()


async def extract_content(url: str) -> dict:
    """从 URL 提取内容。

    Returns:
        {"title": str, "author": str, "platform": str,
         "raw_content": str, "tags": str, "source_url": str}
    """
    # ── 1. 抓取页面 ──
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    raw_html = ""
    fetch_error = ""
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            raw_html = resp.text
            logger.info("页面抓取成功: %s (%d 字节)", url[:80], len(raw_html))
    except Exception as e:
        fetch_error = str(e)[:200]
        logger.warning("页面抓取失败: %s — %s", url[:80], fetch_error)

    # ── 2. 清洗 HTML → 纯文本 ──
    text = ""
    if raw_html:
        text = _clean_html(raw_html)
        # 限制输入长度（LLM 上下文有限）
        if len(text) > 12000:
            text = text[:12000] + "\n\n[内容过长，已截断]"
        logger.info("HTML 清洗完成: %d 字符", len(text))

    # ── 3. 构建 user prompt ──
    platform = _detect_platform(url)
    platform_hint = f"\n根据 URL 推测平台为 {platform}。"

    if text:
        user_prompt = f"""URL: {url}{platform_hint}

## 网页文本内容

{text}

---
请提取结构化信息（只输出 JSON）。"""
    else:
        # 抓取失败时，LLM 只能根据 URL 尽力而为
        user_prompt = f"""URL: {url}{platform_hint}

⚠️ 页面抓取失败（{fetch_error}），请根据 URL 推测平台和最可能的标题。raw_content 留空。只输出 JSON。"""

    # ── 4. LLM 提取 ──
    try:
        raw_result = await chat(
            [
                {"role": "system", "content": EXTRACTION_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            max_tokens=4096,
            timeout=60,
        )
    except Exception as e:
        logger.error("LLM 提取失败: %s", e)
        return {
            "title": "",
            "author": "",
            "platform": platform,
            "raw_content": text[:2000] if text else "",
            "tags": "",
            "source_url": url,
            "_error": f"LLM 提取失败: {str(e)[:100]}",
        }

    # ── 5. 解析 JSON ──
    raw_result = raw_result.strip()
    # 去掉可能的 markdown 代码块标记
    raw_result = re.sub(r'^```(?:json)?\s*\n?', '', raw_result)
    raw_result = re.sub(r'\n?```\s*$', '', raw_result)

    try:
        data = json.loads(raw_result)
    except json.JSONDecodeError:
        logger.warning("LLM 返回非标准 JSON，尝试提取: %s", raw_result[:200])
        data = {
            "title": "",
            "author": "",
            "platform": platform,
            "raw_content": raw_result[:2000],
            "tags": "",
        }

    # ── 6. 清洗 + 补全字段 ──
    data["source_url"] = url
    if not data.get("platform"):
        data["platform"] = platform
    # 清洗 raw_content 中的特殊字符
    data["raw_content"] = clean_content(data.get("raw_content", ""))
    # 清洗 title 和 tags
    if data.get("title"):
        data["title"] = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', data["title"]).strip()
    if data.get("tags"):
        data["tags"] = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', data["tags"]).strip()

    logger.info(
        "内容提取完成: platform=%s, title=%s, raw_content=%d 字",
        data.get("platform", "?"),
        data.get("title", "?")[:40],
        len(data.get("raw_content", "")),
    )

    return data
