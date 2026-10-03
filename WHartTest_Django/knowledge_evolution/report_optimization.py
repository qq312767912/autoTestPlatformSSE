"""报告驱动的 Skill 优化建议：AI 提候选，人工确认后才生效。

口径（2026-10-03 用户拍板）
--------------------------
1. **采纳率只作为不同版本间对比的评分维度**，不作为能否上传或能否进化的门槛。
   门槛若需要，由治理侧显式配置，不写在解析层、也不写在这里。
2. LLM 提优化点的输入固定为**四要素**：

   | # | 要素 | 来源 |
   | --- | --- | --- |
   | ① | 当前 Skill 包正文 | ``SkillVersion.get_full_path()`` 下的文本文件（有截断） |
   | ② | 本轮缺陷 | 报告解析出的、已按"归因类别 + 问题类型"聚合的缺陷 |
   | ③ | 历史归因 | 该能力既往已确认 / 已驳回的归因 |
   | ④ | 人工确认报告 | 人在报告里写下的判定、问题描述、修改点、不采纳原因 |

为什么要单独有这一层
------------------
用例审查的产出是一份**审查结论**，它本身看不出"这份 Skill 哪里要改"。
要改什么，只有把**人在报告里写下的判断**和**这份 Skill 的实际写法**对上才看得出来 ——
例如人反复把某类问题标成"否"，说明这类判定条件在 SKILL.md 里放得太宽。

这个对照由 LLM 做，结论交人确认。**AI 的结论一律落 ``proposed``**，
不落 ``confirmed`` —— 落 ``confirmed`` 就等于把"AI 觉得自己错了"当成改进依据，
而 T23 定下的前提是"改进依据必须是人工复核后确认的错误"。

「② 本轮缺陷」与「④ 人工确认报告」为什么要分开给
------------------------------------------------
②是聚合后的统计（哪类问题、多少条），④是逐条的原文（人到底写了什么）。
只给②，LLM 会按类名想象；只给④，LLM 会被单条样本的措辞带偏。两者一起给，
它才能既看到分布、也看到具体句子。
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from django.core.exceptions import ValidationError

logger = logging.getLogger(__name__)

#: 归因类别 → 飞轮信号词。与 ``case_review_evolution.SIGNAL_BY_CATEGORY`` 同一套语义：
#: ``false_positive`` 读作"这条问题不成立"，``defect_confirmed`` 读作"这条缺陷成立"。
#: 两者在 ``evaluators.FeedbackOutcomeEvaluator`` 里是正负两端，写反会让护栏
#: 对下次执行的模型传达相反的要求。
_SIGNAL_FALSE_ALARM = "false_positive"
_SIGNAL_DEFECT = "defect_confirmed"

#: 判定"这是误报类结论"的类别。这些类别描述的是"AI 不该报/判错了"。
FALSE_ALARM_CATEGORIES = frozenset({
    "intent_error", "planning_error", "retrieval_error", "prompt_error",
    "knowledge_stale", "tool_error",
})


def signal_for_category(category: str) -> str:
    """归因类别 → 飞轮信号词。"""
    return _SIGNAL_FALSE_ALARM if category in FALSE_ALARM_CATEGORIES else _SIGNAL_DEFECT


class LLMUnavailable(RuntimeError):
    """没有可用的激活 LLM 配置。

    单独一个异常类型，是为了让上层能把"缺 LLM"与"LLM 调用失败"分开：
    前者应降级到纯人工标注并**明示**用户，后者应报错重试。
    """


class SkillPackageReader:
    """读 Skill 包内容摘要，供 LLM 归因使用。

    包是不可变产物，所以"读到的内容"与 ``package_sha256`` 一一对应 ——
    引用哪一版包的哪几个文件，事后可以逐字复核。
    """

    MAX_FILE_CHARS = 6000
    MAX_FILES = 12
    TEXT_SUFFIXES = (".md", ".txt", ".yaml", ".yml", ".json", ".py", ".sh")
    SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".venv", "venv"}

    @classmethod
    def read(cls, skill_version) -> dict:
        """返回 ``{"version", "skill_name", "package_sha256", "files", "truncated"}``。"""
        empty = {
            "version": getattr(skill_version, "version", ""),
            "skill_name": getattr(getattr(skill_version, "skill", None), "name", ""),
            "package_sha256": getattr(skill_version, "package_sha256", "") or "",
            "files": [],
            "truncated": False,
        }
        if skill_version is None:
            return empty
        try:
            root = Path(skill_version.get_full_path() or "")
        except Exception:  # pragma: no cover - 路径解析异常不该拖垮归因
            logger.exception("读取 Skill 包路径失败")
            return empty
        if not root or not root.is_dir():
            return empty

        files: list[dict] = []
        truncated = False
        candidates = sorted(
            (
                path for path in root.rglob("*")
                if path.is_file()
                and path.suffix.lower() in cls.TEXT_SUFFIXES
                and not any(part in cls.SKIP_DIRS for part in path.parts)
            ),
            key=lambda path: (path.name != "SKILL.md", str(path)),
        )
        for path in candidates:
            if len(files) >= cls.MAX_FILES:
                truncated = True
                break
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            excerpt = content[: cls.MAX_FILE_CHARS]
            files.append({
                "path": str(path.relative_to(root)),
                "chars": len(content),
                "excerpt": excerpt,
            })
        return {**empty, "files": files, "truncated": truncated}


class ReportOptimizationAdvisor:
    """由四要素提出候选优化点，并支持人工逐条确认。"""

    #: 单次最多提多少条。不是省资源，而是保持"人愿意逐条看完"——
    #: 一次给三十条，人会直接全选，确认这一步就白设了。
    MAX_CANDIDATES = 8
    #: 历史归因取最近多少条。
    MAX_HISTORY = 12

    def __init__(self, llm_factory=None):
        self.llm_factory = llm_factory or self._default_factory

    @staticmethod
    def _default_factory(config):
        from langgraph_integration.views import create_llm_instance
        return create_llm_instance(config, temperature=0)

    # ------------------------------------------------------------ 生成候选

    def propose(self, *, output, scan, skill_version=None, actor=None) -> dict:
        """由四要素生成候选优化点，落 ``proposed`` 归因并返回。

        返回值：``{"candidates": [...], "degraded": False, "package": {...}}``。
        缺 LLM 时抛 :class:`LLMUnavailable`，由调用方转成"降级为人工标注"。
        """
        from langgraph_integration.models import LLMConfig

        from .llm_judges import _json_object
        from .trace_models import FailureAttribution

        skill_version = skill_version or getattr(output, "skill_version", None)
        package = SkillPackageReader.read(skill_version)
        if not package["files"]:
            raise ValidationError(
                "读不到该 Skill 包的正文，无法据此提优化建议"
                "（包可能未落盘或被清理）"
            )

        config = LLMConfig.objects.filter(is_active=True).first()
        if not config:
            raise LLMUnavailable("未配置激活的 LLM，无法生成候选优化点")

        history = self._history(output)
        prompt = self._build_prompt(package=package, scan=scan, history=history)

        try:
            payload = _json_object(self.llm_factory(config).invoke(prompt))
        except Exception as exc:
            logger.exception("生成候选优化点失败")
            raise ValidationError(f"LLM 生成候选优化点失败：{exc}") from exc

        valid_categories = {value for value, _label in FailureAttribution.CATEGORY_CHOICES}
        candidates = []
        for index, item in enumerate(list(payload.get("candidates") or [])[: self.MAX_CANDIDATES]):
            if not isinstance(item, dict):
                continue
            category = str(item.get("category") or "").strip()
            hypothesis = str(item.get("hypothesis") or "").strip()
            if category not in valid_categories or not hypothesis:
                continue
            issue_type = str(item.get("issue_type") or "未标注类型").strip() or "未标注类型"
            try:
                confidence = float(item.get("confidence") or 0.5)
            except (TypeError, ValueError):
                confidence = 0.5
            # 上限 0.8 是刻意的：AI 的结论永远达不到"确定性"，
            # 0.8 以上的置信度只有规则归因和人工确认配得上。
            confidence = max(0.0, min(0.8, confidence))
            signal = signal_for_category(category)
            attribution, _created = FailureAttribution.objects.update_or_create(
                fingerprint=self._fingerprint(output, category, issue_type),
                defaults={
                    "project_id": output.project_id,
                    "output": output,
                    "workflow_id": str(
                        ((output.metadata or {}).get("protocol") or {}).get("workflow_id", "")
                    ),
                    "category": category,
                    "source": "llm",
                    "confidence": confidence,
                    "hypothesis": hypothesis,
                    "evidence": [
                        {"reason": str(text), "feedback_signals": [signal]}
                        for text in (item.get("evidence") or [])[:3] if str(text).strip()
                    ],
                    "counterevidence": [
                        {"reason": str(text)}
                        for text in (item.get("counterevidence") or [])[:3] if str(text).strip()
                    ],
                    "state": "proposed",
                },
            )
            candidates.append({
                "index": index,
                "attribution_id": str(attribution.id),
                "category": category,
                "issue_type": issue_type,
                "hypothesis": hypothesis,
                "confidence": confidence,
                "signal": signal,
                "evidence": [dict(entry) for entry in (attribution.evidence or [])],
                "counterevidence": [dict(entry) for entry in (attribution.counterevidence or [])],
                "state": attribution.state,
            })

        if not candidates:
            raise ValidationError(
                "LLM 没有给出可用的候选优化点。"
                "这通常意味着报告里没有可归因到本 Skill 的问题（例如缺陷都是被测对象的问题）。"
            )
        return {
            "candidates": candidates,
            "degraded": False,
            "package": {
                "version": package["version"],
                "skill_name": package["skill_name"],
                "package_sha256": package["package_sha256"],
                "files": [{"path": item["path"], "chars": item["chars"]} for item in package["files"]],
            },
            "history_count": len(history),
        }

    # ------------------------------------------------------------ 人工确认

    @classmethod
    def decide(cls, *, attribution, actor, action: str, hypothesis: str = "", category: str = "") -> dict:
        """人工对一条候选做出结论。

        ``action``：``accept`` 采纳原样 / ``edit`` 采纳但改写 / ``reject`` 驳回。

        **采纳不抬高置信度**：仍保留 LLM 给出的原值，只额外记录 ``confirmed_by``。
        把 AI 假设在确认后写成 ``confidence=1.0``，等于事后无法区分
        "模型猜对了"与"人确认过"，两种证据的可信度差一个量级。
        """
        from django.utils import timezone

        from .trace_models import FailureAttribution

        action = str(action or "").strip().lower()
        if action not in {"accept", "edit", "reject"}:
            raise ValidationError(f"未知的确认动作：{action or '（空）'}")
        if attribution.state == "confirmed" and action != "reject":
            # 幂等：重复点"采纳"不该报错，也不该改已有结论。
            return cls._candidate_view(attribution)

        updates = []
        if action == "reject":
            attribution.state = "rejected"
            updates.append("state")
        else:
            if action == "edit":
                text = str(hypothesis or "").strip()
                if not text:
                    raise ValidationError("改写后的优化点不能为空")
                attribution.hypothesis = text
                updates.append("hypothesis")
                if category and category != attribution.category:
                    valid = {value for value, _label in FailureAttribution.CATEGORY_CHOICES}
                    if category not in valid:
                        raise ValidationError(f"未知的归因类别：{category}")
                    attribution.category = category
                    updates.append("category")
            attribution.state = "confirmed"
            attribution.confirmed_by = actor
            attribution.confirmed_at = timezone.now()
            updates.extend(["state", "confirmed_by", "confirmed_at"])
        attribution.save(update_fields=list(dict.fromkeys(updates + ["updated_at"])))
        return cls._candidate_view(attribution)

    # ------------------------------------------------------------ 内部

    @staticmethod
    def _candidate_view(attribution) -> dict:
        return {
            "attribution_id": str(attribution.id),
            "category": attribution.category,
            "issue_type": next(
                (
                    str((entry or {}).get("issue_type") or "")
                    for entry in (attribution.evidence or [])
                    if isinstance(entry, dict) and entry.get("issue_type")
                ),
                "",
            ),
            "hypothesis": attribution.hypothesis,
            "confidence": attribution.confidence,
            "state": attribution.state,
            "source": attribution.source,
        }

    @staticmethod
    def _fingerprint(output, category: str, issue_type: str) -> str:
        """同一产出下的同一 (类别, 问题类型) 只保留一条候选。

        重复生成会 ``update_or_create`` 覆盖同一条，而不是堆出一串历史候选：
        候选是"这一轮报告给出的结论"，不是"模型的调用记录"。
        """
        raw = f"{output.id}:llm-proposal:{category}:{issue_type}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @classmethod
    def _history(cls, output) -> list[dict]:
        """历史归因：同一能力、既往已确认或已驳回的结论。

        带上"已驳回"是刻意的 —— 它回答的是"哪些建议人已经否过了"，
        少了这一半，模型每轮都会把同一件事重新提一遍。
        """
        from .trace_models import FailureAttribution

        rows = (
            FailureAttribution.objects
            .filter(
                project_id=output.project_id,
                state__in=("confirmed", "rejected"),
            )
            .exclude(output_id=output.pk)
            .order_by("-updated_at")[: cls.MAX_HISTORY]
        )
        return [
            {
                "category": row.category,
                "hypothesis": row.hypothesis,
                "state": row.state,
                "source": row.source,
            }
            for row in rows
        ]

    @classmethod
    def _build_prompt(cls, *, package: dict, scan, history: list[dict]) -> str:
        categories = ", ".join(
            value for value, _label in _category_choices()
        )
        package_text = "\n\n".join(
            f"--- {item['path']} ---\n{item['excerpt']}" for item in package["files"]
        )
        defects = json.dumps(
            [item.as_dict() for item in scan.defects], ensure_ascii=False,
        )
        # ④ 人工确认报告：逐条原文（人写的判定与修改点）。
        # 与 ② 的聚合统计分开给，理由见模块 docstring。
        human_rows = json.dumps(
            [
                {
                    "问题类型": defect.issue_type,
                    "归因类别": defect.category,
                    "样本": [dict(sample) for sample in defect.samples],
                }
                for defect in scan.defects
            ],
            ensure_ascii=False,
        )
        return (
            "你是 Skill 优化建议员。你的结论会交给人工逐条确认，"
            "只有被采纳的才会写进 Skill 包。\n"
            "约束：\n"
            "1. 只提【能通过改这份 Skill 的正文解决】的问题；"
            "被测对象自身的缺陷、用例本身写错、执行环境问题都不是本次要改的。\n"
            "2. 每条必须说明依据来自哪一类现象，不得把相关性当因果；有反证要一并给出。\n"
            f"3. 最多 {cls.MAX_CANDIDATES} 条，按改进价值从高到低。\n"
            "4. 不得建议删除安全、权限、全覆盖、反证相关的既有要求。\n"
            "5. 历史归因里已被人工驳回的方向不要重复提。\n"
            "仅返回 JSON（不要解释文字）："
            '{"candidates":[{"category":"...","issue_type":"...","hypothesis":"...",'
            '"confidence":0.0,"evidence":["..."],"counterevidence":["..."]}]}\n'
            f"category 取值限于：{categories}\n\n"
            "【一、当前 Skill 包】\n"
            f"{package_text}\n\n"
            "【二、本轮缺陷（按类别聚合，含条数与样本）】\n"
            f"{defects}\n\n"
            "【三、历史归因（往轮已确认/已驳回，避免重复）】\n"
            f"{json.dumps(history, ensure_ascii=False)}\n\n"
            "【四、人工确认报告（人在报告里写下的判定与修改点）】\n"
            f"{human_rows}"
        )


def _category_choices():
    from .trace_models import FailureAttribution
    return FailureAttribution.CATEGORY_CHOICES
