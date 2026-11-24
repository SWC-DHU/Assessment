import type {
  AgentConnectionTestPayload,
  AgentConnectionTestResponse,
  AgentModelsResponse,
  AgentProfile,
  AgentProfilePayload,
  EnvStatus,
  OutputTemplate,
  OutputTemplatePayload,
  RunChatResponse,
  OntologyCatalog, PipelineStage, ProcessGraph, ReviewRequest,
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
  getProcessGraph: (sessionId: string, version: string) =>
    request<ProcessGraph>(`/api/semantics/sessions/${sessionId}/versions/${version}/graph`),
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
};
