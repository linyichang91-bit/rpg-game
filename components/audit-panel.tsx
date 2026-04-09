"use client";

import type { AuditPacket } from "@/lib/types";

type AuditPanelProps = {
  auditTrail: AuditPacket[];
  isOpen: boolean;
  onToggle: () => void;
};

export function AuditPanel({
  auditTrail,
  isOpen,
  onToggle
}: AuditPanelProps) {
  return (
    <aside className={`panel panel-audit ${isOpen ? "open" : "closed"}`}>
      <button className="audit-toggle" onClick={onToggle} type="button">
        {isOpen ? "收起审计" : "展开审计"}
      </button>

      <div className="audit-content">
        <div className="panel-header">
          <span className="panel-kicker">冷酷结算日志</span>
          <h2>执行事件 / 状态变更 / 地图快照</h2>
        </div>

        {auditTrail.length === 0 ? (
          <p className="empty-copy">后端暂时还没有返回审计数据。</p>
        ) : (
          auditTrail.map((packet) => (
            <article className="audit-entry" key={packet.id}>
              <div className="audit-stamp">
                {new Date(packet.created_at).toLocaleTimeString()}
              </div>
              <pre>{JSON.stringify(packet.executed_events, null, 2)}</pre>
              <pre>{JSON.stringify(packet.mutation_logs, null, 2)}</pre>
              {packet.topology_snapshot ? (
                <pre>{JSON.stringify(packet.topology_snapshot, null, 2)}</pre>
              ) : null}
            </article>
          ))
        )}
      </div>
    </aside>
  );
}
