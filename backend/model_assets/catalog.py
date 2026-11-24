"""Model asset catalog loader, query service, import/export, and binding management."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable

from domain_semantics.catalog import load_vocabulary, normalize

from .schema import MODEL_ASSET_SCHEMA_VERSION, ModelAsset, ModelBinding
from .validation import AssetValidationResult, validate_asset, validate_raw_asset

ROOT = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = ROOT / "data"
DEFAULT_GEOMETRY_DIR = ROOT / "geometry"
CATALOG_BASE_VERSION = "1.0.0"


class AssetCatalog:
    """File-backed digital twin model asset catalog with deterministic digesting."""

    def __init__(self, data_dir: Path | None = None, geometry_dir: Path | None = None):
        self.data_dir = Path(data_dir) if data_dir is not None else DEFAULT_DATA_DIR
        self.geometry_dir = Path(geometry_dir) if geometry_dir is not None else DEFAULT_GEOMETRY_DIR
        self._assets: dict[str, ModelAsset] = {}
        self._validations: dict[str, AssetValidationResult] = {}
        self.reload()

    def reload(self) -> None:
        self._assets.clear()
        self._validations.clear()
        if not self.data_dir.exists():
            return
        for path in sorted(self.data_dir.glob("*.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            items = raw if isinstance(raw, list) else raw.get("assets", [raw]) if isinstance(raw, dict) else []
            for entry in items:
                asset, report = validate_raw_asset(entry)
                if asset is not None:
                    self._assets[asset.asset_id] = asset
                    self._validations[asset.asset_id] = report

    @property
    def catalog_digest(self) -> str:
        payload = [self._assets[key].model_dump(mode="json") for key in sorted(self._assets)]
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @property
    def catalog_version(self) -> str:
        return f"{CATALOG_BASE_VERSION}+{self.catalog_digest[:8]}"

    def all_assets(self) -> list[ModelAsset]:
        return [self._assets[key] for key in sorted(self._assets)]

    def get(self, asset_id: str) -> ModelAsset | None:
        return self._assets.get(asset_id)

    def validation_for(self, asset_id: str) -> AssetValidationResult | None:
        return self._validations.get(asset_id)

    def list_assets(
        self,
        *,
        category: str | None = None,
        capability: str | None = None,
        material_type: str | None = None,
        binding_status: str | None = None,
        data_source: str | None = None,
        search: str | None = None,
    ) -> list[ModelAsset]:
        needle = normalize(search) if search and search.strip() else None
        results: list[ModelAsset] = []
        for asset in self.all_assets():
            if category and asset.identity.category != category:
                continue
            if capability and capability not in asset.capability_codes:
                continue
            if material_type:
                mats = set(
                    asset.material_compatibility.supported_material_types
                    + asset.material_compatibility.input_material_types
                    + asset.material_compatibility.output_material_types
                )
                if material_type not in mats:
                    continue
            if binding_status and asset.model_binding.binding_status != binding_status:
                continue
            if data_source and asset.data_source != data_source:
                continue
            if needle:
                haystack = " ".join([
                    asset.identity.asset_id,
                    asset.identity.name,
                    asset.identity.category,
                    asset.identity.subcategory or "",
                    asset.identity.description,
                    " ".join(asset.identity.tags),
                    " ".join(c.code + " " + c.name + " " + c.description for c in asset.capabilities),
                    " ".join(asset.material_compatibility.supported_material_types),
                ])
                if needle not in normalize(haystack):
                    continue
            results.append(asset)
        return results

    def _asset_path(self, asset_id: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in asset_id)
        return self.data_dir / f"{safe}.json"

    def upsert(self, payload: dict | ModelAsset, *, persist: bool = True) -> tuple[ModelAsset, AssetValidationResult]:
        if isinstance(payload, ModelAsset):
            asset = payload
            report = validate_asset(asset)
        else:
            asset, report = validate_raw_asset(payload)
            if asset is None:
                raise ValueError("; ".join(item.message for item in report.issues))
        if not report.valid:
            errors = [item.message for item in report.issues if item.severity == "error"]
            raise ValueError("; ".join(errors))
        self._assets[asset.asset_id] = asset
        self._validations[asset.asset_id] = report
        if persist:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self._asset_path(asset.asset_id).write_text(
                asset.model_dump_json(indent=2) + "\n",
                encoding="utf-8",
            )
        return asset, report

    def delete(self, asset_id: str, *, persist: bool = True) -> bool:
        if asset_id not in self._assets:
            return False
        self._assets.pop(asset_id, None)
        self._validations.pop(asset_id, None)
        if persist:
            path = self._asset_path(asset_id)
            if path.exists():
                path.unlink()
        return True

    def update_binding(self, asset_id: str, binding: ModelBinding, *, persist: bool = True) -> tuple[ModelAsset, AssetValidationResult]:
        asset = self._assets.get(asset_id)
        if asset is None:
            raise KeyError(asset_id)
        updated = asset.model_copy(update={"model_binding": binding})
        updated = ModelAsset.model_validate(updated.model_dump(mode="python"))
        return self.upsert(updated, persist=persist)

    def import_batch(
        self,
        items: list[dict],
        *,
        overwrite: bool = False,
        persist: bool = True,
    ) -> dict:
        imported: list[str] = []
        skipped: list[dict] = []
        failed: list[dict] = []
        for idx, raw in enumerate(items):
            asset, report = validate_raw_asset(raw)
            if asset is None or not report.valid:
                failed.append({
                    "index": idx,
                    "asset_id": report.asset_id,
                    "issues": [issue.model_dump(mode="json") for issue in report.issues],
                })
                continue
            if asset.asset_id in self._assets and not overwrite:
                skipped.append({"index": idx, "asset_id": asset.asset_id, "reason": "资产 ID 已存在（未启用覆盖）"})
                continue
            self.upsert(asset, persist=persist)
            imported.append(asset.asset_id)
        return {
            "imported_count": len(imported),
            "imported_ids": imported,
            "skipped": skipped,
            "failed": failed,
            "catalog_version": self.catalog_version,
        }

    def export_payload(self, asset_ids: Iterable[str] | None = None) -> dict:
        ids = list(asset_ids) if asset_ids is not None else sorted(self._assets.keys())
        assets = [self._assets[key].model_dump(mode="json") for key in ids if key in self._assets]
        return {
            "schema_version": MODEL_ASSET_SCHEMA_VERSION,
            "catalog_version": self.catalog_version,
            "catalog_digest": self.catalog_digest,
            "count": len(assets),
            "assets": assets,
        }

    def summary(self) -> dict:
        vocab = load_vocabulary()
        category_meta = vocab.get("asset_categories", {})
        by_category: dict[str, int] = {}
        by_capability: dict[str, int] = {}
        by_binding: dict[str, int] = {"unbound": 0, "reserved": 0, "bound": 0}
        by_source: dict[str, int] = {}
        valid_count = 0
        for asset in self.all_assets():
            cat = asset.identity.category
            by_category[cat] = by_category.get(cat, 0) + 1
            for code in asset.capability_codes:
                by_capability[code] = by_capability.get(code, 0) + 1
            by_binding[asset.model_binding.binding_status] = by_binding.get(asset.model_binding.binding_status, 0) + 1
            by_source[asset.data_source] = by_source.get(asset.data_source, 0) + 1
            report = self._validations.get(asset.asset_id)
            if report and report.valid:
                valid_count += 1
        return {
            "schema_version": MODEL_ASSET_SCHEMA_VERSION,
            "catalog_version": self.catalog_version,
            "catalog_digest": self.catalog_digest,
            "total_assets": len(self._assets),
            "valid_assets": valid_count,
            "by_category": [
                {"code": code, "label": category_meta.get(code, {}).get("label", code), "count": by_category.get(code, 0)}
                for code in category_meta.keys()
            ],
            "by_capability": by_capability,
            "by_binding_status": by_binding,
            "by_data_source": by_source,
        }


_default_catalog: AssetCatalog | None = None


def get_default_catalog() -> AssetCatalog:
    global _default_catalog
    if _default_catalog is None:
        _default_catalog = AssetCatalog()
    return _default_catalog


def reset_default_catalog() -> None:
    global _default_catalog
    _default_catalog = None
