import { useCallback, useEffect, useMemo, useState } from 'react';
import { ApiClient } from './api';
import { TENANTS } from './labels';
import TopBar from './components/TopBar';
import WorkflowList from './components/WorkflowList';
import WorkflowDetail from './components/WorkflowDetail';
import ApprovalCenter from './components/ApprovalCenter';

/** 视图路由:三个视图(列表 / 详情 / 审批中心),无需引入路由库。 */
export type AppView = { kind: 'list' } | { kind: 'detail'; id: string } | { kind: 'approvals' };

export default function App() {
  const [tenantId, setTenantId] = useState<string>(TENANTS[0].id);
  const [view, setView] = useState<AppView>({ kind: 'list' });
  const [backendOk, setBackendOk] = useState<boolean | null>(null);
  const [pendingApprovals, setPendingApprovals] = useState<number | null>(null);

  // 租户切换 = 重建 client:之后所有请求自动携带新的 X-Tenant-Id 头
  const client = useMemo(() => new ApiClient(tenantId), [tenantId]);

  // 待审数量徽标:进入应用/切换租户时拉取一次;
  // 进入审批中心或做出决策后,由 ApprovalCenter 通过 onQueueChange 回报最新数量
  useEffect(() => {
    let alive = true;
    client
      .listApprovals()
      .then((res) => alive && setPendingApprovals(res.items.length))
      .catch(() => alive && setPendingApprovals(null));
    return () => {
      alive = false;
    };
  }, [client]);

  const handleQueueChange = useCallback((count: number) => setPendingApprovals(count), []);

  // 切换租户后回到列表,避免展示另一个租户的工作流详情造成混淆
  function switchTenant(id: string) {
    setTenantId(id);
    setView({ kind: 'list' });
  }

  // 后端健康指示灯
  useEffect(() => {
    let alive = true;
    client
      .healthz()
      .then(() => alive && setBackendOk(true))
      .catch(() => alive && setBackendOk(false));
    return () => {
      alive = false;
    };
  }, [client]);

  return (
    <div className="app-shell">
      <TopBar
        tenants={TENANTS}
        tenantId={tenantId}
        onTenantChange={switchTenant}
        view={view}
        onViewChange={setView}
        backendOk={backendOk}
        approvalCount={pendingApprovals}
      />

      <main className="page">
        {view.kind === 'list' ? (
          <WorkflowList client={client} onOpenDetail={(id) => setView({ kind: 'detail', id })} />
        ) : view.kind === 'detail' ? (
          <WorkflowDetail key={`${tenantId}:${view.id}`} client={client} workflowId={view.id} onBack={() => setView({ kind: 'list' })} />
        ) : (
          <ApprovalCenter client={client} onQueueChange={handleQueueChange} />
        )}
      </main>

      <footer className="footer">
        跨境电商全链路 Agent 平台 · 工作流可观测面板 — LangGraph 多 Agent 编排 / 决策可审计 / 工具调用幂等
      </footer>
    </div>
  );
}
