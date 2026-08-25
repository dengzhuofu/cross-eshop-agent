import { useState } from 'react';
import type { ApiClient } from '../api';
import type { FeedbackCreated } from '../types';
import { FEEDBACK_CATEGORY_LABELS, FEEDBACK_SINK_LABELS, feedbackCategoryTone } from '../labels';
import StatusBadge from './StatusBadge';

interface Props {
  client?: ApiClient;
  workflowId?: string;
  /** 反馈目标:客服草稿 / Listing 文案 */
  targetType: 'support_draft' | 'listing_copy';
  /** 定位被反馈产物(用步骤 seq) */
  targetKey: string;
}

/**
 * 步骤级反馈组件(M10 反馈-分诊-沉淀闭环的入口):
 * 👍/👎 + 可选评论 → POST /api/v1/feedback → 就地展示分诊子 agent 的归类归因
 * 与沉淀去向。提交后收起输入区、展示结果徽标;反馈文本是不可信输入,
 * 脱敏由后端 scrub_untrusted 统一处理。
 */
export default function FeedbackWidget({ client, workflowId, targetType, targetKey }: Props) {
  const [open, setOpen] = useState(false);
  const [comment, setComment] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<FeedbackCreated | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (!client || !workflowId) return null;

  async function submit(verdict: 'helpful' | 'unhelpful') {
    if (!client || !workflowId || busy) return;
    setBusy(true);
    setError(null);
    try {
      const res = await client.createFeedback({
        workflow_id: workflowId,
        target_type: targetType,
        target_key: targetKey,
        verdict,
        comment: comment.trim() || undefined,
      });
      setResult(res);
      setOpen(false);
      setComment('');
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (result) {
    return (
      <div className="fb-widget fb-done">
        <StatusBadge
          label={FEEDBACK_CATEGORY_LABELS[result.category] ?? result.category}
          tone={feedbackCategoryTone(result.category)}
        />
        <span className="cell-faint">已沉淀:{FEEDBACK_SINK_LABELS[result.sink] ?? result.sink}</span>
        {result.sink_ref && (
          <span className="cell-mono cell-faint" title={result.sink_ref}>
            {result.sink.startsWith('knowledge_candidate') ? `候选 ${result.sink_ref.slice(0, 8)}…` : ''}
          </span>
        )}
        <button type="button" className="btn small ghost" onClick={() => setResult(null)}>
          再反馈
        </button>
      </div>
    );
  }

  return (
    <div className="fb-widget">
      {!open ? (
        <>
          <button
            type="button"
            className="btn small ghost"
            disabled={busy}
            title="这条产出有帮助"
            onClick={() => void submit('helpful')}
          >
            👍 有帮助
          </button>
          <button
            type="button"
            className="btn small ghost"
            disabled={busy}
            title="这条产出有问题——分诊后自动沉淀改进"
            onClick={() => setOpen(true)}
          >
            👎 有问题
          </button>
          {error && <span className="fb-error">{error}</span>}
        </>
      ) : (
        <div className="fb-input-row">
          <input
            className="fb-input"
            value={comment}
            placeholder="什么问题?(可选,如:知识库里查不到…/答非所问/保证100%…)"
            onChange={(e) => setComment(e.target.value)}
            maxLength={500}
          />
          <button
            type="button"
            className="btn small"
            disabled={busy || comment.trim().length === 0}
            title="至少写一句问题描述,分诊才有依据"
            onClick={() => void submit('unhelpful')}
          >
            提交
          </button>
          <button type="button" className="btn small ghost" disabled={busy} onClick={() => setOpen(false)}>
            取消
          </button>
        </div>
      )}
    </div>
  );
}
