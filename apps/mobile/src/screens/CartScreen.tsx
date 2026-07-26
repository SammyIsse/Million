import React, { useMemo, useState } from 'react';
import {
  FlatList,
  Modal,
  Pressable,
  Share,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { useNavigation } from '@react-navigation/native';
import type { NativeStackNavigationProp } from '@react-navigation/native-stack';
import { useAuth } from '../auth/AuthContext';
import { useCart } from '../cart/CartContext';
import { useSharedCart } from '../cart/SharedCartContext';
import type { CartItem } from '../cart/types';
import { useTheme } from '../theme/ThemeContext';
import { StackScreenBody } from '../components/ScreenBody';
import type { RootStackParamList } from '../navigation/types';

type PromptMode = 'save' | 'share' | 'join' | null;

export function CartScreen() {
  const { colors } = useTheme();
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();
  const { items, updateQuantity, removeItem, clearCart } = useCart();
  const { user } = useAuth();
  const { active, title, members, maxMembers, inviteUrl, createShared, joinShared, leaveShared, saveList } =
    useSharedCart();

  const [prompt, setPrompt] = useState<PromptMode>(null);
  const [promptValue, setPromptValue] = useState('');
  const [promptError, setPromptError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const groups = useMemo(() => {
    const map = new Map<string, CartItem[]>();
    for (const item of items) {
      const cat = item.category || 'Andre varer';
      if (!map.has(cat)) map.set(cat, []);
      map.get(cat)!.push(item);
    }
    return Array.from(map.entries());
  }, [items]);

  /** Footer-total UDEN multi-deal (docs/native-app.md §7.4). */
  const footerTotal = useMemo(
    () => items.reduce((sum, i) => sum + i.price * i.quantity, 0),
    [items],
  );

  const openPrompt = (mode: PromptMode) => {
    setPromptValue('');
    setPromptError(null);
    setPrompt(mode);
  };

  const closePrompt = () => {
    setPrompt(null);
    setPromptValue('');
    setPromptError(null);
  };

  const submitPrompt = async () => {
    setPromptError(null);
    setBusy(true);
    try {
      if (prompt === 'save') {
        if (!promptValue.trim()) {
          setPromptError('Angiv et navn');
          return;
        }
        const err = await saveList(promptValue.trim());
        if (err) setPromptError(err);
        else closePrompt();
      } else if (prompt === 'share') {
        const err = await createShared(promptValue.trim() || 'Fælles kurv');
        if (err) setPromptError(err);
        else closePrompt();
      } else if (prompt === 'join') {
        if (!promptValue.trim()) {
          setPromptError('Angiv en kode');
          return;
        }
        const err = await joinShared(promptValue.trim());
        if (err) setPromptError(err);
        else closePrompt();
      }
    } finally {
      setBusy(false);
    }
  };

  const onSharePress = () => {
    if (!user) {
      navigation.navigate('Auth');
      return;
    }
    openPrompt('share');
  };

  const onInvite = () => {
    if (inviteUrl) void Share.share({ message: inviteUrl });
  };

  if (!items.length) {
    return (
      <StackScreenBody style={{ backgroundColor: colors.bg }}>
        <View style={styles.center}>
          <Text style={{ color: colors.textMuted }}>Kurven er tom</Text>
        </View>
      </StackScreenBody>
    );
  }

  return (
    <StackScreenBody style={{ backgroundColor: colors.bg }}>
      {active ? (
        <View style={[styles.sharedBar, { backgroundColor: colors.primaryMuted, borderColor: colors.border }]}>
          <View style={{ flex: 1 }}>
            <Text style={{ color: colors.text, fontWeight: '700' }}>{title}</Text>
            <Text style={{ color: colors.textMuted, fontSize: 12 }}>
              {members.length}/{maxMembers} medlemmer
            </Text>
          </View>
          <Pressable onPress={onInvite} style={[styles.sharedBtn, { borderColor: colors.primary }]}>
            <Text style={{ color: colors.primary, fontWeight: '600' }}>Inviter</Text>
          </Pressable>
          <Pressable onPress={() => void leaveShared()} style={[styles.sharedBtn, { borderColor: colors.sale }]}>
            <Text style={{ color: colors.sale, fontWeight: '600' }}>Forlad</Text>
          </Pressable>
        </View>
      ) : null}

      <FlatList
        style={{ flex: 1 }}
        data={groups}
        keyExtractor={([cat]) => cat}
        contentContainerStyle={{ padding: 16 }}
        renderItem={({ item: [cat, catItems] }) => (
          <View style={{ marginBottom: 16 }}>
            <Text style={[styles.cat, { color: colors.text }]}>{cat}</Text>
            {catItems.map((item) => (
              <View
                key={item.id}
                style={[styles.row, { backgroundColor: colors.surface, borderColor: colors.border }]}
              >
                <View style={{ flex: 1 }}>
                  <Text style={{ color: colors.text, fontWeight: '600' }}>{item.name}</Text>
                  <Text style={{ color: colors.textMuted, marginTop: 2 }}>
                    {item.store} · {(item.price * item.quantity).toFixed(2)} kr
                  </Text>
                </View>
                <View style={styles.qty}>
                  <Pressable onPress={() => updateQuantity(item.id, item.quantity - 1)}>
                    <Text style={[styles.qtyBtn, { color: colors.primary }]}>−</Text>
                  </Pressable>
                  <Text style={{ color: colors.text, minWidth: 24, textAlign: 'center' }}>
                    {item.quantity}
                  </Text>
                  <Pressable onPress={() => updateQuantity(item.id, item.quantity + 1)}>
                    <Text style={[styles.qtyBtn, { color: colors.primary }]}>+</Text>
                  </Pressable>
                </View>
                <Pressable onPress={() => removeItem(item.id)} style={{ marginLeft: 8 }}>
                  <Text style={{ color: colors.sale }}>Slet</Text>
                </Pressable>
              </View>
            ))}
          </View>
        )}
      />

      <View style={[styles.footer, { backgroundColor: colors.surface, borderColor: colors.border }]}>
        <Text style={[styles.total, { color: colors.text }]}>
          Total: {footerTotal.toFixed(2)} kr
        </Text>
        <Text style={{ color: colors.textMuted, fontSize: 12, marginBottom: 10 }}>
          (uden multi-deal — SCO beregner deals separat)
        </Text>

        <View style={styles.actionsRow}>
          <Pressable
            onPress={() => navigation.navigate('Sco')}
            style={[styles.primaryAction, { backgroundColor: colors.primary }]}
          >
            <Text style={styles.primaryActionText}>Find billigste</Text>
          </Pressable>
          <Pressable
            onPress={() => navigation.navigate('Route')}
            style={[styles.primaryAction, { backgroundColor: colors.primary }]}
          >
            <Text style={styles.primaryActionText}>Butiksrute</Text>
          </Pressable>
        </View>

        <View style={styles.actionsRow}>
          <Pressable onPress={clearCart} style={[styles.action, { borderColor: colors.border }]}>
            <Text style={{ color: colors.text }}>Ryd</Text>
          </Pressable>
          <Pressable onPress={() => openPrompt('save')} style={[styles.action, { borderColor: colors.border }]}>
            <Text style={{ color: colors.text }}>Gem liste</Text>
          </Pressable>
          {!active ? (
            <>
              <Pressable onPress={onSharePress} style={[styles.action, { borderColor: colors.border }]}>
                <Text style={{ color: colors.text }}>Del kurv</Text>
              </Pressable>
              <Pressable onPress={() => openPrompt('join')} style={[styles.action, { borderColor: colors.border }]}>
                <Text style={{ color: colors.text }}>Join</Text>
              </Pressable>
            </>
          ) : null}
        </View>
      </View>

      <Modal visible={prompt !== null} transparent animationType="fade" onRequestClose={closePrompt}>
        <View style={styles.modalBackdrop}>
          <View style={[styles.modalCard, { backgroundColor: colors.surface }]}>
            <Text style={{ color: colors.text, fontWeight: '700', fontSize: 16, marginBottom: 10 }}>
              {prompt === 'save' && 'Gem liste'}
              {prompt === 'share' && 'Del kurv'}
              {prompt === 'join' && 'Join delt kurv'}
            </Text>
            <TextInput
              value={promptValue}
              onChangeText={setPromptValue}
              placeholder={
                prompt === 'save' ? 'Navn på liste' : prompt === 'share' ? 'Navn på kurv (valgfrit)' : 'Invitationskode'
              }
              placeholderTextColor={colors.textMuted}
              autoCapitalize={prompt === 'join' ? 'none' : 'sentences'}
              style={[styles.input, { backgroundColor: colors.bg, color: colors.text, borderColor: colors.border }]}
            />
            {promptError ? <Text style={{ color: colors.sale, marginBottom: 8 }}>{promptError}</Text> : null}
            <View style={styles.modalActions}>
              <Pressable onPress={closePrompt} style={[styles.action, { borderColor: colors.border }]}>
                <Text style={{ color: colors.text }}>Annuller</Text>
              </Pressable>
              <Pressable
                onPress={() => void submitPrompt()}
                disabled={busy}
                style={[styles.primaryAction, { backgroundColor: colors.primary, opacity: busy ? 0.7 : 1 }]}
              >
                <Text style={styles.primaryActionText}>Gem</Text>
              </Pressable>
            </View>
          </View>
        </View>
      </Modal>
    </StackScreenBody>
  );
}

const styles = StyleSheet.create({
  center: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  cat: { fontSize: 16, fontWeight: '700', marginBottom: 8 },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    padding: 12,
    borderRadius: 10,
    borderWidth: 1,
    marginBottom: 8,
  },
  qty: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  qtyBtn: { fontSize: 22, fontWeight: '600', paddingHorizontal: 8 },
  footer: {
    borderTopWidth: 1,
    padding: 16,
  },
  total: { fontSize: 18, fontWeight: '700' },
  actionsRow: { flexDirection: 'row', gap: 8, marginBottom: 8 },
  action: {
    flex: 1,
    padding: 12,
    borderRadius: 10,
    borderWidth: 1,
    alignItems: 'center',
  },
  primaryAction: {
    flex: 1,
    padding: 12,
    borderRadius: 10,
    alignItems: 'center',
  },
  primaryActionText: { color: '#fff', fontWeight: '700' },
  sharedBar: {
    flexDirection: 'row',
    alignItems: 'center',
    padding: 12,
    borderBottomWidth: 1,
    gap: 8,
  },
  sharedBtn: {
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 8,
    borderWidth: 1,
  },
  modalBackdrop: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.5)',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 24,
  },
  modalCard: { width: '100%', borderRadius: 16, padding: 20 },
  input: {
    borderWidth: 1,
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 16,
    marginBottom: 10,
  },
  modalActions: { flexDirection: 'row', gap: 8 },
});
