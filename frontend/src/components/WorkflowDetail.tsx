import { useEffect, useRef, useState } from 'react';
import type { ApiClient } from '../api';
import type { TraceResponse, WorkflowDetailData } from '../types';
import {
  isTerminalStatus,
  MARKETPLACE_LABELS,
  statusLabel,
  workflowStatusTone,
} from '../labels';
import StatusBadge from './StatusBadge';
import AgentStream from './AgentStream';

interface Props {
  client: ApiClient;
  workflowId: string;
  onBack: () => void;
  /** 待人工审批时活动流里「前往审批中心」的跳转（App 视图切换） */
  onGoApprovals?: () => void;
}

const POLL_INTERVAL_MS = 1500;

/**
 * 工作流详情视图(核心展示面,M13 起为对话式 agent 过程)。
 *
 * 轮询策略:工作流处于非终态时,每 1.5 秒拉取一次 /trace 与详情;
 * 用 setTimeout 链式调度(而非 setInterval),保证上一次请求返回后才排下一次,
 * 避免慢请求造成堆积;组件卸载或进入终态后自动停止。
 */
export default function WorkflowDetail({ client, workflowId, onBack, onGoApprovals }: Props) {
  const [meta, setMeta] = useState<WorkflowDetailData | null>(null);
  const [trace, setTrace] = useState<TraceResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [polling, setPolling] = useState(false);

  const timerRef = useRef<number | undefined>(undefined);

  useEffect(() => {
    let alive = true;

    async function tick() {
      try {
        const [t, m] = await Promise.all([client.getTrace(workflowId), client.getWorkflow(workflowId)]);
        if (!alive) return;
        setTrace(t);
        setMeta(m);
        setError(null);
        const terminal = isTerminalStatus(t.workflow.status);
        setPolling(!terminal);
        if (!terminal) {
          // 非终态:1.5s 后继续下一轮
          timerRef.current = window.setTimeout(tick, POLL_INTERVAL_MS);
        }
      } catch (e) {
        if (!alive) return;
        setError(e instanceof Error ? e.message : String(e));
        setPolling(true); // 出错也继续重试,直到拿到终态
        timerRef.current = window.setTimeout(tick, POLL_INTERVAL_MS * 2);
      }
    }

    tick();
    return () => {
      alive = false;
      if (timerRef.current !== undefined) window.clearTimeout(timerRef.current);
    };
  }, [client, workflowId]);

  const status = trace?.workflow.status ?? meta?.status ?? '';
  const terminal = status ? isTerminalStatus(status) : false;

  return (
    <section>
      <button className="btn ghost back-btn" onClick={onBack}>
        ← 返回列表
      </button>

      {/* ---- 概览头卡 ---- */}
      <div className="card detail-header">
        <div className="detail-head-row">
          <div>
            <h2 className="detail-title">{meta?.title ?? trace?.workflow.id ?? '加载中…'}</h2>
            <div className="cell-mono cell-muted detail-id">{trace?.workflow.id ?? workflowId}</div>
          </div>
          <div className="detail-badges">
            {status && <StatusBadge label={statusLabel(status)} tone={workflowStatusTone(status)} />}
          </div>
        </div>

        {meta && (
          <>
            <div className="stat-chips">
              <span className="chip">
                选品创意:<strong>{meta.product_idea}</strong>
              </span>
              <span className="chip">
                目标渠道:
                <strong>{meta.marketplaces.map((m) => MARKETPLACE_LABELS[m] ?? m).join(' / ') || '—'}</strong>
              </span>
              <span className="chip">
                步骤数:<strong>{meta.step_count}</strong>
              </span>
              <span className="chip">
                决策数:<strong>{meta.decision_count}</strong>
              </span>
            </div>

            {!terminal && (
              <div className="poll-indicator">
                <span className={polling ? 'pulse-dot' : 'static-dot'} />
                实时追踪中 · 每 1.5s 自动刷新
              </div>
            )}
          </>
        )}

        {(meta?.error || trace?.workflow.error) && (
          <div className="banner-error">运行错误:{meta?.error || trace?.workflow.error}</div>
        )}
        {error && !polling && <div className="banner-error">请求失败:{error}</div>}
      </div>

      {/* ---- M13：对话式 agent 活动流（步骤/决策/工具调用按时间交错） ---- */}
      <AgentStream
        steps={trace?.steps ?? []}
        decisions={trace?.decisions ?? []}
        toolCalls={trace?.tool_calls ?? []}
        loading={!trace}
        status={status}
        currentNode={meta?.current_node ?? null}
        productIdea={meta?.product_idea}
        marketplaces={meta?.marketplaces}
        client={client}
        workflowId={workflowId}
        onGoApprovals={onGoApprovals}
      />
    </section>
  );
}
