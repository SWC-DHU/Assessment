"""REST API for Digital Twin Model Asset Library."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field

from model_assets.catalog import get_default_catalog
from model_assets.schema import ModelAsset, ModelBinding
from model_assets.validation import validate_raw_asset

router = APIRouter(prefix="/api/model-assets", tags=["model_assets"])


class AssetImportRequest(BaseModel):
    assets: list[dict[str, Any]] = Field(min_length=1)
    overwrite: bool = False


class AssetValidateRequest(BaseModel):
    payload: dict[str, Any] | list[dict[str, Any]]


@router.get("/schema")
def asset_schema() -> dict:
    return ModelAsset.model_json_schema()


@router.get("/summary")
def catalog_summary() -> dict:
    return get_default_catalog().summary()


@router.get("")
def list_assets(
    category: str | None = None,
    capability: str | None = None,
    material_type: str | None = None,
    binding_status: str | None = None,
    data_source: str | None = None,
    search: str | None = None,
) -> dict:
    catalog = get_default_catalog()
    items = catalog.list_assets(
        category=category,
        capability=capability,
        material_type=material_type,
        binding_status=binding_status,
        data_source=data_source,
        search=search,
    )
    return {
        "catalog_version": catalog.catalog_version,
        "catalog_digest": catalog.catalog_digest,
        "total": len(items),
        "items": [
            {
                "asset": asset.model_dump(mode="json"),
                "validation": (
                    catalog.validation_for(asset.asset_id).model_dump(mode="json")
                    if catalog.validation_for(asset.asset_id)
                    else None
                ),
            }
            for asset in items
        ],
    }


@router.get("/export")
def export_assets(asset_ids: str | None = None) -> Response:
    catalog = get_default_catalog()
    ids = [part.strip() for part in asset_ids.split(",") if part.strip()] if asset_ids else None
    payload = catalog.export_payload(ids)
    return Response(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="model-assets-export.json"'},
    )


@router.post("/validate")
def validate_asset_payload(body: AssetValidateRequest) -> dict:
    raw = body.payload
    if isinstance(raw, dict) and "assets" in raw and isinstance(raw["assets"], list):
        items = raw["assets"]
    elif isinstance(raw, list):
        items = raw
    else:
        items = [raw]
    results = []
    all_valid = True
    for entry in items:
        _, report = validate_raw_asset(entry if isinstance(entry, dict) else {})
        if not report.valid:
            all_valid = False
        results.append(report.model_dump(mode="json"))
    return {"valid": all_valid, "count": len(results), "results": results}


@router.post("/import", status_code=status.HTTP_200_OK)
def import_assets(body: AssetImportRequest) -> dict:
    catalog = get_default_catalog()
    return catalog.import_batch(body.assets, overwrite=body.overwrite, persist=True)


@router.get("/{asset_id}")
def get_asset(asset_id: str) -> dict:
    catalog = get_default_catalog()
    asset = catalog.get(asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail=f"模型资产 {asset_id} 不存在")
    report = catalog.validation_for(asset_id)
    return {
        "asset": asset.model_dump(mode="json"),
        "validation": report.model_dump(mode="json") if report else None,
    }


@router.put("/{asset_id}")
def upsert_asset(asset_id: str, payload: dict[str, Any]) -> dict:
    catalog = get_default_catalog()
    identity = payload.get("identity") if isinstance(payload, dict) else None
    if isinstance(identity, dict) and identity.get("asset_id") and identity["asset_id"] != asset_id:
        raise HTTPException(status_code=422, detail="路径 asset_id 与请求体 identity.asset_id 不一致")
    try:
        asset, report = catalog.upsert(payload, persist=True)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "asset": asset.model_dump(mode="json"),
        "validation": report.model_dump(mode="json"),
        "catalog_version": catalog.catalog_version,
    }


@router.patch("/{asset_id}/binding")
def update_asset_binding(asset_id: str, binding: ModelBinding) -> dict:
    catalog = get_default_catalog()
    if catalog.get(asset_id) is None:
        raise HTTPException(status_code=404, detail=f"模型资产 {asset_id} 不存在")
    try:
        asset, report = catalog.update_binding(asset_id, binding, persist=True)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "asset": asset.model_dump(mode="json"),
        "validation": report.model_dump(mode="json"),
        "catalog_version": catalog.catalog_version,
    }


@router.delete("/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_asset(asset_id: str) -> Response:
    catalog = get_default_catalog()
    if not catalog.delete(asset_id, persist=True):
        raise HTTPException(status_code=404, detail=f"模型资产 {asset_id} 不存在")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
