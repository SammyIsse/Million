import React, { useMemo, useState } from 'react';
import { Modal, Pressable, StyleSheet, Text, View } from 'react-native';
import type { ListingParams, Product } from '../api/types';
import { useTheme } from '../theme/ThemeContext';

export type FiltersValue = {
  sort?: ListingParams['sort'];
  sale?: boolean;
  organic?: boolean;
  lactose?: boolean;
  min_price?: number;
  max_price?: number;
};

type Props = {
  values: FiltersValue;
  onChange: (next: FiltersValue) => void;
  /** Reserveret plads til undercategory-chips, som renderes af parent-skærmen. */
  showSubcats?: boolean;
};

const SORT_OPTIONS: Array<{ value: NonNullable<ListingParams['sort']>; label: string }> = [
  { value: 'relevance', label: 'Relevans' },
  { value: 'price-asc', label: 'Pris ↑' },
  { value: 'price-desc', label: 'Pris ↓' },
  { value: 'kg-price-asc', label: 'Kg-pris ↑' },
  { value: 'name-asc', label: 'Navn A-Å' },
];

function countActiveFilters(values: FiltersValue): number {
  let n = 0;
  if (values.sort && values.sort !== 'relevance') n += 1;
  if (values.sale) n += 1;
  if (values.organic) n += 1;
  if (values.lactose) n += 1;
  if (values.min_price != null) n += 1;
  if (values.max_price != null) n += 1;
  return n;
}

export function FiltersBar({ values, onChange, showSubcats }: Props) {
  const { colors } = useTheme();
  const [open, setOpen] = useState(false);
  const sort = values.sort || 'relevance';
  const activeCount = useMemo(() => countActiveFilters(values), [values]);

  const toggle = (key: 'sale' | 'organic' | 'lactose') => {
    onChange({ ...values, [key]: !values[key] });
  };

  const reset = () => {
    onChange({ sort: 'relevance' });
  };

  return (
    <View style={showSubcats ? styles.wrapWithSubcats : styles.wrap}>
      <View style={styles.triggerRow}>
        <Pressable
          onPress={() => setOpen(true)}
          style={[
            styles.trigger,
            {
              backgroundColor: activeCount > 0 ? colors.primary : colors.surface,
              borderColor: colors.border,
            },
          ]}
        >
          <Text
            style={{
              color: activeCount > 0 ? '#fff' : colors.text,
              fontWeight: '700',
              fontSize: 14,
            }}
          >
            Filter{activeCount > 0 ? ` (${activeCount})` : ''}
          </Text>
        </Pressable>
      </View>

      <Modal
        visible={open}
        transparent
        animationType="fade"
        onRequestClose={() => setOpen(false)}
      >
        <Pressable style={styles.backdrop} onPress={() => setOpen(false)}>
          <Pressable
            style={[styles.sheet, { backgroundColor: colors.surface, borderColor: colors.border }]}
            onPress={(e) => e.stopPropagation?.()}
          >
            <View style={styles.sheetHead}>
              <Text style={[styles.sheetTitle, { color: colors.text }]}>Filter & sortering</Text>
              <Pressable onPress={() => setOpen(false)}>
                <Text style={{ color: colors.primary, fontWeight: '600' }}>Luk</Text>
              </Pressable>
            </View>

            <Text style={[styles.groupLabel, { color: colors.textMuted }]}>Sortering</Text>
            <View style={styles.chips}>
              {SORT_OPTIONS.map((opt) => {
                const active = sort === opt.value;
                return (
                  <Pressable
                    key={opt.value}
                    onPress={() => onChange({ ...values, sort: opt.value })}
                    style={[
                      styles.chip,
                      {
                        backgroundColor: active ? colors.primary : colors.bg,
                        borderColor: colors.border,
                      },
                    ]}
                  >
                    <Text
                      style={{
                        color: active ? '#fff' : colors.text,
                        fontWeight: '600',
                        fontSize: 13,
                      }}
                    >
                      {opt.label}
                    </Text>
                  </Pressable>
                );
              })}
            </View>

            <Text style={[styles.groupLabel, { color: colors.textMuted }]}>Filtre</Text>
            <View style={styles.chips}>
              <ToggleChip
                label="Tilbud"
                active={!!values.sale}
                activeColor={colors.sale}
                onPress={() => toggle('sale')}
              />
              <ToggleChip
                label="Øko"
                active={!!values.organic}
                activeColor={colors.badge}
                onPress={() => toggle('organic')}
              />
              <ToggleChip
                label="Laktosefri"
                active={!!values.lactose}
                activeColor={colors.badge}
                onPress={() => toggle('lactose')}
              />
            </View>

            <View style={styles.sheetActions}>
              <Pressable
                onPress={reset}
                style={[styles.secondaryBtn, { borderColor: colors.border }]}
              >
                <Text style={{ color: colors.text, fontWeight: '600' }}>Nulstil</Text>
              </Pressable>
              <Pressable
                onPress={() => setOpen(false)}
                style={[styles.primaryBtn, { backgroundColor: colors.primary }]}
              >
                <Text style={{ color: '#fff', fontWeight: '700' }}>Vis resultater</Text>
              </Pressable>
            </View>
          </Pressable>
        </Pressable>
      </Modal>
    </View>
  );
}

function ToggleChip({
  label,
  active,
  activeColor,
  onPress,
}: {
  label: string;
  active: boolean;
  activeColor: string;
  onPress: () => void;
}) {
  const { colors } = useTheme();
  return (
    <Pressable
      onPress={onPress}
      style={[
        styles.chip,
        {
          backgroundColor: active ? activeColor : colors.bg,
          borderColor: active ? activeColor : colors.border,
        },
      ]}
    >
      <Text style={{ color: active ? '#fff' : colors.text, fontWeight: '600', fontSize: 13 }}>
        {label}
      </Text>
    </Pressable>
  );
}

/** Klient-side filtrering/sortering til kuraterede lister (forside) uden backend-round-trip. */
export function applyClientFilters(products: Product[], filters: FiltersValue): Product[] {
  let out = products.filter((p) => {
    if (filters.sale && !(p.is_sale || p.is_any_sale)) return false;
    if (filters.organic && !p.is_organic) return false;
    if (filters.lactose && !p.is_lactose_free) return false;
    if (filters.min_price != null && p.price < filters.min_price) return false;
    if (filters.max_price != null && p.price > filters.max_price) return false;
    return true;
  });
  switch (filters.sort) {
    case 'price-asc':
      out = [...out].sort((a, b) => a.price - b.price);
      break;
    case 'price-desc':
      out = [...out].sort((a, b) => b.price - a.price);
      break;
    case 'kg-price-asc':
      out = [...out].sort((a, b) => (a.kg_price ?? Infinity) - (b.kg_price ?? Infinity));
      break;
    case 'name-asc':
      out = [...out].sort((a, b) => a.name.localeCompare(b.name, 'da'));
      break;
    default:
      break;
  }
  return out;
}

const styles = StyleSheet.create({
  wrap: { marginTop: 4, marginBottom: 2 },
  wrapWithSubcats: { marginTop: 2, marginBottom: 2 },
  triggerRow: {
    flexDirection: 'row',
    justifyContent: 'flex-end',
    paddingHorizontal: 16,
  },
  trigger: {
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: 16,
    borderWidth: 1,
  },
  backdrop: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.45)',
    justifyContent: 'flex-end',
  },
  sheet: {
    borderTopLeftRadius: 16,
    borderTopRightRadius: 16,
    borderWidth: 1,
    paddingHorizontal: 16,
    paddingTop: 16,
    paddingBottom: 28,
  },
  sheetHead: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 12,
  },
  sheetTitle: { fontSize: 17, fontWeight: '700' },
  groupLabel: { fontSize: 12, fontWeight: '600', marginTop: 8, marginBottom: 8 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  chip: {
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 16,
    borderWidth: 1,
  },
  sheetActions: {
    flexDirection: 'row',
    gap: 10,
    marginTop: 20,
  },
  secondaryBtn: {
    flex: 1,
    paddingVertical: 12,
    borderRadius: 10,
    borderWidth: 1,
    alignItems: 'center',
  },
  primaryBtn: {
    flex: 1.4,
    paddingVertical: 12,
    borderRadius: 10,
    alignItems: 'center',
  },
});
