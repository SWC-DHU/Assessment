from functools import lru_cache
from pathlib import Path
import re
import unicodedata

from rdflib import Graph, Namespace, OWL, RDF, URIRef
import yaml

ROOT = Path(__file__).resolve().parent
MANUFACTURING_IRI = URIRef("https://assessment.example.org/ontology/manufacturing")
M = Namespace(f"{MANUFACTURING_IRI}#")


@lru_cache
def load_ontology() -> Graph:
    graph = Graph()
    for path in sorted((ROOT / "ontology").glob("*.ttl")):
        graph.parse(path, format="turtle")
    return graph


@lru_cache
def load_vocabulary() -> dict:
    return yaml.safe_load((ROOT / "vocabulary/manufacturing.yaml").read_text(encoding="utf-8"))


def ontology_metadata() -> dict:
    graph = load_ontology()
    return {
        "namespace": str(M),
        "ontology_version": str(graph.value(MANUFACTURING_IRI, OWL.versionInfo)),
        "version_iri": str(graph.value(MANUFACTURING_IRI, OWL.versionIRI)),
        "concepts": sorted(str(node).split("#")[-1] for node in graph.subjects(RDF.type, OWL.Class) if node in M),
        "relations": sorted(str(node).split("#")[-1] for node in graph.subjects(RDF.type, OWL.ObjectProperty)),
        "vocabulary": load_vocabulary(),
    }


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
                    or (re.match(r"后|前|组件|产物", after) and before)):
                continue
            indexes = set(range(*hit.span()))
            if not indexes & occupied:
                found.append((hit.start(), hit.group()))
                occupied.update(indexes)
    return list(dict.fromkeys(term for _, term in sorted(found))) or [phrase]
