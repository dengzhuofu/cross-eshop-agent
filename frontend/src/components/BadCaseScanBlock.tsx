import type { BadCaseScanDetail } from '../types';
import {
  BADCASE_CATEGORY_LABELS,
  BADCASE_ORIGIN_LABELS,
  DETECTOR_LABELS,
  badCaseOriginTone,
  badCaseSeverityTone,
} from '../labels';
import StatusBadge from './StatusBadge';

/**
 * 从 evidence / findings 等结构中容错提取 patterns / phrases 标签。
 * 后端 detail 字段可能略有出入,这里对数组 / 对象 / 缺失均做兜底,只挑能展示的。
 */
function extractTags(source: unknown): string[] {
  if (source == null) return [];
  const list = Array.isArray(source) ? source : [source];
  const tags: string[] = [];
  for (const entry of list) {
    if (entry == null || typeof entry !== 'object') continue;
    const rec = entry as Record<string, unknown>;
    for (const key of ['patterns', 'phrases']) {
      const value = rec[key];
      if (!Array.isArray(value)) continue;
      for (const item of value) {
        tags.push(typeof item === 'string' ? item : JSON.stringify(item));
      }
    }
  }
  return [...new Set(tags)];
}

function TagList({ tags }: { tags: string[] }) {
  if (tags.length === 0) return null;
  return (
    <ul className="tag-list">
      {tags.map((tag, i) => (
        <li key={i} className="tag">
          {tag}
        </li>
      ))}
    </ul>
  );
}

/**
 * 坏例防线扫描步骤(node="bad_case_scan")的结构化渲染:
 * 盾牌式警示配色 + 扫描来源(planner/listing/retrospective)中文徽标,
 * 命中项逐条展示 类别/检测器/严重度/摘要,patterns 与 phrases 以标签呈现;
 * 完整原始 detail 仍由外层 CollapsibleJson 兜底,保证可审计。
 */
export default function BadCaseScanBlock({ detail }: { detail: unknown }) {
  // 后端字段可能出入,统一按可选形状读取
  const scan: BadCaseScanDetail = (detail ?? {}) as BadCaseScanDetail;
  const origin = typeof scan.origin === 'string' ? scan.origin : '';
  const hits = Array.isArray(scan.hits) ? scan.hits : [];
  const hitTags = extractTags(hits.map((h) => h?.evidence));
  const findingTags = extractTags(scan.findings);
  const findingsText =
    typeof scan.findings === 'string' && scan.findings.trim() ? scan.findings.trim() : '';

  const nothingParsed = hits.length === 0 && hitTags.length === 0 && findingTags.length === 0 && !findingsText;
  if (nothingParsed) return null; // 完全解析不出结构时交给 CollapsibleJson 展示原始 JSON

  return (
    <div className="scan-block">
      <div className="scan-origin">
        <span className={`badge tone-${badCaseOriginTone(origin)}`}>
          {origin ? `防线扫描 · ${BADCASE_ORIGIN_LABELS[origin] ?? origin}` : '防线扫描'}
        </span>
        {hits.length > 0 && (
          <span className="scan-hit-count">{hits.length} 处命中</span>
        )}
      </div>

      {hits.length > 0 && (
        <div className="scan-hits">
          {hits.map((hit, i) => {
            const severity = typeof hit?.severity === 'string' ? hit.severity : '';
            const detector = typeof hit?.detector === 'string' ? hit.detector : '';
            const category = typeof hit?.category === 'string' ? hit.category : '';
            return (
              <div key={i} className="scan-hit">
                <div className="scan-hit-head">
                  {severity && (
                    <StatusBadge label={severity.toUpperCase()} tone={badCaseSeverityTone(severity)} />
                  )}
                  {category && (
                    <StatusBadge
                      label={BADCASE_CATEGORY_LABELS[category] ?? category}
                      tone="gray"
                    />
                  )}
                  {detector && (
                    <span className="scan-detector" title={detector}>
                      {DETECTOR_LABELS[detector] ?? detector}
                    </span>
                  )}
                </div>
                {typeof hit?.summary === 'string' && hit.summary && (
                  <p className="scan-summary">{hit.summary}</p>
                )}
                <TagList tags={extractTags(hit?.evidence)} />
              </div>
            );
          })}
        </div>
      )}

      {findingTags.length > 0 && (
        <div className="scan-findings">
          <span className="choice-label">findings 汇总</span>
          <TagList tags={findingTags} />
        </div>
      )}
      {findingsText && <p className="scan-summary scan-findings-text">{findingsText}</p>}
    </div>
  );
}
