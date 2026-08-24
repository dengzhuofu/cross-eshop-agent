import type { DecisionRecord, Tone } from '../types';
import { DECISION_TYPE_LABELS, decisionTypeTone, formatTime } from '../labels';

/** 把 chosen_option / alternatives 统一成数组渲染(后端可能给字符串或数组)。 */
function toList(value: unknown): unknown[] {
  if (value == null) return [];
  if (Array.isArray(value)) return value;
  return [value];
}

/** 值渲染:对象美化成 JSON,标量直接展示。 */
function ValueBlock({ value }: { value: unknown }) {
  const text = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  return <pre className="value-block">{text}</pre>;
}

/**
 * 决策卡片流 —— 「Agent 自主决策可审计」的核心卖点:
 * 每张卡片带决策类型色条、中文类型徽章、推理过程,
 * 以及 最终选择 vs 备选方案 的对比区。
 */
export default function DecisionCards({ decisions, loading }: { decisions: DecisionRecord[]; loading: boolean }) {
  return (
    <div className="card panel">
      <h3 className="section-title">Agent 决策流</h3>

      {loading && <p className="cell-muted empty-hint">加载中…</p>}
      {!loading && decisions.length === 0 && <p className="cell-muted empty-hint">暂无决策记录。</p>}

      <div className="decision-list">
        {decisions.map((d, i) => {
          const tone: Tone = decisionTypeTone(d.decision_type);
          const alts = toList(d.alternatives);
          return (
            <article key={i} className={`decision-card bar-${tone}`}>
              <header className="dc-head">
                <span className={`badge tone-${tone}`}>
                  {DECISION_TYPE_LABELS[d.decision_type] ?? d.decision_type}
                </span>
                <span className="agent-chip">@{d.agent}</span>
                <time className="dc-time">{formatTime(d.created_at)}</time>
              </header>

              <p className="reasoning">{d.reasoning}</p>

              <div className={`compare-grid${alts.length === 0 ? ' single' : ''}`}>
                <div className="choice-box chosen">
                  <span className="choice-label">最终选择</span>
                  <ValueBlock value={d.chosen_option} />
                </div>
                {alts.length > 0 && (
                  <div className="choice-box alternatives">
                    <span className="choice-label">备选方案({alts.length})</span>
                    {alts.map((alt, j) => (
                      <ValueBlock key={j} value={alt} />
                    ))}
                  </div>
                )}
              </div>
            </article>
          );
        })}
      </div>
    </div>
  );
}
