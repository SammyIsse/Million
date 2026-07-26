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
