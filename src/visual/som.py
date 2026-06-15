"""Set-of-Mark (SoM) — UI 元素检测 + 编号标注。

基于 UI-TARS 论文思路：不给模型裸截图让它猜坐标，
而是先用 macOS Accessibility API 扫描所有可交互元素，
叠加编号标记，让模型说「点 3 号」而不是「点(847, 392)」。

准确率提升原理：
  - AX API 给出精确的元素位置（操作系统级别的信息）
  - VLM 只需要做"描述匹配到编号"（简单的语义任务）
  - 不需要做坐标回归（VLM 不擅长的事）

用法：
  elements = scan_elements("Chrome")        # 扫描窗口内所有 UI 元素
  marked_img = overlay_markers(screenshot, elements)  # 叠编号
  → 发给 Gemma 4: "点几号？"
  → 查表得到精确坐标，执行点击
"""

import logging
import math
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class UIElement:
    """一个可交互的 UI 元素。"""
    index: int                      # SoM 编号
    role: str = ""                  # AXRole: AXButton, AXTextField, AXStaticText...
    label: str = ""                 # 可见文本/标签
    value: str = ""                 # 当前值（如输入框内容）
    description: str = ""           # AX 描述
    x: int = 0                      # 屏幕坐标 X
    y: int = 0                      # 屏幕坐标 Y
    width: int = 0                  # 宽度
    height: int = 0                 # 高度
    enabled: bool = True            # 是否可用

    @property
    def center(self) -> Tuple[int, int]:
        return (self.x + self.width // 2, self.y + self.height // 2)

    @property
    def short_desc(self) -> str:
        """给模型看的简短描述。"""
        parts = [f"[{self.index}]"]
        if self.role:
            role_cn = {
                "AXButton": "按钮", "AXTextField": "输入框", "AXTextArea": "文本区",
                "AXStaticText": "文本", "AXCheckBox": "复选框", "AXPopUpButton": "下拉",
                "AXMenuItem": "菜单项", "AXLink": "链接", "AXImage": "图片",
                "AXGroup": "分组", "AXScrollArea": "滚动区", "AXTabGroup": "标签页",
                "AXSlider": "滑块", "AXComboBox": "组合框", "AXRadioButton": "单选",
            }.get(self.role, self.role)
            parts.append(role_cn)
        if self.label:
            parts.append(f'"{self.label[:30]}"')
        if not self.enabled:
            parts.append("[禁用]")
        return " ".join(parts)


def scan_elements(
    app_name: str = "",
    window_index: int = 0,
    max_elements: int = 50,
    min_size: int = 20,
) -> list[UIElement]:
    """使用 macOS Accessibility API 扫描窗口中的可交互元素。

    Args:
        app_name: App 名称（如 "Chrome"、"Safari"），空则用最前台 App
        window_index: 窗口序号（0=最前面）
        max_elements: 最多返回多少元素
        min_size: 最小元素尺寸（过滤掉太小的装饰性元素）

    Returns:
        带编号的 UI 元素列表
    """
    script = f'''
    use framework "AppKit"
    use framework "Foundation"
    use scripting additions

    -- 获取目标 App
    if "{app_name}" is "" then
        set targetApp to current application's NSWorkspace's sharedWorkspace()'s frontmostApplication()
    else
        set targetApp to missing value
        tell application "System Events"
            repeat with proc in (every process whose visible is true)
                if name of proc contains "{app_name}" then
                    set targetApp to proc
                    exit repeat
                end if
            end repeat
        end tell
    end if

    -- 这里我们换用 Python 的 Quartz/AX API（更强大）
    -- AppleScript 返回窗口位置信息，Python 做 AX 遍历
    tell application "System Events"
        try
            set targetProc to first process whose name contains "{app_name}"
            set frontWindow to window {window_index + 1} of targetProc
            set winPos to position of frontWindow
            set winSize to size of frontWindow
            return {{item 1 of winPos, item 2 of winPos, item 1 of winSize, item 2 of winSize}}
        on error
            return {{0, 0, 0, 0}}
        end try
    end tell
    '''

    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=10,
        )
        win_bounds = _parse_window_bounds(result.stdout)
    except Exception as e:
        logger.warning(f"获取窗口边界失败: {e}")
        win_bounds = (0, 0, 0, 0)

    # 用 Python Quartz API 遍历 UI 元素树
    elements = _scan_with_quartz(app_name, max_elements, min_size)

    # 调整坐标（将窗口内坐标转为屏幕坐标）
    win_x, win_y, _, _ = win_bounds
    if win_x or win_y:
        for el in elements:
            el.x += win_x
            el.y += win_y

    return elements


def _scan_with_quartz(
    app_name: str,
    max_elements: int = 50,
    min_size: int = 20,
) -> list[UIElement]:
    """使用 macOS Quartz Accessibility API 遍历 UI 元素树。"""
    try:
        import Quartz
    except ImportError:
        logger.warning("Quartz (pyobjc-framework-Quartz) 未安装，回退到 AppleScript")
        return _scan_with_applescript(app_name, max_elements, min_size)

    elements = []

    try:
        # 获取目标 App 的 PID
        target_pid = None
        if app_name:
            apps = Quartz.NSWorkspace.sharedWorkspace().runningApplications()
            for app in apps:
                if app_name.lower() in (app.localizedName() or "").lower():
                    target_pid = app.processIdentifier()
                    break

        if target_pid is None:
            # 用最前台的 App
            target_app = Quartz.NSWorkspace.sharedWorkspace().frontmostApplication()
            target_pid = target_app.processIdentifier()

        # 创建 AX 应用元素
        app_element = Quartz.AXUIElementCreateApplication(int(target_pid))

        # 获取焦点窗口
        win_value = None
        Quartz.AXUIElementCopyAttributeValue(
            app_element, Quartz.kAXFocusedWindowAttribute, win_value
        )

        # 遍历
        _traverse_ax_tree(
            app_element if win_value is None else win_value,
            elements, max_elements, min_size, index_counter=[0],
        )

    except Exception as e:
        logger.warning(f"Quartz AX 扫描失败: {e}")
        elements = _scan_with_applescript(app_name, max_elements, min_size)

    return elements


def _traverse_ax_tree(
    element,
    results: list,
    max_elements: int,
    min_size: int,
    index_counter: list,
    depth: int = 0,
):
    """递归遍历 AX 元素树，收集可交互元素。"""
    if len(results) >= max_elements or depth > 15:
        return

    try:
        import Quartz

        # 获取角色
        role = _ax_get_string(element, Quartz.kAXRoleAttribute) or ""

        # 获取位置和大小
        pos = _ax_get_value(element, Quartz.kAXPositionAttribute)
        size = _ax_get_value(element, Quartz.kAXSizeAttribute)

        x, y, w, h = 0, 0, 0, 0
        if pos:
            Quartz.AXValueGetValue(pos, Quartz.kAXValueCGPointType, pos_ref := (0.0, 0.0))
            x, y = int(pos_ref[0]), int(pos_ref[1])
        if size:
            Quartz.AXValueGetValue(size, Quartz.kAXValueCGSizeType, size_ref := (0.0, 0.0))
            w, h = int(size_ref[0]), int(size_ref[1])

        # 只收集足够大、可交互的元素
        interactive_roles = {
            "AXButton", "AXTextField", "AXTextArea", "AXCheckBox",
            "AXPopUpButton", "AXMenuItem", "AXLink", "AXComboBox",
            "AXRadioButton", "AXSlider", "AXTabGroup",
        }

        if role in interactive_roles and w >= min_size and h >= min_size:
            index_counter[0] += 1
            label = _ax_get_string(element, Quartz.kAXDescriptionAttribute) or ""
            if not label:
                label = _ax_get_string(element, Quartz.kAXTitleAttribute) or ""
            value = _ax_get_string(element, Quartz.kAXValueAttribute) or ""
            enabled_val = _ax_get_value(element, Quartz.kAXEnabledAttribute)
            enabled = enabled_val is not False and enabled_val != 0

            results.append(UIElement(
                index=index_counter[0],
                role=role,
                label=label,
                value=value,
                description="",
                x=x, y=y, width=w, height=h,
                enabled=enabled,
            ))

        # 遍历子元素
        children = _ax_get_value(element, Quartz.kAXChildrenAttribute)
        if children:
            count = Quartz.CFArrayGetCount(children)
            for i in range(min(count, 30)):
                child = Quartz.CFArrayGetValueAtIndex(children, i)
                _traverse_ax_tree(
                    child, results, max_elements, min_size,
                    index_counter, depth + 1,
                )

    except Exception:
        pass


def _ax_get_string(element, attr) -> str:
    """从 AX 元素获取字符串属性。"""
    try:
        import Quartz
        val = None
        err = Quartz.AXUIElementCopyAttributeValue(element, attr, val)
        if err == 0 and val:
            if isinstance(val, str):
                return val
            if hasattr(val, '__str__'):
                return str(val)
    except Exception:
        pass
    return ""


def _ax_get_value(element, attr):
    """从 AX 元素获取任意类型属性。"""
    try:
        import Quartz
        val = None
        err = Quartz.AXUIElementCopyAttributeValue(element, attr, val)
        return val if err == 0 else None
    except Exception:
        return None


def _scan_with_applescript(
    app_name: str, max_elements: int, min_size: int,
) -> list[UIElement]:
    """用 AppleScript 降级方案扫描 UI 元素。"""
    script = f'''
    tell application "System Events"
        set results to {{}}
        set idx to 0
        try
            set targetProc to first process whose name contains "{app_name}"
            set frontWindow to window 1 of targetProc
            repeat with elem in (every UI element of frontWindow)
                try
                    set elemRole to role of elem
                    if elemRole is in {{"AXButton", "AXTextField", "AXTextArea", "AXCheckBox", "AXPopUpButton", "AXLink", "AXComboBox", "AXRadioButton"}} then
                        set idx to idx + 1
                        if idx > {max_elements} then exit repeat
                        set elemPos to position of elem
                        set elemSize to size of elem
                        set elemLabel to ""
                        try
                            set elemLabel to description of elem
                        end try
                        if elemLabel is "" then
                            try
                                set elemLabel to title of elem
                            end try
                        end if
                        set end of results to {{idx, elemRole, elemLabel, item 1 of elemPos, item 2 of elemPos, item 1 of elemSize, item 2 of elemSize}}
                    end if
                end try
            end repeat
        end try
        return results
    end tell
    '''

    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=10,
        )
        return _parse_applescript_elements(result.stdout, max_elements)
    except Exception as e:
        logger.warning(f"AppleScript UI 扫描失败: {e}")
        return []


def _parse_applescript_elements(output: str, max_count: int) -> list[UIElement]:
    """解析 AppleScript 返回的元素列表。"""
    elements = []
    # AppleScript 返回格式: {{1, "AXButton", "发布", 100, 200, 80, 32}, ...}
    # 简化解析——按逗号分割后用正则提取
    import re
    # 匹配每个子列表
    items = re.findall(r'\{(\d+),\s*"([^"]*)",\s*"([^"]*)",\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+)\}', output)
    for item in items:
        idx, role, label, x, y, w, h = item
        elements.append(UIElement(
            index=int(idx),
            role=role,
            label=label,
            x=int(x), y=int(y),
            width=int(w), height=int(h),
        ))
        if len(elements) >= max_count:
            break
    return elements


def _parse_window_bounds(output: str) -> Tuple[int, int, int, int]:
    """解析 AppleScript 窗口边界。"""
    import re
    nums = re.findall(r'\d+', output)
    if len(nums) >= 4:
        return int(nums[0]), int(nums[1]), int(nums[2]), int(nums[3])
    return (0, 0, 0, 0)


# ── 标记叠加 ──


def overlay_markers(screenshot_bytes: bytes, elements: list[UIElement]) -> bytes:
    """在截图上叠加 SoM 编号标记，返回新 PNG。

    用 Pillow 绘制半透明背景 + 数字，确保编号在截图上可见。
    """
    try:
        from io import BytesIO
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        logger.warning("Pillow 未安装，返回原始截图")
        return screenshot_bytes

    img = Image.open(BytesIO(screenshot_bytes))
    draw = ImageDraw.Draw(img)

    # 尝试加载字体（macOS 系统字体）
    font = None
    for font_path in [
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/SFNSDisplay.ttf",
    ]:
        try:
            font = ImageFont.truetype(font_path, 16)
            break
        except Exception:
            pass
    if font is None:
        font = ImageFont.load_default()

    for el in elements:
        if not el.enabled:
            continue

        cx, cy = el.center
        radius = max(12, min(el.width, el.height) // 2)

        # 画半透明圆形背景
        draw.ellipse(
            [cx - radius, cy - radius, cx + radius, cy + radius],
            fill=(255, 50, 50, 180),  # 红色半透明
            outline=(255, 255, 255),
            width=2,
        )

        # 画编号
        text = str(el.index)
        try:
            bbox = draw.textbbox((0, 0), text, font=font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
        except Exception:
            tw, th = 10, 14

        draw.text(
            (cx - tw // 2, cy - th // 2),
            text,
            fill=(255, 255, 255),
            font=font,
        )

        # 画极小的标签提示（元素类型 + 标签前8字）
        hint = el.short_desc.replace(f"[{el.index}] ", "")[:14]
        if hint:
            try:
                hint_font = ImageFont.truetype(font_path, 9) if font_path else font
            except Exception:
                hint_font = font
            draw.text(
                (cx + radius + 4, cy - 6),
                hint,
                fill=(255, 255, 255),
                font=hint_font,
            )

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ═══════════════════════════════════════════
#  VLM 纯视觉元素检测（Web 降级方案）
# ═══════════════════════════════════════════

DETECT_ELEMENTS_PROMPT = """你是一个 UI 元素检测器。请仔细分析这张截图，找出所有可交互的 UI 元素。

对于每个元素，提供：
- 元素类型（button/input/textarea/link/checkbox/dropdown/tab/icon）
- 可见文本/标签
- 中心坐标 (x, y)（相对于截图左上角，单位为像素）
- 大致尺寸 (width, height)
- 你的置信度 (0.0-1.0)

返回 JSON 数组：
```json
{
  "elements": [
    {
      "index": 1,
      "type": "button",
      "label": "发布",
      "x": 850,
      "y": 720,
      "width": 80,
      "height": 36,
      "confidence": 0.95
    }
  ]
}
```

注意：
- 坐标是相对于截图左上角的像素值
- 坐标取元素的中心点
- 如果截图中有搜索框、输入区、按钮栏等，全部识别出来
- 按从上到下、从左到右排序
- 只返回 JSON，不要其他文字"""


async def detect_elements_vlm(screenshot_bytes: bytes) -> list[UIElement]:
    """纯 VLM 视觉元素检测 — AX API 不可用时的降级方案。

    直接把截图发给视觉模型，让它找出所有可交互元素并返回坐标。
    这是 UI-TARS 和 Claude Computer Use 的核心方法。
    """
    from . import vision

    result = await vision.analyze_screenshot(
        screenshot_bytes,
        DETECT_ELEMENTS_PROMPT,
        format_json=True,
    )

    if not result or "elements" not in result:
        return []

    elements = []
    for el_data in result.get("elements", [])[:40]:
        try:
            elements.append(UIElement(
                index=el_data.get("index", len(elements) + 1),
                role=f"AX{el_data.get('type', 'Button').title()}",
                label=el_data.get("label", ""),
                x=int(el_data.get("x", 0)),
                y=int(el_data.get("y", 0)),
                width=max(int(el_data.get("width", 40)), 20),
                height=max(int(el_data.get("height", 20)), 15),
                enabled=el_data.get("confidence", 0.5) > 0.3,
            ))
        except Exception:
            continue

    logger.info(f"VLM 视觉检测到 {len(elements)} 个 UI 元素")
    return elements


def build_element_map(elements: list[UIElement]) -> dict[int, UIElement]:
    """构建编号 → 元素的查找表。"""
    return {el.index: el for el in elements}


def build_som_prompt(action_description: str, elements: list[UIElement]) -> str:
    """构建 SoM 风格的点击定位 prompt。

    和旧的 build_click_prompt 的区别：
    - 旧：让模型预测像素坐标 → 不准
    - 新：让模型选择编号 → 精准查表
    """
    # 列出所有可交互元素
    element_list = "\n".join(f"  {el.short_desc}" for el in elements if el.enabled)

    # 禁用元素区块
    disabled_block = ""
    disabled_items = [el for el in elements if not el.enabled]
    if disabled_items:
        disabled_list = "\n".join(f"  {el.short_desc}" for el in disabled_items)
        disabled_block = "## 禁用/不可点元素\n" + disabled_list

    prompt = f"""你是一个 GUI 操作选择器。截图已被标注了编号标记——每个红色圆圈里的数字对应一个 UI 元素。

任务：{action_description}

## 可交互元素
{element_list or '（未检测到可交互元素）'}

{disabled_block}

## 指令
返回 JSON：
{{
  "thinking": "分析任务，选择最相关的元素编号",
  "action": "click|type|scroll|skip",
  "target_index": 数字,  // 要点击的元素编号
  "text_to_type": "",    // 如果 action=type，需要输入什么
  "confidence": "high|medium|low"
}}

**重要**：target_index 必须是上面列出的编号之一。如果找不到合适的元素，action 设为 "skip" 并说明原因。"""

    return prompt
