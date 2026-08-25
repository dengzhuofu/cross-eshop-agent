import { useCallback, useEffect, useMemo, useState } from 'react';
import type { ApiClient } from '../api';
import type { BadCase, BadCaseTerminalStatus, FeedbackItem, KnowledgeCandidate } from '../types';
import {
  BADCASE_CATEGORY_LABELS,
  BADCASE_STATUS_LABELS,
  DETECTOR_LABELS,
  FEEDBACK_CATEGORY_LABELS,
  FEEDBACK_SINK_LABELS,
  KNOWLEDGE_REVIEW_LABELS,
  badCaseCategoryTone,
  badCaseSeverityTone,
  badCaseStatusTone,
  feedbackCategoryTone,
  formatTime,
} from '../labels';
import StatusBadge from './StatusBadge';
import CollapsibleJson from './CollapsibleJson';

interface Props {
  client: ApiClient;
  /** 点击卡片跳转到对应工作流的详情页(沿用 App 视图路由) */
  onOpenDetail: (workflowId: string) => void;
}

/** 类别筛选 chips:全部 + PRD 八类 */
const CATEGORY_FILTERS: Array<{ key: string; label: string }> = [
  { key: 'all', label: '全部' },
  ...Object.entries(BADCASE_CATEGORY_LABELS).map(([key, label]) => ({ key, label })),
];

/** 终态集合(PRD §20.4):已流转到终态的坏例不再提供操作按钮 */
const TERMINAL_STATUSES: ReadonlySet<string> = new Set(['resolved', 'escalated', 'aborted']);

/**
 * 顶部统计小卡数据:总数 / 高危(high) / 已隔离(quarantined)。
 */
function buildStats(items: BadCase[]) {
  return [
    { key: 'total', label: '坏例总数', num: items.length, toneClass: 'tone-blue' },
    {
      key: 'high',
      label: '高危(high)',
      num: items.filter((c) => c.severity?.toLowerCase() === 'high').length,
      toneClass: 'tone-red',
    },
    {
      key: 'quarantined',
      label: '已隔离(quarantined)',
      num: items.filter((c) => c.status?.toLowerCase() === 'quarantined').length,
      toneClass: 'tone-amber',
    },
  ];
}

/**
 * Bad Case 面板(M8 防线可观测):
 * 拉取当前租户的坏例记录,顶部统计 + 类别 chips 前端过滤 + 坏例卡片列表;
 * 点击卡片跳转到对应工作流详情页回溯现场。
 */
export default function BadCasePanel({ client, onOpenDetail }: Props) {
  const [items, setItems] = useState<BadCase[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [category, setCategory] = useState<string>('all');
  /** 正在提交处置请求的坏例 id(防双击重复提交) */
  const [busyId, setBusyId] = useState<string | null>(null);
  // ---- M10 反馈-分诊-沉淀闭环 ----
  const [feedback, setFeedback] = useState<FeedbackItem[] | null>(null);
  const [candidates, setCandidates] = useState<KnowledgeCandidate[] | null>(null);
  const [busyKid, setBusyKid] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [bc, fb, cand] = await Promise.all([
        client.listBadCases({ limit: 100 }),
        client.listFeedback(30).catch(() => ({ items: [] as FeedbackItem[] })),
        client.listKnowledgeCandidates(30).catch(() => ({ items: [] as KnowledgeCandidate[] })),
      ]);
      setItems(bc.items);
      setFeedback(fb.items);
      setCandidates(cand.items);
    } catch (e) {
      setItems([]);
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [client]);

  /**
   * 处置闭环(PRD §20.4):把坏例流转到终态并就地刷新列表。
   * 按钮在卡片 onClick 内部,必须 stopPropagation 防止触发跳转详情。
   */
  const handleDispose = useCallback(
    async (item: BadCase, status: BadCaseTerminalStatus) => {
      if (busyId) return;
      setBusyId(item.id);
      setError(null);
      try {
        await client.updateBadCaseStatus(item.id, { status });
        await load();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusyId(null);
      }
    },
    [busyId, client, load],
  );

  /** M10 候选知识审批:approve 进检索池 / reject 删除,就地刷新 */
  const handleReview = useCallback(
    async (kid: string, action: 'approve' | 'reject') => {
      if (busyKid) return;
      setBusyKid(kid);
      setError(null);
      try {
        await client.reviewKnowledge(kid, { action });
        await load();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusyKid(null);
      }
    },
    [busyKid, client, load],
  );

  // 进入页面与租户切换(client 重建)时重新拉取,并重置筛选
  useEffect(() => {
    setItems(null);
    setCategory('all');
    load();
  }, [load]);

  // 类别筛选为纯前端过滤,不重新请求
  const filtered = useMemo(() => {
    if (!items) return null;
    if (category === 'all') return items;
    return items.filter((c) => c.category === category);
  }, [items, category]);

  const stats = useMemo(() => buildStats(items ?? []), [items]);

  return (
    <section>
      <div className="list-header">
        <div>
          <h2 className="page-title">Bad Case 面板</h2>
          <p className="page-desc">
            坏例防线可观测:输入注入、输出失控、记忆投毒等八类异常由各链路扫描器自动检出并隔离;卡片底部可直接「标记已处置 / 升级处理」完成闭环(PRD §20.4),点击卡片回溯对应工作流现场。
          </p>
        </div>
        <div className="btn-row">
          <button className="btn ghost" onClick={load}>
            刷新
          </button>
        </div>
      </div>

      {/* ---- 顶部统计小卡 ---- */}
      <div className="badcase-stats">
        {stats.map((s) => (
          <div key={s.key} className={`card stat-card ${s.toneClass}`}>
            <div className="stat-num">{items === null ? '—' : s.num}</div>
            <div className="stat-label">{s.label}</div>
          </div>
        ))}
      </div>

      {error && <div className="banner-error">加载失败:{error}</div>}

      {/* ---- 类别筛选 chips ---- */}
      {items !== null && items.length > 0 && (
        <div className="badcase-filters" role="tablist" aria-label="坏例类别筛选">
          {CATEGORY_FILTERS.map((f) => {
            const count =
              f.key === 'all'
                ? items.length
                : items.filter((c) => c.category === f.key).length;
            return (
              <button
                key={f.key}
                role="tab"
                aria-selected={category === f.key}
                title={`${f.label} · ${count} 条`}
                className={`filter-chip${category === f.key ? ' active' : ''}`}
                onClick={() => setCategory(f.key)}
              >
                {f.label}
                <span className="filter-count">{count}</span>
              </button>
            );
          })}
        </div>
      )}

      {/* ---- 坏例卡片列表 ---- */}
      {filtered === null && (
        <div className="card">
          <p className="cell-muted empty-hint">加载中…</p>
        </div>
      )}

      {filtered !== null && filtered.length === 0 && (
        <div className="card">
          <p className="cell-muted empty-hint">
            {items !== null && items.length > 0
              ? '该类别下暂无坏例记录。'
              : '暂无坏例记录——防线完好'}
          </p>
        </div>
      )}

      {filtered !== null && filtered.length > 0 && (
        <div className="badcase-list">
          {filtered.map((item) => {
            const clickable = Boolean(item.workflow_id);
            const terminal = TERMINAL_STATUSES.has(item.status?.toLowerCase() ?? item.status);
            return (
              <article
                key={item.id}
                className={`card badcase-card bar-${badCaseSeverityTone(item.severity)}${clickable ? ' row-click' : ''}`}
                onClick={clickable ? () => onOpenDetail(item.workflow_id) : undefined}
              >
                <header className="badcase-head">
                  <div className="badcase-badges">
                    <StatusBadge
                      label={item.severity?.toUpperCase?.() ?? item.severity}
                      tone={badCaseSeverityTone(item.severity)}
                    />
                    <StatusBadge
                      label={BADCASE_STATUS_LABELS[item.status] ?? item.status}
                      tone={badCaseStatusTone(item.status)}
                    />
                    <StatusBadge
                      label={BADCASE_CATEGORY_LABELS[item.category] ?? item.category}
                      tone={badCaseCategoryTone(item.category)}
                    />
                  </div>
                  <time className="dc-time">{formatTime(item.created_at)}</time>
                </header>

                <p className="badcase-summary">{item.summary || '—'}</p>

                <CollapsibleJson data={item.evidence} label="查看证据(evidence)" />

                <footer className="badcase-foot">
                  <span className="cell-mono cell-faint">detector:{DETECTOR_LABELS[item.detector] ?? item.detector}</span>
                  {!terminal && (
                    <span
                      className="btn-row"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <button
                        type="button"
                        className="btn small ghost"
                        disabled={busyId === item.id}
                        onClick={(e) => {
                          e.stopPropagation();
                          void handleDispose(item, 'resolved');
                        }}
                        title="复核完毕,标记为已处置(resolved)"
                      >
                        标记已处置
                      </button>
                      <button
                        type="button"
                        className="btn small ghost"
                        disabled={busyId === item.id}
                        onClick={(e) => {
                          e.stopPropagation();
                          void handleDispose(item, 'escalated');
                        }}
                        title="需要人工/上游介入,升级处理(escalated)"
                      >
                        升级处理
                      </button>
                    </span>
                  )}
                  {terminal && item.outcome && (
                    <span className="cell-mono cell-faint" title={item.outcome}>
                      处置留痕:{item.outcome}
                    </span>
                  )}
                  {clickable && (
                    <span className="cell-mono cell-faint badcase-wfid" title={item.workflow_id}>
                      工作流 {item.workflow_id} →
                    </span>
                  )}
                </footer>
              </article>
            );
          })}
        </div>
      )}

      {/* ---- M10 反馈-分诊-沉淀闭环 ---- */}
      {candidates !== null && candidates.length > 0 && (
        <section className="fb-section">
          <h3 className="section-title">待审候选知识（{candidates.length}）</h3>
          <p className="page-desc">
            来自用户反馈的知识缺口由分诊子 agent 起草为候选条目；审批通过才进入 RAG
            检索池，驳回则删除——反馈永远不会未经把关直接改写语料。
          </p>
          <div className="badcase-list">
            {candidates.map((c) => (
              <article key={c.id} className="card badcase-card bar-blue">
                <header className="badcase-head">
                  <div className="badcase-badges">
                    <StatusBadge label="候选知识" tone="blue" />
                    <StatusBadge label={c.category} tone="gray" />
                  </div>
                  <time className="dc-time">{formatTime(c.created_at)}</time>
                </header>
                <p className="badcase-summary">{c.title}</p>
                <CollapsibleJson data={{ ref: c.ref, content: c.content }} label="查看内容" />
                <footer className="badcase-foot">
                  <span className="cell-mono cell-faint">{c.ref}</span>
                  <span className="btn-row">
                    <button
                      type="button"
                      className="btn small"
                      disabled={busyKid === c.id}
                      onClick={() => void handleReview(c.id, 'approve')}
                      title="通过后该条目立即进入 RAG 检索池"
                    >
                      {KNOWLEDGE_REVIEW_LABELS.approve}
                    </button>
                    <button
                      type="button"
                      className="btn small ghost"
                      disabled={busyKid === c.id}
                      onClick={() => void handleReview(c.id, 'reject')}
                      title="驳回并删除该候选条目"
                    >
                      {KNOWLEDGE_REVIEW_LABELS.reject}
                    </button>
                  </span>
                </footer>
              </article>
            ))}
          </div>
        </section>
      )}

      {feedback !== null && feedback.length > 0 && (
        <section className="fb-section">
          <h3 className="section-title">反馈分诊记录（{feedback.length}）</h3>
          <p className="page-desc">
            用户对 agent 产物的实时反馈与分诊子 agent 的归类归因——每条都路由到了明确的沉淀位置。
          </p>
          <div className="badcase-list">
            {feedback.map((f) => {
              // 闭包内属性访问不保留窄化，先落局部变量
              const wid = f.workflow_id;
              return (
              <article
                key={f.id}
                className={`card badcase-card${wid ? ' row-click' : ''}`}
                onClick={wid ? () => onOpenDetail(wid) : undefined}
              >
                <header className="badcase-head">
                  <div className="badcase-badges">
                    <StatusBadge
                      label={f.verdict === 'helpful' ? '👍 有帮助' : '👎 有问题'}
                      tone={f.verdict === 'helpful' ? 'green' : 'red'}
                    />
                    {f.triage && (
                      <StatusBadge
                        label={FEEDBACK_CATEGORY_LABELS[f.triage.category] ?? f.triage.category}
                        tone={feedbackCategoryTone(f.triage.category)}
                      />
                    )}
                    {f.triage && (
                      <StatusBadge
                        label={`→ ${FEEDBACK_SINK_LABELS[f.triage.sink] ?? f.triage.sink}`}
                        tone="purple"
                      />
                    )}
                  </div>
                  <time className="dc-time">{formatTime(f.created_at)}</time>
                </header>
                <p className="badcase-summary">
                  {[f.comment, f.quote].filter(Boolean).join(' ｜ ') || '—'}
                </p>
                {f.triage?.root_cause && (
                  <p className="cell-faint">归因:{f.triage.root_cause}</p>
                )}
                <footer className="badcase-foot">
                  <span className="cell-mono cell-faint">
                    分诊来源:{f.triage?.source ?? '-'} · 目标:{f.target_type}
                    {f.target_key ? `#${f.target_key}` : ''}
                  </span>
                  {f.workflow_id && (
                    <span className="cell-mono cell-faint badcase-wfid">
                      工作流 {f.workflow_id} →
                    </span>
                  )}
                </footer>
              </article>
              );
            })}
          </div>
        </section>
      )}
    </section>
  );
}
