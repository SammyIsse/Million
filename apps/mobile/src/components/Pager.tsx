import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { useTheme } from '../theme/ThemeContext';

/**
 * Paginering med sidetal - web-paritet.
 *
 * Appen havde kun Forrige/Næste. På en kategori med 40+ sider betød det, at
 * side 30 lå 29 tryk væk, og der var ingen vej til sidste side overhovedet.
 * Webbens `macros/pagination.html` har første/forrige/tal/næste/sidste, og
 * dette er den samme idé skåret ned til en bredde der giver mening på en
 * telefon: « ‹ og » › plus et vindue på op til tre sidetal omkring den
 * aktuelle.
 *
 * Delt mellem CategoryScreen, SaleScreen og SearchScreen, så de tre ikke
 * driver fra hinanden igen.
 */
export function Pager({
  page,
  totalPages,
  onPage,
}: {
  page: number;
  totalPages: number;
  onPage: (p: number) => void;
}) {
  const { colors } = useTheme();
  if (totalPages <= 1) return null;

  // Vindue på op til 3 sidetal, klemt ind i [1, totalPages].
  const start = Math.max(1, Math.min(page - 1, totalPages - 2));
  const end = Math.min(totalPages, start + 2);
  const numbers: number[] = [];
  for (let p = start; p <= end; p += 1) numbers.push(p);

  const atFirst = page <= 1;
  const atLast = page >= totalPages;

  const Btn = ({
    label,
    disabled,
    onPress,
    active,
    wide,
  }: {
    label: string;
    disabled?: boolean;
    onPress: () => void;
    active?: boolean;
    wide?: boolean;
  }) => (
    <Pressable
      disabled={disabled}
      onPress={onPress}
      accessibilityRole="button"
      accessibilityLabel={label}
      accessibilityState={{ disabled: !!disabled, selected: !!active }}
      style={[
        styles.btn,
        wide ? styles.wide : null,
        {
          backgroundColor: active ? colors.primary : colors.surface,
          borderColor: active ? colors.primary : colors.border,
          opacity: disabled ? 0.4 : 1,
        },
      ]}
    >
      <Text
        style={{
          color: active ? '#fff' : colors.text,
          fontWeight: active ? '700' : '500',
        }}
      >
        {label}
      </Text>
    </Pressable>
  );

  return (
    <View style={styles.row}>
      <Btn label="«" disabled={atFirst} onPress={() => onPage(1)} />
      <Btn label="‹" disabled={atFirst} onPress={() => onPage(page - 1)} />
      {numbers.map((p) => (
        <Btn key={p} label={String(p)} active={p === page} onPress={() => onPage(p)} wide />
      ))}
      <Btn label="›" disabled={atLast} onPress={() => onPage(page + 1)} />
      <Btn label="»" disabled={atLast} onPress={() => onPage(totalPages)} />
    </View>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    paddingVertical: 18,
    flexWrap: 'wrap',
  },
  btn: {
    minWidth: 40,
    paddingHorizontal: 10,
    paddingVertical: 9,
    borderWidth: 1,
    borderRadius: 4,
    alignItems: 'center',
  },
  wide: { minWidth: 46 },
});
