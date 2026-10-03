"""T14：从一份产出回溯质量飞轮完整链路。

为什么需要一个统一的溯源入口：飞轮的七段（产出 → 反馈 → 金标 → 归因 → 优化候选 →
评测门禁 → 审批激活/回滚）分散在五个模块里，如果让页面自己拼，每个页面都会漏掉
不同的环节，而且**漏掉的那一刻不会报错**——页面看起来"闭环已完成"，实际上中间
断了两段。这个模块把七段一次性查出来，并且显式给出 ``stages`` 徽标与
``broken_at``（第一个断点），让"闭环到底通没通"成为一个可以断言的事实。

除闭环之外还回答第二个问题：**产出里的结论是从哪来的**（``sources``）。
两者并列而不是合并——"链路通了"与"内容可信"是两件事，
合并会让"闭环已完成"看起来像是"内容已被验证过"。

只读，不写任何业务状态。所有查询都按 ``project`` 收口，避免跨项目串数据。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from .capability_models import CapabilityRelease, PromotionDecision, ReleaseObservation
from .gold_models import GoldCase
from .models import FeedbackEvent, GenerationOutput
from .optimization_models import OptimizationExperiment, OptimizationProposal
from .trace_models import FailureAttribution

#: 飞轮七段的固定顺序与中文名。顺序即依赖顺序，"第一个断点"按它算。
STAGE_ORDER = (
    ("output", "产出"),
    ("binding", "版本绑定"),
    ("feedback", "反馈"),
    ("gold", "金标"),
    ("attribution", "归因"),
    ("proposal", "优化候选"),
    ("gate", "评测门禁"),
    ("release", "审批发布"),
)

#: 引用条目的 ``source_type`` 取值。四阶段与知识问答共用这一套 ——
#: 前端据此决定"点这条引用该跳到哪"（图谱节点 / 文档 / 需求 / 用例）。
SOURCE_TYPES = ("graph", "document", "requirement", "test_case")

#: 图谱节点类型 → 引用来源类型。图谱里需求与用例是**不同的节点类型**，
#: 只按"有没有节点 id"一律归成 ``graph``，页面就无法把点击引到需求或用例详情上。
_NODE_TYPE_TO_SOURCE = {
    "requirement_document": "requirement",
    "requirement_module": "requirement",
    "test_case": "test_case",
    "test_step": "test_case",
    "test_module": "test_case",
}

#: ``skill_content.files`` 最多列多少个文件。包不可变、哈希即定位，
#: 列全的必要性低于"响应别因为一个大包变得很慢"。
MAX_SKILL_FILES = 40


class OutputLineageService:
    """按产出回溯闭环链路。"""

    @staticmethod
    def trace(output) -> dict:
        """返回该产出的完整链路（只读）。"""
        output = OutputLineageService._as_output(output)
        project_id = output.project_id

        bindings = OutputLineageService._bindings(output)
        feedbacks = list(
            FeedbackEvent.objects.filter(project_id=project_id, output_id=output.pk)
            .select_related("actor", "capability", "release", "skill_version")
            .order_by("created_at")
        )
        gold_cases = list(
            GoldCase.objects.filter(source_output_id=output.pk)
            .select_related("version", "version__dataset")
            .order_by("created_at")
        )
        attributions = list(
            FailureAttribution.objects.filter(project_id=project_id, output_id=output.pk)
            .order_by("created_at")
        )
        proposals = list(
            OptimizationProposal.objects
            .filter(project_id=project_id, attributions__in=attributions)
            .distinct()
            .order_by("created_at")
        ) if attributions else []
        experiments = list(
            OptimizationExperiment.objects
            .filter(proposal__in=proposals)
            .select_related("gold_dataset_version", "baseline_run", "candidate_run", "candidate_release")
            .order_by("created_at")
        ) if proposals else []
        releases = OutputLineageService._releases(
            project_id=project_id, experiments=experiments, bindings=bindings,
        )
        sources = OutputLineageService._sources(output)

        stages = {
            "output": {"ok": True, "count": 1},
            "binding": {"ok": any(item["skill_version_id"] for item in bindings), "count": len(bindings)},
            "feedback": {"ok": bool(feedbacks), "count": len(feedbacks)},
            "gold": {"ok": bool(gold_cases), "count": len(gold_cases)},
            "attribution": {
                "ok": any(item.state == "confirmed" for item in attributions),
                "count": len(attributions),
                "confirmed": sum(1 for item in attributions if item.state == "confirmed"),
            },
            "proposal": {"ok": bool(proposals), "count": len(proposals)},
            "gate": {
                "ok": any(item.get("gate_passed") is not None for item in releases) or bool(experiments),
                "count": len(experiments),
            },
            "release": {
                "ok": any(item["state"] in {"active", "rolled_back"} for item in releases),
                "count": len(releases),
            },
        }
        broken_at = next(
            (code for code, _label in STAGE_ORDER if not stages[code]["ok"]), ""
        )
        return {
            "output": {
                "id": str(output.pk),
                "task_type": output.task_type,
                "task_id": output.task_id,
                "model_version": output.model_version,
                "prompt_version": output.prompt_version,
                "created_at": output.created_at.isoformat() if output.created_at else "",
                "project_id": project_id,
            },
            "skill": OutputLineageService._skill_descriptor(output),
            "bindings": bindings,
            "feedback": [
                {
                    "id": str(item.pk),
                    "signal": item.signal,
                    "actor": getattr(item.actor, "username", "") if item.actor_id else "",
                    "evidence_count": len(item.evidence or []),
                    "created_at": item.created_at.isoformat() if item.created_at else "",
                }
                for item in feedbacks
            ],
            "gold": [
                {
                    "id": str(item.pk),
                    "dataset": item.version.dataset.name if item.version_id else "",
                    "dataset_version": item.version.version if item.version_id else "",
                    "split": item.split,
                    "state": item.state,
                }
                for item in gold_cases
            ],
            "attribution": [
                {
                    "id": str(item.pk),
                    "category": item.category,
                    "layer": item.layer,
                    "state": item.state,
                    "confidence": item.confidence,
                }
                for item in attributions
            ],
            "proposal": [
                {
                    "id": str(item.pk),
                    "proposal_type": item.proposal_type,
                    "title": item.title,
                    "state": item.state,
                }
                for item in proposals
            ],
            "experiment": [
                {
                    "id": str(item.pk),
                    "proposal_id": str(item.proposal_id or ""),
                    "status": item.status,
                    "gate_passed": (item.gate_report or {}).get("passed"),
                    "candidate_release_id": str(item.candidate_release_id or ""),
                }
                for item in experiments
            ],
            "release": releases,
            # 内容来源：回答"这个需求点、这条用例是参考什么文件和 Skill 内容生成的"。
            # 与 stages 并列而不是并进 stages：前者是"链路走到哪"，后者是"内容从哪来"。
            "sources": sources,
            "stages": stages,
            "broken_at": broken_at,
            "closed_loop": not broken_at,
        }

    # ------------------------------------------------------------ 内容来源

    @classmethod
    def _sources(cls, output) -> dict:
        """产出的内容来源。**永远返回四个键**，没数据时给空值不给缺字段。

        给空值而不是省略：前端要区分"这个接口没返回这一块"与"这条产出确实没有引用"，
        缺字段会让两种情况长得一样，于是"没有引用"看起来像"功能没上"。
        """
        trace = output.trace if output.trace_id else None
        channels = dict(trace.channels or {}) if trace is not None else {}
        raw = list(trace.citations or []) if trace is not None else []
        citations = cls._normalize_citations(raw, project_id=output.project_id)
        return {
            "channels": channels,
            "citations": citations,
            "graph_nodes": cls._graph_nodes(citations, project_id=output.project_id),
            "skill_content": cls._skill_content(output),
        }

    @classmethod
    def _normalize_citations(cls, raw: list, *, project_id) -> list[dict]:
        """把两种来源的引用拍成同一套结构。

        现状有两套写法，必须都能读：

        - 知识问答：``{citation_id: "kb:{kb}:{doc}:{chunk}", document_id, chunk_index, rank}``
        - 四阶段：产出的 ``evidence`` 原样落入 ``citations``，
          形如 ``{source_type, source_id, node_id, title, ...}``，字段集不固定
          （协议允许产出方自己带 ``extensions``）。

        统一成 ``{citation_id, source_type, source_id, node_id, document_id,
        chunk_index, rank, title}`` —— 前端只需要会渲染一种结构。
        认不出来的条目**不静默丢掉**，而是保留成 ``source_type=""`` 的条目：
        丢掉会让"引用数对不上"变成一个查不出来的差异。
        """
        node_ids = [
            str(item.get("node_id") or "").strip()
            for item in raw if isinstance(item, dict)
        ]
        node_map = cls._node_map(node_ids, project_id=project_id)

        normalized = []
        for index, item in enumerate(raw):
            if isinstance(item, str):
                # 字符串形态（``evaluation`` / 种子数据里会这么存）：只有一个 id。
                normalized.append({
                    "citation_id": item, "source_type": "", "source_id": item,
                    "node_id": "", "document_id": "", "chunk_index": None,
                    "rank": index + 1, "title": "",
                })
                continue
            if not isinstance(item, dict):
                continue
            node_id = str(item.get("node_id") or "").strip()
            document_id = str(item.get("document_id") or "").strip()
            descriptor = node_map.get(node_id) if node_id else None
            source_type = str(item.get("source_type") or "").strip()
            if source_type not in SOURCE_TYPES:
                # 没给或不认识时按证据本身推断，而不是一律记成"未知"：
                # 推断出来的类型决定了点击这条引用能跳到哪，比留空有用得多。
                if descriptor is not None:
                    source_type = _NODE_TYPE_TO_SOURCE.get(descriptor["kind"], "graph")
                elif node_id:
                    source_type = "graph"
                elif document_id:
                    source_type = "document"
                else:
                    source_type = ""
            source_id = str(item.get("source_id") or "").strip()
            if not source_id:
                source_id = node_id or document_id
            normalized.append({
                "citation_id": str(item.get("citation_id") or f"cite-{index + 1}"),
                "source_type": source_type,
                "source_id": source_id,
                "node_id": node_id,
                "document_id": document_id,
                "chunk_index": item.get("chunk_index"),
                "rank": item.get("rank") or index + 1,
                "title": str(item.get("title") or (descriptor or {}).get("label") or ""),
            })
        return normalized

    @staticmethod
    def _node_map(node_ids: list, *, project_id) -> dict:
        """按引用里给的节点标识批量取图谱节点，返回 ``{标识: 描述}``。

        引用里可能给 ``external_id``（项目级稳定标识）也可能给主键，两种都认 ——
        只认一种会让"有引用但点不过去"，而这一点在页面上表现为"链接坏了"，
        排查方向完全被带偏。
        本项目内查询，杜绝用别项目的节点 id 探到内容。
        """
        from django.db.models import Q

        from .graph_models import GraphNode

        wanted = [item for item in dict.fromkeys(node_ids) if item]
        if not wanted:
            return {}
        query = Q(external_id__in=wanted)
        uuid_like = [item for item in wanted if _looks_like_uuid(item)]
        if uuid_like:
            query |= Q(pk__in=uuid_like)
        result = {}
        for node in GraphNode.objects.filter(project_id=project_id).filter(query):
            descriptor = {
                "node_id": str(node.id),
                "external_id": node.external_id,
                "kind": node.node_type,
                "label": node.name or node.external_id,
            }
            result[str(node.id)] = descriptor
            result[node.external_id] = descriptor
        return result

    @classmethod
    def _graph_nodes(cls, citations: list[dict], *, project_id) -> list[dict]:
        """引用里出现的图谱节点（去重、保序）。

        只列**产出真正引用到的**节点，而不是"项目图谱里有哪些节点"：
        后者会给出一个与这份产出无关的清单，看着很充实，实际无法回答
        "这条结论参考了谁"。
        """
        ids = []
        for item in citations:
            node_id = item.get("node_id") or ""
            if node_id and node_id not in ids:
                ids.append(node_id)
        if not ids:
            return []
        node_map = cls._node_map(ids, project_id=project_id)
        seen = []
        for node_id in ids:
            descriptor = node_map.get(node_id)
            if descriptor is None:
                # 节点被清理了（图谱重建）：仍然报出来并标 ``resolved=False``，
                # 而不是抹掉这条引用 —— 抹掉会让人以为产出根本没引用任何东西。
                seen.append({"node_id": node_id, "kind": "", "label": "", "resolved": False})
                continue
            seen.append({
                "node_id": node_id,
                "kind": descriptor["kind"],
                "label": descriptor["label"],
                "resolved": True,
            })
        return seen

    @staticmethod
    def _skill_content(output) -> dict:
        """这条产出参考了这版 Skill 的哪些文件（清单 + 哈希，**不落正文**）。

        包是不可变产物，哈希足以定位到"当时那一份"；把正文抄进血缘响应
        会让同一个文件在系统里存两份，并让"包被改过"变得难以发现。
        """
        version = output.skill_version if output.skill_version_id else None
        if version is None:
            return {"skill_version_id": "", "package_sha256": "", "files": [], "truncated": False}
        files: list[dict] = []
        truncated = False
        try:
            root = Path(version.get_full_path() or "")
        except Exception:  # pragma: no cover - 路径解析异常不该拖垮血缘
            root = None
        if root is not None and root.is_dir():
            from .report_optimization import SkillPackageReader

            candidates = sorted(
                (
                    path for path in root.rglob("*")
                    if path.is_file()
                    and path.suffix.lower() in SkillPackageReader.TEXT_SUFFIXES
                    and not any(part in SkillPackageReader.SKIP_DIRS for part in path.parts)
                ),
                key=lambda path: (path.name != "SKILL.md", str(path)),
            )
            for path in candidates:
                if len(files) >= MAX_SKILL_FILES:
                    truncated = True
                    break
                try:
                    digest = hashlib.sha256(path.read_bytes()).hexdigest()
                except OSError:
                    continue
                files.append({
                    "path": str(path.relative_to(root)),
                    "sha256": digest,
                    "size": path.stat().st_size,
                })
        return {
            "skill_version_id": str(version.pk),
            "version": version.version,
            "package_sha256": output.skill_package_sha256 or version.package_sha256 or "",
            "files": files,
            "truncated": truncated,
        }

    # ------------------------------------------------------------ 内部

    @staticmethod
    def _as_output(output) -> GenerationOutput:
        if isinstance(output, GenerationOutput):
            return output
        found = GenerationOutput.objects.filter(pk=output).first()
        if found is None:
            from django.core.exceptions import ValidationError

            raise ValidationError(f"产出不存在：{output}")
        return found

    @staticmethod
    def _skill_descriptor(output) -> dict:
        version = output.skill_version if output.skill_version_id else None
        package_sha256 = output.skill_package_sha256 or ""
        if not package_sha256 and version is not None:
            # ORM 直造的产出可能只有外键、没有哈希列；回退到版本记录，
            # 避免"能查到版本却报哈希为空"这种自相矛盾的输出。
            package_sha256 = version.package_sha256 or ""
        protocol = ((output.metadata or {}).get("protocol") or {})
        return {
            "skill_id": str(version.skill_id) if version is not None else "",
            "skill_name": version.skill.name if version is not None and version.skill_id else "",
            "skill_version_id": str(version.pk) if version is not None else "",
            "version": version.version if version is not None else "",
            "package_sha256": package_sha256,
            "protocol_descriptor": protocol.get("skill") or {},
        }

    @staticmethod
    def _bindings(output) -> list[dict]:
        """取出产出对应任务的 Skill 版本锁。"""
        from skills.models import SkillVersion

        from .workflow_models import WorkflowSkillLock

        protocol = ((output.metadata or {}).get("protocol") or {})
        workflow_id = protocol.get("workflow_id") or ""
        locks = []
        if workflow_id:
            locks = list(
                WorkflowSkillLock.objects
                .filter(project_id=output.project_id, workflow_id=str(workflow_id))
                .select_related("skill", "skill_version", "release")
                .order_by("locked_at")
            )
        if locks:
            return [
                {
                    "source": "lock",
                    "workflow_id": lock.workflow_id,
                    "lock_key": lock.lock_key,
                    "scope": lock.scope,
                    "stage": lock.stage,
                    "skill_id": str(lock.skill_id or ""),
                    "skill_name": lock.skill.name if lock.skill_id else "",
                    "skill_version_id": str(lock.skill_version_id or ""),
                    "version": lock.skill_version.version if lock.skill_version_id else "",
                    "release_id": str(lock.release_id or ""),
                    "release_state": lock.release.state if lock.release_id else "",
                    "package_sha256": lock.package_sha256,
                    "locked_at": lock.locked_at.isoformat() if lock.locked_at else "",
                }
                for lock in locks
            ]
        # 没有锁记录时退回产出自身携带的版本，并标明来源，避免把"没有锁"
        # 伪装成"锁住了"。
        if output.skill_version_id:
            version = (
                SkillVersion.objects.filter(pk=output.skill_version_id)
                .select_related("skill", "release").first()
            )
            if version is not None:
                return [{
                    "source": "output",
                    "workflow_id": workflow_id,
                    "lock_key": "",
                    "scope": "",
                    "stage": "",
                    "skill_id": str(version.skill_id),
                    "skill_name": version.skill.name if version.skill_id else "",
                    "skill_version_id": str(version.pk),
                    "version": version.version,
                    "release_id": str(version.release_id or ""),
                    "release_state": version.release.state if version.release_id else "",
                    "package_sha256": output.skill_package_sha256 or version.package_sha256 or "",
                    "locked_at": "",
                }]
        return []

    @staticmethod
    def _releases(*, project_id, experiments, bindings) -> list[dict]:
        release_ids = {item["release_id"] for item in bindings if item.get("release_id")}
        release_ids.update(item.candidate_release_id for item in experiments if item.candidate_release_id)
        if not release_ids:
            return []
        releases = (
            CapabilityRelease.objects
            .filter(project_id=project_id, pk__in=release_ids)
            .select_related("approved_by", "created_by", "candidate_run", "baseline_run")
            .order_by("created_at")
        )
        decisions = PromotionDecision.objects.filter(release__in=releases)
        decisions_by_release = {}
        for decision in decisions:
            decisions_by_release.setdefault(decision.release_id, []).append(decision)
        observations = ReleaseObservation.objects.filter(release__in=releases)
        observations_by_release = {}
        for observation in observations:
            observations_by_release.setdefault(observation.release_id, []).append(observation)

        result = []
        for release in releases:
            gate_report = release.gate_report or {}
            result.append({
                "id": str(release.pk),
                "name": release.name,
                "version": release.version,
                "kind": release.kind,
                "state": release.state,
                "gate_passed": gate_report.get("passed"),
                "approval_snapshot_hash": release.approval_snapshot_hash,
                "observation_state": release.observation_state,
                "approved_by": getattr(release.approved_by, "username", "") if release.approved_by_id else "",
                "decision_count": len(decisions_by_release.get(release.pk, [])),
                "observation_count": len(observations_by_release.get(release.pk, [])),
            })
        return result


class ResponsibilityService:
    """下游 Badcase → 责任阶段 + SkillVersion（R8 / T16）。

    ``AttributionService.trace_upstream_versions`` 已经给出"沿 ``parent_output_ids``
    反向找到的、带**已确认**归因的那一级"，但下游要的是三件具体的事：
    **哪个阶段**、**哪个 SkillVersion**、**那份包的哈希**。把这三件事在这里算出来，
    而不是让每个页面自己去 chain 里翻字段，是因为"翻错一级"不会报错——
    页面会显示一个有版本号的结论，只是那一级并不是责任方。

    硬约束：**没有已确认归因时 ``resolved=False``，且 ``responsible_stage`` 为空**。
    此时不允许据此生成任何候选（由 ``attribution.assert_confirmed_attributions`` 兜底），
    调用方也能凭 ``resolved`` 一眼看出"这条结论还不能用"。
    """

    #: 归因类别 → 更可能出问题的阶段提示。仅用于在无确认归因时给出**参考**线索，
    #: 不参与 ``resolved`` 判定。
    #: ``downstream`` 指向**收口阶段**——主链路口径已从"报告生成"改为
    #: "问题跟踪"，历史流程仍可能是报告生成，所以这类线索按新口径给，
    #: 真正的责任阶段永远以 ``trace_upstream_versions`` 的确认归因为准。
    CATEGORY_STAGE_HINTS = {
        "prompt": "risk_identification",
        "instruction": "case_review",
        "knowledge": "testcase_generation",
        "retrieval": "testcase_generation",
        "skill": "testcase_generation",
        "tool": "test_execution",
        "environment": "test_execution",
        "downstream": "issue_tracking",
    }

    @classmethod
    def locate(cls, output, *, project=None) -> dict:
        """定位下游失败的责任阶段与 SkillVersion（只读）。"""
        from django.core.exceptions import ValidationError

        from .attribution import AttributionService

        output = OutputLineageService._as_output(output)
        if project is not None and int(getattr(project, "pk", project)) != int(output.project_id):
            raise ValidationError("产出不属于该项目，拒绝跨项目归因")
        traced = AttributionService.trace_upstream_versions(output)
        chain = traced["chain"]
        responsible = traced["responsible"]

        slim_chain = [
            {
                "output_id": item["output_id"],
                "depth": item["depth"],
                "stage": item["stage"],
                "stage_label": _stage_label(item["stage"]),
                "skill_id": item["skill_id"],
                "skill_name": item["skill_name"],
                "skill_version_id": item["skill_version_id"],
                "skill_version": item["skill_version"],
                "package_sha256": item["package_sha256"],
                "confirmed_categories": [
                    entry.get("category") for entry in item["confirmed_attributions"]
                ],
            }
            for item in chain
        ]

        if responsible is None:
            # 无已确认归因：给出"最靠近可疑类别"的参考阶段，但明确标记未解决。
            hint = cls._hint_stage(chain)
            return {
                "output_id": str(output.pk),
                "workflow_id": str(
                    (((output.metadata or {}).get("protocol") or {}).get("workflow_id")) or ""
                ),
                "resolved": False,
                "responsible_output_id": "",
                "responsible_stage": "",
                "responsible_stage_label": "",
                "responsible_skill_id": "",
                "responsible_skill_name": "",
                "responsible_skill_version_id": "",
                "responsible_skill_version": "",
                "responsible_package_sha256": "",
                "responsible_categories": [],
                "responsible_layers": [],
                "depth": None,
                "chain": slim_chain,
                "chain_length": len(slim_chain),
                "hint_stage": hint["stage"],
                "hint_stage_label": hint["stage_label"],
                "reason": "该产出及其上游均无已确认归因，不能据此生成候选版本；请先完成归因并人工确认",
            }

        categories = [entry.get("category", "") for entry in responsible["confirmed_attributions"]]
        layers = [entry.get("layer", "") for entry in responsible["confirmed_attributions"]]
        depth = responsible["depth"]
        position = "当前产出" if not depth else "上游第 %s 级" % depth
        if responsible["skill_version"]:
            version_note = "，SkillVersion %s" % responsible["skill_version"]
        else:
            version_note = "，该阶段未锁定 Skill 版本"
        return {
            "output_id": str(output.pk),
            "workflow_id": str(
                (((output.metadata or {}).get("protocol") or {}).get("workflow_id")) or ""
            ),
            "resolved": True,
            "responsible_output_id": responsible["output_id"],
            "responsible_stage": responsible["stage"],
            "responsible_stage_label": _stage_label(responsible["stage"]),
            "responsible_skill_id": responsible["skill_id"],
            "responsible_skill_name": responsible["skill_name"],
            "responsible_skill_version_id": responsible["skill_version_id"],
            "responsible_skill_version": responsible["skill_version"],
            "responsible_package_sha256": responsible["package_sha256"],
            "responsible_categories": categories,
            "responsible_layers": layers,
            "depth": responsible["depth"],
            "chain": slim_chain,
            "chain_length": len(slim_chain),
            "hint_stage": "",
            "hint_stage_label": "",
            "reason": "责任阶段：%s（%s）%s" % (
                _stage_label(responsible["stage"]), position, version_note,
            ),
        }

    @classmethod
    def _hint_stage(cls, chain: list[dict]) -> dict:
        for item in chain:
            for category in item["confirmed_attributions"]:
                stage = cls.CATEGORY_STAGE_HINTS.get(category.get("category", ""))
                if stage:
                    return {"stage": stage, "stage_label": _stage_label(stage)}
        return {"stage": "", "stage_label": ""}


def _looks_like_uuid(value: str) -> bool:
    """判断字符串是不是 UUID。

    先判形状再拿去查主键：把 ``"req-login-01"`` 直接塞进 ``pk__in`` 会让
    数据库抛"非法 UUID 语法"，一个数据问题变成一次 500。
    """
    import uuid as _uuid

    try:
        _uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def _stage_label(stage: str) -> str:
    from .capability_registry import STAGE_LABELS

    return STAGE_LABELS.get(stage, stage or "")


def line_closure_summary(project_id) -> dict:
    """项目级闭环概览：多少产出走完了全程、断点分布在哪一段。"""
    from collections import Counter

    outputs = GenerationOutput.objects.filter(project_id=project_id).order_by("-created_at")[:200]
    counters = Counter()
    closed = 0
    for output in outputs:
        trace = OutputLineageService.trace(output)
        if trace["closed_loop"]:
            closed += 1
        elif trace["broken_at"]:
            counters[trace["broken_at"]] += 1
    labels = dict(STAGE_ORDER)
    return {
        "sampled_outputs": len(outputs),
        "closed_loop_count": closed,
        "broken_breakdown": [
            {"stage": code, "label": labels.get(code, code), "count": count}
            for code, count in counters.most_common()
        ],
    }
