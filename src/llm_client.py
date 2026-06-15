"""独立 LLM 客户端 — 适配 DeepSeek / OpenAI 兼容 API。"""

import logging
import httpx
from . import settings as config

logger = logging.getLogger(__name__)


async def chat(messages: list, temperature: float = 0.3, max_tokens: int = 4096, timeout: int = 180) -> str:
    """调用 LLM API，带重试。"""
    payload = {
        "model": config.LLM_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    last_err = ""
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    f"{config.LLM_BASE_URL}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {config.LLM_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                if resp.status_code in (429, 502, 503):
                    last_err = f"HTTP {resp.status_code}"
                    await __import__("asyncio").sleep(2 ** (attempt + 1))
                    continue
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            last_err = str(e)[:80]
            await __import__("asyncio").sleep(2)
        except Exception as e:
            last_err = str(e)[:200]
            break

    logger.error(f"LLM 调用失败（重试3次后）: {last_err}")
    return ""
