"""Consistency checks with explicit distinction between verified evidence and facts."""
from collections import Counter, defaultdict
import math
import re

import networkx as nx

from .catalog import load_vocabulary, ontology_concept, ontology_metadata, relation_signatures
from .modeling_task import build_modeling_task, stated_scope, task_statements, task_target_options
from .schema import (Constraint, HandoffIssue, HandoffValidation, Issue, ModelingTask, ProcessGraph, ReviewState,
                     SemanticModel, Validation)

from .process_graph import FLOW, flow_endpoints

COLLECTIONS = ("products", "operations", "materials", "material_states", "capabilities", "constraints")
FACT_COLLECTIONS = (*COLLECTIONS, "modeling_intents", "relations")
COMPUTED_ISSUE_CODES = {
    "source_not_found", "evidence_pending", "duplicate_id", "duplicate_source", "reserved_id", "relation_reference",
    "condition_required", "rework_condition_pending", "rework_limit_pending", "unexpected_rework_limit",
    "unexpected_quantity", "inferred_relation", "precedence_cycle", "dependency_cycle", "constraint_target",
    "parameter_unmapped", "parameter_unknown", "unit_mismatch", "quantity_unsupported", "range_bounds",
    "constraint_shape", "negative_time", "constraint_conflict", "capability_unmapped", "capability_unknown",
    "capability_adjudication_pending",
    "state_material_reference", "component_unit_mismatch", "component_quantity_conflict", "information_evidence_pending",
    "model_version_mismatch", "task_reference", "task_evidence_pending", "task_scope_conflict", "task_product_conflict",
}
STATUS_PRIORITY = ("invalid", "conflict", "pending", "unprocessed")
UNIT = r"秒钟|秒|分钟|小时|毫秒|ms|min|MPa|kPa|Pa|bar|mm|μm|um|°C|℃|[shmK]"
NUMBER = r"[-−+]?\d+(?:\.\d+)?"
OPERATORS = r"<=|>=|≤|≥|<|>|＝|=|不超过|不得超过|至多|不少于|至少|不低于|小于|大于|修改为|改为|改成|调整为|为|是"
QUANTITY = re.compile(
    rf"(?P<comparison>{OPERATORS})?\s*(?P<value>{NUMBER})\s*"
    rf"(?:(?:至|到|~|～|–|-)\s*(?P<upper>{NUMBER})\s*)?(?P<unit>{UNIT})(?![A-Za-z])"
    r"(?:\s*(?:/|每)\s*(?P<basis>[\w]+?)(?=[，。；、,;\s]|$))?"
)
COMPARATORS = {
    "≤": "<=", "≥": ">=", "＝": "=", "不超过": "<=", "不得超过": "<=", "至多": "<=",
    "不少于": ">=", "至少": ">=", "不低于": ">=", "小于": "<", "大于": ">",
    "为": "=", "是": "=", "修改为": "=", "改为": "=", "改成": "=", "调整为": "=",
}


def canonical_unit(unit: str | None) -> str | None:
    return load_vocabulary()["unit_aliases"].get(unit, unit)


def constraint_strength(evidence):
    def classify(quote):
        if re.search(r"必须|不得|禁止|\bmust\b|\bshall\b", quote, re.I):
            return "mandatory"
        if re.search(r"建议|优先|最好|\bprefer\b|\bideally\b", quote, re.I):
            return "preferred"
        if re.search(r"要求|不超过|不少于|不低于|至多|至少|\brequired\b", quote, re.I):
            return "mandatory"
        return "unspecified"

    strengths = {classify(item.quote) for item in evidence if item.status == "verified"}
    return next((strength for strength in ("mandatory", "preferred") if strength in strengths), "unspecified")


def fact_evidence(fact, source_ids: set[str]) -> bool:
    evidence = [*fact.evidence, *(fact.review_evidence if fact.review == "confirmed" else [])]
    return any(item.status == "verified" and item.document_id in source_ids for item in evidence)


def summarize_validation(model: SemanticModel, issues: list[Issue]) -> None:
    issues = list({(item.code, item.path, item.message, item.status): item for item in issues}.values())
    states = {item.status for item in issues}
    status = next((state for state in STATUS_PRIORITY if state in states), "verified")
    model.unresolved_items = issues
    model.validation = Validation(status=status, issues=issues)


def validate_task(model: SemanticModel, issue) -> None:
    task = model.modeling_task
    if task is None:
        return
    expected = build_modeling_task(model)
    if expected != task:
        issue("task_reference", "建模任务应引用现有目标、目标产品和整线约束", "modeling_task")
    if task.target_product_id is not None and not any(
        item.id == task.target_product_id and item.status == "verified" for item in model.products
    ):
        issue("task_reference", "建模任务的目标产品缺少已核验产品引用", "modeling_task.target_product_id")
    if not any(item.id in task.objective_ids and item.kind == task.task_type and item.status == "verified"
               for item in model.modeling_intents):
        issue("task_evidence_pending", "建模任务缺少已核验的目标依据", "modeling_task", "pending")
    if task.review_evidence and any(item.method != "manual" or item.status != "verified" or
                                    item.document_id not in {ref.document_id for ref in model.source_refs}
                                    for item in task.review_evidence):
        issue("task_evidence_pending", "建模任务的审核依据待核验", "modeling_task", "pending")
    if not task.review_evidence:
        statements = task_statements(model)
        if len({scope for statement in statements if (scope := stated_scope(statement))}) > 1:
            issue("task_scope_conflict", "来源声明了多个建模范围，需要人工选择", "modeling_task.target_scope", "conflict")
        if len(task_target_options(model, statements)) > 1:
            issue("task_product_conflict", "来源声明了多个候选目标产品，需要人工选择", "modeling_task.target_product_id",
                  "conflict")


def measurements(text: str):
    for match in QUANTITY.finditer(text):
        operator, value, upper, unit, basis = match.groups()
        value = float(value.replace("−", "-"))
        upper = float(upper.replace("−", "-")) if upper is not None else None
        if not math.isfinite(value) or (upper is not None and not math.isfinite(upper)):
            continue
        yield (
            "range" if upper is not None else COMPARATORS.get(operator, operator),
            value, upper, canonical_unit(unit), basis,
        )


def tabular_measurements(text: str, heading: str):
    stated = list(measurements(text))
    if stated:
        return stated
    unit = re.search(rf"[（(]\s*({UNIT})(?:\s*(?:/|每)\s*([\w]+))?\s*[）)]", heading)
    if not unit:
        return []
    literal = f"{text.strip()} {unit[1]}" + (f"/{unit[2]}" if unit[2] else "")
    return list(measurements(literal)) if QUANTITY.fullmatch(literal) else []


def tabular_values(constraint: Constraint):
    evidence = [cite for cite in constraint.evidence if cite.status == "verified" and cite.location.cell]
    heading_names = [constraint.name, *load_vocabulary()["parameters"].get(constraint.parameter_code, {}).get("aliases", [])]
    for value in evidence:
        col = re.match(r"[A-Z]+", value.location.cell)
        if not col:
            continue
        for heading in evidence:
            if (heading.document_id != value.document_id or heading.location.sheet != value.location.sheet
                    or not (heading.location.row and value.location.row and heading.location.row < value.location.row)
                    or not re.fullmatch(rf"{col[0]}\d+", heading.location.cell)
                    or not any(name in heading.quote for name in heading_names)):
                continue
            for operator, number, upper, unit, basis in tabular_measurements(value.quote, heading.quote):
                yield operator or "=", number, upper, unit, basis


def quantity_supported(constraint: Constraint) -> bool:
    if constraint.value is None and constraint.lower is None and constraint.upper is None:
        return not (constraint.comparator or constraint.unit or constraint.basis)
    actual = [*tabular_values(constraint)]
    for evidence in constraint.evidence:
        if evidence.status != "verified":
            continue
        for clause in re.split(r"[，,。；;\n]", evidence.quote):
            if constraint.name in clause:
                actual.extend(measurements(clause))
    if constraint.comparator == "range":
        expected = ("range", constraint.lower, constraint.upper, constraint.unit, constraint.basis)
        return constraint.value is None and expected in actual
    expected = (constraint.comparator, constraint.value, None, constraint.unit, constraint.basis)
    return constraint.lower is None and constraint.upper is None and expected in actual


def validate_model(model: SemanticModel, extra: list[Issue] | None = None, tool=None) -> SemanticModel:
    issues = list(extra or [])
    entities = [entity for group in COLLECTIONS for entity in getattr(model, group)]
    by_id = {entity.id: entity for entity in entities}
    source_ids = {source.document_id for source in model.source_refs}

    def issue(code: str, message: str, path: str, status: ReviewState = "invalid") -> None:
        issues.append(Issue(code=code, message=message, path=path, status=status))

    facts = [fact for group in FACT_COLLECTIONS for fact in getattr(model, group)]
    for key, count in Counter(fact.id for fact in facts).items():
        if count > 1:
            issue("duplicate_id", "事实 ID 重复", key)
        if key == "line" or not key.strip():
            issue("reserved_id", "事实 ID 不能为空或占用整线保留标识", key)
    for key, count in Counter(source.document_id for source in model.source_refs).items():
        if count > 1:
            issue("duplicate_source", "来源 ID 重复", key)
    if model.schema_version == "2.2.0" and model.metadata and (
        model.metadata.schema_version != model.schema_version or model.metadata.ontology_version != model.ontology_version
    ):
        issue("model_version_mismatch", "模型与运行元数据的版本不一致", "metadata")
    for fact in facts:
        evidence = [*fact.evidence, *fact.review_evidence]
        if evidence and any(entry.document_id not in source_ids for entry in evidence):
            fact.status = "invalid"
            issue("source_not_found", "证据引用的文档不存在", fact.id)
        elif not fact_evidence(fact, source_ids):
            fact.status = "pending"
            issue("evidence_pending", "缺少可定位的逐字引文", fact.id)
        else:
            fact.status = "verified"

    for state in model.material_states:
        if state.material_id not in by_id or by_id[state.material_id].concept != "Material":
            state.status = "invalid"
            issue("state_material_reference", "物料状态必须指向基础物料", state.id)

    for op in model.operations:
        for flow in op.information_flows:
            flow.status = "verified" if any(cite.status == "verified" and cite.document_id in source_ids
                                            for cite in flow.evidence) else "pending"
            if flow.status == "pending":
                issue("information_evidence_pending", f"信息流「{flow.name}」的来源待核验", op.id, "pending")

    terminals = relation_signatures(model.ontology_version)
    dependency_graph = nx.DiGraph()
    ordering = []
    for relation in model.relations:
        source, target = by_id.get(relation.source_id), by_id.get(relation.target_id)
        a, b = terminals.get(relation.type, (set(), set()))
        target_type = (ontology_concept(target.concept, model.ontology_version) if target else
                       "ProductionLine" if relation.target_id == "line" else None)
        if (not source or ontology_concept(source.concept, model.ontology_version) not in a or target_type not in b
                or relation.source_id == relation.target_id):
            relation.status = "invalid"
            issue("relation_reference", f"{relation.type} 的关系端点缺失或类型不符", relation.id)
        elif relation.quantity is not None and relation.type != "hasComponent":
            relation.status = "invalid"
            issue("unexpected_quantity", "组成数量仅用于 hasComponent", relation.id)
        elif relation.asserted_as == "inferred":
            relation.status = "pending"
            issue("inferred_relation", "模型候选推断需要人工确认", relation.id, "pending")
        elif relation.type == "conditionallyPrecedes" and not (relation.condition or "").strip():
            relation.status = "invalid"
            issue("condition_required", "条件依赖需要明确条件", relation.id)
        elif relation.type == "reworksTo":
            if not (relation.condition or "").strip():
                issue("rework_condition_pending", "原文未明确返工触发条件", relation.id, "pending")
            if relation.max_iterations is None:
                issue("rework_limit_pending", "原文未明确返工上限", relation.id, "pending")
            if relation.status == "verified" and (not (relation.condition or "").strip() or relation.max_iterations is None):
                relation.status = "pending"
        elif relation.max_iterations is not None and relation.type != "reworksTo":
            relation.status = "invalid"
            issue("unexpected_rework_limit", "返工次数仅适用于返工关系", relation.id)
        elif relation.status == "verified" and relation.type in FLOW and relation.type != "reworksTo":
            earlier, later = flow_endpoints(relation)
            dependency_graph.add_edge(earlier, later)
            ordering.append((relation, earlier, later))

    cycle_membership = {node: index for index, component in enumerate(nx.strongly_connected_components(dependency_graph))
                        if len(component) > 1 for node in component}
    for relation, earlier, later in ordering:
        if earlier in cycle_membership and cycle_membership[earlier] == cycle_membership.get(later):
            relation.status = "invalid"
            code = "dependency_cycle" if relation.type == "dependsOn" else "precedence_cycle"
            issue(code, "工艺依赖形成非返工环路", relation.id)

    component_groups = defaultdict(list)
    for relation in model.relations:
        if relation.type == "hasComponent" and relation.quantity and relation.status == "verified":
            quantity = relation.quantity
            if canonical_unit(quantity.unit) in {"ms", "s", "min", "h", "Pa", "MPa", "K", "°C"}:
                relation.status = "invalid"
                issue("component_unit_mismatch", "物料组成数量使用了不相容量纲", relation.id)
            else:
                component_groups[relation.source_id, relation.target_id, quantity.unit, quantity.basis].append(relation)
    for records in component_groups.values():
        if len({record.quantity.value for record in records}) > 1:
            for relation in records:
                relation.status = "conflict"
                issue("component_quantity_conflict", "同一组件及计量基准出现不同数量", relation.id, "conflict")

    pending_measurements = {item.path for item in issues if item.code == "measurement_unparsed"}
    if tool is None:
        validate_constraints(model.constraints, by_id, issue, pending_measurements)
    else:
        tool("validate_constraints", f"{len(model.constraints)} 条工艺约束", validate_constraints,
             model.constraints, by_id, issue, pending_measurements)

    vocabulary = ontology_metadata(model.ontology_version)["vocabulary"]["capabilities"]
    for capability in model.capabilities:
        if capability.code is None:
            if capability.status == "verified":
                capability.status = "pending"
            issue("capability_unmapped", f"待确认能力术语：{capability.name}", capability.id, "pending")
        elif capability.code not in vocabulary:
            capability.status = "invalid"
            issue("capability_unknown", "能力编码不在词表内", capability.id)
        elif capability.mapping == "adjudicated" and capability.review != "confirmed":
            if capability.status == "verified":
                capability.status = "pending"
            issue("capability_adjudication_pending", "模型建议的歧义能力映射需要人工确认", capability.id, "pending")

    for finding in issues:
        if finding.code in {"field_conflict", "identity_conflict"} and finding.path in by_id:
            by_id[finding.path].status = "conflict"
    conflicting_predecessors = {finding.path for finding in issues if finding.code == "field_conflict"
                                and "precursor_declaration" in finding.message}
    for relation in model.relations:
        if (relation.type == "hasPredecessor" and relation.source_id in conflicting_predecessors) or (
            relation.type == "mergesInto" and relation.target_id in conflicting_predecessors
        ):
            relation.status = "conflict"
        if relation.status == "verified":
            if relation.type == "appliesTo":
                relation.status = by_id[relation.source_id].status
            elif relation.type == "requiresCapability":
                relation.status = by_id[relation.target_id].status

    validate_task(model, issue)
    summarize_validation(model, issues)
    return model


def validate_constraints(constraints: list[Constraint], by_id: dict, issue, pending_measurements: set[str]) -> None:
    grouped = defaultdict(list)
    vocabulary = load_vocabulary()
    parameters = vocabulary["parameters"]
    qualitative = vocabulary["quality_parameters"]
    for value in constraints:
        if value.target_id != "line" and (value.target_id not in by_id or
                by_id[value.target_id].concept not in {"Operation", "Product", "Capability"}):
            value.status = "invalid"
            issue("constraint_target", "约束对象不存在或类型不受支持", value.id)
        if value.parameter_code is None and value.comparator is not None:
            if value.status == "verified":
                value.status = "pending"
            issue("parameter_unmapped", "约束参数尚未映射", value.id, "pending")
        if (value.parameter_code and value.parameter_code not in parameters
                and (value.parameter_code not in qualitative or value.comparator is not None)):
            value.status = "invalid"
            issue("parameter_unknown", "参数编码不在词表内", value.id)
        elif value.parameter_code in parameters and value.unit and value.unit not in parameters[value.parameter_code]["units"]:
            value.status = "invalid"
            issue("unit_mismatch", "单位不适用于此参数", value.id)
        if any(code not in qualitative for code in value.related_parameter_codes):
            value.status = "invalid"
            issue("parameter_unknown", "关联参数编码不在定性词表内", value.id)
        if not interpretable_constraint(value):
            value.status = "invalid"
            issue("constraint_shape", "约束需要定性描述，或完整的比较符、单位及数值或范围", value.id)
        if any(entry.status == "verified" for entry in value.evidence) and not quantity_supported(value):
            value.status = "invalid"
            issue("quantity_unsupported", "原文未支持比较符、数值、范围、单位或计量基准", value.id)
        if value.lower is not None and value.upper is not None and value.lower > value.upper:
            value.status = "invalid"
            issue("range_bounds", "约束的下限高于上限", value.id)
        if value.parameter_code in {"process_time", "wait_time", "buffer_time", "takt_time"} and any(
            number is not None and number < 0 for number in (value.value, value.lower, value.upper)
        ):
            value.status = "invalid"
            issue("negative_time", "时间约束不能使用负数", value.id)
        if value.id in pending_measurements and value.status == "verified":
            value.status = "pending"
        if value.status == "verified" and value.parameter_code and value.comparator:
            grouped[(value.target_id, value.parameter_code, value.basis)].append(value)

    for records in grouped.values():
        bounds = defaultdict(list)
        for record in records:
            scales = {"ms": 0.001, "s": 1, "min": 60, "h": 3600, "m": 1000, "mm": 1, "um": 0.001,
                      "Pa": 1, "kPa": 1000, "MPa": 1000000, "bar": 100000}
            unit = record.unit
            group = "time" if unit in {"ms", "s", "min", "h"} else "length" if unit in {"m", "mm", "um"} else (
                "pressure" if unit in {"Pa", "kPa", "MPa", "bar"} else "temperature")
            def convert(number):
                return number * scales.get(unit, 1) + (273.15 if unit == "°C" else 0)
            lower = record.lower if record.comparator == "range" else record.value if record.comparator in {">", ">=", "="} else None
            upper = record.upper if record.comparator == "range" else record.value if record.comparator in {"<", "<=", "="} else None
            bounds[group].append((record, convert(lower) if lower is not None else -math.inf,
                                  convert(upper) if upper is not None else math.inf))
        for entries in bounds.values():
            required = [entry for entry in entries if entry[0].strength != "preferred"]
            preferences = [entry for entry in entries if entry[0].strength == "preferred"]
            conflicts = required if incompatible_bounds(required) else preferences if incompatible_bounds(entries) else []
            for record, _, _ in conflicts:
                record.status = "conflict"
                issue("constraint_conflict", "同一对象和参数的约束区间不相容", record.id, "conflict")


def incompatible_bounds(entries) -> bool:
    lo = max(((lower, record.comparator == ">") for record, lower, _ in entries), default=(-math.inf, False))
    hi = min(((upper, record.comparator != "<") for record, _, upper in entries), default=(math.inf, True))
    return lo[0] > hi[0] or (lo[0] == hi[0] and (lo[1] or not hi[1]))


def interpretable_constraint(value: Constraint) -> bool:
    if value.comparator is None:
        return (bool((value.description or "").strip())
                and all(item is None for item in (value.value, value.lower, value.upper, value.unit, value.basis)))
    if not (value.unit or "").strip():
        return False
    if value.comparator == "range":
        return value.value is None and value.lower is not None and value.upper is not None and value.lower <= value.upper
    return value.value is not None and value.lower is None and value.upper is None


OPERATION_LINKS = {
    "requiresCapability": ("capability_ids", "capabilities"),
    "consumes": ("input_material_ids", "materials"),
    "produces": ("output_material_ids", "materials"),
    "hasInputState": ("input_state_ids", "material_states"),
    "hasOutputState": ("output_state_ids", "material_states"),
    "hasPredecessor": ("predecessor_ids", "operations"),
}
HANDOFF_BLOCKING_ISSUES = {
    "duplicate_id", "duplicate_source", "reserved_id", "model_version_mismatch", "task_reference",
    "task_evidence_pending", "task_scope_conflict", "task_product_conflict", "precedence_cycle",
    "dependency_cycle", "component_quantity_conflict", "component_unit_mismatch", "predecessor_unresolved",
    "merge_predecessors_pending",
}


def check_handoff(model: SemanticModel, task: ModelingTask | None, graph: ProcessGraph):
    """Check the saved facts without promoting pending links or changing the snapshot."""
    groups = {group: {item.id: item for item in getattr(model, group)} for group in FACT_COLLECTIONS}
    ops, relations = groups["operations"], groups["relations"]
    source_ids = {item.document_id for item in model.source_refs}
    findings: dict[tuple[str, str, str], HandoffIssue] = {}
    links = defaultdict(list)
    associated = defaultdict(set)
    for edge in model.relations:
        links[edge.type, edge.source_id].append(edge)
        participants = set(ops).intersection((edge.source_id, edge.target_id))
        for key in {edge.id, edge.source_id, edge.target_id} - ops.keys():
            associated[key].update(participants)
    for constraint in model.constraints:
        associated[constraint.id].update({constraint.target_id} & ops.keys())
        associated[constraint.id].update(associated[constraint.target_id])

    def report(code, message, path, status="invalid", *, blocker=True, operation_ids=()):
        linked = sorted((set(operation_ids) | associated[path] | {path}) & ops.keys())
        finding = HandoffIssue(code=code, message=message, path=path, status=status,
                               severity="blocker" if blocker else "warning", operation_ids=linked)
        key = code, path, message
        if key not in findings or blocker:
            findings[key] = finding

    def trusted(item):
        return item.status == "verified" and fact_evidence(item, source_ids)

    def trusted_link(edge):
        return edge.asserted_as == "explicit" and trusted(edge)

    try:
        catalog = ontology_metadata(model.ontology_version)
    except KeyError:
        report("handoff_ontology_version", "无法加载该版本的本体和词表", "ontology_version")
        catalog = {"vocabulary": {}}
    vocab = catalog["vocabulary"]
    if vocab and vocab.get("version") != model.vocabulary_version:
        report("handoff_vocabulary_version", "词表版本与来源模型不一致", "vocabulary_version")
    facts = [item for group in FACT_COLLECTIONS for item in getattr(model, group)]
    for key, count in Counter(item.id for item in facts).items():
        if count > 1 or key == "line" or not key.strip():
            report("handoff_fact_id", "事实 ID 重复、为空或使用了保留标识", key)
    if len(source_ids) != len(model.source_refs):
        report("duplicate_source", "来源 ID 重复", "source_refs")
    if model.schema_version == "2.2.0" and model.metadata and (
        model.metadata.schema_version != model.schema_version or model.metadata.ontology_version != model.ontology_version
    ):
        report("model_version_mismatch", "模型与运行元数据的版本不一致", "metadata")

    def task_issue(code, message, path, status="invalid"):
        report(code, message, path, status)

    validate_task(model, task_issue)
    if task is None or not any(obj.id in task.objective_ids and obj.kind == task.task_type and trusted(obj)
                               for obj in model.modeling_intents):
        report("handoff_task_missing", "缺少明确的建模任务，请补充来源或通过人工审核确认", "modeling_task", "pending")
    elif task.target_scope is None:
        report("handoff_task_scope_missing", "建模范围尚未明确", "modeling_task.target_scope", "pending", blocker=False)
    if task and task.target_product_id is None and model.products:
        report("handoff_target_product_unconfirmed", "已有产品事实，目标产品关联待确认",
               "modeling_task.target_product_id", "pending", blocker=False)
    if not ops:
        report("handoff_operations_missing", "缺少可交接的工序", "operations", "pending")

    approved_codes = {}
    for op in model.operations:
        if not trusted(op):
            report("handoff_operation_unverified", "工序标识或来源待核验", op.id, "pending")
        valid_codes = []
        for kind, (field, group) in OPERATION_LINKS.items():
            values = groups[group]
            refs = getattr(op, field)
            edges = links[kind, op.id]
            grounded = {edge.target_id for edge in edges if trusted_link(edge)}
            for key in refs:
                if key not in values or (key not in grounded and kind != "requiresCapability"):
                    report("handoff_operation_reference", f"{field} 引用缺失或缺少已核验关系 · {key}", op.id,
                           operation_ids=[op.id])
            for edge in edges:
                item = values.get(edge.target_id)
                if edge.asserted_as != "explicit":
                    continue
                usable = item is not None and trusted(item) and trusted_link(edge) and item.id in refs
                if kind == "requiresCapability":
                    mapped = usable and item.code in vocab.get("capabilities", {}) and (
                        item.mapping in {"exact", "phrase"} or item.review == "confirmed"
                    )
                    if mapped:
                        valid_codes.append(item.code)
                    else:
                        report("handoff_required_capability_unverified", "原文要求的制造能力尚未核验或确认映射",
                               edge.id, "pending", operation_ids=[op.id])
                elif not usable:
                    report("handoff_operation_link_unverified", f"工序的 {kind} 关系尚未核验",
                           edge.id, "pending", operation_ids=[op.id])
            if kind == "requiresCapability":
                declared = {edge.target_id for edge in edges if edge.asserted_as == "explicit"}
                if set(refs) - declared:
                    report("handoff_capability_link_missing", "能力引用缺少明确的工序需求关系", op.id)
        approved_codes[op.id] = list(dict.fromkeys(valid_codes))
        if not valid_codes:
            report("handoff_capability_missing", "工序缺少已核验的制造能力", op.id, "pending")
        check_handoff_predecessors(op, ops, report)
        expected = {item.id for item in model.constraints if item.target_id == op.id}
        if set(op.constraint_ids) != expected:
            report("handoff_constraint_reference", "工序的约束引用与约束实际作用对象不一致", op.id)
        if op.equipment_requirement and not any(cite.status == "verified" and cite.document_id in source_ids
                                                for cite in op.equipment_evidence):
            report("handoff_equipment_unverified", "原文设备条件缺少独立核验依据", op.id, "pending")

    for state in model.material_states:
        if state.material_id not in groups["materials"]:
            report("state_material_reference", "物料状态引用的基础物料不存在", state.id)
    for product in model.products:
        if any(key not in groups["products"] and key not in groups["materials"] for key in product.component_ids):
            report("handoff_component_reference", "产品组成包含无效引用", product.id)
    for capability in model.capabilities:
        expected = {item.id for item in model.constraints if item.target_id == capability.id}
        if set(capability.parameter_constraint_ids) != expected:
            report("handoff_constraint_reference", "能力的约束引用与约束实际作用对象不一致", capability.id)
    check_handoff_constraints(model, groups, vocab, trusted, report)
    check_handoff_graph(model, graph, ops, relations, trusted_link, report)

    for item in [*model.validation.issues, *model.unresolved_items, *graph.issues]:
        report(item.code, item.message, item.path, item.status, blocker=item.code in HANDOFF_BLOCKING_ISSUES)
    if model.metadata and model.metadata.extraction_status != "complete":
        report("handoff_extraction_coverage", "来源抽取范围未全部完成", "metadata.extraction_status",
               "unprocessed", blocker=False)
    issues = list(findings.values())
    return HandoffValidation(status=HandoffValidation.status_for(issues), issues=issues), approved_codes


def check_handoff_predecessors(op, ops, report):
    if op.precursor_declaration is None:
        return
    declared = set(op.precursor_declaration)
    predecessors = [ops[key] for key in op.predecessor_ids if key in ops]
    names = {key for item in predecessors for key in (item.id, item.name, item.number) if key}
    if not declared <= names or (not declared and predecessors):
        report("handoff_predecessor_unresolved", "原文前驱声明与已核验的依赖不一致", op.id, "pending")


def check_handoff_constraints(model, groups, vocabulary, trusted, report):
    targets = {"line", *groups["operations"], *groups["products"], *groups["capabilities"]}
    measurements = vocabulary.get("parameters", {})
    for item in model.constraints:
        if item.target_id not in targets:
            report("handoff_constraint_target", "约束作用对象不存在", item.id)
            continue
        parameter = measurements.get(item.parameter_code)
        wrong_unit = item.comparator is not None and (not parameter or item.unit not in parameter["units"])
        if not trusted(item) or not interpretable_constraint(item) or wrong_unit:
            mandatory = item.strength == "mandatory" or constraint_strength(item.evidence) == "mandatory"
            conflict = item.status == "conflict" and item.strength != "preferred"
            report("handoff_constraint_unverified", "约束的依据、参数或数值仍需核对", item.id, "pending",
                   blocker=mandatory or conflict)
    for edge in model.relations:
        if edge.type == "appliesTo":
            constraint = groups["constraints"].get(edge.source_id)
            if constraint is None or constraint.target_id != edge.target_id or edge.target_id not in targets:
                report("handoff_constraint_relation", "约束作用对象与语义关系不一致", edge.id)
        if edge.type == "hasComponent" and (edge.source_id not in groups["products"] or
                                            edge.target_id not in groups["products"] | groups["materials"]):
            report("handoff_component_reference", "产品组成关系存在无效端点", edge.id)


def check_handoff_graph(model, graph, ops, relations, trusted_link, report):
    if Counter(node.id for node in graph.nodes) != Counter(ops.keys()):
        report("handoff_graph_nodes", "工艺图节点与工序标识不一致", "process_graph.nodes")
    represented = set()
    forward = nx.DiGraph()
    for edge in graph.edges:
        if edge.source not in ops or edge.target not in ops or edge.source == edge.target or not edge.relation_ids:
            report("handoff_graph_reference", "工艺图边存在无效端点或缺少语义关系", edge.id,
                   operation_ids=[edge.source, edge.target])
            continue
        for key in edge.relation_ids:
            relation = relations.get(key)
            if (relation is None or relation.type not in FLOW or flow_endpoints(relation) != (edge.source, edge.target)
                    or relation.condition != edge.condition or relation.max_iterations != edge.max_iterations):
                report("handoff_graph_reference", "工艺图边与来源关系不一致", edge.id,
                       operation_ids=[edge.source, edge.target])
            else:
                _, kind, direct = FLOW[relation.type]
                if edge.direct != direct or (edge.kind != kind and
                                             not {kind, edge.kind} <= {"declared_predecessor", "direct_transition", "branch", "merge"}):
                    report("handoff_graph_kind", "工艺图的转序类型与来源关系不一致", edge.id)
                if edge.status == "verified" and trusted_link(relation):
                    represented.add(key)
                    if kind != "rework":
                        forward.add_edge(edge.source, edge.target)
                elif edge.status == "verified":
                    report("handoff_graph_evidence", "工艺图包含未经核验的转序", edge.id)
    for edge in model.relations:
        if edge.type not in FLOW or edge.asserted_as != "explicit":
            continue
        a, b = flow_endpoints(edge)
        valid_endpoints = a in ops and b in ops and a != b
        condition_missing = edge.type in {"conditionallyPrecedes", "reworksTo"} and not (edge.condition or "").strip()
        rework_limit_missing = edge.type == "reworksTo" and edge.max_iterations is None
        if not valid_endpoints or condition_missing or rework_limit_missing or edge.id not in represented:
            report("handoff_transition_unverified", "声明的工艺转序缺少有效端点、条件、返工上限或来源依据",
                   edge.id, "pending", blocker=edge.type != "optionalNext" or not valid_endpoints,
                   operation_ids=[a, b])
    if not nx.is_directed_acyclic_graph(forward):
        report("handoff_graph_cycle", "前向工艺关系形成非返工环路", "process_graph.edges")
    if len(ops) > 1 and not forward.edges and any(op.precursor_declaration != [] for op in ops.values()):
        report("handoff_graph_missing", "多工序需求缺少可核验的前向关系或明确的独立工序声明",
               "process_graph.edges", "pending")
