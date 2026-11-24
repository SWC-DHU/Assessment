import { useCallback, useEffect, useMemo, useState } from "react";
import { Alert, Button, Empty, InputNumber, Select, Switch, Tabs } from "antd";
import {
  ArrowDownToLine,
  Braces,
  CheckCircle2,
  Cpu,
  GitBranch,
  History,
  Play,
  ShieldAlert,
  Sparkles,
  XCircle,
} from "lucide-react";
import { api } from "../api/client";
import type {
  CandidateEvaluation,
  CheckVerdict,
  MatchingMethod,
  MatchingRunSummary,
  OperationMatchingResult,
  ResourceMatchingOutput,
  SemanticVersion,
  TestSessionSummary,
} from "../types";
import { ContractBrowser } from "./ContractBrowser";

interface ResourceMatchingWorkbenchProps {
  sessions: TestSessionSummary[];
  initialSessionId: string | null;
  initialVersionId: string | null;
  activeAgentId: string | null;
  onInspectAsset: (assetId: string) => void;
}

const opStatusLabels: Record<OperationMatchingResult["match_status"], string> = {
  matched_single: "单资产匹配",
  matched_partial_composable: "多模型协同",
  pending_only: "仅待确认",
  unmatched: "未匹配",
  blocked: "交接阻塞",
};

const overallStatusLabels: Record<ResourceMatchingOutput["matching_status"], string> = {
  completed: "匹配完成",
  completed_with_warnings: "匹配完成（含提示）",
  partial: "部分匹配",
  blocked: "上游阻塞",
};

const verdictLabels: Record<CheckVerdict, string> = {
  PASS: "PASS",
  FAIL: "FAIL",
  UNKNOWN: "UNKNOWN",
};

const scopeLabels: Record<string, string> = {
  capability: "制造能力",
  material: "物料适配",
  state: "状态转换",
  operation: "工序约束",
  capability_constraint: "能力约束",
  interface: "信息接口",
  equipment: "设备条件",
  line: "整线约束",
};

function saveJson(value: unknown, filename: string) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

function ScoreBar({ label, value }: { label: string; value: number }) {
  const pct = Math.round(value * 100);
  return (
    <div className="score-bar-row">
      <span>{label}</span>
      <div className="score-track">
        <div className="score-fill" style={{ width: `${pct}%` }} />
      </div>
      <b>{pct}%</b>
    </div>
  );
}

function CandidateRow({
  item,
  onInspectAsset,
}: {
  item: CandidateEvaluation;
  onInspectAsset: (assetId: string) => void;
}) {
  return (
    <article className="candidate-panel" data-disposition={item.disposition}>
      <header className="candidate-panel-head">
        <div className="candidate-identity">
          {item.rank != null && <span className="rank-badge">#{item.rank}</span>}
          <button type="button" className="id-link" onClick={() => onInspectAsset(item.asset_id)}>
            <code>{item.asset_id}</code>
          </button>
          <strong>{item.asset_name}</strong>
          <span className="cat-chip">{item.asset_category}</span>
          <span className="verdict-pill" data-verdict={item.hard_constraint_verdict}>
            硬约束 {verdictLabels[item.hard_constraint_verdict]}
          </span>
        </div>
        <div className="candidate-score-head">
          <div className="coverage-chip">能力覆盖 {Math.round(item.capability_coverage * 100)}%</div>
          <div className="total-score-badge">
            <span>综合分</span>
            <strong>{(item.score_breakdown.total_score * 100).toFixed(1)}</strong>
          </div>
        </div>
      </header>

      <div className="candidate-grid">
        <div className="candidate-bars">
          <ScoreBar label="功能适配" value={item.score_breakdown.functional_fit} />
          <ScoreBar label="物料状态" value={item.score_breakdown.material_fit} />
          <ScoreBar label="技术性能" value={item.score_breakdown.technical_performance} />
          <ScoreBar label="信息完整" value={item.score_breakdown.modeling_completeness} />
        </div>
        <div className="candidate-reasons">
          {!!item.rejection_reasons.length && (
            <div className="reason-group" data-kind="fail">
              <h5><XCircle size={13} /> 淘汰原因</h5>
              <ul>{item.rejection_reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
            </div>
          )}
          {!!item.missing_information.length && (
            <div className="reason-group" data-kind="unknown">
              <h5><ShieldAlert size={13} /> 待确认 / 缺失信息</h5>
              <ul>{item.missing_information.map((r, i) => <li key={i}>{r}</li>)}</ul>
            </div>
          )}
          {!!item.match_reasons.length && (
            <div className="reason-group" data-kind="pass">
              <h5><CheckCircle2 size={13} /> 匹配依据</h5>
              <ul>{item.match_reasons.slice(0, 4).map((r, i) => <li key={i}>{r}</li>)}</ul>
            </div>
          )}
          {item.line_takt_reference && (
            <p className="takt-advisory">{item.line_takt_reference}</p>
          )}
        </div>
      </div>

      <details className="result-disclosure">
        <summary>逐项约束校验明细（{item.constraint_checks.length} 项）</summary>
        <table className="clean-table">
          <thead>
            <tr>
              <th>维度</th>
              <th>类型</th>
              <th>判定</th>
              <th>需求要求</th>
              <th>资产表现</th>
              <th>校验依据</th>
            </tr>
          </thead>
          <tbody>
            {item.constraint_checks.map((chk, idx) => (
              <tr key={`${chk.check_id}-${idx}`}>
                <td>{scopeLabels[chk.scope] ?? chk.scope}</td>
                <td>{chk.is_hard ? "硬约束" : "软约束"}</td>
                <td><span className="verdict-pill" data-verdict={chk.verdict}>{chk.verdict}</span></td>
                <td>{chk.required_summary}</td>
                <td>{chk.asset_summary}</td>
                <td>{chk.rationale}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </article>
  );
}

export function ResourceMatchingWorkbench({
  sessions,
  initialSessionId,
  initialVersionId,
  activeAgentId,
  onInspectAsset,
}: ResourceMatchingWorkbenchProps) {
  const [sessionId, setSessionId] = useState<string | null>(initialSessionId ?? sessions[0]?.id ?? null);
  const [versions, setVersions] = useState<SemanticVersion[]>([]);
  const [versionId, setVersionId] = useState<string | null>(initialVersionId);
  const [runs, setRuns] = useState<MatchingRunSummary[]>([]);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [output, setOutput] = useState<ResourceMatchingOutput | null>(null);

  const [topK, setTopK] = useState(3);
  const [method, setMethod] = useState<MatchingMethod>("C");
  const [useLlm, setUseLlm] = useState(false);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const [selectedOpId, setSelectedOpId] = useState<string | null>(null);
  const [opTab, setOpTab] = useState("top_k");
  const [schemaOpen, setSchemaOpen] = useState(false);

  useEffect(() => {
    if (initialSessionId) setSessionId(initialSessionId);
  }, [initialSessionId]);

  useEffect(() => {
    if (initialVersionId) setVersionId(initialVersionId);
  }, [initialVersionId]);

  useEffect(() => {
    if (!sessionId && sessions.length) {
      setSessionId(sessions[0].id);
    }
  }, [sessionId, sessions]);

  const loadVersionsAndRuns = useCallback(async (sid: string) => {
    try {
      const [verList, runList] = await Promise.all([
        api.listVersions(sid),
        api.listMatchingRuns({ session_id: sid }),
      ]);
      setVersions(verList);
      setRuns(runList);
      setVersionId((prev) => {
        if (prev && verList.some((v) => v.id === prev)) return prev;
        return verList[0]?.id ?? null;
      });
      if (runList.length && !activeRunId) {
        const latest = await api.getMatchingRun(runList[0].run_id);
        setOutput(latest);
        setActiveRunId(latest.run_id);
        setSelectedOpId(latest.operation_matches[0]?.operation_id ?? null);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [activeRunId]);

  useEffect(() => {
    if (sessionId) {
      void loadVersionsAndRuns(sessionId);
    }
  }, [sessionId, loadVersionsAndRuns]);

  const handleRunMatching = async () => {
    if (!sessionId || !versionId) return;
    setRunning(true);
    setError("");
    try {
      const result = await api.runResourceMatching(sessionId, versionId, {
        config: {
          top_k: topK,
          partial_top_k: Math.max(3, topK),
          method,
          use_llm: method === "C" ? useLlm : false,
        },
        agent_id: activeAgentId,
        save: true,
      });
      setOutput(result);
      setActiveRunId(result.run_id);
      setSelectedOpId(result.operation_matches[0]?.operation_id ?? null);
      setOpTab("top_k");
      const updatedRuns = await api.listMatchingRuns({ session_id: sessionId });
      setRuns(updatedRuns);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRunning(false);
    }
  };

  const handleLoadRun = async (runId: string) => {
    setError("");
    try {
      const loaded = await api.getMatchingRun(runId);
      setOutput(loaded);
      setActiveRunId(loaded.run_id);
      setSelectedOpId(loaded.operation_matches[0]?.operation_id ?? null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const selectedOp = useMemo(
    () => output?.operation_matches.find((op) => op.operation_id === selectedOpId) ?? output?.operation_matches[0] ?? null,
    [output, selectedOpId]
  );
  const blockerDiagnostics = useMemo(
    () => (output?.diagnostics ?? []).filter((d) => d.severity === "blocker"),
    [output]
  );

  return (
    <div className="module-shell">
      <div className="module-toolbar">
        <div className="module-title-group">
          <div className="module-heading">
            <Cpu size={18} />
            <h2>资源匹配智能体工作台</h2>
            {output && (
              <span className="status-badge" data-status={output.matching_status}>
                {overallStatusLabels[output.matching_status]}
              </span>
            )}
          </div>
          <p className="module-subtitle">
            直接消费需求解析智能体输出的 <code>ResourceMatchingInput</code>，经共享本体检索、三值硬约束校验与综合排序输出各工序 Top-k 候选模型。
          </p>
        </div>
        <div className="module-toolbar-actions">
          <Button size="small" icon={<Braces size={14} />} onClick={() => setSchemaOpen(true)}>
            查看输出 Schema
          </Button>
          {output && (
            <Button
              size="small"
              icon={<ArrowDownToLine size={14} />}
              onClick={() => saveJson(output, `resource-matching-output-${output.run_id.slice(0, 8)}.json`)}
            >
              导出匹配 JSON
            </Button>
          )}
        </div>
      </div>

      <div className="control-bar">
        <div className="control-field">
          <span>需求会话</span>
          <Select
            size="small"
            aria-label="选择需求会话"
            value={sessionId ?? undefined}
            onChange={(val) => {
              setSessionId(val);
              setActiveRunId(null);
              setOutput(null);
            }}
            style={{ width: 180 }}
            options={sessions.map((s) => ({ value: s.id, label: s.title }))}
          />
        </div>
        <div className="control-field">
          <span>语义版本</span>
          <Select
            size="small"
            aria-label="选择语义版本"
            value={versionId ?? undefined}
            onChange={setVersionId}
            style={{ width: 215 }}
            placeholder="当前会话无语义版本"
            options={versions.map((v) => ({
              value: v.id,
              label: `${new Date(v.created_at).toLocaleString("zh-CN")} · ${v.id.slice(0, 6)}`,
            }))}
          />
        </div>
        <div className="control-field">
          <span>Top-k</span>
          <InputNumber
            size="small"
            aria-label="Top-k 候选数量"
            min={1}
            max={10}
            value={topK}
            onChange={(val) => setTopK(val ?? 3)}
            style={{ width: 64 }}
          />
        </div>
        <div className="control-field">
          <span>匹配方法</span>
          <Select
            size="small"
            aria-label="选择匹配方法"
            value={method}
            onChange={setMethod}
            style={{ width: 185 }}
            options={[
              { value: "C", label: "C · 本体语义+多约束推理" },
              { value: "A", label: "A · 关键词匹配基线" },
              { value: "B", label: "B · 直接 LLM 匹配基线" },
            ]}
          />
        </div>
        {method === "C" && (
          <label className="control-toggle">
            <Switch size="small" checked={useLlm} onChange={setUseLlm} />
            <span><Sparkles size={12} style={{ verticalAlign: "-1px" }} /> LLM 解释</span>
          </label>
        )}
        <Button
          size="small"
          type="primary"
          icon={<Play size={14} />}
          loading={running}
          disabled={!sessionId || !versionId}
          onClick={() => void handleRunMatching()}
        >
          启动资源匹配
        </Button>

        {!!runs.length && (
          <div className="control-field control-history">
            <span><History size={13} /> 历史记录</span>
            <Select
              size="small"
              aria-label="选择历史匹配记录"
              value={activeRunId ?? undefined}
              onChange={(val) => void handleLoadRun(val)}
              style={{ width: 210 }}
              options={runs.map((r) => ({
                value: r.run_id,
                label: `方法${r.method} · Top-${r.top_k} · ${new Date(r.created_at).toLocaleTimeString("zh-CN")}`,
              }))}
            />
          </div>
        )}
      </div>

      {error && <Alert type="error" showIcon message="资源匹配执行异常" description={error} style={{ margin: "0 20px 10px" }} />}

      {!output ? (
        <div className="matching-empty">
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={
              versions.length
                ? "点击上方「启动资源匹配」消费所选需求版本的交接契约并生成候选模型集合"
                : "所选会话尚未生成正式语义版本，请先在「需求解析」工作台完成需求抽取"
            }
          />
        </div>
      ) : (
        <>
          <div className="matching-summary-strip">
            <div className="kpi-chip"><span>工序总数</span><strong>{output.summary.total_operations}</strong></div>
            <div className="kpi-chip" data-tone="pass"><span>单资产覆盖</span><strong>{output.summary.matched_single_count}</strong></div>
            <div className="kpi-chip" data-tone="accent"><span>多模型协同</span><strong>{output.summary.matched_partial_count}</strong></div>
            <div className="kpi-chip" data-tone="warn"><span>仅待确认</span><strong>{output.summary.pending_only_count}</strong></div>
            <div className="kpi-chip" data-tone="fail"><span>未匹配/阻塞</span><strong>{output.summary.unmatched_count + output.summary.blocked_count}</strong></div>
            <div className="kpi-chip"><span>Top-1 能力覆盖</span><strong>{Math.round(output.summary.average_top1_capability_coverage * 100)}%</strong></div>
            <div className="kpi-chip"><span>运行耗时</span><strong>{output.summary.total_duration_ms} ms</strong></div>
            <div className="kpi-meta">
              <span>方法 {output.config.method}</span>
              <span>资产库 {output.catalog_version}</span>
              <span>上游交接 {output.source_handoff_status}</span>
            </div>
          </div>

          {!!blockerDiagnostics.length && (
            <div className="matching-blocker-banner">
              <Alert
                type="error"
                showIcon
                message="上游需求交接存在阻塞项，资源匹配已暂停"
                description={
                  <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
                    {blockerDiagnostics.map((d, i) => (
                      <li key={`${d.code}-${i}`}>{d.message}</li>
                    ))}
                  </ul>
                }
              />
            </div>
          )}

          <details className="topology-context-bar">
            <summary>
              <GitBranch size={14} />
              <span>工艺拓扑（{output.process_graph.nodes.length} 工序 / {output.process_graph.edges.length} 边）、整线约束（{output.line_constraints.length}）与执行步骤（{output.tool_trace.length}）</span>
            </summary>
            <div className="topology-context-body">
              <div className="topo-flow-nodes">
                {output.process_graph.nodes.map((node) => {
                  const match = output.operation_matches.find((m) => m.operation_id === node.id);
                  return (
                    <button
                      type="button"
                      key={node.id}
                      className="topo-pill"
                      data-active={node.id === selectedOp?.operation_id}
                      data-status={match?.match_status ?? "unmatched"}
                      onClick={() => setSelectedOpId(node.id)}
                    >
                      <span>{node.number ?? "•"}</span>
                      <strong>{node.name}</strong>
                      {match?.top_k_candidates[0] && <code>{match.top_k_candidates[0].asset_id}</code>}
                    </button>
                  );
                })}
              </div>
              {!!output.line_constraints.length && (
                <div className="line-constraint-bar">
                  <strong>整线级约束引用：</strong>
                  {output.line_constraints.map((lc) => (
                    <span key={lc.constraint_id} className="line-constraint-chip">
                      {lc.name} {lc.comparator} {lc.value} {lc.unit}{lc.basis ? `/${lc.basis}` : ""}
                      <small>（交由下游关联建模与产线平衡校验，不作为单机硬淘汰）</small>
                    </span>
                  ))}
                </div>
              )}
              <ol className="trace-strip">
                {output.tool_trace.map((step, i) => (
                  <li key={i}>
                    <strong>{step.tool}</strong> — {step.summary} <small>({step.duration_ms} ms)</small>
                  </li>
                ))}
              </ol>
            </div>
          </details>

          <div className="split-workspace">
            <aside className="split-list-pane" aria-label="工序匹配列表">
              <ul className="entity-list-rail">
                {output.operation_matches.map((op) => {
                  const active = op.operation_id === selectedOp?.operation_id;
                  const top1 = op.top_k_candidates[0];
                  return (
                    <li key={op.operation_id}>
                      <button
                        type="button"
                        className="rail-row"
                        data-active={active}
                        onClick={() => setSelectedOpId(op.operation_id)}
                      >
                        <div className="rail-row-top">
                          <code className="id-chip">{op.operation_number ?? op.operation_id}</code>
                          <span className="op-status-pill" data-status={op.match_status}>
                            {opStatusLabels[op.match_status]}
                          </span>
                          {op.pending_candidates.length > 0 && (
                            <span className="pending-count-pill">待确认 {op.pending_candidates.length}</span>
                          )}
                        </div>
                        <strong className="rail-row-title" title={op.operation_name}>{op.operation_name}</strong>
                        <div className="rail-row-meta">
                          <span className="cap-inline" title={op.required_capability_codes.join(" · ") || "无能力"}>
                            {op.required_capability_codes.join(" · ") || "无能力"}
                          </span>
                          <span className="completeness-mini">
                            {top1
                              ? `${top1.asset_id} · ${(top1.score_breakdown.total_score * 100).toFixed(0)}分`
                              : op.match_status === "blocked"
                                ? "已阻塞"
                                : op.match_status === "matched_partial_composable"
                                  ? "可协同"
                                  : "—"}
                          </span>
                        </div>
                      </button>
                    </li>
                  );
                })}
              </ul>
            </aside>

            <section className="split-detail-pane" aria-label="工序需求与候选模型详情">
              {selectedOp && (
                <div className="detail-content">
                  <header className="op-req-header">
                    <div>
                      <div className="detail-badges">
                        <code className="id-chip">{selectedOp.operation_number ?? selectedOp.operation_id}</code>
                        {selectedOp.operation_type && <span className="cat-chip">{selectedOp.operation_type}</span>}
                        <span className="op-status-pill" data-status={selectedOp.match_status}>
                          {opStatusLabels[selectedOp.match_status]}
                        </span>
                        {selectedOp.multi_asset_recommended && (
                          <span className="status-badge" data-status="completed_with_warnings">建议多模型协同</span>
                        )}
                      </div>
                      <h3>{selectedOp.operation_name}</h3>
                    </div>
                  </header>

                  <dl className="req-align-grid">
                    <div>
                      <dt>所需制造能力编码</dt>
                      <dd>{selectedOp.required_capability_codes.map((c) => <code key={c} className="cap-badge">{c}</code>)}</dd>
                    </div>
                    <div>
                      <dt>输入 → 输出物料</dt>
                      <dd>{selectedOp.input_materials.join("、") || "—"} → {selectedOp.output_materials.join("、") || "—"}</dd>
                    </div>
                    <div>
                      <dt>输入 → 输出状态</dt>
                      <dd>{selectedOp.input_states.join("、") || "未限定"} → {selectedOp.output_states.join("、") || "未限定"}</dd>
                    </div>
                    <div>
                      <dt>信息流 / 设备条件</dt>
                      <dd>
                        {selectedOp.information_flows.join("、") || "无信息流"}
                        {selectedOp.equipment_requirement ? ` · 设备：${selectedOp.equipment_requirement}` : ""}
                      </dd>
                    </div>
                  </dl>

                  {selectedOp.llm_explanation && (
                    <div className="synthetic-banner">
                      <Sparkles size={14} />
                      <span><strong>LLM 匹配说明：</strong>{selectedOp.llm_explanation}</span>
                    </div>
                  )}

                  {!!selectedOp.unmet_requirements.length && (
                    <Alert
                      type="warning"
                      showIcon
                      message="未满足需求诊断"
                      description={selectedOp.unmet_requirements.map((u) => u.message).join("；")}
                    />
                  )}

                  <Tabs
                    activeKey={opTab}
                    onChange={setOpTab}
                    size="small"
                    items={[
                      {
                        key: "top_k",
                        label: `Top-k 合格候选 (${selectedOp.top_k_candidates.length})`,
                        children: selectedOp.top_k_candidates.length ? (
                          <div className="candidate-stack">
                            {selectedOp.top_k_candidates.map((cand) => (
                              <CandidateRow key={cand.asset_id} item={cand} onInspectAsset={onInspectAsset} />
                            ))}
                          </div>
                        ) : (
                          <Empty
                            image={Empty.PRESENTED_IMAGE_SIMPLE}
                            description={
                              selectedOp.match_status === "blocked"
                                ? selectedOp.diagnostics[0] || "当前语义版本在需求交接检查中存在阻塞项，请先切换到就绪版本或在需求解析工作台完成人工审核"
                                : "当前无同时覆盖全部能力并通过所有硬约束的单模型候选，请查看「按能力拆分」或「待确认资源」"
                            }
                          />
                        ),
                      },
                      {
                        key: "partial",
                        label: `按能力拆分 (${selectedOp.partial_candidates_by_capability.length})`,
                        children: selectedOp.partial_candidates_by_capability.length ? (
                          <div className="partial-groups">
                            {selectedOp.partial_candidates_by_capability.map((grp) => (
                              <section key={grp.capability_code} className="partial-cap-block">
                                <h4>
                                  <code>{grp.capability_code}</code>
                                  <span>{grp.capability_name ?? ""}</span>
                                  <small>{grp.candidates.length} 项合格 · {grp.pending_candidates.length} 项待确认</small>
                                </h4>
                                {grp.candidates.length ? (
                                  <div className="candidate-stack">
                                    {grp.candidates.map((cand) => (
                                      <CandidateRow key={`${grp.capability_code}-${cand.asset_id}`} item={cand} onInspectAsset={onInspectAsset} />
                                    ))}
                                  </div>
                                ) : (
                                  <p className="muted">该子能力暂无合格候选资产。</p>
                                )}
                              </section>
                            ))}
                          </div>
                        ) : (
                          <p className="muted">当前单能力工序已由完整候选覆盖，无需按能力拆分。</p>
                        ),
                      },
                      {
                        key: "pending",
                        label: `待确认资源 (${selectedOp.pending_candidates.length})`,
                        children: selectedOp.pending_candidates.length ? (
                          <div className="candidate-stack">
                            {selectedOp.pending_candidates.map((cand) => (
                              <CandidateRow key={cand.asset_id} item={cand} onInspectAsset={onInspectAsset} />
                            ))}
                          </div>
                        ) : (
                          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="该工序无因信息缺失进入待确认集合的资产" />
                        ),
                      },
                      {
                        key: "rejected",
                        label: `淘汰与原因 (${selectedOp.rejected_candidates.length})`,
                        children: selectedOp.rejected_candidates.length ? (
                          <div className="candidate-stack">
                            {selectedOp.rejected_candidates.slice(0, 12).map((cand) => (
                              <CandidateRow key={cand.asset_id} item={cand} onInspectAsset={onInspectAsset} />
                            ))}
                          </div>
                        ) : (
                          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="该工序检索池中无淘汰记录" />
                        ),
                      },
                      {
                        key: "json",
                        label: "下游输出 JSON",
                        children: (
                          <pre className="json-code">{JSON.stringify(output, null, 2)}</pre>
                        ),
                      },
                    ]}
                  />
                </div>
              )}
            </section>
          </div>
        </>
      )}

      {schemaOpen && <ContractBrowser initialKind="matching_output" onClose={() => setSchemaOpen(false)} />}
    </div>
  );
}
