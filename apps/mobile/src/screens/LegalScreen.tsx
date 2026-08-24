import React from 'react';
import { Linking, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import { env } from '../config/env';
import { useTheme } from '../theme/ThemeContext';
import type { RootStackParamList } from '../navigation/types';

type Props = NativeStackScreenProps<RootStackParamList, 'Legal'>;

const WEB_PATHS: Record<Props['route']['params']['kind'], string> = {
  terms: '/terms-of-service',
  privacy: '/privacy',
  about: '/about',
};

const TITLES: Record<Props['route']['params']['kind'], string> = {
  terms: 'Vilkår og betingelser',
  privacy: 'Privatlivspolitik',
  about: 'Om MadShopper',
};

function TermsBody({ colors }: { colors: ReturnType<typeof useTheme>['colors'] }) {
  return (
    <>
      <Paragraph colors={colors}>
        MadShopper er en gratis tjeneste til prissammenligning af dagligvarer på tværs af danske
        butikker. Priser og tilbud opdateres løbende, men vi kan ikke garantere at alle priser
        altid er 100% opdaterede — tjek altid prisen i butikken eller butikkens egen app før køb.
      </Paragraph>
      <Paragraph colors={colors}>
        Du må ikke bruge tjenesten til automatiseret dataudtræk (scraping), forsøg på at
        overbelaste vores servere, eller på anden vis misbruge appen.
      </Paragraph>
      <Paragraph colors={colors}>
        Vi forbeholder os retten til at ændre eller lukke funktioner i appen uden varsel.
        Fortsat brug af appen efter ændringer betragtes som accept af de opdaterede vilkår.
      </Paragraph>
    </>
  );
}

function PrivacyBody({ colors }: { colors: ReturnType<typeof useTheme>['colors'] }) {
  return (
    <>
      <Paragraph colors={colors}>
        Vi indsamler kun de oplysninger, der er nødvendige for at appen kan fungere: din
        kurv (lokalt på enheden, og hos en ekstern database- og loginudbyder hvis du er
        logget ind - beskyttet af adgangskontrol og krypteret forbindelse/lager, men ikke
        krypteret feltvis så kun du kan læse den), dine butiksvalg, samt anonym statistik
        over hvilke varer der lægges i kurven (uden personhenførbare data).
      </Paragraph>
      <Paragraph colors={colors}>
        Opretter du en konto, gemmer vi din email og et krypteret kodeord hos vores
        database- og loginudbyder. Ved login med Google eller Apple modtager vi kun din
        email og dit navn — aldrig dit kodeord hos dem.
      </Paragraph>
      <Paragraph colors={colors}>
        <Text style={{ fontWeight: '700' }}>Delt kurv og prisalarm (valgfrit): </Text>
        Opretter eller tilslutter du dig en delt kurv med op til 5 andre, ser I hinandens
        visningsnavn, varer og gemte lister. Opretter du en prisalarm, gemmes din
        e-mailadresse sammen med den, så en udbyder af e-mailudsendelse på vores vegne kan
        sende dig én mail, når prisen er nået.
      </Paragraph>
      <Paragraph colors={colors}>
        <Text style={{ fontWeight: '700' }}>Feedback: </Text>
        Skriver du til os via Feedback, sendes din besked (og et evt. navn/e-mail du selv
        angiver) videre til et administrativt regneark hos en databehandler, som vi selv
        rydder op i. Feedback er ikke koblet til en evt. konto og fjernes derfor ikke
        automatisk, hvis du senere sletter kontoen.
      </Paragraph>
      <Paragraph colors={colors}>
        <Text style={{ fontWeight: '700' }}>App-tilladelser: </Text>
        I stedet for cookies (som på vores hjemmeside) bruger app-versionen kun de
        systemtilladelser, du selv godkender ved installation eller første brug. Vi beder
        ikke om adgang til kamera, kontakter, placering eller notifikationer - prisalarmer
        sendes som e-mail, ikke som push-besked. Skulle det ændre sig, opdateres denne
        tekst og din telefons indstillinger viser altid, hvilke tilladelser appen reelt har.
      </Paragraph>
      <Paragraph colors={colors}>
        Du kan til enhver tid slette din konto direkte i appen under Indstillinger → Konto
        → "Slet konto". Det fjerner med det samme din konto, din gemte kurv,
        besparelsesoversigt, prisalarmer og din plads i en evt. delt kurv (er du ejer med
        andre medlemmer tilbage, overdrages ejerskabet automatisk i stedet for at slette
        kurven under dem). Feedback du har sendt, er som nævnt en separat henvendelse og
        fjernes ikke automatisk.
      </Paragraph>
      <Paragraph colors={colors}>
        Fuld liste over hvem der behandler data på vores vegne, dine rettigheder efter
        databeskyttelsesreglerne, og hvordan du klager til Datatilsynet, står i
        web-versionen af denne politik (link nederst).
      </Paragraph>
    </>
  );
}

function AboutBody({ colors }: { colors: ReturnType<typeof useTheme>['colors'] }) {
  return (
    <>
      <Paragraph colors={colors}>
        MadShopper hjælper dig med at finde de billigste dagligvarer på tværs af 14+
        danske butikker — Rema 1000, Bilka, Netto, Føtex, Meny, Spar, SuperBrugsen,
        Brugsen, Kvickly, Min Købmand, 365 Discount, Lidl, Løvbjerg og ABC Lavpris.
      </Paragraph>
      <Paragraph colors={colors}>
        Læg varer i kurven, og lad os finde den butik — eller den kombination af butikker
        — der giver dig den laveste samlede pris. Del kurven med familien, gem lister til
        næste indkøb, og hold øje med prishistorik og tilbud.
      </Paragraph>
      <Paragraph colors={colors}>
        MadShopper er et uafhængigt projekt og er ikke tilknyttet nogen af de nævnte
        butikskæder.
      </Paragraph>
    </>
  );
}

function Paragraph({
  children,
  colors,
}: {
  children: React.ReactNode;
  colors: ReturnType<typeof useTheme>['colors'];
}) {
  return <Text style={[styles.paragraph, { color: colors.textMuted }]}>{children}</Text>;
}

export function LegalScreen({ route }: Props) {
  const { kind } = route.params;
  const { colors } = useTheme();

  return (
    <ScrollView style={{ flex: 1, backgroundColor: colors.bg }} contentContainerStyle={{ padding: 20 }}>
      <Text style={[styles.title, { color: colors.text }]}>{TITLES[kind]}</Text>

      {kind === 'terms' ? <TermsBody colors={colors} /> : null}
      {kind === 'privacy' ? <PrivacyBody colors={colors} /> : null}
      {kind === 'about' ? <AboutBody colors={colors} /> : null}

      <View style={[styles.contactBox, { backgroundColor: colors.surface, borderColor: colors.border }]}>
        <Text style={{ color: colors.text, fontWeight: '600', marginBottom: 4 }}>Spørgsmål?</Text>
        <Pressable onPress={() => void Linking.openURL('mailto:kontakt@madshopper.dk')}>
          <Text style={{ color: colors.primary }}>kontakt@madshopper.dk</Text>
        </Pressable>
      </View>

      <Pressable
        onPress={() => void Linking.openURL(`${env.apiBaseUrl}${WEB_PATHS[kind]}`)}
        style={styles.webLink}
      >
        <Text style={{ color: colors.textMuted, fontSize: 12 }}>Se web-version</Text>
      </Pressable>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  title: { fontSize: 22, fontWeight: '800', marginBottom: 16 },
  paragraph: { fontSize: 14, lineHeight: 21, marginBottom: 14 },
  contactBox: { padding: 14, borderRadius: 12, borderWidth: 1, marginTop: 8 },
  webLink: { marginTop: 20, marginBottom: 32, alignItems: 'center' },
});
