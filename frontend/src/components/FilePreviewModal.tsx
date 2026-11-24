import { useRef, useState } from "react";
import { Alert, Button, Card, Modal, Segmented, Space, Table, Tabs, Tag, Typography } from "antd";
import { Check, Copy, FileText, Table2 } from "lucide-react";
import type { Evidence, UploadedFile } from "../types";
import { evidenceLocation } from "./SemanticDetails";

const { Text } = Typography;

interface FilePreviewModalProps {
  file: UploadedFile | null;
  cite?: Evidence;
  onClose: () => void;
}

interface SheetPreview {
  name: string;
  total_rows: number;
  rows: Array<Array<{ coord: string; value: string }>>;
  truncated_preview?: boolean;
}

export function FilePreviewModal({ file, cite, onClose }: FilePreviewModalProps) {
  const [viewMode, setViewMode] = useState<string>("structured");
  const [copied, setCopied] = useState(false);
  const highlightedCell = useRef<HTMLDivElement>(null);

  const preview = file?.parsed_preview || {};
  const kind = String(preview.kind || "text");
  const sheets = (preview.sheets as SheetPreview[] | undefined) || [];
  const lines = (preview.lines as Array<{ line_no: number; text: string }> | undefined) || [];
  const pages =
    (preview.pages as Array<{ page_no: number; lines: Array<{ coord: string; text: string }> }> | undefined) ||
    [];
  const sections = (preview.sections as Array<Record<string, unknown>> | undefined) || [];

  const handleCopy = async () => {
    if (!file) return;
    await navigator.clipboard.writeText(file.parsed_text || "");
    setCopied(true);
    setTimeout(() => setCopied(false), 1800);
  };

  const renderSheetTable = (sheet: SheetPreview) => {
    if (!sheet.rows || sheet.rows.length === 0) {
      return <Text type="secondary">当前工作表为空</Text>;
    }
    const maxCols = Math.max(...sheet.rows.map((r) => r.length));
    const columns = Array.from({ length: maxCols }, (_, cIdx) => ({
      title: sheet.rows[0]?.[cIdx]?.value || `Col ${cIdx + 1}`,
      dataIndex: `c_${cIdx}`,
      key: `c_${cIdx}`,
      render: (cell: { coord: string; value: string } | undefined) => {
        const active = Boolean(cite?.location.cell && cell?.coord === cite.location.cell && sheet.name === cite.location.sheet);
        return <div className="min-w-[85px]" data-source-cell={active || undefined} ref={active ? highlightedCell : undefined}>
          <div style={{ fontSize: 10, fontFamily: "monospace", color: "var(--accent)" }}>
            {sheet.name !== "CSV" ? `${sheet.name}!${cell?.coord || ""}` : cell?.coord || ""}
          </div>
          <div className="text-xs whitespace-pre-wrap">{cell?.value || "-"}</div>
        </div>;
      },
    }));

    const dataSource = sheet.rows.map((row, rIdx) => {
      const rec: Record<string, unknown> = { key: rIdx };
      row.forEach((cell, cIdx) => {
        rec[`c_${cIdx}`] = cell;
      });
      return rec;
    });

    return (
      <Table
        size="small"
        bordered
        pagination={false}
        columns={columns}
        dataSource={dataSource}
        scroll={{ x: "max-content", y: 440 }}
      />
    );
  };

  return (
    <Modal
      open={Boolean(file)}
      onCancel={onClose}
      footer={null}
      width={960}
      centered
      afterOpenChange={(open) => {
        if (open) highlightedCell.current?.scrollIntoView({ block: "nearest", inline: "nearest" });
      }}
      destroyOnClose
      title={
        file ? (
          <div className="flex flex-wrap items-center justify-between gap-2 pr-8">
            <div>
              <Space size={8}>
                <Text strong style={{ fontSize: 14 }}>
                  解析原文预览：{file.filename}
                </Text>
                <Tag color="blue" style={{ margin: 0 }}>
                  {file.file_ext}
                </Tag>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {(file.parsed_text || "").length} chars
                </Text>
              </Space>
            </div>
            <Space size={8}>
              <Segmented
                size="small"
                value={viewMode}
                onChange={(v) => setViewMode(String(v))}
                options={[
                  {
                    label: (
                      <span className="inline-flex items-center gap-1">
                        <Table2 className="w-3.5 h-3.5" />
                        坐标/结构化预览
                      </span>
                    ),
                    value: "structured",
                  },
                  {
                    label: (
                      <span className="inline-flex items-center gap-1">
                        <FileText className="w-3.5 h-3.5" />
                        模型输入原文
                      </span>
                    ),
                    value: "raw",
                  },
                ]}
              />
              <Button
                size="small"
                icon={
                  copied ? (
                    <Check className="w-3.5 h-3.5 text-[#52c41a]" />
                  ) : (
                    <Copy className="w-3.5 h-3.5" />
                  )
                }
                onClick={handleCopy}
              >
                {copied ? "已复制" : "复制原文"}
              </Button>
            </Space>
          </div>
        ) : null
      }
    >
      {file && (
        <div className="max-h-[68vh] overflow-auto pt-2">
          {cite && <p className="source-location"><strong>{evidenceLocation(cite)}</strong> · {cite.quote}</p>}
          {file.parse_status === "error" ? (
            <Alert
              type="error"
              showIcon
              message="文件解析失败"
              description={file.parse_error}
            />
          ) : viewMode === "raw" ? (
            <pre
              className="m-0 p-4 rounded-md text-xs font-mono whitespace-pre-wrap leading-relaxed"
              style={{ background: "#fafafa", border: "1px solid #f0f0f0" }}
            >
              {file.parsed_text || "(空内容)"}
            </pre>
          ) : kind === "sheets" && sheets.length > 0 ? (
            sheets.length > 1 ? (
              <Tabs
                size="small"
                defaultActiveKey={cite?.location.sheet ?? undefined}
                items={sheets.map((s) => ({
                  key: s.name,
                  label: `${s.name} (${s.total_rows}行)`,
                  children: renderSheetTable(s),
                }))}
              />
            ) : (
              renderSheetTable(sheets[0])
            )
          ) : kind === "pdf" && pages.length > 0 ? (
            <div className="space-y-3">
              {pages.map((p) => (
                <Card key={p.page_no} size="small" title={`Page ${p.page_no}`}>
                  <div className="divide-y divide-slate-100 font-mono text-xs">
                    {p.lines.map((l) => (
                      <div key={l.coord} className="flex gap-3 py-1">
                        <span style={{ color: "#1677ff", width: 64, flexShrink: 0 }}>{l.coord}</span>
                        <span className="whitespace-pre-wrap">{l.text}</span>
                      </div>
                    ))}
                  </div>
                </Card>
              ))}
            </div>
          ) : kind === "document" && sections.length > 0 ? (
            <div className="space-y-3">
              {sections.map((sec, sIdx) => (
                <Card key={sIdx} size="small" title={String(sec.label || "内容")}>
                  {sec.type === "paragraphs" ? (
                    <div className="divide-y divide-slate-100 text-xs">
                      {((sec.items as Array<{ coord: string; text: string }>) || []).map((it) => (
                        <div key={it.coord} className="flex gap-3 py-1.5">
                          <span style={{ color: "#1677ff", width: 42, flexShrink: 0, fontFamily: "monospace" }}>
                            {it.coord}
                          </span>
                          <span>{it.text}</span>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="space-y-3">
                      {((sec.tables as Array<{ name: string; rows: Array<Array<{ coord: string; value: string }>> }>) || []).map(
                        (tbl) => (
                          <div key={tbl.name}>
                            {renderSheetTable({
                              name: tbl.name,
                              total_rows: tbl.rows.length,
                              rows: tbl.rows,
                            })}
                          </div>
                        )
                      )}
                    </div>
                  )}
                </Card>
              ))}
            </div>
          ) : (
            <div
              className="rounded-md divide-y divide-slate-100 font-mono text-xs"
              style={{ border: "1px solid #f0f0f0" }}
            >
              {lines.length > 0 ? (
                lines.map((l) => (
                  <div key={l.line_no} className="flex gap-3 px-3 py-1 hover:bg-slate-50">
                    <span style={{ color: "#1677ff", width: 42, flexShrink: 0, userSelect: "none" }}>
                      L{l.line_no}
                    </span>
                    <span className="whitespace-pre-wrap">{l.text}</span>
                  </div>
                ))
              ) : (
                <pre className="p-4 m-0 whitespace-pre-wrap">{file.parsed_text}</pre>
              )}
            </div>
          )}
        </div>
      )}
    </Modal>
  );
}
