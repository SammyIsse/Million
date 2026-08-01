/** Produkt-JSON som returneret af listing-API'erne (docs/native-app.md §3). */

export type StoreMatch = {
  name: string;
  price: number | null;
  normal_price: number | null;
  is_sale: boolean;
  image: string;
  brand: string;
  description: string;
  weight: string;
  kg_price: number | null;
  multi_deal: string;
  ean: string;
  Kategori: string;
};

export type Product = {
  id: string;
  name: string;
  brand: string;
  description: string;
  image: string;
  main_image: string;
  rema_image: string;
  category: string;
  subcategory: string;
  store: string;
  price: number;
  normal_price: number;
  is_sale: boolean;
  is_any_sale: boolean;
  sale_end_date: string | null;
  unit_measure: string;
  weight_g: number | null;
  stk_count: number | null;
  kg_price: number | null;
  multi_deal: string;
  is_organic: boolean;
  is_lactose_free: boolean;
  has_match: boolean;
  has_match_rema: boolean;
  cheapest_at: string | null;
  cheaper_at: string | null;
  rema_price: number | null;
  rema_is_sale: boolean;
  lowest_price_30d: number | null;
  store_matches: Record<string, StoreMatch>;
};

export type StoreInfo = {
  key: string;
  label: string;
  logo: string;
};

export type HomeSection = {
  key: string;
  title: string;
  href: string | null;
  products: Product[];
};

export type HomeResponse = {
  success: boolean;
  sections: HomeSection[];
  /** Samme forudberegnede top-10-pulje som web-forsidens "Lækre opskrifter"
   * (home_data_v1-KV, klik-pointsum) - se api/recipes.ts for den fulde type. */
  recipes: import('./recipes').Recipe[];
  /** Stub — reel data hentes client-side via get_personal_savings (JWT). */
  personal_savings: { available: boolean; message: string };
  error?: string;
};

export type ListingResponse = {
  success: boolean;
  products: Product[];
  page: number;
  per_page: number;
  total_pages: number;
  total?: number;
  category?: string;
  slug?: string;
  available_subcategories?: string[];
  current_subcategory?: string | null;
  query?: string;
  error?: string;
};

export type ListingParams = {
  stores?: string[];
  sort?: 'relevance' | 'price-asc' | 'price-desc' | 'kg-price-asc' | 'name-asc';
  min_price?: number;
  max_price?: number;
  sale?: boolean;
  organic?: boolean;
  lactose?: boolean;
  min_weight?: number;
  max_weight?: number;
  page?: number;
  subcategory?: string;
  q?: string;
};
