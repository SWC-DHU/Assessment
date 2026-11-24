import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, App as AntdApp, Modal } from "antd";
import { Files, Layers3, MessageSquare } from "lucide-react";
import { api } from "./api/client";
import { AgentManagerModal } from "./components/AgentManagerModal";
import { ChatWorkbench, type RunStage } from "./components/ChatWorkbench";
import { FilePreviewModal } from "./components/FilePreviewModal";
import { LeftSidebar } from "./components/LeftSidebar";
import { ModelAssetLibrary } from "./components/ModelAssetLibrary";
import { ResourceMatchingWorkbench } from "./components/ResourceMatchingWorkbench";
import { RightPanel, type ResultTab } from "./components/RightPanel";
import { TemplateStudioModal } from "./components/TemplateStudioModal";
import { TopBar, type WorkspaceMode } from "./components/TopBar";
import type {
  AgentConnectionTestResponse,
  AgentProfile,
  AgentProfilePayload,
  AgentRoleSpec,
  AgentType,
  EnvStatus,
  Evidence,
  OutputTemplate,
  OutputTemplatePayload,
  SampleDataset,
  PipelineStage, ReviewRequest,
  SemanticVersion,
  TemplatePreviewResponse,
  TestSessionDetail,
  TestSessionSummary,
  UploadedFile,
} from "./types";

function SourceMessage({ text, cite }: { text: string; cite?: Evidence }) {
  const start = cite?.location.start;
  const end = cite?.location.end;
  const characters = Array.from(text);
  const quote = start == null || end == null ? null : characters.slice(start, end).join("");
  return <pre className="message-source-text">{
    cite && start != null && end != null && quote === cite.quote
      ? <>{characters.slice(0, start).join("")}<mark>{quote}</mark>{characters.slice(end).join("")}</>
      : text
  }</pre>;
}

export default function App() {
  const { message: antdMessage } = AntdApp.useApp();

  const [agents, setAgents] = useState<AgentProfile[]>([]);
  const [roleSpecs, setRoleSpecs] = useState<AgentRoleSpec[]>([]);
  const [activeMatchingAgentId, setActiveMatchingAgentId] = useState<string | null>(() => {
    try {
      return localStorage.getItem("assessment.activeMatchingAgentId");
    } catch {
      return null;
    }
  });
  const [agentModalRole, setAgentModalRole] = useState<AgentType>("requirement_parsing");
  const [templates, setTemplates] = useState<OutputTemplate[]>([]);
  const [sessions, setSessions] = useState<TestSessionSummary[]>([]);
  const [activeSession, setActiveSession] = useState<TestSessionDetail | null>(null);
  const [samples, setSamples] = useState<SampleDataset[]>([]);
  const [envStatus, setEnvStatus] = useState<EnvStatus | null>(null);

  const [templatePreview, setTemplatePreview] = useState<TemplatePreviewResponse | null>(null);
  const [selectedDebugMessageId, setSelectedDebugMessageId] = useState<string | null>(null);

  const [stage, setStage] = useState<RunStage>("idle");
  const [isRunning, setIsRunning] = useState(false);
  const [progress, setProgress] = useState<PipelineStage[]>([]);
  const [demoMode, setDemoMode] = useState(false);
  const [strategy, setStrategy] = useState<"semantic" | "legacy">("semantic");
  const [workspaceMode, setWorkspaceMode] = useState<WorkspaceMode>("requirements");
  const [inspectedAssetId, setInspectedAssetId] = useState<string | null>(null);
  const [matchingTarget, setMatchingTarget] = useState<{ sessionId: string | null; versionId: string | null }>({
    sessionId: null,
    versionId: null,
  });
  const [area, setArea] = useState("chat");
  const [tab, setTab] = useState<ResultTab>("overview");
  const [resultWidth, setResultWidth] = useState(43);
  const [versions, setVersions] = useState<SemanticVersion[]>([]);
  const [inspected, setInspected] = useState<Record<string, unknown> | null>(null);
  const [revisionBase, setRevisionBase] = useState<string>();
  const sessionTarget = useRef<string | null>(null);
  const previewRequest = useRef(0);
  const inspectionRequest = useRef(0);

  const [leftCollapsed, setLeftCollapsed] = useState(false);
  const [rightCollapsed, setRightCollapsed] = useState(false);
  const [previewFile, setPreviewFile] = useState<UploadedFile | null>(null);
  const [sourceEvidence, setSourceEvidence] = useState<Evidence>();
  const [messageSource, setMessageSource] = useState<{ text: string; cite?: Evidence }>();
  const [agentModalOpen, setAgentModalOpen] = useState(false);
  const [templateModalOpen, setTemplateModalOpen] = useState(false);
  const [bannerError, setBannerError] = useState<string | null>(null);
  const [agentConnStatuses, setAgentConnStatuses] = useState<
    Record<string, AgentConnectionTestResponse>
  >({});

  const abortRef = useRef<AbortController | null>(null);
  const runningToken = useRef<{ sessionId: string; token: string } | null>(null);

  useEffect(() => {
    const run = runningToken.current;
    if (!isRunning || !run) {
      setProgress([]);
      return;
    }
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const result = await api.getRunProgress(run.sessionId, run.token);
        if (active && result.steps.length) setProgress(result.steps);
      } catch {
        // The run response remains authoritative if polling is temporarily unavailable.
      } finally {
        if (active) timer = setTimeout(() => { void poll(); }, 600);
      }
    };
    void poll();
    return () => { active = false; clearTimeout(timer); };
  }, [isRunning]);


  const showError = useCallback((err: unknown) => {
    const msg = err instanceof Error ? err.message : String(err);
    setBannerError(msg);
  }, []);

  const refreshTemplatePreview = useCallback(
    async (templateId: string | null | undefined, data: Record<string, unknown> | null | undefined) => {
      const requestId = ++previewRequest.current;
      setTemplatePreview(null);
      if (!templateId || !data) {
        setTemplatePreview(null);
        return;
      }
      try {
        const res = await api.previewTemplate(templateId, data);
        if (requestId === previewRequest.current) setTemplatePreview(res);
      } catch (err) {
        if (requestId === previewRequest.current) showError(err);
      }
    },
    [showError]
  );

  const loadSessionDetail = useCallback(
    async (sessionId: string) => {
      sessionTarget.current = sessionId;
      const [detail, resultVersions] = await Promise.all([api.getSession(sessionId), api.listVersions(sessionId)]);
      if (sessionTarget.current !== sessionId) return;
      setActiveSession(detail);
      setVersions(resultVersions);
      setInspected(null);
      const lastAssistant = [...detail.messages]
        .reverse()
        .find((m) => m.role === "assistant" && m.debug_info);
      setSelectedDebugMessageId(lastAssistant?.id ?? null);
      await refreshTemplatePreview(detail.template_id, detail.current_extracted_json);
    },
    [refreshTemplatePreview]
  );

  useEffect(() => {
    let mounted = true;
    (async () => {
      try {
        const [agentList, rolesData, tmplList, sessionList, sampleData] = await Promise.all([
          api.listAgents(),
          api.listAgentRoles().catch(() => []),
          api.listTemplates(),
          api.listSessions(),
          api.getSamples(),
        ]);
        if (!mounted) return;
        setAgents(agentList);
        setRoleSpecs(rolesData);
        setTemplates(tmplList);
        setSamples(sampleData.samples);
        setEnvStatus(sampleData.env_status);

        const reqAgents = agentList.filter((a) => (a.agent_type || "requirement_parsing") === "requirement_parsing");
        const matchAgents = agentList.filter((a) => a.agent_type === "resource_matching");
        setActiveMatchingAgentId((prev) =>
          prev && matchAgents.some((a) => a.id === prev) ? prev : matchAgents[0]?.id ?? null
        );

        if (sessionList.length > 0) {
          setSessions(sessionList);
          await loadSessionDetail(sessionList[0].id);
        } else {
          const created = await api.createSession({
            title: "新建工艺解析测试",
            agent_id: reqAgents[0]?.id ?? null,
            template_id: tmplList[0]?.id ?? null,
          });
          if (!mounted) return;
          setSessions([created]);
          sessionTarget.current = created.id;
          setActiveSession(created);
        }
      } catch (err) {
        if (mounted) showError(err);
      }
    })();
    return () => {
      mounted = false;
    };
  }, [loadSessionDetail, showError]);

  const requirementAgents = agents.filter((a) => (a.agent_type || "requirement_parsing") === "requirement_parsing");
  const matchingAgents = agents.filter((a) => a.agent_type === "resource_matching");
  const activeRequirementAgentId =
    (activeSession?.agent_id && requirementAgents.some((a) => a.id === activeSession.agent_id)
      ? activeSession.agent_id
      : requirementAgents[0]?.id) || null;
  const resolvedMatchingAgentId =
    (activeMatchingAgentId && matchingAgents.some((a) => a.id === activeMatchingAgentId)
      ? activeMatchingAgentId
      : matchingAgents[0]?.id) || null;
  const activeMatchingAgent = matchingAgents.find((a) => a.id === resolvedMatchingAgentId) || null;
  const activeAgentId = activeRequirementAgentId;
  const activeTemplateId = activeSession?.template_id || templates[0]?.id || null;

  const refreshSessionsList = async () => {
    const list = await api.listSessions();
    setSessions(list);
  };

  const handleSelectRequirementAgent = async (agentId: string) => {
    if (!activeSession) return;
    try {
      const updated = await api.updateSession(activeSession.id, { agent_id: agentId });
      setActiveSession(updated);
    } catch (err) {
      showError(err);
    }
  };

  const handleSelectMatchingAgent = (agentId: string) => {
    setActiveMatchingAgentId(agentId);
    try {
      localStorage.setItem("assessment.activeMatchingAgentId", agentId);
    } catch {
      // ignore storage errors
    }
  };

  const openAgentManagerFor = (role: AgentType) => {
    setAgentModalRole(role);
    setAgentModalOpen(true);
  };

  const handleSelectTemplate = async (templateId: string) => {
    if (!activeSession) return;
    try {
      const updated = await api.updateSession(activeSession.id, { template_id: templateId });
      setActiveSession(updated);
      await refreshTemplatePreview(templateId, inspected ?? updated.current_extracted_json);
    } catch (err) {
      showError(err);
    }
  };

  const handleCreateSession = async () => {
    try {
      const created = await api.createSession({
        title: "新建工艺解析测试",
        agent_id: activeAgentId,
        template_id: activeTemplateId,
      });
      sessionTarget.current = created.id;
      setActiveSession(created);
      setVersions([]);
      setInspected(null);
      setRevisionBase(undefined);
      setArea("chat");
      setTemplatePreview(null);
      setSelectedDebugMessageId(null);
      setStage("idle");
      await refreshSessionsList();
      antdMessage.success("已新建测试会话");
    } catch (err) {
      showError(err);
    }
  };

  const handleSelectSession = async (id: string) => {
    try {
      if (isRunning) return;
      setStage("idle");
      setRevisionBase(undefined);
      setArea("chat");
      await loadSessionDetail(id);
    } catch (err) {
      showError(err);
    }
  };

  const handleDeleteSession = async (id: string) => {
    if (isRunning) return;
    try {
      await api.deleteSession(id);
      const list = await api.listSessions();
      setSessions(list);
      if (activeSession?.id === id) {
        if (list.length > 0) {
          await loadSessionDetail(list[0].id);
        } else {
          await handleCreateSession();
        }
      }
      antdMessage.success("已删除会话");
    } catch (err) {
      showError(err);
    }
  };

  const handleUploadFiles = async (files: File[]): Promise<UploadedFile[]> => {
    if (!activeSession) return [];
    try {
      setStage("parsing");
      const uploaded = await api.uploadFiles(activeSession.id, files);
      const detail = await api.getSession(activeSession.id);
      setActiveSession((current) => current?.id === detail.id ? detail : current);
      setStage("idle");
      const failures = uploaded.filter((file) => file.parse_status === "error").length;
      if (failures) antdMessage.warning(`上传 ${uploaded.length} 份文件，${failures} 份解析失败`);
      else antdMessage.success(`已加入 ${uploaded.length} 份可解析资料`);
      return uploaded;
    } catch (err) {
      setStage("idle");
      showError(err);
      return [];
    }
  };

  const handleLoadSampleCsv = async (sample: SampleDataset) => {
    if (!activeSession) return;
    try {
      setStage("parsing");
      const uploaded = await api.loadSampleFile(activeSession.id, sample.id);
      const detail = await api.getSession(activeSession.id);
      setActiveSession(detail);
      setStage("idle");
      setPreviewFile(uploaded);
      antdMessage.success("已生成样例 CSV 并完成解析");
    } catch (err) {
      setStage("idle");
      showError(err);
    }
  };

  const handleDeleteFile = async (fileId: string) => {
    if (!activeSession) return;
    try {
      await api.deleteFile(activeSession.id, fileId);
      const detail = await api.getSession(activeSession.id);
      setActiveSession(detail);
      antdMessage.success("已移除文件");
    } catch (err) {
      showError(err);
    }
  };

  const handleSendMessage = async (text: string, attachedFileIds: string[]) => {
    if (!activeSession || abortRef.current) return;
    setBannerError(null);
    const controller = new AbortController();
    abortRef.current = controller;
    const token = crypto.randomUUID();
    runningToken.current = { sessionId: activeSession.id, token };
    setIsRunning(true);
    setStage("extracting");

    try {
      const res = await api.runTurn(
        activeSession.id,
        {
          message: text,
          file_ids: attachedFileIds,
          agent_id: activeAgentId,
          template_id: activeTemplateId,
          use_demo_fallback: demoMode,
          strategy,
          base_version_id: revisionBase,
          run_token: token,
        },
        controller.signal
      );

      setRevisionBase(undefined);
      await loadSessionDetail(activeSession.id);
      await refreshSessionsList();
      setSelectedDebugMessageId(res.assistant_message.id);
      if (res.template_preview) {
        setTemplatePreview(res.template_preview);
      }
      setStage("done");
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") {
        antdMessage.info("已中止本次请求");
      } else {
        showError(err);
      }
      setStage("idle");
    } finally {
      setIsRunning(false);
      abortRef.current = null;
      runningToken.current = null;
    }
  };

  const handleReview = async (version: string, payload: ReviewRequest) => {
    const session = activeSession;
    if (!session || isRunning) throw new Error("请等待当前解析运行结束");
    const result = await api.reviewVersion(session.id, version, payload);
    await loadSessionDetail(session.id);
    setSelectedDebugMessageId(result.run_id);
    await refreshSessionsList();
    antdMessage.success("人工审核已保存到新版本");
  };

  const handleAbortRun = () => {
    const running = runningToken.current;
    if (running) void api.cancelRun(running.sessionId, running.token).catch(showError);
    abortRef.current?.abort();
  };

  const handleRerunMessage = async (messageId: string, resume = false) => {
    if (!activeSession || abortRef.current) return;
    setBannerError(null);
    const controller = new AbortController();
    abortRef.current = controller;
    const token = crypto.randomUUID();
    runningToken.current = { sessionId: activeSession.id, token };
    setIsRunning(true);
    setStage("extracting");

    try {
      const res = resume
        ? await api.resumeTurn(activeSession.id, messageId, controller.signal, token)
        : await api.rerunTurn(activeSession.id, messageId, demoMode, controller.signal, token);
      await loadSessionDetail(activeSession.id);
      await refreshSessionsList();
      setSelectedDebugMessageId(res.assistant_message.id);
      if (res.template_preview) {
        setTemplatePreview(res.template_preview);
      }
      if (resume && res.assistant_message.debug_info?.activation_note) {
        antdMessage.info(res.assistant_message.debug_info.activation_note);
      }
      setStage("done");
    } catch (err) {
      if (!(err instanceof DOMException && err.name === "AbortError")) {
        showError(err);
      }
      setStage("idle");
    } finally {
      setIsRunning(false);
      abortRef.current = null;
      runningToken.current = null;
    }
  };

  const handleClearSession = async () => {
    if (!activeSession) return;
    try {
      const updated = await api.clearSession(activeSession.id);
      setActiveSession(updated);
      setTemplatePreview(null);
      setInspected(null);
      setRevisionBase(undefined);
      setSelectedDebugMessageId(null);
      setStage("idle");
      antdMessage.success("会话已清空");
    } catch (err) {
      showError(err);
    }
  };

  const handleApplyJsonEdit = async (newJson: Record<string, unknown>) => {
    if (!activeSession) return;
    try {
      const updated = await api.updateSession(activeSession.id, {
        current_extracted_json: newJson,
      });
      setActiveSession(updated);
      await refreshTemplatePreview(activeTemplateId, newJson);
      antdMessage.success("已应用 JSON 微调并刷新模板填充");
    } catch (err) {
      showError(err);
    }
  };

  const visibleData = inspected ?? activeSession?.current_extracted_json ?? null;

  const handleInspect = async (version: string | null) => {
    if (!activeSession) return;
    const sequence = ++inspectionRequest.current;
    try {
      const context = version ? await api.versionContext(activeSession.id, version) : null;
      if (sequence !== inspectionRequest.current || sessionTarget.current !== activeSession.id) return;
      setInspected(context);
      if (version) setSelectedDebugMessageId(version);
      await refreshTemplatePreview(activeTemplateId, context ?? activeSession.current_extracted_json);
    } catch (err) { showError(err); }
  };

  const handleProject = async () => {
    if (!activeSession || !debugMessage) return;
    try {
      const model = await api.projectVersion(activeSession.id, debugMessage.id);
      setVersions(await api.listVersions(activeSession.id));
      await handleInspect(model.run_id);
    } catch (err) { showError(err); }
  };

  const handleReparse = async (id: string) => {
    if (!activeSession) return;
    try {
      const updated = await api.reparseFile(activeSession.id, id);
      setActiveSession((value) => value?.id === activeSession.id
        ? { ...value, files: value.files.map((file) => file.id === id ? updated : file) } : value);
      if (updated.parse_error) antdMessage.error(updated.parse_error);
      else antdMessage.success("重新解析完成");
    } catch (err) { showError(err); }
  };

  const openResults = (next: ResultTab) => {
    setTab(next);
    setRightCollapsed(false);
    setArea("results");
  };

  const handleExport = async (format: "xlsx" | "docx" | "md") => {
    if (!activeSession || !visibleData || !activeTemplateId) return;
    try {
      const prefix =
        String(visibleData.product_name || "") ||
        activeSession.title ||
        "process_output";
      await api.exportTemplate(
        activeTemplateId,
        visibleData,
        format,
        prefix
      );
      antdMessage.success(`已导出 .${format} 文件`);
    } catch (err) {
      showError(err);
    }
  };

  const handleCreateAgent = async (payload: AgentProfilePayload) => {
    const created = await api.createAgent(payload);
    setAgents((prev) => [...prev, created]);
    return created;
  };
  const handleUpdateAgent = async (id: string, payload: Partial<AgentProfilePayload>) => {
    const updated = await api.updateAgent(id, payload);
    setAgents((prev) => prev.map((a) => (a.id === id ? updated : a)));
    setAgentConnStatuses((prev) => {
      if (!(id in prev)) return prev;
      const next = { ...prev };
      delete next[id];
      return next;
    });
    return updated;
  };
  const handleDuplicateAgent = async (id: string) => {
    const dup = await api.duplicateAgent(id);
    setAgents((prev) => [...prev, dup]);
    return dup;
  };
  const handleDeleteAgent = async (id: string) => {
    const target = agents.find((a) => a.id === id);
    await api.deleteAgent(id);
    const next = agents.filter((a) => a.id !== id);
    setAgents(next);
    if (target?.agent_type === "resource_matching") {
      const nextMatch = next.find((a) => a.agent_type === "resource_matching");
      if (resolvedMatchingAgentId === id) {
        handleSelectMatchingAgent(nextMatch?.id ?? "");
      }
    } else if (activeSession?.agent_id === id) {
      const nextReq = next.find((a) => (a.agent_type || "requirement_parsing") === "requirement_parsing");
      if (nextReq) await handleSelectRequirementAgent(nextReq.id);
    }
  };

  const handleCreateTemplate = async (payload: OutputTemplatePayload) => {
    const created = await api.createTemplate(payload);
    setTemplates((prev) => [...prev, created]);
    return created;
  };
  const handleUpdateTemplate = async (id: string, payload: Partial<OutputTemplatePayload>) => {
    const updated = await api.updateTemplate(id, payload);
    setTemplates((prev) => prev.map((t) => (t.id === id ? updated : t)));
    if (id === activeTemplateId && visibleData) {
      await refreshTemplatePreview(id, visibleData);
    }
    return updated;
  };
  const handleDeleteTemplate = async (id: string) => {
    await api.deleteTemplate(id);
    const next = templates.filter((t) => t.id !== id);
    setTemplates(next);
    if (activeSession?.template_id === id && next[0]) {
      await handleSelectTemplate(next[0].id);
    }
  };

  const debugMessage =
    activeSession?.messages.find((m) => m.id === selectedDebugMessageId) ||
    [...(activeSession?.messages || [])]
      .reverse()
      .find((m) => m.role === "assistant" && m.debug_info) ||
    null;

  const topBarAgents = workspaceMode === "matching" ? matchingAgents : requirementAgents;
  const topBarActiveAgentId = workspaceMode === "matching" ? resolvedMatchingAgentId : activeRequirementAgentId;

  return (
    <div className="assessment-workspace">
      <TopBar
        mode={workspaceMode}
        onModeChange={(nextMode) => {
          setWorkspaceMode(nextMode);
          if (nextMode === "matching" && !matchingTarget.sessionId && activeSession) {
            setMatchingTarget({ sessionId: activeSession.id, versionId: versions[0]?.id ?? null });
          }
        }}
        agents={topBarAgents}
        activeAgentId={topBarActiveAgentId}
        connectionStatus={topBarActiveAgentId ? agentConnStatuses[topBarActiveAgentId] : undefined}
        onSelectAgent={(id) => {
          if (workspaceMode === "matching") handleSelectMatchingAgent(id);
          else void handleSelectRequirementAgent(id);
        }}
        onOpenAgentManager={() => openAgentManagerFor(workspaceMode === "matching" ? "resource_matching" : "requirement_parsing")}
        onOpenTemplateStudio={() => setTemplateModalOpen(true)}
        leftCollapsed={leftCollapsed}
        onToggleLeft={() => { setLeftCollapsed((v) => !v); setArea("files"); }}
        rightCollapsed={rightCollapsed}
        onToggleRight={() => { setRightCollapsed((v) => !v); setArea("results"); }}
      />

      {bannerError && (
        <Alert
          type="error"
          banner
          closable
          message={bannerError}
          onClose={() => setBannerError(null)}
        />
      )}

      {workspaceMode === "assets" ? (
        <main className="module-main">
          <ModelAssetLibrary initialAssetId={inspectedAssetId} />
        </main>
      ) : workspaceMode === "matching" ? (
        <main className="module-main">
          <ResourceMatchingWorkbench
            sessions={sessions}
            initialSessionId={matchingTarget.sessionId ?? activeSession?.id ?? null}
            initialVersionId={matchingTarget.versionId}
            activeMatchingAgent={activeMatchingAgent}
            onOpenMatchingAgentConfig={() => openAgentManagerFor("resource_matching")}
            onInspectAsset={(assetId) => {
              setInspectedAssetId(assetId);
              setWorkspaceMode("assets");
            }}
          />
        </main>
      ) : (
        <>
          <nav className="compact-nav" aria-label="切换工作区">
            {([["files", "素材与会话", Files], ["chat", "需求对话", MessageSquare], ["results", "解析结果", Layers3]] as const)
              .map(([id, label, Icon]) => <button type="button" key={id} aria-pressed={area === id} onClick={() => setArea(id)}>
                <Icon size={16} />{label}</button>)}
          </nav>
          <main className="workbench-grid" data-area={area} data-left={!leftCollapsed} data-right={!rightCollapsed}
            style={{ gridTemplateColumns: `${leftCollapsed ? "" : "minmax(210px, 232px) "}minmax(300px, 1fr)${rightCollapsed ? "" : ` minmax(350px, min(${resultWidth}%, calc(100% - ${leftCollapsed ? 300 : 532}px)))`}` }}>
            <aside className="files-area">
              <LeftSidebar sessions={sessions} activeSessionId={activeSession?.id || null} files={activeSession?.files || []}
                onCreateSession={() => { if (!isRunning) void handleCreateSession(); }} onSelectSession={handleSelectSession}
                onDeleteSession={handleDeleteSession} onPreviewFile={setPreviewFile} onDeleteFile={handleDeleteFile}
                onReparseFile={handleReparse} uploadDisabled={!activeSession || isRunning || stage === "parsing"}
                onUpload={() => { setArea("chat"); document.getElementById("workbench-upload")?.click(); }} />
            </aside>
            <div className="chat-area">
              <ChatWorkbench ready={!!activeSession} key={activeSession?.id} messages={activeSession?.messages || []} files={activeSession?.files || []}
                samples={samples} stage={stage} isRunning={isRunning} progress={progress} demoMode={demoMode} onToggleDemoMode={setDemoMode}
                onUploadFiles={handleUploadFiles} onLoadSampleText={() => {}} onLoadSampleCsv={handleLoadSampleCsv}
                onPreviewFile={setPreviewFile} onSendMessage={handleSendMessage} onAbortRun={handleAbortRun}
                onRerunMessage={handleRerunMessage} onResumeMessage={(id) => handleRerunMessage(id, true)} onClearSession={handleClearSession}
                onSelectDebugMessage={(id) => { setSelectedDebugMessageId(id); openResults("debug"); }}
                onOpenAgentManager={() => openAgentManagerFor("requirement_parsing")}
                onOpenResult={(id) => { void handleInspect(id); openResults("overview"); }}
                revisionBase={revisionBase} onClearRevision={() => setRevisionBase(undefined)}
                strategy={strategy} onStrategy={setStrategy} />
            </div>
            <aside className="results-area">
              <label className="resize-result" title="调整结果面板宽度">
                <span>面板宽度</span>
                <input aria-label="调整结果面板宽度" type="range" min={36} max={53} value={resultWidth}
                  onChange={(event) => setResultWidth(Number(event.target.value))} />
              </label>
              <RightPanel sessionId={activeSession?.id ?? null} templates={templates} activeTemplateId={activeTemplateId} onSelectTemplate={handleSelectTemplate}
                extractedJson={visibleData} templatePreview={templatePreview} onApplyJsonEdit={handleApplyJsonEdit}
                onExport={handleExport} debugMessage={debugMessage} progress={progress} isRunning={isRunning} tab={tab} onTab={setTab} versions={versions}
                onVersion={(id) => void handleInspect(id)} onRevise={(id) => {
                  setRevisionBase(id); setStrategy("semantic"); setArea("chat");
                  requestAnimationFrame(() => document.getElementById("workbench-input")?.focus());
                }} onVerify={(id) => activeSession ? api.verifyVersion(activeSession.id, id) : Promise.reject("没有会话")
                } onProject={handleProject} onReview={handleReview}
                onStartMatching={(sid, vid) => {
                  setMatchingTarget({ sessionId: sid, versionId: vid });
                  setWorkspaceMode("matching");
                }}
                onOpenSource={(id, cite) => {
                  const file = activeSession?.files.find((item) => item.id === id);
                  const message = activeSession?.messages.find((item) => item.id === id);
                  if (file) { setSourceEvidence(cite); setPreviewFile(file); }
                  else if (message) setMessageSource({ text: message.content, cite });
                  else antdMessage.info("来源已从当前会话移除");
                }} />
            </aside>
          </main>
        </>
      )}

      <FilePreviewModal key={previewFile?.id} file={previewFile} cite={sourceEvidence}
        onClose={() => { setPreviewFile(null); setSourceEvidence(undefined); }} />
      <Modal open={Boolean(messageSource)} title="用户消息原文" footer={null} onCancel={() => setMessageSource(undefined)}>
        {messageSource && <SourceMessage {...messageSource} />}
      </Modal>

      <AgentManagerModal
        open={agentModalOpen}
        agents={agents}
        roleSpecs={roleSpecs}
        initialRole={agentModalRole}
        activeAgentIdsByRole={{
          requirement_parsing: activeRequirementAgentId,
          resource_matching: resolvedMatchingAgentId,
        }}
        envStatus={envStatus}
        onClose={() => setAgentModalOpen(false)}
        onCreateAgent={handleCreateAgent}
        onUpdateAgent={handleUpdateAgent}
        onDuplicateAgent={handleDuplicateAgent}
        onDeleteAgent={handleDeleteAgent}
        onActivateAgent={async (id, role) => {
          if (role === "resource_matching") handleSelectMatchingAgent(id);
          else await handleSelectRequirementAgent(id);
        }}
        onConnectionTested={(agentId, res) =>
          setAgentConnStatuses((prev) => ({ ...prev, [agentId]: res }))
        }
      />

      <TemplateStudioModal
        open={templateModalOpen}
        templates={templates}
        activeTemplateId={activeTemplateId}
        sampleJson={visibleData}
        onClose={() => setTemplateModalOpen(false)}
        onCreateTemplate={handleCreateTemplate}
        onUpdateTemplate={handleUpdateTemplate}
        onDeleteTemplate={handleDeleteTemplate}
      />
    </div>
  );
}
