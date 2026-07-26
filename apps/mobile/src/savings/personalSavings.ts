/**
 * Personlig besparelse — client-side via Supabase RPC (JWT).
 * Spejler web's get_personal_savings / record_compare_savings.
 */
import { rpcName } from '../config/env';
import { getSupabase } from '../auth/supabase';
import type { ScoStoreResult } from '../cart/sco';

export type PersonalSavings = {
  available: boolean;
  amount: number;
  top_pct: number;
  month_key: string;
  prev_amount: number;
  prev_month_key: string;
  show_prev: boolean;
  message: string;
};

const LOGIN_MSG = 'Log ind for at tracke besparelse';

export function emptySavings(loggedIn: boolean): PersonalSavings {
  return {
    available: loggedIn,
    amount: 0,
    top_pct: 100,
    month_key: '',
    prev_amount: 0,
    prev_month_key: '',
    show_prev: false,
    message: loggedIn ? '' : LOGIN_MSG,
  };
}

function parsePayload(data: unknown): PersonalSavings | null {
  if (!data || typeof data !== 'object') return null;
  const d = data as Record<string, unknown>;
  return {
    available: Boolean(d.available),
    amount: Number(d.amount) || 0,
    top_pct: Math.max(1, Math.min(100, parseInt(String(d.top_pct ?? 100), 10) || 100)),
    month_key: String(d.month_key || ''),
    prev_amount: Number(d.prev_amount) || 0,
    prev_month_key: String(d.prev_month_key || ''),
    show_prev: Boolean(d.show_prev),
    message: String(d.message || ''),
  };
}

/** Billigste/dyreste blandt butikker med fuld kurv-dækning. */
export function fullCoveragePriceRange(
  stores: ScoStoreResult[],
): { cheap: number; expensive: number } | null {
  const full = stores.filter((s) => s.totalItems > 0 && s.coverage === s.totalItems);
  if (full.length < 2) return null;
  let cheap = full[0].totalPrice;
  let expensive = full[0].totalPrice;
  for (let i = 1; i < full.length; i++) {
    const p = full[i].totalPrice;
    if (p < cheap) cheap = p;
    if (p > expensive) expensive = p;
  }
  if (!(expensive > cheap)) return null;
  return { cheap, expensive };
}

export async function fetchPersonalSavings(): Promise<PersonalSavings> {
  const sb = getSupabase();
  if (!sb) return emptySavings(false);
  const { data: sessionData } = await sb.auth.getSession();
  if (!sessionData.session) return emptySavings(false);
  const { data, error } = await sb.rpc(rpcName('get_personal_savings'));
  if (error) return emptySavings(true);
  return parsePayload(data) || emptySavings(true);
}

export async function recordCompareSavings(
  cheap: number,
  expensive: number,
): Promise<PersonalSavings | null> {
  const sb = getSupabase();
  if (!sb) return null;
  const { data: sessionData } = await sb.auth.getSession();
  if (!sessionData.session) return null;
  const { data, error } = await sb.rpc(rpcName('record_compare_savings'), {
    p_cheap: cheap,
    p_expensive: expensive,
  });
  if (error) return null;
  return parsePayload(data);
}

const DK_MONTHS = [
  '',
  'januar',
  'februar',
  'marts',
  'april',
  'maj',
  'juni',
  'juli',
  'august',
  'september',
  'oktober',
  'november',
  'december',
];

export function monthLabel(monthKey: string): string {
  const parts = (monthKey || '').split('-');
  if (parts.length < 2) return monthKey || '';
  const m = parseInt(parts[1], 10);
  const name = DK_MONTHS[m] || monthKey;
  return name.charAt(0).toUpperCase() + name.slice(1);
}

export function formatKr(n: number): string {
  const v = Number(n) || 0;
  return v.toLocaleString('da-DK', {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  });
}
