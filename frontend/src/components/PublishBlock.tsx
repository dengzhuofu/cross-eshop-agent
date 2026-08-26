import { useMemo } from 'react';

/** node_publish 步骤 detail.items 里的一条发布结果（M12 起含商城 url） */
interface PublishItem {
  marketplace: string;
  listing_id: string;
  status: string;
  url?: string;
  validation_errors?: string[];
  replayed?: boolean;
  error?: string;
}

const MP_LABELS: Record<string, string> = {
  amazon: 'Amazon',
  shopify: 'Shopify',
  tiktok_shop: 'TikTok Shop',
};

const STATUS_LABELS: Record<string, string> = {
  published: '已发布',
  validation_failed: '校验未过',
  error: '失败',
};

/**
 * 商城外链规范化:商城服务端已改为返回绝对地址;对历史数据里存的相对路径
 * （/product/xxx，会落到前端自己的域名上）按「商城与前端同主机、端口 8001」
 * 的部署约定补全，保证旧工作流的外链同样可点。
 */
function storefrontHref(url: string): string {
  if (/^https?:\/\//.test(url)) return url;
  return `${window.location.protocol}//${window.location.hostname}:8001${url}`;
}

/**
 * publish 步骤的结构化渲染:逐平台徽标 + 状态 + 「在商城查看」外链(M12)。
 * url 为空 = mock 商城未启动/禁用,只展示状态不渲染死链接。
 */
export default function PublishBlock({ detail }: { detail: unknown }) {
  const items = useMemo<PublishItem[]>(() => {
    const d = (detail ?? {}) as { items?: PublishItem[] };
    return Array.isArray(d.items) ? d.items : [];
  }, [detail]);

  if (items.length === 0) return null;

  return (
    <div className="publish-block">
      {items.map((it) => {
        const label = STATUS_LABELS[it.status] ?? it.status;
        const tone =
          it.status === 'published' ? 'ok' : it.status === 'error' ? 'bad' : 'warn';
        return (
          <div key={it.marketplace + it.listing_id} className="publish-item">
            <span className="pub-mp">{MP_LABELS[it.marketplace] ?? it.marketplace}</span>
            <span className={`pub-status tone-${tone}`}>{label}</span>
            {it.replayed && <span className="pub-replay">重放</span>}
            {it.listing_id && <code className="pub-id">{it.listing_id}</code>}
            {it.url && (
              <a className="pub-link" href={storefrontHref(it.url)} target="_blank" rel="noreferrer">
                在商城查看 ↗
              </a>
            )}
            {it.status === 'validation_failed' && (it.validation_errors?.length ?? 0) > 0 && (
              <span className="pub-err">{it.validation_errors!.join(';')}</span>
            )}
            {it.error && <span className="pub-err">{it.error}</span>}
          </div>
        );
      })}
    </div>
  );
}
