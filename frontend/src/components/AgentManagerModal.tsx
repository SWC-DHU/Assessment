import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  App as AntdApp,
  AutoComplete,
  Button,
  Card,
  Checkbox,
  Divider,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Segmented,
  Select,
  Slider,
  Space,
  Switch,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import {
  CheckCircle2,
  Copy,
  KeyRound,
  PlugZap,
  Plus,
  RefreshCw,
  Save,
  Trash2,
  Wand2,
} from "lucide-react";
import { api } from "../api/client";
import type {
  AgentConnectionTestResponse,
  AgentProfile,
  AgentProfilePayload,
  EnvStatus,
  ExtractionSchemaConfig,
  SchemaFieldDef,
} from "../types";

const { Text } = Typography;

interface AgentManagerModalProps {
  open: boolean;
  agents: AgentProfile[];
  activeAgentId: string | null;
  envStatus: EnvStatus | null;
  onClose: () => void;
  onCreateAgent: (payload: AgentProfilePayload) => Promise<AgentProfile>;
  onUpdateAgent: (id: string, payload: Partial<AgentProfilePayload>) => Promise<AgentProfile>;
  onDuplicateAgent: (id: string) => Promise<AgentProfile>;
  onDeleteAgent: (id: string) => Promise<void>;
  onActivateAgent?: (id: string) => Promise<void>;
  onConnectionTested?: (agentId: string, result: AgentConnectionTestResponse) => void;
}

const defaults = { timeout_seconds: null, request_budget: null, llm_disambiguation: true };

const PROVIDER_PRESETS = [
  { label: "OpenAI", base_url: "https://api.openai.com/v1", model: "gpt-4o-mini" },
  { label: "DeepSeek", base_url: "https://api.deepseek.com/v1", model: "deepseek-chat" },
  { label: "Qwen (百炼)", base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "qwen-plus" },
  { label: "Ollama (本地)", base_url: "http://localhost:11434/v1", model: "qwen2.5:14b" },
  { label: "离线 Mock", base_url: "mock://demo", model: "mock-demo" },
];

function emptyField(): SchemaFieldDef {
  return { name: "", type: "string", description: "", required: false, children: [] };
}

function buildFormFromAgent(agent: AgentProfile): AgentProfilePayload {
  const schema: ExtractionSchemaConfig = {
    mode: agent.extraction_schema?.mode || "visual",
    fields: agent.extraction_schema?.fields || [],
    json_schema: agent.extraction_schema?.json_schema || {},
  };
  return {
    name: agent.name,
    description: agent.description,
    base_url: agent.base_url,
    model: agent.model,
    temperature: agent.temperature,
    max_tokens: agent.max_tokens,
    execution: agent.execution ?? defaults,
    json_mode: agent.json_mode,
    require_source_quote: agent.require_source_quote,
    system_prompt: agent.system_prompt,
    business_rules: agent.business_rules,
    extraction_schema: schema,
  };
}

function fieldToJsonSchemaNode(field: SchemaFieldDef): Record<string, unknown> {
  const children = (field.children || []).filter((c) => c.name.trim());
  const desc = field.description?.trim();

  if (field.type === "object" || (field.type === "array" && children.length > 0)) {
    const properties: Record<string, unknown> = {};
    const required: string[] = [];
    for (const child of children) {
      const cname = child.name.trim();
      properties[cname] = fieldToJsonSchemaNode(child);
      if (child.required) required.push(cname);
    }
    const objSchema: Record<string, unknown> = {
      type: "object",
      properties,
      ...(required.length > 0 ? { required } : {}),
    };
    if (field.type === "array") {
      return {
        type: "array",
        ...(desc ? { description: desc } : {}),
        items: objSchema,
      };
    }
    return {
      ...objSchema,
      ...(desc ? { description: desc } : {}),
    };
  }

  if (field.type === "array") {
    return {
      type: "array",
      ...(desc ? { description: desc } : {}),
      items: { type: "string" },
    };
  }

  return {
    type: field.required ? field.type : [field.type, "null"],
    ...(desc ? { description: desc } : {}),
  };
}

function fieldsToJsonSchema(fields: SchemaFieldDef[]): Record<string, unknown> {
  const properties: Record<string, unknown> = {};
  const required: string[] = [];
  for (const field of fields) {
    const name = field.name.trim();
    if (!name) continue;
    properties[name] = fieldToJsonSchemaNode(field);
    if (field.required) required.push(name);
  }
  return {
    type: "object",
    required,
    properties,
  };
}

function isValidBaseUrl(url: string): boolean {
  const trimmed = url.trim();
  if (!trimmed) return true; // Empty means fall back to .env
  return (
    trimmed.startsWith("http://") ||
    trimmed.startsWith("https://") ||
    trimmed.startsWith("mock://")
  );
}

interface FieldTreeEditorProps {
  fields: SchemaFieldDef[];
  depth?: number;
  onChange: (next: SchemaFieldDef[]) => void;
}

function FieldTreeEditor({ fields, depth = 0, onChange }: FieldTreeEditorProps) {
  const updateAt = (idx: number, patch: Partial<SchemaFieldDef>) => {
    onChange(fields.map((f, i) => (i === idx ? { ...f, ...patch } : f)));
  };
  const removeAt = (idx: number) => {
    onChange(fields.filter((_, i) => i !== idx));
  };

  return (
    <div className="space-y-2">
      {fields.map((field, idx) => (
        <div
          key={idx}
          className={depth === 0 ? "p-2.5 rounded-md space-y-2" : "space-y-1.5"}
          style={
            depth === 0
              ? { background: "#fafafa", border: "1px solid #f0f0f0" }
              : undefined
          }
        >
          <div className="grid grid-cols-12 gap-2 items-center">
            <div className="col-span-3">
              <Input
                size="small"
                placeholder={depth === 0 ? "字段名 (如 product_name)" : "子字段名"}
                value={field.name}
                onChange={(e) => updateAt(idx, { name: e.target.value })}
                style={{ fontFamily: "monospace" }}
              />
            </div>
            <div className="col-span-2">
              <Select
                size="small"
                style={{ width: "100%" }}
                value={field.type}
                onChange={(v) => updateAt(idx, { type: v })}
                options={[
                  { value: "string", label: "string" },
                  { value: "number", label: "number" },
                  { value: "boolean", label: "boolean" },
                  { value: "array", label: "array" },
                  { value: "object", label: "object" },
                ]}
              />
            </div>
            <div className="col-span-5">
              <Input
                size="small"
                placeholder={depth === 0 ? "字段说明" : "子字段说明"}
                value={field.description}
                onChange={(e) => updateAt(idx, { description: e.target.value })}
              />
            </div>
            <div className="col-span-1">
              <Checkbox
                checked={field.required}
                onChange={(e) => updateAt(idx, { required: e.target.checked })}
              >
                <span className={depth === 0 ? "text-xs" : "text-[11px]"}>必填</span>
              </Checkbox>
            </div>
            <div className="col-span-1 flex justify-end">
              <Button
                size="small"
                type="text"
                danger
                icon={<Trash2 className={depth === 0 ? "w-3.5 h-3.5" : "w-3 h-3"} />}
                onClick={() => removeAt(idx)}
              />
            </div>
          </div>

          {(field.type === "array" || field.type === "object") && depth < 2 && (
            <div
              className="pl-3.5 space-y-1.5"
              style={{ borderLeft: "2px solid #b9cdbd" }}
            >
              <FieldTreeEditor
                fields={field.children || []}
                depth={depth + 1}
                onChange={(nextChildren) => updateAt(idx, { children: nextChildren })}
              />
              <Button
                size="small"
                type="link"
                icon={<Plus className="w-3 h-3" />}
                style={{ paddingLeft: 0 }}
                onClick={() =>
                  updateAt(idx, {
                    children: [...(field.children || []), emptyField()],
                  })
                }
              >
                添加子字段
              </Button>
            </div>
          )}
        </div>
      ))}

      {depth === 0 && (
        <Button
          type="dashed"
          block
          icon={<Plus className="w-3.5 h-3.5" />}
          onClick={() => onChange([...fields, emptyField()])}
        >
          添加顶层抽取字段
        </Button>
      )}
    </div>
  );
}

export function AgentManagerModal({
  open,
  agents,
  activeAgentId,
  envStatus,
  onClose,
  onCreateAgent,
  onUpdateAgent,
  onDuplicateAgent,
  onDeleteAgent,
  onActivateAgent,
  onConnectionTested,
}: AgentManagerModalProps) {
  const { message: antdMessage, modal } = AntdApp.useApp();
  const [selectedId, setSelectedId] = useState<string | null>(activeAgentId);
  const [form, setForm] = useState<AgentProfilePayload | null>(null);
  const [apiKeyInput, setApiKeyInput] = useState("");
  const [clearApiKey, setClearApiKey] = useState(false);
  const [rawJsonSchemaText, setRawJsonSchemaText] = useState("{}");
  const [schemaJsonError, setSchemaJsonError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const [testingConn, setTestingConn] = useState(false);
  const [fetchingModels, setFetchingModels] = useState(false);
  const [testResult, setTestResult] = useState<AgentConnectionTestResponse | null>(null);
  const [availableModels, setAvailableModels] = useState<string[]>([]);

  const wasOpenRef = useRef(false);
  const loadedSignatureRef = useRef<string | null>(null);

  const selectedAgent = useMemo(
    () => agents.find((a) => a.id === selectedId) || null,
    [agents, selectedId]
  );

  const loadAgentIntoForm = useCallback((agent: AgentProfile) => {
    const nextForm = buildFormFromAgent(agent);
    setForm(nextForm);
    setRawJsonSchemaText(JSON.stringify(nextForm.extraction_schema.json_schema || {}, null, 2));
    setSchemaJsonError(null);
    setApiKeyInput("");
    setClearApiKey(false);
    setTestResult(null);
    setAvailableModels([]);
    loadedSignatureRef.current = `${agent.id}:${agent.updated_at}`;
  }, []);

  // Initialize selectedId when modal opens; recover if selectedId gets deleted
  useEffect(() => {
    if (!open) {
      wasOpenRef.current = false;
      return;
    }
    if (!wasOpenRef.current) {
      wasOpenRef.current = true;
      const initialId = activeAgentId || agents[0]?.id || null;
      setSelectedId(initialId);
      const target = agents.find((a) => a.id === initialId);
      if (target) loadAgentIntoForm(target);
      return;
    }
    if (selectedId && !agents.some((a) => a.id === selectedId)) {
      const fallback = agents[0] || null;
      setSelectedId(fallback?.id || null);
      if (fallback) loadAgentIntoForm(fallback);
    }
  }, [open, activeAgentId, agents, selectedId, loadAgentIntoForm]);

  // Sync form only when selected agent changes or its persisted version updates
  useEffect(() => {
    if (!open || !selectedAgent) return;
    const sig = `${selectedAgent.id}:${selectedAgent.updated_at}`;
    if (loadedSignatureRef.current !== sig) {
      loadAgentIntoForm(selectedAgent);
    }
  }, [open, selectedAgent, loadAgentIntoForm]);

  const isDirty = useMemo(() => {
    if (!form || !selectedAgent) return false;
    if (apiKeyInput.trim() !== "" || clearApiKey) return true;
    const baseline = buildFormFromAgent(selectedAgent);
    const baselineJsonText = JSON.stringify(baseline.extraction_schema.json_schema || {}, null, 2);
    if (form.extraction_schema.mode === "json_schema" && rawJsonSchemaText !== baselineJsonText) {
      return true;
    }
    return JSON.stringify(form) !== JSON.stringify(baseline);
  }, [form, selectedAgent, apiKeyInput, clearApiKey, rawJsonSchemaText]);

  const confirmDiscardIfDirty = (onProceed: () => void) => {
    if (!isDirty) {
      onProceed();
      return;
    }
    modal.confirm({
      title: "存在未保存的修改",
      content: "当前智能体配置尚未保存，继续操作将丢失未保存的修改。",
      okText: "放弃修改并继续",
      okButtonProps: { danger: true },
      cancelText: "留在此页",
      onOk: onProceed,
    });
  };

  const handleSelectAgentInList = (id: string) => {
    if (id === selectedId) return;
    confirmDiscardIfDirty(() => {
      setSelectedId(id);
      const target = agents.find((a) => a.id === id);
      if (target) loadAgentIntoForm(target);
    });
  };

  const handleCloseModal = () => {
    confirmDiscardIfDirty(onClose);
  };

  const handleCreateNew = async () => {
    const createFlow = async () => {
      try {
        const defaultFields: SchemaFieldDef[] = [
          { name: "product_name", type: "string", description: "产品名称", required: true, children: [] },
          { name: "operations", type: "array", description: "工序列表", required: true, children: [] },
        ];
        const created = await onCreateAgent({
          name: "新自定义工艺智能体",
          description: "用于对比测试的自定义抽取智能体",
          base_url: "",
          api_key: "",
          model: "",
          temperature: 0.1,
          max_tokens: 4096,
          json_mode: true,
          require_source_quote: true,
          system_prompt: "你是一名工业工艺需求解析助手，请从输入文档中提取结构化信息。",
          business_rules: "",
          extraction_schema: {
            mode: "visual",
            fields: defaultFields,
            json_schema: fieldsToJsonSchema(defaultFields),
          },
        });
        setSelectedId(created.id);
        loadAgentIntoForm(created);
        antdMessage.success("已创建新智能体，请填写模型连接参数并保存");
      } catch (err) {
        antdMessage.error(err instanceof Error ? err.message : "创建智能体失败");
      }
    };
    confirmDiscardIfDirty(() => {
      void createFlow();
    });
  };

  const handleSave = async (activateAfterSave = false): Promise<boolean> => {
    if (!form || !selectedId) return false;

    const trimmedName = form.name.trim();
    if (!trimmedName) {
      antdMessage.error("智能体名称不能为空");
      return false;
    }
    if (!isValidBaseUrl(form.base_url)) {
      antdMessage.error("Base URL 格式不正确：请以 http://、https:// 或 mock:// 开头，或留空使用 .env 默认值");
      return false;
    }

    let nextSchema = form.extraction_schema;
    if (nextSchema.mode === "json_schema") {
      try {
        const parsed = JSON.parse(rawJsonSchemaText || "{}");
        nextSchema = { ...nextSchema, json_schema: parsed };
        setSchemaJsonError(null);
      } catch (e) {
        setSchemaJsonError(e instanceof Error ? e.message : "JSON Schema 语法错误");
        antdMessage.error("JSON Schema 语法有误，请修正后再保存");
        return false;
      }
    }

    const payload: Partial<AgentProfilePayload> = {
      ...form,
      name: trimmedName,
      base_url: form.base_url.trim(),
      model: form.model.trim(),
      extraction_schema: nextSchema,
    };
    if (clearApiKey) {
      payload.api_key = "";
    } else if (apiKeyInput.trim()) {
      payload.api_key = apiKeyInput.trim();
    }

    setSaving(true);
    try {
      const updated = await onUpdateAgent(selectedId, payload);
      loadAgentIntoForm(updated);
      if (activateAfterSave && onActivateAgent) {
        await onActivateAgent(updated.id);
        antdMessage.success(`已保存并切换工作台至「${updated.name}」`);
      } else {
        antdMessage.success("智能体配置已保存");
      }
      return true;
    } catch (err) {
      antdMessage.error(err instanceof Error ? err.message : "保存配置失败");
      return false;
    } finally {
      setSaving(false);
    }
  };

  const handleTestConnection = async () => {
    if (!form || !selectedId) return;
    if (!isValidBaseUrl(form.base_url)) {
      antdMessage.error("Base URL 格式不正确，请以 http://、https:// 或 mock:// 开头");
      return;
    }
    setTestingConn(true);
    setTestResult(null);
    try {
      const res = await api.testAgentConnection({
        agent_id: selectedId,
        base_url: form.base_url.trim(),
        api_key: apiKeyInput.trim() || undefined,
        clear_api_key: clearApiKey,
        model: form.model.trim(),
        json_mode: form.json_mode,
      });
      setTestResult(res);
      if (res.available_models.length > 0) {
        setAvailableModels(res.available_models);
      }
      if (selectedAgent && !apiKeyInput.trim() && !clearApiKey && form.base_url.trim() === selectedAgent.base_url
          && form.model.trim() === selectedAgent.model && form.json_mode === selectedAgent.json_mode) {
        onConnectionTested?.(selectedId, res);
      }
      if (res.ok) {
        antdMessage.success(`连接测试通过 (${res.latency_ms} ms)`);
      } else {
        antdMessage.warning("连接测试未通过，请查看连接诊断提示");
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : "测试连接请求失败";
      setTestResult({
        ok: false,
        message: msg,
        latency_ms: 0,
        effective_base_url: form.base_url.trim(),
        effective_model: form.model.trim(),
        available_models: [],
      });
      antdMessage.error(msg);
    } finally {
      setTestingConn(false);
    }
  };

  const handleFetchModels = async () => {
    if (!form || !selectedId) return;
    if (!isValidBaseUrl(form.base_url)) {
      antdMessage.error("Base URL 格式不正确，无法拉取模型列表");
      return;
    }
    setFetchingModels(true);
    try {
      const res = await api.fetchAgentModels({
        agent_id: selectedId,
        base_url: form.base_url.trim(),
        api_key: apiKeyInput.trim() || undefined,
        clear_api_key: clearApiKey,
        model: form.model.trim(),
      });
      if (res.error) {
        antdMessage.error(res.error);
      } else if (res.models.length === 0) {
        antdMessage.warning("接口未返回任何可用模型");
      } else {
        setAvailableModels(res.models);
        antdMessage.success(`已获取 ${res.models.length} 个可用模型，可在 Model Name 下拉框中选择`);
      }
    } catch (err) {
      antdMessage.error(err instanceof Error ? err.message : "拉取模型列表失败");
    } finally {
      setFetchingModels(false);
    }
  };

  const handleSchemaModeChange = (nextMode: "visual" | "json_schema") => {
    if (!form) return;
    const prevMode = form.extraction_schema.mode;
    if (prevMode === nextMode) return;

    if (nextMode === "json_schema") {
      const currentJson = form.extraction_schema.json_schema || {};
      const isEmptyJson = Object.keys(currentJson).length === 0;
      const targetJson = isEmptyJson
        ? fieldsToJsonSchema(form.extraction_schema.fields)
        : currentJson;
      setRawJsonSchemaText(JSON.stringify(targetJson, null, 2));
      setSchemaJsonError(null);
      setForm({
        ...form,
        extraction_schema: {
          ...form.extraction_schema,
          mode: "json_schema",
          json_schema: targetJson,
        },
      });
      return;
    }

    // Switching back to visual: preserve valid JSON edits in form state
    try {
      const parsed = JSON.parse(rawJsonSchemaText || "{}");
      setSchemaJsonError(null);
      setForm({
        ...form,
        extraction_schema: {
          ...form.extraction_schema,
          mode: "visual",
          json_schema: parsed,
        },
      });
    } catch {
      setForm({
        ...form,
        extraction_schema: { ...form.extraction_schema, mode: "visual" },
      });
    }
  };

  const handlePresetClick = (preset: (typeof PROVIDER_PRESETS)[number]) => {
    if (!form || !selectedAgent) return;
    const prevUrl = form.base_url.trim();
    const isSwitchingProvider = prevUrl !== "" && prevUrl !== preset.base_url;
    setForm({ ...form, base_url: preset.base_url, model: preset.model });
    setTestResult(null);
    setAvailableModels([]);
    if (
      isSwitchingProvider &&
      preset.base_url !== "mock://demo" &&
      (selectedAgent.has_custom_api_key || apiKeyInput.trim())
    ) {
      antdMessage.info(`已切换预设为 ${preset.label}，请确认 API Key 与新接口匹配`);
    }
  };

  const renderKeyStatusTag = () => {
    if (!selectedAgent) return null;
    if (form?.base_url.trim().startsWith("mock://")) {
      return <Tag color="purple">离线 Mock（无需 Key）</Tag>;
    }
    if (apiKeyInput.trim()) {
      return <Tag color="processing">待保存新 Key</Tag>;
    }
    if (clearApiKey) {
      return (
        <Tag color="warning">
          {envStatus?.has_default_api_key ? "将清空并改用 .env 默认 Key" : "将清空自定义 Key"}
        </Tag>
      );
    }
    if (selectedAgent.has_custom_api_key) {
      return <Tag color="success">已保存自定义 Key ({selectedAgent.api_key_masked})</Tag>;
    }
    if (envStatus?.has_default_api_key) {
      return <Tag color="blue">使用 .env 默认 Key</Tag>;
    }
    return <Tag color="error">未配置 API Key</Tag>;
  };

  return (
    <Modal
      className="studio-modal"
      open={open}
      onCancel={handleCloseModal}
      footer={null}
      width="88vw"
      style={{ maxWidth: 1200, top: 28 }}
      styles={{ body: { padding: 0, height: "78vh", overflow: "hidden" } }}
      title={
        <div>
          <Text strong style={{ fontSize: 15 }}>
            自定义智能体配置管理 (Agent Profiles)
          </Text>
          <Text type="secondary" style={{ fontSize: 12, display: "block", fontWeight: 400 }}>
            支持切换任意 OpenAI Chat Completions 兼容接口、连通性测试、调整 Prompt、业务规则与抽取字段 Schema
          </Text>
        </div>
      }
    >
      <div className="admin-layout flex h-full overflow-hidden" style={{ borderTop: "1px solid #f0f0f0" }}>
        {/* Left List */}
        <div
          className="admin-nav w-60 p-3 flex flex-col gap-2 shrink-0"
          style={{ background: "#fafafa", borderRight: "1px solid #f0f0f0" }}
        >
          <Button type="primary" block icon={<Plus className="w-3.5 h-3.5" />} onClick={handleCreateNew}>
            新建智能体
          </Button>
          <div className="admin-list flex-1 overflow-y-auto space-y-1.5 mt-1">
            {agents.map((a) => {
              const active = a.id === selectedId;
              const isWorkbenchActive = a.id === activeAgentId;
              return (
                <div
                  key={a.id}
                  onClick={() => handleSelectAgentInList(a.id)}
                  className="p-2.5 rounded-md cursor-pointer transition-colors"
                  style={{
                    background: active ? "#e8f0e9" : "transparent",
                    border: active ? "1px solid #b9cdbd" : "1px solid transparent",
                  }}
                >
                  <div className="flex items-center justify-between gap-1">
                    <Text
                      ellipsis
                      style={{
                        fontSize: 13,
                        fontWeight: active ? 600 : 500,
                        color: active ? "#4e745b" : "rgba(0,0,0,0.88)",
                      }}
                    >
                      {a.name}
                    </Text>
                    {isWorkbenchActive && (
                      <Tag color="green" style={{ margin: 0, fontSize: 10, lineHeight: "16px" }}>
                        当前使用
                      </Tag>
                    )}
                  </div>
                  <Text type="secondary" style={{ fontSize: 11, fontFamily: "monospace" }}>
                    {a.effective_model}
                  </Text>
                </div>
              );
            })}
          </div>
        </div>

        {/* Right Form */}
        {form && selectedAgent ? (
          <div className="flex-1 flex flex-col overflow-hidden bg-white">
            {/* Sticky Toolbar */}
            <div
              className="px-5 py-3 flex items-center justify-between gap-2 shrink-0 flex-wrap"
              style={{ borderBottom: "1px solid #f0f0f0" }}
            >
              <Space wrap>
                <Button
                  size="small"
                  icon={<Copy className="w-3.5 h-3.5" />}
                  onClick={() => {
                    confirmDiscardIfDirty(async () => {
                      try {
                        const dup = await onDuplicateAgent(selectedAgent.id);
                        setSelectedId(dup.id);
                        loadAgentIntoForm(dup);
                        antdMessage.success("已复制智能体副本");
                      } catch (err) {
                        antdMessage.error(err instanceof Error ? err.message : "复制失败");
                      }
                    });
                  }}
                >
                  复制副本
                </Button>
                <Popconfirm
                  title="确认删除该智能体？"
                  okText="删除"
                  cancelText="取消"
                  okButtonProps={{ danger: true }}
                  onConfirm={async () => {
                    try {
                      await onDeleteAgent(selectedAgent.id);
                      antdMessage.success("已删除智能体");
                    } catch (err) {
                      antdMessage.error(err instanceof Error ? err.message : "删除失败");
                    }
                  }}
                >
                  <Button
                    size="small"
                    danger
                    disabled={agents.length <= 1}
                    icon={<Trash2 className="w-3.5 h-3.5" />}
                  >
                    删除
                  </Button>
                </Popconfirm>
                {isDirty && (
                  <Tag color="warning" style={{ margin: 0 }}>
                    有未保存修改
                  </Tag>
                )}
              </Space>

              <Space wrap>
                <Button
                  loading={testingConn}
                  icon={<PlugZap className="w-3.5 h-3.5" />}
                  onClick={handleTestConnection}
                >
                  测试连接
                </Button>
                {onActivateAgent && selectedAgent.id !== activeAgentId && (
                  <Button
                    icon={<CheckCircle2 className="w-3.5 h-3.5" />}
                    loading={saving}
                    onClick={() => handleSave(true)}
                  >
                    保存并设为当前使用
                  </Button>
                )}
                <Button
                  type="primary"
                  loading={saving}
                  icon={<Save className="w-3.5 h-3.5" />}
                  onClick={() => handleSave(false)}
                >
                  保存配置
                </Button>
              </Space>
            </div>

            {/* Scrollable Body */}
            <div className="flex-1 overflow-y-auto p-5 space-y-5">
              {/* Basic Info */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <Text strong style={{ fontSize: 12, display: "block", marginBottom: 6 }}>
                    智能体名称
                  </Text>
                  <Input
                    value={form.name}
                    onChange={(e) => setForm({ ...form, name: e.target.value })}
                  />
                </div>
                <div>
                  <Text strong style={{ fontSize: 12, display: "block", marginBottom: 6 }}>
                    职责描述
                  </Text>
                  <Input
                    value={form.description}
                    onChange={(e) => setForm({ ...form, description: e.target.value })}
                  />
                </div>
              </div>

              {/* Model Connection Card */}
              <Card
                size="small"
                title={
                  <Space size={8} wrap>
                    <span className="text-xs font-semibold">模型连接配置（留空将读取 backend/.env 默认值）</span>
                    {renderKeyStatusTag()}
                  </Space>
                }
                extra={
                  <Space size={4} wrap>
                    {PROVIDER_PRESETS.map((p) => (
                      <Tag
                        key={p.label}
                        color="blue"
                        style={{ cursor: "pointer", margin: 0, fontSize: 11 }}
                        onClick={() => handlePresetClick(p)}
                      >
                        {p.label}
                      </Tag>
                    ))}
                  </Space>
                }
                style={{ background: "#fafafa" }}
              >
                <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                  <div>
                    <Text type="secondary" style={{ fontSize: 11, display: "block", marginBottom: 4 }}>
                      Base URL
                    </Text>
                    <Input
                      value={form.base_url}
                      placeholder={envStatus?.default_base_url || "https://api.openai.com/v1"}
                      onChange={(e) => {
                        setForm({ ...form, base_url: e.target.value });
                        setTestResult(null);
                      }}
                      style={{ fontFamily: "monospace", fontSize: 12 }}
                    />
                  </div>
                  <div>
                    <div className="flex items-center justify-between mb-1">
                      <Text type="secondary" style={{ fontSize: 11 }}>
                        Model Name
                      </Text>
                      <Button
                        type="link"
                        size="small"
                        loading={fetchingModels}
                        icon={<RefreshCw className="w-3 h-3" />}
                        style={{ height: 18, padding: 0, fontSize: 11 }}
                        onClick={handleFetchModels}
                      >
                        拉取模型
                      </Button>
                    </div>
                    <AutoComplete
                      style={{ width: "100%", fontFamily: "monospace", fontSize: 12 }}
                      value={form.model}
                      placeholder={envStatus?.default_model || "gpt-4o-mini"}
                      options={availableModels.map((m) => ({ value: m, label: m }))}
                      filterOption={(inputValue, option) =>
                        (option?.value ?? "")
                          .toString()
                          .toLowerCase()
                          .includes(inputValue.toLowerCase())
                      }
                      onChange={(val) => {
                        setForm({ ...form, model: val });
                        setTestResult(null);
                      }}
                    />
                  </div>
                  <div>
                    <Text type="secondary" style={{ fontSize: 11, display: "block", marginBottom: 4 }}>
                      API Key（脱敏存储 · 留空保持不变）
                    </Text>
                    <Space.Compact style={{ width: "100%" }}>
                      <Input.Password
                        prefix={<KeyRound className="w-3.5 h-3.5 text-slate-400" />}
                        value={apiKeyInput}
                        placeholder={
                          clearApiKey
                            ? "已清空，将使用 .env 默认 Key"
                            : selectedAgent.has_custom_api_key
                              ? `已配置 (${selectedAgent.api_key_masked})，输入新 Key 可覆盖`
                              : envStatus?.has_default_api_key
                                ? "使用 .env 默认 Key"
                                : "未配置，请输入 sk-..."
                        }
                        onChange={(e) => {
                          setApiKeyInput(e.target.value);
                          setClearApiKey(false);
                          setTestResult(null);
                        }}
                        style={{ fontFamily: "monospace", fontSize: 12 }}
                      />
                      {selectedAgent.has_custom_api_key && !clearApiKey && (
                        <Button
                          onClick={() => {
                            setApiKeyInput("");
                            setClearApiKey(true);
                            setTestResult(null);
                          }}
                        >
                          清空
                        </Button>
                      )}
                      {clearApiKey && (
                        <Button
                          onClick={() => {
                            setClearApiKey(false);
                            setTestResult(null);
                          }}
                        >
                          撤销
                        </Button>
                      )}
                    </Space.Compact>
                  </div>
                </div>

                {testResult && (
                  <Alert
                    className="mt-3"
                    type={testResult.ok ? "success" : "error"}
                    showIcon
                    message={
                      <div className="space-y-1">
                        <div className="text-xs font-medium">{testResult.message}</div>
                        <div className="text-[11px] font-mono opacity-80">
                          实际请求: {testResult.effective_base_url} · 模型: {testResult.effective_model}
                          {testResult.status_code ? ` · HTTP ${testResult.status_code}` : ""}
                        </div>
                        {!testResult.ok && testResult.available_models.length > 0 && (
                          <div className="flex flex-wrap items-center gap-1 pt-1">
                            <span className="text-[11px]">点击填入当前 Key 可用模型:</span>
                            {testResult.available_models.slice(0, 8).map((m) => (
                              <Tag
                                key={m}
                                color="blue"
                                style={{ cursor: "pointer", margin: 0, fontFamily: "monospace", fontSize: 11 }}
                                onClick={() => setForm({ ...form, model: m })}
                              >
                                {m}
                              </Tag>
                            ))}
                          </div>
                        )}
                      </div>
                    }
                  />
                )}

                <Divider style={{ margin: "12px 0" }} />

                <div className="grid grid-cols-1 md:grid-cols-4 gap-4 items-center">
                  <div>
                    <Text type="secondary" style={{ fontSize: 11 }}>
                      Temperature: <strong>{form.temperature}</strong>
                    </Text>
                    <Slider
                      min={0}
                      max={1.5}
                      step={0.05}
                      value={form.temperature}
                      onChange={(v) => setForm({ ...form, temperature: v })}
                      style={{ margin: "6px 0 0" }}
                    />
                  </div>
                  <div>
                    <Text type="secondary" style={{ fontSize: 11, display: "block", marginBottom: 4 }}>
                      Max Tokens
                    </Text>
                    <InputNumber
                      min={64}
                      max={128000}
                      step={256}
                      value={form.max_tokens}
                      onChange={(v) => setForm({ ...form, max_tokens: Number(v) || 4096 })}
                      style={{ width: "100%", fontFamily: "monospace" }}
                    />
                  </div>
                  <Tooltip title="开启后发送 response_format: json_object；不支持的代理将自动降级">
                    <div className="flex items-center gap-2 pt-3">
                      <Switch
                        size="small"
                        checked={form.json_mode}
                        onChange={(checked) => setForm({ ...form, json_mode: checked })}
                      />
                      <Text style={{ fontSize: 12 }}>强制 JSON Output</Text>
                    </div>
                  </Tooltip>
                  <Tooltip title="旧版抽取要求逐字 source_quote。领域语义始终核对来源。">
                    <div className="flex items-center gap-2 pt-3">
                      <Switch
                        size="small"
                        checked={form.require_source_quote}
                        onChange={(checked) =>
                          setForm({ ...form, require_source_quote: checked })
                        }
                      />
                      <Text style={{ fontSize: 12 }}>强制 source_quote</Text>
                    </div>
                  </Tooltip>
                </div>
              </Card>

              <Card size="small" title="执行与领域语义">
                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                  <label className="flex flex-col gap-1">请求超时（秒）
                    <InputNumber aria-label="请求超时（秒）" min={1} max={600} placeholder="使用服务默认值"
                      value={form.execution?.timeout_seconds ?? null} onChange={(value) => setForm({
                        ...form, execution: { ...defaults, ...form.execution, timeout_seconds: value },
                      })} />
                  </label>
                  <label className="flex flex-col gap-1">单次运行请求预算
                    <InputNumber aria-label="单次运行请求预算" min={1} max={32} placeholder="使用服务默认值"
                      value={form.execution?.request_budget ?? null} onChange={(value) => setForm({
                        ...form, execution: { ...defaults, ...form.execution, request_budget: value },
                      })} />
                  </label>
                  <div className="flex flex-col gap-2">
                    <span>词表候选消歧</span>
                    <Switch aria-label="允许模型辅助消歧" size="small" checked={(form.execution ?? defaults).llm_disambiguation}
                      onChange={(checked) => setForm({ ...form, execution: { ...defaults, ...form.execution, llm_disambiguation: checked } })} />
                  </div>
                </div>
                <p className="muted">分批抽取、格式降级、局部修复和消歧共享请求预算。当前领域 Schema、本体和词表版本可在结果概览查看。</p>
              </Card>

              {/* System Prompt & Rules */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <Text strong style={{ fontSize: 12, display: "block", marginBottom: 6 }}>
                    System Prompt（系统提示词）
                  </Text>
                  <Input.TextArea
                    rows={6}
                    value={form.system_prompt}
                    onChange={(e) => setForm({ ...form, system_prompt: e.target.value })}
                    style={{ fontFamily: "monospace", fontSize: 12 }}
                  />
                </div>
                <div>
                  <Text strong style={{ fontSize: 12, display: "block", marginBottom: 6 }}>
                    补充业务规则 / 解析约束
                  </Text>
                  <Input.TextArea
                    rows={6}
                    value={form.business_rules}
                    placeholder="例如：工时统一换算为秒；缺失数值禁止猜想..."
                    onChange={(e) => setForm({ ...form, business_rules: e.target.value })}
                    style={{ fontFamily: "monospace", fontSize: 12 }}
                  />
                </div>
              </div>

              {/* Extraction Schema */}
              <Card
                size="small"
                title={<span className="text-xs font-semibold">旧版自定义抽取 Schema</span>}
                extra={
                  <Space size={8}>
                    {form.extraction_schema.mode === "json_schema" && (
                      <Button
                        size="small"
                        icon={<Wand2 className="w-3 h-3" />}
                        onClick={() => {
                          const generated = fieldsToJsonSchema(form.extraction_schema.fields);
                          setRawJsonSchemaText(JSON.stringify(generated, null, 2));
                          setSchemaJsonError(null);
                          setForm({
                            ...form,
                            extraction_schema: {
                              ...form.extraction_schema,
                              json_schema: generated,
                            },
                          });
                          antdMessage.success("已根据可视化字段同步生成 JSON Schema");
                        }}
                      >
                        从可视化字段同步生成
                      </Button>
                    )}
                    <Segmented
                      size="small"
                      value={form.extraction_schema.mode}
                      onChange={(v) => handleSchemaModeChange(v as "visual" | "json_schema")}
                      options={[
                        { label: "可视化字段定义", value: "visual" },
                        { label: "JSON Schema", value: "json_schema" },
                      ]}
                    />
                  </Space>
                }
              >
                {form.extraction_schema.mode === "visual" ? (
                  <FieldTreeEditor
                    fields={form.extraction_schema.fields}
                    onChange={(nextFields) =>
                      setForm({
                        ...form,
                        extraction_schema: {
                          ...form.extraction_schema,
                          fields: nextFields,
                        },
                      })
                    }
                  />
                ) : (
                  <div className="space-y-2">
                    {schemaJsonError && <Alert type="error" showIcon message={schemaJsonError} />}
                    <Input.TextArea
                      rows={11}
                      value={rawJsonSchemaText}
                      onChange={(e) => setRawJsonSchemaText(e.target.value)}
                      spellCheck={false}
                      style={{
                        fontFamily: "monospace",
                        fontSize: 12,
                        background: "#141414",
                        color: "#f0f0f0",
                      }}
                    />
                  </div>
                )}
              </Card>
            </div>
          </div>
        ) : null}
      </div>
    </Modal>
  );
}
