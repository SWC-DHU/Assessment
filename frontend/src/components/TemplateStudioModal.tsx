import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  Alert,
  AutoComplete,
  App as AntdApp,
  Button,
  Card,
  Checkbox,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import { ArrowDown, ArrowUp, Plus, Save, Trash2 } from "lucide-react";
import { HtmlPreview } from "./HtmlPreview";
import { api } from "../api/client";
import type {
  OutputTemplate,
  OutputTemplatePayload,
  TableColumnMapping,
  TemplatePreviewResponse,
} from "../types";

const { Text } = Typography;

interface TemplateStudioModalProps {
  open: boolean;
  templates: OutputTemplate[];
  activeTemplateId: string | null;
  sampleJson: Record<string, unknown> | null;
  onClose: () => void;
  onCreateTemplate: (payload: OutputTemplatePayload) => Promise<OutputTemplate>;
  onUpdateTemplate: (id: string, payload: Partial<OutputTemplatePayload>) => Promise<OutputTemplate>;
  onDeleteTemplate: (id: string) => Promise<void>;
}

const mappingPaths = ["seq", "name", "number", "operation_type", "equipment", "process_time_sec", "cycle_time_sec",
  "input_materials", "output_materials", "predecessors", "capability_names", "quality_requirements", "source_quote",
  "evidence.0.quote", "evidence.0.location.cell", "parameter_code", "comparator", "value", "unit", "basis", "status",
  "$root.product_name", "$root.schema_version", "$root.semantic.validation.status"];

const DEFAULT_SAMPLE_JSON: Record<string, unknown> = {
  product_name: "新能源汽车电机壳体（压铸+CNC加工）",
  part_no: "MTR-HSG-2026-A03",
  target_takt_sec: 60,
  operations: [
    {
      seq: "OP10",
      name: "高压压铸成型",
      equipment: "2500T冷室压铸机",
      cycle_time_sec: 42,
      input_materials: ["ADC12铝合金锭 (3.8kg/件)", "脱模剂"],
      output_materials: ["壳体压铸毛坯"],
      predecessors: [],
      quality_requirements: "X光探伤气孔率 <= 2%",
      source_quote: "Sheet1!B2",
    },
    {
      seq: "OP20",
      name: "CNC精密加工",
      equipment: "五轴卧式加工中心",
      cycle_time_sec: 55,
      input_materials: ["壳体压铸毛坯", "切削液"],
      output_materials: ["精加工电机壳体"],
      predecessors: ["OP10"],
      quality_requirements: "轴承孔同轴度 <= 0.015mm",
      source_quote: "Sheet1!B3",
    },
  ],
  constraints: [{ type: "节拍约束", description: "产线节拍 <= 60s/件", source_quote: "[L2]" }],
  unknown_items: [{ item: "换刀摊销口径未明确", reason: "原文未提及", source_quote: "未提及" }],
};

export function TemplateStudioModal({
  open,
  templates,
  activeTemplateId,
  sampleJson,
  onClose,
  onCreateTemplate,
  onUpdateTemplate,
  onDeleteTemplate,
}: TemplateStudioModalProps) {
  const { message: antdMessage } = AntdApp.useApp();
  const [selectedId, setSelectedId] = useState<string | null>(activeTemplateId);
  const [form, setForm] = useState<OutputTemplatePayload | null>(null);
  const [saving, setSaving] = useState(false);
  const [livePreview, setLivePreview] = useState<TemplatePreviewResponse | null>(null);

  useEffect(() => {
    if (!open) return;
    const tid =
      selectedId && templates.some((t) => t.id === selectedId)
        ? selectedId
        : activeTemplateId || templates[0]?.id || null;
    setSelectedId(tid);
  }, [open, templates, activeTemplateId, selectedId]);

  useEffect(() => {
    const tmpl = templates.find((t) => t.id === selectedId);
    if (!tmpl) {
      setForm(null);
      return;
    }
    setForm({
      name: tmpl.name,
      description: tmpl.description,
      template_type: tmpl.template_type,
      format: tmpl.format,
      jinja_content: tmpl.jinja_content,
      table_config: {
        row_source_path: tmpl.table_config?.row_source_path ?? "operations",
        columns: tmpl.table_config?.columns ?? [],
        include_summary_sheet: tmpl.table_config?.include_summary_sheet ?? true,
      },
    });
  }, [selectedId, templates]);

  // Debounced live preview against backend Jinja2 / Table Mapping engine
  useEffect(() => {
    if (!open || !form) return;
    const dataToUse = sampleJson || DEFAULT_SAMPLE_JSON;
    let current = true;
    const timer = setTimeout(async () => {
      try {
        const res = await api.previewTemplateDraft({
          template_type: form.template_type,
          format: form.format,
          jinja_content: form.jinja_content,
          table_config: form.table_config as unknown as Record<string, unknown>,
          data: dataToUse,
        });
        if (current) setLivePreview(res);
      } catch (err) {
        if (current) setLivePreview({
          template_id: "draft", template_type: form.template_type, format: form.format,
          rendered_text: "", table_headers: [], table_rows: [], error: String(err),
        });
      }
    }, 300);
    return () => { current = false; clearTimeout(timer); };
  }, [open, form, sampleJson]);

  const selectedTemplate = templates.find((t) => t.id === selectedId);
  const [history, setHistory] = useState<{ version: number; created_at: string; definition: OutputTemplate }[]>([]);
  useEffect(() => { setHistory([]); }, [selectedId, selectedTemplate?.version]);

  const handleCreate = async () => {
    const created = await onCreateTemplate({
      name: "新自定义输出模板",
      description: "基于 Jinja2 的工艺需求导出模板",
      template_type: "jinja2",
      format: "markdown",
      jinja_content:
        "# {{ product_name }}\n\n- 图号：{{ part_no | fmt('-') }}\n\n{% for op in operations or [] %}- **{{ op.seq }}** {{ op.name }} ({{ op.cycle_time_sec }}s)\n{% endfor %}",
      table_config: {
        row_source_path: "operations",
        columns: [
          { header: "工序号", path: "seq", fallback: "-" },
          { header: "工序名称", path: "name", fallback: "-" },
        ],
        include_summary_sheet: true,
      },
    });
    setSelectedId(created.id);
    antdMessage.success("已创建新模板");
  };

  const handleSave = async () => {
    if (!form || !selectedId) return;
    setSaving(true);
    try {
      await onUpdateTemplate(selectedId, form);
      antdMessage.success("模板已保存并同步刷新工作台");
    } catch (err) {
      antdMessage.error(String(err));
    } finally {
      setSaving(false);
    }
  };

  const updateCol = (idx: number, patch: Partial<TableColumnMapping>) => {
    if (!form) return;
    const cols = form.table_config.columns.map((c, i) =>
      i === idx ? { ...c, ...patch } : c
    );
    setForm({ ...form, table_config: { ...form.table_config, columns: cols } });
  };

  const moveCol = (idx: number, delta: -1 | 1) => {
    if (!form) return;
    const target = idx + delta;
    const cols = [...form.table_config.columns];
    if (target < 0 || target >= cols.length) return;
    [cols[idx], cols[target]] = [cols[target], cols[idx]];
    setForm({ ...form, table_config: { ...form.table_config, columns: cols } });
  };

  const liveTableColumns = (livePreview?.table_headers || []).map((h, i) => ({
    title: h,
    dataIndex: `c_${i}`,
    key: `c_${i}`,
    render: (text: string) => <span className="text-xs whitespace-pre-wrap">{text}</span>,
  }));

  const liveTableRows = (livePreview?.table_rows || []).map((r, ri) => {
    const rec: Record<string, string | number> = { key: ri };
    r.forEach((cell, ci) => {
      rec[`c_${ci}`] = cell;
    });
    return rec;
  });

  return (
    <Modal
      className="studio-modal"
      open={open}
      onCancel={onClose}
      footer={null}
      width="92vw"
      style={{ maxWidth: 1320, top: 24 }}
      styles={{ body: { padding: 0, height: "80vh", overflow: "hidden" } }}
      title={
        <div>
          <Text strong style={{ fontSize: 15 }}>
            输出模板管理
          </Text>
          <Text type="secondary" style={{ fontSize: 12, display: "block", fontWeight: 400 }}>
            模板将领域语义或旧版 JSON 映射为文档和表格。领域 Schema 由系统维护。
          </Text>
        </div>
      }
    >
      <div className="admin-layout flex h-full overflow-hidden" style={{ borderTop: "1px solid #f0f0f0" }}>
        {/* Left template list */}
        <div
          className="admin-nav w-56 p-3 flex flex-col gap-2 shrink-0"
          style={{ background: "#fafafa", borderRight: "1px solid #f0f0f0" }}
        >
          <Button type="primary" block icon={<Plus className="w-3.5 h-3.5" />} onClick={handleCreate}>
            新建输出模板
          </Button>
          <div className="admin-list flex-1 overflow-y-auto space-y-1.5 mt-1">
            {templates.map((t) => {
              const active = t.id === selectedId;
              return (
                <div
                  key={t.id}
                  onClick={() => setSelectedId(t.id)}
                  className="p-2.5 rounded-md cursor-pointer transition-colors"
                  style={{
                    background: active ? "#e8f0e9" : "transparent",
                    border: active ? "1px solid #b9cdbd" : "1px solid transparent",
                  }}
                >
                  <Text
                    ellipsis
                    style={{
                      fontSize: 12,
                      fontWeight: active ? 600 : 500,
                      color: active ? "#4e745b" : "rgba(0,0,0,0.88)",
                      display: "block",
                    }}
                  >
                    {t.name}
                  </Text>
                  <Tag
                    bordered={false}
                    color={t.template_type === "table_mapping" ? "green" : "blue"}
                    style={{ margin: "4px 0 0", fontSize: 10 }}
                  >
                    {t.template_type === "table_mapping" ? "表格映射 / Excel" : `Jinja2 · ${t.format}`}
                  </Tag>
                  <p className="muted">{t.description || "输出模板"}</p>
                  <small>v{t.version} · Schema {t.schema_version} · {t.table_config?.columns?.length ?? 0} 列</small>
                  <small className="block">{new Date(t.updated_at).toLocaleDateString("zh-CN")}</small>
                </div>
              );
            })}
          </div>
        </div>

        {/* Right Split Editor + Live Preview */}
        {form && selectedTemplate ? (
          <div className="admin-editor flex-1 flex flex-col overflow-hidden bg-white">
            {/* Top Action Bar */}
            <div
              className="px-4 py-2.5 flex items-center justify-between shrink-0"
              style={{ borderBottom: "1px solid #f0f0f0" }}
            >
              <Popconfirm
                title="确认删除该模板？"
                okText="删除"
                cancelText="取消"
                okButtonProps={{ danger: true }}
                onConfirm={async () => {
                  await onDeleteTemplate(selectedTemplate.id);
                  antdMessage.success("已删除模板");
                }}
              >
                <Button
                  size="small"
                  danger
                  disabled={templates.length <= 1}
                  icon={<Trash2 className="w-3.5 h-3.5" />}
                >
                  删除模板
                </Button>
              </Popconfirm>

              <Button size="small" onClick={async () => {
                try {
                  setHistory(history.length ? [] : await api.templateVersions(selectedTemplate.id));
                } catch (err) { antdMessage.error(String(err)); }
              }}>版本历史</Button>
              <Button
                type="primary"
                loading={saving}
                icon={<Save className="w-3.5 h-3.5" />}
                onClick={handleSave}
              >
                保存模板
              </Button>
            </div>

            {!!history.length && <div className="template-history">
              {history.map((item) => <button className="text-action" type="button" key={item.version} onClick={() => {
                const definition = item.definition;
                setForm({
                  name: definition.name, description: definition.description, template_type: definition.template_type,
                  format: definition.format, jinja_content: definition.jinja_content, table_config: definition.table_config,
                });
                setHistory([]);
                antdMessage.info("已载入历史定义。保存将生成新版本。");
              }}>载入 v{item.version} · {new Date(item.created_at).toLocaleDateString("zh-CN")}</button>)}
            </div>}
            <div className="template-panes flex-1">
                {/* Editor Side */}
                <div className="template-pane">
                  <div className="h-full overflow-y-auto p-4 space-y-4">
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                      <div>
                        <Text strong style={{ fontSize: 12, display: "block", marginBottom: 4 }}>
                          模板名称
                        </Text>
                        <Input
                          value={form.name}
                          onChange={(e) => setForm({ ...form, name: e.target.value })}
                        />
                      </div>
                      <div>
                        <Text strong style={{ fontSize: 12, display: "block", marginBottom: 4 }}>
                          模板类型
                        </Text>
                        <Select
                          style={{ width: "100%" }}
                          value={form.template_type}
                          onChange={(tt) =>
                            setForm({
                              ...form,
                              template_type: tt,
                              format: tt === "table_mapping" ? "table" : "markdown",
                            })
                          }
                          options={[
                            { value: "jinja2", label: "在线文本 / Markdown / HTML (Jinja2)" },
                            { value: "table_mapping", label: "结构化表格映射 (Excel / Word)" },
                          ]}
                        />
                      </div>
                    </div>

                    <div>
                      <Text strong style={{ fontSize: 12, display: "block", marginBottom: 4 }}>
                        模板说明
                      </Text>
                      <Input
                        value={form.description}
                        onChange={(e) => setForm({ ...form, description: e.target.value })}
                      />
                    </div>

                    {form.template_type === "jinja2" ? (
                      <div className="space-y-2">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <Space size={4} wrap>
                            <Text type="secondary" style={{ fontSize: 11 }}>
                              快捷变量:
                            </Text>
                            {[
                              "{{ product_name }}",
                              "{{ part_no | fmt('-') }}",
                              "{% for op in operations or [] %}\n{% endfor %}",
                              "| join_items('、')",
                            ].map((snippet) => (
                              <Tag
                                key={snippet}
                                style={{ cursor: "pointer", fontFamily: "monospace", fontSize: 11, margin: 0 }}
                                onClick={() =>
                                  setForm({
                                    ...form,
                                    jinja_content: `${form.jinja_content}\n${snippet}`,
                                  })
                                }
                              >
                                {snippet.replace("\n", " ... ")}
                              </Tag>
                            ))}
                          </Space>

                          <Select
                            size="small"
                            style={{ width: 110 }}
                            value={form.format}
                            onChange={(fmt) => setForm({ ...form, format: fmt })}
                            options={[
                              { value: "markdown", label: "Markdown" },
                              { value: "html", label: "HTML" },
                              { value: "text", label: "纯文本" },
                            ]}
                          />
                        </div>

                        <Input.TextArea
                          rows={16}
                          value={form.jinja_content}
                          onChange={(e) => setForm({ ...form, jinja_content: e.target.value })}
                          spellCheck={false}
                          style={{
                            fontFamily: "monospace",
                            fontSize: 12,
                            background: "#141414",
                            color: "#f0f0f0",
                          }}
                        />
                      </div>
                    ) : (
                      <Card size="small" style={{ background: "#fafafa" }}>
                        <div className="space-y-3">
                          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 items-end">
                            <div>
                              <Text strong style={{ fontSize: 12, display: "block", marginBottom: 4 }}>
                                行数据源路径 (row_source_path)
                              </Text>
                              <Input
                                value={form.table_config.row_source_path}
                                placeholder="如 operations（留空则展平根节点）"
                                onChange={(e) =>
                                  setForm({
                                    ...form,
                                    table_config: {
                                      ...form.table_config,
                                      row_source_path: e.target.value,
                                    },
                                  })
                                }
                                style={{ fontFamily: "monospace" }}
                              />
                            </div>
                            <Checkbox
                              checked={form.table_config.include_summary_sheet}
                              onChange={(e) =>
                                setForm({
                                  ...form,
                                  table_config: {
                                    ...form.table_config,
                                    include_summary_sheet: e.target.checked,
                                  },
                                })
                              }
                            >
                              <span className="text-xs">导出 Excel 附带「基础信息与约束」Sheet</span>
                            </Checkbox>
                          </div>

                          <div className="space-y-2">
                            <Text strong style={{ fontSize: 12 }}>
                              列映射配置（支持 $root.product_name 引用顶层字段）
                            </Text>
                            {form.table_config.columns.map((col, idx) => (
                              <div key={idx} className="grid grid-cols-12 gap-1.5 items-center">
                                <div className="col-span-3">
                                  <Input
                                    size="small"
                                    placeholder="导出列名"
                                    value={col.header}
                                    onChange={(e) => updateCol(idx, { header: e.target.value })}
                                  />
                                </div>
                                <div className="col-span-5">
                                  <AutoComplete
                                    aria-label={`第 ${idx + 1} 列语义路径`} size="small"
                                    placeholder="选择或输入语义路径" value={col.path}
                                    options={mappingPaths.map((value) => ({ value }))}
                                    filterOption={(query, option) => Boolean(option?.value.toLowerCase().includes(query.toLowerCase()))}
                                    onChange={(path) => updateCol(idx, { path })}
                                    style={{ fontFamily: "monospace", width: "100%" }}
                                  />
                                </div>
                                <div className="col-span-2">
                                  <Input
                                    size="small"
                                    placeholder="占位符"
                                    value={col.fallback}
                                    onChange={(e) => updateCol(idx, { fallback: e.target.value })}
                                  />
                                </div>
                                <div className="col-span-2 flex justify-end gap-0.5">
                                  <Button
                                    size="small"
                                    type="text"
                                    disabled={idx === 0}
                                    icon={<ArrowUp className="w-3 h-3" />}
                                    onClick={() => moveCol(idx, -1)}
                                  />
                                  <Button
                                    size="small"
                                    type="text"
                                    disabled={idx === form.table_config.columns.length - 1}
                                    icon={<ArrowDown className="w-3 h-3" />}
                                    onClick={() => moveCol(idx, 1)}
                                  />
                                  <Button
                                    size="small"
                                    type="text"
                                    danger
                                    icon={<Trash2 className="w-3 h-3" />}
                                    onClick={() =>
                                      setForm({
                                        ...form,
                                        table_config: {
                                          ...form.table_config,
                                          columns: form.table_config.columns.filter((_, i) => i !== idx),
                                        },
                                      })
                                    }
                                  />
                                </div>
                              </div>
                            ))}

                            <Button
                              type="dashed"
                              block
                              size="small"
                              icon={<Plus className="w-3.5 h-3.5" />}
                              onClick={() =>
                                setForm({
                                  ...form,
                                  table_config: {
                                    ...form.table_config,
                                    columns: [
                                      ...form.table_config.columns,
                                      { header: "新列", path: "", fallback: "-" },
                                    ],
                                  },
                                })
                              }
                            >
                              添加映射列
                            </Button>
                          </div>
                        </div>
                      </Card>
                    )}
                  </div>
                </div>

                {/* Real-time Preview Side */}
                <div className="template-pane">
                  <div
                    className="h-full flex flex-col overflow-hidden"
                    style={{ background: "#f5f5f5" }}
                  >
                    <div
                      className="px-4 py-2 bg-white flex items-center justify-between shrink-0"
                      style={{ borderBottom: "1px solid #f0f0f0" }}
                    >
                      <Text strong style={{ fontSize: 12 }}>
                        实时填充预览
                      </Text>
                      <Tag bordered={false} color={sampleJson ? "success" : "blue"} style={{ margin: 0, fontSize: 11 }}>
                        {sampleJson ? "数据源：当前会话 JSON" : "数据源：内置样例 JSON"}
                      </Tag>
                    </div>
                    <div className="flex-1 overflow-auto p-4">
                      {!!livePreview?.mapping_issues?.length && <Alert type="warning" showIcon title="映射需检查"
                        description={livePreview.mapping_issues.join(" · ")} />}
                      {livePreview?.error ? (
                        <Alert
                          type="error"
                          showIcon
                          message="模板语法或渲染错误"
                          description={livePreview.error}
                        />
                      ) : form.template_type === "table_mapping" ? (
                        <Card size="small" styles={{ body: { padding: 0 } }}>
                          <Table
                            size="small"
                            bordered
                            pagination={false}
                            columns={liveTableColumns}
                            dataSource={liveTableRows}
                            scroll={{ x: "max-content" }}
                          />
                        </Card>
                      ) : form.format === "html" ? (
                        <Card size="small">
                          <HtmlPreview value={livePreview?.rendered_text || ""} />
                        </Card>
                      ) : (
                        <Card size="small">
                          <div className="markdown-preview">
                            <ReactMarkdown remarkPlugins={[remarkGfm]}>
                              {livePreview?.rendered_text || ""}
                            </ReactMarkdown>
                          </div>
                        </Card>
                      )}
                    </div>
                  </div>
                </div>

            </div>
          </div>
        ) : null}
      </div>
    </Modal>
  );
}
