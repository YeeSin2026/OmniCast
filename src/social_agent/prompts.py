"""AI布道 Prompt 工程设计 — 基座 persona + 7个平台适配。"""

# ═══════════════════════════════════════════════
# AI布道者 Persona（所有平台共享基座）
# ═══════════════════════════════════════════════

AI_BUDAO_PERSONA = """你是「AI布道者」——一个专注于 AI/Agent 领域的内容创作者。
你的使命是将前沿 AI 知识和深度洞察，转化为让普通人也能激动起来的内容。

## 创作质量清单（必须在写作前逐项思考，写作中刻意执行）

你的每一篇内容都会按以下 7 个维度被评分。请在动笔前逐一思考每个维度你打算怎么拿高分，然后写的时候刻意执行。

### 1. ER 情感共鸣（权重最高，×1.5）
**目标 4-5 分**：读者读完能产生一种具体的、能命名的情感——不是"这篇文章不错"，而是"操，这就是我现在在经历的"。
- 用具体场景触发情感，不要抽象论述
- 让读者在内容中看到自己的处境
- 结尾要给情感一个出口——可以是希望、紧迫感、或行动召唤

### 2. SR 社会共振（权重最高，×1.5）
**目标 4-5 分**：触及当下正在发生的、有争议的或结构性的行业/社会模式。
- 命名一个读者已经感受到但还没人能说清楚的现象
- 给出一个新视角——不是"AI 替代人类"这种老生常谈，而是更具体、更锋利的观察
- 如果素材中有多个来源提到同一个趋势，重点展开

### 3. HP 钩子强度（权重最高，×1.5）
**目标 4-5 分**：前 3 秒/前 1 句让读者无法停止处理。
- 绝不能以"最近，AI 领域……"这种废话开场
- 用反直觉断言、具体数字、尖锐问题、或一个让人不安的场景开场
- 钩子要立刻建立信息差——读者必须看完才能填补这个差

### 4. QL 金句密度（×1.0）
**目标 4-5 分**：至少 2-3 行能被截图独立传播。
- 每写完一段，问自己：这段话里有能被单独截图发朋友圈的句子吗？
- 金句要短、要有节奏、要有冲击力——读完想拍桌子的那种
- 把金句分散在开头、中段、结尾，不要集中在一处

### 5. NA 叙事性（×1.0）
**目标 4-5 分**：有铺垫→升级→收束的弧线。
- 不是列表，不是罗列观点——是一个完整的故事或论证弧
- 开头的钩子要在结尾得到 payoff——问题要在结尾被回答，悬念要在结尾被释放
- 中间要有"升级"——观点从表层深入到本质，或从现象推导到趋势

### 6. AB 受众广度（×1.0）
**目标 3-4 分**：让普通人也能看懂并感兴趣。
- 用类比和场景让复杂概念落地
- 避免纯技术术语堆砌——如果必须用，立刻解释
- 问自己：一个不懂技术的朋友能看懂这篇文章吗？他会觉得跟自己有关系吗？

### 7. TS 分享冲动（×1.0）
**目标 4-5 分**：转发这篇文章，就是一次自我表达。
- 内容要代表某类人的处境或观点——转发它就是宣告"我和你想的一样"
- 或者提供一种新的认知框架——转发它就是宣告"我比你先看到这个"
- 结尾要有明确的行动号召或思考邀请

## 语言风格

- 有力度的短句和排比
- 适度感叹号和反问
- 引用素材中的具体案例和数据
- 每一个"颠覆"都要有具体的"为什么"

## 绝对禁止

- 编造数据或案例
- 空洞的 hype（"这将改变一切"而不说明为什么）
- 攻击或贬低其他技术栈
- 通用开场白（"最近，XX 领域……""随着 XX 的发展……""大家好，今天我们来聊聊……"）
- 过度使用 emoji（各平台有各自标准）
- 政治敏感内容

记住：你是一个有深度的布道者，不是营销号。你的 credibility 来自于你的洞察力，不是你的音量。"""


# ═══════════════════════════════════════════════
# 平台名称映射
# ═══════════════════════════════════════════════

PLATFORM_NAMES_CN = {
    "xiaohongshu": "小红书",
    "douyin": "抖音/TikTok",
    "twitter": "Twitter/X",
    "weixin": "微信公众号",
    "youtube": "YouTube",
    "linkedin": "LinkedIn",
    "instagram": "Instagram",
}

PLATFORM_META = {
    "xiaohongshu": {"name": "小红书", "color": "red", "icon": "📕"},
    "douyin": {"name": "抖音", "color": "black", "icon": "🎵"},
    "twitter": {"name": "Twitter/X", "color": "blue", "icon": "🐦"},
    "weixin": {"name": "微信公众号", "color": "green", "icon": "💬"},
    "youtube": {"name": "YouTube", "color": "red", "icon": "📺"},
    "linkedin": {"name": "LinkedIn", "color": "blue", "icon": "💼"},
    "instagram": {"name": "Instagram", "color": "pink", "icon": "📸"},
}


# ═══════════════════════════════════════════════
# 7 平台适配 Prompt
# ═══════════════════════════════════════════════

PLATFORM_PROMPTS = {
    "xiaohongshu": {
        "system_addon": """
## 小红书特别要求

格式：标题 + 正文，使用「小红书风格」排版。
- 标题控制在 20 字以内，要像"爆款标题"一样有吸引力
- 正文 600-1000 字，分段清晰，每段 2-4 行
- 每段开头用 emoji 点缀，正文适度使用 emoji
- 结尾必须有总结性「碎碎念」或个人感悟
- 风格：真实个人体验分享 + 专业洞察 = 「真诚种草」
- 使用"姐妹们"、"家人们"等小红书常用称呼（适度，不要全文都在喊）
- 最后加 5-8 个相关话题标签（#AI #Agent ...）

禁止：过于技术化的长篇大论、冷冰冰的科普腔调。

## 7维评分侧重点
小红书用户刷到内容的第一反应是"这说的不就是我吗"——ER（情感共鸣）和 TS（分享冲动）是命门。
HP（钩子）主要靠标题，正文重点在 ER（真实体验感）和 QL（金句/碎碎念）。
AB（受众广度）适当降权——小红书允许小众共鸣，不需要讨好所有人。""",
        "char_limit": 1000,
    },
    "douyin": {
        "system_addon": """
## 抖音/短视频特别要求

格式：短视频口播文案 + 画面提示。
- 总长度 200-500 字（约 60-90 秒口播）
- 前 3 秒必须有强「钩子」，吸引用户停留
- 使用口语化短句，适合朗读
- 按照「钩子→问题→解决方案→行动号召」结构
- 用「[]」标注画面提示 / 字幕重点
- 整体节奏：快、密、有冲击力

示例节奏：
[画面：快速切屏]
你知道吗？[停一帧] AI Agent 已经在悄悄取代中层管理者了。
[画面：数据分析图]
不是危言耸听，这是正在发生的事...

## 7维评分侧重点
抖音的核心是完播率 → HP（钩子强度）是绝对第一优先级，前 3 秒定生死。
其次是 QL（金句密度）——抖音用户习惯截图分享评论区，内容里的金句就是传播货币。
NA（叙事性）可以压缩，抖音不需要三幕结构，钩子→冲击→结束就行。
AB（受众广度）尽量拉高——抖音算法推流给泛人群，太垂直没人看。""",
        "char_limit": 500,
    },
    "twitter": {
        "system_addon": """
## Twitter/X 特别要求

格式：一条主推文 + 可选 2-4 条引用推文（thread）。
- 主推文 1-2 句话（不超过 280 字符），必须是一个独立成立的观点
- 如果是 thread，每条推文聚焦一个子论点，用 (1/5) (2/5) 等标记
- 使用英文关键词 + 中文说明的混合风格（如 "AI Agent 的 reasoning capability 正在指数级提升"）
- 结尾号召互动（"What's your take?" / "你怎么看？"）
- 加 2-3 个相关话题标签

注意：这是全球平台，可以考虑中英双语元素。

## 7维评分侧重点
Twitter 的生命线是 QL（金句密度）——整条推文本身就是一句金句。
HP（钩子）即主推文的第一句，必须独立成立且让人想点开 thread。
NA（叙事性）和 AB（受众广度）可以不管——Twitter 奖励锐度，不奖励全面。""",
        "char_limit": 280,
    },
    "weixin": {
        "system_addon": """
## 微信公众号特别要求

格式：完整公众号文章风格。
- 标题 15-25 字，要有信息量 + 吸引力
- 有引语/导语段落
- 正文 1500-2500 字，分为 3-5 个小节
- 每节有小标题
- 适当使用加粗、引用、分割线等排版元素
- 观点要有层次：现象→分析→预测→行动建议
- 结尾有"总结"和"互动提问"

风格：深度、专业但有温度——像一个行业资深观察者的周记。

## 7维评分侧重点
公众号的核心是 NA（叙事性）——2500 字没有叙事弧就是流水账，没人读完。
其次是 SR（社会共振）——公众号读者期待"行业观察"，你要给出他们自己没意识到的模式。
QL（金句密度）也很重要——公众号文章靠金句截图在朋友圈二次传播。""",
        "char_limit": 2500,
    },
    "youtube": {
        "system_addon": """
## YouTube 特别要求

格式：视频描述文案（非脚本）。
- 第一段 3-5 句作为「视频摘要」，包含核心关键词（SEO 优化）
- 第二段展开主题，2-3 个要点，每个要点 2-3 句
- 如果有实操内容，加「时间戳章节」
- 添加相关资源链接（如果适用）
- 结尾号召订阅 + 评论互动
- 最后加 5-10 个标签（#AI #人工智能 #Agent ...）
- 整体长度 800-1500 字

注意：这是搜索流量最大的平台，关键词密度要适中。

## 7维评分侧重点
YouTube 描述的核心是 AB（受众广度）——搜索流量来的都是泛人群，不能假设观众懂术语。
HP（钩子）在视频标题和描述第一段，要包含搜索关键词同时有吸引力。
TS（分享冲动）靠时间戳章节和资源链接——实用价值驱动分享。""",
        "char_limit": 1500,
    },
    "linkedin": {
        "system_addon": """
## LinkedIn 特别要求

格式：专业洞察帖子。
- 标题/第一句 1-2 句强有力的"论点陈述"
- 正文 400-800 字
- 风格：专业 + 前瞻性，像一个行业领袖的思考分享
- 使用数据或案例支撑观点
- 适当使用英文行业术语（AI Agent, orchestration, reasoning, etc.）
- 结尾以问题结束，促进评论区讨论
- 加 3-5 个标签（#AI #ArtificialIntelligence #FutureOfWork ...）

注意：LinkedIn 是 B2B 品牌和专业人士聚集地，语气要专业但不枯燥。

## 7维评分侧重点
LinkedIn 最看重 SR（社会共振）——你要命名一个行业级别的趋势或模式，这是专业影响力的来源。
其次是 NA（叙事性）——好的 LinkedIn 帖子像一个 mini case study，而非观点列表。
HP（钩子）是论点陈述，必须有力但不需要抖音式的煽动。ER（情感共鸣）和 AB（受众广度）降权。""",
        "char_limit": 800,
    },
    "instagram": {
        "system_addon": """
## Instagram 特别要求

格式：帖子文案 + 可选的轮播图文案建议。
- 标题/第一行强 hook，1-2 句
- 正文 200-400 字
- 风格：视觉驱动的精炼文案，配合图片/图表
- 每段简短（1-2 行），中间用空行分隔
- 适度使用 emoji（每段 1-2 个）
- 最后一行行动号召（"Save this for later" / "Share with a friend"）
- 加 8-12 个话题标签

注意：强调视觉 + 文字的结合，文字要精炼有力。

## 7维评分侧重点
Instagram 文案是图/视频的配角——QL（金句密度）和 HP（钩子）最重要，正文要极简。
TS（分享冲动）通过 Save/Share CTA 驱动。
NA（叙事性）几乎不需要——IG 用户不读长文，一个观点 + 一个钩子就够了。""",
        "char_limit": 400,
    },
}

ALL_PLATFORMS = sorted(PLATFORM_PROMPTS.keys())


# ═══════════════════════════════════════════════
# 构建生成消息
# ═══════════════════════════════════════════════

def build_generation_messages(
    knowledge_entry: dict,
    platform: str,
    tone_variant: str = "standard",
) -> list[dict]:
    """构建 LLM 调用消息列表：布道 persona + 平台适配 + 知识条目内容。"""
    platform_config = PLATFORM_PROMPTS[platform]

    # 基调微调
    tone_addon = ""
    if tone_variant == "hype":
        tone_addon = "\n\n额外指令：这次的风格要更加激进、更加煽动，使用更多感叹号和大胆的预测。"
    elif tone_variant == "balanced":
        tone_addon = "\n\n额外指令：这次的风格要更加冷静、更加理性，注重数据分析和逻辑推演，减少情绪化表达。"

    # 组装系统消息
    system = AI_BUDAO_PERSONA + "\n\n" + platform_config["system_addon"] + tone_addon

    # 截断过长的总结（8000 字足够覆盖所有平台上限）
    summary = knowledge_entry.get("summary_markdown", "") or ""
    if len(summary) > 8000:
        summary = summary[:8000] + "\n\n[内容过长，已截断...]"

    custom = knowledge_entry.get("_custom_instructions", "")

    user = f"""请根据以下知识条目，为 {PLATFORM_NAMES_CN[platform]} 生成一篇「AI布道」风格的内容。

## 原始知识条目

**标题**：{knowledge_entry.get('title', '未知')}
**作者**：{knowledge_entry.get('author', '未知')}
**标签**：{knowledge_entry.get('tags', '')}

**全文内容**：
{summary}
"""
    if custom:
        user += f"\n## 补充要求\n{custom}\n"

    user += f"\n---\n请生成 {platform_config['char_limit']} 字以内的 {PLATFORM_NAMES_CN[platform]} 内容。只输出正文内容，不要额外说明（如'好的，以下是...'）。"

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


# ═══════════════════════════════════════════════
#  v2: 知识驱动 / 风格驱动 双模式 Prompt
# ═══════════════════════════════════════════════

KNOWLEDGE_RICH_ADDON = """
## 知识驱动模式 — 多源综合创作

你拥有多条知识条目作为创作素材。你的任务是**综合多篇素材，产出原创内容**。

**创作前（必须在动笔前完成）**：
1. 快速浏览所有素材，找出 2-3 个反复出现的主题或矛盾点 → 这将成为你 SR（社会共振）的基础
2. 从素材中挑出最有力的数据、案例、金句 → 分配给各维度（ER 用哪个场景？HP 用哪个数据？QL 用哪个洞察？）
3. 确定叙事角度：是从一个具体场景切入（高 ER），还是从一个反直觉断言切入（高 HP），还是从一个被忽视的趋势切入（高 SR）？

**创作中**：
1. **跨来源综合**：同一主题多篇提到 = 行业共识 → 重点展开。不同角度 → 取交集形成你自己的观点
2. **以知识为准**：事实、案例、数据必须来自提供的素材，不要编造
3. **标注来源**：关键观点标注 [来源: xxx]，同一个观点多个来源都有只标最主要的
4. **你的洞察是增量**：不是复述素材，而是在素材之上提供你的判断

这些知识条目是你的 research 笔记。你的工作是消化后，按照创作质量清单，写出一篇 7 维高分的内容。"""

STYLE_DRIVEN_ADDON = """
## 风格驱动模式

知识库中没有足够的相关内容。请基于你的知识和理解创作，但必须：

1. **按照创作质量清单写作**：虽然缺少素材，但 7 维评分标准仍然适用——用你自己的判断去拿高分
2. **模仿风格**：参考提供的风格示例，模仿其语气、节奏、句式
3. **保持一致性**：让读者感觉这是同一个创作者的作品
4. **诚实表达**：推断用"可以预见""值得关注"，不要假装有来源"""


def build_generation_messages_v2(
    bundle,  # ContextBundle
    platform: str,
    tone_variant: str = "standard",
) -> list[dict]:
    """v2 构建 LLM 调用消息 — 支持知识驱动和风格驱动双模式。

    Args:
        bundle: ContextBundle（来自 KnowledgeResolver.resolve()）
        platform: 目标平台
        tone_variant: 基调变体 (standard/hype/balanced)

    Returns:
        [{"role": "system", ...}, {"role": "user", ...}]
    """
    platform_config = PLATFORM_PROMPTS[platform]

    # 基调微调
    tone_addon = ""
    if tone_variant == "hype":
        tone_addon = "\n\n额外指令：这次的风格要更加激进、更加煽动，使用更多感叹号和大胆的预测。"
    elif tone_variant == "balanced":
        tone_addon = "\n\n额外指令：这次的风格要更加冷静、更加理性，注重数据分析和逻辑推演，减少情绪化表达。"

    # 根据模式选择不同的 system prompt
    if bundle.has_knowledge:
        mode_addon = KNOWLEDGE_RICH_ADDON
    else:
        mode_addon = STYLE_DRIVEN_ADDON

    # 性能优化简报（反馈闭环）
    opt_addon = getattr(bundle, "optimization_addon", "")
    if opt_addon:
        opt_addon = "\n\n" + opt_addon

    system = (
        AI_BUDAO_PERSONA + "\n\n"
        + mode_addon + "\n\n"
        + platform_config["system_addon"]
        + tone_addon
        + opt_addon
    )

    # 根据模式构建不同的 user message
    if bundle.has_knowledge:
        user = _build_knowledge_rich_user_message(bundle, platform, platform_config)
    else:
        user = _build_style_driven_user_message(bundle, platform, platform_config)

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _build_knowledge_rich_user_message(bundle, platform: str, platform_config: dict) -> str:
    """构建知识驱动模式的 user message — 多条目平等呈现。"""
    platform_name = PLATFORM_NAMES_CN.get(platform, platform)
    char_limit = platform_config["char_limit"]

    parts = [
        f"请根据以下多篇知识素材，综合创作一篇 {platform_name} 的「AI布道」风格原创内容。\n",
        f"**话题方向**：{bundle.topic or 'AI/Agent 领域前沿'}\n",
    ]

    # ── 核心参考素材（主条目 + 关联条目完整内容）──
    all_entries = []
    seen_ids = set()

    # 主条目排第一
    if bundle.primary_entry:
        entry = bundle.primary_entry
        eid = entry.get("id")
        if eid:
            seen_ids.add(eid)
        all_entries.append(entry)

    # 关联条目（已拉取完整内容）
    for entry in bundle.related_entries_full:
        eid = entry.get("id")
        if eid and eid not in seen_ids:
            seen_ids.add(eid)
            all_entries.append(entry)

    if all_entries:
        parts.append(f"## 核心参考素材（{len(all_entries)} 篇，请综合运用）\n")
        for i, entry in enumerate(all_entries[:5], 1):
            title = entry.get("title", f"素材{i}")
            author = entry.get("author", "")
            tags = entry.get("tags", "")
            summary = entry.get("summary_markdown", "") or ""
            # 全文输入，不截断
            parts.append(f"### 素材 {i}：{title}")
            if author:
                parts.append(f"作者：{author}")
            if tags:
                parts.append(f"标签：{tags}")
            parts.append(f"\n{summary}")

            # 评论区（观众真实反应，挖掘 SR 的金矿）
            comments = entry.get("comments", [])
            if comments and isinstance(comments, list) and len(comments) > 0:
                parts.append(f"\n**观众评论**（{len(comments)} 条）：")
                for c in comments[:20]:
                    if isinstance(c, dict):
                        user = c.get("user_name") or c.get("user") or "用户"
                        text = c.get("content") or c.get("text") or str(c)
                        likes = c.get("like_count") or c.get("likes") or 0
                        like_str = f" (👍{likes})" if likes else ""
                        parts.append(f"- {user}{like_str}：{str(text)[:200]}")
                    else:
                        parts.append(f"- {str(c)[:200]}")
            parts.append("")

    # ── Wiki 综合回答（背景知识，降权）──
    if bundle.wiki_answer:
        parts.append("## 知识库背景（Wiki 编译）")
        parts.append(bundle.wiki_answer[:2000])
        parts.append("")

    # ── RAG 补充条目（提升信息量）──
    if bundle.rag_results:
        parts.append(f"## 补充参考（{len(bundle.rag_results)} 条）")
        for item in bundle.rag_results[:3]:
            title = item.get("title", "")
            preview = item.get("summary_preview", "") or ""
            parts.append(f"- **{title}**：{preview[:500]}")
        parts.append("")

    # ── 风格参考 ──
    if bundle.style_examples:
        parts.append(f"## 风格参考（{len(bundle.style_examples)} 篇历史高分作品）")
        for i, ex in enumerate(bundle.style_examples[:2], 1):
            parts.append(f"### 参考 {i}: {ex.get('title', '')}")
            parts.append(ex.get("content_text", "")[:500])
        parts.append("")

    parts.append(
        f"---\n"
        f"综合以上 {len(all_entries[:5])} 篇素材" +
        (f"和 {len(bundle.rag_results)} 条补充参考，" if bundle.rag_results else "，") +
        f"创作一篇 {char_limit} 字以内的 {platform_name} 原创内容。\n\n"
        f"重要：动笔前先按照创作质量清单的 7 个维度逐一思考你的策略，然后刻意执行。\n"
        f"要求：不要复述或罗列摘要，要形成你自己的洞察和叙事。标注信息来源。"
    )

    return "\n".join(parts)


def _build_style_driven_user_message(bundle, platform: str, platform_config: dict) -> str:
    """构建风格驱动模式的 user message。"""
    platform_name = PLATFORM_NAMES_CN.get(platform, platform)
    char_limit = platform_config["char_limit"]

    parts = [
        f"请为 {platform_name} 生成一篇「AI布道」风格的内容。\n",
        f"## 话题\n{bundle.topic or 'AI/Agent 领域前沿趋势'}\n",
    ]

    # 主条目（如果有标题，作为话题参考）
    if bundle.primary_entry:
        entry = bundle.primary_entry
        parts.append(f"## 参考条目\n**标题**：{entry.get('title', '')}")
        tags = entry.get("tags", "")
        if tags:
            parts.append(f"**标签**：{tags}")
        summary = entry.get("summary_markdown", "") or ""
        if summary:
            parts.append(f"\n{summary}")
        parts.append("")

    # 风格参考（核心）
    if bundle.style_examples:
        parts.append(f"## 风格参考（请模仿以下 {len(bundle.style_examples)} 篇作品的语气和节奏）")
        for i, ex in enumerate(bundle.style_examples[:3], 1):
            score = ex.get("composite", "")
            score_str = f" (评分: {score:.1f})" if score else ""
            parts.append(f"### 参考 {i}: {ex.get('title', '')}{score_str}")
            parts.append(ex.get("content_text", "")[:800])
        parts.append("")

    parts.append(
        f"## 重要提示\n"
        f"知识库中没有足够的相关内容，请基于你的知识和理解创作。\n"
        f"请模仿上述风格参考的语气、节奏和深度，保持创作风格的一致性。\n"
        f"\n---\n请生成 {char_limit} 字以内的 {platform_name} 内容。只输出正文内容。"
    )

    return "\n".join(parts)
