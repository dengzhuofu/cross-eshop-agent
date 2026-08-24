import { useState } from 'react';

/**
 * 可折叠 JSON 详情块:detail 为空时不渲染;
 * 字符串直接展示,对象/数组美化缩进后放入 <pre>。
 */
export default function CollapsibleJson({ data, label = '查看详情' }: { data: unknown; label?: string }) {
  const [open, setOpen] = useState(false);

  if (data == null || (typeof data === 'object' && Object.keys(data as object).length === 0)) {
    return null;
  }

  const text = typeof data === 'string' ? data : JSON.stringify(data, null, 2);

  return (
    <div className="json-wrap">
      <button
        type="button"
        className="json-toggle"
        onClick={(e) => {
          // 嵌套在可点击卡片里时（如 Bad Case 面板），展开/收起不应触发卡片跳转
          e.stopPropagation();
          setOpen((v) => !v);
        }}
      >
        <span className={`caret${open ? ' open' : ''}`} aria-hidden />
        {open ? '收起详情' : label}
      </button>
      {open && <pre className="json-block">{text}</pre>}
    </div>
  );
}
