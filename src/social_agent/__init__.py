"""AI布道社媒内容生成 Agent。
基于 OmniVault 知识库的 AI 总结，生成全平台社媒内容。
"""
from .store import DraftStore, ContentDraft
from .generator import generate_for_platforms
from .routes import register_routes
