import type { Tone } from '../types';

/** 彩色状态徽章:绿=完成/成功、琥珀=进行中、红=失败、蓝=信息/重放、灰=中性 */
export default function StatusBadge({ label, tone }: { label: string; tone: Tone }) {
  return <span className={`badge tone-${tone}`}>{label}</span>;
}
