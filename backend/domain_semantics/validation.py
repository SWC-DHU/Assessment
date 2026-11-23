"""Consistency checks with explicit distinction between verified evidence and facts."""
from collections import Counter, defaultdict
from graphlib import CycleError, TopologicalSorter
import math
import re

from .catalog import load_vocabulary, relation_signatures
from .schema import Constraint, Issue, SemanticModel, Validation

from .process_graph import FLOW

COLLECTIONS = ("products", "operations", "materials", "material_states", "capabilities", "constraints")
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


def measurements(text: str):
    for match in QUANTITY.finditer(text):
        operator, value, upper, unit, basis = match.groups()
        yield (
            "range" if upper else COMPARATORS.get(operator, operator),
            float(value.replace("−", "-")), float(upper.replace("−", "-")) if upper else None, canonical_unit(unit), basis,
        )


def header_unit(text: str) -> str | None:
    match = re.search(rf"[（(]\s*({UNIT})\s*[）)]", text)
    return canonical_unit(match[1]) if match else None


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
            for operator, number, upper, unit, basis in measurements(value.quote):
                yield operator or "=", number, upper, unit, basis
            unit = header_unit(heading.quote)
            if unit and re.fullmatch(r"\s*\d+(?:\.\d+)?\s*", value.quote):
                yield "=", float(value.quote), None, unit, None


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

    def issue(code: str, message: str, path: str, status: str = "invalid") -> None:
        issues.append(Issue(code=code, message=message, path=path, status=status))

    for key, count in Counter(fact.id for fact in [*entities, *model.relations]).items():
        if count > 1:
            issue("duplicate_id", "事实 ID 重复", key)
    for fact in [*entities, *model.relations, *model.modeling_intents]:
        evidence = fact.evidence
        if evidence and any(entry.document_id not in source_ids for entry in evidence):
            fact.status = "invalid"
            issue("source_not_found", "证据引用的文档不存在", fact.id)
        elif not any(entry.status == "verified" for entry in evidence):
            fact.status = "pending"
            issue("evidence_pending", "缺少可定位的逐字引文", fact.id)
        else:
            fact.status = "verified"

    for state in model.material_states:
        if state.material_id not in by_id or by_id[state.material_id].concept != "Material":
            state.status = "invalid"
            issue("state_material_reference", "物料状态必须指向基础物料", state.id)

    terminals = relation_signatures(model.ontology_version)
    dependency_graphs = {"precedence": defaultdict(set), "dependency": defaultdict(set)}
    for relation in model.relations:
        source, target = by_id.get(relation.source_id), by_id.get(relation.target_id)
        a, b = terminals.get(relation.type, (set(), set()))
        target_type = target.concept if target else "ProductionLine" if relation.target_id == "line" else None
        if (not source or source.concept not in a or target_type not in b
                or relation.source_id == relation.target_id):
            relation.status = "invalid"
            issue("relation_reference", f"{relation.type} 的关系端点缺失或类型不符", relation.id)
        elif relation.quantity is not None and relation.type != "hasComponent":
            relation.status = "invalid"
            issue("unexpected_quantity", "组成数量仅用于 hasComponent", relation.id)
        elif relation.asserted_as == "inferred":
            relation.status = "pending"
            issue("inferred_relation", "模型候选推断需要人工确认", relation.id, "pending")
        elif relation.type == "conditionallyPrecedes" and not relation.condition:
            relation.status = "invalid"
            issue("condition_required", "条件依赖需要明确条件", relation.id)
        elif relation.type == "reworksTo" and relation.max_iterations is None:
            if relation.status == "verified":
                relation.status = "pending"
            issue("rework_limit_pending", "原文未明确返工上限", relation.id, "pending")
        elif relation.max_iterations is not None and relation.type != "reworksTo":
            relation.status = "invalid"
            issue("unexpected_rework_limit", "返工次数仅适用于返工关系", relation.id)
        elif relation.status == "verified" and relation.type in FLOW and relation.type != "reworksTo":
            reverse, _, _ = FLOW[relation.type]
            later, earlier = ((relation.source_id, relation.target_id) if reverse
                              else (relation.target_id, relation.source_id))
            group = "dependency" if relation.type == "dependsOn" else "precedence"
            dependency_graphs[group][later].add(earlier)

    def reachable(dependencies, start, target):
        visited, pending = set(), [start]
        while pending:
            node = pending.pop()
            if node == target:
                return True
            if node not in visited:
                visited.add(node)
                pending.extend(dependencies[node] - visited)
        return False

    for group, dependencies in dependency_graphs.items():
        try:
            tuple(TopologicalSorter(dependencies).static_order())
        except CycleError:
            for relation in model.relations:
                if relation.status != "verified" or relation.type not in FLOW or relation.type == "reworksTo":
                    continue
                if (relation.type == "dependsOn") != (group == "dependency"):
                    continue
                reverse, _, _ = FLOW[relation.type]
                later, earlier = ((relation.source_id, relation.target_id) if reverse
                                  else (relation.target_id, relation.source_id))
                if reachable(dependencies, earlier, later):
                    relation.status = "invalid"
                    issue(f"{group}_cycle", "工艺依赖形成非返工环路", relation.id)

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

    if tool is None:
        validate_constraints(model.constraints, by_id, issue)
    else:
        tool("validate_constraints", f"{len(model.constraints)} 条工艺约束", validate_constraints,
             model.constraints, by_id, issue)

    vocabulary = load_vocabulary()["capabilities"]
    for capability in model.capabilities:
        if capability.code is None:
            if capability.status == "verified":
                capability.status = "pending"
            issue("capability_unmapped", f"待确认能力术语：{capability.name}", capability.id, "pending")
        elif capability.code not in vocabulary:
            capability.status = "invalid"
            issue("capability_unknown", "能力编码不在词表内", capability.id)

    model.unresolved_items = issues
    states = {item.status for item in issues}
    status = next((state for state in ("invalid", "conflict", "pending", "unprocessed") if state in states), "verified")
    model.validation = Validation(status=status, issues=issues)
    return model


def validate_constraints(constraints: list[Constraint], by_id: dict, issue) -> None:
    grouped = defaultdict(list)
    parameters = load_vocabulary()["parameters"]
    for value in constraints:
        if value.target_id != "line" and (value.target_id not in by_id or
                by_id[value.target_id].concept not in {"Operation", "Product", "Capability"}):
            value.status = "invalid"
            issue("constraint_target", "约束对象不存在或类型不受支持", value.id)
        if value.parameter_code is None and value.comparator is not None:
            if value.status == "verified":
                value.status = "pending"
            issue("parameter_unmapped", "约束参数尚未映射", value.id, "pending")
        if value.parameter_code and value.parameter_code not in parameters:
            value.status = "invalid"
            issue("parameter_unknown", "参数编码不在词表内", value.id)
        elif value.parameter_code and value.unit and value.unit not in parameters[value.parameter_code]["units"]:
            value.status = "invalid"
            issue("unit_mismatch", "单位不适用于此参数", value.id)
        if any(entry.status == "verified" for entry in value.evidence) and not quantity_supported(value):
            value.status = "invalid"
            issue("quantity_unsupported", "原文未支持比较符、数值、范围、单位或计量基准", value.id)
        if value.lower is not None and value.upper is not None and value.lower > value.upper:
            value.status = "invalid"
            issue("range_bounds", "约束的下限高于上限", value.id)
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
            lo = max((lower, record.comparator == ">") for record, lower, _ in entries)
            hi = min((upper, record.comparator != "<") for record, _, upper in entries)
            if lo[0] > hi[0] or (lo[0] == hi[0] and (lo[1] or not hi[1])):
                for record, _, _ in entries:
                    record.status = "conflict"
                    issue("constraint_conflict", "同一对象和参数的约束区间不相容", record.id, "conflict")
