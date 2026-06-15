# OmniCast

**知识驱动的全平台社媒内容工厂** — 粘贴链接自动提取文案，7 维度分析对标，一键生成多平台内容。

*A knowledge-driven, multi-platform social media content factory — paste a link to extract copy, analyze with 7-dimension benchmarks, and generate platform-optimized content in one click.*

---

## 快速开始 · Quick Start

```bash
git clone https://github.com/YeeSin2026/OmniCast.git
cd OmniCast
docker compose up -d
```

打开浏览器访问 `http://localhost:8081`，按页面引导完成激活和配置即可使用。

*Open `http://localhost:8081` in your browser, follow the on-screen guide to activate and configure.*

## 配置 · Configuration

首次启动后通过 Web UI（`/setup`）配置，无需手动编辑 `.env` 文件。

*Configure via the Web UI (`/setup`) on first launch — no need to edit `.env` files manually.*

| 配置项 · Setting | 说明 · Description |
|---|---|
| `LLM_API_KEY` | DeepSeek API Key（必填 · Required） |
| `LLM_BASE_URL` | LLM 服务地址 · API endpoint |
| `LLM_MODEL` | 模型名称 · Model name |
| `OMNIVAULT_API_URL` | OmniVault 地址（可选 · Optional） |

配置保存在 `/data/config.json`，重启后仍然有效。

*Settings are persisted to `/data/config.json` and survive restarts.*

## 激活 · Activation

首次访问需要输入激活码（格式 `OMC-XXXX-XXXX-XXXX`）。

*An activation key (format: `OMC-XXXX-XXXX-XXXX`) is required on first access.*

使用 `scripts/generate_key.py` 生成激活码。确保 `ACTIVATION_SECRET` 与服务端一致。

*Use `scripts/generate_key.py` to generate keys. Ensure `ACTIVATION_SECRET` matches the server.*

## 功能 · Features

### 内容生成 · Content Generation
- **7 平台适配**：抖音 / 小红书 / Twitter/X / 微信公众号 / YouTube / LinkedIn / Instagram
- **8 种情绪基调**：激情 / 冷静 / 温暖 / 犀利 / 幽默 / 紧迫 / 鼓舞 / 深度
- **自适应人格**：根据用户提供的资料自动切换领域和风格

### 文案对标 · Copy Benchmarking
- 粘贴对标视频链接，自动提取完整口播文案
- 7 维度技法拆解：钩子强度 / 情感共鸣 / 社会共振 / 金句密度 / 叙事性 / 受众广度 / 分享冲动
- 结合你的产品资料，用对标技法生成新文案

### 内容提取 · Content Extraction
- 支持 30+ 平台分享链接（抖音/小红书/快手/B站/微博/微信/YouTube/Instagram/X/TikTok…）
- 视频类走深度采集获得完整口播文案，图文类本地直接提取
- 粘贴完整分享口令文本即可，自动识别链接

### 质量评分 · Quality Scoring
- 7 维评分体系，复合加权算法
- 传播预测：基于评分预测播放量级
- 标题/标签分项打分

### 知识关联 · Knowledge Linking
- AI 自动发现知识条目之间的关联
- 支持多条目合并创作
- 关联结果缓存，避免重复 token 消耗

## 技术栈 · Tech Stack

| 层 · Layer | 技术 · Technology |
|---|---|
| 后端 · Backend | Python / FastAPI |
| 前端 · Frontend | Jinja2 / HTMX / Tailwind CSS |
| 数据库 · Database | SQLite |
| AI | DeepSeek API |
| 部署 · Deployment | Docker |

## 环境变量 · Environment Variables

| 变量 · Variable | 说明 · Description | 默认值 · Default |
|---|---|---|
| `LLM_API_KEY` | DeepSeek API Key | - |
| `LLM_BASE_URL` | LLM 服务地址 | `https://api.deepseek.com` |
| `LLM_MODEL` | 模型名称 | `deepseek-chat` |
| `OMNIVAULT_API_URL` | OmniVault 地址 | `http://localhost:8080` |
| `ACTIVATION_ENABLED` | 启用激活验证 | `true` |
| `ACTIVATION_SECRET` | 激活码签名密钥 | - |

## License

MIT
