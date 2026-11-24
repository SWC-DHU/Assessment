import type {
  AgentConnectionTestPayload,
  AgentConnectionTestResponse,
  AgentJsonProbePayload,
  AgentJsonProbeResponse,
  AgentModelsResponse,
  AgentProfile,
  AgentProfilePayload,
  EnvStatus,
  OutputTemplate,
  OutputTemplatePayload,
  RunChatResponse,
  OntologyCatalog, PipelineStage, ProcessGraph, ReviewRequest,
  ContractKind, ContractSchema, ResourceMatchingInput,
  AssetCatalogListResponse, AssetCatalogSummary, AssetCatalogItem, AssetValidationResult,
  ModelAsset, ResourceMatchingConfig, ResourceMatchingOutput, MatchingRunSummary,
  SampleDataset,
  SemanticVersion,
  SemanticModel,
  TemplatePreviewResponse,
  TestSessionDetail,
  TestSessionSummary,
  UploadedFile,
} from "../types";

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    let detail = `请求失败 (HTTP ${res.status})`;
    try {
      const data = await res.json();
      if (data?.detail) {
        detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
      }
    } catch {
      const text = await res.text().catch(() => "");
      if (text) detail = text;
    }
    throw new Error(detail);
  }
  if (res.status === 204) {
    return undefined as T;
  }
  return res.json() as Promise<T>;
}

const schemaPaths: Record<ContractKind, string> = {
  candidates: "/api/semantics/candidates/schema",
  formal: "/api/semantics/schema",
  handoff: "/api/semantics/handoff/schema",
  model_asset: "/api/model-assets/schema",
  matching_output: "/api/resource-matching/schema",
};

function versionUrl(sessionId: string, version: string) {
  return `/api/semantics/sessions/${encodeURIComponent(sessionId)}/versions/${encodeURIComponent(version)}`;
}

export const api = {
  // Samples & Env
  getSamples: () =>
    request<{ samples: SampleDataset[]; env_status: EnvStatus }>("/api/samples"),

  // Agents
  listAgents: () => request<AgentProfile[]>("/api/agents"),
  createAgent: (payload: AgentProfilePayload) =>
    request<AgentProfile>("/api/agents", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  updateAgent: (id: string, payload: Partial<AgentProfilePayload>) =>
    request<AgentProfile>(`/api/agents/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  duplicateAgent: (id: string) =>
    request<AgentProfile>(`/api/agents/${id}/duplicate`, { method: "POST" }),
  deleteAgent: (id: string) =>
    request<void>(`/api/agents/${id}`, { method: "DELETE" }),
  testAgentConnection: (payload: AgentConnectionTestPayload) =>
    request<AgentConnectionTestResponse>("/api/agents/test-connection", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  probeAgentJson: (payload: AgentJsonProbePayload) =>
    request<AgentJsonProbeResponse>("/api/agents/probe-json", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  fetchAgentModels: (payload: AgentConnectionTestPayload) =>
    request<AgentModelsResponse>("/api/agents/models", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),

  // Templates
  listTemplates: () => request<OutputTemplate[]>("/api/templates"),
  createTemplate: (payload: OutputTemplatePayload) =>
    request<OutputTemplate>("/api/templates", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  updateTemplate: (id: string, payload: Partial<OutputTemplatePayload>) =>
    request<OutputTemplate>(`/api/templates/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  deleteTemplate: (id: string) =>
    request<void>(`/api/templates/${id}`, { method: "DELETE" }),
  previewTemplate: (id: string, data: Record<string, unknown>) =>
    request<TemplatePreviewResponse>(`/api/templates/${id}/preview`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ data }),
    }),
  previewTemplateDraft: (payload: {
    template_type: "jinja2" | "table_mapping";
    format: "markdown" | "html" | "text" | "table";
    jinja_content: string;
    table_config: Record<string, unknown>;
    data: Record<string, unknown>;
  }) =>
    request<TemplatePreviewResponse>("/api/templates/preview-draft", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  exportTemplate: async (
    id: string,
    data: Record<string, unknown>,
    exportFormat: "xlsx" | "docx" | "md" | "json",
    filenamePrefix: string
  ) => {
    const res = await fetch(`/api/templates/${id}/export`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        data,
        export_format: exportFormat,
        filename_prefix: filenamePrefix,
      }),
    });
    if (!res.ok) {
      let detail = `导出失败 (HTTP ${res.status})`;
      try {
        const err = await res.json();
        if (err?.detail) detail = err.detail;
      } catch {
        // ignore
      }
      throw new Error(detail);
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${filenamePrefix || "process_output"}.${exportFormat}`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  },

  // Sessions
  listSessions: () => request<TestSessionSummary[]>("/api/sessions"),
  createSession: (payload: { title?: string; agent_id?: string | null; template_id?: string | null }) =>
    request<TestSessionDetail>("/api/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  getSession: (id: string) => request<TestSessionDetail>(`/api/sessions/${id}`),
  updateSession: (
    id: string,
    payload: {
      title?: string;
      agent_id?: string | null;
      template_id?: string | null;
      current_extracted_json?: Record<string, unknown> | null;
    }
  ) =>
    request<TestSessionDetail>(`/api/sessions/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  deleteSession: (id: string) =>
    request<void>(`/api/sessions/${id}`, { method: "DELETE" }),
  clearSession: (id: string) =>
    request<TestSessionDetail>(`/api/sessions/${id}/clear`, { method: "POST" }),

  getOntology: (version?: string) =>
    request<OntologyCatalog>(`/api/semantics/catalog${version ? `?version=${encodeURIComponent(version)}` : ""}`),
  getProcessGraph: (sessionId: string, version: string, signal?: AbortSignal) =>
    request<ProcessGraph>(`${versionUrl(sessionId, version)}/graph`, { signal }),
  getContractSchema: (kind: ContractKind, signal: AbortSignal) =>
    request<ContractSchema>(schemaPaths[kind], { signal }),
  getResourceMatchingInput: (sessionId: string, version: string, signal: AbortSignal) =>
    request<ResourceMatchingInput>(`${versionUrl(sessionId, version)}/handoff`, { signal }),
  exportResourceMatchingUrl: (sessionId: string, version: string) => `${versionUrl(sessionId, version)}/handoff/export`,
  exportSemanticVersionUrl: (sessionId: string, version: string) => `${versionUrl(sessionId, version)}/export`,
  reviewVersion: (sessionId: string, version: string, payload: ReviewRequest) =>
    request<SemanticModel>(`/api/semantics/sessions/${sessionId}/versions/${version}/review`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    }),
  getRunProgress: (sessionId: string, token: string) =>
    request<{ status: string; steps: PipelineStage[] }>(`/api/sessions/${sessionId}/runs/${token}/progress`),

  listVersions: (id: string) => request<SemanticVersion[]>(`/api/semantics/sessions/${id}/versions`),
  versionContext: (id: string, version: string) =>
    request<Record<string, unknown>>(`/api/semantics/sessions/${id}/versions/${version}/context`),
  verifyVersion: (id: string, version: string) =>
    request<SemanticModel["validation"]>(`/api/semantics/sessions/${id}/versions/${version}/validate`, { method: "POST" }),
  projectVersion: (id: string, message_id: string) =>
    request<SemanticModel>(`/api/semantics/sessions/${id}/project`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message_id }),
    }),
  templateVersions: (id: string) =>
    request<{ version: number; created_at: string; definition: OutputTemplate }[]>(`/api/templates/${id}/versions`),

  // Files
  uploadFiles: (sessionId: string, files: File[]) => {
    const formData = new FormData();
    files.forEach((f) => formData.append("files", f));
    return request<UploadedFile[]>(`/api/sessions/${sessionId}/files`, {
      method: "POST",
      body: formData,
    });
  },
  loadSampleFile: (sessionId: string, sampleId: string) =>
    request<UploadedFile>(`/api/sessions/${sessionId}/files/sample/${sampleId}`, {
      method: "POST",
    }),
  reparseFile: (sessionId: string, fileId: string) =>
    request<UploadedFile>(`/api/sessions/${sessionId}/files/${fileId}/reparse`, { method: "POST" }),
  deleteFile: (sessionId: string, fileId: string) =>
    request<void>(`/api/sessions/${sessionId}/files/${fileId}`, { method: "DELETE" }),

  cancelRun: (sessionId: string, token: string) =>
    request<{ status: string }>(`/api/sessions/${sessionId}/runs/${token}/cancel`, { method: "POST" }),

  // Run & Rerun
  runTurn: (
    sessionId: string,
    payload: {
      message: string;
      file_ids: string[];
      agent_id?: string | null;
      template_id?: string | null;
      use_demo_fallback?: boolean;
      base_version_id?: string;
      run_token?: string;
      strategy?: "semantic" | "legacy";
    },
    signal?: AbortSignal
  ) =>
    request<RunChatResponse>(`/api/sessions/${sessionId}/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal,
    }),
  rerunTurn: (
    sessionId: string,
    messageId: string,
    useDemoFallback = false,
    signal?: AbortSignal,
    runToken?: string
  ) =>
    request<RunChatResponse>(
      `/api/sessions/${sessionId}/rerun/${messageId}?use_demo_fallback=${useDemoFallback}${runToken ? `&run_token=${runToken}` : ""}`,
      { method: "POST", signal }
    ),
  resumeTurn: (sessionId: string, messageId: string, signal: AbortSignal, runToken: string) =>
    request<RunChatResponse>(
      `/api/sessions/${sessionId}/resume/${messageId}?run_token=${runToken}`,
      { method: "POST", signal },
    ),

  // Model Asset Library
  getAssetSummary: () => request<AssetCatalogSummary>("/api/model-assets/summary"),
  listAssets: (params?: {
    category?: string;
    capability?: string;
    material_type?: string;
    binding_status?: string;
    search?: string;
  }) => {
    const query = new URLSearchParams();
    if (params?.category) query.set("category", params.category);
    if (params?.capability) query.set("capability", params.capability);
    if (params?.material_type) query.set("material_type", params.material_type);
    if (params?.binding_status) query.set("binding_status", params.binding_status);
    if (params?.search) query.set("search", params.search);
    const qs = query.toString();
    return request<AssetCatalogListResponse>(`/api/model-assets${qs ? `?${qs}` : ""}`);
  },
  getAsset: (assetId: string) =>
    request<AssetCatalogItem>(`/api/model-assets/${encodeURIComponent(assetId)}`),
  validateAssetPayload: (payload: unknown) =>
    request<{ valid: boolean; count: number; results: AssetValidationResult[] }>("/api/model-assets/validate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ payload }),
    }),
  importAssets: (assets: unknown[], overwrite = false) =>
    request<{
      imported_count: number;
      imported_ids: string[];
      skipped: { index: number; asset_id: string; reason: string }[];
      failed: { index: number; asset_id: string | null; issues: unknown[] }[];
      catalog_version: string;
    }>("/api/model-assets/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ assets, overwrite }),
    }),
  updateAssetBinding: (assetId: string, binding: ModelAsset["model_binding"]) =>
    request<AssetCatalogItem & { catalog_version: string }>(
      `/api/model-assets/${encodeURIComponent(assetId)}/binding`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(binding),
      }
    ),
  exportAssetsUrl: (assetIds?: string[]) =>
    `/api/model-assets/export${assetIds?.length ? `?asset_ids=${encodeURIComponent(assetIds.join(","))}` : ""}`,

  // Resource Matching Agent
  runResourceMatching: (
    sessionId: string,
    versionId: string,
    payload: { config: Partial<ResourceMatchingConfig>; agent_id?: string | null; save?: boolean }
  ) =>
    request<ResourceMatchingOutput>(
      `/api/resource-matching/sessions/${encodeURIComponent(sessionId)}/versions/${encodeURIComponent(versionId)}/run`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      }
    ),
  listMatchingRuns: (params?: { session_id?: string; semantic_version_id?: string }) => {
    const query = new URLSearchParams();
    if (params?.session_id) query.set("session_id", params.session_id);
    if (params?.semantic_version_id) query.set("semantic_version_id", params.semantic_version_id);
    const qs = query.toString();
    return request<MatchingRunSummary[]>(`/api/resource-matching/runs${qs ? `?${qs}` : ""}`);
  },
  getMatchingRun: (runId: string) =>
    request<ResourceMatchingOutput>(`/api/resource-matching/runs/${encodeURIComponent(runId)}`),
  deleteMatchingRun: (runId: string) =>
    request<void>(`/api/resource-matching/runs/${encodeURIComponent(runId)}`, { method: "DELETE" }),
  exportMatchingRunUrl: (runId: string) =>
    `/api/resource-matching/runs/${encodeURIComponent(runId)}/export`,
};
