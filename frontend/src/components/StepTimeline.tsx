import type { WorkflowStep } from '../types';
import type { ApiClient } from '../api';
import { formatLatency, nodeLabel, STEP_STATUS_LABELS, stepStatusTone } from '../labels';
import StatusBadge from './StatusBadge';
import CollapsibleJson from './CollapsibleJson';
import BadCaseScanBlock from './BadCaseScanBlock';
import FeedbackWidget from './FeedbackWidget';

/** 可反馈的节点 → 反馈 target_type 映射(M10 闭环入口) */
const FEEDBACK_TARGETS: Record<string, 'support_draft' | 'listing_copy'> = {
  support: 'support_draft',
  listing: 'listing_copy',
};

interface Props {
  steps: WorkflowStep[];
  loading: boolean;
  client?: ApiClient;
  workflowId?: string;
}

/**
 * 步骤时间线:竖向连线 + 彩色节点圆点,按 seq 升序;
 * detail 通过 CollapsibleJson 折叠展示,避免长 JSON 淹没主流程;
 * 坏例防线扫描步骤(node="bad_case_scan")走警示样式的结构化渲染;
 * support/listing 步骤带反馈组件(M10):👍/👎 → 分诊沉淀。
 */
export default function StepTimeline({ steps, loading, client, workflowId }: Props) {
  const sorted = [...steps].sort((a, b) => a.seq - b.seq);
  const feedbackTarget = (node: string) =>
    client && workflowId ? FEEDBACK_TARGETS[node] : undefined;

  return (
    <div className="card panel">
      <h3 className="section-title">步骤时间线</h3>

      {loading && <p className="cell-muted empty-hint">加载中…</p>}
      {!loading && sorted.length === 0 && <p className="cell-muted empty-hint">暂无步骤记录。</p>}

      {sorted.length > 0 && (
        <ol className="timeline">
          {sorted.map((step) => {
            const isScan = step.node === 'bad_case_scan';
            const target = feedbackTarget(step.node);
            return (
              <li key={step.seq} className={`tl-item${isScan ? ' scan-step' : ''}`}>
                <span
                  className={`tl-dot ${isScan ? 'bg-red' : `bg-${stepStatusTone(step.status)}`}`}
                  aria-hidden
                />
                <div className={`tl-body${isScan ? ' scan-body' : ''}`}>
                  <div className="tl-head">
                    <span className="tl-seq">#{step.seq}</span>
                    <span className="tl-node">{nodeLabel(step.node)}</span>
                    <span className="cell-mono cell-faint tl-raw">{step.node}</span>
                    <StatusBadge label={STEP_STATUS_LABELS[step.status] ?? step.status} tone={stepStatusTone(step.status)} />
                    <span className="tl-latency">{formatLatency(step.latency_ms)}</span>
                  </div>
                  {isScan ? (
                    <>
                      <BadCaseScanBlock detail={step.detail} />
                      <CollapsibleJson data={step.detail} label="原始扫描数据" />
                    </>
                  ) : (
                    <>
                      <CollapsibleJson data={step.detail} />
                      {target && step.status === 'completed' && (
                        <FeedbackWidget
                          client={client}
                          workflowId={workflowId}
                          targetType={target}
                          targetKey={String(step.seq)}
                        />
                      )}
                    </>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}
