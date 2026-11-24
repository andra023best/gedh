// ISO 3166-1: alpha-3 (коди платформи) ↔ числовий код (контури world-atlas). Лише 25 країн платформи (D-02).
export const ISO3_TO_NUM: Record<string, string> = {
  ARG: '032',
  AUS: '036',
  BRA: '076',
  CAN: '124',
  CHE: '756',
  CHN: '156',
  DEU: '276',
  EGY: '818',
  ESP: '724',
  FRA: '250',
  GBR: '826',
  IDN: '360',
  IND: '356',
  ITA: '380',
  JPN: '392',
  KOR: '410',
  MEX: '484',
  NLD: '528',
  POL: '616',
  SAU: '682',
  SWE: '752',
  TUR: '792',
  UKR: '804',
  USA: '840',
  ZAF: '710',
};

export const NUM_TO_ISO3: Record<string, string> = Object.fromEntries(
  Object.entries(ISO3_TO_NUM).map(([iso3, num]) => [num, iso3]),
);

// Число з одиницею й масштабом показника; null — «н/д» (UC-05 EX1: без інтерполяції)
export function fmtValue(value: number | null | undefined, unit?: string, scale?: string): string {
  if (value === null || value === undefined) return 'н/д';
  const abs = Math.abs(value);
  const digits = abs >= 1000 ? 0 : abs >= 100 ? 1 : 2;
  const num = value.toLocaleString('uk-UA', { maximumFractionDigits: digits, minimumFractionDigits: 0 });
  const sc = scale && !['одиниці', 'частка', 'р/р', 'річних'].includes(scale) ? ` ${scale}` : '';
  return `${num}${sc}${unit ? ` ${unit}` : ''}`;
}
