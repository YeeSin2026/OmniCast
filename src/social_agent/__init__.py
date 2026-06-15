"""社媒内容生成 Agent。
基于 OmniVault 知识库的内容总结，生成全平台社媒内容。
支持文案对标功能：分析参考文案的 7 维写作技法，结合用户资料对标创作。
"""
from .store import DraftStore, ContentDraft
from .generator import generate_for_platforms, generate_benchmarked_copy
from .routes import register_routes
