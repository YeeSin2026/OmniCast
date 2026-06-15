"""拟人化鼠标/键盘动作。

双模式：
  1. 后台模式（优先）— bin/bgclick + bin/bgtype → CGEventPostToPid
     不抢光标、不激活窗口、不切换 Space
  2. 前台模式（降级）— PyAutoGUI + AppleScript → 传统方法
     会抢占光标，作为 bgclick 不可用时的兜底

mac-cua 方案核心：
  CGEventPostToPid 把鼠标/键盘事件直接投递到目标进程，
  绕过全局 HID 流，所以光标不动、焦点不抢。
"""

import logging
import os
import random
import subprocess
import time
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# ── 二进制路径 ──

_BIN_DIR = Path(__file__).resolve().parent / "bin"
_BGCLICK = str(_BIN_DIR / "bgclick")
_BGTYPE = str(_BIN_DIR / "bgtype")

_HAS_BGCLICK = os.path.exists(_BGCLICK)
_HAS_BGTYPE = os.path.exists(_BGTYPE)

# ── 拟人化参数 ──

TYPING_SPEED_MIN = 60
TYPING_SPEED_MAX = 200
PAUSE_MIN = 500
PAUSE_MAX = 2500
THINK_PAUSE_PROB = 0.15
THINK_PAUSE_MIN = 2000
THINK_PAUSE_MAX = 6000


def human_delay(min_ms: int = 500, max_ms: int = 2000):
    ms = random.randint(min_ms, max_ms) / 1000.0
    time.sleep(ms)


def human_pause(thinking: bool = False):
    if thinking and random.random() < THINK_PAUSE_PROB:
        ms = random.randint(THINK_PAUSE_MIN, THINK_PAUSE_MAX) / 1000.0
    else:
        ms = random.randint(PAUSE_MIN, PAUSE_MAX) / 1000.0
    time.sleep(ms)


# ═══════════════════════════════════════════
#  后台模式（优先）— CGEventPostToPid
# ═══════════════════════════════════════════


def background_click(x: int, y: int, pid: int, button: str = "left") -> bool:
    """后台点击 — 不抢光标、不激活窗口。

    Args:
        x, y: 屏幕坐标（相对于主屏幕左上角）
        pid: 目标进程 PID
        button: 'left' | 'right'

    Returns:
        是否成功
    """
    if not _HAS_BGCLICK:
        logger.debug("bgclick 不可用，降级到前台点击")
        return False

    args = [_BGCLICK, str(pid), str(x), str(y)]
    if button == "right":
        args.append("right")

    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            logger.debug(f"后台点击: ({x},{y}) → PID {pid}")
            return True
        else:
            logger.warning(f"bgclick 失败: {result.stderr.strip()}")
            return False
    except Exception as e:
        logger.warning(f"bgclick 异常: {e}")
        return False


def background_type(text: str, pid: int) -> bool:
    """后台打字 — 字符逐个通过 CGEventPostToPid 发送。

    自动判断：短文本逐字打，长文本粘贴。
    """
    if len(text) > 200:
        return background_paste(pid)

    if not _HAS_BGTYPE:
        return False

    try:
        result = subprocess.run(
            [_BGTYPE, str(pid), text],
            capture_output=True, text=True, timeout=30,
        )
        return result.returncode == 0
    except Exception as e:
        logger.warning(f"bgtype 异常: {e}")
        return False


def background_paste(pid: int) -> bool:
    """后台粘贴（Cmd+V）— 适合长文本。"""
    if not _HAS_BGTYPE:
        return False

    try:
        result = subprocess.run(
            [_BGTYPE, str(pid), "--paste"],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except Exception as e:
        logger.warning(f"bgpaste 异常: {e}")
        return False


# ═══════════════════════════════════════════
#  前台模式（降级）— PyAutoGUI + AppleScript
# ═══════════════════════════════════════════


def human_click(x: int, y: int, button: str = "left"):
    """前台拟人化点击（pyautogui）。"""
    try:
        import pyautogui
        pyautogui.moveTo(x, y, duration=random.uniform(0.2, 0.6), _pause=False)
        human_pause()
        if button == "right":
            pyautogui.rightClick(x, y, _pause=False)
        else:
            pyautogui.click(x, y, _pause=False)
        return True
    except ImportError:
        pass

    # AppleScript 兜底
    try:
        subprocess.run(
            ["osascript", "-e", f'tell application "System Events" to click at {{{x}, {y}}}'],
            capture_output=True, timeout=5,
        )
        return True
    except Exception:
        return False


def human_type(text: str, wpm: int = 60):
    """前台拟人化打字。"""
    base_delay = 60.0 / (wpm * 5)
    try:
        import pyautogui
        for i, char in enumerate(text):
            pyautogui.typewrite(char, interval=base_delay * random.uniform(0.7, 1.5))
            if char in ".!?。！？\n" and random.random() < 0.3:
                time.sleep(random.uniform(0.5, 2.0))
            if random.random() < 0.005:
                pyautogui.press("backspace")
                time.sleep(base_delay * 2)
                pyautogui.typewrite(char)
        return True
    except ImportError:
        pass

    # AppleScript 兜底
    for char in text:
        escaped = char.replace('"', '\\"').replace("'", "\\'")
        try:
            subprocess.run(
                ["osascript", "-e",
                 f'tell application "System Events" to keystroke "{escaped}"'],
                capture_output=True, timeout=2,
            )
        except Exception:
            pass
        time.sleep(base_delay * random.uniform(0.7, 1.5))
    return True


def human_paste():
    """前台粘贴（Cmd+V）。"""
    try:
        import pyautogui
        pyautogui.hotkey("command", "v", _pause=False)
        return True
    except ImportError:
        try:
            subprocess.run(
                ["osascript", "-e",
                 'tell application "System Events" to keystroke "v" using command down'],
                capture_output=True, timeout=3,
            )
            return True
        except Exception:
            return False


def human_hotkey(*keys: str):
    """前台快捷键。"""
    try:
        import pyautogui
        pyautogui.hotkey(*keys, _pause=False)
    except ImportError:
        for k in keys:
            try:
                subprocess.run(
                    ["osascript", "-e",
                     f'tell application "System Events" to key down "{k}"'],
                    capture_output=True, timeout=2,
                )
            except Exception:
                pass
        for k in reversed(keys):
            try:
                subprocess.run(
                    ["osascript", "-e",
                     f'tell application "System Events" to key up "{k}"'],
                    capture_output=True, timeout=2,
                )
            except Exception:
                pass


def human_scroll(clicks: int):
    """前台滚动。"""
    try:
        import pyautogui
        pyautogui.scroll(clicks, _pause=False)
        return True
    except ImportError:
        return False


# ═══════════════════════════════════════════
#  统一的"智能点击"：自动选后台/前台
# ═══════════════════════════════════════════


def smart_click(x: int, y: int, pid: Optional[int] = None) -> bool:
    """智能点击 — 有 PID 就用后台，没有就降级前台。"""
    if pid and _HAS_BGCLICK:
        return background_click(x, y, pid)
    else:
        return human_click(x, y)


def smart_type(text: str, pid: Optional[int] = None) -> bool:
    """智能输入 — 有 PID 就用后台，没有就降级前台。"""
    if pid and _HAS_BGTYPE:
        if len(text) > 200:
            return background_paste(pid)
        else:
            return background_type(text, pid)
    else:
        return human_type(text)


# ═══════════════════════════════════════════
#  剪贴板
# ═══════════════════════════════════════════


def set_clipboard(text: str):
    """设置系统剪贴板。"""
    try:
        subprocess.run(["pbcopy"], input=text.encode("utf-8"), timeout=5)
    except Exception:
        try:
            import pyperclip
            pyperclip.copy(text)
        except ImportError:
            pass


def get_clipboard() -> str:
    """读取系统剪贴板。"""
    try:
        result = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=5)
        return result.stdout
    except Exception:
        return ""
