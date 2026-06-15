"""macOS 后台截图 + 窗口管理。

吸收 mac-cua 方案：截图不激活窗口，点击不抢光标。

截特定窗口：
  1. AppleScript 获取窗口 bounds（不需要激活）
  2. screencapture -R x,y,w,h 截取该区域（不需要激活）
  3. 坐标无需转换 — AppleScript System Events position 即 screencapture 左上角原点

后台点击：
  由 bin/bgclick (Swift + CGEventPostToPid) 实现，见 action.py
"""

import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# ── 二进制路径 ──

_BIN_DIR = Path(__file__).resolve().parent / "bin"


# ── 截图 ──


def capture_screen(region: Optional[Tuple[int, int, int, int]] = None) -> bytes:
    """截取整个屏幕或指定区域，返回 PNG 字节。

    Args:
        region: (x, y, width, height)，None 则截全屏

    Returns:
        PNG 格式的截图字节
    """
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        tmp_path = f.name

    try:
        if region:
            x, y, w, h = region
            subprocess.run(
                ["screencapture", "-R", f"{x},{y},{w},{h}", "-x", "-t", "png", tmp_path],
                check=True, capture_output=True, timeout=10,
            )
        else:
            subprocess.run(
                ["screencapture", "-x", "-t", "png", tmp_path],
                check=True, capture_output=True, timeout=10,
            )

        data = Path(tmp_path).read_bytes()
        os.unlink(tmp_path)
        return data
    except subprocess.TimeoutExpired:
        logger.warning("截图超时")
        os.unlink(tmp_path)
        return b""
    except Exception as e:
        logger.warning(f"截图失败: {e}")
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        return b""


def capture_window_by_title(title_contains: str) -> bytes:
    """截取标题包含指定字符串的窗口（不激活窗口）。

    通过 AppleScript 获取窗口位置 → screencapture -R 截取区域。
    整个过程窗口始终在后台。
    """
    bounds = get_window_bounds(title_contains)
    if not bounds:
        logger.warning(f"未找到窗口: {title_contains}")
        return b""
    return capture_screen(region=bounds)


# ── 窗口信息 ──


def get_window_bounds(title_contains: str) -> Optional[Tuple[int, int, int, int]]:
    """通过 AppleScript 获取窗口边界（不需要激活窗口）。

    Returns:
        (x, y, width, height) — x,y 是左上角坐标，可直接用于 screencapture -R
    """
    script = f'''
    tell application "System Events"
        set targetProcess to first process whose name contains "{title_contains}"
        set frontWindow to window 1 of targetProcess
        set winPos to position of frontWindow
        set winSize to size of frontWindow
        set x to item 1 of winPos
        set y to item 2 of winPos
        set w to item 1 of winSize
        set h to item 2 of winSize
        return (x as text) & "," & (y as text) & "," & (w as text) & "," & (h as text)
    end tell
    '''
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=10,
        )
        parts = [v.strip() for v in result.stdout.strip().split(",") if v.strip()]
        if len(parts) == 4:
            x, y, w, h = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
            return (x, y, w, h)
    except Exception as e:
        logger.warning(f"获取窗口边界失败: {e}")
    return None


def get_window_pid(title_contains: str) -> Optional[int]:
    """获取进程 PID（用于后台点击）。"""
    script = f'''
    tell application "System Events"
        set targetProcess to first process whose name contains "{title_contains}"
        return unix id of targetProcess
    end tell
    '''
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=5,
        )
        pid_str = result.stdout.strip()
        if pid_str.isdigit():
            return int(pid_str)
    except Exception as e:
        logger.warning(f"获取 PID 失败: {e}")
    return None


def get_window_info(title_contains: str) -> Optional[dict]:
    """获取窗口的完整信息：bounds + PID（一次调用）。

    Returns:
        {"bounds": (x,y,w,h), "pid": int, "name": str}
    """
    script = f'''
    tell application "System Events"
        set targetProcess to first process whose name contains "{title_contains}"
        set frontWindow to window 1 of targetProcess
        set winPos to position of frontWindow
        set winSize to size of frontWindow
        set x to item 1 of winPos
        set y to item 2 of winPos
        set w to item 1 of winSize
        set h to item 2 of winSize
        set winName to name of frontWindow
        set pid to unix id of targetProcess
        return (x as text) & "|" & (y as text) & "|" & (w as text) & "|" & (h as text) & "|" & winName & "|" & pid
    end tell
    '''
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=10,
        )
        parts = result.stdout.strip().split("|")
        if len(parts) >= 6:
            return {
                "bounds": (int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])),
                "name": parts[4],
                "pid": int(parts[5]) if parts[5].strip().isdigit() else None,
            }
    except Exception as e:
        logger.warning(f"获取窗口信息失败: {e}")
    return None


# ── 全屏截图 + PIL crop（备选方案）──


def capture_window_via_crop(title_contains: str) -> bytes:
    """全屏截图后用 PIL 裁切目标窗口区域。

    比 screencapture -R 稍慢，但更可靠（不依赖坐标转换）。
    需要 Pillow。
    """
    try:
        from io import BytesIO
        from PIL import Image
    except ImportError:
        logger.warning("Pillow 未安装，回退到 screencapture -R")
        return capture_window_by_title(title_contains)

    bounds = get_window_bounds(title_contains)
    if not bounds:
        return b""

    full = capture_screen()
    if not full:
        return b""

    x, y, w, h = bounds
    img = Image.open(BytesIO(full))
    crop = img.crop((x, y, x + w, y + h))

    buf = BytesIO()
    crop.save(buf, format="PNG")
    return buf.getvalue()


# ── 窗口激活（仅必要时使用）──


def activate_window(title_contains: str):
    """激活窗口（仅在必须时使用，比如需要用户手动确认的操作）。"""
    script = f'''
    tell application "System Events"
        set targetProcess to first process whose name contains "{title_contains}"
        set frontmost of targetProcess to true
    end tell
    '''
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5)
        logger.info(f"窗口已激活: {title_contains}")
    except Exception as e:
        logger.warning(f"激活窗口失败: {e}")


def get_frontmost_app() -> str:
    """获取当前最前台 App 名称。"""
    try:
        result = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to return name of first process whose frontmost is true'],
            capture_output=True, text=True, timeout=3,
        )
        return result.stdout.strip()
    except Exception:
        return ""


def list_window_titles() -> list[str]:
    """列出所有可见窗口标题。"""
    script = '''
    tell application "System Events"
        set windowList to {}
        repeat with proc in (every process whose visible is true)
            try
                repeat with w in (every window of proc)
                    set end of windowList to name of w
                end repeat
            end try
        end repeat
        return windowList
    end tell
    '''
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=10,
        )
        titles = [t.strip() for t in result.stdout.split(",") if t.strip()]
        return titles
    except Exception as e:
        logger.warning(f"获取窗口列表失败: {e}")
        return []
