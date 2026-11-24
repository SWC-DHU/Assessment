"""Build a traceable model from explicit spreadsheet facts and qualified candidates."""
from collections import defaultdict
from datetime import datetime, timezone
from functools import lru_cache
import json
from math import isfinite
import re
from time import perf_counter

from pydantic import ValidationError

from . import candidates as candidate_schema
from .catalog import candidates, capability_terms, load_vocabulary, normalize, normalize_term, ontology_metadata
from .modeling_task import TASK_KIND, build_modeling_task, task_statement
from .process_graph import build_process_graph
from .schema import (Capability, Constraint, InformationFlow, Issue, Material, MaterialState, Objective, Operation,
                     ProcessRelation, Product, QuantitySpecification, RequirementMetadata, SCHEMA_VERSION,
                     SemanticModel)
from .sources import Document, Fragment, digest, locate
from .structured_text import operation_declarations
from .validation import (COLLECTIONS, COMPUTED_ISSUE_CODES, QUANTITY, canonical_unit, constraint_strength, measurements,
                         summarize_validation, tabular_measurements, validate_model)

MODELS = dict(zip(COLLECTIONS, (Product, Operation, Material, MaterialState, Capability, Constraint)))
CANDIDATES = dict(zip((*COLLECTIONS, "objectives", "relations"), (
    candidate_schema.ProductCandidate, candidate_schema.OperationCandidate, candidate_schema.MaterialCandidate,
    candidate_schema.MaterialStateCandidate, candidate_schema.NamedCandidate, candidate_schema.ConstraintCandidate,
    candidate_schema.NamedCandidate, candidate_schema.RelationCandidate,
)))
HEADERS = {
    "product": ("产品名称", "产品", "product name"),
    "model_number": ("产品型号", "零件图号", "图号", "part number"),
    "components": ("产品组件", "产品组成", "product components"),
    "number": ("工序编号", "工序id", "工序号", "工序序号", "序号", "operation id", "seq"),
    "name": ("工序名称", "工艺步骤", "生产工序", "operation", "operation name"),
    "capabilities": ("制造能力需求", "制造能力", "所需能力", "capability"),
    "consumes": ("输入物料", "投入物料", "input materials"),
    "produces": ("输出物料", "产出物料", "output materials"),
    "operation_type": ("工序类型", "operation type"),
    "predecessors": ("前置工序", "前驱工序", "predecessors"),
    "next": ("下一工序", "直接后续工序", "下一工序编号", "next operation"),
    "next_condition": ("转序条件", "流转条件", "transition condition"),
    "pass_next": ("合格后工序", "合格下一工序"),
    "fail_next": ("不合格后工序", "不合格下一工序"),
    "quality": ("关键工艺约束", "质量要求", "工艺约束", "quality requirements"),
    "equipment": ("核心设备", "设备要求", "设备型号", "设备名称", "equipment"),
    "process_time": ("单件工时", "单件工时(s)", "单件工时（秒）", "工时", "工时(秒)", "process time"),
    "wait_time": ("等待时间",),
    "buffer_time": ("缓存时间", "缓冲时间"),
    "takt_time": ("生产节拍", "节拍"),
}
HEADER_INDEX = {normalize(alias): key for key, aliases in HEADERS.items() for alias in aliases}
MEASUREMENT_PARAMETERS = tuple(load_vocabulary()["parameters"])
EMPTY = {"", "-", "—", "–", "无", "n/a", "未明确", "未知"}
SPLIT_LIST = re.compile(r"[；;、，,\n]+")
COMPONENT = re.compile(r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>个|件|套|台|块|根|片|组)\s*(?P<name>\S.*)")
ORDINALS = dict(zip("一二三四五六七八九十", range(1, 11)))
REVISION = re.compile(
    r"(?:把|将)?\s*(?:第(?P<ordinal>[一二三四五六七八九十\d]+)道工序|工序\s*(?P<number>[A-Za-z0-9_-]+))"
    r"\s*的?\s*(?P<parameter>单件工时|工时|等待时间|缓存时间|缓冲时间|生产节拍|节拍)"
    r"\s*(?:修改为|改为|改成|调整为)\s*\d+(?:\.\d+)?\s*(?:秒钟|秒|分钟|小时|s|min|h)"
    r"(?:\s*/\s*[\w]+)?[。.!！]?"
)
RELATION_WORDS = {
    "hasComponent": r"包含|由.+组成|包括|contains|consists of",
    "hasPredecessor": r"前置|直接前驱|直接前序|direct predecessor",
    "consumes": r"消耗|投入|输入|使用|consumes|input|uses",
    "produces": r"产出|产生|输出|生成|produces|output",
    "requiresCapability": r"需要|要求|需具备|requires",
    "appliesTo": r".",
    "branchesTo": r"分支|旁路|branch",
    "mergesInto": r"汇合|并入|合流|merge",
    "reworksTo": r"返工|返修|rework",
    "conditionallyPrecedes": r"如果|若|满足|当.+时|合格|condition",
    "precedes": r"先于|之前|before|precedes",
    "dependsOn": r"依赖|取决于|depends on",
    "directlyPrecedes": r"直接转入|紧接|下一道|directly",
    "optionalNext": r"可选|可跳过|可进入|optional",
    "stateOf": r"状态|属于|源于|state",
    "hasInputState": r"输入|消耗|需要|input",
    "hasOutputState": r"输出|产出|产生|output",
}


def record_id(prefix: str, value: str) -> str:
    return f"{prefix}:{digest(normalize(value))[:16]}"


def trace(stage: str, started: datetime, clock: float, status: str, summary: str, code: str | None = None) -> dict:
    return {"stage": stage, "started_at": started.isoformat(), "ended_at": datetime.now(timezone.utc).isoformat(),
            "duration_ms": round((perf_counter() - clock) * 1000, 2), "status": status,
            "summary": summary, "error_code": code}


def timestamp() -> tuple[datetime, float]:
    return datetime.now(timezone.utc), perf_counter()


@lru_cache(maxsize=512)
def column_kind(text: str) -> str | None:
    if heading := HEADER_INDEX.get(normalize(text)):
        return heading
    name = re.sub(r"\s*[（(][^()（）]+[）)]\s*$", "", text)
    matches = candidates("parameters", name)
    return matches[0] if len(matches) == 1 else None


def table_rows(documents: list[Document], covered: set[tuple[str, str]] | None = None):
    for doc in documents:
        for rows in doc.sheets.values():
            columns = {}
            for row in rows:
                headers = {kind: idx for idx, cell in enumerate(row) if (kind := column_kind(cell.text))}
                if {"number", "name"} <= headers.keys():
                    columns = headers
                    header_cells = row
                    if covered is not None:
                        covered.update((row[index].document_id, row[index].key) for index in headers.values())
                    continue
                if not columns:
                    continue
                data = {key: row[idx] for key, idx in columns.items()
                        if idx < len(row) and normalize(row[idx].text) not in EMPTY}
                if {"number", "name"} <= data.keys():
                    if "predecessors" in columns and columns["predecessors"] < len(row):
                        cell = row[columns["predecessors"]]
                        if cell.text.strip() and normalize(cell.text) not in {"未知", "未明确", "n/a"}:
                            data["predecessors"] = cell
                    for key in (*MEASUREMENT_PARAMETERS, "pass_next", "fail_next"):
                        if key in data:
                            data[f"{key}_heading"] = header_cells[columns[key]]
                    if covered is not None:
                        covered.update((cell.document_id, cell.key) for cell in data.values())
                    yield data


def candidate_errors(payload: dict) -> list[str]:
    try:
        candidate_schema.ExtractionCandidates.model_validate(payload)
    except ValidationError as exc:
        return [f"{'.'.join(map(str, entry['loc']))}: {entry['msg']}"
                for entry in exc.errors(include_input=False, include_url=False)]
    return []


def revision_request(text: str) -> bool:
    return bool(REVISION.fullmatch(text.strip()))


def information_term(term: str) -> bool:
    return normalize(term) in load_vocabulary()["information_outputs"]


def material_base(term: str) -> str:
    states = load_vocabulary()["material_states"]
    name = re.sub(r"[（(]\s*如有\s*[）)]$", "", term).strip()
    for prefix in states["prefixes"]:
        if name.startswith(prefix) and len(name) - len(prefix) >= 2:
            return name[len(prefix):]
    for suffix in states["suffixes"]:
        if name.endswith(suffix) and len(name) - len(suffix) >= 2:
            return name[:-len(suffix)]
    for pattern in states["modifier_patterns"]:
        if match := re.fullmatch(pattern, name):
            return match["material"]
    return term


def entity_terms(fact) -> list[str]:
    if not fact or not hasattr(fact, "name"):
        return []
    terms = [fact.name]
    if isinstance(fact, Capability):
        terms.extend(load_vocabulary()["capabilities"].get(fact.code, ()))
    if isinstance(fact, Operation) and fact.number:
        terms.append(fact.number)
    return list(dict.fromkeys(terms))


def entity_pattern(terms: list[str]) -> str:
    alternatives = [rf"(?<![A-Za-z0-9_-]){re.escape(term)}(?![A-Za-z0-9_-])" if term.isascii()
                    else re.escape(term) for term in terms]
    return "(?:" + "|".join(alternatives) + ")"


def component_term(term: str, product_name: str) -> tuple[str, QuantitySpecification | None]:
    parsed = COMPONENT.fullmatch(term)
    if not parsed:
        return term, None
    value = float(parsed["value"])
    return parsed["name"], (QuantitySpecification(value=value, unit=parsed["unit"], basis=f"每{product_name}")
                            if isfinite(value) and value > 0 else None)


class Builder:
    def __init__(self, documents: list[Document], run_id: str, base: SemanticModel | None = None):
        metadata = ontology_metadata()
        refs = {source.document_id: source for source in (base.source_refs if base else [])}
        refs.update({doc.ref.document_id: doc.ref for doc in documents})
        input_digest = digest(json.dumps(
            sorted((source.kind, source.name, source.content_sha256) for source in refs.values()),
            ensure_ascii=False,
        ))
        self.model = base.model_copy(deep=True, update={
            "run_id": run_id, "parent_version_id": base.run_id, "created_at": datetime.now(timezone.utc),
            "source_refs": list(refs.values()), "input_digest": input_digest,
            "schema_version": SCHEMA_VERSION, "ontology_version": metadata["ontology_version"],
            "vocabulary_version": metadata["vocabulary"]["version"],
        }) if base else SemanticModel(
            run_id=run_id, ontology_version=metadata["ontology_version"],
            vocabulary_version=metadata["vocabulary"]["version"], source_refs=list(refs.values()),
            input_digest=input_digest,
        )
        self.model.evidences = {}
        self.model.metadata = RequirementMetadata(ontology_version=self.model.ontology_version)
        self.documents = documents
        self.batch = run_id
        self.issues = [issue for issue in base.unresolved_items if issue.code not in COMPUTED_ISSUE_CODES
                       and not issue.code.startswith(("graph_", "batch_")) and not issue.path.startswith("batch-")
                       and issue.code not in {"conditional_route_missing", "merge_predecessors_pending", "file_parse_error"}] if base else []
        self.by_id = {fact.id: fact for group in (*COLLECTIONS, "relations", "modeling_intents")
                      for fact in getattr(self.model, group)}
        self.aliases = {option: item.id for group in COLLECTIONS for item in getattr(self.model, group)
                        for option in (item.id, item.name, getattr(item, "number", None)) if option}
        self.information_aliases = {}
        self.material_state_aliases = {}
        self.stages: list[dict] = []
        self.active_batch: str | None = None
        self.residual_cells: set[tuple[str, str]] = set()
        if base and base.ontology_version != self.model.ontology_version:
            self.upgrade_legacy_facts()

    def issue(self, code: str, message: str, path: str, status: str = "pending") -> None:
        item = Issue(code=code, message=message, path=path, status=status)
        if item not in self.issues:
            self.issues.append(item)

    def add(self, group, fact, *, merge_fields=()):
        previous = self.by_id.get(fact.id)
        if previous is None:
            self.by_id[fact.id] = fact
            getattr(self.model, group).append(fact)
            return fact
        if type(previous) is not type(fact) or (hasattr(fact, "name") and normalize(previous.name) != normalize(fact.name)
                                             and not isinstance(fact, Constraint)):
            self.issue("identity_conflict", "相同标识对应不同实体，需要核对数据源", fact.id, "conflict")
            return previous
        for key in merge_fields:
            old, new = getattr(previous, key), getattr(fact, key)
            if old is None and new is not None:
                setattr(previous, key, new)
                if key == "equipment_requirement":
                    previous.equipment_evidence = fact.equipment_evidence
            elif old is not None and new is not None and old != new:
                self.issue("field_conflict", f"字段 {key} 在来源之间不一致", fact.id, "conflict")
        if isinstance(fact, Capability):
            previous.raw_names = list(dict.fromkeys([*previous.raw_names, *fact.raw_names]))
        if isinstance(fact, ProcessRelation) and fact.asserted_as == "explicit" and previous.asserted_as == "inferred":
            previous.asserted_as = "explicit"
        known = {(entry.document_id, entry.fragment_id, entry.quote) for entry in previous.evidence}
        previous.evidence.extend(entry for entry in fact.evidence
                                 if (entry.document_id, entry.fragment_id, entry.quote) not in known)
        return previous

    def upgrade_legacy_facts(self):
        relations = defaultdict(list)
        for relation in self.model.relations:
            relations[relation.source_id].append(relation)
            relations[relation.target_id].append(relation)
        removed = set()
        state_bases = {state.material_id for state in self.model.material_states}
        for material in self.model.materials:
            references = relations[material.id]
            if (not information_term(material.name) or material.review == "confirmed"
                    or not references or material.id in state_bases):
                continue
            if any(edge.type not in {"consumes", "produces"} or edge.status != "verified" or edge.review == "confirmed"
                   or not isinstance(self.by_id.get(edge.source_id), Operation) for edge in references):
                continue
            for edge in references:
                self.information_flow(self.by_id[edge.source_id], edge.type, material.name, edge.evidence)
                removed.add(edge.id)
            removed.add(material.id)
        self.model.materials = [item for item in self.model.materials if item.id not in removed]
        self.model.relations = [item for item in self.model.relations if item.id not in removed]
        for key in removed:
            self.by_id.pop(key, None)
        self.aliases = {name: key for name, key in self.aliases.items() if key not in removed}

    def information_flow(self, operation, kind, term, evidence):
        direction = "input" if kind == "consumes" else "output"
        flow = next((item for item in operation.information_flows
                     if (item.name, item.direction) == (term, direction)), None)
        if flow:
            known = {(item.document_id, item.fragment_id, item.quote) for item in flow.evidence}
            flow.evidence.extend(item for item in evidence if (item.document_id, item.fragment_id, item.quote) not in known)
        else:
            operation.information_flows.append(InformationFlow(name=term, direction=direction, evidence=evidence))

    def material_flow(self, operation, kind, term, evidence, origin="document"):
        if information_term(term):
            self.information_flow(operation, kind, term, evidence)
            return
        base = material_base(term)
        part = self.add("materials", Material(id=record_id("material", base), name=base, evidence=evidence))
        self.relation(kind, operation.id, part.id, evidence, origin=origin)
        if base != term:
            self.material_state(term, part.id, operation.id, kind, evidence, origin)

    def quality_constraints(self, target, cell):
        vocabulary = load_vocabulary()
        for raw in re.split(r"[。；;\n]", cell.text):
            clause = raw.strip()
            if not clause:
                continue
            cite = [cell.evidence(self.batch, clause)]
            parameters = [(alias, code) for code, spec in vocabulary["parameters"].items()
                          for alias in spec["aliases"] if alias in clause]
            values = list(measurements(clause))
            if len(values) == 1 and parameters:
                alias, code = max(parameters, key=lambda match: len(match[0]))
                if {key for term, key in parameters if term not in alias} <= {code}:
                    operator, value, upper, unit, basis = values[0]
                    if operator:
                        self.constraint(alias, target, cite, parameter_code=code, comparator=operator,
                                        value=value if upper is None else None, lower=value if upper is not None else None,
                                        upper=upper, unit=unit, basis=basis, description=clause)
                        continue
            qualitative = vocabulary["quality_parameters"]
            tags = [code for code, spec in qualitative.items() if any(alias in clause for alias in spec["aliases"])]
            pending_numeric = bool(re.search(r"\d", clause) and (values or re.search(r"[<>=≤≥±]|不超过|至少", clause)))
            classification = "condition" if re.search(r"后才|到位后|方可|不得进入|仅当", clause) else "quality"
            label = "、".join(qualitative[tag]["label"] for tag in tags)
            item = self.constraint(label or ("工艺条件" if classification == "condition" else "质量要求"), target, cite,
                                   description=clause, constraint_type=classification,
                                   parameter_code=tags[0] if len(tags) == 1 else None,
                                   related_parameter_codes=tags if len(tags) > 1 else [])
            if pending_numeric:
                self.issue("measurement_unparsed", "约束含有未唯一解析的量化表达，请核对参数、数值及单位", item.id)
                self.residual_cells.add((cell.document_id, cell.key))

    def relation(self, kind, source, target, evidence, *, origin="document", condition=None, max_iterations=None,
                 quantity=None, asserted_as="explicit"):
        suffix = quantity.model_dump_json() if quantity else ""
        identity = f"{kind}|{source}|{target}|{condition or ''}|{max_iterations or ''}" + (f"|{suffix}" if suffix else "")
        value = ProcessRelation(id=record_id("rel", identity),
                                type=kind, source_id=source, target_id=target, evidence=evidence, origin=origin,
                                condition=condition, max_iterations=max_iterations, quantity=quantity, asserted_as=asserted_as)
        return self.add("relations", value)

    def constraint(self, name, target, evidence, *, description=None, **attributes):
        if "strength" not in attributes:
            attributes["strength"] = constraint_strength(evidence)
        # Canonical parameter and amount determine numeric fact identity across wordings.
        identity = [attributes.get("parameter_code") or name, target, description] + [
            attributes.get(key) for key in ("comparator", "value", "lower", "upper", "unit", "basis")
        ]
        key = json.dumps(identity, ensure_ascii=False)
        parameter = attributes.get("parameter_code")
        attributes.setdefault("constraint_type", parameter if parameter in {"process_time", "wait_time", "buffer_time", "takt_time"}
                              else "numeric" if parameter else "quality" if description else "general")
        value = Constraint(id=record_id("constraint", key), name=name, target_id=target, evidence=evidence,
                           description=description, **attributes)
        stored = self.add("constraints", value)
        if stored is not value and stored.review != "confirmed":
            stored.strength = constraint_strength(stored.evidence)
        self.relation("appliesTo", stored.id, target, evidence,
                      origin="user" if any(item.location.message_id for item in evidence) else "document")
        return stored

    def table(self, rows):
        for row in rows:
            number, name = row["number"], row["name"]
            key = f"operation:{number.text.strip()}"
            kind, equip = row.get("operation_type"), row.get("equipment")
            predecessor = row.get("predecessors")
            declared = None
            if predecessor:
                declared = list(dict.fromkeys(token for token in re.split(r"[；;、，,\s]+", predecessor.text)
                                               if normalize(token) not in EMPTY | {"none"}))
            operation = Operation(
                id=key, name=name.text, number=number.text.strip(), operation_type=kind.text if kind else None,
                equipment_requirement=equip.text if equip else None,
                equipment_evidence=[equip.evidence(self.batch)] if equip else [], precursor_declaration=declared,
                evidence=[name.evidence(self.batch), number.evidence(self.batch),
                          *([kind.evidence(self.batch)] if kind else []),
                          *([predecessor.evidence(self.batch)] if predecessor and not declared else [])],
            )
            operation = self.add("operations", operation, merge_fields=("operation_type", "equipment_requirement", "precursor_declaration"))
            self.aliases[operation.number] = key
            self.aliases[operation.name] = key
            if product_name := row.get("product"):
                model_number = row.get("model_number")
                product = Product(
                    id=record_id("product", product_name.text), name=product_name.text,
                    part_number=model_number.text if model_number else None,
                    evidence=[product_name.evidence(self.batch), *([model_number.evidence(self.batch)] if model_number else [])],
                )
                self.add("products", product, merge_fields=("part_number",))
                if components := row.get("components"):
                    for label in (part.strip() for part in SPLIT_LIST.split(components.text)):
                        if normalize(label) in EMPTY:
                            continue
                        name, quantity = component_term(label, product.name)
                        item = Material(id=record_id("material", name), name=name,
                                        evidence=[components.evidence(self.batch, label)])
                        self.add("materials", item)
                        self.relation("hasComponent", product.id, item.id, item.evidence[:1], quantity=quantity)
            material_terms = {name.strip() for field in ("consumes", "produces") if field in row
                              for name in SPLIT_LIST.split(row[field].text)}
            for field in ("consumes", "produces", "capabilities"):
                if field not in row:
                    continue
                cell = row[field]
                for original in SPLIT_LIST.split(cell.text):
                    term = original.strip()
                    if not term or normalize(term) in EMPTY:
                        continue
                    if field == "capabilities":
                        material_phrase = re.sub(r"^(?:汇合|合流|并入)\s*", "", term)
                        items = re.split(r"与|和|及", material_phrase)
                        if items and all(item.strip() in material_terms for item in items):
                            continue
                        for short in capability_terms(term):
                            item = Capability(id=record_id("capability", short), name=short, raw_names=[term],
                                              evidence=[cell.evidence(self.batch, short)])
                            self.add("capabilities", item)
                            self.relation("requiresCapability", key, item.id, [cell.evidence(self.batch, term)])
                    else:
                        self.material_flow(operation, field, term, [cell.evidence(self.batch, term)])
            for token in declared or []:
                self.relation("hasPredecessor", key, f"operation:{token}", [predecessor.evidence(self.batch)])
            for column, outcome in (("next", None), ("pass_next", "合格"), ("fail_next", "不合格")):
                if target_cell := row.get(column):
                    condition = row.get("next_condition") if column == "next" else row[f"{column}_heading"]
                    for target in (item.strip() for item in SPLIT_LIST.split(target_cell.text)):
                        if normalize(target) in EMPTY:
                            continue
                        kind = "conditionallyPrecedes" if condition else "directlyPrecedes"
                        evidence = [target_cell.evidence(self.batch), *([condition.evidence(self.batch)] if condition else [])]
                        self.relation(kind, key, f"operation:{target}", evidence,
                                      condition=outcome or (condition.text if condition else None))
            if quality := row.get("quality"):
                self.quality_constraints(key, quality)
            for parameter in MEASUREMENT_PARAMETERS:
                if cell := row.get(parameter):
                    heading = row[f"{parameter}_heading"]
                    raw_values = tabular_measurements(cell.text, heading.text)
                    for operator, value, upper, unit, basis in raw_values:
                        self.constraint(load_vocabulary()["parameters"][parameter]["aliases"][0], key,
                                        [cell.evidence(self.batch), heading.evidence(self.batch)],
                                        parameter_code=parameter, comparator=operator or "=",
                                        value=value if upper is None else None, lower=value if upper is not None else None,
                                        upper=upper, unit=unit, basis=basis)
                    if not raw_values:
                        pending = self.constraint(load_vocabulary()["parameters"][parameter]["aliases"][0], key,
                                                  [cell.evidence(self.batch), heading.evidence(self.batch)],
                                                  parameter_code=parameter, description=cell.text)
                        self.issue("measurement_unparsed", f"{parameter} 的单元格缺少可核验的数值及单位", pending.id)
                        self.residual_cells.add((cell.document_id, cell.key))

    def material_state(self, name, material_id, operation_id, flow, evidence, origin="document"):
        state = MaterialState(id=record_id("material-state", f"{material_id}|{name}"), name=name,
                              material_id=material_id, evidence=evidence)
        self.add("material_states", state)
        self.relation("hasInputState" if flow == "consumes" else "hasOutputState", operation_id, state.id, evidence,
                      origin=origin)

    def stated_operations(self, fragment, covered):
        declarations = list(operation_declarations(fragment.text))
        for item in declarations:
            key = f"operation:{item.number}"
            self.add("operations", Operation(id=key, name=item.name, number=item.number,
                                             evidence=[fragment.evidence(self.batch, item.quote, "rule")]))
            self.aliases[item.number] = key
            self.aliases[item.name] = key
        for item in declarations:
            key = f"operation:{item.number}"
            complete = item.fully_read
            for kind, quote, names in item.fields:
                evidence = [fragment.evidence(self.batch, quote, "rule")]
                for name in names:
                    if kind == "hasPredecessor":
                        matches = [op.id for op in self.model.operations if op.id == name or op.number == name]
                        if not matches:
                            matches = [op.id for op in self.model.operations if op.name == name]
                        target = matches[0] if len(matches) == 1 else None
                        if target:
                            self.relation(kind, key, target, evidence, origin="user")
                        else:
                            self.issue("predecessor_unresolved", f"前置工序 {name} 缺少唯一标识", key)
                            complete = False
                    elif kind == "requiresCapability":
                        fact = Capability(id=record_id("capability", name), name=name, evidence=evidence)
                        self.add("capabilities", fact)
                        self.relation(kind, key, fact.id, evidence, origin="user")
                    else:
                        self.material_flow(self.by_id[key], kind, name, evidence, "user")
            if complete and covered is not None:
                covered.add((fragment.document_id, fragment.key, item.quote))
        return len(declarations)

    def explicit_requirements(self, document: Document, covered=None) -> int:
        return sum(self.fragment_requirements(fragment, covered) for fragment in document.fragments
                   if fragment.location.cell is None)

    def fragment_requirements(self, fragment, covered=None):
        count = self.fragment_line_constraints(fragment, covered) + self.stated_operations(fragment, covered)
        pattern = r"每(?:个|件|台|套)(?P<product>[^，。；\n]{1,32}?)由(?P<parts>[^，。；\n]{1,110}?)组成"
        for match in re.finditer(pattern, fragment.text):
            product_name = match["product"].strip()
            evidence = [fragment.evidence(self.batch, match[0], "rule")]
            product = self.add("products", Product(
                id=record_id("product", product_name), name=product_name, evidence=evidence,
            ))
            for part in re.split(r"及|和|、", match["parts"]):
                part = part.strip()
                if not part:
                    continue
                name, quantity = component_term(part, product_name)
                material = self.add("materials", Material(id=record_id("material", name), name=name, evidence=evidence))
                self.relation("hasComponent", product.id, material.id, evidence, origin="user", quantity=quantity)
                count += 1
        clauses = re.split(r"[。！？.!?；;\n]", fragment.text)
        for clause in clauses:
            if statement := task_statement(clause):
                self.add("modeling_intents", Objective(
                    id=record_id("intent", TASK_KIND), kind=TASK_KIND,
                    description=statement, evidence=[fragment.evidence(self.batch, statement, "rule")],
                ))
                count += 1
        intents = {
            "resource_matching": r"设备匹配|资源匹配",
            "model_linking": r"模型关联构建|模型关联",
            "simulation_validation": r"仿真验证|仿真校核",
        }
        for kind, words in intents.items():
            for clause in clauses:
                match = re.search(words, clause)
                if match and not re.search(rf"(?:不需要|无需|不做|禁止)\s*(?:{words})", clause):
                    evidence = [fragment.evidence(self.batch, match[0], "rule")]
                    intent = Objective(id=record_id("intent", kind), kind=kind,
                                            description=match[0], evidence=evidence)
                    self.add("modeling_intents", intent)
                    count += 1
                    break
        return count

    def explicit_line_constraints(self, document: Document, covered=None) -> int:
        return sum(self.fragment_line_constraints(fragment, covered) for fragment in document.fragments)

    def fragment_line_constraints(self, fragment: Fragment, covered=None) -> int:
        count = 0
        vocabulary = load_vocabulary()["parameters"]
        aliases = sorted({alias for parameter in vocabulary.values() for alias in parameter["aliases"]},
                         key=lambda value: (-len(value), value))
        parameter_pattern = "|".join(map(re.escape, aliases))
        pattern = re.compile(rf"(?:^|(?<=[。；;\n]))\s*(?:要求\s*)?(?:整线|产线|生产线)\s*(?:目标)?\s*(?P<parameter>{parameter_pattern})"
                             r"\s*(?P<quantity>[^，,。；;\n]+)(?P<end>[。；;\n]|$)?")
        for match in pattern.finditer(fragment.text):
            literal = match["quantity"].strip()
            if not QUANTITY.fullmatch(literal):
                continue
            terms = list(measurements(literal))
            codes = candidates("parameters", match["parameter"])
            if len(terms) != 1 or len(codes) != 1:
                continue
            operator, value, upper, unit, basis = terms[0]
            if operator is None:
                continue
            self.constraint(match["parameter"], "line", [fragment.evidence(self.batch, match[0], "rule")],
                            parameter_code=codes[0], comparator=operator, unit=unit, basis=basis,
                            value=value if upper is None else None, lower=value if upper is not None else None,
                            upper=upper)
            if covered is not None and (match["end"] or match.end() == len(fragment.text)):
                covered.add((fragment.document_id, fragment.key, match[0].strip().rstrip("。；;")))
            count += 1
        return count

    def citations(self, supplied, path):
        evidence = []
        for citation in supplied:
            batch = f"{self.batch}/{self.active_batch}" if self.active_batch else self.batch
            hits = locate(self.documents, citation.quote, batch,
                          document_id=citation.document_id, fragment_id=citation.fragment_id)
            if not hits:
                self.issue("evidence_not_located", "无法唯一定位候选引文", path)
            evidence.extend(hits)
        return evidence

    def candidate_rows(self, payload, scopes):
        for group in payload.keys() - CANDIDATES.keys():
            self.issue("candidate_shape", "未知候选字段", group, "invalid")
        for group, schema in CANDIDATES.items():
            raw = payload.get(group, [])
            if not isinstance(raw, list):
                self.issue("candidate_shape", "抽取集合应为数组", group, "invalid")
                continue
            for idx, item in enumerate(raw):
                self.active_batch = scopes.get((group, idx))
                path = ".".join(str(part) for part in (self.active_batch, group, idx) if part is not None)
                try:
                    yield group, schema.model_validate(item), path
                except ValidationError as exc:
                    error = exc.errors(include_input=False, include_url=False)[0]
                    self.issue("candidate_shape", f"{'.'.join(map(str, error['loc']))}: {error['msg']}", path, "invalid")

    def resolve_alias(self, value):
        if value in self.by_id or "::" in value:
            return self.aliases.get(value, value)
        numbered = [item.id for item in self.model.operations if item.number == value]
        if len(numbered) == 1:
            return numbered[0]
        named = [item.id for item in self.by_id.values() if getattr(item, "name", None) == value]
        if len(named) > 1:
            return value
        if named or value in self.aliases:
            return self.aliases.get(value, named[0] if named else value)
        mapped = candidates("capabilities", value)
        matches = [item.id for item in self.model.capabilities if item.code in mapped]
        return matches[0] if len(matches) == 1 else value

    def candidates(self, payload, scopes=None):
        if not payload:
            return
        for group, item, path in self.candidate_rows(payload, scopes or {}):
            evidence = self.citations(item.evidence, path)
            quotes = " ".join(entry.quote for entry in evidence)
            if not evidence:
                continue
            values = item.model_dump(exclude={"id", "evidence", "equipment_evidence"})
            if group == "relations":
                source = self.resolve_alias(item.source_id)
                target = self.resolve_alias(item.target_id)
                state_alias = self.material_state_aliases.get(item.target_id)
                if state_alias is None and isinstance(self.by_id.get(target), MaterialState):
                    state_alias = self.by_id[target]
                if state_alias and item.type in {"hasInputState", "hasOutputState"}:
                    target = state_alias.id
                elif state_alias and item.type in {"consumes", "produces"}:
                    target = state_alias.material_id
                endpoints = (self.by_id.get(source), self.by_id.get(target))
                endpoint_terms = [terms for fact in endpoints if (terms := entity_terms(fact))]
                information = self.information_aliases.get(item.target_id, item.target_id)
                if item.type in {"consumes", "produces"} and isinstance(endpoints[0], Operation) and information_term(information):
                    if not item.inferred and any(re.search(
                        rf"{entity_pattern(endpoint_terms[0])}.{{0,32}}(?:{RELATION_WORDS[item.type]}).{{0,32}}{re.escape(information)}", quote, re.I,
                    ) for quote in (entry.quote for entry in evidence)):
                        self.information_flow(endpoints[0], item.type, information, evidence)
                    else:
                        self.issue("relation_unsubstantiated", "引文未明确支持工序与信息的输入输出", path)
                    continue
                if item.inferred:
                    if len(endpoint_terms) == 2 and item.type in {"dependsOn", "precedes", "directlyPrecedes",
                                                          "conditionallyPrecedes", "branchesTo", "optionalNext", "reworksTo"}:
                        self.relation(item.type, source, target, evidence, origin="model", condition=item.condition,
                                      asserted_as="inferred")
                    else:
                        self.issue("inferred_candidate", "推断缺少可定位的工艺端点", path)
                    continue
                patterns = [entity_pattern(terms) for terms in endpoint_terms]
                support = [entry.quote for entry in evidence if all(re.search(pattern, entry.quote, re.I) for pattern in patterns)]
                if len(patterns) != (1 if target == "line" else 2) or not support:
                    self.issue("relation_unsubstantiated", "引文未同时支持关系端点", path)
                    continue
                matching = [quote for quote in support if re.search(RELATION_WORDS[item.type], quote, re.I)
                            and (not item.condition or item.condition in quote)]
                if not matching:
                    self.issue("relation_unsubstantiated", "引文未明确支持关系类型或条件", path)
                    continue
                if len(patterns) == 2:
                    source_name, target_name = patterns
                    directional_words = {
                        **RELATION_WORDS, "reworksTo": r"返工(?:至|到|回)|返修(?:至|到|回)|返回|rework to",
                        "mergesInto": r"汇合|并入|汇入|合流|merge", "conditionallyPrecedes": r"后|下一步|再|then",
                    }
                    forward = rf"{source_name}.{{0,32}}(?:{directional_words[item.type]}).{{0,32}}{target_name}"
                    patterns = (forward,)
                    if item.type == "hasPredecessor":
                        patterns = (
                            rf"{source_name}.{{0,35}}(?:前置|直接前驱|直接前序|direct predecessor).{{0,18}}{target_name}",
                        )
                    elif item.type == "appliesTo":
                        patterns = (rf"{source_name}.{{0,36}}{target_name}", rf"{target_name}.{{0,36}}{source_name}")
                    if not any(re.search(pattern, quote, re.I) for quote in matching for pattern in patterns):
                        code = "precedence_direction" if item.type == "hasPredecessor" else "relation_direction"
                        self.issue(code, "引文未明确支持候选的关系方向", path)
                        continue
                if item.max_iterations is not None and (
                    item.max_iterations < 1 or not re.search(rf"(?<!\d){item.max_iterations}\s*次", quotes)
                ):
                    self.issue("rework_limit_unsubstantiated", "引文未明确支持返工次数", path)
                    continue
                quantity = item.quantity
                if quantity and (item.type != "hasComponent" or not re.search(
                    rf"(?<![\d.]){quantity.value:g}\s*{re.escape(quantity.unit)}\s*{patterns[-1]}", quotes,
                )):
                    self.issue("quantity_unsubstantiated", "引文未支持组件数量与单位", path)
                    quantity = None
                self.relation(item.type, source, target, evidence, origin="model", condition=item.condition,
                              max_iterations=item.max_iterations, quantity=quantity)
                if state_alias and item.type in {"consumes", "produces"}:
                    self.material_state(state_alias.name, target, source, item.type, evidence, "model")
                continue
            if item.name not in quotes:
                self.issue("name_unsubstantiated", "引文未包含候选原始术语", path)
                continue
            if group == "objectives":
                if not any(item.name == intent.description or item.name in intent.description
                           for intent in self.model.modeling_intents):
                    self.add("modeling_intents", Objective(id=record_id("intent", item.name), kind="stated_objective",
                                                           description=item.name, evidence=evidence))
                continue
            if group == "materials" and information_term(item.name):
                self.information_aliases[item.id] = item.name
                continue
            if group == "materials" and (base_name := material_base(item.name)) != item.name:
                material = self.add("materials", Material(id=record_id("material", base_name), name=base_name, evidence=evidence))
                state = self.add("material_states", MaterialState(id=record_id("material-state", f"{material.id}|{item.name}"),
                                                                 name=item.name, material_id=material.id, evidence=evidence))
                self.aliases[item.id] = material.id
                self.material_state_aliases[item.id] = state
                continue
            if group == "constraints":
                target = self.resolve_alias(item.target_id)
                if normalize(target) in {"line", "整线", "产线", "生产线"}:
                    target = "line"
                entity = self.by_id.get(target)
                if (target == "line" and not re.search(r"整线|产线|生产线|\bline\b", quotes, re.I)) or (
                    target != "line" and (not entity or not any(term and term in quotes
                        for term in (getattr(entity, "name", None), getattr(entity, "number", None))))
                ):
                    self.issue("constraint_target_unsubstantiated", "引文未明确支持约束作用对象", path)
                    continue
                params = candidates("parameters", item.name)
                if item.name in load_vocabulary()["quality_parameters"]:
                    params = [item.name]
                if not params and target == "line":
                    params = candidates("parameters", re.sub(r"^(?:整线|产线|生产线)\s*", "", item.name))
                values.pop("target_id")
                values.pop("name")
                if values.get("description") and values["description"] not in quotes:
                    values["description"] = None
                if not any(values[key] is not None for key in ("comparator", "value", "lower", "upper")):
                    values["description"] = values.get("description") or "；".join(entry.quote for entry in evidence)
                self.constraint(item.name, target, evidence, parameter_code=params[0] if len(params) == 1 else None,
                                **{**values, "unit": canonical_unit(item.unit)})
                continue
            for optional in ("number", "operation_type", "material_type", "part_number", "unit"):
                value = values.get(optional)
                if value is not None and (value not in quotes or (optional == "number" and
                        not re.search(rf"(?<![a-zA-Z0-9]){re.escape(value)}(?![a-zA-Z0-9])", quotes))):
                    values[optional] = None
                    self.issue("attribute_unsubstantiated", f"引文未支持字段 {optional}", path)
            if group == "products" and item.quantity is not None:
                amount = f"{item.quantity:g}"
                if item.quantity < 0 or not item.unit or not re.search(
                    rf"(?<![\d.]){re.escape(amount)}\s*{re.escape(item.unit)}", quotes,
                ):
                    values.update(quantity=None, unit=None)
                    self.issue("attribute_unsubstantiated", "引文未支持产品数量与单位", path)
            key = record_id(group.rstrip("s"), item.name)
            if group == "operations":
                matches = [op for op in self.model.operations if op.name == item.name]
                key = f"operation:{values['number']}" if values["number"] else matches[0].id if len(matches) == 1 else key
                equipment = self.citations(item.equipment_evidence, path) if item.equipment_requirement else []
                if item.equipment_requirement and not any(item.equipment_requirement in entry.quote for entry in equipment):
                    self.issue("equipment_unsubstantiated", "设备要求没有独立逐字引文", path)
                    values["equipment_requirement"] = None
                    equipment = []
                values["equipment_evidence"] = equipment
            if group == "material_states":
                material_id = self.resolve_alias(item.material_id)
                material = self.by_id.get(material_id)
                if not isinstance(material, Material) or not all(term in quotes for term in (material.name, item.name)):
                    self.issue("state_material_unsubstantiated", "引文未明确支持物料状态归属", path)
                    continue
                key = record_id("material-state", f"{material_id}|{item.name}")
                values["material_id"] = material_id
                self.relation("stateOf", key, material_id, evidence, origin="model")
            self.aliases[item.id] = key
            self.aliases.setdefault(item.name, key)
            self.add(group, MODELS[group](id=key, evidence=evidence, **values),
                     merge_fields=("equipment_requirement", "operation_type") if group == "operations" else ())

    def revise(self, document: Document) -> bool:
        fragment = document.fragments[0]
        match = REVISION.fullmatch(fragment.text.strip())
        if not match:
            return False
        ordinal, number, parameter = match.group("ordinal", "number", "parameter")
        operations = self.model.operations
        if ordinal:
            index = int(ordinal) if ordinal.isdigit() else ORDINALS.get(ordinal, 0)
            selected = [operations[index - 1]] if 0 < index <= len(operations) else []
        else:
            selected = [operation for operation in operations if operation.number == number]
        if len(selected) != 1:
            self.issue("revision_target_unresolved", "修订目标未能唯一定位，请指定工序编号", "operations")
            return True
        target = selected[0].id
        code = candidates("parameters", parameter)[0]
        values = list(measurements(fragment.text))
        if len(values) != 1 or values[0][0] != "=":
            self.issue("revision_value_unresolved", "修订数值或单位无法核验", target)
            return True
        _, value, _, unit, basis = values[0]
        matching = [item for item in self.model.constraints if item.target_id == target and item.parameter_code == code]
        if any(item.review == "confirmed" for item in matching):
            self.issue("revision_review_required", "已确认的约束需要人工审核后才能修订", target)
            return True
        superseded = {item.id for item in matching}
        self.model.constraints = [item for item in self.model.constraints if item.id not in superseded]
        removed = {item.id for item in self.model.relations if item.source_id in superseded}
        self.model.relations = [item for item in self.model.relations if item.id not in removed]
        for key in superseded | removed:
            self.by_id.pop(key)
        self.constraint(parameter, target, [fragment.evidence(self.batch)], parameter_code=code, comparator="=",
                        value=value, unit=unit, basis=basis, strength="mandatory")
        return True

    def mapping_queue(self) -> list[dict]:
        result = []
        for item in self.model.operations:
            mapped = normalize_term("operations", item.name)
            item.standard_term, item.mapping_basis = mapped["term"], mapped["basis"]
        for item in self.model.capabilities:
            if item.mapping == "adjudicated" or item.review == "confirmed":
                continue
            options = candidates("capabilities", item.name)
            item.candidates = options
            if len(options) == 1:
                item.code = options[0]
                item.standard_term, item.mapping_basis = item.code, "受控能力词表"
                item.mapping = "phrase" if item.raw_names and item.name not in item.raw_names else "exact"
            elif len(options) > 1:
                op_ids = {edge.source_id for edge in self.model.relations if edge.type == "requiresCapability"
                          and edge.target_id == item.id}
                result.append({"id": item.id, "term": item.name, "candidates": options,
                               "operations": [op.name for op in self.model.operations if op.id in op_ids]})
        return result

    def accept_mapping(self, choices: dict) -> None:
        for item in self.model.capabilities:
            choice = choices.get(item.id)
            if len(item.candidates) > 1 and isinstance(choice, str) and choice in item.candidates:
                item.code, item.mapping = choice, "adjudicated"
                item.standard_term, item.mapping_basis = choice, "模型在受控候选中消歧"

    def consolidate_capabilities(self):
        vocabulary = load_vocabulary()["capabilities"]
        canonical = {}
        replacements = {}
        for item in self.model.capabilities:
            if item.code not in vocabulary or (item.mapping == "adjudicated" and item.review != "confirmed"):
                continue
            key = record_id("capability", item.code)
            replacements[item.id] = key
            if key not in canonical:
                canonical[key] = item.model_copy(update={
                    "id": key, "name": vocabulary[item.code][0], "raw_names": [], "evidence": [], "review_evidence": [],
                })
            stored = canonical[key]
            stored.raw_names = list(dict.fromkeys([*stored.raw_names, *(item.raw_names or [item.name])]))
            for field in ("evidence", "review_evidence"):
                existing = getattr(stored, field)
                known = {(cite.document_id, cite.fragment_id, cite.quote) for cite in existing}
                existing.extend(cite for cite in getattr(item, field)
                                if (cite.document_id, cite.fragment_id, cite.quote) not in known)
            if item.review == "confirmed" or (item.mapping == "adjudicated" and stored.review != "confirmed"):
                stored.review, stored.mapping, stored.mapping_basis = item.review, item.mapping, item.mapping_basis
        if not replacements:
            return
        self.model.capabilities = list({
            replacements.get(item.id, item.id): canonical.get(replacements.get(item.id), item)
            for item in self.model.capabilities
        }.values())
        for key in replacements:
            self.by_id.pop(key, None)
        self.by_id.update(canonical)

        for constraint in self.model.constraints:
            constraint.target_id = replacements.get(constraint.target_id, constraint.target_id)
        relations = self.model.relations
        self.model.relations = []
        for edge in relations:
            self.by_id.pop(edge.id, None)
        for edge in relations:
            target = replacements.get(edge.target_id, edge.target_id)
            if target == edge.target_id:
                self.add("relations", edge)
            else:
                stored = self.relation(edge.type, edge.source_id, target, edge.evidence, origin=edge.origin,
                                       asserted_as=edge.asserted_as, condition=edge.condition,
                                       max_iterations=edge.max_iterations, quantity=edge.quantity)
                if edge.review == "confirmed":
                    stored.review = edge.review
                    stored.review_evidence.extend(edge.review_evidence)

    def finish(self, tool=None):
        self.consolidate_capabilities()
        self.model.modeling_task = build_modeling_task(self.model)
        started, clock = timestamp()
        if tool:
            tool("validate_semantics", f"{len(self.by_id)} 项带引用的事实", validate_model, self.model, self.issues, tool)
        else:
            validate_model(self.model, self.issues)
        self.stages.append(trace("consistency", started, clock, self.model.validation.status,
                                 f"{len(self.model.validation.issues)} 项诊断"))
        started, clock = timestamp()
        lookup = defaultdict(list)
        inverse = defaultdict(list)
        for relation in self.model.relations:
            if relation.status == "verified" or (relation.status == "pending" and relation.type == "requiresCapability"):
                lookup[relation.type, relation.source_id].append(relation.target_id)
                inverse[relation.type, relation.target_id].append(relation.source_id)
        for entity in self.model.products:
            entity.component_ids = lookup["hasComponent", entity.id]
        for entity in self.model.operations:
            entity.predecessor_ids = list(dict.fromkeys(lookup["hasPredecessor", entity.id]))
            entity.input_material_ids = list(dict.fromkeys(lookup["consumes", entity.id]))
            entity.output_material_ids = list(dict.fromkeys(lookup["produces", entity.id]))
            entity.input_state_ids = list(dict.fromkeys(lookup["hasInputState", entity.id]))
            entity.output_state_ids = list(dict.fromkeys(lookup["hasOutputState", entity.id]))
            entity.capability_ids = list(dict.fromkeys(lookup["requiresCapability", entity.id]))
            entity.constraint_ids = [item.id for item in self.model.constraints if item.target_id == entity.id]
        for entity in self.model.materials:
            entity.consumed_by = inverse["consumes", entity.id]
            entity.produced_by = inverse["produces", entity.id]
        for entity in self.model.capabilities:
            entity.operation_ids = inverse["requiresCapability", entity.id]
            entity.parameter_constraint_ids = [item.id for item in self.model.constraints if item.target_id == entity.id]
        graph_start = timestamp()
        self.model.process_graph = (tool("build_process_graph", f"{len(self.model.operations)} 个工序", build_process_graph, self.model)
                                    if tool else build_process_graph(self.model))
        graph_issues = self.model.process_graph.issues
        summarize_validation(self.model, [*self.model.validation.issues, *graph_issues])
        if not tool:
            self.stages.append(trace("build_process_graph", *graph_start, self.model.validation.status,
                                     f"{len(self.model.process_graph.nodes)} 节点 · {len(self.model.process_graph.edges)} 边"))
        for fact in self.by_id.values():
            for item in [*fact.evidence, *fact.review_evidence, *getattr(fact, "equipment_evidence", [])]:
                key = digest(json.dumps([item.document_id, item.fragment_id, item.quote, item.extraction_batch, item.method],
                                        ensure_ascii=False))
                self.model.evidences[key] = item
        if self.model.modeling_task:
            for item in self.model.modeling_task.review_evidence:
                key = digest(json.dumps([item.document_id, item.fragment_id, item.quote, item.extraction_batch, item.method],
                                        ensure_ascii=False))
                self.model.evidences[key] = item
        for op in self.model.operations:
            for flow in op.information_flows:
                for item in flow.evidence:
                    key = digest(json.dumps([item.document_id, item.fragment_id, item.quote, item.extraction_batch, item.method],
                                            ensure_ascii=False))
                    self.model.evidences[key] = item
        data = self.model.model_dump(mode="json", exclude={"created_at", "run_id", "parent_version_id", "content_digest",
                                                           "source_refs", "input_digest", "metadata", "evidences"})
        def content(value):
            if isinstance(value, dict):
                return {key: content(item) for key, item in value.items()
                        if key not in {"document_id", "extraction_batch", "message_id"}}
            if isinstance(value, list):
                return [content(item) for item in value]
            return value
        self.model.content_digest = digest(json.dumps(content(data), ensure_ascii=False, sort_keys=True))
        self.stages.append(trace("instantiation", started, clock, "success",
                                 f"{len(self.by_id)} 项实体与关系，已建立双向引用"))
        return SemanticModel.model_validate_json(self.model.model_dump_json())


def project(documents: list[Document], run_id: str, payload: dict | None = None) -> SemanticModel:
    builder = Builder(documents, run_id)
    builder.table(list(table_rows(documents)))
    for document in documents:
        builder.explicit_requirements(document)
    builder.candidates(payload)
    builder.mapping_queue()
    return builder.finish()


def legacy_candidates(payload: dict) -> dict:
    """Import the subset of old extraction results for which exact evidence can be rechecked."""
    if isinstance(payload.get("semantic"), dict):
        payload = payload["semantic"]
        return {
            group: [{key: value for key, value in item.items() if key in schema.model_fields}
                    | {"evidence": [
                        {key: entry.get(key) for key in ("quote", "document_id", "fragment_id")}
                        for entry in item.get("evidence", [])
                    ]}
                    for item in payload.get(group, [])]
            for group, schema in CANDIDATES.items()
        }
    converted: dict = {"operations": [], "products": []}
    operations = payload.get("operations")
    for item in operations if isinstance(operations, list) else []:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("evidence"), list):
            converted["operations"].append(item)
            continue
        if not isinstance(item.get("source_quote"), str) or not item.get("name"):
            continue
        number = item.get("seq") or item.get("number")
        converted["operations"].append({
            "id": str(number or item["name"]), "name": item["name"], "number": str(number) if number is not None else None,
            "evidence": [{"quote": item["source_quote"]}],
        })
    return converted
