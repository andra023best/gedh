// Графік часових рядів (FR-08, FR-10) у чистому SVG: до 4 рядів, пропуски — розрив лінії (UC-05 EX1, без інтерполяції).
// Оформлення — пізніше разом із дизайном (S-05); кольори задано класами .series-0 … .series-3.
import { useState } from 'react';

export type Point = { x: string; y: number | null };
export type Series = { label: string; points: Point[] };

const W = 760;
const H = 300;
const M = { top: 16, right: 16, bottom: 32, left: 64 };

function niceTicks(min: number, max: number, count = 5): number[] {
  if (min === max) return [min];
  const span = max - min;
  const step0 = span / count;
  const mag = 10 ** Math.floor(Math.log10(step0));
  const step = [1, 2, 2.5, 5, 10].map((k) => k * mag).find((s) => s >= step0) ?? step0;
  const out: number[] = [];
  // від кратного кроку нижче мінімуму до кратного вище максимуму — пік не виходить за сітку
  for (let v = Math.floor(min / step) * step; v < max + step - step * 1e-9; v += step) out.push(Number(v.toPrecision(12)));
  return out;
}

export default function LineChart({ series, format }: { series: Series[]; format: (v: number) => string }) {
  const [hoverX, setHoverX] = useState<string | null>(null);
  const xs = [...new Set(series.flatMap((s) => s.points.map((p) => p.x)))].sort();
  const ys = series.flatMap((s) => s.points.map((p) => p.y)).filter((v): v is number => v !== null);
  if (xs.length === 0 || ys.length === 0) return <p className="hint">Немає значень для графіка.</p>;

  let lo = Math.min(...ys);
  let hi = Math.max(...ys);
  if (lo > 0 && lo / (hi || 1) < 0.5) lo = 0; // нуль на осі, якщо не стискає графік
  if (lo === hi) {
    lo -= 1;
    hi += 1;
  }
  const ticks = niceTicks(lo, hi);
  lo = Math.min(lo, ticks[0]);
  hi = Math.max(hi, ticks[ticks.length - 1]);
  const iw = W - M.left - M.right;
  const ih = H - M.top - M.bottom;
  const px = (x: string) => M.left + (xs.length === 1 ? iw / 2 : (xs.indexOf(x) / (xs.length - 1)) * iw);
  const py = (y: number) => M.top + ih - ((y - lo) / (hi - lo)) * ih;
  const every = Math.ceil(xs.length / 12);

  // лінія розривається там, де значення немає
  function path(points: Point[]): string {
    const map = new Map(points.map((p) => [p.x, p.y]));
    let d = '';
    let pen = false;
    for (const x of xs) {
      const y = map.get(x);
      if (y === null || y === undefined) {
        pen = false;
        continue;
      }
      d += `${pen ? 'L' : 'M'}${px(x).toFixed(1)},${py(y).toFixed(1)}`;
      pen = true;
    }
    return d;
  }

  return (
    <div className="chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Графік часового ряду" onMouseLeave={() => setHoverX(null)}>
        {ticks.map((t) => (
          <g key={t} className="grid">
            <line x1={M.left} x2={W - M.right} y1={py(t)} y2={py(t)} />
            <text x={M.left - 6} y={py(t) + 4} textAnchor="end">
              {format(t)}
            </text>
          </g>
        ))}
        {xs.map((x, i) =>
          (xs.length - 1 - i) % every === 0 ? ( // підписи відраховуються від останнього періоду
            <text key={x} className="axis" x={px(x)} y={H - 10} textAnchor="middle">
              {x}
            </text>
          ) : null,
        )}
        {series.map((s, i) => (
          <g key={s.label} className={`series-${i}`}>
            <path d={path(s.points)} fill="none" />
            {s.points
              .filter((p) => p.y !== null)
              .map((p) => (
                <circle key={p.x} cx={px(p.x)} cy={py(p.y as number)} r={hoverX === p.x ? 4 : 2.5} />
              ))}
          </g>
        ))}
        {xs.map((x) => (
          <rect
            key={x}
            x={px(x) - iw / Math.max(xs.length - 1, 1) / 2}
            y={M.top}
            width={iw / Math.max(xs.length - 1, 1)}
            height={ih}
            fill="transparent"
            onMouseEnter={() => setHoverX(x)}
          />
        ))}
        {hoverX && <line className="cursor" x1={px(hoverX)} x2={px(hoverX)} y1={M.top} y2={M.top + ih} />}
      </svg>
      <div className="chart-legend">
        {hoverX && <strong>{hoverX}: </strong>}
        {series.map((s, i) => {
          const v = hoverX ? s.points.find((p) => p.x === hoverX)?.y : undefined;
          return (
            <span key={s.label} className={`legend series-${i}`}>
              <span className="swatch" /> {s.label}
              {hoverX && ` — ${v === null || v === undefined ? 'н/д' : format(v)}`}
            </span>
          );
        })}
      </div>
    </div>
  );
}
