export const lightColors = {
  bg: '#F7F8F5',
  surface: '#FFFFFF',
  text: '#1A1C19',
  textMuted: '#5C6358',
  border: '#E2E6DE',
  primary: '#1B5E20',
  primaryMuted: '#E8F5E9',
  sale: '#C62828',
  badge: '#2E7D32',
  tabInactive: '#8A9184',
  // Web-paritet (styles.css --yellow/--yellow-light): samme advarselsfarve
  // som .price-insight-badge.fake-deal — et tilbud der næppe er et rigtigt
  // tilbud, IKKE en positiv besparelse.
  warning: '#D97706',
  warningMuted: '#FEF3C7',
};

export const darkColors = {
  bg: '#121412',
  surface: '#1C1F1B',
  text: '#F0F2ED',
  textMuted: '#A3AAA0',
  border: '#2C312B',
  primary: '#81C784',
  primaryMuted: '#1B3A1D',
  sale: '#EF9A9A',
  badge: '#66BB6A',
  tabInactive: '#6B7268',
  warning: '#F5B94D',
  warningMuted: '#3A2E12',
};

export type ThemeColors = typeof lightColors;
