from domain_semantics.pipeline import Builder, project
from domain_semantics.sources import file_document, message_document
from app.services.parser import parse_file_bytes


def test_natural_language_verifies_direction_value_and_units_without_trusting_candidates():
    text = "电芯堆叠的直接前驱是电芯清洗。电芯堆叠需要精确定位。整线生产节拍≤120秒/模组。"
    doc = message_document("user-a", text)
    quote = lambda value: [{"quote": value, "document_id": "user-a", "fragment_id": "message"}]
    payload = {
        "operations": [
            {"id": "clean", "name": "电芯清洗", "evidence": quote("电芯清洗")},
            {"id": "stack", "name": "电芯堆叠", "evidence": quote("电芯堆叠需要精确定位")},
        ],
        "capabilities": [{"id": "position", "name": "精确定位", "evidence": quote("精确定位")}],
        "constraints": [
            {"id": "target", "name": "生产节拍", "target_id": "line", "comparator": "<=",
             "value": 120, "unit": "秒", "basis": "模组", "evidence": quote("整线生产节拍≤120秒/模组")},
            {"id": "invented", "name": "生产节拍", "target_id": "line", "comparator": ">=",
             "value": 120, "unit": "秒", "basis": "模组", "evidence": quote("整线生产节拍≤120秒/模组")},
        ],
        "relations": [
            {"type": "hasPredecessor", "source_id": "stack", "target_id": "clean",
             "evidence": quote("电芯堆叠的直接前驱是电芯清洗")},
            {"type": "hasPredecessor", "source_id": "clean", "target_id": "stack",
             "evidence": quote("电芯堆叠的直接前驱是电芯清洗")},
            {"type": "requiresCapability", "source_id": "stack", "target_id": "position",
             "evidence": quote("电芯堆叠需要精确定位")},
        ],
    }
    model = project([doc], "run-1", payload)
    assert model.operations[1].predecessor_ids == [model.operations[0].id]
    assert model.capabilities[0].code == "POSITION"
    assert [(record.comparator, record.status) for record in model.constraints] == [("<=", "verified"), (">=", "invalid")]
    assert {"quantity_unsupported", "precedence_direction"} <= {issue.code for issue in model.unresolved_items}
    stored = model.constraints[0].evidence[0]
    assert text[stored.location.start:stored.location.end] == stored.quote


def test_tabular_quotes_and_versions_are_reproducible():
    raw = ("工序编号,工序名称,输入物料,输出物料,前置工序,制造能力,关键工艺约束\n"
           "01,清洗,原件,清洁件,无,未知研磨方法,保持表面清洁\n"
           "Z1,定位,夹具,已定位件,无,精确定位,\n"
           "02,装配,清洁件；已定位件,成品,01；Z1,装配,").encode()
    source = parse_file_bytes(raw, "fixture.csv")
    doc = file_document("csv-fixture", "fixture.csv", source.parsed_text, source.parsed_preview)
    first = project([doc], "first")
    second = project([doc], "second")
    assert first.content_digest == second.content_digest
    assert first.operations[2].predecessor_ids == ["operation:01", "operation:Z1"]
    assert first.operations[0].evidence[0].quote == "清洗"
    assert first.operations[0].evidence[0].location.cell == "B2"
    assert [item.code for item in first.capabilities] == [None, "POSITION", "ASSEMBLE"]
    assert not any(edge.type == "branchesTo" for edge in first.relations)

    message = message_document("revision", "把第三道工序的工时修改为45秒。")
    builder = Builder([message], "child", first)
    assert builder.revise(message)
    child = builder.finish()
    assert child.parent_version_id == first.run_id
    assert child.operations[:2] == first.operations[:2]
    assert child.materials == first.materials
    changed = next(item for item in child.constraints if item.parameter_code == "process_time")
    assert changed.target_id == "operation:02" and changed.value == 45
    assert changed.evidence[0].document_id == "revision"
    assert all(item.parameter_code is None for item in first.constraints)


def test_precedence_cycle_identified_without_treating_rework_as_dependency():
    parsed = parse_file_bytes("工序编号,工序名称,前置工序\n01,清洗,02\n02,装配,01".encode(), "cycle.csv")
    doc = file_document("cycle", "cycle.csv", parsed.parsed_text, parsed.parsed_preview)
    model = project([doc], "cycle-run")
    assert model.validation.status == "invalid"
    assert "precedence_cycle" in {issue.code for issue in model.unresolved_items}
    assert all(not op.predecessor_ids for op in model.operations)


def test_empty_and_unlocated_evidence_cannot_create_facts():
    model = project([message_document("one", "已有资料")], "trace", {"operations": [
        {"id": "bad", "name": "虚构设备", "evidence": [{"quote": "虚构设备"}]},
        {"id": "bad-shape", "name": "未知", "evidence": []},
    ]})
    assert not model.operations
    assert {item.code for item in model.unresolved_items} == {"evidence_not_located", "candidate_shape"}


def test_table_heading_and_cell_are_separate_evidence_for_numeric_values():
    raw = ("产品名称,图号,产品组件,工序编号,工序名称,设备名称,单件工时(秒),前置工序\n"
           "装配件,A-1,外壳；端盖,01,固定,夹紧台,0,无\n"
           "装配件,A-1,外壳；端盖,02,检测,,12秒,01").encode()
    source = parse_file_bytes(raw, "fixture.csv")
    doc = file_document("csv", "fixture.csv", source.parsed_text, source.parsed_preview)
    model = project([doc], "run")
    assert model.products[0].name == "装配件" and model.products[0].part_number == "A-1"
    assert len(model.products[0].component_ids) == 2
    assert model.operations[0].equipment_evidence[0].quote == "夹紧台"
    assert model.operations[0].precursor_declaration == []
    assert [(c.value, c.unit, c.status) for c in model.constraints] == [(0, "s", "verified"), (12, "s", "verified")]
    assert [item.quote for item in model.constraints[0].evidence] == ["0", "单件工时(秒)"]


def test_rework_limits_and_conflicting_time_units_are_validated():
    user = message_document("user", "不合格时检测返工至清洗，最多2次。清洗工时为1分钟。清洗工时≤45秒。")
    cite = lambda text: [{"quote": text}]
    payload = {
        "operations": [{"id": name, "name": name, "evidence": cite("不合格时检测返工至清洗")}
                       for name in ("清洗", "检测")],
        "relations": [
            {"type": "reworksTo", "source_id": "检测", "target_id": "清洗", "max_iterations": 2,
             "condition": "不合格", "evidence": cite("不合格时检测返工至清洗，最多2次")},
        ],
        "constraints": [
            {"id": "first", "name": "工时", "target_id": "清洗", "comparator": "=", "value": 1,
             "unit": "分钟", "evidence": cite("清洗工时为1分钟")},
            {"id": "second", "name": "工时", "target_id": "清洗", "comparator": "<=", "value": 45,
             "unit": "秒", "evidence": cite("清洗工时≤45秒")},
        ],
    }
    model = project([user], "run", payload)
    assert model.validation.status == "conflict"
    assert all(item.status == "conflict" for item in model.constraints)
    assert next(edge for edge in model.relations if edge.type == "reworksTo").status == "verified"
    assert not model.operations[0].predecessor_ids


def test_table_terms_do_not_infer_later_actions_from_material_phrases():
    raw = "工序编号,工序名称,制造能力需求\n06,涂胶,对待堆叠单体组件规定表面施胶".encode()
    source = parse_file_bytes(raw, "fixture.csv")
    model = project([file_document("file", "fixture.csv", source.parsed_text, source.parsed_preview)], "run")
    assert [item.code for item in model.capabilities] == ["ADHESIVE_APPLY"]


def test_explicit_line_constraint_does_not_match_negated_user_instruction():
    user = message_document("user", "不要采用整线生产节拍≤120秒/件。")
    builder = Builder([user], "run")
    assert builder.explicit_line_constraints(user) == 0
