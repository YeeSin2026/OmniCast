# OmniCast — 知识驱动的全平台社媒内容工厂

## 定位
从 OmniVault 知识库读取 AI 总结 → 一键生成 7 大平台社媒内容（AI布道风格）+ 7 维质量评分 + 传播预测。**这是发布端，知识采集在 OmniVault 项目。**

## 部署
```bash
cd ~/Projects/OmniCast
docker compose up -d   # 端口 8081
# 前提：OmniVault 必须在 8080 在线
# 基础镜像复用 omnivault:3.0.0（不需要拉取 python:3.11-slim）
```

## 技术栈
- FastAPI + Jinja2/HTMX + Tailwind CDN
- 独立 SQLite（/data/drafts.db）
- AI：DeepSeek API（独立 llm_client.py，不依赖 OmniVault）
- 通过 HTTP API 连接 OmniVault（connector.py）

## 核心模块
- `src/app.py` — FastAPI 入口，注册 18 个路由
- `src/settings.py` — 配置（LLM、OmniVault URL、DB 路径）
- `src/connector.py` — OmniVault API 客户端（list_entries / get_entry / health_check）
- `src/llm_client.py` — 独立 LLM 调用（chat 函数）
- `src/social_agent/` — 内容生成核心
  - `prompts.py` — AI布道 persona + 7 平台适配 prompt
  - `generator.py` — asyncio.gather 并行生成
  - `scorer.py` — 7 维评分 + 盲预测（改编自 Cheat on Content）
  - `store.py` — DraftStore（content_drafts + content_scores 表）
  - `routes.py` — 18 个路由（页面 + API + 评分 + 预测）

## 7 维评分体系
ER(情感共鸣)×1.5 + SR(社会共振)×1.5 + HP(钩子强度)×1.5 + QL(金句密度) + NA(叙事性) + AB(受众广度) + TS(分享冲动)
composite = (ER×1.5+SR×1.5+HP×1.5+QL+NA+AB+TS)/8.5×2.0 → 0-10 分

## 工作流
选知识条目 → 选目标平台 → 并行生成 → 预览草稿 → 打分+预测 → 人工审批 → ready for publish

## 环境变量（.env）
LLM_API_KEY / LLM_BASE_URL / LLM_MODEL / OMNIVAULT_API_URL / OMNICAST_DB_PATH

## 与 OmniVault 的关系
- OmniCast 通过 `GET http://omnivault:8080/api/videos` 读取知识条目
- OmniCast 通过 `GET http://omnivault:8080/api/videos/{id}` 读取详情
- OmniCast 不直接访问 OmniVault 的数据库
- OmniVault 离线时 OmniCast 仍可启动，只是读不到条目
