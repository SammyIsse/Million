import React, { useMemo } from 'react';
import { StyleSheet, Text, View } from 'react-native';
import Svg, { Circle, Line, Polyline, Text as SvgText } from 'react-native-svg';
import { useTheme } from '../theme/ThemeContext';

export type PricePoint = { date: string; price: number };
export type PriceSeries = { key: string; color: string; points: PricePoint[] };

type Props = {
  series: PriceSeries[];
  height?: number;
};

const PADDING_X = 10;
const PADDING_Y = 10;
const LABEL_AREA_HEIGHT = 18;
const DAY_MS = 24 * 60 * 60 * 1000;

function formatTickDate(ts: number): string {
  const d = new Date(ts);
  const dd = String(d.getDate()).padStart(2, '0');
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  return `${dd}/${mm}`;
}

/** Alle mandage i [minTs, maxTs], til x-akse-mærker. */
function mondaysInRange(minTs: number, maxTs: number): number[] {
  const start = new Date(minTs);
  start.setHours(0, 0, 0, 0);
  const day = start.getDay(); // 0=søn, 1=man, ..., 6=lør
  const daysUntilMonday = (8 - day) % 7;
  const ticks: number[] = [];
  let cur = start.getTime() + daysUntilMonday * DAY_MS;
  while (cur <= maxTs) {
    ticks.push(cur);
    cur += 7 * DAY_MS;
  }
  return ticks;
}

/** Single-point historik → flad 30-dages linje (web-paritet). */
function normalizePoints(points: PricePoint[]): PricePoint[] {
  if (points.length === 1) {
    const only = points[0];
    const d = new Date(only.date);
    const past = Number.isNaN(d.getTime()) ? new Date(Date.now() - 30 * DAY_MS) : new Date(d.getTime() - 30 * DAY_MS);
    return [{ date: past.toISOString().slice(0, 10), price: only.price }, only];
  }
  return points;
}

type Coord = { x: number; y: number; price: number; date: string };
type SeriesCoords = { key: string; color: string; coords: Coord[] };

export function PriceHistoryChart({ series, height = 140 }: Props) {
  const { colors } = useTheme();
  const width = 320;

  const normalizedSeries = useMemo(
    () =>
      series
        .map((s) => ({ ...s, points: normalizePoints(s.points) }))
        .filter((s) => s.points.length > 0),
    [series],
  );

  const bounds = useMemo(() => {
    let minTs = Infinity;
    let maxTs = -Infinity;
    let minPrice = Infinity;
    let maxPrice = -Infinity;
    normalizedSeries.forEach((s) => {
      s.points.forEach((p) => {
        const ts = new Date(p.date).getTime();
        if (!Number.isNaN(ts)) {
          minTs = Math.min(minTs, ts);
          maxTs = Math.max(maxTs, ts);
        }
        minPrice = Math.min(minPrice, p.price);
        maxPrice = Math.max(maxPrice, p.price);
      });
    });
    return { minTs, maxTs, minPrice, maxPrice };
  }, [normalizedSeries]);

  const seriesCoords: SeriesCoords[] = useMemo(() => {
    if (normalizedSeries.length === 0 || !Number.isFinite(bounds.minTs)) return [];
    const innerW = width - PADDING_X * 2;
    const innerH = height - PADDING_Y * 2;
    const tsRange = bounds.maxTs - bounds.minTs || 1;
    const priceRange = bounds.maxPrice - bounds.minPrice || 1;
    return normalizedSeries.map((s) => ({
      key: s.key,
      color: s.color,
      coords: s.points.reduce<Coord[]>((acc, p) => {
        const ts = new Date(p.date).getTime();
        if (Number.isNaN(ts)) return acc;
        const x = PADDING_X + ((ts - bounds.minTs) / tsRange) * innerW;
        const y = PADDING_Y + innerH - ((p.price - bounds.minPrice) / priceRange) * innerH;
        acc.push({ x, y, price: p.price, date: p.date });
        return acc;
      }, []),
    }));
  }, [normalizedSeries, bounds, height]);

  const dateTicks = useMemo(() => {
    if (!Number.isFinite(bounds.minTs) || !Number.isFinite(bounds.maxTs)) return [];
    const innerW = width - PADDING_X * 2;
    if (bounds.maxTs === bounds.minTs) {
      return [{ x: PADDING_X, label: formatTickDate(bounds.minTs), anchor: 'start' as const }];
    }
    const tsRange = bounds.maxTs - bounds.minTs;
    const toTick = (ts: number, anchor: 'start' | 'middle' | 'end') => ({
      x: PADDING_X + ((ts - bounds.minTs) / tsRange) * innerW,
      label: formatTickDate(ts),
      anchor,
    });
    const mondays = mondaysInRange(bounds.minTs, bounds.maxTs);
    if (mondays.length === 0) {
      return [toTick(bounds.minTs, 'start'), toTick(bounds.maxTs, 'end')];
    }
    return mondays.map((ts) => toTick(ts, 'middle'));
  }, [bounds]);

  const hasData = seriesCoords.some((s) => s.coords.length > 0);

  if (!hasData) {
    return (
      <View style={[styles.empty, { height, borderColor: colors.border }]}>
        <Text style={{ color: colors.textMuted }}>Ingen prishistorik tilgængelig</Text>
      </View>
    );
  }

  const showPoints = seriesCoords.length === 1;
  const totalHeight = height + LABEL_AREA_HEIGHT;

  return (
    <View style={{ width: '100%' }}>
      <Svg width="100%" height={totalHeight} viewBox={`0 0 ${width} ${totalHeight}`}>
        <Line
          x1={PADDING_X}
          y1={height - PADDING_Y}
          x2={width - PADDING_X}
          y2={height - PADDING_Y}
          stroke={colors.border}
          strokeWidth={1}
        />
        {seriesCoords.map((s) => (
          <Polyline
            key={s.key}
            points={s.coords.map((c) => `${c.x},${c.y}`).join(' ')}
            fill="none"
            stroke={s.color}
            strokeWidth={2}
          />
        ))}
        {showPoints &&
          seriesCoords[0].coords.map((c, i) => (
            <Circle key={`${c.date}-${i}`} cx={c.x} cy={c.y} r={3} fill={seriesCoords[0].color} />
          ))}
        {dateTicks.map((t, i) => (
          <SvgText
            key={i}
            x={t.x}
            y={height - PADDING_Y + LABEL_AREA_HEIGHT - 4}
            fontSize={10}
            fill={colors.textMuted}
            textAnchor={t.anchor}
          >
            {t.label}
          </SvgText>
        ))}
      </Svg>
    </View>
  );
}

const styles = StyleSheet.create({
  empty: {
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderRadius: 12,
  },
});
