import { useEffect, useState } from "react";
import { Alert, Button, Form, Input, InputNumber, Modal, Select, Tabs } from "antd";
import { api } from "../api/client";
import type { OntologyCatalog, OntologyTerm, PipelineStage, ReviewRequest, SemanticModel } from "../types";
import { groups } from "./SemanticGraph";

export const toolNames: Record<string, string> = {
  parse_document: "读取文档与定位来源", extract_facts: "分批提取事实", query_ontology: "查询本体",
  normalize_term: "术语标准化", map_semantic_entities: "实例化候选事实", build_process_graph: "构建工艺拓扑",
  validate_semantics: "校验语义", validate_constraints: "校验工艺约束", export_requirement_model: "生成需求模型",
  deterministic_extraction: "提取结构化事实", batch_split: "拆分截断批次", llm_repair: "局部修复",
  llm_disambiguation: "能力消歧",
};

const stageStatus: Record<string, string> = {
  running: "进行中", success: "完成", partial: "部分完成", error: "失败", skipped: "已跳过",
};

export function RunProgress({ steps }: { steps: PipelineStage[] }) {
  const latest = new Map(steps.map((stage) => [`${stage.stage}/${stage.batch_id ?? ""}`, stage]));
  return <div className="requirement-progress" aria-label="实时执行进度" aria-live="polite">
    {latest.size ? Array.from(latest.values()).slice(-5).map((step) =>
      <span key={`${step.stage}/${step.batch_id ?? ""}`} data-state={step.status} title={step.summary}>
        {toolNames[step.stage] ?? step.stage}{step.batch_id ? ` ${step.batch_id}` : ""} · {stageStatus[step.status] ?? step.status}
      </span>) : <span>等待后端执行</span>}
  </div>;
}

function OntologyTerms({ terms }: { terms: OntologyTerm[] }) {
  return <div className="ontology-list">{terms.map((term) => <article key={term.id}>
    {term.label !== term.id && <strong>{term.label} </strong>}<code>{term.id}</code>
    {term.definition && <p>{term.definition}</p>}
    {(term.domain.length > 0 || term.range.length > 0) && <small>
      {term.domain.join(" / ") || "任意主体"} → {term.range.join(" / ") || "未指定值域"}
    </small>}
    {term.subclass_of.length > 0 && <small>父类 {term.subclass_of.join("、")}</small>}
  </article>)}</div>;
}

export function OntologyBrowser({ version, onClose }: { version?: string; onClose: () => void }) {
  const [data, setData] = useState<OntologyCatalog>();
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  useEffect(() => {
    let active = true;
    api.getOntology(version).then((value) => { if (active) setData(value); })
      .catch((reason: unknown) => { if (active) setError(String(reason)); });
    return () => { active = false; };
  }, [version]);
  const find = (terms: OntologyTerm[]) => terms.filter((term) =>
    [term.id, term.label, term.definition].join(" ").toLowerCase().includes(query.trim().toLowerCase()));
  return <Modal title={`领域本体 ${data?.ontology_version ?? version ?? ""}`} open onCancel={onClose} footer={null} width={640}>
    <Input.Search allowClear placeholder="搜索类、关系或属性" value={query} onChange={(event) => setQuery(event.target.value)} />
    {error && <Alert type="error" title={error} />}
    {!data && !error && <p className="muted">正在读取 RDF 本体</p>}
    {data && <Tabs items={[
      { key: "classes", label: "概念", children: <OntologyTerms terms={find(data.classes)} /> },
      { key: "relations", label: "对象关系", children: <OntologyTerms terms={find(data.object_properties)} /> },
      { key: "attributes", label: "数据属性", children: <OntologyTerms terms={find(data.data_properties)} /> },
    ]} />}
  </Modal>;
}

const flowTypes = [
  ["directlyPrecedes", "直接转序"], ["precedes", "先后"], ["dependsOn", "业务依赖"], ["branchesTo", "分支"],
  ["mergesInto", "汇合"], ["conditionallyPrecedes", "条件转序"], ["optionalNext", "可选转序"], ["reworksTo", "返工"],
];

const actionNames = [
  ["rename_operation", "修正工序名称"], ["set_component_quantity", "修订组件数量"], ["confirm_capability", "确认能力映射"],
  ["add_transition", "补充工艺关系"], ["confirm_fact", "确认事实"],
];

export function ReviewDialog({ model, onSubmit, onClose }: {
  model: SemanticModel;
  onSubmit: (payload: ReviewRequest) => Promise<void>;
  onClose: () => void;
}) {
  const [form] = Form.useForm();
  const action = Form.useWatch("action", form);
  const relationType = Form.useWatch("type", form);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [catalog, setCatalog] = useState<OntologyCatalog>();
  useEffect(() => {
    let active = true;
    api.getOntology().then((value) => { if (active) setCatalog(value); })
      .catch((reason: unknown) => { if (active) setError(String(reason)); });
    return () => { active = false; };
  }, []);
  const entities = groups.flatMap(([key]) => model[key]);
  const options = entities.map((item) => ({ value: item.id, label: [item.number, item.name].filter(Boolean).join(" · ") }));
  const opOptions = options.filter((item) => model.operations.some((op) => op.id === item.value));
  const components = model.relations.filter((relation) => relation.type === "hasComponent");
  const relationOptions = (rows: typeof components) => rows.map((item) => ({
    value: item.id, label: `${entities.find((entity) => entity.id === item.source_id)?.name ?? item.source_id} · ${item.type} · ${entities.find((entity) => entity.id === item.target_id)?.name ?? item.target_id}`,
  }));

  return <Modal title="人工审核 · 保存为新版本" open onCancel={onClose} footer={null} width={570}>
    <Form form={form} layout="vertical" initialValues={{ action: "rename_operation", type: "directlyPrecedes" }}
      onFinish={async (value: ReviewRequest & { value?: number; unit?: string; basis?: string }) => {
        setError(""); setSaving(true);
        try {
          const { value: amount, unit, basis, ...fields } = value;
          const payload = fields.action === "set_component_quantity"
            ? { ...fields, quantity: { value: amount!, unit: unit!, basis } } : fields;
          await onSubmit(payload);
          onClose();
        } catch (reason) { setError(String(reason)); }
        finally { setSaving(false); }
      }}>
      <Form.Item name="action" label="审核动作" rules={[{ required: true }]}>
        <Select options={actionNames.map(([value, label]) => ({ value, label }))} />
      </Form.Item>
      {action === "rename_operation" && <>
        <Form.Item name="operation_id" label="工序" preserve={false} rules={[{ required: true }]}><Select options={opOptions} /></Form.Item>
        <Form.Item name="name" label="修正名称" preserve={false} rules={[{ required: true }]}><Input maxLength={150} /></Form.Item>
      </>}
      {action === "set_component_quantity" && <>
        <Form.Item name="relation_id" label="产品组成" preserve={false} rules={[{ required: true }]}>
          <Select options={relationOptions(components)} />
        </Form.Item>
        <div className="review-quantity">
          <Form.Item name="value" label="数量" preserve={false} rules={[{ required: true }]}><InputNumber min={0.000001} /></Form.Item>
          <Form.Item name="unit" label="单位" preserve={false} rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="basis" label="计量基准" preserve={false}><Input /></Form.Item>
        </div>
      </>}
      {action === "confirm_capability" && <>
        <Form.Item name="capability_id" label="能力" preserve={false} rules={[{ required: true }]}>
          <Select options={model.capabilities.map((item) => ({ value: item.id, label: item.name }))} />
        </Form.Item>
        <Form.Item name="code" label="词表编码" preserve={false} rules={[{ required: true }]}>
          <Select showSearch optionFilterProp="label" options={Object.entries(catalog?.vocabulary.capabilities ?? {}).map(([value, terms]) =>
            ({ value, label: `${value} · ${terms[0]}` }))} />
        </Form.Item>
      </>}
      {action === "add_transition" && <>
        <Form.Item name="source_id" label={relationType === "dependsOn" ? "依赖方工序" : "起点工序"} preserve={false} rules={[{ required: true }]}><Select options={opOptions} /></Form.Item>
        <Form.Item name="target_id" label={relationType === "dependsOn" ? "所依赖工序" : "终点工序"} preserve={false} rules={[{ required: true }]}><Select options={opOptions} /></Form.Item>
        <Form.Item name="type" label="关系类型" preserve={false} rules={[{ required: true }]}>
          <Select options={flowTypes.map(([value, label]) => ({ value, label }))} />
        </Form.Item>
        <Form.Item name="condition" label="转序条件" preserve={false} rules={[{ required: relationType === "conditionallyPrecedes" }]}><Input maxLength={300} /></Form.Item>
        {relationType === "reworksTo" && <Form.Item name="max_iterations" label="返工上限" preserve={false}><InputNumber min={1} precision={0} /></Form.Item>}
      </>}
      {action === "confirm_fact" && <Form.Item name="fact_id" label="确认对象" preserve={false} rules={[{ required: true }]}>
        <Select showSearch optionFilterProp="label" options={[...options, ...relationOptions(model.relations)]} />
      </Form.Item>}
      <Form.Item name="note" label="审核依据" rules={[{ required: true, whitespace: true }]}>
        <Input.TextArea rows={2} placeholder="写明来源、现场确认或修改原因" maxLength={1200} />
      </Form.Item>
      {error && <Alert type="error" title={error} />}
      <Button type="primary" htmlType="submit" loading={saving}>保存审核版本</Button>
    </Form>
  </Modal>;
}
