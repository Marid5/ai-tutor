interface ProgressBarProps {
  value: number;
  max: number;
  label: string;
  tone?: 'accent' | 'good';
  size?: 'sm' | 'md' | 'lg';
}

/**
 * A native <progress>, styled in CSS. Its fill comes from the value/max
 * attributes, so no inline style is needed (the CSP forbids them).
 */
export function ProgressBar({ value, max, label, tone = 'accent', size = 'md' }: ProgressBarProps) {
  const safeMax = max > 0 ? max : 1;
  const safeValue = Math.min(Math.max(value, 0), safeMax);
  return <progress className={`progress progress-${size} progress-${tone}`} value={safeValue} max={safeMax} aria-label={label} />;
}
