"""正式金标资产服务。所有状态变化集中在这里，避免 API 绕过业务约束。"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import Counter

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from .capability_registry import (
    ALL_WORKFLOW_STAGES,
    capability_info,
    package_sha256_of,
)
from .feedback import assert_evidence_complete, effective_evidence
from .gold_models import (
    AnnotationConflict,
    GoldAnnotation,
    GoldCase,
    GoldDataset,
    GoldDatasetVersion,
)
from .models import AssetCandidateEvent, FeedbackEvent, GenerationOutput

logger = logging.getLogger(__name__)


def _canonical_hash(value) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class GoldCandidateService:
    ELIGIBLE_SIGNALS = {
        "defect_confirmed", "false_positive", "missed", "edited",
        "test_failed", "reverted", "rejected",
    }

    @staticmethod
    @transaction.atomic
    def from_feedback(*, version: GoldDatasetVersion, feedback, actor, split="fresh") -> GoldCase:
        if version.state == "frozen":
            raise ValidationError("冻结版本不能新增金标候选")
        if feedback.signal not in GoldCandidateService.ELIGIBLE_SIGNALS:
            raise ValidationError("该反馈信号不足以生成金标候选")
        # 证据不完整时禁止升级（R5）。把"缺什么"直接带进异常，而不是只报"不允许"。
        missing = assert_evidence_complete(feedback)
        if missing:
            raise ValidationError(
                "反馈不足以升级为金标：" + "；".join(item["detail"] for item in missing)
            )
        output = feedback.output
        if not output:
            raise ValidationError("金标候选必须关联具体业务产出")
        if output.project_id != version.dataset.project_id:
            raise ValidationError("反馈与金标数据集不属于同一项目")
        # 金标必须按能力组织：数据集声明的 task_type 与产出必须一致，
        # 否则会出现"代码审查的样本被塞进用例审查金标集"，评测时毫无意义。
        if output.task_type != version.dataset.task_type:
            raise ValidationError(
                f"产出能力（{output.task_type}）与金标数据集能力（{version.dataset.task_type}）不一致"
            )

        protocol = (output.metadata or {}).get("protocol") or {}
        privacy = protocol.get("privacy") or {}
        privacy_level = privacy.get("level", "internal")
        prohibited = bool(privacy.get("prohibit_optimization")) or privacy_level == "prohibited"
        source_hash = _canonical_hash({
            "project": output.project_id,
            "output": str(output.id),
            "feedback": str(feedback.id),
            "output_hash": output.output_hash,
            "signal": feedback.signal,
        })
        evidence = effective_evidence(feedback)
        case, _ = GoldCase.objects.get_or_create(
            version=version,
            source_hash=source_hash,
            defaults={
                "source_output": output,
                "source_feedback": feedback,
                "task_type": output.task_type,
                "title": f"{output.task_type} · {feedback.get_signal_display()}",
                # 只保存引用和哈希，避免把原始查询、Diff或敏感正文复制到金标元数据。
                "input_snapshot": {
                    "trace_id": str(output.trace_id),
                    "output_id": str(output.id),
                    "query_hash": _canonical_hash(output.trace.query),
                    "output_hash": output.output_hash,
                    "protocol_version": protocol.get("schema_version", ""),
                    # 责任版本溯源：下游 Badcase 要能反向定位到具体 Skill 包/发布。
                    "capability_id": str(feedback.capability_id or ""),
                    "release_id": str(feedback.release_id or ""),
                    "skill_version_id": str(feedback.skill_version_id or ""),
                    "package_sha256": package_sha256_of(output),
                },
                "expected_output": (feedback.detail or {}).get("expected_output") or {},
                "evidence": evidence,
                "tags": [
                    tag for tag in (
                        feedback.signal,
                        feedback.reason_code,
                        feedback.capability_kind,
                    ) if tag
                ],
                "split": split,
                "state": "candidate",
                "privacy_level": "prohibited" if prohibited else privacy_level,
                "allow_optimization": not prohibited,
                "created_by": actor,
            },
        )
        if version.state == "draft":
            version.state = "labeling"
            version.save(update_fields=["state", "updated_at"])
        return case


class AssetCandidateService:
    """把散落的"该沉淀候选"信号收敛成幂等事件，并统一做预检与推荐（T04）。

    分工必须说清楚，因为它决定了"自动化能做什么"的边界：

    * 业务入口：只写一条 ``AssetCandidateEvent``（幂等），不做去重、不建候选；
    * 本服务：做项目归属、隐私、完整性、精确去重、近似冲突、推荐归属预检，
      然后**只**创建/更新 ``GoldCase(state=candidate)``；
    * 人工：所有"进入正式测试集"的准入动作，本服务一律不碰。

    任何一步"看起来可以自动确认"的捷径都不实现：客观执行结果只提高推荐优先级
    （``candidate_score``），不改变审核门禁（R10）。
    """

    #: 触发候选沉淀的反馈信号。``accepted`` / ``test_passed`` 不在其中：
    #: 确认型信号本身不产生"平台错了/漏了"的结论，沉淀成金标没有信息量。
    TRIGGER_SIGNALS = frozenset({
        "defect_confirmed", "missed", "false_positive",
        "test_failed", "reverted", "edited",
    })

    #: 只有这些信号断言"平台漏了/错了"，缺证据时不允许进入审核队列（R5/R10）。
    EVIDENCE_REQUIRED_SIGNALS = frozenset({
        "defect_confirmed", "missed", "false_positive", "rejected",
    })

    #: 候选来源映射（``GoldCase.ORIGIN_CHOICES`` 的子集）。
    ORIGIN_BY_SIGNAL = {
        "defect_confirmed": "defect",
        "test_failed": "defect",
        "missed": "missed",
        "false_positive": "false_positive",
        "reverted": "rollback",
        "edited": "feedback",
        "stage_output": "feedback",
        "history_import": "history",
    }

    #: 推荐分区：由已修复缺陷/漏测/误报/失败/回滚沉淀的样本天生是为"防复发"服务的，
    #: 默认进回归分区；编辑类反馈只说明"表述可改"，进新鲜分区。
    SPLIT_BY_SIGNAL = {
        "defect_confirmed": "regression",
        "missed": "regression",
        "false_positive": "regression",
        "test_failed": "regression",
        "reverted": "regression",
        "edited": "fresh",
    }

    #: 语义近似阈值。一期用**确定性**的字符 2-gram Jaccard 近似，不引入向量依赖：
    #: 向量聚类属于三期（design §11），提前接进来只会让预检结果不可复现。
    NEAR_DUPLICATE_THRESHOLD = 0.75

    #: 候选池版本命名前缀。
    POOL_VERSION = "candidate-pool"

    # ------------------------------------------------------------------ 入队

    @classmethod
    def enqueue_from_feedback(cls, feedback):
        """确认稿上传、缺陷/漏测/误报/失败/回滚/编辑反馈后入队。"""
        if feedback is None or feedback.signal not in cls.TRIGGER_SIGNALS:
            return None
        if not feedback.output_id:
            # 没有产出就没有"系统当时产出了什么"，只有结论的金标无法复现，不入队。
            return None
        output = feedback.output
        protocol = (output.metadata or {}).get("protocol") or {}
        return cls._enqueue(
            project=feedback.project,
            source_type="feedback",
            source_id=str(feedback.pk),
            signal=feedback.signal,
            payload={
                "output_id": str(output.pk),
                "feedback_id": str(feedback.pk),
                "task_type": output.task_type,
                "workflow_id": str(protocol.get("workflow_id") or ""),
                "stage": str(protocol.get("stage") or output.task_type),
            },
            idempotency_key=f"feedback:{feedback.pk}",
        )

    @classmethod
    def enqueue_from_output(cls, output):
        """正式阶段产出提交后入队；非链路产出（单次能力/知识问答）不入队。"""
        if output is None:
            return None
        protocol = (output.metadata or {}).get("protocol") or {}
        workflow_id = str(protocol.get("workflow_id") or "")
        stage = str(protocol.get("stage") or output.task_type)
        if not workflow_id or stage not in ALL_WORKFLOW_STAGES:
            return None
        return cls._enqueue(
            project=output.project,
            source_type="stage_output",
            source_id=str(output.pk),
            signal="stage_output",
            payload={
                "output_id": str(output.pk),
                "task_type": output.task_type,
                "workflow_id": workflow_id,
                "stage": stage,
            },
            idempotency_key=f"stage-output:{output.pk}",
        )

    @classmethod
    def enqueue_history_candidate(cls, *, project, task_type, source_id, payload=None, actor=None):
        """历史资料导入完成后的入队入口（T08 复用；本任务只提供通道）。"""
        return cls._enqueue(
            project=project,
            source_type="history_import",
            source_id=str(source_id),
            signal="history_import",
            payload={"task_type": task_type, **(payload or {})},
            idempotency_key=f"history-import:{project.pk}:{source_id}",
        )

    @classmethod
    def _enqueue(cls, *, project, source_type, source_id, signal, payload, idempotency_key):
        try:
            with transaction.atomic():
                event, created = AssetCandidateEvent.objects.get_or_create(
                    idempotency_key=idempotency_key,
                    defaults={
                        "project": project,
                        "source_type": source_type,
                        "source_id": str(source_id),
                        "signal": signal,
                        "payload": payload or {},
                        "status": "pending",
                    },
                )
            if created:
                # 与业务写库同一事务：业务回滚则事件也不存在；业务提交后才派发处理。
                transaction.on_commit(lambda: cls.dispatch(event.pk))
            return event
        except Exception:
            # 飞轮入队失败**不得**改变原业务任务结果（R9）。补偿由处理阶段的失败
            # 与死信状态承担；这里只保证不把异常抛回业务入口。
            logger.exception("候选事件入队失败，不影响业务结果")
            return None

    @classmethod
    def dispatch(cls, event_id) -> None:
        """把事件交给异步处理器；派发不出去时记一次失败尝试而不是静默丢弃。

        测试进程里刻意不派发：测试用事务回滚，事件行只存在于测试库，而异步 worker
        读的是生产库——派发既无意义，又会让测试套件依赖 broker 可用性。
        测试显式调用 :meth:`process` 即可，处理逻辑完全一致。
        """
        import sys

        if any(arg == "test" for arg in sys.argv):
            return
        try:
            from .tasks import process_asset_candidate_event

            process_asset_candidate_event.delay(str(event_id))
        except Exception as exc:  # noqa: BLE001
            # 派发本身就失败，也要算作一次失败尝试，否则"事件永远卡在 pending"
            # 会被误读成"还没轮到处理"，而不是"根本没人能处理它"。
            cls._bump_attempts(event_id)
            cls.record_failure(event_id, f"候选事件派发失败：{exc}")

    @staticmethod
    def _bump_attempts(event_id) -> None:
        event = AssetCandidateEvent.objects.filter(pk=event_id).first()
        if event is None:
            return
        event.attempts = (event.attempts or 0) + 1
        event.save(update_fields=["attempts", "updated_at"])

    # ------------------------------------------------------------------ 处理

    @classmethod
    def process_by_id(cls, event_id, *, actor=None):
        event = AssetCandidateEvent.objects.filter(pk=event_id).first()
        if event is None:
            return None
        return cls.process(event, actor=actor)

    @classmethod
    def process(cls, event, *, actor=None):
        """幂等处理：已进入审核队列或已完成的事件不重复建候选。"""
        event = AssetCandidateEvent.objects.filter(pk=event.pk).first()
        if event is None:
            return None
        if event.status in {"needs_review", "completed"}:
            return event
        event.attempts = (event.attempts or 0) + 1
        event.status = "processing"
        event.save(update_fields=["attempts", "status", "updated_at"])
        try:
            case, preflight = cls._materialize(event, actor=actor)
        except Exception as exc:  # noqa: BLE001
            return cls.record_failure(event.pk, f"{type(exc).__name__}: {exc}")
        event.refresh_from_db()
        event.candidate = case
        event.preflight = preflight
        event.last_error = ""
        event.processed_at = timezone.now()
        # 预检存在**阻断项**（如缺陷类信号缺证据）时仍保留候选，但不进审核队列，
        # 缺件清单在 preflight.missing 里，页面据此提示"缺什么才能送审"。
        event.status = "completed" if preflight.get("blocking") else "needs_review"
        event.save(update_fields=[
            "candidate", "preflight", "last_error", "processed_at", "status", "updated_at",
        ])
        return event

    @classmethod
    def record_failure(cls, event_id, message: str):
        """记一次失败：留痕 + 达到阈值落死信，供控制台告警与人工重试。

        **不**在这里累加 ``attempts``：一次处理尝试只在 :meth:`process` 入口（或
        派发失败时）记一次，否则重试三次会被算成六次，死信阈值会提前一半触发。
        """
        event = AssetCandidateEvent.objects.filter(pk=event_id).first()
        if event is None:
            return None
        event.last_error = str(message or "")[:2000]
        event.status = "dead_letter" if (event.attempts or 0) >= (event.max_attempts or 1) else "failed"
        event.save(update_fields=["last_error", "status", "updated_at"])
        if event.status == "dead_letter":
            logger.error(
                "候选事件进入死信，需人工介入：project=%s event=%s error=%s",
                event.project_id, event.pk, event.last_error,
            )
        return event

    @classmethod
    def retry(cls, event, *, actor=None):
        if event is None:
            return None
        if event.status in {"needs_review", "completed"}:
            return event
        return cls.process(event, actor=actor)

    @classmethod
    def retry_failed(cls, *, project_id, statuses=("failed", "dead_letter"), limit=50, actor=None) -> list:
        ids = list(
            AssetCandidateEvent.objects
            .filter(project_id=project_id, status__in=list(statuses))
            .order_by("created_at").values_list("pk", flat=True)[:limit]
        )
        return [
            {"id": str(pk), "status": getattr(cls.process_by_id(pk, actor=actor), "status", "missing")}
            for pk in ids
        ]

    @classmethod
    def status_summary(cls, project_id) -> dict:
        """飞轮控制台使用的候选队列/死信告警概览（项目范围内）。"""
        rows = (
            AssetCandidateEvent.objects.filter(project_id=project_id)
            .values("status").annotate(total=Count("id"))
        )
        counts = {row["status"]: row["total"] for row in rows}
        dead = counts.get("dead_letter", 0)
        failed = counts.get("failed", 0)
        return {
            "project_id": project_id,
            "by_status": counts,
            "pending": counts.get("pending", 0) + counts.get("processing", 0),
            "needs_review": counts.get("needs_review", 0),
            "dead_letter": dead,
            "failed": failed,
            "alert": bool(dead or failed),
        }

    # ------------------------------------------------------------------ 预检

    @classmethod
    def _resolve_source(cls, event):
        payload = event.payload or {}
        output = feedback = None
        if payload.get("output_id"):
            output = GenerationOutput.objects.filter(
                pk=payload["output_id"], project_id=event.project_id,
            ).first()
            if output is None:
                # 只可能是"跨项目引用"或"来源已被清理"。两种情况都必须报错，
                # 不能退化成"用 payload 里的 task_type 猜一个候选"——那会把
                # 别的项目的产出悄悄记到本项目账上。
                raise ValidationError("候选来源产出不属于当前项目或已不存在")
        if payload.get("feedback_id"):
            feedback = FeedbackEvent.objects.filter(
                pk=payload["feedback_id"], project_id=event.project_id,
            ).first()
            if feedback is None:
                raise ValidationError("候选来源反馈不属于当前项目或已不存在")
        if output is None and feedback is not None:
            output = feedback.output
        return output, feedback

    @staticmethod
    def _sanitize_privacy(raw_level, raw_prohibit) -> tuple[str, bool]:
        level = str(raw_level or "internal")
        if level not in {"internal", "restricted", "prohibited"}:
            level = "internal"
        return level, bool(raw_prohibit) or level == "prohibited"

    @classmethod
    def _input_snapshot(cls, *, output, feedback, payload) -> dict:
        """候选输入快照：只留标识与哈希，不复制原始查询/正文（隐私与访问面）。"""
        snapshot = {
            "task_type": (output.task_type if output is not None else payload.get("task_type")) or "",
            "workflow_id": str(payload.get("workflow_id") or ""),
            "stage": str(payload.get("stage") or ""),
        }
        if output is not None:
            snapshot.update({
                "output_id": str(output.pk),
                "trace_id": str(output.trace_id),
                "output_hash": output.output_hash,
                "query_hash": _canonical_hash(output.trace.query),
                "task_id": output.task_id,
            })
        if feedback is not None:
            snapshot["feedback_ids"] = [str(feedback.pk)]
        return snapshot

    @classmethod
    def _checklist(cls, *, input_snapshot, expected, rubric, evidence, privacy_level, signal) -> list:
        items = [
            ("input_snapshot", "输入快照", bool(input_snapshot), False),
            # 期望输出缺失**不是**阻断项：正式阶段产出的候选本来就没有人工答案，
            # 缺件清单就是给审核人"必须补一个期望"的提示。真正的阻断是缺证据。
            ("expected_output", "期望输出", bool(expected), False),
            ("rubric", "评分规则", bool(rubric), False),
            ("source_evidence", "来源证据", bool(evidence), signal in cls.EVIDENCE_REQUIRED_SIGNALS),
            ("privacy_declared", "隐私声明", bool(privacy_level), False),
        ]
        return [
            {"code": code, "label": label, "ok": ok, "blocking": bool(block and not ok)}
            for code, label, ok, block in items
        ]

    @classmethod
    def _fingerprint(cls, project_id, task_type: str, input_snapshot: dict) -> str:
        """精确去重指纹 = 项目 + 任务类型 + 规范化输入。

        只取输入不取期望：正是"同一输入、不同期望"才需要被识别成冲突，
        若把期望也揉进指纹，冲突双方会各自成为一条独立候选，永远碰不上面。
        """
        core = {key: value for key, value in input_snapshot.items()}
        core.pop("feedback_ids", None)
        return _canonical_hash({"project": project_id, "task_type": task_type, "input": core})

    @staticmethod
    def _expected_equal(left, right) -> bool:
        if not left or not right:
            # 期望缺失时不做冲突判定——冲突必须有**两侧**结论才成立。
            return True
        return _canonical_hash(left) == _canonical_hash(right)

    @staticmethod
    def _tokenize(value) -> set:
        """字符 2-gram 词袋：中文没有空格分词，按字切才能得到稳定可比的特征。"""
        text = value if isinstance(value, str) else json.dumps(
            value, ensure_ascii=False, sort_keys=True, default=str,
        )
        tokens: set = set()
        for chunk in re.findall(r"[0-9a-z\u4e00-\u9fff]+", text.lower()):
            if len(chunk) <= 2:
                tokens.add(chunk)
            else:
                tokens.update(chunk[index:index + 2] for index in range(len(chunk) - 1))
        return tokens

    @classmethod
    def _find_existing(cls, *, project, task_type, version, source_hash, fingerprint,
                       input_snapshot, expected):
        """返回 ``(命中案例 | None, 关系)``。

        关系取值：``exact``（同版本同内容）、``same_input``（同输入同期望，合并证据）、
        ``conflict``（同输入不同期望）、``near_equivalent`` / ``near_conflict``（近似）。
        """
        exact = GoldCase.objects.filter(version=version, source_hash=source_hash).first()
        if exact is not None:
            return exact, "exact"

        peers = GoldCase.objects.filter(
            version__dataset__project_id=project.pk, task_type=task_type,
        ).exclude(state="rejected")
        if fingerprint:
            same_input = peers.filter(dedup_fingerprint=fingerprint).order_by("created_at").first()
            if same_input is not None:
                return same_input, (
                    "same_input" if cls._expected_equal(same_input.expected_output, expected) else "conflict"
                )

        tokens = cls._tokenize(input_snapshot)
        if not tokens:
            return None, "new"
        best = None
        best_score = 0.0
        for case in peers.filter(dedup_fingerprint__gt="").order_by("-created_at")[:200]:
            other = cls._tokenize(case.input_snapshot)
            if not other:
                continue
            union = tokens | other
            score = len(tokens & other) / len(union) if union else 0.0
            if score > best_score:
                best, best_score = case, score
        if best is not None and best_score >= cls.NEAR_DUPLICATE_THRESHOLD:
            return best, (
                "near_equivalent" if cls._expected_equal(best.expected_output, expected) else "near_conflict"
            )
        return None, "new"

    @classmethod
    def _recommend(cls, *, task_type, signal, info, has_evidence, output=None) -> dict:
        from .feedback import FeedbackService

        split = cls.SPLIT_BY_SIGNAL.get(signal, "fresh")
        weight = float(FeedbackService.SIGNAL_WEIGHTS.get(signal, 0.5))
        # 优先级只影响"审核人先看哪条"，不是审核结论（R10）。
        score = round(min(1.0, weight * (1.0 if has_evidence else 0.7)), 3)
        tags = [tag for tag in (task_type, signal, info.get("kind")) if tag]
        return {"split": split, "score": score, "tags": tags}

    @classmethod
    def candidate_bucket(cls, *, project, task_type, actor=None) -> GoldDatasetVersion:
        """取（或建）本项目的候选池草稿版本。

        候选必须有地方落：审核通过后才允许创建正式冻结版本，因此在人工确认之前，
        候选统一待在按任务类型分组的"候选池"草稿版本里。已冻结的候选池不再接收
        新样本（``GoldCase`` 自身就禁止改冻结版本），此时滚动出后继版本。
        """
        label = capability_info(task_type)["label"]
        dataset, _ = GoldDataset.objects.get_or_create(
            project=project, name=f"候选池·{label}", task_type=task_type, scope_key="",
            defaults={
                "scope_type": "general",
                "description": "自动沉淀的金标候选池：需人工初标/复核并冻结后才成为正式测试集。",
                "governance": {"auto_pool": True, "policy": "manual_review_required"},
                "created_by": actor if getattr(actor, "pk", None) else None,
            },
        )
        version = dataset.versions.order_by("-created_at").first()
        if version is None or version.state == "frozen":
            index = dataset.versions.count() + 1
            version = GoldDatasetVersion.objects.create(
                dataset=dataset,
                version=cls.POOL_VERSION if index == 1 else f"{cls.POOL_VERSION}.{index}",
                state="draft",
                parent_version=version,
                created_by=actor if getattr(actor, "pk", None) else None,
            )
        return version

    @classmethod
    @transaction.atomic
    def _materialize(cls, event, *, actor=None):
        output, feedback = cls._resolve_source(event)
        payload = event.payload or {}
        task_type = str((output.task_type if output is not None else payload.get("task_type")) or "")
        if not task_type:
            raise ValidationError("候选事件缺少任务类型，无法归属金标资产")
        info = capability_info(task_type)
        protocol = (output.metadata or {}).get("protocol") or {} if output is not None else {}
        privacy = protocol.get("privacy") or {}
        privacy_level, prohibited = cls._sanitize_privacy(
            privacy.get("level"), privacy.get("prohibit_optimization"),
        )

        evidence = list(effective_evidence(feedback)) if feedback is not None else list(
            protocol.get("evidence") or []
        )
        detail = (feedback.detail or {}) if feedback is not None else {}
        expected = detail.get("expected_output") or {}
        rubric = detail.get("rubric") or {}
        input_snapshot = cls._input_snapshot(output=output, feedback=feedback, payload=payload)
        checklist = cls._checklist(
            input_snapshot=input_snapshot, expected=expected, rubric=rubric,
            evidence=evidence, privacy_level=privacy_level, signal=event.signal,
        )
        blocking = [item for item in checklist if item["blocking"]]

        fingerprint = cls._fingerprint(event.project_id, task_type, input_snapshot)
        version = cls.candidate_bucket(project=event.project, task_type=task_type, actor=actor)
        source_hash = _canonical_hash({
            "version": str(version.pk), "fingerprint": fingerprint,
            "expected": _canonical_hash(expected),
        })
        existing, relationship = cls._find_existing(
            project=event.project, task_type=task_type, version=version, source_hash=source_hash,
            fingerprint=fingerprint, input_snapshot=input_snapshot, expected=expected,
        )
        recommendation = cls._recommend(
            task_type=task_type, signal=event.signal, info=info,
            has_evidence=bool(evidence), output=output,
        )
        origin = cls.ORIGIN_BY_SIGNAL.get(event.signal, "manual")
        preflight = {
            "checklist": checklist,
            "missing": [item["code"] for item in checklist if not item["ok"]],
            "blocking": [item["code"] for item in blocking],
            "relationship": relationship,
            "dedup_fingerprint": fingerprint,
            "recommended": {
                "split": recommendation["split"],
                "tags": recommendation["tags"],
                "score": recommendation["score"],
                "dataset_id": str(version.dataset_id),
                "version_id": str(version.pk),
            },
            "privacy": {"level": privacy_level, "prohibited": prohibited},
            "evidence_count": len(evidence),
        }

        if existing is not None:
            case = cls._merge_existing(
                existing=existing, relationship=relationship, evidence=evidence,
                checklist=checklist, preflight=preflight, fingerprint=fingerprint,
                recommendation=recommendation,
            )
        else:
            case = GoldCase.objects.create(
                version=version, source_output=output, source_feedback=feedback,
                task_type=task_type,
                title=f"{info['label']} · {cls._origin_label(event.signal)}",
                input_snapshot=input_snapshot,
                expected_output=expected,
                rubric=rubric,
                evidence=evidence,
                tags=[],
                candidate_origin=origin,
                candidate_score=recommendation["score"],
                recommended_split=recommendation["split"],
                recommended_tags=recommendation["tags"],
                dedup_fingerprint=fingerprint,
                review_checklist={"checklist": checklist, "occurrences": 1,
                                  "missing": preflight["missing"], "relationship": relationship},
                # 推荐分区先落到 ``split``，人工审核时以最终结论覆盖（T05）。
                split=recommendation["split"],
                state="candidate",
                privacy_level=privacy_level,
                allow_optimization=not prohibited,
                source_hash=source_hash,
                created_by=actor if getattr(actor, "pk", None) else None,
            )
            if version.state == "draft":
                version.state = "labeling"
                version.save(update_fields=["state", "updated_at"])
        preflight["candidate_id"] = str(case.pk)
        preflight["candidate_state"] = case.state
        return case, preflight

    @classmethod
    def _merge_existing(cls, *, existing, relationship, evidence, checklist, preflight,
                        fingerprint, recommendation):
        """一致样本合并证据与出现次数；冲突只标记，绝不用新结论覆盖旧结论（R10）。"""
        checklist_state = dict(existing.review_checklist or {})
        occurrences = int(checklist_state.get("occurrences") or 1) + 1
        merged_evidence = list(existing.evidence or [])
        for item in evidence:
            if item not in merged_evidence:
                merged_evidence.append(item)

        updates = ["evidence", "review_checklist", "updated_at"]
        existing.evidence = merged_evidence
        if recommendation["score"] > (existing.candidate_score or 0.0):
            existing.candidate_score = recommendation["score"]
            updates.append("candidate_score")
        if not existing.dedup_fingerprint:
            existing.dedup_fingerprint = fingerprint
            updates.append("dedup_fingerprint")

        checklist_state.update({
            "checklist": checklist,
            "occurrences": occurrences,
            "missing": preflight["missing"],
            "relationship": relationship,
        })
        if relationship in {"conflict", "near_conflict"}:
            conflicts = list(checklist_state.get("conflicts") or [])
            marker = {
                "against_case_id": str(existing.pk),
                "relationship": relationship,
                "reason": "同一输入存在不同期望结论，必须人工仲裁",
            }
            if marker not in conflicts:
                conflicts.append(marker)
            checklist_state["conflicts"] = conflicts
            # ``conflict`` 是 GoldCase 的"待仲裁"态：它不会被冻结门禁放行，
            # 也不会被当成"最新上传即正确"。
            existing.state = "conflict"
            updates.append("state")
        existing.review_checklist = checklist_state
        existing.save(update_fields=list(dict.fromkeys(updates)))
        return existing

    @staticmethod
    def _origin_label(signal: str) -> str:
        return {
            "defect_confirmed": "确认缺陷",
            "test_failed": "测试失败",
            "missed": "漏测",
            "false_positive": "误报",
            "reverted": "回滚",
            "edited": "人工编辑",
            "stage_output": "正式阶段产出",
            "history_import": "历史资料",
        }.get(signal, signal or "候选")


class GoldAnnotationService:
    """人工审核：初标、复核、冲突仲裁（T05）。

    角色门禁在视图层（``views``）：复核 / 仲裁 / 冻结仅测试负责人可用。服务层不
    重复判角色——它同时被管理命令与异步任务调用，那里没有 ``request.user``；但服务层
    **必须**独自保证"没有人工审核的样本进不了已确认、进不了冻结版本"这条不变量。
    """

    @staticmethod
    def _snapshot(case, annotation) -> dict:
        """记录审核时刻的指纹，回答"人当时审的就是这份材料"。

        样本的 ``input_snapshot`` / ``expected_output`` 之后可能被后继版本复用或
        来源产出被更新；只存审核结论而没有"当时看见的是什么"，事后无法自证。
        """
        return {
            "captured_at": timezone.now().isoformat(),
            "round": annotation.round,
            "input_hash": _canonical_hash(case.input_snapshot),
            "source_hash": case.source_hash,
            "answer_hash": _canonical_hash(annotation.answer),
            "evidence_hash": _canonical_hash(annotation.evidence),
            "split": annotation.split or case.split,
            "category": annotation.category or "",
            "tags": list(annotation.tags or case.tags or []),
        }

    @staticmethod
    def _audit(case, annotation, *, from_state, to_state, action="annotate") -> None:
        """写审核审计：谁、哪一轮、从什么状态到什么状态、理由与治理结论（R9/T05）。"""
        from .knowledge_models import KnowledgeAuditLog

        KnowledgeAuditLog.record(
            project_id=case.version.dataset.project_id,
            action=action,
            entity=case,
            actor=annotation.annotator,
            from_state=from_state,
            to_state=to_state,
            reason=annotation.comment or annotation.conclusion,
            detail={
                "round": annotation.round,
                "conclusion": annotation.conclusion,
                "split": annotation.split,
                "category": annotation.category,
                "tags": list(annotation.tags or []),
                "review_snapshot": annotation.review_snapshot,
            },
        )

    @staticmethod
    @transaction.atomic
    def submit(*, case: GoldCase, round_name: str, actor, answer, rubric_scores,
               evidence, conclusion: str, comment="", tags=None, split="",
               category="") -> GoldAnnotation:
        if case.version.state == "frozen":
            raise ValidationError("冻结版本不能继续标注")
        if round_name not in {"primary", "review"}:
            raise ValidationError("普通标注只支持初标或复核")
        if split and split not in dict(GoldCase.SPLIT_CHOICES):
            raise ValidationError(f"未知的分区：{split}")
        primary = case.annotations.filter(round="primary").first()
        if round_name == "review":
            if not primary:
                raise ValidationError("必须先完成初标")
            if primary.annotator_id == actor.id:
                raise ValidationError("复核人不能与初标人相同")
        from_state = case.state
        annotation = GoldAnnotation.objects.create(
            case=case, round=round_name, answer=answer or {},
            rubric_scores=rubric_scores or {}, evidence=evidence or [],
            conclusion=conclusion, comment=comment, annotator=actor,
            tags=list(tags or []), split=split or "", category=category or "",
        )
        annotation.review_snapshot = GoldAnnotationService._snapshot(case, annotation)
        annotation.save(update_fields=["review_snapshot"])
        case.state = "labeling"
        case.save(update_fields=["state", "updated_at"])
        if round_name == "review":
            GoldAnnotationService._compare(case, primary, annotation)
        case.refresh_from_db(fields=["state"])
        GoldAnnotationService._audit(
            case, annotation, from_state=from_state, to_state=case.state,
        )
        return annotation

    @staticmethod
    def _compare(case, primary, review):
        differing = []
        for field in ("answer", "rubric_scores", "evidence", "conclusion"):
            if getattr(primary, field) != getattr(review, field):
                differing.append(field)
        if differing:
            AnnotationConflict.objects.update_or_create(
                case=case,
                defaults={
                    "primary_annotation": primary,
                    "review_annotation": review,
                    "differing_fields": differing,
                    "state": "open",
                },
            )
            case.state = "conflict"
            case.save(update_fields=["state", "updated_at"])
            return
        GoldAnnotationService._apply_final(case, review)

    @staticmethod
    def _apply_final(case, annotation):
        case.state = "confirmed" if annotation.conclusion == "accepted" else "rejected"
        case.expected_output = annotation.answer
        case.rubric = annotation.rubric_scores
        case.evidence = annotation.evidence
        update = ["state", "expected_output", "rubric", "evidence", "updated_at"]
        # 治理结论只在人工显式给出时落地：字段为空表示"这一轮没动它"。
        # 不能因为传了个空值就把候选预检的建议当成人工结论写死——那会让
        # "人工结论"与"系统建议"在库里变成同一个来源（T05 / T07 要求两者可区分）。
        if annotation.split:
            case.split = annotation.split
            update.append("split")
        if annotation.tags:
            case.tags = annotation.tags
            update.append("tags")
        case.save(update_fields=update)

    @staticmethod
    @transaction.atomic
    def resolve(*, conflict: AnnotationConflict, actor, answer, rubric_scores,
                evidence, conclusion: str, comment="", tags=None, split="",
                category="") -> GoldAnnotation:
        if conflict.state != "open":
            raise ValidationError("该冲突已经处理")
        if split and split not in dict(GoldCase.SPLIT_CHOICES):
            raise ValidationError(f"未知的分区：{split}")
        case = conflict.case
        from_state = case.state
        annotation = GoldAnnotation.objects.create(
            case=case, round="arbitration", answer=answer or {},
            rubric_scores=rubric_scores or {}, evidence=evidence or [],
            conclusion=conclusion, comment=comment, annotator=actor,
            tags=list(tags or []), split=split or "", category=category or "",
        )
        annotation.review_snapshot = GoldAnnotationService._snapshot(case, annotation)
        annotation.save(update_fields=["review_snapshot"])
        conflict.state = "resolved"
        conflict.resolution = {
            "annotation_id": str(annotation.id),
            "conclusion": conclusion,
        }
        conflict.resolved_by = actor
        conflict.resolved_at = timezone.now()
        conflict.save(update_fields=["state", "resolution", "resolved_by", "resolved_at"])
        GoldAnnotationService._apply_final(case, annotation)
        case.refresh_from_db(fields=["state"])
        GoldAnnotationService._audit(
            case, annotation, from_state=from_state, to_state=case.state,
            action="conflict_resolved",
        )
        return annotation


class GoldVersionService:
    """数据集版本冻结（T05）。

    冻结是**不可逆**动作（此后只能退役），所以放行条件必须是可枚举、可解释的：
    每一条不满足都明确说出缺什么，而不是笼统地回一句"数据不完整"。
    """

    @staticmethod
    def _missing_critical_scenarios(taxonomy, cases) -> list[str]:
        """关键场景覆盖判定。

        口径：场景 ``key`` 出现在至少一条样本的 ``tags`` 里即算覆盖。锚在 ``tags``
        而不是分类 key，是因为分类回答"样本属于哪一类"、关键场景回答"这版数据必须
        能考到哪些业务路径"，两者是多对多关系——用分类反推会漏掉跨类场景。
        """
        covered = set()
        for case in cases:
            covered.update(str(item) for item in (case.tags or []))
        missing = []
        for scenario in taxonomy.critical_scenarios or []:
            if isinstance(scenario, dict):
                key = scenario.get("key")
                label = scenario.get("name") or key
            else:
                key = scenario
                label = scenario
            if key and str(key) not in covered:
                missing.append(str(label))
        return missing

    @staticmethod
    @transaction.atomic
    def freeze(*, version: GoldDatasetVersion, actor) -> GoldDatasetVersion:
        # 只 select_related 非空外键：``taxonomy_version`` 可空，PostgreSQL 不允许
        # 把 FOR UPDATE 施加在外连接的可空侧（NotSupportedError）。分类版本改为
        # 按需读取，反正冻结路径只访问一次。
        version = (
            GoldDatasetVersion.objects.select_for_update()
            .select_related("dataset")
            .get(pk=version.pk)
        )
        if version.state == "frozen":
            return version
        from_state = version.state
        cases = list(version.cases.order_by("id"))
        if not cases:
            raise ValidationError("空数据集版本不能冻结")
        invalid = [case for case in cases if case.state != "confirmed"]
        if invalid:
            raise ValidationError(f"仍有 {len(invalid)} 条样本未确认")
        # 显式查未解决冲突：只看 ``case.state`` 会漏掉"结论已定、争议未结"——
        # 复核与初标不一致时案例会转 conflict，但若此后由人工以 modify-approve
        # 之外的方式改过状态，冲突记录仍可能停在 open。
        open_conflicts = AnnotationConflict.objects.filter(case__version=version, state="open")
        if open_conflicts.exists():
            raise ValidationError(f"仍有 {open_conflicts.count()} 条冲突未仲裁")
        if any(case.privacy_level == "prohibited" and case.allow_optimization for case in cases):
            raise ValidationError("存在隐私策略不一致的样本")

        dataset = version.dataset
        taxonomy = dataset.taxonomy_version
        if taxonomy is None:
            # 通用数据集允许不绑定分类；"业务专用"必须绑定，否则"专用"二字
            # 没有可核对的业务范围，评测结论也无法回溯到分类版本。
            if dataset.scope_type == "domain":
                raise ValidationError("业务专用数据集必须绑定已发布的分类版本才能冻结")
        else:
            if taxonomy.state != "published":
                raise ValidationError(
                    "关联的分类版本当前为「"
                    f"{taxonomy.get_state_display()}」，只有已发布的分类版本才能冻结数据集"
                )
            missing = GoldVersionService._missing_critical_scenarios(taxonomy, cases)
            if missing:
                raise ValidationError("关键场景未覆盖：" + "、".join(missing))

        governance_snapshot = {
            "taxonomy_version_id": str(taxonomy.id) if taxonomy is not None else None,
            "taxonomy_version": taxonomy.version if taxonomy is not None else "",
            "scope_type": dataset.scope_type,
            "scope_key": dataset.scope_key,
            "task_type": dataset.task_type,
            "governance": dataset.governance,
            "privacy_levels": dict(sorted(Counter(case.privacy_level for case in cases).items())),
            "optimization_allowed": all(case.allow_optimization for case in cases),
            "captured_at": timezone.now().isoformat(),
        }
        snapshot = [
            {
                "id": str(case.id), "source_hash": case.source_hash,
                "task_type": case.task_type, "split": case.split,
                "tags": list(case.tags or []),
                "expected_hash": _canonical_hash(case.expected_output),
                "rubric_hash": _canonical_hash(case.rubric),
                "evidence_hash": _canonical_hash(case.evidence),
                "privacy_level": case.privacy_level,
                "allow_optimization": case.allow_optimization,
            }
            for case in cases
        ]
        split_counts = Counter(case.split for case in cases)
        task_counts = Counter(case.task_type for case in cases)
        # 治理策略进哈希：这样"内容没动、用途被改"是可检测的，而不是只靠字段保护。
        version.content_hash = _canonical_hash(
            {"cases": snapshot, "governance": governance_snapshot}
        )
        version.governance_snapshot = governance_snapshot
        version.sample_stats = {
            "total": len(cases),
            "splits": dict(sorted(split_counts.items())),
            "task_types": dict(sorted(task_counts.items())),
        }
        version.state = "frozen"
        version.frozen_by = actor
        version.frozen_at = timezone.now()
        # 首次冻结时模型保护逻辑允许从非 frozen 状态进入 frozen。
        version.save(update_fields=[
            "content_hash", "governance_snapshot", "sample_stats", "state",
            "frozen_by", "frozen_at", "updated_at",
        ])

        from .knowledge_models import KnowledgeAuditLog

        KnowledgeAuditLog.record(
            project_id=dataset.project_id,
            action="freeze",
            entity=version,
            actor=actor,
            from_state=from_state,
            to_state="frozen",
            reason="人工冻结数据集版本",
            detail={
                "dataset_id": str(dataset.id),
                "total": len(cases),
                "governance_snapshot": governance_snapshot,
            },
        )
        return version


class GoldCatalogService:
    """按能力与评测分区组织金标资产（T10 / R10）。

    为什么不再让页面自己 group by：能力分类口径、必需分区、可用性判定这三件事
    在别处已经有了唯一真值（``capability_registry`` / ``evaluation_gates``）。
    页面若各自 group by，就会出现"金标页认为某分区是必须的、门禁页却不这么认为"。
    """

    @staticmethod
    def overview(project_id) -> dict:
        from .capability_registry import ALL_TASK_TYPES, PARTITION_ORDER_SAFE, capability_info
        from .gold_models import GoldDataset

        datasets = list(
            GoldDataset.objects.filter(project_id=project_id)
            .prefetch_related("versions__cases")
            .order_by("task_type", "name")
        )
        by_capability = {}
        for task_type in ALL_TASK_TYPES:
            info = capability_info(task_type)
            by_capability[task_type] = {
                "task_type": task_type,
                "label": info["label"],
                "kind": info["kind"],
                "mode": info["mode"],
                "required_partitions": list(info["partitions"]),
                "datasets": [],
            }

        for dataset in datasets:
            bucket = by_capability.setdefault(dataset.task_type, {
                "task_type": dataset.task_type, "label": dataset.task_type,
                "kind": "feedback_source", "mode": "single",
                "required_partitions": [], "datasets": [],
            })
            latest = None
            for version in dataset.versions.all():
                if latest is None or version.created_at > latest.created_at:
                    latest = version
            splits = {}
            if latest is not None:
                counts = Counter(case.split for case in latest.cases.all())
                splits = dict(sorted(counts.items()))
            bucket["datasets"].append({
                "id": str(dataset.id),
                "name": dataset.name,
                "status": dataset.status,
                "latest_version": latest.version if latest else "",
                "latest_state": latest.state if latest else "",
                "case_count": latest.cases.count() if latest else 0,
                "splits": splits,
                # 冻结版本才有 content_hash，才能被 GoldReplayService 回放。
                "replayable": bool(latest and latest.state == "frozen" and latest.content_hash),
                "missing_partitions": [
                    item for item in (bucket["required_partitions"] if latest else [])
                    if splits.get(item, 0) == 0
                ],
            })

        covered = [item for item in by_capability.values() if item["datasets"]]
        # 能力级的缺口：该能力**必需**的分区在整个项目里一条样本都没有。
        # 与数据集级缺口分开报：数据集缺某项说明"这个集不全"，
        # 能力缺某项说明"这个能力的门禁根本跑不起来"——后果完全不同。
        for item in by_capability.values():
            covered_splits = set()
            for dataset in item["datasets"]:
                covered_splits.update(
                    split for split, count in (dataset["splits"] or {}).items() if count
                )
            item["missing_partitions"] = [
                split for split in item["required_partitions"]
                if split not in covered_splits
            ]
        return {
            "project_id": project_id,
            "partition_order": list(PARTITION_ORDER_SAFE),
            "capabilities": by_capability,
            "covered_capability_count": len(covered),
            "total_capability_count": len(ALL_TASK_TYPES),
        }
