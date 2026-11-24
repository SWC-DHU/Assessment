from functools import lru_cache
from pathlib import Path
import re
import unicodedata

from rdflib import Graph, Namespace, OWL, RDF, RDFS, URIRef
from rdflib.collection import Collection
import yaml

ROOT = Path(__file__).resolve().parent
MANUFACTURING_IRI = URIRef("https://assessment.example.org/ontology/manufacturing")
M = Namespace(f"{MANUFACTURING_IRI}#")
ONTOLOGY_VERSION = "3.0.0"
HISTORICAL_VERSIONS = ("1.0.0", "2.0.0")
CORE_CONCEPTS = ("Product", "Material", "Operation", "CapabilityRequirement", "Constraint", "Objective")
CORE_RELATIONS = ("hasComponent", "hasPredecessor", "consumes", "produces", "requiresCapability", "appliesTo")
RESOURCE_CONCEPTS = ("ModelAsset", "ResourceCapability", "Interface")
RESOURCE_RELATIONS = (
    "providesCapability", "satisfiesCapability", "hasInterface",
    "handlesMaterial", "supportsState", "addressesConstraint",
)


def ontology_concept(concept: str, version: str = ONTOLOGY_VERSION) -> str:
    # The JSON contract preserves the historical discriminator.
    if concept == "Capability" and version == ONTOLOGY_VERSION:
        return "CapabilityRequirement"
    return concept


@lru_cache
def load_ontology(version: str = ONTOLOGY_VERSION) -> Graph:
    base = ROOT / "ontology"
    if version != ONTOLOGY_VERSION:
        if version not in HISTORICAL_VERSIONS:
            raise KeyError(version)
        base = base / "versions" / version
    graph = Graph()
    for path in sorted(base.glob("*.ttl")):
        graph.parse(path, format="turtle")
    return graph


@lru_cache
def load_vocabulary() -> dict:
    return yaml.safe_load((ROOT / "vocabulary/manufacturing.yaml").read_text(encoding="utf-8"))


def term_id(node) -> str:
    return str(node).removeprefix(str(M))


def endpoints(graph: Graph, node, predicate) -> list[str]:
    result = []
    for value in graph.objects(node, predicate):
        member_list = graph.value(value, OWL.unionOf)
        if member_list:
            result.extend(term_id(member) for member in Collection(graph, member_list))
        else:
            result.append(term_id(value))
    return sorted(result)


@lru_cache
def relation_signatures(version: str = ONTOLOGY_VERSION) -> dict[str, tuple[set[str], set[str]]]:
    graph = load_ontology(version)
    return {
        term_id(node): (set(endpoints(graph, node, RDFS.domain)), set(endpoints(graph, node, RDFS.range)))
        for node in graph.subjects(RDF.type, OWL.ObjectProperty)
    }


def term_details(graph: Graph, node) -> dict:
    return {
        "id": term_id(node), "iri": str(node),
        "label": str(graph.value(node, RDFS.label) or term_id(node)),
        "definition": str(graph.value(node, RDFS.comment) or ""),
        "subclass_of": sorted(term_id(parent) for parent in graph.objects(node, RDFS.subClassOf)),
        "domain": endpoints(graph, node, RDFS.domain),
        "range": endpoints(graph, node, RDFS.range),
    }


@lru_cache
def ontology_metadata(version: str = ONTOLOGY_VERSION) -> dict:
    graph = load_ontology(version)
    classes = sorted((node for node in graph.subjects(RDF.type, OWL.Class) if node in M), key=str)
    relations = sorted(graph.subjects(RDF.type, OWL.ObjectProperty), key=str)
    attributes = sorted(graph.subjects(RDF.type, OWL.DatatypeProperty), key=str)
    vocabulary = load_vocabulary() if version == ONTOLOGY_VERSION else yaml.safe_load(
        (ROOT / "ontology/versions" / version / "vocabulary.yaml").read_text(encoding="utf-8"))
    return {
        "namespace": str(M),
        "ontology_version": str(graph.value(MANUFACTURING_IRI, OWL.versionInfo)),
        "version_iri": str(graph.value(MANUFACTURING_IRI, OWL.versionIRI)),
        "available_versions": [*HISTORICAL_VERSIONS, ONTOLOGY_VERSION],
        "core_concepts": [concept for concept in CORE_CONCEPTS if M[concept] in classes],
        "core_relations": [relation for relation in CORE_RELATIONS if M[relation] in relations],
        "resource_concepts": [concept for concept in RESOURCE_CONCEPTS if M[concept] in classes],
        "resource_relations": [relation for relation in RESOURCE_RELATIONS if M[relation] in relations],
        "demand_resource_mappings": vocabulary.get("demand_resource_mappings", []),
        "instance_concepts": {"Capability": ontology_concept("Capability", version)},
        "concepts": [term_id(node) for node in classes],
        "relations": [term_id(node) for node in relations],
        "classes": [term_details(graph, node) for node in classes],
        "object_properties": [term_details(graph, node) for node in relations],
        "data_properties": [term_details(graph, node) for node in attributes],
        "vocabulary": vocabulary,
    }


def check_ontology() -> None:
    from typing import get_args
    from .schema import RelationKind

    metadata = ontology_metadata()
    classes = set(metadata["concepts"])
    signatures = relation_signatures()
    expected_relations = set(get_args(RelationKind)) | set(RESOURCE_RELATIONS)
    if (metadata["ontology_version"] != ONTOLOGY_VERSION
            or not set(CORE_CONCEPTS) <= classes
            or not set(RESOURCE_CONCEPTS) <= classes
            or set(signatures) != expected_relations
            or any(not domain or not range_ or not (domain | range_) <= classes for domain, range_ in signatures.values())):
        raise RuntimeError("OWL 关系与语义 Schema 的端点契约不一致")


def query_ontology(term: str | None = None, version: str = ONTOLOGY_VERSION) -> dict:
    metadata = ontology_metadata(version)
    if not term:
        return metadata
    needle = normalize(term)
    concepts = metadata["vocabulary"]["concepts"]
    matches = [
        row for group in ("classes", "object_properties", "data_properties")
        for row in metadata[group]
        if needle in {normalize(word) for word in (row["id"], row["iri"], row["label"], *concepts.get(row["id"], []))}
    ]
    return {key: metadata[key] for key in ("ontology_version", "namespace")} | {"matches": matches}


NORMALIZABLE_GROUPS = {
    "concepts", "operations", "capabilities", "parameters",
    "asset_categories", "material_types", "material_state_codes", "interface_types",
}


def normalize_term(group: str, text: str) -> dict:
    if group not in NORMALIZABLE_GROUPS:
        raise ValueError("未知词表分类")
    matches = candidates(group, text)
    return {"original": text, "group": group, "candidates": matches,
            "term": matches[0] if len(matches) == 1 else None, "basis": "词表编码或别名" if len(matches) == 1 else None}


def normalize_material_type(text: str | None) -> str | None:
    """Resolve raw material name or alias into a canonical material_types code."""
    if not text or not text.strip():
        return None
    exact = candidates("material_types", text)
    if len(exact) == 1:
        return exact[0]
    cleaned = normalize(text)
    vocab = load_vocabulary().get("material_types", {})
    # Longest alias substring fallback for decorated material names (e.g. "清洗后方形电芯")
    best: tuple[int, str] | None = None
    for code, entry in vocab.items():
        aliases = entry.get("aliases", []) if isinstance(entry, dict) else entry
        for alias in [code, *aliases]:
            norm_alias = normalize(alias)
            if norm_alias and norm_alias in cleaned:
                if best is None or len(norm_alias) > best[0]:
                    best = (len(norm_alias), code)
    return best[1] if best else None


def normalize_material_state(text: str | None) -> str | None:
    """Resolve raw material state phrase into a canonical material_state_codes code."""
    if not text or not text.strip():
        return None
    exact = candidates("material_state_codes", text)
    if len(exact) == 1:
        return exact[0]
    cleaned = normalize(text)
    vocab = load_vocabulary().get("material_state_codes", {})
    best: tuple[int, str] | None = None
    for code, entry in vocab.items():
        aliases = entry.get("aliases", []) if isinstance(entry, dict) else entry
        for alias in [code, *aliases]:
            norm_alias = normalize(alias)
            if norm_alias and norm_alias in cleaned:
                if best is None or len(norm_alias) > best[0]:
                    best = (len(norm_alias), code)
    return best[1] if best else None


def semantic_mappings() -> list[dict]:
    """Return explicit demand-side to resource-side semantic correspondence mappings."""
    return list(load_vocabulary().get("demand_resource_mappings", []))


def normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def candidates(group: str, term: str) -> list[str]:
    if not term.strip():
        return []
    needle = normalize(term)
    result = []
    for code, values in load_vocabulary()[group].items():
        aliases = values["aliases"] if isinstance(values, dict) else values
        if needle in {normalize(value) for value in [code, *aliases]}:
            result.append(code)
    return sorted(result)


def capability_terms(phrase: str) -> list[str]:
    if candidates("capabilities", phrase):
        return [phrase]
    aliases = sorted(
        {alias for terms in load_vocabulary()["capabilities"].values() for alias in terms},
        key=lambda alias: (-len(alias), alias),
    )
    occupied = set()
    found = []
    for alias in aliases:
        pattern = re.escape(alias)
        if alias.isascii():
            pattern = rf"(?<![A-Za-z]){pattern}(?![A-Za-z])"
        for hit in re.finditer(pattern, phrase):
            before, after = phrase[:hit.start()], phrase[hit.end():]
            if (re.search(r"(?:待|已|避免|不得|禁止|无需|不需要)$", before)
                    or re.match(r"后|前|组件|产物", after)
                    or (hit.group() == "定位" and after.startswith("置"))):
                continue
            indexes = set(range(*hit.span()))
            if not indexes & occupied:
                found.append((hit.start(), hit.group()))
                occupied.update(indexes)
    return list(dict.fromkeys(term for _, term in sorted(found))) or [phrase]
