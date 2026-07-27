import React, { useEffect, useState } from 'react';
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import * as AppleAuthentication from 'expo-apple-authentication';
import { useAuth } from '../auth/AuthContext';
import { useTheme } from '../theme/ThemeContext';
import type { RootStackParamList } from '../navigation/types';

type Props = NativeStackScreenProps<RootStackParamList, 'Auth'>;

type Mode = 'login' | 'signup' | 'reset' | 'newpassword';

export function AuthScreen({ navigation }: Props) {
  const { colors, isDark } = useTheme();
  const {
    user,
    signInEmail,
    signUpEmail,
    signInGoogle,
    signInApple,
    resetPassword,
    updatePassword,
    logout,
  } = useAuth();
  const [appleAvailable, setAppleAvailable] = useState(false);

  const [mode, setMode] = useState<Mode>('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setError(null);
    setInfo(null);
  }, [mode]);

  useEffect(() => {
    if (Platform.OS !== 'ios') return;
    void AppleAuthentication.isAvailableAsync().then(setAppleAvailable);
  }, []);

  const submit = async () => {
    setError(null);
    setInfo(null);
    setBusy(true);
    try {
      if (mode === 'login') {
        const err = await signInEmail(email.trim(), password);
        if (err) setError(err);
        else navigation.goBack();
      } else if (mode === 'signup') {
        if (!displayName.trim()) {
          setError('Navn er påkrævet');
          return;
        }
        const err = await signUpEmail(email.trim(), password, displayName.trim());
        if (err) setError(err);
        else setInfo('Tjek din mail for at bekræfte oprettelsen.');
      } else if (mode === 'reset') {
        const err = await resetPassword(email.trim());
        if (err) setError(err);
        else setInfo('Vi har sendt et link til nulstilling af adgangskode.');
      } else if (mode === 'newpassword') {
        const err = await updatePassword(password);
        if (err) setError(err);
        else {
          setInfo('Adgangskode opdateret.');
          navigation.goBack();
        }
      }
    } finally {
      setBusy(false);
    }
  };

  const submitGoogle = async () => {
    setError(null);
    setInfo(null);
    setBusy(true);
    try {
      const err = await signInGoogle();
      if (err) setError(err);
      else navigation.goBack();
    } finally {
      setBusy(false);
    }
  };

  const submitApple = async () => {
    setError(null);
    setInfo(null);
    setBusy(true);
    try {
      const err = await signInApple();
      if (err) setError(err);
      else navigation.goBack();
    } finally {
      setBusy(false);
    }
  };

  if (user) {
    return (
      <View style={[styles.center, { backgroundColor: colors.bg }]}>
        <Text style={{ color: colors.text, fontSize: 16, marginBottom: 16 }}>
          Du er logget ind som {user.email}
        </Text>
        <Pressable
          onPress={() => void logout()}
          style={[styles.btnOutline, { borderColor: colors.border }]}
        >
          <Text style={{ color: colors.text }}>Log ud</Text>
        </Pressable>
      </View>
    );
  }

  return (
    <KeyboardAvoidingView
      style={{ flex: 1, backgroundColor: colors.bg }}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <ScrollView contentContainerStyle={{ padding: 20 }} keyboardShouldPersistTaps="handled">
        <Text style={[styles.title, { color: colors.text }]}>
          {mode === 'login' && 'Log ind'}
          {mode === 'signup' && 'Opret konto'}
          {mode === 'reset' && 'Nulstil adgangskode'}
          {mode === 'newpassword' && 'Vælg ny adgangskode'}
        </Text>

        {mode === 'signup' ? (
          <TextInput
            value={displayName}
            onChangeText={setDisplayName}
            placeholder="Dit navn"
            placeholderTextColor={colors.textMuted}
            style={[styles.input, { backgroundColor: colors.surface, color: colors.text, borderColor: colors.border }]}
          />
        ) : null}

        {mode !== 'newpassword' ? (
          <TextInput
            value={email}
            onChangeText={setEmail}
            placeholder="Email"
            placeholderTextColor={colors.textMuted}
            autoCapitalize="none"
            keyboardType="email-address"
            style={[styles.input, { backgroundColor: colors.surface, color: colors.text, borderColor: colors.border }]}
          />
        ) : null}

        {mode !== 'reset' ? (
          <TextInput
            value={password}
            onChangeText={setPassword}
            placeholder={mode === 'newpassword' ? 'Ny adgangskode' : 'Adgangskode'}
            placeholderTextColor={colors.textMuted}
            secureTextEntry
            style={[styles.input, { backgroundColor: colors.surface, color: colors.text, borderColor: colors.border }]}
          />
        ) : null}

        {error ? <Text style={[styles.error, { color: colors.sale }]}>{error}</Text> : null}
        {info ? <Text style={[styles.info, { color: colors.badge }]}>{info}</Text> : null}

        <Pressable
          onPress={() => void submit()}
          disabled={busy}
          style={[styles.btn, { backgroundColor: colors.primary, opacity: busy ? 0.7 : 1 }]}
        >
          {busy ? (
            <ActivityIndicator color="#fff" />
          ) : (
            <Text style={styles.btnText}>
              {mode === 'login' && 'Log ind'}
              {mode === 'signup' && 'Opret konto'}
              {mode === 'reset' && 'Send link'}
              {mode === 'newpassword' && 'Gem adgangskode'}
            </Text>
          )}
        </Pressable>

        {mode === 'login' || mode === 'signup' ? (
          <Pressable
            onPress={() => void submitGoogle()}
            disabled={busy}
            style={[styles.btnOutline, { borderColor: colors.border, marginTop: 10 }]}
          >
            <Text style={{ color: colors.text, fontWeight: '600' }}>Fortsæt med Google</Text>
          </Pressable>
        ) : null}

        {(mode === 'login' || mode === 'signup') && appleAvailable ? (
          <AppleAuthentication.AppleAuthenticationButton
            buttonType={AppleAuthentication.AppleAuthenticationButtonType.CONTINUE}
            buttonStyle={
              isDark
                ? AppleAuthentication.AppleAuthenticationButtonStyle.WHITE
                : AppleAuthentication.AppleAuthenticationButtonStyle.BLACK
            }
            cornerRadius={12}
            style={styles.appleBtn}
            onPress={() => void submitApple()}
          />
        ) : null}

        <View style={styles.links}>
          {mode !== 'login' ? (
            <Pressable onPress={() => setMode('login')}>
              <Text style={{ color: colors.primary }}>Log ind</Text>
            </Pressable>
          ) : null}
          {mode !== 'signup' ? (
            <Pressable onPress={() => setMode('signup')}>
              <Text style={{ color: colors.primary }}>Opret konto</Text>
            </Pressable>
          ) : null}
          {mode !== 'reset' ? (
            <Pressable onPress={() => setMode('reset')}>
              <Text style={{ color: colors.primary }}>Glemt adgangskode?</Text>
            </Pressable>
          ) : null}
        </View>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 20 },
  title: { fontSize: 22, fontWeight: '800', marginBottom: 16 },
  input: {
    borderWidth: 1,
    borderRadius: 12,
    paddingHorizontal: 14,
    paddingVertical: 12,
    fontSize: 16,
    marginBottom: 12,
  },
  error: { marginBottom: 12 },
  info: { marginBottom: 12 },
  btn: { padding: 14, borderRadius: 12, alignItems: 'center' },
  btnText: { color: '#fff', fontWeight: '700', fontSize: 16 },
  btnOutline: { padding: 14, borderRadius: 12, alignItems: 'center', borderWidth: 1 },
  appleBtn: { height: 48, marginTop: 10 },
  links: {
    marginTop: 20,
    flexDirection: 'row',
    flexWrap: 'wrap',
    justifyContent: 'center',
    gap: 16,
  },
});
