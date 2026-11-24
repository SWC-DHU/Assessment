"""Project typed process flow from grounded semantic relations."""
from collections import defaultdict

import networkx as nx

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

UNCONDITIONAL = {"declared_predecessor", "direct_transition", "branch", "merge"}
CANONICAL_KIND = {"merge": 0, "branch": 1, "direct_transition": 2, "declared_predecessor": 3}


def flow_endpoints(relation):
    if FLOW[relation.type][0]:
        return relation.target_id, relation.source_id
    return relation.source_id, relation.target_id


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
    by_id = {node.id: node for node in nodes}
    grouped = defaultdict(list)
    issues = []

    def issue(code, message, path, status="pending"):
        issues.append(Issue(code=code, message=message, path=path, status=status))

    for relation in model.relations:
        if relation.type not in FLOW:
            continue
        _, kind, direct = FLOW[relation.type]
        source, target = flow_endpoints(relation)
        if source not in by_id or target not in by_id or source == target:
            issue("graph_endpoint", "工艺边存在无效端点或自连接", relation.id, "invalid")
            continue
        status = relation.status
        evidence = [*relation.evidence, *(relation.review_evidence if relation.review == "confirmed" else [])]
        if not any(item.status == "verified" for item in evidence) and status == "verified":
            status = "pending"
        if kind in UNCONDITIONAL and by_id[target].category == "merge":
            kind = "merge"
            evidence.extend(cite for cite in by_id[target].evidence
                            if CATEGORIES.get(cite.quote.strip().lower()) == "merge" and cite.status == "verified")
        key = (source, target, "direct" if kind in UNCONDITIONAL else kind, relation.condition,
               relation.max_iterations, status)
        grouped[key].append(GraphEdge(
            id=relation.id, source=source, target=target, kind=kind, direct=direct,
            condition=relation.condition, max_iterations=relation.max_iterations, status=status,
            evidence=evidence, relation_ids=[relation.id],
        ))

    edges = []
    for parallel in grouped.values():
        edge = min(parallel, key=lambda item: CANONICAL_KIND.get(item.kind, 0))
        citations = {(cite.document_id, cite.fragment_id, cite.quote): cite
                     for candidate in parallel for cite in candidate.evidence}
        edge.evidence = list(citations.values())
        edge.relation_ids = [candidate.id for candidate in parallel]
        edges.append(edge)

    direct_graph = nx.DiGraph()
    direct_graph.add_nodes_from(by_id)
    direct_graph.add_edges_from((edge.source, edge.target) for edge in edges
                                if edge.status == "verified" and edge.direct and edge.kind != "rework")
    if nx.is_directed_acyclic_graph(direct_graph):
        for layer, generation in enumerate(nx.topological_generations(direct_graph)):
            for node_id in generation:
                by_id[node_id].layer = layer
    route_graph = nx.DiGraph()
    route_graph.add_nodes_from(by_id)
    route_graph.add_edges_from((edge.source, edge.target) for edge in edges if edge.status == "verified")
    outgoing_conditions = defaultdict(list)
    for edge in edges:
        if edge.status != "verified":
            continue
        if edge.kind in {"conditional", "rework", "branch"} and edge.condition:
            outgoing_conditions[edge.source].append(edge.condition)
    materials = {item.id: item.name for item in [*model.materials, *model.material_states]}
    for node in nodes:
        if len(nodes) > 1 and route_graph.degree(node.id) == 0:
            issue("graph_isolated", "缺少已校验的工艺连接", node.id)
        if node.category == "merge" and direct_graph.in_degree(node.id) < 2:
            issue("merge_predecessors_pending", "汇合工序缺少至少两个明确的直接前驱", node.id)
        outputs = [materials.get(key, "") for key in node.output_material_ids + node.output_state_ids]
        if any("不合格" in name for name in outputs) and not any(
            "不合格" in condition for condition in outgoing_conditions[node.id]
        ):
            issue("conditional_route_missing", "原文给出不合格产出，后续处理路径待确认", node.id)
    return ProcessGraph(nodes=nodes, edges=edges, issues=issues)
