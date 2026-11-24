import { Button, Dropdown, Select, Tooltip } from "antd";
import { Blocks, Box, Cpu, FileSearch, PanelLeft, PanelRight, Settings2, Sheet } from "lucide-react";
import type { AgentConnectionTestResponse, AgentProfile } from "../types";

export type WorkspaceMode = "requirements" | "assets" | "matching";

interface TopBarProps {
  mode: WorkspaceMode;
  onModeChange: (mode: WorkspaceMode) => void;
  agents: AgentProfile[];
  activeAgentId: string | null;
  connectionStatus?: AgentConnectionTestResponse;
  onSelectAgent: (agentId: string) => void;
  onOpenAgentManager: () => void;
  onOpenTemplateStudio: () => void;
  leftCollapsed: boolean;
  onToggleLeft: () => void;
  rightCollapsed: boolean;
  onToggleRight: () => void;
}

const connectionLabels: Record<string, string> = {
  unconfigured: "未配置",
  authentication_failed: "认证失败",
  model_access_denied: "模型无访问权限",
  provider_forbidden: "服务拒绝访问",
  service_unavailable: "网络或服务不可用",
};

export function TopBar(props: TopBarProps) {
  const agent = props.agents.find((item) => item.id === props.activeAgentId);
  const hasKey = agent?.has_custom_api_key || agent?.using_env_api_key;
  const offline = agent?.effective_base_url.startsWith("mock://");
  const connection = props.connectionStatus;
  const label = offline ? "本地模式" : connection ? connection.ok ? "连接成功" :
    connectionLabels[connection.error_code ?? ""] || "连接异常" : hasKey ? "已配置 · 未测试" : "未配置";
  return <header className="workspace-header">
    <div className="workspace-brand"><div className="brand-mark"><Blocks size={20} /></div>
      <div><h1>Assessment</h1><span>工艺需求与资源匹配平台</span></div></div>
    <nav className="workspace-mode-nav" aria-label="功能模块切换">
      {([
        ["requirements", "需求解析", FileSearch],
        ["assets", "模型资产库", Box],
        ["matching", "资源匹配", Cpu],
      ] as const).map(([id, text, Icon]) => (
        <button
          key={id}
          type="button"
          className="mode-nav-btn"
          aria-pressed={props.mode === id}
          onClick={() => props.onModeChange(id)}
        >
          <Icon size={15} />
          <span>{text}</span>
        </button>
      ))}
    </nav>
    <div className="header-agent">
      <Select aria-label="当前智能体" size="small" value={agent?.id}
        onChange={props.onSelectAgent} popupMatchSelectWidth={false}
        options={props.agents.map((item) => ({ value: item.id, label: item.name }))} />
      <Tooltip title={connection?.message || agent?.effective_model}>
        <button type="button" className="connection-pill" data-state={connection?.ok || offline ? "ready" : connection ? "error" : "idle"}
          onClick={props.onOpenAgentManager}><i />{label}</button>
      </Tooltip>
    </div>
    <nav className="header-actions" aria-label="工作台设置">
      <Dropdown trigger={["click"]} menu={{ items: [
        { key: "agent", label: "智能体配置", icon: <Settings2 size={15} />, onClick: props.onOpenAgentManager },
        { key: "templates", label: "模板管理", icon: <Sheet size={15} />, onClick: props.onOpenTemplateStudio },
      ] }}><Button type="text" aria-label="高级设置" icon={<Settings2 size={17} />}><span>设置</span></Button></Dropdown>
      {props.mode === "requirements" && <div className="desktop-toggles">
        <Tooltip title={props.leftCollapsed ? "展开素材" : "折叠素材"}><Button type="text" aria-label="切换素材侧栏"
          aria-pressed={!props.leftCollapsed} icon={<PanelLeft size={16} />} onClick={props.onToggleLeft} /></Tooltip>
        <Tooltip title={props.rightCollapsed ? "展开结果" : "折叠结果"}><Button type="text" aria-label="切换结果侧栏"
          aria-pressed={!props.rightCollapsed} icon={<PanelRight size={16} />} onClick={props.onToggleRight} /></Tooltip>
      </div>}
    </nav>
  </header>;
}
