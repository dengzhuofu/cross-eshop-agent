import type { ToolCall } from '../types';
import { formatLatency, RISK_LEVEL_LABELS, riskLevelTone, TOOL_STATUS_LABELS, toolStatusTone } from '../labels';
import StatusBadge from './StatusBadge';

/**
 * 工具调用审计表:
 * 高风险工具红色徽章;状态 ok 绿 / error 红 / replayed 蓝;
 * 幂等键等宽字体展示,体现"高风险写操作幂等可重放"的设计。
 */
export default function ToolCallTable({ toolCalls }: { toolCalls: ToolCall[] }) {
  return (
    <div className="card table-card panel-wide">
      <h3 className="section-title">工具调用审计</h3>

      {toolCalls.length === 0 ? (
        <p className="cell-muted empty-hint">暂无工具调用记录。</p>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>#</th>
              <th>工具</th>
              <th>风险等级</th>
              <th>状态</th>
              <th>幂等键</th>
              <th style={{ textAlign: 'right' }}>耗时</th>
              <th>错误信息</th>
            </tr>
          </thead>
          <tbody>
            {toolCalls.map((tc) => (
              <tr key={String(tc.id)}>
                <td className="cell-muted">{tc.id}</td>
                <td className="cell-mono">{tc.tool}</td>
                <td>
                  <StatusBadge
                    label={RISK_LEVEL_LABELS[tc.risk_level] ?? tc.risk_level}
                    tone={riskLevelTone(tc.risk_level)}
                  />
                </td>
                <td>
                  <StatusBadge
                    label={TOOL_STATUS_LABELS[tc.status] ?? tc.status}
                    tone={toolStatusTone(tc.status)}
                  />
                </td>
                <td className="cell-mono cell-faint idem-key" title={tc.idempotency_key ?? ''}>
                  {tc.idempotency_key ?? '—'}
                </td>
                <td className="cell-muted" style={{ textAlign: 'right' }}>
                  {formatLatency(tc.latency_ms)}
                </td>
                <td className="cell-error">{tc.error ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
