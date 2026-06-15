"""Visual — 视觉自动化引擎。

基于截图的 GUI 自动化，不使用 Playwright/Selenium。
通过 macOS 原生 API 发送真实的鼠标键盘事件。

核心理念：
  截图 → 本地视觉模型 (Gemma 4) 理解界面 → OS 级操作 → 截图验证
  平台看到的是一个普通人在操作电脑，无法检测为机器人。

模块：
  screen  — 截图 + 窗口管理（macOS CoreGraphics + AppleScript）
  vision  — 视觉理解（Gemma 4 / 降级 DeepSeek）
  action  — 拟人化鼠标键盘（CGEvent + PyAutoGUI + AppleScript）
  agent   — 编排调度器（截图→理解→动作→验证循环）
"""

from .screen import capture_screen, capture_window_by_title, activate_window
from .vision import analyze_screenshot, build_scrape_prompt, build_click_prompt, build_som_click_prompt
from .action import human_click, human_type, human_paste, human_scroll, set_clipboard
from .som import scan_elements, overlay_markers, build_element_map, detect_elements_vlm, UIElement
from .agent import VisualAgent

__all__ = [
    "capture_screen",
    "capture_window_by_title",
    "activate_window",
    "analyze_screenshot",
    "build_scrape_prompt",
    "build_click_prompt",
    "build_som_click_prompt",
    "human_click",
    "human_type",
    "human_paste",
    "human_scroll",
    "set_clipboard",
    "scan_elements",
    "overlay_markers",
    "build_element_map",
    "UIElement",
    "VisualAgent",
]
