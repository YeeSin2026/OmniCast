# OmniCast — 知识驱动的全平台社媒内容工厂

从知识库读取内容总结，一键生成 7 大平台社媒内容 + 7 维质量评分 + 传播预测 + 文案对标。

## 快速开始

```bash
# 1. 克隆
git clone https://github.com/yeesin/OmniCast.git
cd OmniCast

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env，填入 LLM_API_KEY 和激活码

# 3. 启动
docker compose up -d
# 访问 http://localhost:8081
```

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `LLM_API_KEY` | DeepSeek API Key | - |
| `LLM_BASE_URL` | LLM 服务地址 | `https://api.deepseek.com` |
| `LLM_MODEL` | 模型名称 | `deepseek-chat` |
| `OMNIVAULT_API_URL` | OmniVault 地址（可选） | `http://localhost:8080` |
| `ACTIVATION_SECRET` | 激活码签名密钥 | - |
| `ACTIVATION_ENABLED` | 是否启用激活验证 | `true` |

## 功能

- **7 平台内容生成**：抖音/小红书/Twitter/微信公众号/YouTube/LinkedIn/Instagram 一键生成
- **7 维质量评分**：ER 情感共鸣 × SR 社会共振 × HP 钩子强度 × QL 金句密度 × NA 叙事性 × AB 受众广度 × TS 分享冲动
- **文案对标**：分析参考文案的 7 维写作技法，结合你的资料对标创作
- **独立内容提取**：粘贴各平台分享链接自动提取文案（30+ 平台支持）
- **知识关联分析**：AI 自动发现知识条目之间的关联
- **自适应创作人格**：根据用户资料自动切换领域和专业风格
- **语音合成**：CosyVoice 声音克隆 + TTS

## 技术栈

- FastAPI + Jinja2/HTMX + Tailwind CSS
- SQLite
- DeepSeek API

## 激活

首次访问需输入激活码（格式 `OMC-XXXX-XXXX-XXXX`）。获取激活码请联系开发者。

## License

MIT
