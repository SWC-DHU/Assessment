"""Read-only context for both semantic paths and traditional worksheet columns."""
from .schema import SemanticModel
from .validation import COLLECTIONS


def template_context(model: SemanticModel) -> dict:
    semantic = model.model_dump(mode="json")
    lookup = {row.id: row for group in COLLECTIONS for row in getattr(model, group)}
    durations = {"ms": 0.001, "s": 1, "min": 60, "h": 3600}
    operations = []
    for op, raw in zip(model.operations, semantic["operations"]):
        issues = {issue.code for issue in model.validation.issues if issue.path == op.id}
        constraints = [lookup[key] for key in op.constraint_ids if key in lookup]
        def times(code):
            return [item for item in constraints if item.parameter_code == code]
        def seconds(items):
            valid = [item.value * durations[item.unit] for item in items if item.unit in durations
                     and item.comparator == "=" and item.value is not None and item.status == "verified"]
            return valid[0] if valid and len(set(valid)) == 1 else None
        work, takt = times("process_time"), times("takt_time")
        input_names = [lookup[key].name for key in op.input_material_ids]
        output_names = [lookup[key].name for key in op.output_material_ids]
        predecessor_labels = [lookup[key].number or lookup[key].name for key in op.predecessor_ids]
        cell_states = {}
        unavailable = any(item.status == "unprocessed" for item in model.validation.issues)
        if op.equipment_requirement and op.status != "verified":
            cell_states["equipment"] = "unverified"
        elif not op.equipment_requirement:
            cell_states["equipment"] = "unverified" if "equipment_unsubstantiated" in issues else "not_extracted" if unavailable else "source_missing"
        if seconds(work) is None:
            cell_states["process_time_sec"] = "unverified" if work else (
                "not_extracted" if unavailable or "measurement_unparsed" in issues else "source_missing")
        if not predecessor_labels and op.precursor_declaration != []:
            cell_states["predecessors"] = "unverified" if op.precursor_declaration else "source_missing"
        if seconds(takt) is None:
            cell_states["cycle_time_sec"] = "unverified" if takt else "source_missing"
        quotes = [item.quote for item in op.evidence if item.status == "verified"]
        operations.append({
            **raw, "seq": op.number, "equipment": op.equipment_requirement if op.status == "verified" else None,
            "process_time_sec": seconds(work), "cycle_time_sec": seconds(takt),
            "input_materials": input_names, "output_materials": output_names, "predecessors": predecessor_labels,
            "quality_requirements": "；".join(item.description for item in constraints
                                                if item.description and item.status == "verified"),
            "capability_names": [lookup[key].name for key in op.capability_ids],
            "source_quote": quotes[0] if quotes else "", "source_quotes": list(dict.fromkeys(quotes)), "_cell_states": cell_states, "_explicit_no_predecessor": op.precursor_declaration == [] and op.status == "verified",
        })
    line_takt = next((value for value in model.constraints if value.target_id == "line" and
                      value.parameter_code == "takt_time" and value.value is not None and value.unit in durations
                      and value.status == "verified"), None)
    return {
        **semantic, "semantic": semantic, "operations": operations,
        "product_name": model.products[0].name if model.products else None,
        "part_no": model.products[0].part_number if model.products else None,
        "target_takt_sec": line_takt.value * durations[line_takt.unit] if line_takt else None,
        "constraints": [
            {**raw, "type": value.name, "source_quote": next((ev.quote for ev in value.evidence), "")}
            for raw, value in zip(semantic["constraints"], model.constraints)
        ],
        "unknown_items": [dict(item.model_dump(), item=item.message, reason=item.code) for item in model.unresolved_items],
    }
