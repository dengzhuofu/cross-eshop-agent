import { useCallback, useEffect, useMemo, useState } from 'react';
import type { ApiClient } from '../api';
import type { BadCase, BadCaseTerminalStatus } from '../types';
import {
  BADCASE_CATEGORY_LABELS,
  BADCASE_STATUS_LABELS,
  DETECTOR_LABELS,
  badCaseCategoryTone,
  badCaseSeverityTone,
  badCaseStatusTone,
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

  const load = useCallback(async () => {
    setError(null);
    try {
      const res = await client.listBadCases({ limit: 100 });
      setItems(res.items);
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
    </section>
  );
}
