"""Project typed process flow from grounded semantic relations."""
from collections import defaultdict

from .schema import GraphEdge, GraphNode, Issue, ProcessGraph, RequirementSemanticModel

# Whether to reverse the ontology direction, edge kind, and explicitly direct route.
FLOW = {
    "hasPredecessor": (True, "declared_predecessor", True),
    "dependsOn": (True, "dependency", False),
    "precedes": (False, "precedence", False),
    "directlyPrecedes": (False, "direct_transition", True),
    "branchesTo": (False, "branch", True),
    "mergesInto": (False, "merge", True),
    "conditionallyPrecedes": (False, "conditional", True),
    "reworksTo": (False, "rework", True),
    "optionalNext": (False, "optional", True),
}

CATEGORIES = {
    "主线工序": "main", "主线": "main", "main": "main",
    "旁路工序": "bypass", "旁路": "bypass", "bypass": "bypass",
    "汇合工序": "merge", "汇合": "merge", "merge": "merge",
    "可选工序": "optional", "可选": "optional", "optional": "optional",
}


def build_process_graph(model: RequirementSemanticModel) -> ProcessGraph:
    nodes = [
        GraphNode(
            id=op.id, name=op.name, number=op.number,
            category=CATEGORIES.get((op.operation_type or "").strip().lower(), "unspecified"),
            input_material_ids=op.input_material_ids, output_material_ids=op.output_material_ids,
            input_state_ids=op.input_state_ids, output_state_ids=op.output_state_ids,
            capability_ids=op.capability_ids, constraint_ids=op.constraint_ids, evidence=op.evidence,
        )
        for op in model.operations
    ]
    ids = {node.id for node in nodes}
    edges = []
    issues = []

    def issue(code, message, path, status="pending"):
        issues.append(Issue(code=code, message=message, path=path, status=status))

    merges = {(edge.source_id, edge.target_id): edge for edge in model.relations if edge.type == "mergesInto"}
    seen = set()
    for relation in model.relations:
        if relation.type not in FLOW:
            continue
        reverse, kind, direct = FLOW[relation.type]
        source, target = (relation.target_id, relation.source_id) if reverse else (relation.source_id, relation.target_id)
        if not {source, target} <= ids or source == target:
            issue("graph_endpoint", "工艺边存在无效端点或自连接", relation.id, "invalid")
            continue
        linked_merge = merges.get((source, target)) if relation.type == "hasPredecessor" else None
        if linked_merge and linked_merge.status == relation.status:
            continue  # The merge edge will carry both references below.
        key = (source, target, kind, relation.condition)
        if key in seen:
            issue("graph_duplicate", "同一工艺连接存在多个定义", relation.id, "conflict")
        seen.add(key)
        references = [relation.id]
        if relation.type == "mergesInto":
            references.extend(edge.id for edge in model.relations if edge.type == "hasPredecessor"
                              and edge.target_id == source and edge.source_id == target and edge.status == relation.status)
        status = relation.status
        if not any(item.status == "verified" for item in relation.evidence) and status == "verified":
            status = "pending"
        edges.append(GraphEdge(
            id=relation.id, source=source, target=target, kind=kind, direct=direct,
            condition=relation.condition, max_iterations=relation.max_iterations, status=status,
            evidence=relation.evidence, relation_ids=references,
        ))

    incoming = defaultdict(set)
    connected = set()
    outgoing_conditions = defaultdict(list)
    for edge in edges:
        if edge.status != "verified":
            continue
        connected.update((edge.source, edge.target))
        if edge.direct and edge.kind != "rework":
            incoming[edge.target].add(edge.source)
        if edge.kind in {"conditional", "rework", "branch"} and edge.condition:
            outgoing_conditions[edge.source].append(edge.condition)
    materials = {item.id: item.name for item in [*model.materials, *model.material_states]}
    for node in nodes:
        if len(nodes) > 1 and node.id not in connected:
            issue("graph_isolated", "缺少已校验的工艺连接", node.id)
        if node.category == "merge" and len(incoming[node.id]) < 2:
            issue("merge_predecessors_pending", "汇合工序缺少至少两个明确的直接前驱", node.id)
        outputs = [materials.get(key, "") for key in node.output_material_ids + node.output_state_ids]
        if any("不合格" in name for name in outputs) and not any(
            "不合格" in condition for condition in outgoing_conditions[node.id]
        ):
            issue("conditional_route_missing", "原文给出不合格产出，后续处理路径待确认", node.id)
    return ProcessGraph(nodes=nodes, edges=edges, issues=issues)
