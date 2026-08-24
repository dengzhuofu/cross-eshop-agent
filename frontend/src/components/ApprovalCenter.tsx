import { useCallback, useEffect, useState } from 'react';
import type { ApiClient } from '../api';
import { ApiError } from '../api';
import type { ApprovalDecision, ApprovalQueueItem, Tone } from '../types';
import { formatTime, MARKETPLACE_LABELS, statusLabel, workflowStatusTone } from '../labels';
import StatusBadge from './StatusBadge';

interface Props {
  client: ApiClient;
  /** 每次队列刷新后向父级回报待审数量(顶栏徽标消费) */
  onQueueChange?: (count: number) => void;
}

/** 渠道徽章配色:Amazon 蓝 / Shopify 绿 / TikTok Shop 紫,未知渠道回落蓝 */
function marketplaceTone(marketplace: string): Tone {
  switch (marketplace) {
    case 'shopify':
      return 'green';
    case 'tiktok_shop':
      return 'purple';
    default:
      return 'blue';
  }
}

/** 提交中的决策(同一时刻只允许一张卡片在途) */
interface Acting {
  id: string;
  decision: ApprovalDecision;
}

/**
 * 审批中心(HITL 人工闸门):
 * 列出所有 awaiting_approval 的工作流,展示决策门快照(利润率/主供应商/风险标记/
 * 审查轮数/各平台 Listing 草稿),由人对每个工作流做出 通过 或 驳回 的最终决策。
 */
export default function ApprovalCenter({ client, onQueueChange }: Props) {
  const [items, setItems] = useState<ApprovalQueueItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [acting, setActing] = useState<Acting | null>(null);
  const [comments, setComments] = useState<Record<string, string>>({});

  const load = useCallback(async () => {
    setError(null);
    try {
      const res = await client.listApprovals(20);
      setItems(res.items);
      onQueueChange?.(res.items.length);
    } catch (e) {
      setItems([]);
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [client, onQueueChange]);

  // 进入页面(挂载)与租户切换(client 重建)时拉取待审队列
  useEffect(() => {
    setItems(null);
    load();
  }, [load]);

  /** 提交人工决策:成功则移除卡片并重拉列表;409 视为已被处理,提示并刷新 */
  async function decide(item: ApprovalQueueItem, decision: ApprovalDecision) {
    if (acting) return;
    setActing({ id: item.id, decision });
    setError(null);
    setNotice(null);
    try {
      await client.submitApproval(item.id, { decision, comment: comments[item.id]?.trim() ?? '' });
      // 先本地移除卡片给出即时反馈,随后重新拉取权威列表
      setItems((prev) => prev?.filter((x) => x.id !== item.id) ?? prev);
      setComments((prev) => {
        const next = { ...prev };
        delete next[item.id];
        return next;
      });
      setNotice(
        decision === 'approve'
          ? `已通过「${item.title}」,工作流将继续执行多平台发布。`
          : `已驳回「${item.title}」,该工作流不会发布 Listing。`,
      );
      load();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setNotice(`「${item.title}」已不在待审状态(可能已被处理),列表已刷新。`);
        load();
      } else {
        setError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setActing(null);
    }
  }

  return (
    <section>
      <div className="list-header">
        <div>
          <h2 className="page-title">审批中心</h2>
          <p className="page-desc">
            人工审批闸门(HITL):创建时勾选「发布前需人工审批」的工作流会在 Listing 发布前挂起于此,等待人工通过或驳回。
          </p>
        </div>
        <div className="btn-row">
          <button className="btn ghost" onClick={load} disabled={acting !== null}>
            刷新
          </button>
        </div>
      </div>

      {notice && <div className="banner-info">{notice}</div>}
      {error && <div className="banner-error">加载失败:{error}</div>}

      {items === null && (
        <div className="card">
          <p className="cell-muted empty-hint">加载中…</p>
        </div>
      )}

      {items !== null && items.length === 0 && (
        <div className="card">
          <p className="cell-muted empty-hint">
            暂无待审工作流 — 新建工作流时勾选「发布前需人工审批」,其 Listing 将在发布前在此等待人工决策。
          </p>
        </div>
      )}

      {items !== null && items.length > 0 && (
        <div className="approval-list">
          {items.map((item) => {
            const pa = item.pending_approval;
            const busy = acting?.id === item.id;
            return (
              <article key={item.id} className="card approval-card">
                {/* ---- 头部:标题 + ID/时间 + 待审徽章 ---- */}
                <header className="approval-head">
                  <div>
                    <h3 className="approval-title">{item.title}</h3>
                    <div className="cell-mono cell-faint approval-id">
                      {item.id} · {formatTime(item.created_at)}
                    </div>
                  </div>
                  <StatusBadge label={statusLabel('awaiting_approval')} tone={workflowStatusTone('awaiting_approval')} />
                </header>

                {/* ---- 决策门快照指标 ---- */}
                <div className="stat-chips">
                  <span className="chip">
                    选品创意:<strong>{item.product_idea}</strong>
                  </span>
                  <span className="chip">
                    利润率:<strong>{(pa.margin_pct * 100).toFixed(2)}%</strong>
                  </span>
                  <span className="chip">
                    主供应商:<strong className="cell-mono">{pa.primary_supplier}</strong>
                  </span>
                  <span className="chip">
                    目标渠道:
                    <strong>{item.marketplaces.map((m) => MARKETPLACE_LABELS[m] ?? m).join(' / ') || '—'}</strong>
                  </span>
                  <span className="chip">
                    审查轮数:<strong>{pa.critique_rounds}</strong>
                  </span>
                </div>

                {/* ---- 风险标记(警示色) ---- */}
                {pa.risk_flags.length > 0 && (
                  <ul className="risk-flags">
                    {pa.risk_flags.map((flag, i) => (
                      <li key={i} className="risk-flag">
                        {flag}
                      </li>
                    ))}
                  </ul>
                )}

                {/* ---- 各平台 Listing 草稿预览 ---- */}
                {pa.listings.length > 0 && (
                  <div className="listing-grid">
                    {pa.listings.map((listing, i) => (
                      <div key={`${listing.marketplace}-${i}`} className="listing-box">
                        <header className="listing-head">
                          <StatusBadge
                            label={MARKETPLACE_LABELS[listing.marketplace] ?? listing.marketplace}
                            tone={marketplaceTone(listing.marketplace)}
                          />
                          <span className="listing-label">Listing 草稿预览</span>
                        </header>
                        <div className="listing-title">{listing.title}</div>
                        <ul className="listing-bullets">
                          {listing.bullets.map((bullet, j) => (
                            <li key={j}>{bullet}</li>
                          ))}
                        </ul>
                        {listing.claim && (
                          <p className="listing-claim">
                            <span className="claim-tag">claim</span>
                            {listing.claim}
                          </p>
                        )}
                      </div>
                    ))}
                  </div>
                )}

                {/* ---- 附言 + 通过/驳回 ---- */}
                <footer className="approval-actions">
                  <input
                    type="text"
                    className="comment-input"
                    placeholder="附言(可选),将随决策写入审计记录"
                    value={comments[item.id] ?? ''}
                    disabled={busy}
                    onChange={(e) => setComments((prev) => ({ ...prev, [item.id]: e.target.value }))}
                  />
                  <button className="btn primary" disabled={busy} onClick={() => decide(item, 'approve')}>
                    {busy && acting?.decision === 'approve' ? '提交中…' : '通过'}
                  </button>
                  <button className="btn danger" disabled={busy} onClick={() => decide(item, 'reject')}>
                    {busy && acting?.decision === 'reject' ? '提交中…' : '驳回'}
                  </button>
                </footer>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}
