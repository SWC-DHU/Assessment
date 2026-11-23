import type { SemanticEntity, SemanticModel, SemanticRelation } from "../types";

export const groups = [
  ["products", "产品"], ["operations", "工序"], ["materials", "物料"],
  ["material_states", "物料状态"], ["capabilities", "制造能力"], ["constraints", "约束"],
] as const;

const labels: Record<string, string> = {
  hasPredecessor: "直接依赖", branchesTo: "分支", mergesInto: "汇合", reworksTo: "返工",
  conditionallyPrecedes: "条件", hasComponent: "组成", consumes: "消耗", produces: "产出",
  requiresCapability: "需要能力", appliesTo: "作用于", hasInputState: "输入状态", hasOutputState: "输出状态", stateOf: "归属物料",
  precedes: "先后", dependsOn: "依赖", directlyPrecedes: "直接转序", optionalNext: "可选转序",
  declared_predecessor: "直接前驱", direct_transition: "直接转序", precedence: "先后", dependency: "依赖",
  branch: "分支", merge: "汇合", conditional: "条件", rework: "返工", optional: "可选转序",
};

export function relationLabel(edge: Pick<SemanticRelation, "type">) {
  return labels[edge.type] ?? edge.type;
}

const conceptLabels: Record<string, string> = {
  Product: "产品", Operation: "工序", Material: "物料", MaterialState: "物料状态",
  Capability: "制造能力", Constraint: "约束", ProductionLine: "整线",
};

export function conceptLabel(concept: string) {
  return conceptLabels[concept] ?? concept;
}

export function entityTitle(entity: SemanticEntity) {
  return [entity.number, entity.name].filter(Boolean).join(" · ");
}

export function semanticEntities(model: SemanticModel) {
  const entities = new Map(groups.flatMap(([group]) => model[group]).map((entity) => [entity.id, entity]));
  if (!entities.has("line") && (model.relations.some((edge) => edge.target_id === "line" || edge.source_id === "line") ||
    model.constraints.some((constraint) => constraint.target_id === "line"))) {
    entities.set("line", { id: "line", name: "整线", concept: "ProductionLine", status: "unprocessed",
      evidence: model.constraints.filter((item) => item.target_id === "line").flatMap((item) => item.evidence) });
  }
  return entities;
}

export function relationTitle(edge: SemanticRelation, entities: ReadonlyMap<string, SemanticEntity>) {
  const name = (id: string) => {
    const entity = entities.get(id);
    return entity ? entityTitle(entity) : `未解析实体 ${id}`;
  };
  return `${name(edge.source_id)} → ${relationLabel(edge)} → ${name(edge.target_id)}`;
}
