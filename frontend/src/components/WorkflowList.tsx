import { useCallback, useEffect, useState } from 'react';
import type { ApiClient } from '../api';
import type { WorkflowSummary } from '../types';
import { statusLabel, workflowStatusTone } from '../labels';
import StatusBadge from './StatusBadge';
import CreateWorkflowForm from './CreateWorkflowForm';

interface Props {
  client: ApiClient;
  onOpenDetail: (id: string) => void;
}

/** 工作流列表视图:表格 + 「新建工作流」展开表单 */
export default function WorkflowList({ client, onOpenDetail }: Props) {
  const [items, setItems] = useState<WorkflowSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [formOpen, setFormOpen] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      const res = await client.listWorkflows(20);
      setItems(res.items);
    } catch (e) {
      setItems([]);
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [client]);

  // 租户变化(client 重建)时重新拉取列表
  useEffect(() => {
    setItems(null);
    load();
  }, [load]);

  return (
    <section>
      <div className="list-header">
        <div>
          <h2 className="page-title">工作流</h2>
          <p className="page-desc">选品 → 研究 → 测算 → 决策门 → Listing 闭环 → 审批 → 发布 → 运营 → 复盘 的每一次全链路运行。</p>
        </div>
        <div className="btn-row">
          <button className="btn ghost" onClick={load}>
            刷新
          </button>
          <button className="btn primary" onClick={() => setFormOpen((v) => !v)}>
            {formOpen ? '收起表单' : '+ 新建工作流'}
          </button>
        </div>
      </div>

      {formOpen && (
        <CreateWorkflowForm
          client={client}
          onCancel={() => setFormOpen(false)}
          onCreated={(id) => {
            setFormOpen(false);
            onOpenDetail(id); // 创建成功后自动跳转详情,观察异步执行过程
          }}
        />
      )}

      {error && <div className="banner-error">加载失败:{error}</div>}

      <div className="card table-card">
        <table className="table">
          <thead>
            <tr>
              <th style={{ width: '46%' }}>标题</th>
              <th>状态</th>
              <th>工作流 ID</th>
              <th style={{ width: '120px', textAlign: 'right' }}>操作</th>
            </tr>
          </thead>
          <tbody>
            {items === null && (
              <tr>
                <td colSpan={4} className="cell-muted">
                  加载中…
                </td>
              </tr>
            )}
            {items !== null && items.length === 0 && (
              <tr>
                <td colSpan={4} className="cell-muted">
                  暂无工作流 — 点击右上角「新建工作流」,发起一次全链路 Agent 演示。
                </td>
              </tr>
            )}
            {items?.map((wf) => (
              <tr key={wf.id} className="row-click" onClick={() => onOpenDetail(wf.id)}>
                <td className="cell-title">{wf.title}</td>
                <td>
                  <StatusBadge label={statusLabel(wf.status)} tone={workflowStatusTone(wf.status)} />
                </td>
                <td className="cell-mono cell-muted">{wf.id}</td>
                <td style={{ textAlign: 'right' }}>
                  <button
                    className="btn small ghost"
                    onClick={(e) => {
                      e.stopPropagation();
                      onOpenDetail(wf.id);
                    }}
                  >
                    查看详情
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
