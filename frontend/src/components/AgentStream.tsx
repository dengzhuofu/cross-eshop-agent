import { useMemo } from 'react';
import type { ApiClient } from '../api';
import type { DecisionRecord, ToolCall, WorkflowStep } from '../types';
import {
  DECISION_TYPE_LABELS,
  MARKETPLACE_LABELS,
  SOURCE_LABELS,
  STEP_STATUS_LABELS,
  STRATEGY_LABELS,
  formatLatency,
  nodeLabel,
  stepStatusTone,
  strLabel,
  toolLabel,
} from '../labels';
import StatusBadge from './StatusBadge';
import CollapsibleJson from './CollapsibleJson';
import BadCaseScanBlock from './BadCaseScanBlock';
import FeedbackWidget from './FeedbackWidget';
import PublishBlock from './PublishBlock';

/** 可反馈的节点 → 反馈 target_type 映射（M10 闭环入口，与旧时间线一致） */
const FEEDBACK_TARGETS: Record<string, 'support_draft' | 'listing_copy'> = {
  support: 'support_draft',
  listing: 'listing_copy',
};

/** 节点 → 图标：活动卡片左侧的视觉锚点 */
const NODE_ICONS: Record<string, string> = {
  planner: '🧭',
  research: '🔍',
  profit: '💰',
  supplier: '🏭',
  decision_gate: '⚖️',
  listing: '📝',
  critic: '🛡️',
  approval_check: '🚦',
  publish: '🚀',
  ops: '📊',
  support: '🎧',
  retrospective: '🧾',
  bad_case_scan: '🕵️',
  halted: '⛔',
};

interface Props {
  steps: WorkflowStep[];
  decisions: DecisionRecord[];
  toolCalls: ToolCall[];
  loading: boolean;
  /** 工作流状态（终态时折叠工作过程、只突出最终交付） */
  status: string;
  currentNode: string | null;
  productIdea?: string;
  marketplaces?: string[];
  client?: ApiClient;
  workflowId?: string;
  /** 待人工审批时「前往审批中心」的跳转 */
  onGoApprovals?: () => void;
}

type Event =
  | { kind: 'step'; at: number; idx: number; step: WorkflowStep }
  | { kind: 'decision'; at: number; idx: number; d: DecisionRecord }
  | { kind: 'tool'; at: number; idx: number; t: ToolCall };

/** 后端时间戳形如 "2026-08-24 03:21:07.123456+00:00"——空格换 T 才能被 Date 解析 */
function tsNum(s: string | undefined): number {
  if (!s) return Number.NaN;
  const t = new Date(s.replace(' ', 'T')).getTime();
  return Number.isNaN(t) ? Number.NaN : t;
}

/**
 * M13 Codex 式对话界面：用户指令气泡 → 一整个可折叠的「工作过程」区
 * （工具调用/自主决策/步骤按时间交错，运行中自动展开、结束后折叠成一行摘要）
 * → 折叠区外的「最终交付」答案块（含商城外链）。与 Codex/Claude 的 agent
 * 对话一致：中间过程可查证，但答案才是主角。
 */
export default function AgentStream({
  steps,
  decisions,
  toolCalls,
  loading,
  status,
  currentNode,
  productIdea,
  marketplaces,
  client,
  workflowId,
  onGoApprovals,
}: Props) {
  const events = useMemo<Event[]>(() => {
    const all: Event[] = [
      ...steps.map((step, idx) => ({ kind: 'step' as const, at: tsNum(step.created_at), idx, step })),
      ...decisions.map((d, idx) => ({ kind: 'decision' as const, at: tsNum(d.created_at), idx, d })),
      ...toolCalls.map((t, idx) => ({ kind: 'tool' as const, at: tsNum(t.created_at), idx, t })),
    ];
    // 同一毫秒（或缺失时间戳）时按 rank 稳定排序：工具/决策发生在节点完成步骤之前
    const rank = (e: Event) => (e.kind === 'tool' ? 0 : e.kind === 'decision' ? 1 : 2);
    all.sort((a, b) => {
      const ta = a.at;
      const tb = b.at;
      if (!Number.isNaN(ta) && !Number.isNaN(tb) && ta !== tb) return ta - tb;
      if (rank(a) !== rank(b)) return rank(a) - rank(b);
      return a.idx - b.idx;
    });
    return all;
  }, [steps, decisions, toolCalls]);

  const terminal = ['completed', 'failed', 'cancelled', 'blocked', 'quarantined'].includes(status);

  // 摘要行统计：耗时取首末事件差（缺时间戳就不显示）
  const elapsedSec = useMemo(() => {
    const ts = events.map((e) => e.at).filter((t) => !Number.isNaN(t));
    if (ts.length < 2) return null;
    return Math.max(1, Math.round((Math.max(...ts) - Math.min(...ts)) / 1000));
  }, [events]);

  return (
    <div className="agent-stream">
      {/* ---- 用户指令气泡 ---- */}
      <div className="chat-user">
        <div className="chat-bubble-user">
          <div className="chat-role">运营指令</div>
          <p className="chat-idea">{productIdea || '（加载中…）'}</p>
          <div className="brief-chips">
            {(marketplaces ?? []).map((m) => (
              <span key={m} className="chip chip-sm">
                🛒 {MARKETPLACE_LABELS[m] ?? m}
              </span>
            ))}
          </div>
        </div>
      </div>

      {/* ---- Agent 回合：可折叠工作过程（Codex 式） ---- */}
      <details className={`cx-work${terminal ? ' cx-done' : ''}`} open={!terminal || loading}>
        <summary>
          <span className="cx-gear" aria-hidden>
            ⚙
          </span>
          {!loading && !terminal ? (
            <>
              <span className="cx-live">正在工作</span>
              {currentNode && <span className="cx-cur">— {nodeLabel(currentNode)}</span>}
              <span className="pulse-dot" />
            </>
          ) : (
            <>
              <span className="cx-live">工作过程</span>
              <span className="cx-meta">
                {steps.length} 步骤 · {toolCalls.length} 工具调用 · {decisions.length} 决策
                {elapsedSec != null && ` · ${elapsedSec}s`}
              </span>
            </>
          )}
          <span className="cx-caret" aria-hidden />
        </summary>

        <div className="work-body">
          {loading && events.length === 0 && <p className="cell-muted empty-hint">正在读取执行轨迹…</p>}
          {events.map((ev, i) => {
            if (ev.kind === 'step') {
              return <StepCard key={`s${ev.step.seq}-${i}`} step={ev.step} client={client} workflowId={workflowId} />;
            }
            if (ev.kind === 'decision') {
              return <ThinkRow key={`d${i}`} d={ev.d} />;
            }
            return <ToolRow key={`t${i}`} t={ev.t} />;
          })}
        </div>
      </details>

      {/* ---- 待人工审批插卡（需要用户行动，常驻不折叠） ---- */}
      {status === 'awaiting_approval' && (
        <div className="approval-wait">
          <span className="aw-icon">⏸</span>
          <div>
            <strong>已生成 Listing 并通过合规审查，等待人工审批</strong>
            <p>审批通过后 agent 将自动继续：多平台发布 → 商品同步上架到 Mock 商城。</p>
          </div>
          {onGoApprovals && (
            <button type="button" className="btn primary" onClick={onGoApprovals}>
              前往审批中心 →
            </button>
          )}
        </div>
      )}

      {/* ---- 运行中指示（折叠区外的一行轻提示） ---- */}
      {!loading && !terminal && currentNode && (
        <div className="live-row">
          <span className="pulse-dot" /> 正在执行：<strong>{nodeLabel(currentNode)}</strong>
          …
        </div>
      )}

      {/* ---- 最终交付（Codex 的「最终答案」，折叠后依然可见） ---- */}
      {terminal && !loading && <FinalCard status={status} steps={steps} decisions={decisions} />}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* 步骤卡（工作过程内部的结构化节点产出）                                */
/* ------------------------------------------------------------------ */

function StepCard({
  step,
  client,
  workflowId,
}: {
  step: WorkflowStep;
  client?: ApiClient;
  workflowId?: string;
}) {
  const isScan = step.node === 'bad_case_scan';
  const target = client && workflowId ? FEEDBACK_TARGETS[step.node] : undefined;
  const icon = NODE_ICONS[step.node] ?? '⚙️';
  return (
    <div className={`act-card${isScan ? ' act-scan' : ''}`}>
      <div className="act-head">
        <span className="act-icon" aria-hidden>
          {icon}
        </span>
        <span className="act-title">{nodeLabel(step.node)}</span>
        <span className="cell-mono cell-faint act-raw">{step.node}</span>
        <StatusBadge
          label={STEP_STATUS_LABELS[step.status] ?? step.status}
          tone={isScan ? 'red' : stepStatusTone(step.status)}
        />
        <span className="act-latency">{formatLatency(step.latency_ms)}</span>
      </div>
      <div className="act-body">
        {isScan ? (
          <>
            <BadCaseScanBlock detail={step.detail} />
            <CollapsibleJson data={step.detail} label="原始扫描数据" />
          </>
        ) : (
          <>
            <StepBody step={step} />
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
    </div>
  );
}

/** 取 detail 里的浅层标量字段做摘要 chips（嵌套对象交给 CollapsibleJson） */
function scalarChips(d: Record<string, unknown>): Array<[string, string]> {
  const skip = new Set(['detail', 'knowledge_refs', 'retrieval_trace', 'items', 'blocking_issues', 'key_decisions']);
  const out: Array<[string, string]> = [];
  const zh: Record<string, string> = {
    engine: '引擎',
    round: '轮次',
    evidence_score: '证据分',
    margin_pct: '利润率',
    chosen: '结论',
    verdict: '裁定',
    issue_count: '问题数',
    draft_source: '草稿来源',
    order_found: '订单命中',
    eta_text: 'ETA',
    memory_writeback: '记忆回写',
    published: '发布成功',
    total: '发布总数',
  };
  for (const [k, v] of Object.entries(d)) {
    if (skip.has(k) || v == null) continue;
    if (typeof v === 'boolean') out.push([zh[k] ?? k, v ? '是' : '否']);
    else if (typeof v === 'number' || typeof v === 'string')
      out.push([zh[k] ?? k, String(v).slice(0, 60)]);
  }
  return out.slice(0, 8);
}

function Chips({ pairs }: { pairs: Array<[string, string]> }) {
  if (pairs.length === 0) return null;
  return (
    <div className="kv-chips">
      {pairs.map(([k, v]) => (
        <span key={k} className="kv-chip">
          {k}:<strong>{v}</strong>
        </span>
      ))}
    </div>
  );
}

function RefChips({ refs, label }: { refs: unknown; label?: string }) {
  // 兼容两种形态：["REF-1", ...] 或 { amazon: ["REF-1"], ... }
  let flat: string[] = [];
  if (Array.isArray(refs)) flat = refs.map((r) => String(r));
  else if (refs && typeof refs === 'object') {
    for (const [mp, arr] of Object.entries(refs as Record<string, unknown>)) {
      if (Array.isArray(arr)) flat = flat.concat(arr.map((r) => `${MARKETPLACE_LABELS[mp] ?? mp}·${String(r)}`));
    }
  }
  if (flat.length === 0) return null;
  return (
    <div className="kb-refs">
      <span className="kb-label">📚 {label ?? '知识库引用'}</span>
      {flat.slice(0, 8).map((r) => (
        <code key={r} className="kb-ref">
          {r}
        </code>
      ))}
    </div>
  );
}

/** 节点级富渲染：把散在 detail 里的过程语义翻译成人话 */
function StepBody({ step }: { step: WorkflowStep }) {
  const d = (step.detail ?? {}) as Record<string, unknown>;
  switch (step.node) {
    case 'support':
      return <SupportBody d={d} />;
    case 'critic':
      return <CriticBody d={d} />;
    case 'decision_gate':
      return <GateBody d={d} />;
    default: {
      const pairs = scalarChips(d);
      return (
        <>
          {step.node === 'planner' || step.node === 'listing' ? (
            <RefChips refs={d.knowledge_refs} label={step.node === 'listing' ? 'Listing 守则引用' : '运营打法引用'} />
          ) : null}
          {step.node === 'retrospective' && Array.isArray(d.key_decisions) && d.key_decisions.length > 0 && (
            <ul className="mini-list">
              {d.key_decisions.map((k, i) => (
                <li key={i}>• {String(k)}</li>
              ))}
            </ul>
          )}
          <Chips pairs={pairs} />
        </>
      );
    }
  }
}

/** 客服节点：路由 → 策略 → 检索轮次 → 引用 → 草稿 —— agentic RAG 全过程可视 */
function SupportBody({ d }: { d: Record<string, unknown> }) {
  const route = d.route as { realtime?: boolean; policy?: boolean } | undefined;
  const trace = Array.isArray(d.retrieval_trace) ? (d.retrieval_trace as Array<Record<string, unknown>>) : [];
  const conflict = d.conflict_check as { detected?: boolean; tool_eta?: string; draft_etas?: string[] } | undefined;
  const strategy = strLabel(d.strategy);
  const source = typeof d.strategy_source === 'string' ? d.strategy_source : '';
  return (
    <>
      {route && (
        <div className="route-chips">
          <span className={`chip chip-sm ${route.realtime ? 'chip-on' : ''}`}>
            {route.realtime ? '✓' : '—'} 实时订单工具
          </span>
          <span className={`chip chip-sm ${route.policy ? 'chip-on' : ''}`}>
            {route.policy ? '✓' : '—'} 政策知识库
          </span>
          {source && (
            <span className={`badge tone-${source === 'llm' ? 'purple' : 'gray'}`}>
              策略来源:{SOURCE_LABELS[source] ?? source}
            </span>
          )}
        </div>
      )}
      {strategy && (
        <p className="strategy-line">
          检索策略：<span className="badge tone-teal">{STRATEGY_LABELS[strategy] ?? strategy}</span>
          {typeof d.strategy_reason === 'string' && d.strategy_reason && (
            <em className="reason-quote">“{d.strategy_reason}”</em>
          )}
        </p>
      )}
      {trace.length > 0 && (
        <table className="rag-table">
          <thead>
            <tr>
              <th>轮次</th>
              <th>策略</th>
              <th>查询</th>
              <th>召回→相关</th>
            </tr>
          </thead>
          <tbody>
            {trace.map((r, i) => (
              <tr key={i}>
                <td>#{String(r.round ?? i + 1)}</td>
                <td>
                  <StrategyBadge s={strLabel(r.strategy)} hyde={Boolean(r.hyde)} />
                </td>
                <td className="rt-query">{String(r.query ?? '').slice(0, 60)}</td>
                <td>
                  {String(r.hits ?? 0)} → <strong>{String(r.relevant_count ?? 0)}</strong>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <RefChips refs={d.refs} label="草稿引用" />
      {conflict?.detected && (
        <div className="banner-error conflict-note">
          ⚠ 融合铁律触发：草稿时效与工具实时 ETA 冲突（草稿:{(conflict.draft_etas ?? []).join('/')} vs 工具:
          {conflict.tool_eta}），整稿弃用回退模板。
        </div>
      )}
      {typeof d.draft_preview === 'string' && d.draft_preview && (
        <blockquote className="draft-quote">
          {d.draft_preview}
          <footer>— {d.draft_source === 'llm' ? 'LLM 草稿（已过措辞整形）' : '模板兜底'}</footer>
        </blockquote>
      )}
    </>
  );
}

function StrategyBadge({ s, hyde }: { s: string; hyde?: boolean }) {
  if (!s) return <span>—</span>;
  return (
    <span className="stack-badge">
      <span className="badge tone-teal">{STRATEGY_LABELS[s] ?? s}</span>
      {hyde && <span className="badge tone-purple">假设文档</span>}
    </span>
  );
}

/** critic 子 agent 卡：裁定 + 问题统计 + 阻断项明细 */
function CriticBody({ d }: { d: Record<string, unknown> }) {
  const verdict = strLabel(d.verdict);
  const blocking = Array.isArray(d.blocking_issues) ? d.blocking_issues : [];
  const pairs = (
    [
      ['issue_count', '问题总数'],
      ['blocking_count', '阻断项'],
      ['advisory_count', '建议项'],
      ['critique_round', '重写轮次'],
    ] as Array<[string, string]>
  )
    .filter(([k]) => d[k] != null)
    .map(([k, label]) => [label, String(d[k])] as [string, string]);
  return (
    <>
      {verdict && (
        <div className={`verdict-banner ${verdict === 'pass' ? 'v-pass' : 'v-rewrite'}`}>
          {verdict === 'pass' ? '✓ 审查通过' : '↻ 打回重写（子 agent 循环 ≤3 轮）'}
        </div>
      )}
      <Chips pairs={pairs} />
      {blocking.length > 0 && (
        <ul className="mini-list block-list">
          {blocking.slice(0, 4).map((b, i) => (
            <li key={i}>⚠ {shortText(b, 120)}</li>
          ))}
        </ul>
      )}
    </>
  );
}

/** 决策门：go/no-go 结论横幅 */
function GateBody({ d }: { d: Record<string, unknown> }) {
  const chosen = strLabel(d.chosen);
  if (!chosen) {
    return <Chips pairs={scalarChips(d)} />;
  }
  const proceed = chosen === 'proceed';
  return (
    <div className={`verdict-banner ${proceed ? 'v-pass' : 'v-halt'}`}>
      {proceed ? '⇢ 决策门放行：进入 Listing 与发布' : '⛔ 决策门叫停：利润/风险不达门槛，流程终止'}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Codex 式紧凑行：思考（决策）/ 工具调用                               */
/* ------------------------------------------------------------------ */

function ThinkRow({ d }: { d: DecisionRecord }) {
  return (
    <details className="think-row">
      <summary>
        <span aria-hidden>💭</span>
        <strong>{DECISION_TYPE_LABELS[d.decision_type] ?? d.decision_type}</strong>
        <span className="cell-faint tr-sub">{d.agent}</span>
        <span className="tr-snippet">{firstLine(d.reasoning, 64)}</span>
      </summary>
      <div className="row-detail">
        <p className="think-reasoning">{d.reasoning}</p>
        <div className="think-chosen">
          结论：<code>{shortText(d.chosen_option, 120)}</code>
        </div>
        <CollapsibleJson data={d.alternatives} label="备选项" />
      </div>
    </details>
  );
}

function ToolRow({ t }: { t: ToolCall }) {
  const failed = ['error', 'failed'].includes((t.status || '').toLowerCase());
  const replayed = t.status === 'replayed';
  return (
    <details className="tool-row">
      <summary>
        <span className={`tr-mark ${failed ? 'm-bad' : replayed ? 'm-replay' : 'm-ok'}`} aria-hidden>
          {failed ? '✗' : replayed ? '↻' : '✓'}
        </span>
        <strong>{toolLabel(t.tool)}</strong>
        <code className="cell-faint tr-sub">{t.tool}</code>
        {t.risk_level === 'high' && <span className="badge tone-red">高风险</span>}
        <span className="tr-snippet">{t.latency_ms != null ? `${t.latency_ms} ms` : ''}</span>
      </summary>
      <div className="row-detail">
        {t.error && <div className="banner-error">调用失败:{t.error}</div>}
        <CollapsibleJson data={t.input_summary} label="输入摘要" />
        <CollapsibleJson data={t.output_summary} label="输出摘要" />
        {t.idempotency_key && <div className="cell-mono cell-faint idem-key">幂等键:{t.idempotency_key}</div>}
      </div>
    </details>
  );
}

/* ------------------------------------------------------------------ */
/* 最终交付卡（Codex 的最终答案，含可点的商城外链）                      */
/* ------------------------------------------------------------------ */

function FinalCard({ status, steps, decisions }: { status: string; steps: WorkflowStep[]; decisions: DecisionRecord[] }) {
  const bySeq = [...steps].sort((a, b) => a.seq - b.seq);
  const gate = bySeq.find((s) => s.node === 'decision_gate');
  const gateChosen = strLabel((gate?.detail as Record<string, unknown> | undefined)?.chosen);
  const publishStep = [...bySeq].reverse().find((s) => s.node === 'publish');
  const pubDetail = (publishStep?.detail ?? {}) as { published?: number; total?: number };
  const retro = [...bySeq].reverse().find((s) => s.node === 'retrospective');
  const keyDecisions = Array.isArray((retro?.detail as Record<string, unknown> | undefined)?.key_decisions)
    ? ((retro!.detail as Record<string, unknown>).key_decisions as unknown[])
    : [];
  const halted = gateChosen === 'halt';

  return (
    <div className={`final-card${status === 'failed' ? ' f-fail' : ''}`}>
      <h3 className="final-title">
        {status === 'completed' ? '🏁 最终交付' : status === 'failed' ? '💥 运行失败' : `运行结束（${status}）`}
      </h3>
      {gateChosen && (
        <p className="final-line">
          决策结论：
          <strong>{halted ? '未过决策门，流程终止' : '通过决策门'}</strong>
        </p>
      )}
      {publishStep && <PublishBlock detail={publishStep.detail} />}
      {pubDetail.total != null && (
        <p className="final-line">
          铺货结果：
          <strong>
            {pubDetail.published ?? 0}/{pubDetail.total} 平台发布成功
          </strong>
          （商品已同步上架到 Mock 商城，点上方「在商城查看」直达商品页）
        </p>
      )}
      {keyDecisions.length > 0 && (
        <div className="final-retro">
          <span className="kb-label">复盘要点</span>
          <ul className="mini-list">
            {keyDecisions.map((k, i) => (
              <li key={i}>• {String(k)}</li>
            ))}
          </ul>
        </div>
      )}
      {decisions.length > 0 && (
        <p className="cell-faint final-meta">
          全程 {decisions.filter((x) => x.decision_type !== 'human_approval').length} 次 agent 自主决策 ·{' '}
          {steps.length} 个执行步骤，全部留痕可审计
        </p>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* 小工具                                                              */
/* ------------------------------------------------------------------ */

function shortText(v: unknown, n: number): string {
  if (v == null) return '';
  const s = typeof v === 'string' ? v : JSON.stringify(v);
  return s.length > n ? `${s.slice(0, n)}…` : s;
}

function firstLine(s: string, n: number): string {
  const line = (s || '').split('\n')[0].trim();
  return line.length > n ? `${line.slice(0, n)}…` : line;
}
