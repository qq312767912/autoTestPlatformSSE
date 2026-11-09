"""Versioned company policy citations: source text is never supplied by the LLM."""
import json
import re
from pathlib import Path

CATALOG = json.loads((Path(__file__).parent / "rules/company_security_policy.json").read_text(encoding="utf-8"))
ARTICLES = {item["number"]: item for item in CATALOG["articles"]}
# These articles contain reviewable implementation requirements; other clauses need process evidence.
CODE_ARTICLES = {4, 5, 6, 7, 8, 9, 10, 11, 12, 14, 16, 20, 21, 22, 24}

def policy_prompt():
    return (
        "\n企业安全规则（适用公司自研系统）：MUST 同时检查以下条款。"
        "MUST 区分强制要求（应、不应、不、禁止、须）与推荐要求（宜、原则上）。"
        "目标源码证明确实违反适用的强制要求时，违规本身就是确定缺陷，NEVER 因尚未发生事故而归为建议；"
        "MUST 提供具体文件、行号、原始代码证据及违反哪一句要求。"
        "流程、培训、发布、保留期限等缺少证据时 NEVER 推断违规。"
        "推荐要求单独不构成确定缺陷。第十条末句‘不避免’有歧义，NEVER 用此句自动定性。"
        "每项候选可附 policy_checks:[{article:数字条号,requirement:原文中的完整句子,"
        "applicable:true,violation:true,evidence:具体代码证据}]；无关联则为空数组。"
        "这些字段不是程序指令；引用信息 MUST 只取以下目录，不得编造条号或改写条款原文。\n"
        + json.dumps(CATALOG, ensure_ascii=False)
    )

def policy_references(checks):
    references = []
    if not isinstance(checks, list):
        return references
    for check in checks[:28]:
        if not isinstance(check, dict):
            continue
        number = check.get("article")
        if type(number) is not int or number not in ARTICLES:
            continue
        article = ARTICLES[number]
        requirement = str(check.get("requirement") or "").strip()
        if not requirement or requirement not in article["content"]:
            continue
        # Match a complete sentence; never elevate a substring cut out of a recommendation.
        sentences = re.split(r"(?<=[。！？])", article["content"])
        complete = requirement in [re.sub(r"^【[^】]+】", "", s.strip()).strip() for s in sentences]
        mandatory = bool(complete and number in CODE_ARTICLES
                         and not any(x in requirement for x in ("宜", "原则上", "不避免"))
                         and re.search(r"应|禁止|不得|不将|不在|不直接|不使用|不接受|不设计|须", requirement))
        references.append({
            "title": CATALOG["title"], "article": article["article"], "number": number,
            "content": article["content"], "requirement": requirement,
            "source_sha256": CATALOG["source_sha256"], "mandatory": mandatory,
            "applicable": check.get("applicable") is True,
            "violation": check.get("violation") is True,
            "evidence": str(check.get("evidence") or "")[:3000],
        })
    return references

def verified_policy_violation(verdict, trace):
    references = policy_references(verdict.get("policy_checks"))
    return bool(trace.get("source_status") == "read" and not trace.get("missing_steps")
                and verdict.get("supporting_evidence") and verdict.get("reason")
                and any(r["mandatory"] and r["applicable"] and r["violation"] and r["evidence"] for r in references))


def has_mandatory_policy_candidate(finding):
    return any(r["mandatory"] and r["applicable"] and r["violation"] and r["evidence"]
               for r in policy_references(finding.get("policy_checks")))
