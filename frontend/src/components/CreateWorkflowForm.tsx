import { useState } from 'react';
import type { ApiClient } from '../api';
import type { Marketplace, TargetMarket, RiskPreference } from '../types';

interface Props {
  client: ApiClient;
  onCreated: (id: string) => void;
  onCancel: () => void;
}

const MARKETPLACE_OPTIONS: { value: Marketplace; label: string }[] = [
  { value: 'amazon', label: 'Amazon' },
  { value: 'shopify', label: 'Shopify' },
  { value: 'tiktok_shop', label: 'TikTok Shop' },
];

const TARGET_MARKETS: TargetMarket[] = ['US', 'UK', 'DE', 'JP'];
const RISK_PREFERENCES: { value: RiskPreference; label: string }[] = [
  { value: 'conservative', label: '保守 conservative' },
  { value: 'balanced', label: '均衡 balanced' },
  { value: 'aggressive', label: '激进 aggressive' },
];

/** 新建工作流表单:提交后后端异步执行,父组件负责跳转详情 */
export default function CreateWorkflowForm({ client, onCreated, onCancel }: Props) {
  const [productIdea, setProductIdea] = useState('可折叠床底收纳箱');
  const [marketplaces, setMarketplaces] = useState<Marketplace[]>(['amazon', 'tiktok_shop']);
  const [targetMarket, setTargetMarket] = useState<TargetMarket>('US');
  const [riskPreference, setRiskPreference] = useState<RiskPreference>('balanced');
  // 勾选后请求体带 auto_approve:false,工作流将在发布前挂起等待人工审批(HITL)
  const [manualApprove, setManualApprove] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function toggleMarketplace(m: Marketplace) {
    setMarketplaces((prev) => (prev.includes(m) ? prev.filter((x) => x !== m) : [...prev, m]));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!productIdea.trim()) {
      setError('请填写选品创意');
      return;
    }
    if (marketplaces.length === 0) {
      setError('请至少勾选一个目标渠道');
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const wf = await client.createWorkflow({
        product_idea: productIdea.trim(),
        marketplaces,
        target_market: targetMarket,
        risk_preference: riskPreference,
        auto_approve: !manualApprove,
      });
      onCreated(wf.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setSubmitting(false);
    }
  }

  return (
    <form className="card form-card" onSubmit={handleSubmit}>
      <h3 className="form-title">新建工作流</h3>

      <div className="form-grid">
        <label className="field span-2">
          <span className="field-label">选品创意 product_idea *</span>
          <input
            type="text"
            value={productIdea}
            placeholder="例如:可折叠床底收纳箱"
            onChange={(e) => setProductIdea(e.target.value)}
          />
        </label>

        <div className="field">
          <span className="field-label">目标渠道 marketplaces *(多选)</span>
          <div className="checkbox-group">
            {MARKETPLACE_OPTIONS.map((opt) => (
              <label key={opt.value} className={`check-item${marketplaces.includes(opt.value) ? ' checked' : ''}`}>
                <input
                  type="checkbox"
                  checked={marketplaces.includes(opt.value)}
                  onChange={() => toggleMarketplace(opt.value)}
                />
                {opt.label}
              </label>
            ))}
          </div>
        </div>

        <div className="field-pair">
          <label className="field">
            <span className="field-label">目标市场 target_market</span>
            <select value={targetMarket} onChange={(e) => setTargetMarket(e.target.value as TargetMarket)}>
              {TARGET_MARKETS.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </label>

          <label className="field">
            <span className="field-label">风险偏好 risk_preference</span>
            <select
              value={riskPreference}
              onChange={(e) => setRiskPreference(e.target.value as RiskPreference)}
            >
              {RISK_PREFERENCES.map((r) => (
                <option key={r.value} value={r.value}>
                  {r.label}
                </option>
              ))}
            </select>
          </label>
        </div>

        <div className="field">
          <span className="field-label">审批策略 auto_approve</span>
          <label className={`check-item${manualApprove ? ' checked' : ''}`}>
            <input
              type="checkbox"
              checked={manualApprove}
              onChange={(e) => setManualApprove(e.target.checked)}
            />
            发布前需人工审批
          </label>
        </div>
      </div>

      {error && <div className="banner-error">{error}</div>}

      <div className="btn-row form-actions">
        <button type="button" className="btn ghost" onClick={onCancel} disabled={submitting}>
          取消
        </button>
        <button type="submit" className="btn primary" disabled={submitting}>
          {submitting ? '创建中…' : '创建并启动 Agent 链路'}
        </button>
      </div>
    </form>
  );
}
