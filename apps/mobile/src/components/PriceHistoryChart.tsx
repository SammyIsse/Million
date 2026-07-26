import React, { useMemo } from 'react';
import { StyleSheet, Text, View } from 'react-native';
import Svg, { Circle, Line, Polyline } from 'react-native-svg';
import { useTheme } from '../theme/ThemeContext';

export type PricePoint = { date: string; price: number };

type Props = {
  points: PricePoint[];
  color?: string;
  height?: number;
};

const PADDING_X = 10;
const PADDING_Y = 10;
const DAY_MS = 24 * 60 * 60 * 1000;

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

export function PriceHistoryChart({ points, color, height = 140 }: Props) {
  const { colors } = useTheme();
  const lineColor = color || colors.primary;
  const width = 320;

  const normalized = useMemo(() => normalizePoints(points), [points]);

  const coords = useMemo(() => {
    if (normalized.length === 0) return [];
    const prices = normalized.map((p) => p.price);
    const min = Math.min(...prices);
    const max = Math.max(...prices);
    const range = max - min || 1;
    const innerW = width - PADDING_X * 2;
    const innerH = height - PADDING_Y * 2;
    const step = normalized.length > 1 ? innerW / (normalized.length - 1) : 0;
    return normalized.map((p, i) => {
      const x = PADDING_X + step * i;
      const y = PADDING_Y + innerH - ((p.price - min) / range) * innerH;
      return { x, y, price: p.price, date: p.date };
    });
  }, [normalized, height]);

  if (normalized.length === 0) {
    return (
      <View style={[styles.empty, { height, borderColor: colors.border }]}>
        <Text style={{ color: colors.textMuted }}>Ingen prishistorik tilgængelig</Text>
      </View>
    );
  }

  const polylinePoints = coords.map((c) => `${c.x},${c.y}`).join(' ');

  return (
    <View style={{ width: '100%' }}>
      <Svg width="100%" height={height} viewBox={`0 0 ${width} ${height}`}>
        <Line
          x1={PADDING_X}
          y1={height - PADDING_Y}
          x2={width - PADDING_X}
          y2={height - PADDING_Y}
          stroke={colors.border}
          strokeWidth={1}
        />
        <Polyline points={polylinePoints} fill="none" stroke={lineColor} strokeWidth={2} />
        {coords.map((c, i) => (
          <Circle key={`${c.date}-${i}`} cx={c.x} cy={c.y} r={3} fill={lineColor} />
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
