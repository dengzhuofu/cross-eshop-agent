import type {
  WorkflowCreatePayload,
  WorkflowCreated,
  WorkflowDetailData,
  WorkflowSummary,
  TraceResponse,
  ApprovalQueueItem,
  ApprovalRequestPayload,
  ApprovalResult,
  BadCase,
  BadCaseStatusPayload,
  BadCaseStatusResult,
} from './types';

/** GET /api/v1/badcases 的查询参数(全部可选) */
export interface BadCaseQuery {
  limit?: number;
  workflow_id?: string;
  category?: string;
}

/** 非 2xx 响应抛出的错误,携带状态码与后端 detail 信息。 */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = 'ApiError';
  }
}

/**
 * 类型化 API 客户端。
 *
 * 关键点:每个请求都会注入 `X-Tenant-Id` 请求头 —— 后端据此做行级多租户隔离,
 * 前端切换租户时只需更换 client 实例(setTenant / useMemo 重建)即可,
 * 业务组件完全不感知租户细节,也不必在每处调用手工传头。
 */
export class ApiClient {
  private tenantId: string;

  constructor(tenantId: string) {
    this.tenantId = tenantId;
  }

  setTenant(tenantId: string): void {
    this.tenantId = tenantId;
  }

  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    const res = await fetch(path, {
      ...init,
      headers: {
        'Content-Type': 'application/json',
        // 租户头注入:所有请求统一带上,保证列表/详情/trace 都在同一租户作用域内
        'X-Tenant-Id': this.tenantId,
        ...(init?.headers ?? {}),
      },
    });

    if (!res.ok) {
      let message = `${res.status} ${res.statusText}`;
      try {
        const body = (await res.json()) as { detail?: unknown };
        if (body?.detail) {
          message = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
        }
      } catch {
        /* 响应体不是 JSON 时保留默认信息 */
      }
      throw new ApiError(res.status, message);
    }

    return (await res.json()) as T;
  }

  /** GET /healthz — 顶部后端健康指示灯 */
  healthz(): Promise<{ status: string }> {
    return this.request<{ status: string }>('/healthz');
  }

  /** GET /api/v1/workflows?limit=20 */
  listWorkflows(limit = 20): Promise<{ items: WorkflowSummary[] }> {
    return this.request<{ items: WorkflowSummary[] }>(`/api/v1/workflows?limit=${limit}`);
  }

  /** POST /api/v1/workflows(201,工作流随后在后台异步执行) */
  createWorkflow(payload: WorkflowCreatePayload): Promise<WorkflowCreated> {
    return this.request<WorkflowCreated>('/api/v1/workflows', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  /** GET /api/v1/workflows/{id} */
  getWorkflow(id: string): Promise<WorkflowDetailData> {
    return this.request<WorkflowDetailData>(`/api/v1/workflows/${id}`);
  }

  /** GET /api/v1/workflows/{id}/trace — 步骤 / 决策 / 工具调用全量轨迹 */
  getTrace(id: string): Promise<TraceResponse> {
    return this.request<TraceResponse>(`/api/v1/workflows/${id}/trace`);
  }

  /** GET /api/v1/approvals?limit=20 — 当前租户待人工审批的工作流队列(HITL) */
  listApprovals(limit = 20): Promise<{ items: ApprovalQueueItem[] }> {
    return this.request<{ items: ApprovalQueueItem[] }>(`/api/v1/approvals?limit=${limit}`);
  }

  /**
   * POST /api/v1/workflows/{id}/approval — 提交人工决策(通过 / 驳回)。
   * 409 表示工作流已不在待审状态(可能已被处理),由调用方刷新列表即可。
   */
  submitApproval(id: string, payload: ApprovalRequestPayload): Promise<ApprovalResult> {
    return this.request<ApprovalResult>(`/api/v1/workflows/${id}/approval`, {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  /** GET /api/v1/badcases?limit=&workflow_id=&category= — 坏例防线记录(租户头统一注入) */
  listBadCases(params?: BadCaseQuery): Promise<{ items: BadCase[] }> {
    const qs = new URLSearchParams();
    if (params?.limit != null) qs.set('limit', String(params.limit));
    if (params?.workflow_id) qs.set('workflow_id', params.workflow_id);
    if (params?.category) qs.set('category', params.category);
    const query = qs.toString();
    return this.request<{ items: BadCase[] }>(`/api/v1/badcases${query ? `?${query}` : ''}`);
  }

  /**
   * POST /api/v1/badcases/{id}/status — 坏例处置闭环(PRD §20.4):流转到终态。
   * 跨租户/不存在的记录返回 404;非法目标状态由后端 Literal 校验拒绝(422)。
   */
  updateBadCaseStatus(id: string, payload: BadCaseStatusPayload): Promise<BadCaseStatusResult> {
    return this.request<BadCaseStatusResult>(`/api/v1/badcases/${id}/status`, {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }
}
