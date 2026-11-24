// Карта покриття (UC-04, FR-06): країни з даними — зелені, без даних — сірі; підказка з назвою й кількістю показників.
// Контури — world-atlas (Natural Earth 1:110m), завантажуються окремим файлом, лише коли карта потрібна.
import { geoNaturalEarth1, geoPath, type GeoPermissibleObjects } from 'd3-geo';
import { useEffect, useMemo, useState, type MouseEvent } from 'react';
import { feature } from 'topojson-client';
import type { Topology } from 'topojson-specification';
import { NUM_TO_ISO3 } from '../lib/geo';

type Shape = { id: string; d: string };
const W = 960;
const H = 470;

export type MapCountry = { code: string; name: string; indicators: number; numeric?: string | null };

export default function WorldMap({
  countries,
  onSelect,
  selected,
}: {
  countries: Record<string, MapCountry>; // лише країни платформи (ISO3)
  onSelect: (iso3: string | null, name: string) => void;
  selected?: string | null;
}) {
  const [shapes, setShapes] = useState<{ id: string; name: string; d: string }[] | null>(null);
  const [hover, setHover] = useState<{ iso3: string | null; name: string; x: number; y: number } | null>(null);

  useEffect(() => {
    let alive = true;
    void import('world-atlas/countries-110m.json').then((mod) => {
      const topo = (mod.default ?? mod) as unknown as Topology;
      const fc = feature(topo, topo.objects.countries) as unknown as {
        features: { id?: string; properties: { name: string } }[];
      };
      const features = fc.features.filter((f) => f.id !== '010'); // без Антарктиди
      const projection = geoNaturalEarth1().fitSize([W, H], { type: 'FeatureCollection', features } as never);
      const path = geoPath(projection);
      if (alive)
        setShapes(
          features.map((f, i) => ({
            id: f.id ?? `x${i}`,
            name: f.properties.name,
            d: path(f as unknown as GeoPermissibleObjects) ?? '',
          })),
        );
    });
    return () => {
      alive = false;
    };
  }, []);

  // числовий код контуру → ISO3: з довідника країн (нові країни, UC-20 AC2), інакше — вбудована таблиця
  const byNum = useMemo(() => {
    const out: Record<string, string> = { ...NUM_TO_ISO3 };
    for (const c of Object.values(countries)) if (c.numeric) out[c.numeric] = c.code;
    return out;
  }, [countries]);

  function info(s: Shape & { name: string }) {
    const iso3 = byNum[s.id] ?? null;
    const c = iso3 ? countries[iso3] : undefined;
    return { iso3: c && c.indicators > 0 ? iso3 : null, name: c?.name ?? s.name, count: c?.indicators ?? 0, known: !!c };
  }

  function move(e: MouseEvent<SVGPathElement>, s: Shape & { name: string }) {
    const box = (e.currentTarget.ownerSVGElement as SVGSVGElement).getBoundingClientRect();
    const i = info(s);
    setHover({ iso3: i.iso3, name: i.name, x: e.clientX - box.left, y: e.clientY - box.top });
  }

  if (!shapes) return <p className="hint">Завантаження карти…</p>;
  const hovered = hover ? Object.values(countries).find((c) => c.code === hover.iso3) : undefined;

  return (
    <div className="map-wrap">
      <svg viewBox={`0 0 ${W} ${H}`} className="world-map" role="img" aria-label="Карта покриття даними">
        {shapes.map((s) => {
          const i = info(s);
          const cls = ['country', i.iso3 ? 'has-data' : 'no-data', i.iso3 && i.iso3 === selected ? 'selected' : '']
            .filter(Boolean)
            .join(' ');
          return (
            <path
              key={s.id + s.name}
              d={s.d}
              className={cls}
              onMouseMove={(e) => move(e, s)}
              onMouseLeave={() => setHover(null)}
              onClick={() => onSelect(i.iso3, i.name)}
            />
          );
        })}
      </svg>
      {hover && (
        <div className="map-tip" style={{ left: hover.x + 12, top: hover.y + 12 }}>
          <strong>{hover.name}</strong>
          <div>{hovered ? `${hovered.indicators} показників` : 'даних немає'}</div>
        </div>
      )}
      <div className="map-legend">
        <span className="swatch has-data" /> є дані <span className="swatch no-data" /> немає даних
      </div>
    </div>
  );
}
