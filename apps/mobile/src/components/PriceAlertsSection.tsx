import React, { useCallback, useEffect, useState } from 'react';
import { ActivityIndicator, Alert, Pressable, StyleSheet, Text, View } from 'react-native';
import { useFocusEffect } from '@react-navigation/native';
import { getSupabase } from '../auth/supabase';
import { useAuth } from '../auth/AuthContext';
import { useTheme } from '../theme/ThemeContext';
import { env } from '../config/env';

type PriceAlert = {
  id: string;
  product_name: string | null;
  target_price: number;
};

/**
 * "Mine prisalarmer" — web-paritet (static/js/auth.js::loadPriceAlerts).
 *
 * Man kunne oprette en prisalarm fra produktskærmen, men hverken se eller
 * stoppe den igen — på nogen af platformene. Rettighederne fandtes allerede:
 * scripts/supabase-price-alerts-v2.sql giver `authenticated` SELECT og DELETE
 * på egne rækker (RLS: auth.uid() = user_id) og nævner selv "en fremtidig
 * Mine alarmer-visning". Kun visningen manglede.
 *
 * Tabelnavnet følger TABLE_SUFFIX som alle andre skrive-tabeller, så staging
 * rammer price_alerts_dev og ikke produktionens rækker.
 */
const ALERTS_TABLE = `price_alerts${env.rpcSuffix}`;

export function PriceAlertsSection() {
  const { colors } = useTheme();
  const { user } = useAuth();
  const [alerts, setAlerts] = useState<PriceAlert[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const load = useCallback(async () => {
    const sb = getSupabase();
    if (!sb || !user) {
      setAlerts([]);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const res = await sb
        .from(ALERTS_TABLE)
        .select('id,product_name,target_price')
        .order('created_at', { ascending: false })
        .limit(200);
      // supabase-js kaster ikke ved RLS/HTTP-fejl - den returnerer { error }.
      if (res.error) throw res.error;
      setAlerts((res.data as PriceAlert[]) || []);
    } catch {
      setError('Kunne ikke hente dine prisalarmer lige nu.');
      setAlerts([]);
    } finally {
      setLoading(false);
    }
  }, [user]);

  useEffect(() => {
    void load();
  }, [load]);

  // En alarm kan være udløst (og slettet) mens skærmen lå i baggrunden.
  useFocusEffect(
    useCallback(() => {
      void load();
    }, [load]),
  );

  const remove = useCallback(
    async (alert: PriceAlert) => {
      const sb = getSupabase();
      if (!sb || !user) return;
      setDeletingId(alert.id);
      try {
        // RLS begrænser allerede til egne rækker; eq('user_id') er et andet
        // lag, så et fejlkonfigureret policy stadig kun kan ramme brugerens egne.
        const res = await sb
          .from(ALERTS_TABLE)
          .delete()
          .eq('id', alert.id)
          .eq('user_id', user.id);
        if (res.error) throw res.error;
        setAlerts((prev) => prev.filter((a) => a.id !== alert.id));
      } catch {
        Alert.alert('Fejl', 'Kunne ikke slette prisalarmen. Prøv igen.');
      } finally {
        setDeletingId(null);
      }
    },
    [user],
  );

  const confirmRemove = useCallback(
    (alert: PriceAlert) => {
      Alert.alert(
        'Slet prisalarm?',
        `Du får ikke længere besked, når ${alert.product_name || 'varen'} falder i pris.`,
        [
          { text: 'Annullér', style: 'cancel' },
          { text: 'Slet', style: 'destructive', onPress: () => void remove(alert) },
        ],
      );
    },
    [remove],
  );

  if (!user) return null;

  return (
    <View style={styles.wrap}>
      <Text style={[styles.title, { color: colors.textMuted }]}>MINE PRISALARMER</Text>
      {loading && !alerts.length ? (
        <ActivityIndicator style={{ marginVertical: 12 }} color={colors.primary} />
      ) : error ? (
        <View style={[styles.row, { backgroundColor: colors.surface, borderColor: colors.border }]}>
          <Text style={{ color: colors.text, flex: 1 }}>{error}</Text>
          <Pressable onPress={() => void load()} hitSlop={8}>
            <Text style={{ color: colors.primary, fontWeight: '600' }}>Prøv igen</Text>
          </Pressable>
        </View>
      ) : !alerts.length ? (
        <Text style={{ color: colors.textMuted, fontSize: 13, paddingHorizontal: 4 }}>
          Du har ingen aktive prisalarmer. Åbn en vare og tryk “Overvåg pris”.
        </Text>
      ) : (
        alerts.map((a) => (
          <View
            key={a.id}
            style={[styles.row, { backgroundColor: colors.surface, borderColor: colors.border }]}
          >
            <View style={{ flex: 1, minWidth: 0 }}>
              <Text style={{ color: colors.text, fontWeight: '600' }} numberOfLines={1}>
                {a.product_name || 'Ukendt vare'}
              </Text>
              <Text style={{ color: colors.textMuted, fontSize: 12 }}>
                Giver besked under {Number(a.target_price).toFixed(2)} kr
              </Text>
            </View>
            <Pressable
              onPress={() => confirmRemove(a)}
              disabled={deletingId === a.id}
              hitSlop={8}
              accessibilityRole="button"
              accessibilityLabel={`Slet prisalarm for ${a.product_name || 'varen'}`}
            >
              <Text
                style={{
                  color: colors.sale,
                  fontWeight: '600',
                  opacity: deletingId === a.id ? 0.5 : 1,
                }}
              >
                {deletingId === a.id ? 'Sletter…' : 'Slet'}
              </Text>
            </Pressable>
          </View>
        ))
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { marginTop: 22, gap: 8 },
  title: { fontSize: 12, fontWeight: '700', letterSpacing: 0.6, paddingHorizontal: 4 },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    borderWidth: 1,
    borderRadius: 8,
    paddingHorizontal: 14,
    paddingVertical: 12,
  },
});
