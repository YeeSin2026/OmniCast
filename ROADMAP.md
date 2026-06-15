# OmniCast 开发路线图

> ⚠️ 内部文件，不提交到 GitHub。

---

## 当前版本：已发布

已完成：AI Agent 内容生成（7 平台适配）、7 维评分 + 盲预测、OmniVault 知识库集成、URL 直接输入、视觉自动化模块、语音合成。

---

## P0 — 创作决策

### P0-1: 选题推荐
**目标**：不需要人工翻知识库找选题，打开 OmniCast 就有推荐
- `GET /api/editor/picks?limit=10` — 今日推荐选题
  - 评分公式：内容质量(评论丰富度)×0.4 + 时效性×0.3 + 话题广度×0.3
  - 调用 OmniVault API 获取知识条目数据，OmniCast 侧做评分排序
- `GET /api/editor/trending?source=weibo` — 热点匹配
  - 拉微博热搜/知乎热榜 → 调 OmniVault `/api/agent/search` 交叉匹配 → 返回「这个话题有素材」
- 首页「今日推荐选题」模块
- **预估**：3-4 天
- **依赖**：OmniVault 已提供 `/api/agent/search`（hybrid 模式）

### P0-2: 选题日历
**目标**：热点事件日历 + 知识库储备 → 推荐下周发布计划
- 热点事件日历视图
- 基于知识库标签匹配推荐内容储备
- 一键生成下周发布计划
- **预估**：3-4 天

---

## P1 — 效果闭环

### P1-1: 内容效果回流（OmniCast 侧）
**目标**：发布后数据自动回流到 OmniVault，让知识价值可量化
- 发布后回调 OmniVault `POST /api/feedback`
- 上报：发布平台、播放量、点赞、评论、转发
- 配合 OmniVault 侧 feedback 表和 API（见 OmniVault ROADMAP.md P1-2）
- **预估**：1-2 天
- **依赖**：OmniVault 需先实现 `POST /api/feedback`

---

## P2 — 发布能力

### P2-1: 多平台一键发布
- 接入各平台发布 API（抖音、小红书、YouTube、公众号等）
- 定时发布 + 草稿箱
- **预估**：5-7 天

### P2-2: 发布效果追踪
- 仪表盘展示各平台发布效果
- 同期对比 + 趋势图
- **预估**：3-4 天

---

## 远期

- A/B 内容变体测试
- AI 生成封面图
- 竞品账号监控
- 内容日历协作（团队版）
