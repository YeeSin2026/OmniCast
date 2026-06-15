"""视觉自动化 Agent — 编排截图→理解→动作→验证的主循环。

这是整个视觉方案的核心调度器。
不做 DOM 解析，不注入 JS，不操控浏览器内部——
只是看截图，然后像人一样操作电脑。

循环：
  截图 → Gemma 4 分析 → 生成动作列表 → 逐个执行
  → 截图验证 → 成功/失败/重试
"""

import asyncio
import json
import logging
from typing import Optional

from . import screen, action, vision
from . import som as som_module

logger = logging.getLogger(__name__)

# ── 主 Agent ──


class VisualAgent:
    """视觉自动化 Agent — 截图→理解→动作。"""

    def __init__(self, max_retries: int = 3):
        self.max_retries = max_retries

    # ═══════════════════════════════════════════
    #  内容抓取（替代 Playwright 爬虫）
    # ═══════════════════════════════════════════

    async def scrape_page(
        self,
        browser_window_title: str,
        url: str,
        platform_name: str,
    ) -> dict:
        """从浏览器窗口中抓取页面内容。

        替代 Playwright 的 page.goto + page.query_selector。

        Args:
            browser_window_title: 浏览器窗口标题包含的字符串（如 "Chrome"）
            url: 页面 URL（用于 prompt 上下文）
            platform_name: 平台名（如 "小红书"）

        Returns:
            {title, author, text_content, images, comments}
        """
        logger.info(f"视觉抓取: {platform_name} ({url[:60]})")

        # 1. 激活浏览器窗口
        screen.activate_window(browser_window_title)
        await asyncio.sleep(1.5)  # 等窗口完全渲染

        # 2. 截图
        img = screen.capture_window_by_title(browser_window_title)
        if not img:
            logger.warning("截图失败，尝试全屏截图")
            img = screen.capture_screen()

        if not img:
            return self._empty_result(f"无法截图: {platform_name}")

        # 3. 视觉分析
        prompt = vision.build_scrape_prompt(url, platform_name)
        result = await vision.analyze_screenshot(img, prompt, format_json=True)

        if not result:
            return self._empty_result(f"视觉分析失败: {platform_name}")

        logger.info(
            f"视觉抓取完成: title={result.get('title', '')[:40]}, "
            f"images={len(result.get('images', []))}, "
            f"comments={len(result.get('comments', []))}"
        )
        return result

    # ═══════════════════════════════════════════
    #  内容发布（替代 Playwright 表单填充）
    # ═══════════════════════════════════════════

    async def publish_content(
        self,
        browser_window_title: str,
        content: dict,
        platform_config: dict,
    ) -> dict:
        """通过视觉方案发布内容到平台。

        Args:
            browser_window_title: 浏览器窗口标题
            content: 要发布的内容 {title, body, tags, images}
            platform_config: 平台配置 {name, steps: [{action, description, after_wait}]}

        Returns:
            {success: bool, url: str, evidence: str}
        """
        logger.info(f"视觉发布: {platform_config.get('name', '?')}")

        # 1. 获取窗口信息（后台，不激活）
        win_info = screen.get_window_info(browser_window_title)
        if win_info:
            self._target_pid = win_info.get("pid")
            logger.info(f"目标窗口: {win_info['name'][:40]} (PID={self._target_pid}), 后台操作模式")
        else:
            self._target_pid = None
            screen.activate_window(browser_window_title)
            await asyncio.sleep(2)
            logger.info(f"未获取到 PID，降级前台模式")

        # 2. 逐步骤执行
        steps = platform_config.get("steps", [])
        for i, step in enumerate(steps):
            logger.info(f"  步骤 {i+1}/{len(steps)}: {step.get('description', '?')}")

            success = await self._execute_step(step, content, browser_window_title)
            if not success:
                retry_success = await self._retry_step(step, content, browser_window_title)
                if not retry_success:
                    return {
                        "success": False,
                        "url": "",
                        "evidence": f"步骤 {i+1} 失败: {step.get('description')}",
                    }

            # 步骤后等待
            after_wait = step.get("after_wait", 1.5)
            await asyncio.sleep(after_wait)

        # 3. 截图验证
        img = screen.capture_window_by_title(browser_window_title)
        if img:
            verify_prompt = vision.build_verify_prompt(
                f"内容已成功发布到{platform_config.get('name', '平台')}。"
                f"检查截图中是否有「发布成功」、「已发布」或类似的确认信息。"
            )
            verify_result = await vision.analyze_screenshot(img, verify_prompt)

            if verify_result and verify_result.get("success"):
                return {
                    "success": True,
                    "url": f"已发布到 {platform_config.get('name', '?')}",
                    "evidence": verify_result.get("evidence", ""),
                }

        return {
            "success": True,
            "url": f"已提交到 {platform_config.get('name', '?')}",
            "evidence": "操作步骤已完成",
        }

    async def _execute_step(self, step: dict, content: dict, window_title: str) -> bool:
        """执行单个动作步骤。优先后台模式（bgclick），失败降级前台。"""
        step_type = step.get("action", "click")
        pid = getattr(self, "_target_pid", None)

        try:
            if step_type == "click":
                # 优先 SoM（AX 扫描+编号标注），失败降级纯视觉
                use_som = step.get("use_som", True)
                if use_som:
                    result = await self._click_target_som(step["description"], window_title)
                    if not result:
                        logger.info("SoM 定位失败，降级纯视觉定位")
                        result = await self._click_target(step["description"], window_title)
                    return result
                else:
                    return await self._click_target(step["description"], window_title)

            elif step_type == "type":
                text_key = step.get("content_key", "body")
                text = content.get(text_key, "")
                if text:
                    self._type_text(text, step.get("paste", True), pid=pid)
                return True

            elif step_type == "click_then_type":
                clicked = await self._click_target(step["description"], window_title)
                if not clicked:
                    return False
                action.human_pause()
                text_key = step.get("content_key", "body")
                text = content.get(text_key, "")
                if text:
                    self._type_text(text, step.get("paste", True), pid=pid)
                return True

            elif step_type == "scroll":
                amount = step.get("amount", -300)
                action.human_scroll(amount)
                return True

            elif step_type == "wait":
                await asyncio.sleep(step.get("seconds", 2))
                return True

            elif step_type == "hotkey":
                keys = step.get("keys", [])
                if keys:
                    action.human_hotkey(*keys)
                return True

            else:
                logger.warning(f"未知步骤类型: {step_type}")
                return True  # 不阻塞流程

        except Exception as e:
            logger.warning(f"步骤执行异常: {e}")
            return False

    async def _click_target(self, description: str, window_title: str) -> bool:
        """截图 → 视觉定位 → 点击目标。

        Args:
            description: 目标描述（如"发布按钮"）
            window_title: 窗口标题

        Returns:
            是否成功点击
        """
        # 截图
        img = screen.capture_window_by_title(window_title)
        if not img:
            img = screen.capture_screen()
        if not img:
            return False

        # 视觉定位
        prompt = vision.build_click_prompt(description)
        result = await vision.analyze_screenshot(img, prompt)

        if not result or not result.get("found"):
            fallback = result.get("fallback", "") if result else ""
            logger.warning(f"未找到目标: {description}. Fallback: {fallback}")
            return False

        x, y = result.get("x", 0), result.get("y", 0)
        confidence = result.get("confidence", 0)

        if confidence < 0.5:
            logger.warning(f"定位置信度低 ({confidence}): {description}, 坐标 ({x},{y})")
            # 低置信度但仍尝试点击（可能是对的）
        else:
            logger.info(f"定位成功 ({confidence}): {description} → ({x}, {y})")

        pid = getattr(self, "_target_pid", None)
        action.smart_click(x, y, pid)
        action.human_pause(thinking=True)
        return True

    async def _click_target_som(self, description: str, window_title: str) -> bool:
        """SoM 增强版点击定位 — 用 AX API 扫描元素 + 编号标注。

        流程：
        1. AX API 扫描窗口内所有 UI 元素
        2. 在截图上叠加红色编号标记
        3. Gemma 4 看图选编号（不做坐标回归）
        4. 查表拿到精确坐标 → 点击

        比 _click_target 准确率高得多——模型只需做语义匹配，不需要猜坐标。
        """
        # 1. 截图
        img = screen.capture_window_by_title(window_title)
        if not img:
            img = screen.capture_screen()
        if not img:
            return False

        # 2. AX API 扫描元素（优先，因为是精确的 OS 级坐标）
        elements = som_module.scan_elements(
            app_name=window_title,
            max_elements=40,
            min_size=15,
        )

        if len(elements) < 2:
            # AX 不可用（Chrome 不暴露网页内容）→ VLM 纯视觉检测
            logger.info(f"AX 仅发现 {len(elements)} 个元素，启用 VLM 视觉检测")
            elements = await som_module.detect_elements_vlm(img)

        if len(elements) < 2:
            # VLM 也不行 → 降级到纯视觉坐标预测
            logger.info(f"VLM 检测也不足 ({len(elements)} 个)，降级到纯视觉坐标")
            return await self._click_target(description, window_title)

        # 3. 叠加编号标记
        marked_img = som_module.overlay_markers(img, elements)
        element_map = som_module.build_element_map(elements)

        # 4. 构建 SoM prompt，发给视觉模型
        prompt = vision.build_som_click_prompt(description, elements)
        result = await vision.analyze_screenshot(marked_img, prompt, format_json=True)

        if not result:
            logger.warning("SoM 视觉分析失败，降级到纯视觉定位")
            return await self._click_target(description, window_title)

        target_index = result.get("target_index", 0)
        action_type = result.get("action", "skip")
        confidence = result.get("confidence", "low")

        if action_type == "skip" or not target_index:
            logger.info(f"SoM 判断无需操作: {result.get('thinking', '')[:80]}")
            return False

        # 5. 查表 → 拿到精确坐标
        target_el = element_map.get(target_index)
        if not target_el:
            logger.warning(f"SoM 选中的编号 {target_index} 不在元素表中")
            return await self._click_target(description, window_title)

        cx, cy = target_el.center
        logger.info(
            f"SoM 定位: [{target_index}] {target_el.short_desc} "
            f"→ ({cx}, {cy}) confidence={confidence}"
        )

        # 6. 后台执行（有 PID 优先）
        pid = getattr(self, "_target_pid", None)
        if action_type == "click":
            action.smart_click(cx, cy, pid)
        elif action_type == "type":
            action.smart_click(cx, cy, pid)  # 先点输入框
            action.human_pause()
            text = result.get("text_to_type", "")
            if text:
                self._type_text(text, pid=pid)
        else:
            action.smart_click(cx, cy, pid)

        action.human_pause(thinking=True)
        return True

    async def _retry_step(self, step: dict, content: dict, window_title: str) -> bool:
        """重试失败的步骤——强制使用 SoM（UI 可能已变化，需要重新扫描）。"""
        logger.info(f"  重试步骤（SoM 强制）: {step.get('description', '?')}")
        await asyncio.sleep(1)

        if step.get("action") == "click":
            return await self._click_target_som(step["description"], window_title)

        return await self._execute_step(step, content, window_title)

    def _type_text(self, text: str, paste: bool = True, pid: int = None):
        """智能输入——有 PID 后台，无 PID 前台。"""
        if pid and action.smart_type(text, pid):
            action.human_pause()
            return
        # 降级前台
        if paste and len(text) > 200:
            action.set_clipboard(text)
            action.human_paste()
        else:
            action.human_type(text, wpm=65)
        action.human_pause()

    # ═══════════════════════════════════════════
    #  页面状态检测（动态适配）
    # ═══════════════════════════════════════════

    PAGE_STATE_PROMPT = """分析这张网页截图。识别当前页面类型和状态，列出所有可执行的操作。

## 页面类型
- login: 登录页（有二维码/手机号输入/密码输入）
- home: 首页/发现页（有内容流/推荐/搜索）
- editor: 内容编辑页（有标题框/正文框/发布按钮）
- publishing: 正在发布中（有进度条/loading/请稍候）
- success: 发布成功（有"发布成功"/"已发布"等确认信息）
- error: 错误页（有错误提示/验证失败）
- captcha: 验证码/滑块验证
- other: 其他页面

## 返回 JSON
{
  "page_type": "login|home|editor|publishing|success|error|captcha|other",
  "page_title": "页面标题/标签页名",
  "description": "对当前页面的简要描述",
  "logged_in": true/false,
  "can_proceed": true/false,
  "user_action_needed": "如果需要用户介入（如扫码登录），说明需要做什么。不需要则为空。",
  "available_actions": [
    {"index": 1, "action": "click", "target": "发布按钮", "x": 800, "y": 600, "priority": "high"},
    {"index": 2, "action": "type", "target": "标题输入框", "x": 400, "y": 300, "priority": "high"}
  ]
}

坐标用独立的 x, y 整数。只返回 JSON。"""

    async def detect_page_state(self, window_title: str = "Chrome") -> dict:
        """截取窗口 → UI-TARS 分析 → 返回页面状态 + 可用操作。

        这是动态适配的核心——不写死坐标，不预判页面状态。
        每次截图都重新分析当前是什么页面、能做什么。

        Returns:
            {
                "page_type": "login|home|editor|...",
                "logged_in": bool,
                "can_proceed": bool,
                "user_action_needed": str,
                "available_actions": [...]
            }
        """
        img = screen.capture_window_by_title(window_title)
        if not img:
            img = screen.capture_screen()
        if not img:
            return {"page_type": "other", "can_proceed": False, "available_actions": []}

        result = await vision.analyze_screenshot(img, self.PAGE_STATE_PROMPT, format_json=True)

        if not result:
            return {
                "page_type": "other",
                "description": "视觉分析失败",
                "can_proceed": False,
                "available_actions": [],
            }

        logger.info(
            f"页面状态: {result.get('page_type', '?')} | "
            f"已登录={result.get('logged_in', False)} | "
            f"可继续={result.get('can_proceed', False)} | "
            f"操作数={len(result.get('available_actions', []))}"
        )

        if result.get("user_action_needed"):
            logger.info(f"  ⚠️ 需要用户: {result['user_action_needed'][:80]}")

        return result

    # ═══════════════════════════════════════════
    #  状态机发布流程
    # ═══════════════════════════════════════════

    async def publish_smart(
        self,
        browser_window_title: str,
        content: dict,
        platform_name: str,
        max_steps: int = 20,
    ) -> dict:
        """状态机驱动的智能发布——自动处理登录、导航、填写、发布。

        与 publish_content() 的区别：
        - publish_content: 预定义步骤序列，遇到意外就失败
        - publish_smart: 每步截图→检测状态→动态决定下一步

        可以处理：
        - 未登录 → 提示用户扫码 → 等待 → 检测登录成功 → 继续
        - 页面跳转 → 识别新状态 → 调整动作
        - 发布失败 → 读取错误信息 → 重试或报告
        """
        logger.info(f"智能发布: {platform_name}")

        # 获取窗口 PID
        win_info = screen.get_window_info(browser_window_title)
        pid = win_info.get("pid") if win_info else None
        self._target_pid = pid

        step_count = 0
        last_state = None
        login_alerted = False

        while step_count < max_steps:
            step_count += 1

            # 1. 检测当前页面状态
            state = await self.detect_page_state(browser_window_title)
            page_type = state.get("page_type", "other")

            # 避免死循环：连续 3 次同一状态退出
            if page_type == last_state:
                self._same_state_count = getattr(self, "_same_state_count", 0) + 1
                if self._same_state_count >= 3:
                    logger.warning(f"连续 {self._same_state_count} 次状态不变 ({page_type})，退出")
                    return {"success": False, "error": f"卡在 {page_type} 页面", "state": state}
            else:
                self._same_state_count = 1
            last_state = page_type

            logger.info(f"  [{step_count}/{max_steps}] 状态={page_type}")

            # 2. 根据状态决定动作
            if page_type == "success":
                logger.info(f"✅ 发布成功!")
                return {"success": True, "state": state, "steps": step_count}

            elif page_type == "error":
                error_desc = state.get("description", "未知错误")
                logger.warning(f"❌ 页面错误: {error_desc}")
                # 尝试找"返回"或"重试"按钮
                retry_action = self._find_priority_action(state, "high")
                if retry_action:
                    await self._execute_state_action(retry_action)
                    await asyncio.sleep(2)
                    continue
                return {"success": False, "error": error_desc, "state": state}

            elif page_type == "login":
                if not login_alerted:
                    user_need = state.get("user_action_needed", "请登录")
                    logger.info(f"🔐 需要登录: {user_need}")
                    login_alerted = True
                    # 通知用户
                    self._notify_user("login_required", {
                        "message": user_need,
                        "platform": platform_name,
                    })

                # 等待用户登录（每 3 秒检测一次，最多等 2 分钟）
                for _ in range(40):
                    await asyncio.sleep(3)
                    state = await self.detect_page_state(browser_window_title)
                    if state.get("page_type") != "login" and state.get("logged_in"):
                        logger.info("✅ 登录成功，继续发布流程")
                        login_alerted = False
                        break
                else:
                    return {"success": False, "error": "登录超时（2 分钟）", "state": state}

                # 登录后可能需要导航到发布页
                continue

            elif page_type == "captcha":
                logger.warning("🤖 遇到验证码，需要人工处理")
                self._notify_user("captcha_required", {
                    "message": "页面显示验证码，请手动完成",
                    "platform": platform_name,
                })
                await asyncio.sleep(5)  # 给用户时间处理
                continue

            elif page_type == "publishing":
                logger.info("⏳ 发布中，等待完成...")
                await asyncio.sleep(2)
                continue

            elif page_type in ("home", "editor", "other"):
                # 有可用操作就执行，没有就说明流程卡住了
                actions = state.get("available_actions", [])
                if not actions:
                    logger.warning("无可执行操作，可能需要导航")
                    # 尝试通过 URL 等参数判断下一步
                    return {"success": False, "error": "无法确定下一步操作", "state": state}

                # 执行最高优先级操作
                best_action = self._find_priority_action(state, "high") or actions[0]
                logger.info(f"  → 执行: {best_action.get('action')} {best_action.get('target', '?')}")
                await self._execute_state_action(best_action)
                await asyncio.sleep(1.5)
                continue

            else:
                logger.warning(f"未知页面类型: {page_type}")
                await asyncio.sleep(2)

        return {"success": False, "error": f"超过最大步骤数 ({max_steps})", "state": last_state}

    def _find_priority_action(self, state: dict, priority: str) -> Optional[dict]:
        """从状态中找指定优先级的操作。"""
        actions = state.get("available_actions", [])
        for a in actions:
            if a.get("priority") == priority:
                return a
        return actions[0] if actions else None

    async def _execute_state_action(self, action_item: dict):
        """执行从页面状态中提取的操作——全部后台执行。"""
        pid = getattr(self, "_target_pid", None)
        act = action_item.get("action", "click")
        x, y = action_item.get("x", 0), action_item.get("y", 0)
        target = action_item.get("target", "?")

        if act == "click":
            action.smart_click(x, y, pid)
            logger.info(f"    后台点击: {target} ({x},{y})")
        elif act == "type":
            action.smart_click(x, y, pid)
            action.human_pause()
            # 如果有预设文本就输入，否则粘贴剪贴板
            text = action_item.get("text", "")
            if text:
                action.smart_type(text, pid)
            else:
                action.background_paste(pid)
            logger.info(f"    后台输入: {target}")

        action.human_pause()

    def _notify_user(self, event_type: str, data: dict):
        """通知用户需要介入。默认打印到终端，可被外部回调覆盖。"""
        cb = getattr(self, "_user_callback", None)
        if cb:
            cb(event_type, data)
        else:
            print(f"\n{'='*50}")
            print(f"  ⚠️  [{event_type}] {data.get('message', '')}")
            print(f"  平台: {data.get('platform', '?')}")
            print(f"{'='*50}\n")

    def on_user_action_needed(self, callback):
        """设置用户介入回调。Agent 遇到登录/验证码时会调用此函数。

        callback(event_type, data)
          event_type: "login_required" | "captcha_required"
          data: {"message": str, "platform": str}
        """
        self._user_callback = callback

    # ═══════════════════════════════════════════
    #  辅助方法
    # ═══════════════════════════════════════════

    def _empty_result(self, error: str) -> dict:
        return {
            "title": "",
            "author": "",
            "text_content": "",
            "images": [],
            "comments": [],
            "_error": error,
        }

    async def find_and_focus_window(self, titles: list[str]) -> Optional[str]:
        """在窗口列表中查找匹配的窗口并激活。"""
        all_titles = screen.list_window_titles()
        for search in titles:
            for t in all_titles:
                if search.lower() in t.lower():
                    screen.activate_window(search)
                    logger.info(f"已激活窗口: {t}")
                    return t
        logger.warning(f"未找到匹配窗口: {titles}")
        return None
