"""
知识库操作层 - SQLite 存储
表结构：entities / relations / entity_mentions / daily_reports
"""

from typing import Optional
import json
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "knowledge.db"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    """建表（幂等）"""
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS entities (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            type        TEXT NOT NULL,          -- concept / tool / person / company / event
            name        TEXT NOT NULL UNIQUE,
            aliases     TEXT DEFAULT '[]',      -- JSON array
            summary     TEXT DEFAULT '',        -- AI 维护的简介，随时间归并更新
            first_seen  TEXT NOT NULL,
            last_updated TEXT NOT NULL,
            source_group TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS relations (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            from_id         INTEGER REFERENCES entities(id),
            to_id           INTEGER REFERENCES entities(id),
            relation_type   TEXT NOT NULL,  -- is_a / part_of / made_by / used_for / mentioned_with / said_by
            evidence        TEXT DEFAULT '',
            created_at      TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS entity_mentions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id   INTEGER REFERENCES entities(id),
            report_date TEXT NOT NULL,
            group_name  TEXT DEFAULT '',
            speaker     TEXT DEFAULT '',
            context     TEXT DEFAULT '',
            created_at  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS daily_reports (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            group_name  TEXT NOT NULL,
            report_date TEXT NOT NULL,
            ai_report   TEXT NOT NULL,
            merged      INTEGER DEFAULT 0,     -- 0=未归并, 1=已归并
            created_at  TEXT NOT NULL,
            UNIQUE(group_name, report_date)
        );

        CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name);
        CREATE INDEX IF NOT EXISTS idx_mentions_entity ON entity_mentions(entity_id);
        CREATE INDEX IF NOT EXISTS idx_mentions_date ON entity_mentions(report_date);
        CREATE INDEX IF NOT EXISTS idx_relations_from ON relations(from_id);
        CREATE INDEX IF NOT EXISTS idx_reports_date ON daily_reports(report_date);
        """)


# ── 实体 CRUD ──────────────────────────────────

def upsert_entity(name: str, type_: str, summary: str = "", aliases: list = None,
                  source_group: str = "", date: str = None) -> int:
    now = date or datetime.now().strftime("%Y-%m-%d")
    aliases_json = json.dumps(aliases or [], ensure_ascii=False)
    with get_conn() as conn:
        existing = conn.execute("SELECT id, aliases FROM entities WHERE name=?", (name,)).fetchone()
        if existing:
            # 合并 aliases
            old_aliases = json.loads(existing["aliases"] or "[]")
            new_aliases = list(set(old_aliases + (aliases or [])))
            conn.execute(
                "UPDATE entities SET last_updated=?, aliases=?, summary=CASE WHEN ?!='' THEN ? ELSE summary END WHERE id=?",
                (now, json.dumps(new_aliases, ensure_ascii=False), summary, summary, existing["id"])
            )
            return existing["id"]
        else:
            cur = conn.execute(
                "INSERT INTO entities(type,name,aliases,summary,first_seen,last_updated,source_group) VALUES(?,?,?,?,?,?,?)",
                (type_, name, aliases_json, summary, now, now, source_group)
            )
            return cur.lastrowid


def update_entity_summary(entity_id: int, summary: str):
    now = datetime.now().strftime("%Y-%m-%d")
    with get_conn() as conn:
        conn.execute("UPDATE entities SET summary=?, last_updated=? WHERE id=?",
                     (summary, now, entity_id))


def get_entity(entity_id: int) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM entities WHERE id=?", (entity_id,)).fetchone()
        return dict(row) if row else None


def search_entities(query: str, limit: int = 20) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM entities WHERE name LIKE ? OR aliases LIKE ? OR summary LIKE ? ORDER BY last_updated DESC LIMIT ?",
            (f"%{query}%", f"%{query}%", f"%{query}%", limit)
        ).fetchall()
        return [dict(r) for r in rows]


def list_entities(type_: str = None, limit: int = 100) -> list[dict]:
    with get_conn() as conn:
        if type_:
            rows = conn.execute(
                "SELECT * FROM entities WHERE type=? ORDER BY last_updated DESC LIMIT ?",
                (type_, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM entities ORDER BY last_updated DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


# ── 关系 CRUD ──────────────────────────────────

def add_relation(from_id: int, to_id: int, relation_type: str, evidence: str = "") -> int:
    now = datetime.now().strftime("%Y-%m-%d")
    with get_conn() as conn:
        # 去重：同 from/to/type 只保留一条
        existing = conn.execute(
            "SELECT id FROM relations WHERE from_id=? AND to_id=? AND relation_type=?",
            (from_id, to_id, relation_type)
        ).fetchone()
        if existing:
            return existing["id"]
        cur = conn.execute(
            "INSERT INTO relations(from_id,to_id,relation_type,evidence,created_at) VALUES(?,?,?,?,?)",
            (from_id, to_id, relation_type, evidence, now)
        )
        return cur.lastrowid


def get_relations(entity_id: int) -> list[dict]:
    """获取某实体的所有关系（包含关联实体名称）"""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT r.*, e1.name as from_name, e1.type as from_type,
                          e2.name as to_name,   e2.type as to_type
            FROM relations r
            JOIN entities e1 ON r.from_id = e1.id
            JOIN entities e2 ON r.to_id   = e2.id
            WHERE r.from_id=? OR r.to_id=?
        """, (entity_id, entity_id)).fetchall()
        return [dict(r) for r in rows]


# ── 提及记录 ──────────────────────────────────

def add_mention(entity_id: int, report_date: str, group_name: str = "",
                speaker: str = "", context: str = ""):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO entity_mentions(entity_id,report_date,group_name,speaker,context,created_at) VALUES(?,?,?,?,?,?)",
            (entity_id, report_date, group_name, speaker, context, now)
        )


def get_mentions(entity_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM entity_mentions WHERE entity_id=? ORDER BY report_date DESC",
            (entity_id,)
        ).fetchall()
        return [dict(r) for r in rows]


# ── 每日报告 ──────────────────────────────────

def save_report(group_name: str, report_date: str, ai_report: str):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO daily_reports(group_name,report_date,ai_report,merged,created_at) VALUES(?,?,?,0,?)",
            (group_name, report_date, ai_report, now)
        )


def get_unmerged_reports() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM daily_reports WHERE merged=0 ORDER BY report_date ASC"
        ).fetchall()
        return [dict(r) for r in rows]


def mark_report_merged(report_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE daily_reports SET merged=1 WHERE id=?", (report_id,))


# ── 图谱数据（供前端可视化） ──────────────────────────────────

def get_graph(limit: int = 200) -> dict:
    """返回 {nodes: [...], edges: [...]}，供 vis.js 渲染"""
    with get_conn() as conn:
        entities = conn.execute(
            "SELECT id, name, type, summary, first_seen, last_updated FROM entities ORDER BY last_updated DESC LIMIT ?",
            (limit,)
        ).fetchall()

        entity_ids = [e["id"] for e in entities]
        if not entity_ids:
            return {"nodes": [], "edges": []}

        placeholders = ",".join("?" * len(entity_ids))
        relations = conn.execute(
            f"SELECT * FROM relations WHERE from_id IN ({placeholders}) AND to_id IN ({placeholders})",
            entity_ids + entity_ids
        ).fetchall()

    type_colors = {
        "concept": "#5b9cf6",
        "tool":    "#34c759",
        "person":  "#ff9f0a",
        "company": "#bf5af2",
        "event":   "#ff453a",
    }
    type_labels = {
        "concept": "概念", "tool": "工具", "person": "人物",
        "company": "公司", "event": "事件",
    }

    nodes = [
        {
            "id": e["id"],
            "label": e["name"],
            "title": (e["summary"] or "")[:200],
            "group": e["type"],
            "color": type_colors.get(e["type"], "#aaa"),
            "typeLabel": type_labels.get(e["type"], e["type"]),
            "first_seen": e["first_seen"],
            "last_updated": e["last_updated"],
        }
        for e in entities
    ]
    edges = [
        {
            "id": r["id"],
            "from": r["from_id"],
            "to": r["to_id"],
            "label": r["relation_type"],
            "title": r["evidence"],
        }
        for r in relations
    ]
    return {"nodes": nodes, "edges": edges}


# ── Obsidian 导出 ──────────────────────────────────

RELATION_ZH = {
    "is_a": "是一种",
    "part_of": "属于",
    "made_by": "由...开发",
    "used_for": "用于",
    "mentioned_with": "关联",
    "said_by": "由...提出",
}

TYPE_ZH = {
    "concept": "概念",
    "tool": "工具",
    "person": "人物",
    "company": "公司",
    "event": "事件",
}


def export_to_obsidian(vault_dir: str) -> int:
    """
    将知识图谱实体导出为 Obsidian Markdown 文件（双链格式）。
    每个实体一个 .md 文件，relations 生成 [[双链]]。
    返回导出文件数量。
    """
    import os
    out = Path(os.path.expanduser(vault_dir)) / "AI知识图谱"
    out.mkdir(parents=True, exist_ok=True)

    entities = list_entities(limit=2000)
    id_to_name = {e["id"]: e["name"] for e in entities}
    count = 0

    for e in entities:
        relations = get_relations(e["id"])
        mentions = get_mentions(e["id"])
        aliases = json.loads(e.get("aliases") or "[]")

        # frontmatter
        lines = [
            "---",
            f"type: {e['type']}",
            f"type_zh: {TYPE_ZH.get(e['type'], e['type'])}",
        ]
        if aliases:
            lines.append(f"aliases: [{', '.join(aliases)}]")
        lines += [
            f"first_seen: {e['first_seen']}",
            f"last_updated: {e['last_updated']}",
            "---",
            "",
            f"# {e['name']}",
            "",
            e.get("summary") or "（暂无简介）",
            "",
        ]

        # 关系区块
        out_rels = [r for r in relations if r["from_id"] == e["id"]]
        in_rels  = [r for r in relations if r["to_id"]   == e["id"]]
        if out_rels:
            lines.append("## 关联")
            for r in out_rels:
                target = id_to_name.get(r["to_id"], str(r["to_id"]))
                rel_zh = RELATION_ZH.get(r["relation_type"], r["relation_type"])
                ev = f"（{r['evidence']}）" if r.get("evidence") else ""
                lines.append(f"- {rel_zh} [[{target}]]{ev}")
            lines.append("")
        if in_rels:
            lines.append("## 被提及关联")
            for r in in_rels:
                src = id_to_name.get(r["from_id"], str(r["from_id"]))
                rel_zh = RELATION_ZH.get(r["relation_type"], r["relation_type"])
                lines.append(f"- [[{src}]] {rel_zh} 此条目")
            lines.append("")

        # 提及时间线
        if mentions:
            lines.append("## 提及时间线")
            for m in mentions[:30]:  # 最多30条
                speaker = f"**{m['speaker']}**：" if m.get("speaker") else ""
                lines.append(f"- `{m['report_date']}` {speaker}{m.get('context','')}")
            lines.append("")

        # 安全文件名
        safe_name = "".join(c if c.isalnum() or c in " _-·." else "_" for c in e["name"])
        file_path = out / f"{safe_name}.md"
        file_path.write_text("\n".join(lines), encoding="utf-8")
        count += 1

    return count


# ── 联系人画像 ──────────────────────────────────

def init_conversation_tables():
    """建立对话相关表（幂等）"""
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS contact_profiles (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            company     TEXT DEFAULT '',
            role        TEXT DEFAULT '',
            summary     TEXT DEFAULT '',
            tags        TEXT DEFAULT '[]',
            first_met   TEXT NOT NULL,
            last_met    TEXT NOT NULL,
            meet_count  INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS conversations (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_id   INTEGER REFERENCES contact_profiles(id),
            date         TEXT NOT NULL,
            duration_sec INTEGER DEFAULT 0,
            transcript   TEXT DEFAULT '',
            summary      TEXT DEFAULT '',
            key_points   TEXT DEFAULT '{}',
            audio_path   TEXT DEFAULT '',
            created_at   TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_contacts_name ON contact_profiles(name);
        CREATE INDEX IF NOT EXISTS idx_contacts_company ON contact_profiles(company);
        CREATE INDEX IF NOT EXISTS idx_conversations_contact ON conversations(contact_id);
        CREATE INDEX IF NOT EXISTS idx_conversations_date ON conversations(date);
        """)

    # 增量迁移：为旧版数据库添加新字段（不破坏已有数据）
    with get_conn() as conn:
        existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(conversations)")}
        for col, col_def in [
            ("scene",          "TEXT DEFAULT ''"),
            ("sentiment",      "TEXT DEFAULT ''"),
            ("follow_up_date", "TEXT DEFAULT ''"),
            ("commitments",    "TEXT DEFAULT ''"),
        ]:
            if col not in existing_cols:
                conn.execute(f"ALTER TABLE conversations ADD COLUMN {col} {col_def}")


def get_or_create_contact(name: str, company: str = "") -> int:
    """模糊匹配已有联系人（同名+公司），没有则新建。返回 contact_id"""
    now = datetime.now().strftime("%Y-%m-%d")
    with get_conn() as conn:
        # 精确匹配 name
        row = conn.execute(
            "SELECT id FROM contact_profiles WHERE name=? AND company=?",
            (name, company)
        ).fetchone()
        if not row:
            # 仅匹配名字（公司可能不一样）
            row = conn.execute(
                "SELECT id FROM contact_profiles WHERE name=?", (name,)
            ).fetchone()
        if row:
            return row["id"]
        cur = conn.execute(
            "INSERT INTO contact_profiles(name,company,summary,tags,first_met,last_met,meet_count) VALUES(?,?,?,?,?,?,0)",
            (name, company, "", "[]", now, now)
        )
        return cur.lastrowid


def update_contact_after_meeting(contact_id: int, new_summary: str, role: str = "", tags: list = None):
    now = datetime.now().strftime("%Y-%m-%d")
    with get_conn() as conn:
        cur_row = conn.execute("SELECT meet_count, tags FROM contact_profiles WHERE id=?", (contact_id,)).fetchone()
        count = (cur_row["meet_count"] or 0) + 1 if cur_row else 1
        merged_tags = list(set(json.loads(cur_row["tags"] or "[]") + (tags or []))) if cur_row else (tags or [])
        conn.execute(
            "UPDATE contact_profiles SET summary=?, role=CASE WHEN ?!='' THEN ? ELSE role END, tags=?, last_met=?, meet_count=? WHERE id=?",
            (new_summary, role, role, json.dumps(merged_tags, ensure_ascii=False), now, count, contact_id)
        )


def list_contacts(company: str = None, limit: int = 100) -> list[dict]:
    with get_conn() as conn:
        if company:
            rows = conn.execute(
                "SELECT * FROM contact_profiles WHERE company=? ORDER BY last_met DESC LIMIT ?",
                (company, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM contact_profiles ORDER BY last_met DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


def get_contact(contact_id: int) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM contact_profiles WHERE id=?", (contact_id,)).fetchone()
        return dict(row) if row else None


def search_contacts(query: str, limit: int = 20) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM contact_profiles WHERE name LIKE ? OR company LIKE ? OR summary LIKE ? ORDER BY last_met DESC LIMIT ?",
            (f"%{query}%", f"%{query}%", f"%{query}%", limit)
        ).fetchall()
        return [dict(r) for r in rows]


def save_conversation(contact_id: Optional[int], date: str, transcript: str,
                      summary: str, key_points: dict, duration_sec: int = 0,
                      audio_path: str = "") -> int:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO conversations
               (contact_id,date,duration_sec,transcript,summary,key_points,audio_path,
                scene,sentiment,follow_up_date,commitments,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (contact_id, date, duration_sec, transcript, summary,
             json.dumps(key_points, ensure_ascii=False), audio_path,
             key_points.get("scene", ""),
             key_points.get("sentiment", ""),
             key_points.get("follow_up_date", ""),
             key_points.get("commitments", ""),
             now)
        )
        return cur.lastrowid


def list_conversations(contact_id: int = None, limit: int = 50) -> list[dict]:
    with get_conn() as conn:
        if contact_id:
            rows = conn.execute(
                "SELECT * FROM conversations WHERE contact_id=? ORDER BY date DESC, created_at DESC LIMIT ?",
                (contact_id, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM conversations ORDER BY date DESC, created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


def get_conversation(conv_id: int) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM conversations WHERE id=?", (conv_id,)).fetchone()
        return dict(row) if row else None


def update_conversation_contact(conv_id: int, contact_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE conversations SET contact_id=? WHERE id=?", (contact_id, conv_id))


def delete_conversation(conv_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM conversations WHERE id=?", (conv_id,))


def merge_conversations(conv_ids: list) -> int:
    """
    合并多条对话：按 created_at 排序，拼接 transcript，累加 duration_sec，
    删除原始记录，写入新合并记录（summary/key_points 由调用方重新提炼）。
    返回新 conv_id。
    """
    convs = [get_conversation(i) for i in conv_ids]
    convs = [c for c in convs if c]  # 过滤不存在的
    if not convs:
        raise ValueError("没有找到指定的对话")
    convs.sort(key=lambda c: c.get('created_at', ''))
    first = convs[0]
    merged_transcript = "\n\n--- 间隔 ---\n\n".join(c.get('transcript', '') for c in convs)
    merged_duration = sum(c.get('duration_sec', 0) for c in convs)
    with get_conn() as conn:
        for c in convs:
            conn.execute("DELETE FROM conversations WHERE id=?", (c['id'],))
    return save_conversation(
        contact_id=first.get('contact_id'),
        date=first.get('date', datetime.now().strftime('%Y-%m-%d')),
        transcript=merged_transcript,
        summary='',
        key_points={},
        duration_sec=merged_duration,
        audio_path='',
    )


# 初始化
init_db()
init_conversation_tables()
