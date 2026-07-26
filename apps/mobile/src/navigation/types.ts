import type { NavigatorScreenParams } from '@react-navigation/native';

export type TabParamList = {
  Home: undefined;
  Search: undefined;
  Settings: undefined;
};

export type RootStackParamList = {
  Tabs: NavigatorScreenParams<TabParamList> | undefined;
  Category: { slug: string; title: string };
  Sale: undefined;
  Cart: undefined;
  ProductDetail: { product: import('../api/types').Product };
  Sco: undefined;
  Route: undefined;
  Auth: undefined;
  Feedback: undefined;
  Legal: { kind: 'terms' | 'privacy' | 'about' };
};
