import type { Tenant } from '../types';
import type { AppView } from '../App';

interface Props {
  tenants: Tenant[];
  tenantId: string;
  onTenantChange: (id: string) => void;
  view: AppView;
  onViewChange: (view: AppView) => void;
  backendOk: boolean | null;
}

/** 顶栏:品牌区 + 租户切换器 + 视图切换 + 后端健康指示灯 */
export default function TopBar({ tenants, tenantId, onTenantChange, view, onViewChange, backendOk }: Props) {
  return (
    <header className="topbar">
      <div className="topbar-inner">
        <div className="brand">
          <div className="brand-mark">ES</div>
          <div>
            <div className="brand-title">跨境电商全链路 Agent 平台</div>
            <div className="brand-sub">工作流可观测面板 · 控制台</div>
          </div>
        </div>

        {/* 租户切换器:切换后所有请求自动携带新租户头 */}
        <div className="tenant-switch" role="tablist" aria-label="租户切换">
          {tenants.map((t) => (
            <button
              key={t.id}
              role="tab"
              aria-selected={t.id === tenantId}
              className={`tenant-pill${t.id === tenantId ? ' active' : ''}`}
              onClick={() => onTenantChange(t.id)}
              title={t.id}
            >
              {t.name}
            </button>
          ))}
        </div>

        <nav className="nav-tabs">
          <button
            className={`tab${view.kind === 'list' ? ' active' : ''}`}
            onClick={() => onViewChange({ kind: 'list' })}
          >
            工作流列表
          </button>
          <button
            className={`tab${view.kind === 'detail' ? ' active' : ''}`}
            disabled={view.kind !== 'detail'}
            onClick={() => view.kind === 'detail' && onViewChange(view)}
          >
            运行详情
          </button>
        </nav>

        <div className="health" title="GET /healthz">
          <span
            className={`health-dot ${backendOk === true ? 'ok' : backendOk === false ? 'down' : 'unknown'}`}
          />
          <span className="health-text">{backendOk === true ? '后端在线' : backendOk === false ? '后端离线' : '检测中…'}</span>
        </div>
      </div>
    </header>
  );
}
