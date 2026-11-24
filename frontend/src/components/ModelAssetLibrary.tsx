import { useCallback, useEffect, useMemo, useState } from "react";
import { Alert, Button, Empty, Input, Modal, Select, Switch, Tabs } from "antd";
import {
  ArrowDownToLine,
  Box,
  Braces,
  CheckCircle2,
  Copy,
  FileUp,
  Link2,
  Search,
  ShieldCheck,
} from "lucide-react";
import { api } from "../api/client";
import type {
  AssetCatalogItem,
  AssetCatalogSummary,
  AssetValidationResult,
  BindingStatus,
  ModelAsset,
  ModelFileFormat,
} from "../types";
import { ContractBrowser } from "./ContractBrowser";

const categoryLabels: Record<string, string> = {
  feeding: "上料设备",
  cleaning: "清洗设备",
  adhesive: "涂胶与贴胶",
  stacking: "堆叠设备",
  assembly: "装配设备",
  welding: "焊接与激光",
  inspection: "检测设备",
  transport: "输送移载",
  robot: "工业机器人",
  auxiliary: "辅助预处理",
};

const bindingLabels: Record<BindingStatus, string> = {
  unbound: "未绑定三维",
  reserved: "已预留路径",
  bound: "已绑定模型",
};

const sourceLabels: Record<string, string> = {
  synthetic: "Synthetic 实验数据",
  vendor_spec: "厂商规格",
  calibrated: "标定模型",
  imported: "外部导入",
};

function saveJson(value: unknown, filename: string) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

interface ModelAssetLibraryProps {
  initialAssetId?: string | null;
}

export function ModelAssetLibrary({ initialAssetId }: ModelAssetLibraryProps) {
  const [summary, setSummary] = useState<AssetCatalogSummary | null>(null);
  const [items, setItems] = useState<AssetCatalogItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [category, setCategory] = useState<string>("");
  const [capability, setCapability] = useState<string>("");
  const [bindingStatus, setBindingStatus] = useState<string>("");
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(initialAssetId ?? null);
  const [detailTab, setDetailTab] = useState("semantics");
  const [schemaOpen, setSchemaOpen] = useState(false);

  // Import / Validate modal
  const [importOpen, setImportOpen] = useState(false);
  const [importText, setImportText] = useState("");
  const [importOverwrite, setImportOverwrite] = useState(false);
  const [validationResults, setValidationResults] = useState<AssetValidationResult[] | null>(null);
  const [importNotice, setImportNotice] = useState<{ type: "success" | "error" | "info"; text: string } | null>(null);
  const [importBusy, setImportBusy] = useState(false);

  // Binding form
  const [bindingUri, setBindingUri] = useState("");
  const [bindingFormat, setBindingFormat] = useState<ModelFileFormat>("glb");
  const [bindingState, setBindingState] = useState<BindingStatus>("reserved");
  const [bindingNotes, setBindingNotes] = useState("");
  const [bindingSaving, setBindingSaving] = useState(false);
  const [bindingNotice, setBindingNotice] = useState<string | null>(null);

  const loadCatalog = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [sum, list] = await Promise.all([
        api.getAssetSummary(),
        api.listAssets({
          category: category || undefined,
          capability: capability || undefined,
          binding_status: bindingStatus || undefined,
          search: search.trim() || undefined,
        }),
      ]);
      setSummary(sum);
      setItems(list.items);
      setSelectedId((prev) => {
        if (prev && list.items.some((item) => item.asset.identity.asset_id === prev)) return prev;
        return list.items[0]?.asset.identity.asset_id ?? null;
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [category, capability, bindingStatus, search]);

  useEffect(() => {
    void loadCatalog();
  }, [loadCatalog]);

  useEffect(() => {
    if (initialAssetId) setSelectedId(initialAssetId);
  }, [initialAssetId]);

  const selected = useMemo(
    () => items.find((item) => item.asset.identity.asset_id === selectedId) ?? items[0] ?? null,
    [items, selectedId]
  );

  useEffect(() => {
    if (!selected) return;
    const b = selected.asset.model_binding;
    setBindingUri(b.model_uri ?? `geometry/${selected.asset.identity.asset_id}.glb`);
    setBindingFormat((b.file_format && b.file_format !== "none" ? b.file_format : "glb") as ModelFileFormat);
    setBindingState(b.binding_status);
    setBindingNotes(b.notes ?? "");
    setBindingNotice(null);
  }, [selected]);

  const capabilityOptions = useMemo(() => {
    const caps = Object.keys(summary?.by_capability ?? {}).sort();
    return [{ value: "", label: "全部能力" }, ...caps.map((c) => ({ value: c, label: `${c} (${summary?.by_capability[c]})` }))];
  }, [summary]);

  const handleValidateImport = async () => {
    setImportNotice(null);
    setValidationResults(null);
    try {
      const parsed = JSON.parse(importText);
      setImportBusy(true);
      const res = await api.validateAssetPayload(parsed);
      setValidationResults(res.results);
      setImportNotice({
        type: res.valid ? "success" : "error",
        text: res.valid
          ? `全部 ${res.count} 项资产通过 ModelAsset Schema 与领域词表校验。`
          : `校验未通过，请检查下方字段诊断。`,
      });
    } catch (err) {
      setImportNotice({ type: "error", text: err instanceof Error ? err.message : "JSON 语法解析失败" });
    } finally {
      setImportBusy(false);
    }
  };

  const handleCommitImport = async () => {
    setImportNotice(null);
    try {
      const parsed = JSON.parse(importText);
      const arr = Array.isArray(parsed)
        ? parsed
        : parsed && Array.isArray(parsed.assets)
          ? parsed.assets
          : [parsed];
      setImportBusy(true);
      const res = await api.importAssets(arr, importOverwrite);
      setImportNotice({
        type: res.failed.length ? "error" : "success",
        text: `成功导入 ${res.imported_count} 项，跳过 ${res.skipped.length} 项，失败 ${res.failed.length} 项（资产库版本 ${res.catalog_version}）。`,
      });
      await loadCatalog();
    } catch (err) {
      setImportNotice({ type: "error", text: err instanceof Error ? err.message : String(err) });
    } finally {
      setImportBusy(false);
    }
  };

  const handleSaveBinding = async () => {
    if (!selected) return;
    setBindingSaving(true);
    setBindingNotice(null);
    try {
      await api.updateAssetBinding(selected.asset.identity.asset_id, {
        model_uri: bindingState === "unbound" ? null : bindingUri.trim() || null,
        file_format: bindingState === "unbound" ? null : bindingFormat,
        binding_status: bindingState,
        asset_ref_key: selected.asset.identity.asset_id,
        checksum_sha256: selected.asset.model_binding.checksum_sha256 ?? null,
        notes: bindingNotes.trim() || null,
      });
      setBindingNotice("已更新三维模型绑定配置");
      await loadCatalog();
    } catch (err) {
      setBindingNotice(`更新失败：${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBindingSaving(false);
    }
  };

  const asset: ModelAsset | null = selected?.asset ?? null;
  const validation = selected?.validation ?? null;

  return (
    <div className="module-shell">
      <div className="module-toolbar">
        <div className="module-title-group">
          <div className="module-heading">
            <Box size={18} />
            <h2>数字孪生模型资产库</h2>
            {summary && (
              <span className="pill-subtle">
                {summary.total_assets} 项资产 · Schema {summary.schema_version} · {summary.catalog_version}
              </span>
            )}
          </div>
          <p className="module-subtitle">
            模型语义与三维文件解耦存储；所有内置虚拟资产均明确标注为 synthetic 实验数据，支持按稳定 asset_id 多次实例化与后续三维绑定。
          </p>
        </div>
        <div className="module-toolbar-actions">
          <Button size="small" icon={<Braces size={14} />} onClick={() => setSchemaOpen(true)}>
            查看 Schema
          </Button>
          <Button
            size="small"
            icon={<FileUp size={14} />}
            onClick={() => {
              setImportOpen(true);
              setImportNotice(null);
              setValidationResults(null);
            }}
          >
            导入与校验 JSON
          </Button>
          <Button size="small" icon={<ArrowDownToLine size={14} />} href={api.exportAssetsUrl()}>
            导出资产库
          </Button>
        </div>
      </div>

      <div className="filter-strip">
        <Input
          allowClear
          size="small"
          prefix={<Search size={14} />}
          placeholder="搜索资产 ID、名称、能力或标签..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{ width: 240 }}
        />
        <Select
          size="small"
          aria-label="按类别筛选"
          value={category}
          onChange={setCategory}
          style={{ width: 155 }}
          options={[
            { value: "", label: `全部类别 (${summary?.total_assets ?? 0})` },
            ...(summary?.by_category ?? []).map((c) => ({
              value: c.code,
              label: `${c.label} (${c.count})`,
            })),
          ]}
        />
        <Select
          size="small"
          aria-label="按能力编码筛选"
          value={capability}
          onChange={setCapability}
          style={{ width: 170 }}
          showSearch
          optionFilterProp="label"
          options={capabilityOptions}
        />
        <Select
          size="small"
          aria-label="按三维绑定状态筛选"
          value={bindingStatus}
          onChange={setBindingStatus}
          style={{ width: 145 }}
          options={[
            { value: "", label: "全部绑定状态" },
            { value: "unbound", label: `未绑定 (${summary?.by_binding_status?.unbound ?? 0})` },
            { value: "reserved", label: `已预留 (${summary?.by_binding_status?.reserved ?? 0})` },
            { value: "bound", label: `已绑定 (${summary?.by_binding_status?.bound ?? 0})` },
          ]}
        />
        <span className="filter-count">当前显示 {items.length} 项</span>
      </div>

      {error && <Alert type="error" showIcon message="加载模型资产库失败" description={error} style={{ margin: "0 20px 12px" }} />}

      <div className="split-workspace">
        <aside className="split-list-pane" aria-label="模型资产列表">
          {loading && !items.length ? (
            <p className="muted" style={{ padding: 16 }}>正在加载模型资产…</p>
          ) : !items.length ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有符合筛选条件的模型资产" />
          ) : (
            <ul className="entity-list-rail">
              {items.map(({ asset: item, validation: val }) => {
                const active = item.identity.asset_id === asset?.identity.asset_id;
                const pct = Math.round((val?.completeness_score ?? 0) * 100);
                return (
                  <li key={item.identity.asset_id}>
                    <button
                      type="button"
                      className="rail-row"
                      data-active={active}
                      onClick={() => setSelectedId(item.identity.asset_id)}
                    >
                      <div className="rail-row-top">
                        <code className="id-chip">{item.identity.asset_id}</code>
                        <span className="cat-chip">{categoryLabels[item.identity.category] ?? item.identity.category}</span>
                        <span className="binding-dot" data-state={item.model_binding.binding_status}>
                          {bindingLabels[item.model_binding.binding_status]}
                        </span>
                      </div>
                      <strong className="rail-row-title" title={item.identity.name}>{item.identity.name}</strong>
                      <div className="rail-row-meta">
                        <span className="cap-inline" title={item.capabilities.map((c) => c.code).join(" · ")}>
                          {item.capabilities.map((c) => c.code).join(" · ")}
                        </span>
                        <span className="completeness-mini">完整度 {pct}%</span>
                      </div>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </aside>

        <section className="split-detail-pane" aria-label="模型资产详情">
          {!asset ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="在左侧选择一个模型资产查看详情" />
          ) : (
            <div className="detail-content">
              <header className="detail-header">
                <div>
                  <div className="detail-badges">
                    <code className="id-chip">{asset.identity.asset_id}</code>
                    <span className="cat-chip">{categoryLabels[asset.identity.category] ?? asset.identity.category}</span>
                    <span className="source-chip" data-source={asset.data_source}>
                      {sourceLabels[asset.data_source] ?? asset.data_source}
                    </span>
                    <span className="binding-dot" data-state={asset.model_binding.binding_status}>
                      {bindingLabels[asset.model_binding.binding_status]}
                    </span>
                  </div>
                  <h3>{asset.identity.name}</h3>
                  <p className="detail-desc">{asset.identity.description}</p>
                </div>
                <div className="detail-header-meta">
                  <div className="kpi-inline">
                    <span>建模完整度</span>
                    <strong>{Math.round((validation?.completeness_score ?? 0) * 100)}%</strong>
                  </div>
                  <div className="kpi-inline">
                    <span>可实例化</span>
                    <strong>{asset.instantiation_policy.reusable ? "支持多次实例化" : "单实例"}</strong>
                  </div>
                  <Button
                    size="small"
                    icon={<ArrowDownToLine size={14} />}
                    onClick={() => saveJson(asset, `${asset.identity.asset_id}.json`)}
                  >
                    导出单项
                  </Button>
                </div>
              </header>

              {asset.data_source === "synthetic" && asset.provenance.synthetic_disclaimer && (
                <div className="synthetic-banner">
                  <ShieldCheck size={14} />
                  <span>{asset.provenance.synthetic_disclaimer}</span>
                </div>
              )}

              <Tabs
                activeKey={detailTab}
                onChange={setDetailTab}
                size="small"
                items={[
                  {
                    key: "semantics",
                    label: "能力、物料、参数与接口",
                    children: (
                      <div className="asset-sections">
                        <section className="asset-section">
                          <h4>制造能力（Capabilities）</h4>
                          <table className="clean-table">
                            <thead>
                              <tr>
                                <th>能力编码</th>
                                <th>名称</th>
                                <th>工艺说明</th>
                                <th>定性质量维度</th>
                              </tr>
                            </thead>
                            <tbody>
                              {asset.capabilities.map((cap) => (
                                <tr key={cap.code}>
                                  <td><code>{cap.code}</code></td>
                                  <td>{cap.name}</td>
                                  <td>{cap.description}</td>
                                  <td>{cap.qualitative_features.join(", ") || "—"}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </section>

                        <section className="asset-section">
                          <h4>物料适配与状态转换（Material Compatibility）</h4>
                          <dl className="fact-grid">
                            <div>
                              <dt>适配物料类型</dt>
                              <dd>{asset.material_compatibility.supported_material_types.join("、") || "未声明"}</dd>
                            </div>
                            <div>
                              <dt>输入 / 输出物料</dt>
                              <dd>
                                {(asset.material_compatibility.input_material_types.length
                                  ? asset.material_compatibility.input_material_types
                                  : asset.material_compatibility.supported_material_types
                                ).join("、") || "—"}
                                {" → "}
                                {(asset.material_compatibility.output_material_types.length
                                  ? asset.material_compatibility.output_material_types
                                  : asset.material_compatibility.supported_material_types
                                ).join("、") || "—"}
                              </dd>
                            </div>
                            <div>
                              <dt>输入状态</dt>
                              <dd>{asset.material_compatibility.input_states.join("、") || "未声明"}</dd>
                            </div>
                            <div>
                              <dt>输出状态</dt>
                              <dd>{asset.material_compatibility.output_states.join("、") || "未声明"}</dd>
                            </div>
                          </dl>
                          {!!asset.material_compatibility.state_transitions.length && (
                            <ul className="transition-list">
                              {asset.material_compatibility.state_transitions.map((tr, i) => (
                                <li key={i}>
                                  <code>{tr.material_type ?? "*"}</code>: <span>{tr.from_state}</span> → <span>{tr.to_state}</span>
                                  {tr.condition && <small>（{tr.condition}）</small>}
                                </li>
                              ))}
                            </ul>
                          )}
                        </section>

                        <section className="asset-section">
                          <h4>技术参数（Technical Parameters）</h4>
                          {asset.parameters.length ? (
                            <table className="clean-table">
                              <thead>
                                <tr>
                                  <th>参数编码</th>
                                  <th>名称</th>
                                  <th>数值 / 范围</th>
                                  <th>单位与基准</th>
                                  <th>所属能力</th>
                                </tr>
                              </thead>
                              <tbody>
                                {asset.parameters.map((p, idx) => (
                                  <tr key={`${p.parameter_code}-${idx}`}>
                                    <td><code>{p.parameter_code}</code></td>
                                    <td>{p.name}</td>
                                    <td>
                                      {p.lower != null || p.upper != null
                                        ? `${p.value != null ? `${p.value} ` : ""}[${p.lower ?? "-∞"}, ${p.upper ?? "+∞"}]`
                                        : p.value}
                                    </td>
                                    <td>{p.unit}{p.basis ? ` / ${p.basis}` : ""}</td>
                                    <td>{p.capability_code ? <code>{p.capability_code}</code> : "资产级"}</td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          ) : (
                            <p className="muted">该资产暂未声明技术参数（匹配时涉及数值参数约束将标记为 UNKNOWN / 待确认）。</p>
                          )}
                        </section>

                        <section className="asset-section">
                          <h4>连接接口（Interfaces）</h4>
                          {asset.interfaces.length ? (
                            <table className="clean-table">
                              <thead>
                                <tr>
                                  <th>接口 ID</th>
                                  <th>类型</th>
                                  <th>方向</th>
                                  <th>兼容条件 / 信息载荷</th>
                                  <th>协议</th>
                                </tr>
                              </thead>
                              <tbody>
                                {asset.interfaces.map((iface) => (
                                  <tr key={iface.interface_id}>
                                    <td><code>{iface.interface_id}</code></td>
                                    <td>{iface.interface_type}</td>
                                    <td>{iface.direction}</td>
                                    <td>{iface.compatible_conditions.join("、") || "—"}</td>
                                    <td>{iface.protocol || "—"}</td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          ) : (
                            <p className="muted">暂未声明接口定义。</p>
                          )}
                        </section>
                      </div>
                    ),
                  },
                  {
                    key: "geometry_behavior",
                    label: "几何、行为与校验",
                    children: (
                      <div className="asset-sections">
                        <section className="asset-section">
                          <h4>几何信息（Geometry · 允许为空）</h4>
                          {asset.geometry ? (
                            <dl className="fact-grid">
                              <div>
                                <dt>包络尺寸 (mm)</dt>
                                <dd>
                                  {asset.geometry.dimensions_mm
                                    ? `${asset.geometry.dimensions_mm.length} × ${asset.geometry.dimensions_mm.width} × ${asset.geometry.dimensions_mm.height}`
                                    : "未提供"}
                                </dd>
                              </div>
                              <div>
                                <dt>坐标系 / 安装基准</dt>
                                <dd>{asset.geometry.coordinate_system || "—"} · {asset.geometry.mounting_datum || "—"}</dd>
                              </div>
                              <div>
                                <dt>连接点数量</dt>
                                <dd>{asset.geometry.connection_points.length} 个</dd>
                              </div>
                              <div>
                                <dt>几何完整度</dt>
                                <dd>{asset.geometry.completeness}</dd>
                              </div>
                            </dl>
                          ) : (
                            <p className="muted">当前无几何信息占位，后续可通过绑定三维文件补充。</p>
                          )}
                        </section>

                        <section className="asset-section">
                          <h4>行为模型（Behavior）</h4>
                          {asset.behavior ? (
                            <dl className="fact-grid">
                              <div>
                                <dt>标称加工时间</dt>
                                <dd>
                                  {asset.behavior.cycle_time_s != null
                                    ? `${asset.behavior.cycle_time_s} s${asset.behavior.cycle_time_basis ? ` / ${asset.behavior.cycle_time_basis}` : ""}`
                                    : "未声明"}
                                </dd>
                              </div>
                              <div>
                                <dt>批处理数量</dt>
                                <dd>{asset.behavior.batch_size ?? "—"}</dd>
                              </div>
                              <div>
                                <dt>运行状态</dt>
                                <dd>{asset.behavior.operating_states.join(" / ")}</dd>
                              </div>
                              <div>
                                <dt>可用率 / 行为引用</dt>
                                <dd>
                                  {asset.behavior.availability != null ? `${Math.round(asset.behavior.availability * 100)}%` : "—"}
                                  {" · "}
                                  {asset.behavior.behavior_model_ref || "无外部行为模型"}
                                </dd>
                              </div>
                            </dl>
                          ) : (
                            <p className="muted">当前未配置行为模型。</p>
                          )}
                        </section>

                        <section className="asset-section">
                          <h4>实例化策略与校验诊断</h4>
                          <dl className="fact-grid">
                            <div>
                              <dt>实例命名模板</dt>
                              <dd><code>{asset.instantiation_policy.instance_id_pattern}</code></dd>
                            </div>
                            <div>
                              <dt>校验状态</dt>
                              <dd>{validation?.valid ? "通过 Schema 与领域词表校验" : "存在校验错误"}</dd>
                            </div>
                          </dl>
                          {!!validation?.issues.length && (
                            <ul className="issue-compact-list">
                              {validation.issues.map((iss, idx) => (
                                <li key={idx} data-severity={iss.severity}>
                                  <code>{iss.path}</code> — {iss.message}
                                </li>
                              ))}
                            </ul>
                          )}
                        </section>
                      </div>
                    ),
                  },
                  {
                    key: "binding",
                    label: "三维模型绑定",
                    children: (
                      <div className="asset-sections">
                        <section className="asset-section">
                          <h4><Link2 size={15} style={{ verticalAlign: "-2px", marginRight: 6 }} />预留三维模型绑定入口</h4>
                          <p className="muted">
                            语义资产定义与真实三维文件解耦。后续导入 GLB / STEP / USD 文件时，通过稳定的 <code>asset_id</code>（<code>{asset.identity.asset_id}</code>）直接绑定即可，无需修改工艺匹配逻辑。
                          </p>
                          <div className="binding-form">
                            <label>
                              <span>绑定状态</span>
                              <Select
                                size="small"
                                value={bindingState}
                                onChange={setBindingState}
                                options={[
                                  { value: "unbound", label: "unbound · 暂未绑定" },
                                  { value: "reserved", label: "reserved · 预留三维路径" },
                                  { value: "bound", label: "bound · 已绑定三维文件" },
                                ]}
                              />
                            </label>
                            <label>
                              <span>三维模型 URI / 相对路径</span>
                              <Input
                                size="small"
                                disabled={bindingState === "unbound"}
                                value={bindingUri}
                                onChange={(e) => setBindingUri(e.target.value)}
                                placeholder="geometry/MA-FEED-01.glb"
                              />
                            </label>
                            <label>
                              <span>文件格式</span>
                              <Select
                                size="small"
                                disabled={bindingState === "unbound"}
                                value={bindingFormat}
                                onChange={setBindingFormat}
                                options={[
                                  { value: "glb", label: "GLB (.glb)" },
                                  { value: "gltf", label: "glTF (.gltf)" },
                                  { value: "step", label: "STEP (.step)" },
                                  { value: "stp", label: "STEP (.stp)" },
                                  { value: "usd", label: "USD (.usd)" },
                                  { value: "obj", label: "OBJ (.obj)" },
                                ]}
                              />
                            </label>
                            <label>
                              <span>绑定备注</span>
                              <Input
                                size="small"
                                value={bindingNotes}
                                onChange={(e) => setBindingNotes(e.target.value)}
                                placeholder="如：待接入真实三维数模版本"
                              />
                            </label>
                            <div className="binding-actions">
                              <Button size="small" type="primary" loading={bindingSaving} onClick={() => void handleSaveBinding()}>
                                保存绑定配置
                              </Button>
                              {bindingNotice && <span className="muted" style={{ margin: 0 }}>{bindingNotice}</span>}
                            </div>
                          </div>
                        </section>
                      </div>
                    ),
                  },
                  {
                    key: "json",
                    label: "JSON 定义",
                    children: (
                      <div className="asset-sections">
                        <div className="result-actions">
                          <Button
                            size="small"
                            icon={<Copy size={14} />}
                            onClick={() => void navigator.clipboard.writeText(JSON.stringify(asset, null, 2))}
                          >
                            复制 JSON
                          </Button>
                          <Button
                            size="small"
                            icon={<ArrowDownToLine size={14} />}
                            onClick={() => saveJson(asset, `${asset.identity.asset_id}.json`)}
                          >
                            下载 JSON
                          </Button>
                        </div>
                        <pre className="json-code">{JSON.stringify(asset, null, 2)}</pre>
                      </div>
                    ),
                  },
                ]}
              />
            </div>
          )}
        </section>
      </div>

      <Modal
        open={importOpen}
        title="模型资产 JSON 校验与导入"
        width={680}
        onCancel={() => setImportOpen(false)}
        footer={[
          <Button key="close" onClick={() => setImportOpen(false)}>关闭</Button>,
          <Button key="validate" icon={<CheckCircle2 size={14} />} loading={importBusy} onClick={() => void handleValidateImport()}>
            字段与词表校验
          </Button>,
          <Button key="import" type="primary" loading={importBusy} onClick={() => void handleCommitImport()}>
            确认导入资产库
          </Button>,
        ]}
      >
        <div className="import-modal-body">
          <div className="import-row">
            <label className="file-upload-inline">
              <input
                type="file"
                accept=".json,application/json"
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (!file) return;
                  void file.text().then(setImportText);
                }}
              />
              <span>选择本地 JSON 文件</span>
            </label>
            <label className="overwrite-toggle">
              <Switch size="small" checked={importOverwrite} onChange={setImportOverwrite} />
              <span>覆盖同 asset_id 已有资产</span>
            </label>
          </div>
          <Input.TextArea
            rows={10}
            value={importText}
            onChange={(e) => setImportText(e.target.value)}
            placeholder='粘贴单个 ModelAsset JSON 对象，或包含 "assets": [...] 的数组 JSON'
            style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", fontSize: 12 }}
          />
          {importNotice && <Alert type={importNotice.type} showIcon message={importNotice.text} />}
          {validationResults && (
            <div className="validation-preview">
              {validationResults.map((res, idx) => (
                <div key={idx} className="validation-preview-row">
                  <strong>{res.asset_id ?? `条目 #${idx + 1}`}</strong>
                  <span>{res.valid ? "通过" : "失败"} · 完整度 {Math.round(res.completeness_score * 100)}%</span>
                  {res.issues.map((iss, j) => (
                    <small key={j} data-severity={iss.severity}>[{iss.severity}] {iss.path}: {iss.message}</small>
                  ))}
                </div>
              ))}
            </div>
          )}
        </div>
      </Modal>

      {schemaOpen && <ContractBrowser initialKind="model_asset" onClose={() => setSchemaOpen(false)} />}
    </div>
  );
}
