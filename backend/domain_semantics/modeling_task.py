"""Assemble the task from grounded objectives and explicit user decisions."""
import re

from .schema import ModelingTask, RequirementSemanticModel

TASK_KIND = "digital_twin_modeling"
MODEL_ARTIFACT = r"数字孪生(?:仿真)?模型|digital[ -]twin(?: simulation)? model"
BUILD_REQUEST = re.compile(
    rf"(?:构建|建立|搭建|创建|开发|生成|设计|build|create|develop|construct)\s*.{{0,120}}?(?:{MODEL_ARTIFACT})"
    r"|(?:数字孪生(?:仿真)?模型)(?:的)?(?:构建|建立|搭建)", re.I,
)
NEGATION = re.compile(r"不需要|无需|不必|不要|不做|不用|禁止|取消|不构建|不建立|不生成|不考虑|无需构建|"
                      r"\b(?:do not|don't|no need to|without|must not|not to)\b", re.I)
SCOPES = (
    ("production_line", re.compile(r"产线|生产线|装配线|\b(?:production|assembly|manufacturing) line\b", re.I)),
    ("workstation", re.compile(r"工作站|工位|\bworkstation\b", re.I)),
    ("operation", re.compile(r"工序|\boperation\b", re.I)),
    ("product", re.compile(r"(?:(?:构建|建立|创建|生成)(?:该|此)?产品)|\b(?:build|create)\s+(?:a |the )?product\b", re.I)),
)


def task_statement(clause: str) -> str | None:
    for match in BUILD_REQUEST.finditer(clause):
        preceding = re.split(r"[，,；;]", clause[:match.start()])[-1]
        if not NEGATION.search(preceding + match[0]):
            return clause.strip()
    return None


def stated_scope(statement: str):
    match = BUILD_REQUEST.search(statement)
    target_phrase = match[0] if match else ""
    return next((scope for scope, pattern in SCOPES if pattern.search(target_phrase)), None)


def task_target_options(model: RequirementSemanticModel, statements: list[str]) -> list[str]:
    return [product.id for product in model.products if any(
        re.search(rf"(?<![\w-]){re.escape(product.name)}(?![\w-])", statement, re.I)
        if product.name.isascii() else product.name in statement for statement in statements
    )]


def task_statements(model: RequirementSemanticModel) -> list[str]:
    source_ids = {source.document_id for source in model.source_refs}
    return list(dict.fromkeys(
        statement for item in model.modeling_intents if item.kind == TASK_KIND
        for cite in item.evidence if cite.status == "verified" and cite.document_id in source_ids
        if (statement := task_statement(cite.quote))
    ))


def build_modeling_task(model: RequirementSemanticModel) -> ModelingTask | None:
    statements = task_statements(model)
    previous = model.modeling_task
    if not statements and not (previous and previous.review_evidence):
        return None
    scopes = {scope for statement in statements if (scope := stated_scope(statement))}
    products = task_target_options(model, statements)
    return ModelingTask(
        task_type=TASK_KIND,
        target_scope=previous.target_scope if previous and previous.review_evidence else
        next(iter(scopes)) if len(scopes) == 1 else None,
        target_product_id=previous.target_product_id if previous and previous.review_evidence else
        products[0] if len(products) == 1 else None,
        required_outputs=["digital_twin_model"],
        objective_ids=[item.id for item in model.modeling_intents],
        constraint_ids=[item.id for item in model.constraints if item.target_id == "line"],
        review_evidence=previous.review_evidence if previous else [],
    )
