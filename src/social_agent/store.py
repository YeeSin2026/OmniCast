"""草稿存储 — content_drafts + content_scores 表，独立数据库。"""

import json
import logging
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .. import settings as config

logger = logging.getLogger(__name__)


@dataclass
class ContentDraft:
    knowledge_id: int = 0
    platform: str = ""
    title: str = ""
    content_text: str = ""
    status: str = "draft"
    tone_variant: str = "standard"
    version: int = 1
    id: Optional[int] = None
    created_at: str = ""
    updated_at: str = ""
    published_at: Optional[str] = None
    publish_url: str = ""
    metadata_json: str = "{}"


class DraftStore:
    def __init__(self, db_path: str = ""):
        self.db_path = db_path or config.DB_PATH
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_table()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_table(self):
        conn = self._get_conn()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS content_drafts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    knowledge_id INTEGER NOT NULL,
                    platform TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    content_text TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'draft',
                    tone_variant TEXT NOT NULL DEFAULT 'standard',
                    version INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    published_at TEXT,
                    publish_url TEXT NOT NULL DEFAULT '',
                    deleted_at TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(knowledge_id, platform, version)
                )
            """)
            # 迁移：为旧数据库添加 deleted_at 列
            try:
                conn.execute("ALTER TABLE content_drafts ADD COLUMN deleted_at TEXT")
            except Exception:
                pass  # 列已存在
            conn.execute("CREATE INDEX IF NOT EXISTS idx_drafts_knowledge ON content_drafts(knowledge_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_drafts_status ON content_drafts(status)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS content_scores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    draft_id INTEGER NOT NULL UNIQUE,
                    er INTEGER NOT NULL DEFAULT 0,
                    sr INTEGER NOT NULL DEFAULT 0,
                    hp INTEGER NOT NULL DEFAULT 0,
                    ql INTEGER NOT NULL DEFAULT 0,
                    na INTEGER NOT NULL DEFAULT 0,
                    ab INTEGER NOT NULL DEFAULT 0,
                    ts INTEGER NOT NULL DEFAULT 0,
                    composite REAL NOT NULL DEFAULT 0,
                    one_liner TEXT NOT NULL DEFAULT '',
                    prediction_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (draft_id) REFERENCES content_drafts(id) ON DELETE CASCADE
                )
            """)
            conn.commit()
            logger.info("OmniCast 数据库初始化完成")
        finally:
            conn.close()

    def save(self, draft: ContentDraft) -> int:
        now = datetime.now(timezone.utc).isoformat()
        if not draft.created_at:
            draft.created_at = now
        draft.updated_at = now
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT MAX(version) FROM content_drafts WHERE knowledge_id=? AND platform=?",
                (draft.knowledge_id, draft.platform),
            ).fetchone()
            latest = (row[0] or 0)
            if latest > 0 and draft.version <= latest:
                draft.version = latest + 1
            cursor = conn.execute(
                """INSERT INTO content_drafts
                   (knowledge_id, platform, title, content_text, status, tone_variant,
                    version, created_at, updated_at, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (draft.knowledge_id, draft.platform, draft.title, draft.content_text,
                 draft.status, draft.tone_variant, draft.version, draft.created_at,
                 draft.updated_at, draft.metadata_json),
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def get_by_id(self, draft_id: int) -> Optional[dict]:
        conn = self._get_conn()
        try:
            row = conn.execute("SELECT * FROM content_drafts WHERE id=?", (draft_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def list_by_knowledge(self, knowledge_id: int) -> list[dict]:
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM content_drafts WHERE knowledge_id=? ORDER BY platform, version DESC",
                (knowledge_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def list_recent(self, limit: int = 20, status: Optional[str] = None, platform: Optional[str] = None) -> list[dict]:
        conn = self._get_conn()
        try:
            conditions = ["d.status != 'trash'"]  # 默认排除回收站
            params = []
            if status:
                conditions.append("d.status=?")
                params.append(status)
            if platform:
                conditions.append("d.platform=?")
                params.append(platform)
            where = " AND ".join(conditions)
            rows = conn.execute(
                f"""SELECT d.*
                    FROM content_drafts d
                    WHERE {where}
                    ORDER BY d.updated_at DESC LIMIT ?""",
                params + [limit],
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def list_high_scored(self, platform: str = "", limit: int = 5) -> list[dict]:
        """获取高分历史草稿，用于风格参考。

        Args:
            platform: 平台过滤（为空则不限平台）
            limit: 返回数量

        Returns:
            草稿列表（含评分），按 composite 降序
        """
        conn = self._get_conn()
        try:
            if platform:
                rows = conn.execute(
                    """SELECT d.*, s.composite, s.one_liner
                       FROM content_drafts d
                       JOIN content_scores s ON s.draft_id = d.id
                       WHERE d.platform = ? AND d.content_text != ''
                       ORDER BY s.composite DESC LIMIT ?""",
                    (platform, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT d.*, s.composite, s.one_liner
                       FROM content_drafts d
                       JOIN content_scores s ON s.draft_id = d.id
                       WHERE d.content_text != ''
                       ORDER BY s.composite DESC LIMIT ?""",
                    (limit,),
                ).fetchall()

            if not rows:
                # 回退：没有评分数据时返回最近的草稿
                if platform:
                    rows = conn.execute(
                        """SELECT d.*, NULL as composite, '' as one_liner
                           FROM content_drafts d
                           WHERE d.platform = ? AND d.content_text != ''
                           ORDER BY d.updated_at DESC LIMIT ?""",
                        (platform, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """SELECT d.*, NULL as composite, '' as one_liner
                           FROM content_drafts d
                           WHERE d.content_text != ''
                           ORDER BY d.updated_at DESC LIMIT ?""",
                        (limit,),
                    ).fetchall()

            return [dict(r) for r in rows]
        finally:
            conn.close()

    def record_publish(
        self,
        draft_id: int,
        publish_url: str = "",
        metrics: Optional[dict] = None,
    ) -> bool:
        """记录发布结果和表现数据。

        Args:
            draft_id: 草稿 ID
            publish_url: 发布后的公开链接
            metrics: 表现数据 {views, likes, shares, comments, platform_data}

        Returns:
            是否成功
        """
        now = datetime.now(timezone.utc).isoformat()
        metrics_json = json.dumps(metrics or {}, ensure_ascii=False)
        conn = self._get_conn()
        try:
            conn.execute(
                """UPDATE content_drafts
                   SET status='published', published_at=?, publish_url=?,
                       metadata_json = json_patch(metadata_json, ?),
                       updated_at=?
                   WHERE id=?""",
                (now, publish_url, json.dumps({"publish_metrics": metrics or {}}), now, draft_id),
            )
            conn.commit()
            logger.info(f"发布记录已保存: draft_id={draft_id}, metrics={metrics}")
            return True
        except Exception as e:
            # json_patch 可能不支持，fallback 直接覆盖
            try:
                draft = self.get_by_id(draft_id)
                if draft:
                    existing_meta = {}
                    try:
                        existing_meta = json.loads(draft.get("metadata_json", "{}"))
                    except (json.JSONDecodeError, TypeError):
                        pass
                    existing_meta["publish_metrics"] = metrics or {}
                    conn.execute(
                        """UPDATE content_drafts
                           SET status='published', published_at=?, publish_url=?,
                               metadata_json=?, updated_at=?
                           WHERE id=?""",
                        (now, publish_url, json.dumps(existing_meta, ensure_ascii=False), now, draft_id),
                    )
                    conn.commit()
                    return True
            except Exception as e2:
                logger.error(f"记录发布失败: {e2}")
            return False
        finally:
            conn.close()

    def get_performance_summary(self, platform: str = "", limit: int = 10) -> list[dict]:
        """获取已发布内容的性能摘要，用于优化未来创作。

        Returns:
            [{draft_id, platform, title, content_text, metrics, composite_score}, ...]
            按 composite 降序排列
        """
        conn = self._get_conn()
        try:
            if platform:
                rows = conn.execute(
                    """SELECT d.id, d.platform, d.title, d.content_text, d.metadata_json,
                              s.composite, s.one_liner, s.er, s.sr, s.hp, s.ql, s.na, s.ab, s.ts
                       FROM content_drafts d
                       LEFT JOIN content_scores s ON s.draft_id = d.id
                       WHERE d.status = 'published' AND d.platform = ?
                       ORDER BY s.composite DESC NULLS LAST LIMIT ?""",
                    (platform, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT d.id, d.platform, d.title, d.content_text, d.metadata_json,
                              s.composite, s.one_liner, s.er, s.sr, s.hp, s.ql, s.na, s.ab, s.ts
                       FROM content_drafts d
                       LEFT JOIN content_scores s ON s.draft_id = d.id
                       WHERE d.status = 'published'
                       ORDER BY s.composite DESC NULLS LAST LIMIT ?""",
                    (limit,),
                ).fetchall()

            results = []
            for row in rows:
                d = dict(row)
                metrics = {}
                try:
                    meta = json.loads(d.get("metadata_json", "{}"))
                    metrics = meta.get("publish_metrics", {})
                except (json.JSONDecodeError, TypeError):
                    pass
                d["publish_metrics"] = metrics
                results.append(d)
            return results
        finally:
            conn.close()

    def update_status(self, draft_id: int, status: str, content_text: Optional[str] = None) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        try:
            if content_text is not None:
                conn.execute(
                    "UPDATE content_drafts SET status=?, content_text=?, updated_at=? WHERE id=?",
                    (status, content_text, now, draft_id),
                )
            else:
                conn.execute(
                    "UPDATE content_drafts SET status=?, updated_at=? WHERE id=?",
                    (status, now, draft_id),
                )
            conn.commit()
            return conn.total_changes > 0
        finally:
            conn.close()

    def delete_by_id(self, draft_id: int) -> bool:
        """软删除：移入回收站。"""
        conn = self._get_conn()
        try:
            now = datetime.now(timezone.utc).isoformat()
            cur = conn.execute(
                "UPDATE content_drafts SET status='trash', deleted_at=? WHERE id=?",
                (now, draft_id),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def restore_from_trash(self, draft_id: int) -> bool:
        conn = self._get_conn()
        try:
            cur = conn.execute(
                "UPDATE content_drafts SET status='draft', deleted_at=NULL WHERE id=? AND status='trash'",
                (draft_id,),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def permanent_delete(self, draft_id: int) -> bool:
        conn = self._get_conn()
        try:
            cur = conn.execute("DELETE FROM content_drafts WHERE id=? AND status='trash'", (draft_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def clean_expired_trash(self, days: int) -> int:
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT id, deleted_at FROM content_drafts WHERE status='trash'"
            ).fetchall()
            deleted = 0
            now = datetime.now(timezone.utc)
            for row in rows:
                if row["deleted_at"]:
                    try:
                        dt = datetime.fromisoformat(row["deleted_at"])
                        if (now - dt).days >= days:
                            conn.execute("DELETE FROM content_drafts WHERE id=?", (row["id"],))
                            deleted += 1
                    except Exception:
                        pass
            conn.commit()
            return deleted
        finally:
            conn.close()

    def empty_trash(self) -> int:
        conn = self._get_conn()
        try:
            cur = conn.execute("DELETE FROM content_drafts WHERE status='trash'")
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def list_trash(self, limit: int = 50) -> list[dict]:
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM content_drafts WHERE status='trash' ORDER BY deleted_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def save_score(self, draft_id: int, scores: dict, prediction: dict = None):
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        try:
            existing = conn.execute("SELECT prediction_json FROM content_scores WHERE draft_id=?", (draft_id,)).fetchone()
            if existing and existing["prediction_json"] and existing["prediction_json"] != "{}":
                conn.execute(
                    """UPDATE content_scores SET er=?, sr=?, hp=?, ql=?, na=?, ab=?, ts=?, composite=?, one_liner=? WHERE draft_id=?""",
                    (scores.get("ER", 0), scores.get("SR", 0), scores.get("HP", 0),
                     scores.get("QL", 0), scores.get("NA", 0), scores.get("AB", 0),
                     scores.get("TS", 0), scores.get("composite", 0),
                     scores.get("one_liner", ""), draft_id),
                )
            else:
                pred_json = json.dumps(prediction or {}, ensure_ascii=False)
                conn.execute(
                    """INSERT OR REPLACE INTO content_scores
                       (draft_id, er, sr, hp, ql, na, ab, ts, composite, one_liner, prediction_json, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (draft_id, scores.get("ER", 0), scores.get("SR", 0), scores.get("HP", 0),
                     scores.get("QL", 0), scores.get("NA", 0), scores.get("AB", 0),
                     scores.get("TS", 0), scores.get("composite", 0),
                     scores.get("one_liner", ""), pred_json, now),
                )
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"保存评分失败: {e}")
            return False
        finally:
            conn.close()

    def get_score(self, draft_id: int) -> Optional[dict]:
        conn = self._get_conn()
        try:
            row = conn.execute("SELECT * FROM content_scores WHERE draft_id=?", (draft_id,)).fetchone()
            if not row:
                return None
            d = dict(row)
            try:
                d["prediction"] = json.loads(d.get("prediction_json", "{}"))
            except (json.JSONDecodeError, TypeError):
                d["prediction"] = {}
            return d
        finally:
            conn.close()
