"""Versioned manufacturing semantics for reuse across applications."""
from .catalog import ontology_metadata
from .schema import SemanticModel

__all__ = ["SemanticModel", "ontology_metadata"]
