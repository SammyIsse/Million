import React from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

/**
 * Sidste værn mod hvid skærm.
 *
 * Uden en error boundary river en enkelt uventet render-fejl hele React-træet
 * ned, og brugeren står tilbage med en blank skærm og ingen vej videre end at
 * dræbe appen. Webben har aldrig haft det problem på samme måde: siderne er
 * server-renderede, og fejler dataene, svarer app.py i degraded mode
 * (`_mark_data_degraded`) med en side der stadig kan navigeres.
 *
 * Denne komponent giver appen den samme egenskab: fejlen fanges, brugeren får
 * en forklaring på dansk (samme tone som resten af appen) og en "Prøv igen",
 * der nulstiler boundaryen og gen-monterer træet.
 *
 * BEVIDST uden ekstern crash-rapportering: der findes ingen Sentry/Crashlytics
 * i projektet, og at tilføje en tjeneste er en beslutning om leverandør, pris
 * og privatlivserklæring - ikke noget der skal snige sig ind via en
 * fejlskærm. Fejlen logges derfor til konsollen, som i et EAS-build ender i
 * enhedens log og i App Store Connects crash-rapporter.
 */
type Props = { children: React.ReactNode };
type State = { error: Error | null };

export class ErrorBoundary extends React.Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('[ErrorBoundary]', error, info.componentStack);
  }

  reset = () => this.setState({ error: null });

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    return (
      // Farverne er hårdkodede: fejlen kan være sket INDE i ThemeProvider,
      // så useTheme er ikke til at stole på her.
      <View style={styles.wrap}>
        <ScrollView contentContainerStyle={styles.body}>
          <Text style={styles.title}>Noget gik galt</Text>
          <Text style={styles.text}>
            Der opstod en uventet fejl i appen. Dine varer i kurven er gemt - prøv igen.
          </Text>
          <Pressable onPress={this.reset} style={styles.btn} accessibilityRole="button">
            <Text style={styles.btnText}>Prøv igen</Text>
          </Pressable>
          {__DEV__ ? <Text style={styles.detail}>{String(error.message || error)}</Text> : null}
        </ScrollView>
      </View>
    );
  }
}

const styles = StyleSheet.create({
  wrap: { flex: 1, backgroundColor: '#F7F8F5' },
  body: { flexGrow: 1, justifyContent: 'center', padding: 24, gap: 12 },
  title: { fontSize: 22, fontWeight: '800', color: '#1A1C19', textAlign: 'center' },
  text: { fontSize: 15, color: '#44483F', textAlign: 'center', lineHeight: 21 },
  btn: {
    marginTop: 8,
    alignSelf: 'center',
    backgroundColor: '#059669',
    paddingHorizontal: 28,
    paddingVertical: 13,
    borderRadius: 10,
  },
  btnText: { color: '#fff', fontWeight: '700', fontSize: 15 },
  detail: { marginTop: 20, fontSize: 12, color: '#8A8F80', textAlign: 'center' },
});
