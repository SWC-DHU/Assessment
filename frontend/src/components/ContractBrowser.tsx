import { useEffect, useState } from "react";
import { Alert, Modal, Select, Tabs } from "antd";
import { api } from "../api/client";
import type { ContractKind, ContractSchema } from "../types";

const contracts = [
  { key: "candidates", label: "候选抽取", description: "分批提交给模型的候选事实和引文。服务端逐条校验并核对原始来源。" },
  { key: "formal", label: "正式语义", description: "后端实例化、校验并保存的可追溯需求版本。字段的实际值与来源可在语义模型中查看。" },
  { key: "handoff", label: "匹配输入", description: "从指定正式版本生成的资源匹配标准输入（ResourceMatchingInput）。" },
  { key: "model_asset", label: "模型资产", description: "统一数字孪生模型资产定义（ModelAsset 1.0.0），含八大模块并与三维文件解耦。" },
  { key: "matching_output", label: "匹配输出", description: "资源匹配智能体输出契约（ResourceMatchingOutput 1.0.0），交由下游关联建模智能体消费。" },
] as const;

const fieldMeaning: Record<string, string> = {
  products: "产品实例及组成引用",
  operations: "工序实例及其能力、约束、物料和前驱引用",
  materials: "基础物料",
  material_states: "基础物料关联的工序状态",
  modeling_intents: "来源声明的建模目标",
  modeling_task: "建模范围、产物与目标及整线约束的引用",
  constraints: "工序、整线、产品或能力的定量与定性要求",
  relations: "类型化语义关系，端点类型由本体定义",
  process_graph: "后端按执行方向生成的逻辑工艺图",
  evidence: "支持事实的原文引文或来源定位",
  review_evidence: "人工审核依据",
  source_refs: "原始文件及用户消息的稳定引用",
  status: "结构中的状态字段，实际值随事实或校验结果变化",
  review: "事实的人工确认状态",
  unresolved_items: "待确认和校验诊断",
  handoff_validation: "交接判定及阻塞／警告",
};

const ontologyClasses: Record<string, string> = {
  Product: "Product", Material: "Material", MaterialState: "MaterialState", Operation: "Operation",
  Capability: "CapabilityRequirement", Constraint: "Constraint", Objective: "Objective",
  OperationResourceRequirement: "Operation", ProcessRelation: "RDF 对象属性",
};

function fieldType(field: ContractSchema): string {
  if (field.$ref) return field.$ref.slice(field.$ref.lastIndexOf("/") + 1);
  if (field.const) return JSON.stringify(field.const);
  if (field.enum) return field.enum.join(" | ");
  if (field.anyOf) return field.anyOf.map(fieldType).join(" | ");
  if (field.items) return `Array<${fieldType(field.items)}>`;
  if (typeof field.additionalProperties === "object") return `Record<string, ${fieldType(field.additionalProperties)}>`;
  return field.type ?? "对象";
}

function ContractFields({ kind, description }: { kind: ContractKind; description: string }) {
  const [schema, setSchema] = useState<ContractSchema>();
  const [error, setError] = useState("");
  const [definition, setDefinition] = useState("");
  useEffect(() => {
    const request = new AbortController();
    api.getContractSchema(kind, request.signal).then(setSchema).catch((reason: unknown) => {
      if (!request.signal.aborted) setError(String(reason));
    });
    return () => request.abort();
  }, [kind]);
  const value = definition ? schema?.$defs?.[definition] : schema;
  const concept = ontologyClasses[definition.replace(/Candidate$/, "")];
  return <div className="contract-fields">
    <p>{description}</p>
    {error && <Alert type="error" title="读取 Schema 失败" description={error} />}
    {!schema && !error && <p role="status">正在读取 Schema</p>}
    {schema && <>
      <Select aria-label="Schema 结构" showSearch optionFilterProp="label" value={definition} onChange={setDefinition}
        options={[{ value: "", label: schema.title ?? "根结构" }, ...Object.keys(schema.$defs ?? {}).map((name) => ({ value: name, label: name }))]} />
      {concept && <p className="muted">本体对应 <code>{concept}</code></p>}
      <dl className="contract-field-list">{Object.entries(value?.properties ?? {}).map(([key, field]) =>
        <div key={key}><dt><code>{key}</code><span>{value?.required?.includes(key) ? "必填" : "可省略"}</span></dt>
          <dd><code>{fieldType(field)}</code>{(field.description || fieldMeaning[key]) && <p>{field.description ?? fieldMeaning[key]}</p>}</dd>
        </div>)}</dl>
      <details className="result-disclosure"><summary>完整 JSON Schema</summary><pre className="json-code">{JSON.stringify(schema, null, 2)}</pre></details>
    </>}
  </div>;
}

export function ContractBrowser({ initialKind, onClose }: { initialKind: ContractKind; onClose: () => void }) {
  return <Modal title="数据契约" open onCancel={onClose} footer={null} width={700}>
    <Tabs defaultActiveKey={initialKind} items={contracts.map(({ key, label, description }) => ({
      key, label, children: <ContractFields kind={key} description={description} />,
    }))} />
  </Modal>;
}
