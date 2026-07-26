export type RootStackParamList = {
  Tabs: undefined;
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

export type TabParamList = {
  Home: undefined;
  Search: undefined;
  Settings: undefined;
};
