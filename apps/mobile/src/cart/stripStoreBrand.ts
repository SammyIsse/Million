/** stripStoreBrand — 1:1 fra script.js */

const PREFIXES = [
  'rema 1000 ',
  'rema ',
  'salling ',
  'coop ',
  'xtra ',
  'änglamark ',
  'irma ',
  'first price ',
  'fp ',
  'grøn balance ',
  'gestus ',
  'levevis ',
  'vores ',
  'karma ',
  'cirkel ',
  'bilka ',
  'meny ',
  'spar ',
  'min købmand ',
  'min kobmand ',
];

export function stripStoreBrand(name: string | null | undefined): string {
  if (!name) return name || '';
  const lower = name.toLowerCase();
  for (const prefix of PREFIXES) {
    if (lower.startsWith(prefix)) {
      const stripped = name.slice(prefix.length).trim();
      return stripped.charAt(0).toUpperCase() + stripped.slice(1).toLowerCase();
    }
  }
  if (name === name.toUpperCase() && name.length > 1) {
    return name.charAt(0) + name.slice(1).toLowerCase();
  }
  return name;
}

/**
 * cartItemTitle — 1:1 fra script.js.
 *
 * Varianten ligger tit i beskrivelsen, ikke i navnet: baade "Coca cola
 * original" og "Coca cola zero sugar" hedder name="COCA COLA". Uden dette
 * viste kurven to identiske linjer.
 */
export function cartItemTitle(item: { name?: string | null; description?: string | null }): string {
  const base = stripStoreBrand(item.name || '');
  const desc = String(item.description || '').trim();
  if (!desc) return base;
  const b = base.toLowerCase();
  const d = desc.toLowerCase();
  if (d.startsWith(b) && d.length > b.length) return stripStoreBrand(desc);
  return base;
}
