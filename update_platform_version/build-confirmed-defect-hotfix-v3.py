#!/usr/bin/env python3
import hashlib
import sys
from pathlib import Path

EXPECTED = "a10722971762e38cdb8419a5a3ab40a8bc88dfb59e90186b43dccdb76409455f"


def replace_once(text, old, new, label):
    if text.count(old) != 1:
        raise SystemExit(f"{label}: expected exactly one match, got {text.count(old)}")
    return text.replace(old, new, 1)


source_path, output_path = map(Path, sys.argv[1:3])
raw = source_path.read_bytes()
actual = hashlib.sha256(raw).hexdigest()
if actual != EXPECTED:
    raise SystemExit(f"unexpected source sha256: {actual}")
text = raw.decode("utf-8")

text = replace_once(text,
    '"每条风险必须给出可定位的文件和代码证据，并填写 disposition：confirmed 仅用于有明确触发链路和可验证错误结果的缺陷；needs_confirmation 用于需要业务规则、运行环境或调用方信息才能定性的风险；advisory 用于不影响正确性的改进建议。"\n'
    '        "同时填写 trigger（如何触发）、recommendation（开发如何修改或验证）；证据不足时不得使用 confirmed。"',
    '"每条风险必须给出可定位的文件和代码证据，并填写 disposition：confirmed 仅用于目标版本中仍存在、有明确触发链路和可验证错误结果的缺陷；needs_confirmation 用于需要业务规则、运行环境或调用方信息才能定性的风险；advisory 用于不影响正确性的改进建议。"\n'
    '        "不得把本次变更已经修复的旧问题、符合需求的内容/日期/链接更新、正常功能实现、正向效果说明或单纯回归验证点报告为缺陷；"\n'
    '        "仅能说明‘可能’‘建议验证’或‘若未同步将出错’时不得使用 confirmed。"\n'
    '        "同时填写 trigger（如何触发）、recommendation（开发如何修改或验证）；证据不足时不得使用 confirmed。"',
    "initial review prompt")

helper = '''def _has_direct_target_defect_evidence(finding):
    """识别无需业务推断即可由目标源码直接判定的缺陷，避免核验服务失败时漏报。"""
    confidence = float(finding.get("confidence") or 0)
    if confidence < 0.95 or not finding.get("evidence") or not finding.get("trigger"):
        return False
    text = " ".join(str(finding.get(field) or "") for field in ("change", "evidence", "trigger")).lower()
    if any(marker in text for marker in ("已移除", "改为环境变量", "改用环境变量", "不再硬编码", "修复后")):
        return False
    empty_implementation = (
        any(marker in text for marker in ("todo", "未实现", "空实现"))
        and any(marker in text for marker in ("直接返回", "空响应", "return new", "return null"))
    )
    plaintext_credential = (
        any(marker in text for marker in ("硬编码", "明文"))
        and any(marker in text for marker in ("accesskey", "access-key", "secretkey", "secret-key", "sessioncredentials", "凭证"))
        and any(marker in text for marker in ("配置", "代码", "赋值", "字符串", "sessioncredentials"))
    )
    return empty_implementation or plaintext_credential


'''
text = replace_once(text, "def _apply_verification_results(findings, verdicts):", helper + "def _apply_verification_results(findings, verdicts):", "helper insertion")

start = text.index("def _apply_verification_results(findings, verdicts):")
end = text.index("\ndef _related_source_context", start)
new_apply = '''def _apply_verification_results(findings, verdicts):
    """合并反证裁决；rejected 候选仅记统计，不交付给开发。"""
    allowed = {"confirmed", "needs_confirmation", "rejected", "advisory"}
    mapped = {str(item.get("key")): item for item in verdicts if str(item.get("verdict")) in allowed}
    active, rejected = [], []
    for finding in findings:
        item = dict(finding)
        verdict = mapped.get(str(item.get("key")), {})
        item["verification_reason"] = str(verdict.get("reason") or "")[:2000]
        item["counter_evidence"] = str(verdict.get("counter_evidence") or "")[:3000]
        supporting = verdict.get("supporting_evidence") or []
        if isinstance(supporting, str):
            supporting = [supporting]
        item["supporting_evidence"] = [str(value)[:1000] for value in supporting[:10] if value]
        item["trigger_reachable"] = verdict.get("trigger_reachable") if isinstance(verdict.get("trigger_reachable"), bool) else None
        item["target_commit_verified"] = bool(verdict)
        decision = verdict.get("verdict") or item.get("disposition") or "needs_confirmation"
        direct_evidence_fallback = not verdict and _has_direct_target_defect_evidence(item)
        if not verdict and decision == "confirmed" and item.get("source") != "static_scan" and not direct_evidence_fallback:
            decision = "needs_confirmation"
            item["verification_reason"] = "目标 Commit 反证核验未返回有效结论，不能作为确定缺陷"
        if verdict and decision == "confirmed" and item.get("source") != "static_scan":
            if not (item["verification_reason"] and item["supporting_evidence"] and item["trigger_reachable"] is True):
                decision = "needs_confirmation"
                item["verification_reason"] = item["verification_reason"] or "反证核验缺少完整证据，不能作为确定缺陷"
        item["verification_status"] = "direct_evidence_fallback" if direct_evidence_fallback else ({
            "confirmed": "supported", "needs_confirmation": "partially_supported",
            "rejected": "refuted", "advisory": "inconclusive",
        }.get(decision, "inconclusive") if verdict else "verification_failed")
        item["verdict"] = decision
        if verdict.get("severity") in {"high", "medium", "low"}:
            item["severity"] = verdict["severity"]
        if decision == "rejected":
            rejected.append(item)
            continue
        item["disposition"] = decision
        item["verified"] = decision == "confirmed"
        active.append(item)
    return active, rejected

'''
text = text[:start] + new_apply + text[end + 1:]

text = replace_once(text,
    '    if task.mode == "quick" or not findings:\n        return findings, [], 0, "快速模式未执行 AI 反证审查"',
    '    if not findings:\n        return [], [], 0, "没有需要执行反证审查的候选问题"\n'
    '    if task.mode == "quick":\n        active, rejected = _apply_verification_results(findings, [])\n'
    '        return active, rejected, 0, "快速模式未执行 AI 反证审查，缺少直接源码证据的 AI 确定缺陷已降为待确认"',
    "quick mode")
text = replace_once(text,
    '    except Exception as exc:\n        return findings, [], 0, f"反证审查未执行：{exc}"',
    '    except Exception as exc:\n        active, rejected = _apply_verification_results(findings, [])\n'
    '        return active, rejected, 0, f"反证审查未执行，缺少直接源码证据的 AI 确定缺陷已降为待确认：{exc}"',
    "config failure")
text = replace_once(text,
    '"你是代码审查反证员。逐条验证候选问题，必须先尝试用目标 Commit 源码和跨文件检索结果推翻它。"\n'
    '            "仅变更了字段、路由、应用注册、组件属性或容器入口，不等于存在缺陷；若相关定义、调用方、配置或兼容逻辑已同步，必须 rejected。"\n'
    '            "confirmed 要求完整证据能直接证明可复现错误；needs_confirmation 仅用于代码无法决定的业务或运行环境问题；advisory 仅用于非正确性改进。"',
    '"你是代码审查反证员。逐条验证候选问题，必须先尝试用目标 Commit 源码和跨文件检索结果推翻它。"\n'
    '            "只判断目标 Commit 当前仍然存在的错误；本次变更已修复的问题、正常实现或正向效果必须 rejected。"\n'
    '            "仅变更字段、路由、配置或入口不等于缺陷；相关逻辑已同步时必须 rejected。"\n'
    '            "‘可能’‘建议验证’或‘若未同步将出错’不能证明缺陷。"\n'
    '            "confirmed 必须同时给出目标版本错误代码、可达触发路径、确定错误结果和支持证据；任一项缺失均不得 confirmed。"\n'
    '            "needs_confirmation 仅用于代码无法决定的问题；advisory 仅用于非正确性改进。"',
    "verification prompt")
text = replace_once(text,
    '\\"counter_evidence\\":\\"\\"',
    '\\"supporting_evidence\\":[\\"\\"],\\"counter_evidence\\":\\"\\",\\"trigger_reachable\\":true',
    "verification schema")

output_path.write_text(text, encoding="utf-8")
print(hashlib.sha256(output_path.read_bytes()).hexdigest())
