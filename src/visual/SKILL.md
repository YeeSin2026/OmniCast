# visual-agent — macOS 视觉自动化 Skill

基于截图+视觉模型+OS级后台点击的 GUI Agent。
不操控 DOM，不注入 JS，不会被平台风控检测。

## 前置条件

1. macOS 14+
2. 系统设置 → 隐私与安全性 → **辅助功能**（Accessibility）→ 添加终端
3. 系统设置 → 隐私与安全性 → **屏幕录制**（Screen Recording）→ 添加终端
4. OMLX 运行视觉模型（默认 UI-TARS-1.5-7B），监听 127.0.0.1:8000
5. 目标浏览器已登录各平台账号（正常使用态，不需要额外 cookie 管理）

## 核心能力

### 1. 后台截图
```bash
visual-agent screenshot --window Chrome -o /tmp/page.png
```
不激活窗口、不切换 Space、不干扰你当前工作。

### 2. 页面状态检测
```bash
visual-agent detect --window Chrome
```
返回 JSON：
```json
{
  "page_type": "editor",
  "logged_in": true,
  "can_proceed": true,
  "user_action_needed": "",
  "available_actions": [
    {"index": 1, "action": "click", "target": "发布按钮", "x": 800, "y": 600, "priority": "high"}
  ]
}
```

### 3. 智能点击（SoM 增强）
```bash
visual-agent click --window Chrome --target "发布按钮"
```
流程：截图 → AX/VLM 扫描元素 → 叠加编号 → 视觉模型选号 → 后台点击

### 4. 智能发布（状态机）
```bash
visual-agent publish --window Chrome -f /tmp/content.json -p xiaohongshu
```
自动处理：
- 检测登录状态 → 未登录提示扫码 → 等待 → 检测登录成功 → 继续
- 导航到发布页 → 填写标题/正文 → 发布
- 遇到验证码 → 通知用户
- 发布成功/失败 → 返回结果

content.json 格式：
```json
{
  "title": "文章标题",
  "body": "正文内容...",
  "tags": "tag1,tag2"
}
```

### 5. 元素扫描
```bash
visual-agent scan --window Chrome -o /tmp/elements.png
```
输出带 SoM 编号标记的截图 + 元素列表。

### 6. HTTP API 服务
```bash
visual-agent serve --port 8100
```
供远程 Agent 调用：
- `GET /health` — 健康检查
- `GET /windows` — 窗口列表
- `POST /detect` — 页面状态检测 `{"window": "Chrome"}`
- `POST /click` — 智能点击 `{"window": "Chrome", "target": "发布按钮"}`
- `POST /publish` — 智能发布 `{"window": "Chrome", "content": {...}, "platform": "xiaohongshu"}`

## 给 Agent 的使用指南

### 场景 1：用户要求发布内容到小红书

```bash
# Step 1: 确保浏览器打开
visual-agent list-windows | grep -i chrome

# Step 2: 检测当前页面状态
visual-agent detect --window Chrome
# → {"page_type": "home", "logged_in": true, ...}

# Step 3: 写入内容文件
cat > /tmp/post.json << 'EOF'
{"title": "...", "body": "...", "tags": "..."}
EOF

# Step 4: 智能发布
visual-agent publish --window Chrome -f /tmp/post.json -p xiaohongshu
# 如果需要登录，会输出 ⚠️ login_required 并等待
```

### 场景 2：需要扫码登录

```
Agent 调用 visual-agent publish → 检测到 login 状态
→ 输出: ⚠️ [login_required] 请扫描小红书二维码登录
→ 每 3 秒重新检测
→ 用户扫码完成 → 检测到 logged_in=true
→ 自动继续发布流程
```

### 场景 3：遇到验证码

```
Agent 调用 visual-agent publish → 检测到 captcha 状态
→ 输出: ⚠️ [captcha_required] 请手动完成验证
→ 等待 5 秒 → 重新检测
→ 验证码已通过 → 继续
```

## 技术原理

- **截图**: `screencapture -R` 或全屏 + PIL crop（不激活窗口）
- **元素定位**: macOS AX API（原生 App） + UI-TARS VLM 视觉检测（网页内容）
- **后台点击**: Swift 编译的 `bgclick` → `CGEventPostToPid`（不抢光标）
- **后台输入**: Swift 编译的 `bgtype` → `CGEventPostToPid`（不抢焦点）
- **状态检测**: UI-TARS 分析截图 → 返回 page_type + available_actions

## 文件结构

```
src/visual/
├── bin/
│   ├── bgclick        # Swift, CGEventPostToPid 点击
│   ├── bgtype         # Swift, CGEventPostToPid 输入
│   └── visual-agent   # Python CLI, 主入口
├── screen.py          # 截图 + 窗口管理
├── vision.py          # 视觉模型接口
├── action.py          # 后台/前台 鼠标键盘
├── som.py             # Set-of-Mark UI 元素检测
├── agent.py           # 编排调度器 + 状态机
└── SKILL.md           # 本文件
```
