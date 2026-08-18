import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { fetchStores } from '../api/listing';
import type { StoreInfo } from '../api/types';
import { env } from '../config/env';

const STORES_KEY = 'selectedStores';
const VERSION_KEY = 'madshopper_store_version';

type StoreCatalogValue = {
  catalog: StoreInfo[];
  version: number;
  selectedLabels: Set<string>;
  toggleStore: (label: string) => void;
  selectAll: () => void;
  ready: boolean;
  logoUrl: (logoPath: string) => string;
};

const StoreCatalogContext = createContext<StoreCatalogValue | null>(null);

function labelsAddedSince(
  storesAdded: Record<string, string[]>,
  savedVersion: number,
  currentVersion: number,
): string[] {
  const out: string[] = [];
  for (let v = savedVersion + 1; v <= currentVersion; v++) {
    out.push(...(storesAdded[String(v)] || []));
  }
  return out;
}

export function StoreCatalogProvider({ children }: { children: React.ReactNode }) {
  const [catalog, setCatalog] = useState<StoreInfo[]>([]);
  const [version, setVersion] = useState(0);
  const [selectedLabels, setSelectedLabels] = useState<Set<string>>(new Set());
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await fetchStores();
        if (cancelled) return;
        setCatalog(data.stores);
        setVersion(data.version);
        const savedRaw = await AsyncStorage.getItem(STORES_KEY);
        const savedVersion =
          parseInt((await AsyncStorage.getItem(VERSION_KEY)) || '0', 10) || 0;
        let labels: Set<string>;
        if (savedRaw) {
          try {
            labels = new Set(JSON.parse(savedRaw) as string[]);
          } catch {
            labels = new Set(data.stores.map((s) => s.label));
          }
          for (const label of labelsAddedSince(
            data.stores_added as Record<string, string[]>,
            savedVersion,
            data.version,
          )) {
            labels.add(label);
          }
        } else {
          labels = new Set(data.stores.map((s) => s.label));
        }
        setSelectedLabels(labels);
        await AsyncStorage.setItem(STORES_KEY, JSON.stringify([...labels]));
        await AsyncStorage.setItem(VERSION_KEY, String(data.version));
      } catch {
        // Offline / API nede — tomt katalog; screens viser fejl
      } finally {
        if (!cancelled) setReady(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const persist = useCallback(async (labels: Set<string>) => {
    setSelectedLabels(new Set(labels));
    await AsyncStorage.setItem(STORES_KEY, JSON.stringify([...labels]));
  }, []);

  /**
   * Mindst én butik skal altid vaere valgt - samme spaerre som webben har to
   * steder (script.js: `if (selectedStores.size > 1)` og
   * `if (defaults.length === 0) return;`).
   *
   * Uden den kunne man fravaelge dem alle, og saa modsagde appen sig selv:
   * `storesParam` blev tom, `buildQuery` udelod `stores` helt, og listerne
   * viste derfor ALLE butikker - mens SCO og butiksruten brugte det tomme
   * `selectedLabels` og svarede "Ingen butikker matcher din kurv endnu".
   * To dele af appen viste to forskellige verdener uden nogen forklaring.
   */
  const toggleStore = useCallback(
    (label: string) => {
      const next = new Set(selectedLabels);
      if (next.has(label)) {
        if (next.size <= 1) return; // sidste butik - afvis fravalget
        next.delete(label);
      } else {
        next.add(label);
      }
      void persist(next);
    },
    [persist, selectedLabels],
  );

  const selectAll = useCallback(() => {
    void persist(new Set(catalog.map((s) => s.label)));
  }, [catalog, persist]);

  const logoUrl = useCallback(
    (logoPath: string) =>
      logoPath.startsWith('http') ? logoPath : `${env.apiBaseUrl}${logoPath}`,
    [],
  );

  const value = useMemo(
    () => ({
      catalog,
      version,
      selectedLabels,
      toggleStore,
      selectAll,
      ready,
      logoUrl,
    }),
    [catalog, version, selectedLabels, toggleStore, selectAll, ready, logoUrl],
  );

  return (
    <StoreCatalogContext.Provider value={value}>{children}</StoreCatalogContext.Provider>
  );
}

export function useStoreCatalog() {
  const ctx = useContext(StoreCatalogContext);
  if (!ctx) throw new Error('useStoreCatalog without provider');
  return ctx;
}

/** Query-param når ikke alle butikker er valgt (web-paritet). */
export function storesParam(
  selected: Set<string>,
  catalog: StoreInfo[],
): string[] | undefined {
  if (!catalog.length) return undefined;
  if (selected.size >= catalog.length) return undefined;
  return [...selected];
}
