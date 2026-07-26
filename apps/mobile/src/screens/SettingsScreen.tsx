import React from 'react';
import { Pressable, ScrollView, StyleSheet, Switch, Text, View } from 'react-native';
import { useNavigation } from '@react-navigation/native';
import type { NativeStackNavigationProp } from '@react-navigation/native-stack';
import { useAuth } from '../auth/AuthContext';
import { useStoreCatalog } from '../stores/StoreCatalogContext';
import { useTheme } from '../theme/ThemeContext';
import { env } from '../config/env';
import type { RootStackParamList } from '../navigation/types';

export function SettingsScreen() {
  const { colors, isDark, toggleDark } = useTheme();
  const { catalog, selectedLabels, toggleStore, selectAll } = useStoreCatalog();
  const { user, displayName, logout } = useAuth();
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();
  const [pushEnabled, setPushEnabled] = React.useState(false);
  const [newsletter, setNewsletter] = React.useState(false);

  return (
    <ScrollView style={{ flex: 1, backgroundColor: colors.bg }} contentContainerStyle={{ padding: 16 }}>
      <Text style={[styles.h, { color: colors.text }]}>Konto</Text>
      {user ? (
        <View style={[styles.row, { backgroundColor: colors.surface, borderColor: colors.border }]}>
          <View style={{ flex: 1 }}>
            <Text style={{ color: colors.text, fontWeight: '600' }}>{displayName || user.email}</Text>
            <Text style={{ color: colors.textMuted, fontSize: 12 }}>{user.email}</Text>
          </View>
          <Pressable onPress={() => void logout()}>
            <Text style={{ color: colors.sale, fontWeight: '600' }}>Log ud</Text>
          </Pressable>
        </View>
      ) : (
        <Pressable
          onPress={() => navigation.navigate('Auth')}
          style={[styles.row, { backgroundColor: colors.surface, borderColor: colors.border }]}
        >
          <Text style={{ color: colors.primary, fontWeight: '600' }}>Log ind / Opret konto</Text>
        </Pressable>
      )}

      <Text style={[styles.h, { color: colors.text }]}>Udseende</Text>
      <View style={[styles.row, { backgroundColor: colors.surface, borderColor: colors.border }]}>
        <Text style={{ color: colors.text }}>Mørk tilstand</Text>
        <Switch value={isDark} onValueChange={toggleDark} />
      </View>

      <Text style={[styles.h, { color: colors.text }]}>Standardbutikker</Text>
      <Pressable onPress={selectAll} style={{ marginBottom: 8 }}>
        <Text style={{ color: colors.primary }}>Vælg alle</Text>
      </Pressable>
      {catalog.map((s) => (
        <View
          key={s.key}
          style={[styles.row, { backgroundColor: colors.surface, borderColor: colors.border }]}
        >
          <Text style={{ color: colors.text, flex: 1 }}>{s.label}</Text>
          <Switch
            value={selectedLabels.has(s.label)}
            onValueChange={() => toggleStore(s.label)}
          />
        </View>
      ))}

      <Text style={[styles.h, { color: colors.text }]}>Notifikationer</Text>
      <View style={[styles.row, { backgroundColor: colors.surface, borderColor: colors.border }]}>
        <View style={{ flex: 1 }}>
          <Text style={{ color: colors.text }}>Push</Text>
          <Text style={{ color: colors.textMuted, fontSize: 12 }}>Kommer snart</Text>
        </View>
        <Switch value={pushEnabled} onValueChange={setPushEnabled} />
      </View>
      <View style={[styles.row, { backgroundColor: colors.surface, borderColor: colors.border }]}>
        <View style={{ flex: 1 }}>
          <Text style={{ color: colors.text }}>Nyhedsbrev</Text>
          <Text style={{ color: colors.textMuted, fontSize: 12 }}>Kommer snart</Text>
        </View>
        <Switch value={newsletter} onValueChange={setNewsletter} />
      </View>

      <Text style={[styles.h, { color: colors.text }]}>Feedback</Text>
      <Pressable
        onPress={() => navigation.navigate('Feedback')}
        style={[styles.row, { backgroundColor: colors.surface, borderColor: colors.border }]}
      >
        <Text style={{ color: colors.primary }}>Send feedback eller meld en fejl</Text>
      </Pressable>

      <Text style={[styles.h, { color: colors.text }]}>Juridisk</Text>
      {(
        [
          ['Vilkår', 'terms'],
          ['Privatliv', 'privacy'],
          ['Om os', 'about'],
        ] as const
      ).map(([label, kind]) => (
        <Pressable
          key={kind}
          onPress={() => navigation.navigate('Legal', { kind })}
          style={[styles.row, { backgroundColor: colors.surface, borderColor: colors.border }]}
        >
          <Text style={{ color: colors.primary }}>{label}</Text>
        </Pressable>
      ))}

      <Text style={[styles.meta, { color: colors.textMuted }]}>
        Flavor: {env.flavor} · RPC-suffix: {env.rpcSuffix || '(prod)'} · API: {env.apiBaseUrl}
      </Text>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  h: { fontSize: 16, fontWeight: '700', marginTop: 20, marginBottom: 8 },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: 14,
    borderRadius: 10,
    borderWidth: 1,
    marginBottom: 8,
  },
  meta: { marginTop: 24, fontSize: 12, marginBottom: 40 },
});
