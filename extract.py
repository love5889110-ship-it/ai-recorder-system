"""
实体抽取与知识归并
每次报告生成后调用 merge_report()，AI 从报告中抽取实体和关系，写入知识库
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config
import knowledge as kb
from wechat_digest import call_claude


EXTRACT_PROMPT = """你是一个知识图谱构建助手。请从以下群聊解读报告中抽取所有重要实体和关系。

报告日期：{date}
群组：{group}

---
{report}
---

请严格按照以下 JSON 格式输出，不要有任何其他文字：

{{
  "entities": [
    {{
      "name": "实体名称",
      "type": "concept|tool|person|company|event",
      "aliases": ["别名1", "别名2"],
      "summary": "一句话简介（20字以内）",
      "speakers": ["发言人1"]
    }}
  ],
  "relations": [
    {{
      "from": "实体A名称",
      "to": "实体B名称",
      "type": "is_a|part_of|made_by|used_for|mentioned_with|said_by",
      "evidence": "来自报告的简短依据（30字以内）"
    }}
  ],
  "mentions": [
    {{
      "entity": "实体名称",
      "speaker": "发言人昵称",
      "context": "原文片段（50字以内）"
    }}
  ]
}}

实体类型说明：
- concept：AI概念/技术术语（如 AI Agent、提示词工程、RAG）
- tool：软件工具/产品（如 Claude代码助手、Coze扣子、ChatGPT）
- person：群内发言人昵称（保留原始昵称）
- company：公司/机构（如 Anthropic、字节跳动）
- event：特定事件（如 gemma4发布、Sam Altman深调）

命名规则（非常重要）：
1. 实体 name 必须用中文或中文为主（如"Claude代码助手"不要写"Claude Code"）
2. 英文原名放入 aliases 数组（如 aliases: ["Claude Code"]）
3. 公司名用中文（"谷歌"不要写"Google"，"微软"不要写"Microsoft"）
4. 人名保留原始昵称，不要翻译
5. summary 必须是中文，不超过20字

只抽取报告中明确提到的内容，不要臆造。至少抽取5个实体。"""


MERGE_SUMMARY_PROMPT = """你是知识管理助手。以下是关于「{name}」的旧简介和新补充信息，请将两者合并为一段不超过80字的最新简介。

旧简介：{old_summary}

新信息：{new_info}

直接输出合并后的简介，不要有任何前缀或解释。"""


def extract_entities(report_text: str, report_date: str, group_name: str) -> dict:
    """调用 AI 从报告中抽取实体、关系、提及"""
    prompt = EXTRACT_PROMPT.format(
        date=report_date,
        group=group_name,
        report=report_text[:6000]  # 控制输入长度，留出足够输出空间
    )
    try:
        raw = call_claude(prompt, max_tokens=8000)
        # 提取 JSON 部分（防止 AI 输出多余文字）
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start == -1 or end == 0:
            return {"entities": [], "relations": [], "mentions": []}
        json_str = raw[start:end]
        # 修复常见格式问题：中文引号、尾部多余逗号
        import re
        json_str = json_str.replace("\u201c", '"').replace("\u201d", '"')
        json_str = re.sub(r',\s*([}\]])', r'\1', json_str)
        return json.loads(json_str)
    except Exception as e:
        print(f"[extract] 抽取失败: {e}")
        return {"entities": [], "relations": [], "mentions": []}


def merge_summary(entity_name: str, old_summary: str, new_info: str) -> str:
    """用 AI 将新旧简介合并"""
    if not old_summary:
        return new_info[:80]
    prompt = MERGE_SUMMARY_PROMPT.format(
        name=entity_name,
        old_summary=old_summary,
        new_info=new_info
    )
    try:
        return call_claude(prompt).strip()[:200]
    except Exception:
        return old_summary  # 失败时保留旧简介


def merge_report(report_date: str, group_name: str, ai_report: str,
                 log_fn=None) -> dict:
    """
    完整归并流程：
    1. 保存报告到 daily_reports
    2. AI 抽取实体/关系/提及
    3. 写入知识库（新建或更新）
    返回统计信息
    """
    def log(msg):
        print(msg)
        if log_fn:
            log_fn(msg)

    log(f"[知识归并] 开始处理 {report_date} / {group_name}")

    # 保存报告
    kb.save_report(group_name, report_date, ai_report)

    # AI 抽取
    log("[知识归并] 调用 AI 抽取实体...")
    extracted = extract_entities(ai_report, report_date, group_name)

    entities_data = extracted.get("entities", [])
    relations_data = extracted.get("relations", [])
    mentions_data = extracted.get("mentions", [])

    log(f"[知识归并] 抽取到 {len(entities_data)} 个实体，{len(relations_data)} 条关系，{len(mentions_data)} 条提及")

    # 写入实体
    name_to_id = {}
    new_count = 0
    updated_count = 0

    for e in entities_data:
        name = e.get("name", "").strip()
        if not name:
            continue
        existing = kb.search_entities(name)
        exact = next((x for x in existing if x["name"] == name), None)

        if exact and exact.get("summary") and e.get("summary"):
            # 合并简介
            merged = merge_summary(name, exact["summary"], e["summary"])
            entity_id = kb.upsert_entity(
                name=name, type_=e.get("type", "concept"),
                summary=merged, aliases=e.get("aliases", []),
                source_group=group_name, date=report_date
            )
            kb.update_entity_summary(entity_id, merged)
            updated_count += 1
        else:
            entity_id = kb.upsert_entity(
                name=name, type_=e.get("type", "concept"),
                summary=e.get("summary", ""), aliases=e.get("aliases", []),
                source_group=group_name, date=report_date
            )
            if not exact:
                new_count += 1

        name_to_id[name] = entity_id

    # 写入关系
    rel_count = 0
    for r in relations_data:
        from_name = r.get("from", "")
        to_name = r.get("to", "")
        if from_name not in name_to_id or to_name not in name_to_id:
            continue
        kb.add_relation(
            from_id=name_to_id[from_name],
            to_id=name_to_id[to_name],
            relation_type=r.get("type", "mentioned_with"),
            evidence=r.get("evidence", "")
        )
        rel_count += 1

    # 写入提及
    for m in mentions_data:
        entity_name = m.get("entity", "")
        if entity_name not in name_to_id:
            continue
        kb.add_mention(
            entity_id=name_to_id[entity_name],
            report_date=report_date,
            group_name=group_name,
            speaker=m.get("speaker", ""),
            context=m.get("context", "")
        )

    log(f"[知识归并] 完成：新增 {new_count} 个实体，更新 {updated_count} 个，写入 {rel_count} 条关系")
    return {
        "new_entities": new_count,
        "updated_entities": updated_count,
        "relations": rel_count,
        "mentions": len(mentions_data)
    }
