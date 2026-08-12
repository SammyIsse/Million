import React, { useState } from 'react';
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
import { apiPost } from '../api/client';
import { useAuth } from '../auth/AuthContext';
import { useTheme } from '../theme/ThemeContext';

type FeedbackType = 'feedback' | 'bug' | 'feature' | 'other';

const TYPE_OPTIONS: Array<{ value: FeedbackType; label: string }> = [
  { value: 'feedback', label: 'Feedback' },
  { value: 'bug', label: 'Fejl' },
  { value: 'feature', label: 'Ønske' },
  { value: 'other', label: 'Andet' },
];

const MIN_LEN = 10;
// Skal matche app.py's submit_feedback (len(message) > 500 -> 400). Stod
// tidligere på 5000 her: tælleren viste "800/5000" og lod brugeren sende,
// men serveren afviste alt over 500 med et generisk "prøv igen"-svar (se
// apiPost/client.ts, som ikke viser serverens præcise fejltekst) - lang,
// værdifuld feedback kunne derfor aldrig sendes.
const MAX_LEN = 500;

export function FeedbackScreen() {
  const { colors } = useTheme();
  const { user } = useAuth();

  const [type, setType] = useState<FeedbackType>('feedback');
  const [name, setName] = useState('');
  const [email, setEmail] = useState(user?.email || '');
  const [subject, setSubject] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  const submit = async () => {
    setError(null);
    const trimmed = message.trim();
    if (trimmed.length < MIN_LEN) {
      setError(`Besked skal være mindst ${MIN_LEN} tegn`);
      return;
    }
    if (trimmed.length > MAX_LEN) {
      setError(`Besked må maks være ${MAX_LEN} tegn`);
      return;
    }
    setBusy(true);
    try {
      await apiPost('/api/feedback', {
        type,
        name: name.trim() || undefined,
        email: email.trim() || undefined,
        subject: subject.trim() || undefined,
        message: trimmed,
      });
      setSuccess(true);
      setMessage('');
      setSubject('');
    } catch {
      setError('Kunne ikke sende feedback. Prøv igen senere.');
    } finally {
      setBusy(false);
    }
  };

  if (success) {
    return (
      <View style={[styles.center, { backgroundColor: colors.bg }]}>
        <Text style={{ color: colors.badge, fontSize: 18, fontWeight: '700', marginBottom: 8 }}>
          Tak for din feedback!
        </Text>
        <Text style={{ color: colors.textMuted, textAlign: 'center', marginBottom: 20 }}>
          Vi læser alle beskeder og bruger dem til at forbedre MadShopper.
        </Text>
        <Pressable
          onPress={() => setSuccess(false)}
          style={[styles.btnOutline, { borderColor: colors.border }]}
        >
          <Text style={{ color: colors.text }}>Send mere feedback</Text>
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
        <Text style={[styles.title, { color: colors.text }]}>Feedback</Text>
        <Text style={{ color: colors.textMuted, marginBottom: 16 }}>
          Fortæl os hvad du synes, eller meld en fejl
        </Text>

        <View style={styles.typeRow}>
          {TYPE_OPTIONS.map((opt) => {
            const active = type === opt.value;
            return (
              <Pressable
                key={opt.value}
                onPress={() => setType(opt.value)}
                style={[
                  styles.typeChip,
                  {
                    backgroundColor: active ? colors.primary : colors.surface,
                    borderColor: active ? colors.primary : colors.border,
                  },
                ]}
              >
                <Text style={{ color: active ? '#fff' : colors.text, fontWeight: '600' }}>
                  {opt.label}
                </Text>
              </Pressable>
            );
          })}
        </View>

        <TextInput
          value={name}
          onChangeText={setName}
          placeholder="Navn (valgfrit)"
          placeholderTextColor={colors.textMuted}
          style={[styles.input, { backgroundColor: colors.surface, color: colors.text, borderColor: colors.border }]}
        />
        <TextInput
          value={email}
          onChangeText={setEmail}
          placeholder="Email (valgfrit)"
          placeholderTextColor={colors.textMuted}
          autoCapitalize="none"
          keyboardType="email-address"
          style={[styles.input, { backgroundColor: colors.surface, color: colors.text, borderColor: colors.border }]}
        />
        <TextInput
          value={subject}
          onChangeText={setSubject}
          placeholder="Emne (valgfrit)"
          placeholderTextColor={colors.textMuted}
          style={[styles.input, { backgroundColor: colors.surface, color: colors.text, borderColor: colors.border }]}
        />
        <TextInput
          value={message}
          onChangeText={setMessage}
          placeholder="Din besked (mindst 10 tegn)"
          placeholderTextColor={colors.textMuted}
          multiline
          numberOfLines={6}
          style={[
            styles.input,
            styles.textarea,
            { backgroundColor: colors.surface, color: colors.text, borderColor: colors.border },
          ]}
        />
        <Text style={{ color: colors.textMuted, fontSize: 12, marginBottom: 12 }}>
          {message.trim().length}/{MAX_LEN} tegn
        </Text>

        {error ? <Text style={[styles.error, { color: colors.sale }]}>{error}</Text> : null}

        <Pressable
          onPress={() => void submit()}
          disabled={busy}
          style={[styles.btn, { backgroundColor: colors.primary, opacity: busy ? 0.7 : 1 }]}
        >
          {busy ? <ActivityIndicator color="#fff" /> : <Text style={styles.btnText}>Send feedback</Text>}
        </Pressable>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24 },
  title: { fontSize: 22, fontWeight: '800' },
  typeRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginBottom: 16 },
  typeChip: { paddingHorizontal: 14, paddingVertical: 8, borderRadius: 16, borderWidth: 1 },
  input: {
    borderWidth: 1,
    borderRadius: 12,
    paddingHorizontal: 14,
    paddingVertical: 12,
    fontSize: 16,
    marginBottom: 12,
  },
  textarea: { minHeight: 120, textAlignVertical: 'top' },
  error: { marginBottom: 12 },
  btn: { padding: 14, borderRadius: 12, alignItems: 'center' },
  btnText: { color: '#fff', fontWeight: '700', fontSize: 16 },
  btnOutline: { padding: 14, borderRadius: 12, alignItems: 'center', borderWidth: 1 },
});
