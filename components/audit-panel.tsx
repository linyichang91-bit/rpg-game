"use client";

import { useState } from "react";

import type { AuditPacket } from "@/lib/types";

type AuditPanelProps = {
  auditTrail: AuditPacket[];
  isOpen: boolean;
  onToggle: () => void;
};

function formatTimestamp(ts: number): string {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(ts));
}

type SectionKey = "executed" | "mutations" | "topology";

function AuditSection({
  label,
  data,
}: {
  label: string;
  data: unknown;
}) {
  const [isOpen, setIsOpen] = useState(false);

  return (
    <div className="audit-section">
      <button
        className="audit-section-toggle"
        onClick={() => setIsOpen((prev) => !prev)}
        type="button"
      >
        <span className="audit-section-chevron">{isOpen ? "▾" : "▸"}</span>
        <span>{label}</span>
        <span className="audit-section-count">
          {Array.isArray(data) ? `${data.length} 条` : ""}
        </span>
      </button>
      {isOpen ? (
        <pre className="audit-section-content">
          {JSON.stringify(data, null, 2)}
        </pre>
      ) : null}
    </div>
  );
}

function AuditEntry({ packet }: { packet: AuditPacket }) {
  const [isOpen, setIsOpen] = useState(false);

  return (
    <article className="audit-entry">
      <button
        className="audit-entry-header"
        onClick={() => setIsOpen((prev) => !prev)}
        type="button"
      >
        <span className="audit-stamp">{formatTimestamp(packet.created_at)}</span>
        <span className="audit-entry-summary">
          {packet.executed_events.length > 0
            ? `${packet.executed_events.length} 个事件`
            : ""}
          {packet.mutation_logs.length > 0
            ? ` · ${packet.mutation_logs.length} 个状态变更`
            : ""}
        </span>
        <span className="audit-entry-chevron">{isOpen ? "▾" : "▸"}</span>
      </button>
      {isOpen ? (
        <div className="audit-entry-body">
          <AuditSection
            label="已执行事件"
            data={packet.executed_events}
          />
          <AuditSection
            label="状态变更日志"
            data={packet.mutation_logs}
          />
          {packet.topology_snapshot ? (
            <AuditSection
              label="地图拓扑快照"
              data={packet.topology_snapshot}
            />
          ) : null}
        </div>
      ) : null}
    </article>
  );
}

export function AuditPanel({
  auditTrail,
  isOpen,
  onToggle,
}: AuditPanelProps) {
  return (
    <aside className={`panel panel-audit ${isOpen ? "open" : "closed"}`}>
      <button className="audit-toggle" onClick={onToggle} type="button">
        {isOpen ? "收起审计" : "展开审计"}
      </button>

      <div className="audit-content">
        <div className="panel-header">
          <span className="panel-kicker">审计轨迹</span>
          <h2>执行事件 / 状态变更 / 地图快照</h2>
        </div>

        {auditTrail.length === 0 ? (
          <p className="empty-copy">后端暂时还没有返回审计数据。</p>
        ) : (
          auditTrail.map((packet) => (
            <AuditEntry key={packet.id} packet={packet} />
          ))
        )}
      </div>
    </aside>
  );
}
