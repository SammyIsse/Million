import React from 'react';
import { Alert, Pressable, ScrollView, StyleSheet, Switch, Text, TextInput, View } from 'react-native';
import { useNavigation } from '@react-navigation/native';
import type { NativeStackNavigationProp } from '@react-navigation/native-stack';
import { useAuth } from '../auth/AuthContext';
import { useStoreCatalog } from '../stores/StoreCatalogContext';
import { useTheme } from '../theme/ThemeContext';
import { env } from '../config/env';
import type { RootStackParamList } from '../navigation/types';

export function SettingsScreen() {
  const { colors, mode, setMode } = useTheme();
  const { catalog, selectedLabels, toggleStore, selectAll } = useStoreCatalog();
  const { user, displayName, logout, deleteAccount, saveDisplayName } = useAuth();
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();
  const [deleting, setDeleting] = React.useState(false);

  // Web-paritet (base.html #auth-account-name / auth.js saveDisplayNameFromAccount)
  // — funktionen fandtes allerede i AuthContext, men uden nogen skærm der kaldte
  // den. Fundet under paritetsrevisionen 2026-08-17.
  const [nameInput, setNameInput] = React.useState(displayName);
  const [nameSaving, setNameSaving] = React.useState(false);
  const [nameMsg, setNameMsg] = React.useState<{ text: string; error: boolean } | null>(null);
  React.useEffect(() => setNameInput(displayName), [displayName]);

  const onSaveName = React.useCallback(async () => {
    const trimmed = nameInput.trim();
    if (!trimmed) {
      setNameMsg({ text: 'Skriv et navn (max 40 tegn).', error: true });
      return;
    }
    setNameSaving(true);
    setNameMsg(null);
    const err = await saveDisplayName(trimmed);
    setNameSaving(false);
    setNameMsg(err ? { text: err, error: true } : { text: 'Navnet er gemt.', error: false });
  }, [nameInput, saveDisplayName]);

  // Apple Guideline 5.1.1(v): sletning skal kunne startes inde i appen.
  const confirmDelete = React.useCallback(() => {
    Alert.alert(
      'Slet konto',
      'Din konto, din gemte kurv og din besparelseshistorik slettes permanent. Det kan ikke fortrydes.',
      [
        { text: 'Annullér', style: 'cancel' },
        {
          text: 'Slet konto',
          style: 'destructive',
          onPress: () => {
            setDeleting(true);
            void deleteAccount()
              .then((err) => {
                if (err) Alert.alert('Kunne ikke slette kontoen', err);
                else Alert.alert('Konto slettet', 'Din konto og dine data er slettet.');
              })
              .finally(() => setDeleting(false));
          },
        },
      ],
    );
  }, [deleteAccount]);

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
      ) : null}
      {user ? (
        <View style={[styles.row, styles.nameRow, { backgroundColor: colors.surface, borderColor: colors.border }]}>
          <Text style={{ color: colors.text, fontWeight: '600', marginBottom: 8 }}>Dit navn</Text>
          <View style={{ flexDirection: 'row', gap: 8, alignItems: 'center' }}>
            <TextInput
              value={nameInput}
              onChangeText={setNameInput}
              placeholder="Vises for andre i en delt kurv"
              placeholderTextColor={colors.textMuted}
              maxLength={40}
              style={[styles.nameInput, { color: colors.text, borderColor: colors.border }]}
            />
            <Pressable
              onPress={() => void onSaveName()}
              disabled={nameSaving}
              style={[styles.saveBtn, { backgroundColor: colors.primary, opacity: nameSaving ? 0.6 : 1 }]}
            >
              <Text style={{ color: '#fff', fontWeight: '700' }}>{nameSaving ? '…' : 'Gem'}</Text>
            </Pressable>
          </View>
          {nameMsg ? (
            <Text style={{ color: nameMsg.error ? colors.sale : colors.badge, fontSize: 12, marginTop: 6 }}>
              {nameMsg.text}
            </Text>
          ) : null}
        </View>
      ) : null}
      {user ? (
        <Pressable
          onPress={confirmDelete}
          disabled={deleting}
          style={[
            styles.row,
            { backgroundColor: colors.surface, borderColor: colors.border, opacity: deleting ? 0.5 : 1 },
          ]}
        >
          <View style={{ flex: 1 }}>
            <Text style={{ color: colors.sale, fontWeight: '600' }}>
              {deleting ? 'Sletter konto…' : 'Slet konto'}
            </Text>
            <Text style={{ color: colors.textMuted, fontSize: 12 }}>
              Sletter permanent din konto, gemte kurv og besparelseshistorik
            </Text>
          </View>
        </Pressable>
      ) : (
        <Pressable
          onPress={() => navigation.navigate('Auth')}
          style={[styles.row, { backgroundColor: colors.surface, borderColor: colors.border }]}
        >
          <Text style={{ color: colors.primary, fontWeight: '600' }}>Log ind / Opret konto</Text>
        </Pressable>
      )}

      <Text style={[styles.h, { color: colors.text }]}>Udseende</Text>
      {/* ThemeContext understøttede allerede "system" — kun UI'et manglede en
          vej til det (fundet under paritetsrevisionen 2026-08-17). */}
      <View style={[styles.row, styles.themeRow, { backgroundColor: colors.surface, borderColor: colors.border }]}>
        {(
          [
            ['system', 'Følg system'],
            ['light', 'Lys'],
            ['dark', 'Mørk'],
          ] as const
        ).map(([value, label]) => {
          const active = mode === value;
          return (
            <Pressable
              key={value}
              onPress={() => setMode(value)}
              style={[
                styles.themeOption,
                { backgroundColor: active ? colors.primary : 'transparent' },
              ]}
            >
              <Text style={{ color: active ? '#fff' : colors.text, fontWeight: '600', fontSize: 13 }}>
                {label}
              </Text>
            </Pressable>
          );
        })}
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

      {/* Brugere har intet at bruge flavor/RPC-suffix til - kun vi har. I
          produktion vises derfor kun versionen, som er det, en fejlmelding
          skal indeholde. */}
      <Text style={[styles.meta, { color: colors.textMuted }]}>
        MadShopper {env.appVersion}
        {env.flavor !== 'production'
          ? ` · ${env.flavor} · RPC${env.rpcSuffix || ' (prod)'} · ${env.apiBaseUrl}`
          : ''}
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
  nameRow: { flexDirection: 'column', alignItems: 'stretch' },
  nameInput: {
    flex: 1,
    borderWidth: 1,
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 8,
    fontSize: 14,
  },
  saveBtn: { paddingHorizontal: 16, paddingVertical: 9, borderRadius: 8 },
  themeRow: { padding: 4, gap: 4 },
  themeOption: { flex: 1, alignItems: 'center', paddingVertical: 10, borderRadius: 8 },
  meta: { marginTop: 24, fontSize: 12, marginBottom: 40 },
});
