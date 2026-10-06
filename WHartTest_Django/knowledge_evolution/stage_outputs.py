"""``stage-result/v1`` 运行时校验、Skill 兼容等级与阶段产出适配。

这里解决的是**协议层**的问题，不是业务层的问题：

- 业务 Skill 照旧产出自己的 Excel / PDF / 日志，平台不统一它们；
- 平台只额外要求一份机器可读的 ``stage_result.json``，凭它做评审、图谱与归因；
- 于是「这个 Skill 能被平台用到什么程度」变成一个可以计算的东西，就是 L0–L3。

两条边界写死在实现里，不靠调用方自觉：

1. **协议失败不动业务文件**。校验不通过时只返回「结构化协议失败」的标记，
   绝不去删除或改名 Skill 已经产出的业务产物——业务产出能不能用，和协议合不合规
   是两回事，前者不该被后者连坐。
2. **等级以产出实测为准，声明只是声明**。Skill 在 manifest 里写 ``L3`` 不算数，
   要看 ``stage_result.json`` 里是否真有需求、证据和决策记录。声明高于实测时
   给出 ``declared_level_not_met``，让"声称能进化、实际不能归因"这件事在
   Skill Hub 上直接可见，而不是等到派生补丁时才发现没料可依。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# 协议常量
# ---------------------------------------------------------------------------

#: 协议版本真值。校验器只认这一版；不认识的值不报错，按 L0 处理
#: （见 ``validate_stage_result`` 的版本分流）——因为一个新版 Skill 被搬到旧平台
#: 上跑，报错中断会让它连业务产物都交不出来，而降级处理至少业务照常。
SCHEMA_VERSION = "stage-result/v1"

#: Skill 包内应提交的文件名。
STAGE_RESULT_FILENAME = "stage_result.json"

#: Skill 包内 schema 存放在 ``knowledge_evolution/schemas/`` 下。
SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"
SCHEMA_FILE = SCHEMA_DIR / "stage_result_v1.json"


# ---------------------------------------------------------------------------
# 兼容等级真值（模块级 —— 写成类属性会在 import 期炸，且 ``manage.py check`` 碰不到）
# ---------------------------------------------------------------------------

LEVEL_L0 = "L0"
LEVEL_L1 = "L1"
LEVEL_L2 = "L2"
LEVEL_L3 = "L3"

#: 由低到高。顺序即能力包含关系：高等级天然满足低等级。
COMPATIBILITY_LEVELS: tuple[str, ...] = (LEVEL_L0, LEVEL_L1, LEVEL_L2, LEVEL_L3)

LEVEL_RANK: dict[str, int] = {level: index for index, level in enumerate(COMPATIBILITY_LEVELS)}

COMPATIBILITY_LABELS: dict[str, str] = {
    LEVEL_L0: "L0 普通文件",
    LEVEL_L1: "L1 可识别产物",
    LEVEL_L2: "L2 可评审产物",
    LEVEL_L3: "L3 可进化产物",
}

COMPATIBILITY_CAPABILITIES: dict[str, str] = {
    LEVEL_L0: "可下载，无结构化评审",
    LEVEL_L1: "明确主产物，不再靠文件名猜",
    LEVEL_L2: "提供结构化 items，可生成确认报告",
    LEVEL_L3: "提供需求、证据和决策记录，可归因和派生补丁",
}

#: manifest 里声明等级的键。T06 给 e 投票方案 Skill 加的就是它。
DECLARED_LEVEL_KEY = "stage_result_level"

#: 结果标记：结构化协议失败。与业务生成失败是**两个不同的东西**，
#: 页面上必须分开显示——「Skill 跑失败了」和「Skill 跑成功了但信封不合规」
#: 对应的补救动作完全不同（前者重跑，后者改 Skill 或改产出）。
RESULT_OK = "ok"
RESULT_STRUCTURED_FAILURE = "structured_protocol_failure"
RESULT_BUSINESS_FAILURE = "business_failure"


def is_known_level(value: Any) -> bool:
    return str(value or "").strip().upper() in LEVEL_RANK


def normalize_level(value: Any) -> str:
    """把输入的等级归一成 ``L0``–``L3``；不认识的一律当 ``L0``。

    为什么不抛错：等级是**描述**而不是**开关**。一个拼错的等级不该让 Skill
    无法运行，只该让它退回最低档、在页面上显示成 L0 让人看见。
    """
    text = str(value or "").strip().upper()
    return text if text in LEVEL_RANK else LEVEL_L0


def level_at_least(level: str, floor: str) -> bool:
    return LEVEL_RANK.get(normalize_level(level), 0) >= LEVEL_RANK.get(normalize_level(floor), 0)


def level_label(level: str) -> str:
    level = normalize_level(level)
    return COMPATIBILITY_LABELS.get(level, level)


# ---------------------------------------------------------------------------
# 校验结果
# ---------------------------------------------------------------------------


@dataclass
class StageResultIssue:
    """一条具体的字段级错误。

    ``path`` 用 JSON Pointer 风格（``/items/2/id``），因为验收要求"返回具体字段错误"——
    只说"schema 校验失败"等于把定位工作全推回给 Skill 作者。
    """

    path: str
    message: str
    code: str = "invalid"

    def as_dict(self) -> dict[str, str]:
        return {"path": self.path, "message": self.message, "code": self.code}


@dataclass
class StageResultValidation:
    """一次校验的完整结论。"""

    ok: bool
    #: 实测等级——由产出内容算出来，不看声明。
    level: str = LEVEL_L0
    #: Skill 自己声明的等级（来自 manifest）。空表示没声明。
    declared_level: str = ""
    #: 平台实际可以按哪一档对待。声明高于实测时取实测，避免"声称 L3 却无法归因"。
    effective_level: str = LEVEL_L0
    issues: list[StageResultIssue] = field(default_factory=list)
    payload: dict[str, Any] | None = None
    detail: str = ""

    @property
    def structured_failure(self) -> bool:
        return not self.ok

    @property
    def level_gap(self) -> bool:
        """声明等级高于实测等级。不是硬错误，但要显式暴露。"""
        if not self.declared_level:
            return False
        return LEVEL_RANK.get(self.declared_level, 0) > LEVEL_RANK.get(self.level, 0)

    @property
    def result_marker(self) -> str:
        return RESULT_OK if self.ok else RESULT_STRUCTURED_FAILURE

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "schema_version": SCHEMA_VERSION,
            "level": self.level,
            "level_label": level_label(self.level),
            "declared_level": self.declared_level,
            "effective_level": self.effective_level,
            "effective_level_label": level_label(self.effective_level),
            "level_gap": self.level_gap,
            "result_marker": self.result_marker,
            "issues": [issue.as_dict() for issue in self.issues],
            "detail": self.detail,
        }


# ---------------------------------------------------------------------------
# Schema 读取
# ---------------------------------------------------------------------------


def load_schema() -> dict[str, Any]:
    """读取 ``stage-result/v1`` JSON Schema。

    ``jsonschema`` 延迟导入：它只在真正校验时才需要。飞轮里大量路径只是查询
    （例如 Skill Hub 列表要显示等级标签），不该为了显示一个标签就把 schema 库
    拉进 import 图。
    """
    try:
        from jsonschema import Draft7Validator  # noqa: F401  仅做可用性探测
    except ImportError as exc:  # pragma: no cover - 依赖缺失时的显式报错
        raise RuntimeError("缺少 jsonschema 依赖，无法校验 stage-result/v1") from exc

    with SCHEMA_FILE.open("r", encoding="utf-8") as handle:
        return json.load(handle)


# ---------------------------------------------------------------------------
# 结构化引用与等级判定
# ---------------------------------------------------------------------------


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _non_empty_str(value: Any) -> str:
    return str(value or "").strip()


def classify_level(payload: Any) -> str:
    """按产出内容实测兼容等级。

    判据完全落在"平台接下来能做什么"上，不放任何主观项：

    - 没有信封 / 没有主产物声明 → L0：平台只能提供下载，不知道哪个文件是正主。
    - 声明了主产物但没有可用 items → L1：知道正主是谁，但没法逐项评审。
    - 有带稳定 ID 的 items → L2：能生成确认报告、能逐项比对人工修改。
    - items 挂得上需求与**可复核**证据、且有显式决策记录 → L3：
      平台能沿"人工修改 → 业务项 → 证据 → 决策"反查到 Skill 规则，才谈得上派生补丁。
    """
    if not isinstance(payload, dict) or not payload:
        return LEVEL_L0

    if not _as_list(payload.get("primary_artifacts")):
        return LEVEL_L0

    items = [item for item in _as_list(payload.get("items")) if isinstance(item, dict)]
    if not items:
        return LEVEL_L1

    # 没有稳定 ID 的 items 无法做差异比对：重跑一次整份方案都会被算成全新内容，
    # 所以停在 L1 而不是"凑合当 L2 用"。
    if any(not _non_empty_str(item.get("id")) for item in items):
        return LEVEL_L1

    if _has_traceable_evidence(payload, items) and _has_decision_records(payload, items):
        return LEVEL_L3
    return LEVEL_L2


def _has_traceable_evidence(payload: dict, items: list[dict]) -> bool:
    evidence = [entry for entry in _as_list(payload.get("evidence")) if isinstance(entry, dict)]
    if not evidence:
        return False
    by_id = {_non_empty_str(entry.get("id")): entry for entry in evidence}
    for item in items:
        requirement_ids = [rid for rid in _as_list(item.get("requirement_ids")) if _non_empty_str(rid)]
        evidence_ids = [eid for eid in _as_list(item.get("evidence_ids")) if _non_empty_str(eid)]
        if not requirement_ids or not evidence_ids:
            return False
        for eid in evidence_ids:
            entry = by_id.get(eid)
            if entry is None:
                return False
            # 定位三要素缺一不可：只有 quote 没有 chunk/版本，回到原文这一步就做不了，
            # 那 L3 承诺的"证据反查"是空的。
            if not _non_empty_str(entry.get("document_id")):
                return False
            if not _non_empty_str(entry.get("chunk_id")):
                return False
            if not _non_empty_str(entry.get("quote")):
                return False
    return True


def _has_decision_records(payload: dict, items: list[dict]) -> bool:
    if any(_as_list(item.get("decisions")) for item in items):
        return True
    return bool(_as_list(payload.get("decision_summary")))


def read_declared_level(manifest: Any) -> str:
    """从 SkillVersion.manifest 里读声明等级。

    兼容两种写法：``stage_result_level`` 直写，或 ``stage_result: {level: L3}``。
    取不到就返回空串（表示"未声明"），而不是 L0——"没声明"和"声明了 L0"
    在 Skill Hub 上要显示成不同的话：前者是需要补填，后者是明确不打算结构化。
    """
    if not isinstance(manifest, dict):
        return ""
    direct = _non_empty_str(manifest.get(DECLARED_LEVEL_KEY))
    if direct:
        return normalize_level(direct)
    nested = manifest.get("stage_result")
    if isinstance(nested, dict):
        value = _non_empty_str(nested.get("level"))
        if value:
            return normalize_level(value)
    return ""


# ---------------------------------------------------------------------------
# 校验入口
# ---------------------------------------------------------------------------


def _dedupe_issues(issues: list[StageResultIssue]) -> list[StageResultIssue]:
    """同一条错误可能被 schema 与语义两遍都报出来，去重但保持顺序。"""
    seen: set[tuple[str, str]] = set()
    result: list[StageResultIssue] = []
    for issue in issues:
        key = (issue.path, issue.message)
        if key in seen:
            continue
        seen.add(key)
        result.append(issue)
    return result


def _schema_issues(payload: dict) -> list[StageResultIssue]:
    from jsonschema import Draft7Validator

    validator = Draft7Validator(load_schema())
    issues: list[StageResultIssue] = []
    for error in sorted(validator.iter_errors(payload), key=lambda item: list(item.absolute_path)):
        path = "/" + "/".join(str(part) for part in error.absolute_path)
        if path == "/":
            path = ""
        # 顶层 required 的报错路径是空的，补上字段名，否则"缺 primary_artifacts"
        # 这条错误在页面上会显示成一个没有落点的空路径。
        if not error.absolute_path and error.validator == "required":
            missing = error.message.split("'")[1] if "'" in error.message else ""
            path = f"/{missing}" if missing else ""
        issues.append(StageResultIssue(path=path, message=error.message, code="schema"))
    return issues


def _semantic_issues(payload: dict, *, expected_stage: str = "") -> list[StageResultIssue]:
    """JSON Schema 表达不了的跨引用检查。

    Schema 能保证 ``items[].id`` 是个合法字符串，但保证不了**两份** items 的 id
    不重复、也保证不了 ``evidence_ids`` 指向的证据真的存在。这些才是"非法引用"
    的实际来源，必须单独查。
    """
    issues: list[StageResultIssue] = []

    actual_stage = _non_empty_str(payload.get("stage"))
    if expected_stage and actual_stage and actual_stage != expected_stage:
        issues.append(StageResultIssue(
            path="/stage",
            message=f"阶段不一致：产出声明 {actual_stage}，当前流程阶段是 {expected_stage}",
            code="stage_mismatch",
        ))

    items = [item for item in _as_list(payload.get("items")) if isinstance(item, dict)]
    seen_item_ids: set[str] = set()
    for index, item in enumerate(items):
        item_id = _non_empty_str(item.get("id"))
        if not item_id:
            # schema 已经会报 required，这里补一条更直白的说明，让人知道"为什么
            # 这个字段是必填"——因为它是差异比对的主键。
            issues.append(StageResultIssue(
                path=f"/items/{index}/id",
                message="缺少稳定业务项 ID；没有它，重跑时整份方案会被算成全新内容",
                code="missing_stable_id",
            ))
            continue
        if item_id in seen_item_ids:
            issues.append(StageResultIssue(
                path=f"/items/{index}/id",
                message=f"业务项 ID 重复：{item_id}",
                code="duplicate_item_id",
            ))
        seen_item_ids.add(item_id)

    evidence = [entry for entry in _as_list(payload.get("evidence")) if isinstance(entry, dict)]
    known_evidence: set[str] = set()
    for index, entry in enumerate(evidence):
        entry_id = _non_empty_str(entry.get("id"))
        if not entry_id:
            issues.append(StageResultIssue(
                path=f"/evidence/{index}/id",
                message="证据缺少 ID，无法被 items 引用",
                code="missing_evidence_id",
            ))
            continue
        if entry_id in known_evidence:
            issues.append(StageResultIssue(
                path=f"/evidence/{index}/id",
                message=f"证据 ID 重复：{entry_id}",
                code="duplicate_evidence_id",
            ))
        known_evidence.add(entry_id)

    requirements = [entry for entry in _as_list(payload.get("requirements")) if isinstance(entry, dict)]
    known_requirements = {_non_empty_str(entry.get("id")) for entry in requirements if _non_empty_str(entry.get("id"))}
    check_requirements = bool(requirements)

    for index, item in enumerate(items):
        for ref_index, ref in enumerate(_as_list(item.get("evidence_ids"))):
            ref_id = _non_empty_str(ref)
            if ref_id and ref_id not in known_evidence:
                issues.append(StageResultIssue(
                    path=f"/items/{index}/evidence_ids/{ref_index}",
                    message=f"引用了不存在的证据：{ref_id}",
                    code="unknown_evidence_reference",
                ))
        if not check_requirements:
            continue
        for ref_index, ref in enumerate(_as_list(item.get("requirement_ids"))):
            ref_id = _non_empty_str(ref)
            if ref_id and ref_id not in known_requirements:
                issues.append(StageResultIssue(
                    path=f"/items/{index}/requirement_ids/{ref_index}",
                    message=f"引用了不存在的需求：{ref_id}",
                    code="unknown_requirement_reference",
                ))

    return issues


def validate_stage_result(
    payload: Any,
    *,
    expected_stage: str = "",
    declared_level: str = "",
) -> StageResultValidation:
    """校验一份 ``stage_result.json``。

    ``payload`` 允许是 dict、JSON 字符串或 bytes，因为三个来源都会走到这里：
    Skill 直接写文件（读出来是 str/bytes）、平台侧已解析的对象、以及测试里的字面量。
    """
    declared = normalize_level(declared_level) if is_known_level(declared_level) else ""
    raw, parse_issue = _coerce_payload(payload)
    if parse_issue is not None:
        return StageResultValidation(
            ok=False,
            level=LEVEL_L0,
            declared_level=declared,
            effective_level=LEVEL_L0,
            issues=[parse_issue],
            payload=None,
            detail="stage_result.json 无法解析为 JSON 对象",
        )

    if not isinstance(raw, dict):
        return StageResultValidation(
            ok=False,
            level=LEVEL_L0,
            declared_level=declared,
            effective_level=LEVEL_L0,
            issues=[StageResultIssue(path="", message="stage_result 顶层必须是 JSON 对象", code="not_an_object")],
            payload=None,
            detail="stage_result.json 顶层不是对象",
        )

    version = _non_empty_str(raw.get("schema_version"))
    if version and version != SCHEMA_VERSION:
        # 不认识的协议版本按 L0 处理：业务产物照旧可下载，只是平台不做结构化解析。
        # 报错中断反而会让"新版 Skill 搬到旧平台"直接跑不起来。
        return StageResultValidation(
            ok=True,
            level=LEVEL_L0,
            declared_level=declared,
            effective_level=LEVEL_L0,
            issues=[],
            payload=raw,
            detail=f"未知协议版本 {version}，按 L0 处理（业务产物不受影响）",
        )

    issues = _schema_issues(raw) + _semantic_issues(raw, expected_stage=expected_stage)
    issues = _dedupe_issues(issues)
    measured = classify_level(raw) if not issues else LEVEL_L0
    effective = measured if not declared else min(
        (measured, declared), key=lambda level: LEVEL_RANK[level]
    )

    validation = StageResultValidation(
        ok=not issues,
        level=measured,
        declared_level=declared,
        effective_level=effective,
        issues=issues,
        payload=raw,
        detail="" if not issues else f"stage-result 校验未通过，共 {len(issues)} 项字段问题",
    )
    if validation.ok and declared:
        # 声明与实测不一致时给一条可展示的说明，但**不算失败**——等级是承诺，
        # 不是门槛；把偏差显示出来就够了，不必让这次运行变成错误。
        if validation.level_gap:
            validation.detail = (
                f"声明等级 {declared} 未达成，实测仅 {measured}；"
                "归因与派生不会基于本次产出进行"
            )
        elif LEVEL_RANK[measured] > LEVEL_RANK[declared]:
            validation.detail = (
                f"产出实测达到 {measured}，但 Skill 仅声明 {declared}；"
                f"按 {declared} 使用（作者未承诺跨版本保持稳定 ID 与证据定位）"
            )
    return validation


def _coerce_payload(payload: Any) -> tuple[Any, StageResultIssue | None]:
    if isinstance(payload, (bytes, bytearray)):
        try:
            payload = payload.decode("utf-8")
        except UnicodeDecodeError:
            return None, StageResultIssue(path="", message="stage_result.json 不是有效 UTF-8", code="bad_encoding")
    if isinstance(payload, str):
        try:
            return json.loads(payload), None
        except json.JSONDecodeError as exc:
            return None, StageResultIssue(
                path="", message=f"JSON 解析失败：{exc.msg}（第 {exc.lineno} 行）", code="invalid_json",
            )
    return payload, None


def read_stage_result_file(path_or_bytes: Any, **kwargs) -> StageResultValidation:
    """从文件路径或原始字节校验，供运行时调用。

    协议失败时**只返回结论**，不触碰磁盘——业务主产物是 Skill 的交付物，
    不该因为信封不合规被删掉或改名。
    """
    if isinstance(path_or_bytes, (bytes, bytearray, str)) and not _looks_like_path(path_or_bytes):
        return validate_stage_result(path_or_bytes, **kwargs)
    path = Path(str(path_or_bytes))
    if not path.exists():
        return StageResultValidation(
            ok=False,
            declared_level=normalize_level(kwargs.get("declared_level", "")) if kwargs.get("declared_level") else "",
            issues=[StageResultIssue(path="", message="未找到 stage_result.json", code="missing_file")],
            detail="Skill 未提交 stage_result.json",
        )
    return validate_stage_result(path.read_bytes(), **kwargs)


def _looks_like_path(value: Any) -> bool:
    if isinstance(value, (bytes, bytearray)):
        return False
    text = str(value)
    return "\n" not in text and "{" not in text and text.endswith(".json")


# ---------------------------------------------------------------------------
# 归一化产出与适配器接口（T07 实现具体阶段）
# ---------------------------------------------------------------------------


@dataclass
class StageResult:
    """校验通过后的归一化产出。平台下游只认它，不认任何具体 Excel 的列号。"""

    stage: str
    status: str
    level: str
    primary_artifacts: list[dict[str, Any]] = field(default_factory=list)
    items: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    requirements: list[dict[str, Any]] = field(default_factory=list)
    decision_summary: list[Any] = field(default_factory=list)
    assumptions: list[Any] = field(default_factory=list)
    uncertainties: list[Any] = field(default_factory=list)
    counterevidence: list[Any] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: dict, level: str) -> "StageResult":
        return cls(
            stage=_non_empty_str(payload.get("stage")),
            status=_non_empty_str(payload.get("status")) or "completed",
            level=normalize_level(level),
            primary_artifacts=_as_list(payload.get("primary_artifacts")),
            items=_as_list(payload.get("items")),
            evidence=_as_list(payload.get("evidence")),
            requirements=_as_list(payload.get("requirements")),
            decision_summary=_as_list(payload.get("decision_summary")),
            assumptions=_as_list(payload.get("assumptions")),
            uncertainties=_as_list(payload.get("uncertainties")),
            counterevidence=_as_list(payload.get("counterevidence")),
            raw=payload,
        )

    def item_ids(self) -> list[str]:
        return [_non_empty_str(item.get("id")) for item in self.items if isinstance(item, dict)]

    def evidence_ids(self) -> list[str]:
        return [_non_empty_str(entry.get("id")) for entry in self.evidence if isinstance(entry, dict)]


class StageOutputAdapter:
    """阶段产出适配器接口（§6.3）。

    抽象基类放在协议层而不是各阶段的文件里，是因为"平台按统一协议解析产出"
    属于协议的一部分——搬走它会让人以为 L0–L3 只是某个阶段的私有概念。
    """

    #: 该适配器服务的阶段标识。
    stage: str = ""

    def parse(self, artifacts: Any) -> StageResult:  # pragma: no cover - 接口定义
        raise NotImplementedError

    def build_review_report(self, stage_result: StageResult, **kwargs: Any) -> bytes:  # pragma: no cover
        raise NotImplementedError

    def build_evidence_graph(self, stage_result: StageResult, trace: Any = None, **kwargs: Any) -> dict:  # pragma: no cover
        raise NotImplementedError

    def build_quality_summary(self, stage_result: StageResult, trace: Any = None, **kwargs: Any) -> dict:  # pragma: no cover
        raise NotImplementedError


class TestPlanOutputAdapter(StageOutputAdapter):
    """方案阶段适配器。

    实现的每一步都委托给协议层/图谱层/报告层的纯函数，自己不藏判断逻辑——
    这样"同一输入生成同一份报告"这句话只需要在这三处各验一次，而不是
    每个适配器各验一次。
    """

    stage = "test_plan_generation"

    def parse(self, artifacts: Any, **kwargs: Any) -> StageResult:
        """解析 ``stage_result.json``。校验不过时抛 ``StageProtocolError``。

        刻意抛错而不是返回一个"半成品 StageResult"：下游若拿到一份校验失败的
        结构化产出却在上面做归因，得到的结论会看起来合法、实际没有依据。
        业务主产物不受影响（调用方仍然保留 Skill 产出的文件）。
        """
        validation = validate_stage_result(
            artifacts,
            expected_stage=self.stage,
            declared_level=kwargs.get("declared_level", ""),
        )
        if not validation.ok:
            raise StageProtocolError(validation)
        return StageResult.from_payload(validation.payload or {}, validation.level)

    def build_evidence_graph(
        self, stage_result: StageResult, trace: Any = None, **kwargs: Any,
    ) -> dict:
        from .evidence_graph import build_evidence_graph

        return build_evidence_graph(
            stage_result, trace=trace, corpus=kwargs.get("corpus"), output_id=kwargs.get("output_id", ""),
        )

    def build_quality_summary(
        self, stage_result: StageResult, trace: Any = None, **kwargs: Any,
    ) -> dict:
        from .quality_summary import build_quality_summary

        return build_quality_summary(stage_result, graph=kwargs.get("graph"), trace=trace)

    def build_review_report(
        self, stage_result: StageResult, **kwargs: Any,
    ) -> bytes:
        from .review_reports import build_review_workbook

        return build_review_workbook(
            stage_result, graph=kwargs.get("graph"), quality=kwargs.get("quality"),
        )


class StageProtocolError(Exception):
    """结构化协议校验失败。

    带 ``validation``：页面要能把逐条字段错误显示出来，而不是只给一句
    "协议失败"。这不是业务失败——Skill 的业务产出仍然有效。
    """

    def __init__(self, validation: "StageResultValidation") -> None:
        super().__init__(validation.detail or "stage-result 校验未通过")
        self.validation = validation


#: 阶段 → 适配器。一期只登记方案阶段，其余阶段回落到通用空实现。
#:
#: 为什么不给每个阶段都放一个"能跑但什么都不做"的适配器：那会让
#: `get_adapter('testcase_generation')` 返回一个对象、看起来像"这个阶段已支持"，
#: 而它其实什么都不会解析。显式返回 ``None`` 比返回空壳诚实。
STAGE_ADAPTERS: dict[str, StageOutputAdapter] = {
    TestPlanOutputAdapter.stage: TestPlanOutputAdapter(),
}


def get_adapter(stage: str) -> StageOutputAdapter | None:
    """取阶段适配器；未实现时返回 ``None``（不要造空壳）。"""
    return STAGE_ADAPTERS.get(str(stage or ""))


def build_derived_artifacts(
    stage_result: StageResult,
    *,
    trace: Any = None,
    corpus: Any = None,
    output_id: str = "",
) -> dict[str, Any]:
    """一生二：从一份 ``StageResult`` 生成图谱、质量摘要与确认报告。

    三个产物之间**有依赖顺序**：图谱先算出每条引用的状态，质量摘要与确认报告
    再据此统计。所以这里统一编排，而不是让调用方自己排顺序——顺序错了会得到
    "证据准确率 100%"这种看起来正常的假数字。
    """
    from .quality_summary import build_quality_summary
    from .review_reports import build_review_workbook

    graph = build_evidence_graph_payload(
        stage_result, trace=trace, corpus=corpus, output_id=output_id,
    )
    quality = build_quality_summary(stage_result, graph=graph, trace=trace)
    report = build_review_workbook(stage_result, graph=graph, quality=quality)
    return {"graph": graph, "quality": quality, "report": report}


def build_evidence_graph_payload(
    stage_result: StageResult, *, trace: Any = None, corpus: Any = None, output_id: str = "",
) -> dict:
    from .evidence_graph import build_evidence_graph

    return build_evidence_graph(stage_result, trace=trace, corpus=corpus, output_id=output_id)
