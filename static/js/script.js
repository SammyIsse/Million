// Menu functionality
let priceHistoryChart = null;

// Chart.js lazy-loader: hentes første gang et overlay åbnes (~70 KB).
// Promise genbruges ved efterfølgende kald så biblioteket kun indlæses én gang.
let _chartJsPromise = null;
function loadChartJs() {
    if (window.Chart) return Promise.resolve();
    if (_chartJsPromise) return _chartJsPromise;
    _chartJsPromise = new Promise((resolve, reject) => {
        const s = document.createElement('script');
        s.src = 'https://cdn.jsdelivr.net/npm/chart.js';
        s.onload = resolve;
        s.onerror = reject;
        document.head.appendChild(s);
    });
    return _chartJsPromise;
}

function safeJSONParse(key, fallback) {
    try {
        const val = localStorage.getItem(key);
        return val ? JSON.parse(val) : fallback;
    } catch (e) {
        console.warn('Cleared corrupted localStorage key:', key);
        localStorage.removeItem(key);
        return fallback;
    }
}


const MOBILE_MQ = window.matchMedia('(max-width: 767px)');

function isMobileViewport() {
    return MOBILE_MQ.matches;
}

function updateHeaderHeight() {
    const header = document.querySelector('header');
    if (header) {
        document.documentElement.style.setProperty('--header-height', `${header.offsetHeight}px`);
    }
}

function updateMobileHeaderHeight() {
    updateHeaderHeight();
}

function setMobileFiltersOpen(open) {
    const backdrop = document.getElementById('mobile-filters-backdrop');
    if (!backdrop || !isMobileViewport()) return;
    backdrop.classList.toggle('active', open);
    backdrop.setAttribute('aria-hidden', open ? 'false' : 'true');
    document.body.classList.toggle('filters-open', open);
}

function closeMobileFilters() {
    document.querySelectorAll('.advanced-filters.active').forEach((panel) => {
        panel.classList.remove('active');
    });
    document.querySelectorAll('.advanced-filters-toggle.active').forEach((btn) => {
        btn.classList.remove('active');
    });
    setMobileFiltersOpen(false);
}

function applyOverlayLayout(overlayEl) {
    if (!overlayEl) return;
    if (isMobileViewport()) {
        overlayEl.style.display = 'flex';
        overlayEl.style.alignItems = 'flex-end';
        overlayEl.style.justifyContent = 'center';
    } else {
        overlayEl.style.display = 'flex';
        overlayEl.style.alignItems = 'center';
        overlayEl.style.justifyContent = 'center';
    }
}

function initMobileEnhancements() {
    updateMobileHeaderHeight();

    window.addEventListener('resize', updateMobileHeaderHeight, { passive: true });
    if (window.visualViewport) {
        window.visualViewport.addEventListener('resize', updateMobileHeaderHeight);
    }

    const filterBackdrop = document.getElementById('mobile-filters-backdrop');
    if (filterBackdrop) {
        filterBackdrop.addEventListener('click', closeMobileFilters);
    }

    MOBILE_MQ.addEventListener('change', () => {
        updateMobileHeaderHeight();
        if (!isMobileViewport()) {
            closeMobileFilters();
            document.body.classList.remove('panel-open');
        }
    });
}

function toggleMenu() {
    const menu = document.getElementById('nav-menu');
    const hamburger = document.querySelector('.hamburger-btn');
    const overlay = document.getElementById('menu-overlay');
    const body = document.body;

    menu.classList.toggle('active');
    hamburger.classList.toggle('active');
    overlay.classList.toggle('active');

    // Toggle body scroll
    if (menu.classList.contains('active')) {
        body.style.overflow = 'hidden';
        if (isMobileViewport()) body.classList.add('panel-open');
    } else {
        body.style.overflow = '';
        body.classList.remove('panel-open');
    }
}

// Cart Panel functionality
function toggleCart() {
    const cartPanel = document.getElementById('cart-panel');
    const cartOverlay = document.getElementById('cart-overlay');
    const body = document.body;

    cartPanel.classList.toggle('active');
    cartOverlay.classList.toggle('active');

    // Toggle body scroll
    if (cartPanel.classList.contains('active')) {
        body.style.overflow = 'hidden';
        if (isMobileViewport()) body.classList.add('panel-open');
    } else {
        body.style.overflow = '';
        body.classList.remove('panel-open');
    }
}

// Close menu and cart when clicking outside
document.addEventListener('click', function (event) {
    const menu = document.getElementById('nav-menu');
    const hamburger = document.querySelector('.hamburger-btn');
    const menuOverlay = document.getElementById('menu-overlay');
    const cartPanel = document.getElementById('cart-panel');
    const cartOverlay = document.getElementById('cart-overlay');
    const cartIcon = document.querySelector('.cart-icon');

    // Handle menu clicks
    if (menu.classList.contains('active') &&
        (event.target === menuOverlay || (!menu.contains(event.target) && (!hamburger || !hamburger.contains(event.target))))) {
        toggleMenu();
    }

    // Handle cart clicks
    if (cartPanel.classList.contains('active') &&
        event.target === cartOverlay) {
        toggleCart();
    }
});

// Close menu and cart when pressing Escape key
document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') {
        const menu = document.getElementById('nav-menu');
        const cartPanel = document.getElementById('cart-panel');
        const zoomOverlay = document.getElementById('image-zoom-overlay');

        if (zoomOverlay && zoomOverlay.classList.contains('active')) {
            closeImageZoom();
            return; // Don't close other things if we just closed the zoom
        }

        // Produkt-overlayet havde INGEN Esc-handler: en tastaturbruger kunne
        // kun lukke det ved at klikke udenfor. Lukkes foerst, saa Esc rammer
        // det oeverste lag foerst - som med zoom ovenfor.
        const produktOverlay = document.getElementById('overlay');
        if (produktOverlay && getComputedStyle(produktOverlay).display !== 'none') {
            if (typeof closeOverlay === 'function') closeOverlay();
            return;
        }

        if (menu && menu.classList.contains('active')) {
            toggleMenu();
        }
        if (cartPanel && cartPanel.classList.contains('active')) {
            toggleCart();
        }
    }
});

// Store Filter State
// ALL_STORES is populated dynamically from /api/stores on DOMContentLoaded.
// Each entry: { key: 'bilka', label: 'Bilka', logo: '/static/images/bilka-logo.png' }
let ALL_STORES = [];
let selectedStores = new Set();

/** Checks whether the user has given functional consent via Zaraz */
function harFunktioneltSamtykke() {
    return typeof zaraz !== 'undefined' && zaraz.consent && zaraz.consent.get('icuR') === true;
}

/** Checks whether the user has given analytics consent via Zaraz (purpose NpgO) */
function harAnalyseSamtykke() {
    return typeof zaraz !== 'undefined' && zaraz.consent && zaraz.consent.get('NpgO') === true;
}

/**
 * Fire-and-forget analytics event via Zaraz → GA4.
 * No-op without Analyse-samtykke. Never throws into app flow.
 */
function trackEvent(name, props) {
    if (!harAnalyseSamtykke()) return;
    try {
        if (typeof zaraz !== 'undefined' && typeof zaraz.track === 'function') {
            zaraz.track(name, props || {});
        }
    } catch (_) { /* analytics må aldrig blokere UI */ }
}

/** Reopens the Zaraz consent modal so the user can change cookie preferences at any time */
function openCookiePreferences() {
    const open = () => {
        if (typeof zaraz !== 'undefined' && zaraz.consent) {
            zaraz.consent.modal = true;
            return true;
        }
        return false;
    };
    if (open()) return;
    // Consent-API'en loader async via /cdn-cgi/zaraz/s.js. Hvis brugeren
    // klikker før den er klar, vent på Zaraz' eget ready-event i stedet for
    // at gøre ingenting.
    document.addEventListener('zarazConsentAPIReady', () => { open(); }, { once: true });
}

// Faelles cookie-flag. Secure udelades paa http://localhost, ellers ville
// browseren afvise cookien under lokal udvikling.
const COOKIE_FLAGS = ';path=/;max-age=31536000;SameSite=Lax'
    + (location.protocol === 'https:' ? ';Secure' : '');

function saveStoreFilters() {
    if (!harFunktioneltSamtykke()) return;

    const storesArray = Array.from(selectedStores);
    localStorage.setItem('selectedStores', JSON.stringify(storesArray));
    // SameSite=Lax: cookien maa ikke sendes med ved cross-site-requests, saa en
    // fremmed side ikke kan paavirke hvilke butikker der vises. Secure: kun over
    // HTTPS. Begge er sat via COOKIE_FLAGS, saa de ikke kan glemmes ét sted.
    //
    // Cookien saettes KUN naar valget reelt afviger fra "alle butikker".
    // app.py::_is_cookie_personalised() afgoer udelukkende paa om cookien
    // FINDES (ikke dens indhold) - blev den skrevet ubetinget her (ogsaa
    // naar alle butikker er valgt, som er standarden for stort set alle
    // besoegende), fik enhver med funktionelt samtykke `private, no-store`
    // paa HVER request og ramte edge-cachen aldrig. En aeldre cookie fra
    // dengang brugeren havde et reelt (del-)valg ryddes i stedet, saa
    // vedkommende falder tilbage til den delte cache.
    const allLabels = ALL_STORES.map(s => s.label);
    const isAllSelected = allLabels.length > 0
        && storesArray.length === allLabels.length
        && allLabels.every(l => selectedStores.has(l));
    if (isAllSelected) {
        document.cookie = 'madshopper_stores=; path=/; max-age=0';
    } else {
        document.cookie = "madshopper_stores=" + encodeURIComponent(JSON.stringify(storesArray)) + COOKIE_FLAGS;
    }
    const catalogVersion = window._storeCatalogVersion || parseInt(localStorage.getItem('storeCatalogVersion') || '0', 10);
    if (catalogVersion > 0) {
        document.cookie = "madshopper_store_version=" + catalogVersion + COOKIE_FLAGS;
    }
    updateInternalLinks();
    if (typeof closeAutocomplete === 'function') closeAutocomplete();
}

// Re-persist the current store selection once the user grants functional consent,
// or slet cookies med det samme hvis samtykket bliver trukket tilbage
document.addEventListener('zarazConsentChoicesUpdated', () => {
    if (harFunktioneltSamtykke()) {
        saveStoreFilters();
    } else {
        document.cookie = 'madshopper_stores=; path=/; max-age=0';
        document.cookie = 'madshopper_store_version=; path=/; max-age=0';
        localStorage.removeItem('selectedStores');
        localStorage.removeItem('knownStores');
        localStorage.removeItem('storeCatalogVersion');
    }
});

function readCookieStores() {
    const match = document.cookie.match(/(?:^|; )madshopper_stores=([^;]*)/);
    if (!match) return null;
    try {
        return JSON.parse(decodeURIComponent(match[1]));
    } catch {
        return null;
    }
}

/** 
 * Helper to get active stores as a query string
 */
function getStoresQueryParam() {
    return Array.from(selectedStores).join(',');
}

/**
 * Finds all internal links and appends the 'stores' parameter
 */
function updateInternalLinks() {
    const stores = getStoresQueryParam();
    // Tomt katalog = /api/stores fejlede. Saa er butiksvalget ukendt, og vi
    // maa ikke haenge et (typisk tomt) ?stores= paa hvert eneste link - det
    // ville forplante moerklaegningen til alle sider brugeren klikker videre
    // til. Behandl det som "alle valgt", dvs. ingen parameter.
    const catalogUnavailable = typeof ALL_STORES === 'undefined' || ALL_STORES.length === 0;
    const allSelected = catalogUnavailable || selectedStores.size >= ALL_STORES.length;
    const internalLinks = document.querySelectorAll('.logo-link, .category-nav a, .nav-category-grid a, a[href*=".html"], a[href^="/search"], .product-type h2 a');

    internalLinks.forEach(link => {
        try {
            const url = new URL(link.href, window.location.origin);
            // Only modify links that are on the same domain
            if (url.origin === window.location.origin) {
                if (allSelected) {
                    url.searchParams.delete('stores');
                } else {
                    url.searchParams.set('stores', stores);
                }
                link.href = url.pathname + url.search + url.hash;
            }
        } catch (e) {
            // Skip invalid or non-standard URLs
        }
    });
}

/**
 * Initial sync: If URL is missing 'stores', try to restore from localStorage
 */
function syncUrlWithLocalStorage() {
    const urlParams = new URLSearchParams(window.location.search);
    if (!urlParams.has('stores') && selectedStores.size > 0 && selectedStores.size < ALL_STORES.length && ALL_STORES.length > 0) {
        urlParams.set('stores', getStoresQueryParam());
        // Use replaceState to update URL without adding to history
        const newUrl = window.location.pathname + '?' + urlParams.toString() + window.location.hash;
        window.history.replaceState(null, '', newUrl);
        
        // Store filtering is handled client-side - no server reload needed
    }
}

/** Sync settings-panel checkboxes to match the current selectedStores state */
function syncSettingsCheckboxes() {
    document.querySelectorAll('.store-checkbox input[type="checkbox"]').forEach(cb => {
        cb.checked = selectedStores.has(cb.value);
    });
}

/** Sync frontpage/category store filter button appearance to match selectedStores */
function syncFilterButtons() {
    document.querySelectorAll('.store-filter-btn').forEach(btn => {
        const store = btn.dataset.store;
        if (selectedStores.has(store)) {
            btn.classList.remove('inactive');
        } else {
            btn.classList.add('inactive');
        }
    });
}

function initStoreFilters() {
    const filterButtons = document.querySelectorAll('.store-filter-btn');
    if (filterButtons.length === 0) {
        applyStoreFilters();
        return;
    }

    filterButtons.forEach(btn => {
        // Guard: skip if listener already attached to prevent duplicates
        if (btn.dataset.listenerAttached === 'true') return;
        btn.dataset.listenerAttached = 'true';

        const store = btn.dataset.store;

        btn.addEventListener('click', () => {
            if (selectedStores.has(store)) {
                if (selectedStores.size > 1) { // Prevent unselecting all
                    selectedStores.delete(store);
                }
            } else {
                selectedStores.add(store);
            }

            // Always sync both UIs from the single source of truth
            syncFilterButtons();
            syncSettingsCheckboxes();
            saveStoreFilters();

            // Trigger content update
            updateDynamicStoreContent();

            // If search results are visible, refresh them. refreshSearchResults()
            // og ikke performSearch(): et butiksskift skal genhente de viste
            // resultater med filtrene i behold - ikke starte søgningen forfra
            // (og dermed nulstille dem) på det ord der tilfældigvis står i feltet.
            const searchResults = document.getElementById('searchResults');
            if (searchResults && searchResults.classList.contains('visible')) {
                refreshSearchResults(true);
            }

            // Update cart summary if open
            if (typeof updateCartDisplay === 'function') {
                updateCartDisplay();
            }
        });
    });

    // Apply initial visual state from selectedStores
    syncFilterButtons();
    syncSettingsCheckboxes();
    applyStoreFilters();
    updateInternalLinks();
    syncUrlWithLocalStorage();
}

/**
 * Fetches updated content from the server based on selected stores
 * and replaces the dynamic-content container.
 *
 * resetPage: true når brugeren aktivt har ændret butiksvalget (resultaterne
 * er reelt anderledes, så side 1 er det rigtige udgangspunkt). false når vi
 * blot genindlæser for at synkronisere med et allerede-gemt butiksvalg ved
 * sideindlæsning (fx initAllStores) - her skal en direkte navigation til
 * ?page=2 ikke blive tromlet tilbage til side 1.
 */
function updateDynamicStoreContent(resetPage = true) {
    const dynamicContainer = document.getElementById('dynamic-content');
    if (!dynamicContainer) return;

    dynamicContainer.style.opacity = '0.5';
    dynamicContainer.style.pointerEvents = 'none';

    const storesParam = Array.from(selectedStores).join(',');

    // Update the browser URL first so any subsequent filter calls use the correct stores
    const urlObj = new URL(window.location.href);
    urlObj.searchParams.set('stores', storesParam);
    if (resetPage) urlObj.searchParams.delete('page'); // reset to page 1 when store selection changes
    window.history.pushState({}, '', urlObj.pathname + urlObj.search);

    fetch(urlObj, {
        headers: {
            'X-Requested-With': 'XMLHttpRequest'
        }
    })
        .then(response => {
            if (!response.ok) throw new Error('Network response was not ok');
            return response.text();
        })
        .then(html => {
            const parser = new DOMParser();
            const doc = parser.parseFromString(html, 'text/html');

            const newContent = doc.getElementById('dynamic-content');
            dynamicContainer.innerHTML = newContent ? newContent.innerHTML : html;

            updateInternalLinks();

            if (typeof attachProductEventListeners === 'function') {
                attachProductEventListeners();
            }

            if (typeof applyStoreFilters === 'function') {
                applyStoreFilters();
            }

            dynamicContainer.style.opacity = '1';
            dynamicContainer.style.pointerEvents = 'auto';
        })
        .catch(error => {
            console.error('Error updating content:', error);
            dynamicContainer.style.opacity = '1';
            dynamicContainer.style.pointerEvents = 'auto';
        });
}

function applyStoreFilters() {
    // Butikskataloget kunne ikke hentes (/api/stores fejlede). Uden det kan vi
    // ikke afgoere hvilke kort der hoerer til de valgte butikker, og den gamle
    // adfaerd endte med at skjule SAMTLIGE produkter: en enkelt netvaerkshikke
    // moerklagde hele siden uden fejlbesked. At vise for meget er langt bedre
    // end at vise ingenting.
    if (!ALL_STORES.length) {
        document.querySelectorAll('.product').forEach(p => p.classList.remove('store-hidden'));
        return;
    }

    const products = document.querySelectorAll('.product');
    products.forEach(p => {
        let store = p.dataset.store || 'Rema 1000';
        if (store === 'Min Koebmand') store = 'Min Købmand';

        // Show if primary store is selected
        let visible = selectedStores.has(store);

        // Also show if the product has a price for any selected comparison store
        if (!visible) {
            visible = ALL_STORES.some(({ key, label }) =>
                selectedStores.has(label) &&
                p.dataset[key + 'Price'] !== undefined &&
                p.dataset[key + 'Price'] !== ''
            );
        }

        // Rema is not in ALL_STORES (it's the primary feed, not an Excel store).
        // Always show products that have a valid Rema price when Rema 1000 is selected.
        if (!visible && selectedStores.has('Rema 1000')) {
            const remaPrice = parseFloat(p.dataset.remaPrice || '0');
            if (remaPrice > 0) visible = true;
        }

        p.classList.toggle('store-hidden', !visible);
    });
    updateStoreBadges();
}

function updateStoreBadges() {
    const storeToKey = {};
    ALL_STORES.forEach(({ key, label }) => storeToKey[label] = key);

    document.querySelectorAll('.product').forEach(p => {
        const badge = p.querySelector('.store-badge');
        if (!badge) return;

        const priceContainer = p.querySelector('.product-price');
        const saleBadge      = p.querySelector('.sale-badge');

        // Restore any display state overridden in a previous call
        if (p.dataset.originalPriceHtml !== undefined && priceContainer) {
            priceContainer.innerHTML = p.dataset.originalPriceHtml;
            delete p.dataset.originalPriceHtml;
        }
        if (p.dataset.originalSaleBadgeDisplay !== undefined && saleBadge) {
            saleBadge.style.display = p.dataset.originalSaleBadgeDisplay;
            delete p.dataset.originalSaleBadgeDisplay;
        }

        let primaryStore = p.dataset.store || 'Rema 1000';
        if (primaryStore === 'Min Koebmand') primaryStore = 'Min Købmand';

        let displayLabel = primaryStore;
        let displayKey   = storeToKey[primaryStore] || 'rema';

        // If visible only because of a comparison store match, show that store's badge
        if (!p.classList.contains('store-hidden') && !selectedStores.has(primaryStore)) {
            // Check Rema explicitly first (not in ALL_STORES)
            if (selectedStores.has('Rema 1000') && parseFloat(p.dataset.remaPrice || '0') > 0) {
                displayLabel = 'Rema 1000';
                displayKey   = 'rema';

                // Swap the displayed price to Rema's price
                if (priceContainer) {
                    p.dataset.originalPriceHtml = priceContainer.innerHTML;
                    const remaPrice  = parseFloat(p.dataset.remaPrice).toFixed(2);
                    const remaIsSale = p.dataset.remaIsSale === 'true';
                    priceContainer.innerHTML = remaIsSale
                        ? `<div class="price-sale price sale">${remaPrice} kr</div>`
                        : `<div class="price-main price">${remaPrice} kr</div>`;
                }

                // Hide the sale badge if Rema doesn't have a sale on this product
                if (saleBadge) {
                    p.dataset.originalSaleBadgeDisplay = saleBadge.style.display;
                    saleBadge.style.display = p.dataset.remaIsSale === 'true' ? '' : 'none';
                }
            } else {
                const match = ALL_STORES.find(({ key, label }) =>
                    selectedStores.has(label) &&
                    p.dataset[key + 'Price'] !== undefined &&
                    p.dataset[key + 'Price'] !== ''
                );
                if (match) {
                    displayLabel = match.label;
                    displayKey   = match.key;
                }
            }
        }

        badge.className   = `store-badge ${displayKey}`;
        badge.textContent = displayLabel;
    });
}

// Cart functionality with localStorage
let cart = safeJSONParse('cart', []);
// (scoByStoreOpen fjernet sammen med toggleScoByStore/renderScoByStore - de
//  refererede elementer som sco-items-1 og sco-winner-name, der ikke findes i
//  base.html laengere, og blev aldrig kaldt fra nogen onclick.)

// Bro til auth.js/kurv-synk. auth.js sætter _onChange, når en bruger er logget
// ind, og bruger get()/applyFromServer() ved login og kontosletning. Er ingen
// logget ind, er _onChange null → ingen synk, kurven bor kun i localStorage.
// applyFromServer sætter kurven UDEN at kalde notify(), så vi ikke laver en
// synk-løkke, når vi netop har hentet data fra serveren.
window.CartBridge = {
    _onChange: null,
    get: function () { return cart; },
    applyFromServer: function (items) {
        cart = Array.isArray(items) ? items : [];
        try { localStorage.setItem('cart', JSON.stringify(cart)); } catch (e) { /* ignorér */ }
        updateCartDisplay();
        updateCartCount();
    },
    notify: function () { if (typeof this._onChange === 'function') this._onChange(cart); }
};

function parseDKKPrice(text) {
    const s = String(text)
        .replace(/\s/g, '')
        .replace(/DKK/gi, '')
        .replace(',', '.')
        .trim();
    const n = parseFloat(s);
    return Number.isNaN(n) ? NaN : n;
}

/** Rema-shelfpris + matchet Bilka/MK pris fra produktkort. */
function parsePricesFromProductCard(productElement) {
    const salePriceElement = productElement.querySelector('.price.sale');
    const regularPriceElement = productElement.querySelector('.price:not(.sale):not(.original)');

    let mainPrice = null;
    if (salePriceElement) {
        mainPrice = parseDKKPrice(salePriceElement.innerText);
    } else if (regularPriceElement) {
        mainPrice = parseDKKPrice(regularPriceElement.innerText);
    }

    if (Number.isNaN(mainPrice)) {
        return null;
    }

    const cardStore = productElement.dataset.store || 'Rema 1000';
    const storePrices = {};

    // Assign the card's visible price to the store shown on the card
    storePrices[cardStore] = mainPrice;

    // Read per-store prices from data attributes generated by the template loop
    ALL_STORES.forEach(({ key, label }) => {
        const raw = productElement.dataset[`${key}Price`];
        if (raw !== undefined && raw !== '') {
            const p = parseFloat(String(raw).replace(',', '.'));
            if (!Number.isNaN(p)) storePrices[label] = p;
        }
    });

    // Legacy rema-price attribute
    const remaRaw = productElement.dataset.remaPrice;
    if (remaRaw !== undefined && remaRaw !== '') {
        const p = parseFloat(String(remaRaw).replace(',', '.'));
        if (!Number.isNaN(p)) storePrices['Rema 1000'] = p;
    }

    return { storePrices, mainPrice };
}

function parseMultiDeal(dealStr) {
    if (!dealStr) return null;
    const m = dealStr.match(/(\d+)\s+for\s+([\d.,]+)/i);
    if (!m) return null;
    const qty = parseInt(m[1]);
    const totalPrice = parseFloat(m[2].replace(',', '.'));
    return (qty > 1 && !isNaN(totalPrice) && totalPrice > 0) ? { qty, totalPrice } : null;
}

function applyDealPrice(regularPrice, quantity, dealStr) {
    const deal = parseMultiDeal(dealStr);
    if (!deal) return regularPrice * quantity;
    const bundles = Math.floor(quantity / deal.qty);
    return bundles * deal.totalPrice + (quantity % deal.qty) * regularPrice;
}

function collectStoreMultiDeals(productElement) {
    const deals = {};
    ALL_STORES.forEach(({ key, label }) => {
        const raw = productElement.dataset[`${key}Multideal`];
        if (raw && raw.trim()) deals[label] = raw.trim();
    });
    const main = productElement.dataset.multideal;
    const store = productElement.dataset.store || 'Rema 1000';
    if (main && main.trim()) deals[store] = main.trim();
    return deals;
}

function saveCart() {
    // Uden try/catch stoppede en fuld/blokeret localStorage-kvote
    // (QuotaExceededError, Safari privat browsing) hele funktionen her - og
    // dermed ALT der kaldte den, fx addToCart(), hvor koden EFTER
    // saveCart()-kaldet (API-kald, sporing, nulstilling af knappens ikon)
    // aldrig blev nået. Knappen stod derefter permanent på "Tilføjet".
    try {
        localStorage.setItem('cart', JSON.stringify(cart));
    } catch (e) {
        console.error('Kunne ikke gemme kurven lokalt:', e);
    }
    updateCartDisplay();
    updateCartCount();
    // Synk til Supabase, hvis brugeren er logget ind (ellers no-op).
    if (window.CartBridge) window.CartBridge.notify();
}

// Kurv-synk MELLEM FANER. Uden dette blev fane B's kurv i hukommelsen
// foraeldet, saa snart fane A aendrede noget - og naeste saveCart() i fane B
// skrev sin gamle version hen over A's aendring. For udlogget var der ingen
// redning overhovedet; for indloggede blev det foerst "repareret" ved naeste
// sideindlaesnings merge, som pga. union-fletningen kunne genoplive netop
// slettede varer.
//
// storage-eventen fyrer kun i ANDRE faner end den der skrev, saa der er ingen
// risiko for at vi overskriver vores egen igangvaerende aendring.
window.addEventListener('storage', function (e) {
    if (e.key !== 'cart') return;
    try {
        const incoming = e.newValue ? JSON.parse(e.newValue) : [];
        if (!Array.isArray(incoming)) return;
        cart = incoming;
        updateCartDisplay();
        updateCartCount();
    } catch (err) {
        console.warn('Kunne ikke laese kurv fra anden fane:', err);
    }
});

// Samler hurtige, gentagne 'add'-hændelser for SAMME produkt til ét POST i
// stedet for ét pr. klik - uden dette kunne ivrige klik (fx den animerede
// "+"-knap) selv udløse cart_event_limiter'en, selvom selve kurvens antal
// altid talte korrekt (fundet under QA-audit 2026-08-17). Kurven selv
// (cart.push/existingItem.quantity) opdateres stadig synkront ved hvert
// klik - kun denne rent statistiske populæritets-registrering udsættes.
const _cartEventQueue = {};
const CART_EVENT_DEBOUNCE_MS = 600;

function queueCartEvent(eventType, id, qty) {
    const key = eventType + ':' + id;
    const entry = _cartEventQueue[key] || { qty: 0, timer: null };
    entry.qty += qty;
    clearTimeout(entry.timer);
    entry.timer = setTimeout(() => {
        delete _cartEventQueue[key];
        fetch('/api/cart-event', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ event: eventType, items: [{ id: id, qty: entry.qty }] })
        }).catch(() => {});
    }, CART_EVENT_DEBOUNCE_MS);
    _cartEventQueue[key] = entry;
}

function addToCart(event, productElementOrId) {
    // Prevent event bubbling
    event.stopPropagation();

    let productElement;
    if (typeof productElementOrId === 'string') {
        productElement = document.getElementById(productElementOrId);
    } else {
        productElement = productElementOrId;
    }

    if (!productElement) {
        console.error('Product not found:', productElementOrId);
        return;
    }

    const productId = productElement.id;

    // Get the button that was clicked
    const addToCartBtn = event.target;

    // Get product details
    const name = productElement.querySelector('h3').innerText;
    const parsed = parsePricesFromProductCard(productElement);
    if (!parsed) {
        console.error('Price element not found');
        return;
    }
    const { storePrices, mainPrice } = parsed;
    const image = productElement.querySelector('.product-image').src;
    const category = productElement.dataset.category || 'Andre varer';
    const unitMeasure = productElement.dataset.remaWeight || '';
    const kgPrice = productElement.dataset.remaKgPrice || '';
    const store = productElement.dataset.store || 'Rema 1000';
    const multiDeal = productElement.dataset.multideal || '';
    const storeMultiDeals = collectStoreMultiDeals(productElement);

    // Check if product already exists in cart
    const existingItem = cart.find(item => item.id === productId);

    if (existingItem) {
        existingItem.quantity += 1;
    } else {
        cart.push({
            id: productId,
            name: name,
            store: store,
            price: mainPrice,
            storePrices: storePrices,
            storeMultiDeals: storeMultiDeals,
            image: image,
            category: category,
            unitMeasure: unitMeasure,
            kgPrice: kgPrice,
            multiDeal: multiDeal,
            quantity: 1
        });
    }

    // Find the actual button
    const btn = event.target.closest('.add-to-cart-btn') || event.target.closest('.corner-box') || event.target;

    // Prevent double-click from overwriting the saved SVG with the "Tilføjet" text
    if (!btn.dataset.originalHtml) {
        btn.dataset.originalHtml = btn.innerHTML;
    }

    // Show animations and change text
    btn.classList.add('clicked');

    // Change to text, use a small span to ensure it centers nicely
    btn.innerHTML = '<span style="font-size: 0.8rem; font-weight: bold;">Tilføjet</span>';

    // Save cart
    saveCart();

    // Record popularity (fire-and-forget, debounced). qty er altid 1 - hvert
    // klik lægger præcis én vare i kurven, uanset hvor mange der allerede
    // ligger der.
    queueCartEvent('add', productId.replace(/^product/, ''), 1);

    trackEvent('add_to_cart', {
        product_id: productId.replace(/^product/, ''),
        category: category,
        store: store,
        quantity: 1
    });

    // Reset animations and text after delay
    setTimeout(() => {
        btn.classList.remove('clicked');
        // Restore original HTML if available
        if (btn.dataset.originalHtml) {
            btn.innerHTML = btn.dataset.originalHtml;
            delete btn.dataset.originalHtml;
        }
    }, 1000);

}

// "Læg alle fundne varer i kurv" på opskrift-siden (templates/opskrift.html).
// Bygger kurv-varer direkte fra server-leveret JSON (app.py::_fetch_recipe_detail)
// i stedet for at scrape DOM'en som addToCart() gør - opskrift-siden har ingen
// .product-kort med data-* attributter at læse fra. Samme kurv-vare-form som
// addToCart, samme localStorage-nøgle ('cart'), samme /api/cart-event-mønster,
// bare ét batched kald for alle varer i stedet for ét pr. vare.
function addRecipeToCart(items, btn) {
    if (!Array.isArray(items) || items.length === 0) return;

    let addedCount = 0;
    items.forEach((item) => {
        if (!item || !item.id) return;
        // item.quantity = antal pakker af DENNE vare (fx 3, hvis opskriften
        // ved den nuværende personer-skalering kræver 3 pakker af den
        // billigste kandidat) - default 1 for almindelige kurv-varer.
        const qty = Math.max(1, item.quantity || 1);
        const productId = 'product' + item.id;
        const existingItem = cart.find((c) => c.id === productId);
        if (existingItem) {
            existingItem.quantity += qty;
        } else {
            cart.push({
                id: productId,
                name: item.name || '',
                store: item.store || 'Rema 1000',
                price: item.price,
                storePrices: item.store_prices || {},
                storeMultiDeals: {},
                image: item.image || '',
                category: item.category || 'Andre varer',
                unitMeasure: item.unit_measure || '',
                kgPrice: item.kg_price || '',
                multiDeal: item.multi_deal || '',
                quantity: qty,
            });
        }
        addedCount += 1;
    });

    if (addedCount === 0) return;
    saveCart();

    fetch('/api/cart-event', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            event: 'add',
            items: items.filter((i) => i && i.id).map((i) => ({ id: i.id, qty: Math.max(1, i.quantity || 1) })),
        }),
    }).catch(() => {});

    trackEvent('add_recipe_to_cart', { item_count: addedCount });

    if (btn) {
        if (!btn.dataset.originalHtml) btn.dataset.originalHtml = btn.innerHTML;
        btn.innerHTML = `${addedCount} varer tilføjet til kurv`;
        btn.disabled = true;
        setTimeout(() => {
            if (btn.dataset.originalHtml) {
                btn.innerHTML = btn.dataset.originalHtml;
                delete btn.dataset.originalHtml;
            }
            btn.disabled = false;
        }, 2000);
    }
}

function toggleAlertForm(event) {
    if (event) {
        event.preventDefault();
        event.stopPropagation();
    }
    // Kræver login - vis kort besked før AuthBridge åbner login-modalen.
    const isLoggedIn = window.AuthBridge && window.AuthBridge.getUser && window.AuthBridge.getUser();
    if (!isLoggedIn) {
        alert('Log ind for at bruge prisovervågning.');
    }
    if (!window.AuthBridge || !window.AuthBridge.requireAuth()) return;
    const form = document.getElementById('alert-form');
    if (form) form.style.display = form.style.display === 'none' ? 'block' : 'none';
}

function initPriceAlertButton() {
    const btn = document.getElementById('price-alert-btn');
    if (!btn || btn.dataset.bound === '1') return;
    btn.dataset.bound = '1';
    btn.addEventListener('click', toggleAlertForm);
}

async function savePriceAlert() {
    if (!window.AuthBridge || !window.AuthBridge.requireAuth()) return;

    const input = document.getElementById('target-price-input');
    const targetPrice = parseFloat(input ? input.value : '');
    if (!targetPrice || targetPrice <= 0) {
        alert('Indtast venligst en gyldig målpris.');
        return;
    }

    const piEl = document.querySelector('.product-info');
    const productId = piEl ? piEl.dataset.productId : '';
    const currentPrice = parseFloat(piEl ? piEl.dataset.cheapestPrice : '') || 0;
    const productName = document.getElementById('overlay-title')?.innerText || '';
    if (!productId || !currentPrice) return;

    const client = window.AuthBridge.getClient();
    if (!client) return;

    try {
        const { data, error } = await client.rpc(window.AuthBridge.rpcName('create_price_alert'), {
            pid: productId, pname: productName, target: targetPrice, current: currentPrice
        });
        if (error || data === false) {
            alert('Kunne ikke oprette prisalarm. Prøv igen.');
            return;
        }
        const btn = document.querySelector('.alert-toggle-btn');
        if (btn) { btn.innerHTML = '✅ Alarm sat - du får en mail'; btn.disabled = true; }
        if (input) input.value = '';
        const form = document.getElementById('alert-form');
        if (form) form.style.display = 'none';
    } catch (e) {
        console.error('Alert error:', e);
        alert('Kunne ikke oprette prisalarm. Prøv igen.');
    }
}

function removeFromCart(productId) {
    cart = cart.filter(item => item.id !== productId);
    saveCart();
}

// (En tidligere clearCart laa her. Den skrev direkte til localStorage uden at
//  kalde saveCart(), saa serveren aldrig fik besked om at kurven var toemt.
//  Definitionen laengere nede vandt i praksis - nu er der kun den ene.)

function updateCartCount() {
    const cartBadge = document.getElementById('cart-badge');
    if (!cartBadge) return;

    // FORCED: Read directly from localStorage
    const actualCart = safeJSONParse('cart', []);
    let totalItems = 0;
    actualCart.forEach(item => {
        const q = parseInt(item.quantity);
        if (!isNaN(q)) {
            totalItems += q;
        }
    });

    cartBadge.textContent = totalItems;
    if (totalItems > 0) {
        cartBadge.style.display = 'flex';
    } else {
        cartBadge.style.display = 'none';
    }
}

function updateCartDisplay() {
    const cartItems = document.querySelector('.cart-items');
    const cartTotalPrice = document.getElementById('cart-total-price');
    cartItems.innerHTML = '';

    let total = 0;
    const isValidPrice = (p) => p != null && !isNaN(p) && Number(p) > 0;

    // Group items by category
    const groupedCart = {};
    cart.forEach((item, index) => {
        const cat = item.category || 'Andre varer';
        if (!groupedCart[cat]) groupedCart[cat] = [];
        groupedCart[cat].push({ ...item, originalIndex: index });
    });

    for (const [category, items] of Object.entries(groupedCart)) {
        const catHeader = document.createElement('h3');
        catHeader.className = 'cart-category-header';
        catHeader.textContent = category;
        cartItems.appendChild(catHeader);

        items.forEach(item => {
            const index = item.originalIndex;
            const cartItem = document.createElement('div');
            cartItem.className = 'cart-item';
            cartItem.dataset.index = index;

            // item.price er varens EGEN pris og skal vises deterministisk -
            // faldt vi (som før) tilbage til "første gyldige pris i
            // storePrices", var rækkefølgen ikke garanteret varens egen butik
            // (fx for opskrift-tilføjede varer, hvor storePrices' rækkefølge
            // kommer fra serveren). Kun hvis item.price selv er ugyldig,
            // bruges første gyldige butikspris som nødløsning - app-paritet
            // (CartScreen.tsx bruger altid item.price direkte).
            const allPrices = item.storePrices
                ? Object.values(item.storePrices)
                : [item.remaPrice, item.bilkaPrice, item.mkPrice, item.menyPrice, item.sparPrice];
            let unit = isValidPrice(item.price) ? item.price : (allPrices.find(p => isValidPrice(p)) ?? 0);
            if (!isValidPrice(unit)) unit = 0;
            total += unit * item.quantity;

            let extraInfo = '';
            const infoArr = [];
            if (item.unitMeasure) infoArr.push(escapeHtml(item.unitMeasure));
            if (item.kgPrice) infoArr.push(`${escapeHtml(item.kgPrice)} kr/kg`);
            if (infoArr.length > 0) extraInfo = `<div class="cart-item-extra">${infoArr.join(' | ')}</div>`;

            const multiDealHtml = item.multiDeal ? `<div class="cart-item-multideal">${escapeHtml(item.multiDeal)}</div>` : '';

            cartItem.innerHTML = `
                <button class="delete-item-btn" onclick="deleteCartItem(${index})">&times;</button>
                <div class="cart-item-top">
                    <div class="cart-item-image">
                        <img src="${escapeHtml(item.image || '')}" alt="${escapeHtml(item.name)}">
                    </div>
                    <div class="cart-item-details">
                        <h4 class="cart-item-title">${escapeHtml(stripStoreBrand(item.name))}</h4>
                        ${extraInfo}
                        ${multiDealHtml}
                        <div class="cart-item-price">${unit.toFixed(2)} kr</div>
                        <div class="cart-item-quantity">
                            <button class="quantity-btn" onclick="updateQuantity(${index}, -1)">-</button>
                            <span class="quantity">${item.quantity}</span>
                            <button class="quantity-btn" onclick="updateQuantity(${index}, 1)">+</button>
                        </div>
                    </div>
                </div>
            `;
            cartItems.appendChild(cartItem);
        });
    }

    // Update total price display with 2 decimal places
    if (cartTotalPrice) cartTotalPrice.textContent = `${total.toFixed(2)} kr`;

    // Show/hide cart footer and clear button
    const footerSection = document.getElementById('cart-footer-section');
    const storeGrid = document.getElementById('cart-store-grid'); // may be null if removed
    const clearBtn = document.getElementById('clear-cart-btn');

    if (footerSection) {
        if (cart.length === 0) {
            footerSection.style.display = 'none';
            if (clearBtn) clearBtn.style.display = 'none';
            // Show empty state
            if (!cartItems.querySelector('.cart-empty')) {
                const emptyDiv = document.createElement('div');
                emptyDiv.className = 'cart-empty';
                emptyDiv.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M6 2L3 6v14a2 2 0 002 2h14a2 2 0 002-2V6l-3-4z"/><line x1="3" y1="6" x2="21" y2="6"/><path d="M16 10a4 4 0 01-8 0"/></svg><p>Din kurv er tom</p><button class="cart-empty-btn" onclick="toggleCart()">Start indkøb</button>`;
                cartItems.appendChild(emptyDiv);
            }
        } else {
            footerSection.style.display = 'flex';
            if (clearBtn) clearBtn.style.display = 'flex';
            // Build store summary dynamically
            const storeTotals = {};
            cart.forEach(item => {
                // New format: item.storePrices = { 'Rema 1000': price, ... }
                // Legacy format: item.remaPrice / item.bilkaPrice / etc.
                let prices = item.storePrices;
                if (!prices) {
                    prices = {};
                    const legacyMap = {
                        'Rema 1000': item.remaPrice, 'Bilka': item.bilkaPrice,
                        'Min Købmand': item.mkPrice,  'Meny': item.menyPrice, 'Spar': item.sparPrice
                    };
                    for (const [label, p] of Object.entries(legacyMap)) {
                        if (p != null) prices[label] = p;
                    }
                    if (Object.keys(prices).length === 0) prices[item.store || 'Rema 1000'] = item.price;
                }
                for (const [label, p] of Object.entries(prices)) {
                    if (p != null && !isNaN(p)) {
                        storeTotals[label] = (storeTotals[label] || 0) + Number(p) * item.quantity;
                    }
                }
            });
            const sorted = Object.entries(storeTotals)
                .filter(([name]) => selectedStores.has(name))
                .sort((a, b) => a[1] - b[1]);

            if (storeGrid) {
                storeGrid.innerHTML = sorted.map(([name, price], i) =>
                    `<div class="cart-store-box${i === 0 ? ' winner' : ''}">
                        <div class="cart-store-name">${escapeHtml(name)}</div>
                        <div class="cart-store-total">${price.toFixed(2)} kr</div>
                    </div>`
                ).join('');
            }

            const savingsEl = document.getElementById('cart-best-savings-text');
            if (savingsEl && sorted.length >= 1) {
                if (sorted.length >= 2) {
                    const saved = sorted[sorted.length - 1][1] - sorted[0][1];
                    savingsEl.textContent = saved > 0.01
                        ? `Spar op til ${saved.toFixed(2)} kr - klik for at sammenligne`
                        : `Se priser på tværs af butikker`;
                } else {
                    savingsEl.textContent = `Laveste pris: ${sorted[0][1].toFixed(2)} kr`;
                }
            }
        }
    }

    // Update cart count
    updateCartCount();
}

function updateQuantity(index, change) {
    const item = cart[index];
    if (!item) return;
    const newQuantity = item.quantity + change;
    const cartItem = document.querySelector(`.cart-item[data-index="${index}"]`);

    if (newQuantity <= 0) {
        // Add fade-out animation
        if (cartItem) cartItem.classList.add('removing');

        // Samme indeks-faelde som i deleteCartItem: to hurtige klik paa "−"
        // ved antal 1 ser begge quantity 1 -> 0 og planlaegger hver sin
        // fjernelse. Opslaget paa varen selv goer den anden til en no-op i
        // stedet for at slette naboen.
        setTimeout(() => {
            const current = cart.indexOf(item);
            if (current !== -1) {
                cart.splice(current, 1);
                saveCart();
            }
            updateCartDisplay();
        }, 300); // Match this with CSS animation duration
        return;
    }

    item.quantity = newQuantity;
    saveCart();
    updateCartDisplay();
}

// Global state for store comparison popup
let _scoCompData = null;

// Produkter der allerede er talt med som "sammenlignet" i denne fane. Holdes i
// hukommelsen - IKKE i localStorage/sessionStorage - så vi hverken lagrer noget
// på brugerens udstyr (ingen samtykkekrav) eller kan genkende nogen på tværs af
// besøg. Formålet er kun at gentagne klik på samme kurv ikke puster tallene op.
const _comparedProductIds = new Set();

function recordCompareEvent(cartProducts) {
    try {
        const fresh = [];
        cartProducts.forEach(item => {
            const pid = String(item.id || '').replace(/^product/, '');
            if (pid && !_comparedProductIds.has(pid)) {
                _comparedProductIds.add(pid);
                fresh.push({ id: pid, qty: Number(item.quantity) || 1 });
            }
        });
        if (fresh.length === 0) return;

        fetch('/api/cart-event', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ event: 'compare', items: fresh })
        }).catch(() => {});
    } catch (e) {
        // Statistik må aldrig kunne blokere selve prissammenligningen
    }
}

/** Billigste/dyreste blandt butikker med fuld kurv-dækning. Null hvis <2. */
function fullCoveragePriceRange(stores) {
    if (!stores || !stores.length) return null;
    const full = stores.filter(s => s.totalItems > 0 && s.coverage === s.totalItems);
    if (full.length < 2) return null;
    let cheap = full[0].totalPrice;
    let expensive = full[0].totalPrice;
    for (let i = 1; i < full.length; i++) {
        const p = full[i].totalPrice;
        if (p < cheap) cheap = p;
        if (p > expensive) expensive = p;
    }
    if (!(expensive > cheap)) return null;
    return { cheap: Number(cheap), expensive: Number(expensive) };
}

// Sidste kurv-sammensaetning vi har optjent besparelse for. Holdes i
// hukommelsen som _comparedProductIds - ikke i localStorage, saa vi hverken
// lagrer noget paa brugerens udstyr eller kan genkende nogen paa tvaers af
// besoeg.
let _savingsSignature = null;

function recordPersonalSavings(stores) {
    try {
        if (!_authUser()) return;
        const range = fullCoveragePriceRange(stores);
        if (!range) return;
        // Kun ÉN gang pr. kurv. Funktionen kaldes ved HVER aabning af
        // sammenligningen og igen hver gang man accepterer et alternativ, saa
        // uden denne spaerre blev samme uaendrede kurv talt med hver gang -
        // maanedens besparelse voksede ved at man kiggede paa den. Serverens
        // loft (50 events/dag) begraensede skaden, men fjernede den ikke.
        // Cart-events har allerede samme slags dedupe (_comparedProductIds).
        const signature = (cart || [])
            .map(function (i) { return i.id + ':' + (i.quantity || 1); })
            .sort()
            .join('|') + '#' + range.cheap.toFixed(2) + '/' + range.expensive.toFixed(2);
        if (_savingsSignature === signature) return;
        _savingsSignature = signature;
        const sb = _sbClient();
        if (!sb) return;
        sb.rpc(_rpc('record_compare_savings'), {
            p_cheap: range.cheap,
            p_expensive: range.expensive
        }).then(function (res) {
            if (res && res.data) renderPersonalSavingsWidget(res.data);
        }).catch(function () { /* aldrig blokér sammenligning */ });
    } catch (e) { /* stille */ }
}

function showReference() {
    // .show-reference-btn er den skjulte legacy-knap; #cart-best-deal-btn er
    // den faktiske knap i kurv-panelet brugeren klikker - begge skal vise
    // loading-state, ellers ser det ud som om intet sker på langsomt netværk.
    const buttons = document.querySelectorAll('.show-reference-btn, #cart-best-deal-btn');
    const button = buttons[0];

    if (Array.from(buttons).some(btn => btn.classList.contains('loading'))) {
        return;
    }

    const cartProducts = safeJSONParse('cart', []);
    if (cartProducts.length === 0) {
        alert('Kurven er tom - tilføj varer før du sammenligner priser.');
        return;
    }

    buttons.forEach(btn => btn.classList.add('loading'));

    // Et klik her er et stærkere købssignal end en ren kurv-tilføjelse, så
    // varerne tæller også med i Populære varer (fire-and-forget)
    recordCompareEvent(cartProducts);

    trackEvent('compare_prices', {
        item_count: cartProducts.length,
        total_qty: cartProducts.reduce((sum, item) => sum + (Number(item.quantity) || 1), 0)
    });

    const overlay = document.getElementById('store-comparison-overlay');

    calculateStoreComparisons()
        .then(({ stores, matchedItemsPerStore }) => {
            // Sort: flest matchede varer først, ved uafgjort: billigst først
            const sorted = stores.slice().sort((a, b) => {
                if (b.coverage !== a.coverage) return b.coverage - a.coverage;
                return a.totalPrice - b.totalPrice;
            });

            if (sorted.length === 0) {
                overlay.style.display = 'flex';
                document.body.style.overflow = 'hidden';
                return;
            }

            _scoCompData = { sorted, matchedItemsPerStore, altByStore: {} };

            renderScoStoreRow(sorted);
            selectScoStore(sorted[0].name);

            overlay.style.display = 'flex';
            document.body.style.overflow = 'hidden';

            // Personlig besparelse: dyreste − billigste (fuld dækning), kræver login
            recordPersonalSavings(sorted);
        })
        .catch(error => {
            console.error('Error calculating store comparisons:', error);
        })
        .finally(() => {
            buttons.forEach(btn => btn.classList.remove('loading'));
        });
}

function renderScoStoreRow(sortedStores) {
    const row = document.getElementById('sco-store-row');
    if (!row) return;

    row.innerHTML = sortedStores.slice(0, 5).map((store, i) => {
        const storeEntry = ALL_STORES.find(s => s.label === store.name);
        const logo = storeEntry ? storeEntry.logo : '';
        const imgHtml = logo
            ? `<img class="sco-sc-logo" src="${escapeHtml(logo)}" alt="${escapeHtml(store.name)}" onerror="this.style.display='none'">`
            : `<span class="sco-sc-name-fallback">${escapeHtml(store.name)}</span>`;
        const isFirst = i === 0;
        return `
            <button class="sco-store-card${isFirst ? ' active' : ''}" data-store="${escapeHtml(store.name)}" onclick="selectScoStore('${escapeHtml(store.name).replace(/'/g, "\\'")}')">
                <span class="sco-sc-count">${store.coverage}/${store.totalItems}</span>
                ${imgHtml}
                <span class="sco-sc-price">${store.totalPrice.toFixed(2)} kr</span>
            </button>
        `;
    }).join('');
}

function selectScoStore(storeName) {
    document.querySelectorAll('.sco-store-card').forEach(card => {
        card.classList.toggle('active', card.dataset.store === storeName);
    });

    if (!_scoCompData) return;

    const compData = _scoCompData;
    const { sorted, matchedItemsPerStore, altByStore } = compData;
    const storeData = sorted.find(s => s.name === storeName);
    if (!storeData) return;

    const matched = matchedItemsPerStore[storeName] || [];
    const missing = storeData.missingDetails || [];

    renderScoItemList(storeName, matched, missing, altByStore[storeName] || [], storeData.totalPrice);

    // Erstatningsvarer hentes for den butik man kigger på - de er butiks-
    // specifikke, så ét fælles opslag for hele kurven kunne kun besvare den
    // første butik og efterlod resten uden forslag.
    if (missing.length > 0 && altByStore[storeName] === undefined) {
        altByStore[storeName] = [];
        fetch('/api/alternatives', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ missing_items: missing })
        })
        .then(r => r.json())
        .then(data => {
            if (!data.success) {
                // Ægte fejl (400/500), ikke bare "ingen forslag fundet" -
                // ryd cache-posten, så næste klik på butikken prøver igen i
                // stedet for at låse fejlen fast resten af sessionen (før
                // var altByStore[storeName] allerede sat til [] på linje
                // 1346, så retry-tjekket ovenfor kunne aldrig blive sandt igen).
                delete altByStore[storeName];
                return;
            }
            if (!data.alternatives || !data.alternatives.length) return;
            altByStore[storeName] = data.alternatives;
            // Accepterer man et forslag undervejs, regnes hele sammenligningen
            // forfra - så er svaret her forældet og må ikke tegnes ind.
            if (_scoCompData !== compData) return;
            const activeCard = document.querySelector('.sco-store-card.active');
            if (activeCard && activeCard.dataset.store === storeName) {
                renderScoItemList(storeName, matched, missing, data.alternatives, storeData.totalPrice);
            }
        })
        .catch(err => {
            console.error('Error fetching alternatives:', err);
            delete altByStore[storeName];
        });
    }
}

function renderScoItemList(storeName, matched, missing, alternatives, totalPrice) {
    const list = document.getElementById('sco-item-list');
    if (!list) return;

    let html = '';

    // Varer der MANGLER hos butikken (øverst)
    if (missing.length > 0) {
        html += `<div class="sco-il-section-label">Mangler hos ${escapeHtml(storeName)}</div>`;
        missing.forEach(item => {
            const alt = alternatives.find(a => a.cart_id === item.cart_id);
            const imgSrc = item.image || '';

            let altHtml = '';
            if (alt) {
                const altData = JSON.stringify(alt).replace(/"/g, '&quot;');
                const safeCartId = escapeHtml(item.cart_id).replace(/'/g, '&#39;');
                altHtml = `
                    <div class="sco-il-alt">
                        <img class="sco-il-alt-img" src="${escapeHtml(alt.alt_image || '')}" alt="${escapeHtml(alt.alt_name)}" onerror="this.style.display='none'">
                        <div class="sco-il-alt-info">
                            <div class="sco-il-alt-name">${escapeHtml(stripStoreBrand(alt.alt_name))}</div>
                            <div class="sco-il-alt-price">${alt.alt_price.toFixed(2)} kr</div>
                        </div>
                        <button class="sco-il-alt-btn" onclick="acceptAlternative('${safeCartId}', ${altData})" title="Skift til dette alternativ">+</button>
                    </div>`;
            }

            html += `
                <div class="sco-il-row sco-il-row--missing">
                    <div class="sco-il-left">
                        ${imgSrc ? `<img class="sco-il-img" src="${escapeHtml(imgSrc)}" alt="" onerror="this.style.display='none'">` : '<div class="sco-il-img sco-il-img--empty"></div>'}
                        <div class="sco-il-name">${escapeHtml(item.name)}</div>
                    </div>
                    ${altHtml}
                </div>`;
        });
    }

    // Varer der MATCHER hos butikken (nederst)
    if (matched.length > 0) {
        if (missing.length > 0) html += `<div class="sco-il-divider"></div>`;
        html += `<div class="sco-il-section-label">Matcher hos ${escapeHtml(storeName)}</div>`;
        matched.forEach(item => {
            html += `
                <div class="sco-il-row">
                    <div class="sco-il-left">
                        ${item.image ? `<img class="sco-il-img" src="${escapeHtml(item.image)}" alt="" onerror="this.style.display='none'">` : '<div class="sco-il-img sco-il-img--empty"></div>'}
                        <div class="sco-il-name">${escapeHtml(item.name)}${item.quantity > 1 ? ` <span class="sco-il-qty">×${item.quantity}</span>` : ''}</div>
                    </div>
                    <div class="sco-il-price">${(item.price * item.quantity).toFixed(2)} kr</div>
                </div>`;
        });
    }

    // Total
    html += `
        <div class="sco-il-total">
            <span>${matched.length}/${matched.length + missing.length} varer matchet</span>
            <span>${totalPrice.toFixed(2)} kr</span>
        </div>`;

    list.innerHTML = html;
}

// Produktnavne og billed-URL'er kommer fra butikkernes feeds og indsaettes
// bl.a. i dobbelt-quotede attributter (src/alt i kurv, sammenligning og
// butiksrute). textContent -> innerHTML escaper IKKE anfoerselstegn, saa den
// vej kunne en vaerdi bryde ud af attributten. Escapes eksplicit, saa
// funktionen er sikker i baade tekst- og attribut-kontekst.
function escapeHtml(text) {
    return String(text == null ? '' : text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

/**
 * Ingredienslister kommer fra tredjeparter - bl.a. Open Food Facts, som alle
 * kan redigere - saa indholdet er ikke betroet markup. Kilderne bruger dog
 * <b> til den lovpligtige allergen-fremhaevning og <br> til linjeskift, saa vi
 * kan ikke bare escape alt. Derfor: escape ALT, og gendan kun de to tags i
 * deres attributloese form. "<b onclick=...>" bliver til "&lt;b onclick=...&gt;"
 * og matcher ikke moenstrene nedenfor - det forbliver ufarlig tekst.
 */
function sanitizeNutritionHtml(text) {
    return escapeHtml(text)
        .replace(/&lt;(\/?)b&gt;/gi, '<$1b>')
        .replace(/&lt;br\s*\/?&gt;/gi, '<br>');
}

function stripStoreBrand(name) {
    if (!name) return name;
    const prefixes = [
        'rema 1000 ', 'rema ', 'salling ', 'coop ', 'xtra ', 'änglamark ',
        'irma ', 'first price ', 'fp ', 'grøn balance ', 'gestus ', 'levevis ',
        'vores ', 'karma ', 'cirkel ', 'bilka ', 'meny ', 'spar ',
        'min købmand ', 'min kobmand ',
    ];
    const lower = name.toLowerCase();
    for (const prefix of prefixes) {
        if (lower.startsWith(prefix)) {
            const stripped = name.slice(prefix.length).trim();
            return stripped.charAt(0).toUpperCase() + stripped.slice(1).toLowerCase();
        }
    }
    // Normalize all-caps names (e.g. "MINIMÆLK" → "Minimælk")
    if (name === name.toUpperCase() && name.length > 1) {
        return name.charAt(0) + name.slice(1).toLowerCase();
    }
    return name;
}

/** Lille liste med billede + pris for varer der kun findes i én butik. */
function buildExclusiveSlotHtml(title, items) {
    if (!items || items.length === 0) return '';
    const rows = items.map((it) => {
        const unit = Number(it.unitPrice);
        const q = Number(it.quantity) || 1;
        const line = (unit * q).toFixed(2);
        const qtyPart = q > 1 ? ` · ${q} stk` : '';
        const hasImg = it.image && String(it.image).trim();
        const thumb = hasImg
            ? `<img src="${escapeHtml(it.image)}" alt="" class="store-exclusive-img" loading="lazy" width="40" height="40">`
            : '<div class="store-exclusive-img store-exclusive-img--empty" aria-hidden="true"></div>';
        return `
            <div class="store-exclusive-item">
                ${thumb}
                <div class="store-exclusive-meta">
                    <span class="store-exclusive-name">${escapeHtml(it.name)}${qtyPart}</span>
                    <span class="store-exclusive-lineprice">${line} kr</span>
                </div>
            </div>`;
    }).join('');
    return `<p class="store-exclusive-title">${escapeHtml(title)}</p><div class="store-exclusive-list">${rows}</div>`;
}

function closeStoreComparison() {
    document.getElementById('store-comparison-overlay').style.display = 'none';
    document.body.style.overflow = '';
    _scoCompData = null;
}

function closeButiksrute() {
    const overlay = document.getElementById('butiksrute-overlay');
    if (overlay) { overlay.style.display = 'none'; }
    document.body.style.overflow = '';
}

async function showButiksrute() {
    const cartProducts = safeJSONParse('cart', []);
    if (cartProducts.length === 0) {
        alert('Kurven er tom - tilføj varer for at se butiksruten.');
        return;
    }

    const overlay = document.getElementById('butiksrute-overlay');
    const summaryEl = document.getElementById('br-summary');
    const storesEl = document.getElementById('br-stores');
    if (!overlay || !summaryEl || !storesEl) return;

    summaryEl.innerHTML = '<div class="br-loading">Beregner optimal rute…</div>';
    storesEl.innerHTML = '';
    overlay.style.cssText = 'display:flex; position:fixed; inset:0; z-index:1100; align-items:flex-end; justify-content:center;';
    document.body.style.overflow = 'hidden';

    try {
        const { stores } = await calculateStoreComparisons();
        if (!stores || stores.length === 0) {
            summaryEl.innerHTML = '<div class="br-loading">Ingen prisdata fundet.</div>';
            return;
        }

        const isValidPrice = (p) => p != null && !isNaN(p) && Number(p) > 0;

        // Group each cart item by its cheapest store
        const grouped = {};
        cart.forEach(item => {
            let prices = item.storePrices || {};
            if (!prices || Object.keys(prices).length === 0) {
                const legacyMap = {
                    'Rema 1000': item.remaPrice, 'Bilka': item.bilkaPrice,
                    'Min Købmand': item.mkPrice, 'Meny': item.menyPrice, 'Spar': item.sparPrice
                };
                prices = {};
                for (const [lbl, p] of Object.entries(legacyMap)) {
                    if (p != null) prices[lbl] = p;
                }
                if (Object.keys(prices).length === 0) prices[item.store || 'Rema 1000'] = item.price;
            }

            let bestStore = null, bestPrice = Infinity;
            for (const [store, p] of Object.entries(prices)) {
                if (isValidPrice(p) && selectedStores.has(store) && Number(p) < bestPrice) {
                    bestPrice = Number(p); bestStore = store;
                }
            }
            if (!bestStore) {
                for (const [store, p] of Object.entries(prices)) {
                    if (isValidPrice(p) && Number(p) < bestPrice) {
                        bestPrice = Number(p); bestStore = store;
                    }
                }
            }
            const store = bestStore || item.store || 'Ukendt butik';
            const price = bestPrice === Infinity ? (item.price || 0) : bestPrice;
            if (!grouped[store]) grouped[store] = { items: [], subtotal: 0 };
            grouped[store].items.push({ item, price });
            grouped[store].subtotal += price * (item.quantity || 1);
        });

        // Total combined price (optimal route)
        const routeTotal = Object.values(grouped).reduce((s, g) => s + g.subtotal, 0);

        // Single cheapest store total
        const singleCheapest = [...stores].sort((a, b) => a.totalPrice - b.totalPrice)[0];
        const savings = singleCheapest ? (singleCheapest.totalPrice - routeTotal) : 0;
        const storeCount = Object.keys(grouped).length;

        // Summary bar
        summaryEl.innerHTML = `
            <div class="br-summary-row">
                <div class="br-summary-main">
                    <span class="br-summary-total">${routeTotal.toFixed(2)} kr</span>
                    <span class="br-summary-label">fordelt på ${storeCount} butik${storeCount !== 1 ? 'ker' : ''}</span>
                </div>
                ${savings > 0.05 ? `<div class="br-summary-save">Spar ${savings.toFixed(2)} kr<span class="br-summary-save-vs"> ift. ${escapeHtml(singleCheapest.name)}</span></div>` : ''}
            </div>`;

        // Render each store group
        const storesSorted = Object.entries(grouped).sort((a, b) => b[1].subtotal - a[1].subtotal);
        storesEl.innerHTML = storesSorted.map(([storeName, group]) => {
            const storeEntry = ALL_STORES.find(s => s.label === storeName);
            const logoHtml = storeEntry ? `<img class="br-store-logo" src="${escapeHtml(storeEntry.logo)}" alt="${escapeHtml(storeName)}">` : '';
            const itemsHtml = group.items.map(({ item, price }) => `
                <div class="br-item">
                    <img class="br-item-img" src="${escapeHtml(item.image || '')}" alt="${escapeHtml(item.name)}" onerror="this.style.display='none'">
                    <span class="br-item-name">${escapeHtml(stripStoreBrand(item.name))}${(item.quantity || 1) > 1 ? ` <span class="br-item-qty">×${item.quantity}</span>` : ''}</span>
                    <span class="br-item-price">${(price * (item.quantity || 1)).toFixed(2)} kr</span>
                </div>`).join('');
            return `
                <div class="br-store-group">
                    <div class="br-store-header">
                        ${logoHtml}
                        <span class="br-store-name">${escapeHtml(storeName)}</span>
                        <span class="br-store-subtotal">${group.subtotal.toFixed(2)} kr</span>
                    </div>
                    <div class="br-store-items">${itemsHtml}</div>
                </div>`;
        }).join('');

    } catch (err) {
        console.error('Butiksrute error:', err);
        summaryEl.innerHTML = '<div class="br-loading">Kunne ikke beregne rute - prøv igen.</div>';
    }
}

function toggleScoMissing() {
    const btn  = document.getElementById('sco-missing-toggle');
    const body = document.getElementById('sco-missing-body');
    const open = body.classList.toggle('open');
    btn.classList.toggle('open', open);
    btn.setAttribute('aria-expanded', String(open));
}

async function calculateStoreComparisons() {
    const allLabels   = ALL_STORES.map(s => s.label);
    const storeTotals = Object.fromEntries(allLabels.map(l => [l, 0]));
    const storeCoverage = Object.fromEntries(allLabels.map(l => [l, 0]));
    const missingDetails = Object.fromEntries(allLabels.map(l => [l, []]));
    const matchedItemsPerStore = Object.fromEntries(allLabels.map(l => [l, []]));
    let linesWithoutMatches = 0;
    const exclusiveItems = Object.fromEntries(allLabels.map(l => [l, []]));
    const partialItems = [];
    // We collect raw partial data first, then filter after storeTotals is complete
    const rawPartials = [];

    const cartProducts = safeJSONParse('cart', []);

    // Fetch live Rema product data to augment cart prices
    let remaMap = null;
    try {
        const response = await fetch('/api/products');
        const data = await response.json();
        if (data.success) {
            remaMap = new Map(
                data.rema_products.map(p => [String(p['/product/id']), p])
            );
        }
    } catch (error) {
        console.error('Error fetching products for comparison:', error);
    }

    cartProducts.forEach(cartItem => {
        const productId  = String(cartItem.id.replace('product', ''));
        const quantity   = cartItem.quantity;
        const itemStore  = cartItem.store || 'Rema 1000';

        // Build per-label price map from new or legacy cart format
        const prices = {};
        if (cartItem.storePrices) {
            for (const [label, p] of Object.entries(cartItem.storePrices)) {
                const v = Number(p);
                if (!Number.isNaN(v) && v > 0) prices[label] = v;
            }
        } else {
            // Legacy cart item migration
            const legacyMap = {
                'Rema 1000': cartItem.remaPrice, 'Bilka': cartItem.bilkaPrice,
                'Min Købmand': cartItem.mkPrice,  'Meny': cartItem.menyPrice, 'Spar': cartItem.sparPrice
            };
            for (const [label, p] of Object.entries(legacyMap)) {
                const v = Number(p);
                if (p != null && !Number.isNaN(v) && v > 0) prices[label] = v;
            }
            // Re-bucket old items that had visible price stored under wrong label
            const inferredStore = itemStore
                || (productId.startsWith('bilka_') ? 'Bilka'
                    : productId.startsWith('mk_')   ? 'Min Købmand' : 'Rema 1000');
            if (inferredStore !== 'Rema 1000' && prices['Rema 1000'] != null && prices[inferredStore] == null) {
                prices[inferredStore] = prices['Rema 1000'];
                delete prices['Rema 1000'];
            }
            if (Object.keys(prices).length === 0 && cartItem.price != null && Number(cartItem.price) > 0) {
                prices[inferredStore] = Number(cartItem.price);
            }
        }

        // Enhance with live API data
        const remaProduct = remaMap ? remaMap.get(productId) : null;
        if (remaProduct) {
            if (prices['Rema 1000'] == null) {
                prices['Rema 1000'] = getProductPrice(remaProduct);
            }
            const storeMatches = remaProduct['/product/store_matches'] || {};
            for (const [key, match] of Object.entries(storeMatches)) {
                const storeEntry = ALL_STORES.find(s => s.key === key);
                if (storeEntry && prices[storeEntry.label] == null) {
                    const v = parseFloat(match.price);
                    if (!Number.isNaN(v) && v > 0) prices[storeEntry.label] = v;
                }
            }
        }

        // Accumulate totals for selected stores, applying bundle deals where applicable
        for (const [label, p] of Object.entries(prices)) {
            if (selectedStores.has(label) && !Number.isNaN(p)) {
                storeCoverage[label] += 1;
                const dealStr = cartItem.storeMultiDeals ? (cartItem.storeMultiDeals[label] || '') : '';
                storeTotals[label] = (storeTotals[label] || 0) + applyDealPrice(p, quantity, dealStr);
                matchedItemsPerStore[label].push({
                    cart_id: cartItem.id,
                    name: stripStoreBrand(cartItem.name || 'Vare'),
                    image: cartItem.image || '',
                    price: p,
                    quantity: quantity
                });
            }
        }

        // Track missing details per store. Prisen sendes med, så serveren kan
        // afvise erstatninger i en helt anden prisklasse (en kasse øl for én).
        const knownPrices = Object.values(prices).filter(p => Number(p) > 0);
        const refPrice = knownPrices.length ? Math.min(...knownPrices) : Number(cartItem.price) || 0;
        for (const label of selectedStores) {
            if (prices[label] == null || Number.isNaN(Number(prices[label])) || Number(prices[label]) <= 0) {
                missingDetails[label].push({
                    cart_id: cartItem.id,
                    product_id: cartItem.id,
                    name: stripStoreBrand(cartItem.name || 'Vare'),
                    image: cartItem.image || '',
                    category: cartItem.category || '',
                    weight_str: cartItem.unitMeasure || '',
                    price: refPrice,
                    store: label
                });
            }
        }

        const availableCount = Object.values(prices).filter(p => p != null && !Number.isNaN(p)).length;
        if (availableCount < 2) linesWithoutMatches += 1;

        // Exclusive-store tracking: only one label has a price
        if (availableCount === 1) {
            const [onlyLabel, onlyPrice] = Object.entries(prices)[0];
            if (exclusiveItems[onlyLabel]) {
                exclusiveItems[onlyLabel].push({
                    name: cartItem.name || 'Vare',
                    image: cartItem.image || '',
                    unitPrice: onlyPrice,
                    quantity: quantity
                });
            }
        }

        // Partial-availability tracking: item exists in some but not all selected stores
        const availableInSelected = Object.entries(prices)
            .filter(([label, p]) => selectedStores.has(label) && !Number.isNaN(Number(p)) && Number(p) > 0)
            .length;
        const selectedCount = selectedStores.size;
        if (availableInSelected > 0 && availableInSelected < selectedCount) {
            rawPartials.push({
                name: stripStoreBrand(cartItem.name || 'Vare'),
                image: cartItem.image || '',
                prices
            });
        }
    });

    const totalCartItems = cartProducts.length;
    const stores = allLabels
        .filter(l => selectedStores.has(l) && (storeTotals[l] > 0 || storeCoverage[l] > 0))
        .map(l => ({ 
            name: l, 
            totalPrice: parseFloat(storeTotals[l].toFixed(2)),
            coverage: storeCoverage[l],
            totalItems: totalCartItems,
            missingDetails: missingDetails[l]
        }));

    // Build partialItems now that storeTotals is complete - only show stores visible in comparison
    const comparisonStores = new Set(stores.map(s => s.name));
    for (const raw of rawPartials) {
        const missingStores = [...comparisonStores].filter(label => {
            const p = raw.prices[label];
            return p == null || Number.isNaN(Number(p)) || Number(p) <= 0;
        });
        if (missingStores.length > 0) {
            partialItems.push({ name: raw.name, image: raw.image, missingStores });
        }
    }

    return { stores, linesWithoutMatches, exclusiveItems, partialItems, matchedItemsPerStore };
}

function getProductPrice(product) {
    const salePrice = product['/product/sale_price'];
    const regularPrice = product['/product/price'];
    return salePrice && !isNaN(salePrice) ? parseFloat(salePrice) : parseFloat(regularPrice);
}

// Add event listener for ESC key to close overlays
document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') {
        const brOverlay = document.getElementById('butiksrute-overlay');
        if (brOverlay && brOverlay.style.display === 'flex') { closeButiksrute(); return; }
        const storeComparisonOverlay = document.getElementById('store-comparison-overlay');
        if (storeComparisonOverlay.style.display === 'flex') {
            closeStoreComparison();
        }
    }
});

// Close store comparison overlay when clicking outside
document.addEventListener('click', function (event) {
    const overlay = document.getElementById('store-comparison-overlay');
    const content = document.querySelector('.sco-modal');

    if (overlay && overlay.style.display === 'flex' &&
        content && !content.contains(event.target) &&
        event.target !== overlay) {
        closeStoreComparison();
    }
});

async function initAllStores() {
    let catalogVersion = 1;
    let storesAdded = {};
    try {
        const res  = await fetch('/api/stores');
        const data = await res.json();
        ALL_STORES = data.stores; // [{key, label, logo}, ...]
        catalogVersion = data.version || 1;
        storesAdded = data.stores_added || {};
    } catch (err) {
        // Tomt katalog haandteres defensivt i applyStoreFilters og
        // updateInternalLinks (vis alt, ingen ?stores=). Logges saa fejlen
        // ikke er helt tavs, hvis nogen undrer sig over manglende butiksvalg.
        console.warn('[stores] Kunne ikke hente butikskataloget - viser alle produkter:', err);
        ALL_STORES = [];
    }
    window._storeCatalogVersion = catalogVersion;

    const allLabels = ALL_STORES.map(s => s.label);
    const urlStores = new URLSearchParams(window.location.search).get('stores');
    const savedVersion = parseInt(localStorage.getItem('storeCatalogVersion') || '0', 10);
    const cookieStoresBefore = readCookieStores();
    let storesAddedByVersion = false;

    if (urlStores) {
        // URL takes precedence - user followed a link with an explicit store selection
        selectedStores = new Set(urlStores.split(',').filter(s => allLabels.includes(s)));
        if (selectedStores.size === 0) selectedStores = new Set(allLabels);
    } else {
        const saved = safeJSONParse('selectedStores', null);
        const prevKnown = new Set(safeJSONParse('knownStores', []));

        if (saved && Array.isArray(saved) && saved.length > 0) {
            // Filtrér mod det AKTUELLE katalog. URL-grenen ovenfor gjorde det
            // allerede, men localStorage-grenen gjorde ikke: en omdoebt eller
            // fjernet butik blev haengende i vaelget og talte med i
            // "alle valgt"-beregningen (size >= ALL_STORES.length), saa
            // ?stores= blev sat paa alle links selvom alt reelt var valgt.
            selectedStores = new Set(saved.filter(l => allLabels.includes(l)));
            if (selectedStores.size === 0) selectedStores = new Set(allLabels);
            // Only add stores that are genuinely new (never seen before)
            allLabels.forEach(label => {
                if (!prevKnown.has(label)) selectedStores.add(label);
            });
        } else {
            selectedStores = new Set(allLabels);
        }
    }

    // Auto-enable butikker tilføjet i nyere katalog-versioner (fx Lidl)
    if (catalogVersion > savedVersion) {
        for (let ver = savedVersion + 1; ver <= catalogVersion; ver++) {
            const labels = storesAdded[ver] || storesAdded[String(ver)] || [];
            labels.forEach(label => {
                if (allLabels.includes(label) && !selectedStores.has(label)) {
                    storesAddedByVersion = true;
                }
                if (allLabels.includes(label)) selectedStores.add(label);
            });
        }
        if (harFunktioneltSamtykke()) localStorage.setItem('storeCatalogVersion', String(catalogVersion));
    }

    if (harFunktioneltSamtykke()) {
        localStorage.setItem('knownStores', JSON.stringify(allLabels));
        localStorage.setItem('selectedStores', JSON.stringify([...selectedStores]));
    }
    saveStoreFilters();

    // cookieStoresBefore er null når brugeren (endnu) ikke har givet funktionelt
    // samtykke - cookien bliver da aldrig skrevet, selvom valget reelt matcher
    // serverens standard (alle butikker). Uden dette faldt storesChanged altid ud
    // som "true" i det tilfælde, hvilket tvang en unødig content-refetch der
    // nulstillede page-parametret på hver eneste sideindlæsning.
    const storesChanged = cookieStoresBefore
        ? JSON.stringify([...selectedStores].sort()) !== JSON.stringify([...cookieStoresBefore].sort())
        : JSON.stringify([...selectedStores].sort()) !== JSON.stringify([...allLabels].sort());

    // Search functionality - only trigger on Enter, not on every keystroke
    const searchInput = document.getElementById('searchInput');
    if (searchInput) {
        searchInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                closeAutocomplete();
                performSearch();
                searchInput.blur();
            }
        });
    }

    initStoreFilters();
    updateCartDisplay();
    updateCartCount();
    attachProductEventListeners();

    // Genindlæs server-renderet indhold når Lidl (eller andre nye butikker) netop er tilføjet
    if ((storesAddedByVersion || storesChanged) && document.getElementById('dynamic-content')) {
        updateDynamicStoreContent(false); // reconciling med gemt valg, ikke en brugerhandling - bevar page
    }

    const referenceBtn = document.querySelector('.show-reference-btn');
    if (referenceBtn && !referenceBtn.querySelector('.button-text')) {
        const buttonText = referenceBtn.textContent;
        referenceBtn.innerHTML = `
            <span class="button-text">${buttonText}</span>
            <div class="loading-spinner"></div>
        `;
    }

    if (typeof initAdvancedFilters === 'function') initAdvancedFilters();
    if (typeof initSettings === 'function')        initSettings();
    if (typeof initAutocomplete === 'function')    initAutocomplete();
    initCategoryAnalytics();
    updateListsBadge();
    initMobileEnhancements();
    initPriceAlertButton();
}

/** Track category-nav clicks (full page loads) - only with Analyse-samtykke */
function initCategoryAnalytics() {
    document.addEventListener('click', (e) => {
        const link = e.target.closest('.category-nav a, .nav-category-grid a');
        if (!link) return;
        const label = (link.textContent || '').trim();
        const path = link.getAttribute('href') || '';
        if (!label || !path) return;
        trackEvent('category_click', {
            category: label.slice(0, 40),
            path: path.slice(0, 80)
        });
    });
}

document.addEventListener('DOMContentLoaded', initAllStores);

// Subcategory pill bar
document.addEventListener('DOMContentLoaded', () => {
    const bar = document.getElementById('subcategoryBar');
    if (!bar) return;
    bar.addEventListener('click', (e) => {
        const pill = e.target.closest('.subcategory-pill');
        if (!pill) return;
        bar.querySelectorAll('.subcategory-pill').forEach(p => p.classList.remove('active'));
        pill.classList.add('active');
        if (typeof applyAllFilters === 'function') applyAllFilters(false, true);
    });
});

// Function to perform AJAX search
let searchTimeout = null;
// Seneste søgeord vist i det flydende panel. Bruges af applyAllFilters til at
// genhente panelets resultater med nye filtre - søgefeltets aktuelle indhold
// kan være noget andet, hvis brugeren er begyndt at taste en ny søgning.
let _lastSearchQuery = '';

// Henter og indsætter en side af søgeresultater (inkl. samme paginerings-UI
// som kategori-sider) i det flydende søgepanel. Genbruges af både den
// debouncede indtastningssøgning (side 1) og klik på sidetal/side-hop.
function fetchSearchResults(query, page, sporSoegning = true) {
    const searchResults = document.getElementById('searchResults');
    const wrapper = document.getElementById('searchProductsWrapper');
    const searchTitle = searchResults.querySelector('.search-title');
    if (!query) return;

    _lastSearchQuery = query;
    searchResults.style.display = 'block';
    searchTitle.textContent = `Søgeresultater for "${query}"`;

    // Filter/sortering sendes med, så panelets resultater følger filterpanelet
    // over panelet (og bevares når man bladrer mellem sider).
    const params = new URLSearchParams();
    params.set('q', query);
    params.set('stores', Array.from(selectedStores).join(','));
    params.set('page', page);
    applyFilterParams(params, readFilterValues(searchFilterPanel()));

    fetch(`/search?${params.toString()}`)
        .then(response => response.json())
        .then(data => {
            if (data.html) {
                wrapper.innerHTML = data.html;
                attachProductEventListeners();

                const resultCount = wrapper.querySelectorAll('.product').length;
                // Kun ved en REEL ny soegning - ellers taelles hver
                // sidebladring og hvert filterskift som en soegning i GA4.
                if (sporSoegning) trackEvent('search', {
                    search_term: query.slice(0, 80),
                    result_count: resultCount
                });

                // Force reflow and add visibility classes
                requestAnimationFrame(() => {
                    updateHeaderHeight();
                    searchResults.scrollTop = 0;
                    searchResults.classList.add('visible');
                    wrapper.classList.add('visible');
                    document.body.classList.add('search-active');
                    applyStoreFilters();
                });
            } else {
                wrapper.innerHTML = '<div class="no-results">Ingen resultater fundet</div>';
                // Kun ved en REEL ny soegning - ellers taelles hver
                // sidebladring og hvert filterskift som en soegning i GA4.
                if (sporSoegning) trackEvent('search', {
                    search_term: query.slice(0, 80),
                    result_count: 0
                });
            }
        })
        .catch(error => {
            console.error('Search error:', error);
            wrapper.innerHTML = '<div class="error">Der opstod en fejl under søgningen</div>';
        });
}

function performSearch() {
    const searchInput = document.getElementById('searchInput');
    const searchResults = document.getElementById('searchResults');
    const query = searchInput.value.trim();

    if (searchTimeout) {
        clearTimeout(searchTimeout);
    }

    // Annullér en ventende/igangværende autocomplete FØR søgningen sendes.
    // Uden dette kan et Enter-tryk lige efter en tastetryks-pause sende
    // /api/autocomplete og /search af sted mod samme isolate næsten samtidig -
    // Cloudflares Python Workers-runtime tillader ikke to overlappende
    // request-tasks i én isolate ("Cannot enter into task ... while another
    // task is being executed"), verificeret i produktion 2026-08-05.
    closeAutocomplete();

    if (!query) {
        _lastSearchQuery = '';
        searchResults.classList.remove('visible');
        setTimeout(() => {
            searchResults.style.display = 'none';
            document.body.classList.remove('search-active');
        }, 300);
        return;
    }

    // Hver søgning starter med rene filtre. Kun søgepanelets egne felter
    // røres - filtrene på siden bagved (fx en kategoriside man er i gang med
    // at browse) skal stå uændrede, så de stadig passer på listen bagved.
    resetFilterPanel(searchFilterPanel());

    searchTimeout = setTimeout(() => fetchSearchResults(query, 1), 500);
}

// Klik på sidetal/pil inde i søgepanelet skal blade i panelet i stedet for
// at navigere væk til JSON-endpointet linket peger på (samme UX som
// kategori-siders paginering, blot uden fuld sidegenindlæsning).
document.addEventListener('DOMContentLoaded', () => {
    const searchResults = document.getElementById('searchResults');
    if (!searchResults) return;
    searchResults.addEventListener('click', (e) => {
        const link = e.target.closest('.pagination a.page-btn');
        if (!link) return;
        e.preventDefault();
        const url = new URL(link.href, window.location.origin);
        const page = url.searchParams.get('page') || 1;
        const query = url.searchParams.get('q') || '';
        fetchSearchResults(query, page, false);
    });
});

// ===== AUTOCOMPLETE =====
let _acTimeout = null;
let _acIndex = -1;   // current keyboard-focused row index
let _acController = null; // aborter for the in-flight autocomplete fetch

function initAutocomplete() {
    const input = document.getElementById('searchInput');
    const dropdown = document.getElementById('autocomplete-dropdown');
    if (!input || !dropdown) return;

    // Input event - debounced fetch
    input.addEventListener('input', () => {
        clearTimeout(_acTimeout);
        _acIndex = -1;
        const q = input.value.trim();
        if (q.length < 2) { closeAutocomplete(); return; }
        _acTimeout = setTimeout(() => fetchAutocomplete(q), 200);
    });

    // Keyboard navigation inside the dropdown
    input.addEventListener('keydown', (e) => {
        const items = dropdown.querySelectorAll('.autocomplete-item');
        if (e.key === 'ArrowDown' && dropdown.classList.contains('open')) {
            e.preventDefault();
            _acIndex = Math.min(_acIndex + 1, items.length - 1);
            updateAcActive(items);
        } else if (e.key === 'ArrowUp' && dropdown.classList.contains('open')) {
            e.preventDefault();
            _acIndex = Math.max(_acIndex - 1, 0);
            updateAcActive(items);
        } else if (e.key === 'Enter') {
            if (dropdown.classList.contains('open') && _acIndex >= 0) {
                e.preventDefault();
                items[_acIndex].click();
            }
            // If no item selected, fall through to the keydown listener in initAllStores
        } else if (e.key === 'Escape') {
            closeAutocomplete();
        }
    });

    // Close when clicking outside
    document.addEventListener('click', (e) => {
        if (!input.contains(e.target) && !dropdown.contains(e.target)) {
            closeAutocomplete();
        }
    });
}

function updateAcActive(items) {
    items.forEach((el, i) => el.classList.toggle('ac-active', i === _acIndex));
}

function closeAutocomplete() {
    // Cancel any in-flight request so a late response can't reopen the dropdown
    if (_acController) {
        _acController.abort();
        _acController = null;
    }
    clearTimeout(_acTimeout);
    const dropdown = document.getElementById('autocomplete-dropdown');
    if (dropdown) dropdown.classList.remove('open');
    // Soegefeltet er en combobox: skaermlaesere annoncerer kun forslagene
    // hvis aria-expanded foelger den faktiske tilstand.
    const acInput = document.getElementById('searchInput');
    if (acInput) acInput.setAttribute('aria-expanded', 'false');
    _acIndex = -1;
}

async function fetchAutocomplete(query) {
    if (_acController) _acController.abort();
    const controller = new AbortController();
    _acController = controller;
    try {
        const storesParam = Array.from(selectedStores).join(',');
        const url = `/api/autocomplete?q=${encodeURIComponent(query)}&stores=${encodeURIComponent(storesParam)}`;
        const res = await fetch(url, { signal: controller.signal });
        const data = await res.json();
        if (_acController === controller) {
            renderAutocomplete(data.suggestions || [], query, data.query_suggestion || query);
        }
    } catch (err) {
        if (err.name !== 'AbortError') console.error('Autocomplete fetch error:', err);
    }
}

function renderAutocomplete(suggestions, query, querySuggestion) {
    const dropdown = document.getElementById('autocomplete-dropdown');
    const input    = document.getElementById('searchInput');
    if (!dropdown) return;

    const qSuggest = (querySuggestion || query || '').trim();
    if (suggestions.length === 0 && !qSuggest) {
        closeAutocomplete();
        return;
    }

    const escHtml = (s) => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');

    // Highlight matching substring in product name
    function highlight(text, q) {
        const terms = q.trim().split(/\s+/).filter(Boolean);
        let result = escHtml(text);
        terms.forEach(term => {
            const re = new RegExp(`(${term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi');
            result = result.replace(re, '<mark style="background:var(--green-light);color:var(--green-dark);border-radius:2px;padding:0 1px;">$1</mark>');
        });
        return result;
    }

    // Første række: selve søgeordet, så man kan vælge præcist "øl" (ikke pølser)
    let html = '';
    if (qSuggest) {
        html += `<div class="autocomplete-item ac-query" role="option" tabindex="-1"
                     onclick="selectAutocomplete(${escHtml(JSON.stringify(qSuggest))})">
            <div class="ac-query-icon" aria-hidden="true">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                    <circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
                </svg>
            </div>
            <div class="ac-info">
                <div class="ac-name">Søg efter <strong>${escHtml(qSuggest)}</strong></div>
            </div>
        </div>`;
    }

    html += suggestions.map((s) => {
        const imgHtml = s.image && !s.image.includes('logo')
            ? `<img class="ac-thumb" src="${escHtml(s.image)}" alt="" loading="lazy" onerror="this.style.display='none'">`
            : `<div class="ac-thumb-placeholder"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg></div>`;

        const priceHtml = s.price > 0
            ? `<span class="ac-price${s.is_sale ? ' ac-sale' : ''}">${s.price.toFixed(2).replace('.',',')} kr</span>`
            : '';

        const brandHtml = s.brand && s.brand !== 'nan'
            ? `<div class="ac-brand">${escHtml(s.brand)}</div>`
            : '';

        return `<div class="autocomplete-item" role="option" tabindex="-1"
                     onclick="selectAutocomplete(${escHtml(JSON.stringify(s.name))})">
            ${imgHtml}
            <div class="ac-info">
                <div class="ac-name">${highlight(s.name, query)}</div>
                ${brandHtml}
            </div>
            ${priceHtml}
        </div>`;
    }).join('');

    // Footer: "Se alle resultater for ..."
    // escHtml om JSON.stringify som i item-kaldet ovenfor: JSON escaper " som \",
    // men backslash betyder intet i en HTML-attribut, så anførselstegnet ville
    // ellers lukke onclick="..." og lade resten af søgeteksten blive til markup.
    if (suggestions.length > 0) {
        html += `<div class="ac-footer" onclick="selectAutocomplete(${escHtml(JSON.stringify(query))})">
            Se alle resultater for "${escHtml(query)}" →
        </div>`;
    }

    dropdown.innerHTML = html;
    dropdown.classList.add('open');
    const acInput = document.getElementById('searchInput');
    if (acInput) acInput.setAttribute('aria-expanded', 'true');
    _acIndex = -1;
}

function selectAutocomplete(name) {
    const input = document.getElementById('searchInput');
    if (input) {
        input.value = name;
    }
    closeAutocomplete();
    performSearch();
}

// Close search results when pressing Escape
document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') {
        const searchResults = document.getElementById('searchResults');
        const searchInput = document.getElementById('searchInput');
        if (!searchResults || !searchInput) return;

        // KUN naar soegepanelet faktisk er fremme. Handleren koerte foer ved
        // ETHVERT Escape-tryk, saa lukkede man kurven eller en modal med Esc,
        // blev hele soegefeltet ryddet med i koebet - og et halvt indtastet
        // soegeord var vaek uden at man havde bedt om det.
        const panelFremme = searchResults.classList.contains('visible')
            || getComputedStyle(searchResults).display !== 'none';
        if (!panelFremme) return;

        searchResults.style.display = 'none';
        // .visible er den tilstand resten af koden spørger om ("er en søgning
        // fremme?"). Uden at fjerne den her så et butiksskift bagefter panelet
        // som åbent og genkørte søgningen på en side ingen kunne se.
        searchResults.classList.remove('visible');
        document.getElementById('searchProductsWrapper')?.classList.remove('visible');
        _lastSearchQuery = '';
        document.body.classList.remove('search-active');
        searchInput.value = '';
        searchInput.blur();
    }
});

// Function to update quantity in overlay
function updateOverlayQuantity(change) {
    const quantityElement = document.querySelector('#overlay .quantity');
    if (!quantityElement) return;
    let quantity = parseInt(quantityElement.textContent);
    quantity = Math.max(1, quantity + change);
    quantityElement.textContent = quantity;
}

// Function to add to cart from overlay
function addToCartFromOverlay(event) {
    event.preventDefault();
    const addToCartBtn = event.target;
    const productInfoEl = document.querySelector('.product-info');
    const productId = productInfoEl ? productInfoEl.dataset.productId : null;
    const quantityEl = document.querySelector('#overlay .quantity');
    const quantity = quantityEl ? parseInt(quantityEl.textContent) : 1;

    const productElement = document.getElementById(productId);
    if (!productElement) {
        console.error('Product not found:', productId);
        return;
    }

    // Get product details
    const name = productElement.querySelector('h3').innerText;
    const parsed = parsePricesFromProductCard(productElement);
    if (!parsed) {
        console.error('Price element not found');
        return;
    }
    const { storePrices, mainPrice } = parsed;
    const image = productElement.querySelector('.product-image').src;
    const category = productElement.dataset.category || 'Andre varer';
    const unitMeasure = productElement.dataset.remaWeight || '';
    const kgPrice = productElement.dataset.remaKgPrice || '';
    const store = productElement.dataset.store || 'Rema 1000';
    const storeMultiDeals = collectStoreMultiDeals(productElement);

    // Check if product already exists in cart
    const existingItem = cart.find(item => item.id === productId);

    if (existingItem) {
        existingItem.quantity += quantity;
    } else {
        cart.push({
            id: productId,
            name: name,
            store: store,
            price: mainPrice,
            storePrices: storePrices,
            storeMultiDeals: storeMultiDeals,
            image: image,
            category: category,
            unitMeasure: unitMeasure,
            kgPrice: kgPrice,
            // multiDeal manglede her, men saettes af addToCart. Uden det viste
            // kurven hverken multikoebs-maerket eller den rigtige bundtpris for
            // varer lagt i fra produkt-overlayet.
            multiDeal: productElement.dataset.multideal || '',
            quantity: quantity
        });
    }

    // Show animation on the product card and button
    productElement.classList.add('added-to-cart');
    addToCartBtn.classList.add('clicked');
    addToCartBtn.textContent = 'Tilføjet';

    // Save cart and animate overlay closing
    saveCart();

    // Samme populaeritets-registrering som addToCart (debounced, se
    // queueCartEvent). Manglede her, saa varer lagt i fra overlayet aldrig
    // talte med i "Populaere varer" paa forsiden.
    queueCartEvent('add', String(productId).replace(/^product/, ''), quantity);

    trackEvent('add_to_cart', {
        product_id: String(productId || '').replace(/^product/, ''),
        category: category,
        store: store,
        quantity: quantity,
        source: 'overlay'
    });

    // Wait for button animation and then close overlay
    setTimeout(() => {
        overlay.classList.add('closing');
        setTimeout(() => {
            overlay.classList.remove('closing');
            overlay.style.display = 'none';
            document.body.classList.remove('no-scroll');
            // Reset button state
            addToCartBtn.classList.remove('clicked');
            addToCartBtn.textContent = 'Tilføj til kurv';
        }, 500); // Increased fade-out time
    }, 1000); // Increased wait time after button click

    // Remove product animation
    setTimeout(() => {
        productElement.classList.remove('added-to-cart');
    }, 300);
}

// Prishistorik: fast farve pr. butik (CVD-valideret rækkefølge, Rema = grøn).
// Butikker uden fast slot får første ledige farve i den viste graf.
const OVERLAY_COMP_MAX_STORES = 5;
const HISTORY_STORE_ORDER = ['rema', 'bilka', 'foetex', 'netto', 'sb', 'kvickly', 'brugsen', 'lidl', 'discount365', 'loevbjerg', 'abclavpris', 'meny', 'spar', 'mk'];
const HISTORY_PALETTE = ['#1baf7a', '#2a78d6', '#eda100', '#008300', '#4a3aa7', '#e34948', '#e87ba4', '#eb6834'];
const HISTORY_FALLBACK_COLOR = '#898781';
const HISTORY_FONT = "'Plus Jakarta Sans', 'Inter', system-ui, sans-serif";
const _HISTORY_KEY_LABELS = {
    rema: 'Rema 1000', bilka: 'Bilka', foetex: 'Føtex', netto: 'Netto',
    mk: 'Min Købmand', meny: 'Meny', spar: 'Spar', sb: 'SuperBrugsen',
    brugsen: 'Brugsen', kvickly: 'Kvickly', discount365: '365 Discount',
    lidl: 'Lidl', loevbjerg: 'Løvbjerg', abclavpris: 'ABC Lavpris'
};
const _priceHistoryCache = {};

function _storeLabelToKey(label) {
    const hit = (ALL_STORES || []).find(s => s.label === label);
    if (hit) return hit.key;
    return Object.keys(_HISTORY_KEY_LABELS).find(k => _HISTORY_KEY_LABELS[k] === label) || '';
}

function _storeKeyToLabel(key) {
    const hit = (ALL_STORES || []).find(s => s.key === key);
    return hit ? hit.label : (_HISTORY_KEY_LABELS[key] || key);
}

const _nutritionCache = {};
const _NUTRITION_SOURCE_LABELS = { rema: 'Rema 1000', salling: 'butikkens varedeklaration', off: 'Open Food Facts' };
// Skifter hver gang et overlay åbnes for et nyt produkt. En sen respons for
// et tidligere produkt tjekker sit eget token mod dette og dropper sig selv
// i stedet for at overskrive skærmen med et forkert produkts data.
let _nutritionRequestToken = 0;

function renderNutritionSection(productId) {
    const section = document.getElementById('overlay-nutrition-section');
    const table = document.getElementById('nutrition-table');
    const ingredientsEl = document.getElementById('nutrition-ingredients');
    const emptyEl = document.getElementById('nutrition-empty');
    const sourceEl = document.getElementById('nutrition-source');
    const perBadge = document.getElementById('nutrition-per-badge');
    if (!section) return;

    const requestToken = ++_nutritionRequestToken;
    const pid = productId.replace('product', '');
    if (!_nutritionCache[pid]) {
        _nutritionCache[pid] = fetch(`/api/nutrition/${pid}`)
            .then(r => r.json())
            .catch(() => {
                // Slet cache-posten ved fejl, så et senere åbn af samme
                // produkt prøver forfra i stedet for at genbruge det samme
                // fejlede resultat resten af sidens levetid - uden dette
                // låste én forbigående netværksfejl "ingen næringsdata" fast.
                delete _nutritionCache[pid];
                return { nutrition: null };
            });
    }

    // Ryd forrige produkts indhold med det samme, så intet gammelt blinker frem
    table.innerHTML = '';
    ingredientsEl.style.display = 'none';
    emptyEl.style.display = 'none';
    sourceEl.textContent = '';
    perBadge.style.display = 'none';
    section.style.display = 'block';

    _nutritionCache[pid].then(data => {
        if (requestToken !== _nutritionRequestToken) return; // overlay har skiftet til et andet produkt
        const nutrition = data && data.nutrition;
        if (!nutrition || !Array.isArray(nutrition.rows) || !nutrition.rows.length) {
            table.innerHTML = '';
            ingredientsEl.style.display = 'none';
            sourceEl.textContent = '';
            perBadge.style.display = 'none';
            emptyEl.style.display = 'block';
            return;
        }

        emptyEl.style.display = 'none';
        perBadge.style.display = 'inline-block';
        perBadge.textContent = 'pr. ' + (nutrition.per || '100 g');
        // Escapes: naeringsdata kommer fra tredjeparter (bl.a. Open Food Facts,
        // som alle kan redigere), saa label/value/ingredienser er ikke betroet
        // markup - uden escapeHtml kan en OFF-redigering injicere HTML her.
        table.innerHTML = nutrition.rows.map(row => {
            const isSub = /^(heraf|- heraf)/i.test(row.label || '');
            return `<tr class="${isSub ? 'nutrition-row-sub' : ''}"><td>${escapeHtml(row.label)}</td><td>${escapeHtml(row.value)}</td></tr>`;
        }).join('');

        if (nutrition.ingredients) {
            ingredientsEl.innerHTML = `<strong>Ingredienser:</strong> ${sanitizeNutritionHtml(nutrition.ingredients)}`;
            ingredientsEl.style.display = 'block';
        } else {
            ingredientsEl.style.display = 'none';
        }

        sourceEl.textContent = 'Kilde: ' + (_NUTRITION_SOURCE_LABELS[nutrition.source] || nutrition.source || 'ukendt');
    });
}

// Se _nutritionRequestToken - samme mønster, separat token fordi de to
// overlays kan blive kaldt uafhængigt af hinanden.
let _priceHistoryRequestToken = 0;

function renderPriceHistoryChart(productId, currentPrice, isSale, storeLabel, allowedStoreLabels, storePricesByLabel) {
    const requestToken = ++_priceHistoryRequestToken;
    loadChartJs().catch(err => {
        if (requestToken !== _priceHistoryRequestToken) throw err;
        // Chart.js hentes fra CDN. Uden denne gren stod placeholderteksten og
        // ventede i det uendelige - og foer i tiden stod der en konkret,
        // opdigtet pris dér ("stabilt paa 12,00 kr."), som brugeren saa
        // troede paa for enhver vare.
        console.error('Kunne ikke hente graf-biblioteket:', err);
        const summaryEl = document.getElementById('history-summary');
        if (summaryEl) summaryEl.textContent = 'Prishistorikken kunne ikke indlæses.';
        const badge = document.getElementById('price-insight-badge');
        if (badge) badge.textContent = 'Prishistorik';
        throw err;
    }).then(() => {
    if (requestToken !== _priceHistoryRequestToken) return; // overlay har skiftet til et andet produkt
    const ctx = document.getElementById('priceHistoryChart').getContext('2d');
    const insightBadge = document.getElementById('price-insight-badge');
    const summaryEl = document.getElementById('history-summary');

    // Destroy previous chart if exists
    if (priceHistoryChart) {
        priceHistoryChart.destroy();
        priceHistoryChart = null;
    }

    const pid = productId.replace('product', '');
    if (!_priceHistoryCache[pid]) {
        _priceHistoryCache[pid] = fetch(`/api/price-history/${pid}`)
            .then(r => r.json())
            .catch(() => {
                // Se kommentaren ved _nutritionCache ovenfor - samme fejl,
                // samme fix: ikke lås en tom graf fast pga. én fejlet request.
                delete _priceHistoryCache[pid];
                return {};
            });
    }

    _priceHistoryCache[pid].then(data => {
        if (requestToken !== _priceHistoryRequestToken) return; // overlay har skiftet til et andet produkt
        const todayStr = new Date().toISOString().split('T')[0];
        const kr = v => v.toFixed(2).replace('.', ',') + ' kr';
        const curPrice = parseFloat(currentPrice) || 0;

        // Kopiér serierne, så patch af dagens pris ikke muterer cachen
        const byStore = {};
        Object.entries((data && data.history_by_store) || {}).forEach(([key, rows]) => {
            if (Array.isArray(rows) && rows.length) byStore[key] = rows.slice();
        });

        if (Array.isArray(allowedStoreLabels) && allowedStoreLabels.length) {
            const allowedKeys = new Set(
                allowedStoreLabels.map(l => _storeLabelToKey(l)).filter(Boolean)
            );
            Object.keys(byStore).forEach(k => {
                if (!allowedKeys.has(k)) delete byStore[k];
            });
        }

        // Butikker i sammenligning uden gemt historik endnu: start med dagens pris
        if (Array.isArray(allowedStoreLabels) && storePricesByLabel) {
            allowedStoreLabels.forEach(label => {
                const key = _storeLabelToKey(label);
                if (!key || (byStore[key] && byStore[key].length)) return;
                const entry = storePricesByLabel[label];
                const p = parseFloat(entry && entry.price != null ? entry.price : entry) || 0;
                if (p > 0) byStore[key] = [{ date: todayStr, price: p }];
            });
        }

        let selectedKey = _storeLabelToKey(storeLabel || '');
        if (!selectedKey || (!byStore[selectedKey] && !curPrice)) {
            selectedKey = byStore.rema ? 'rema' : (Object.keys(byStore)[0] || 'rema');
        }

        const patchTodayPrice = (key, price) => {
            if (!key || price <= 0) return;
            const series = (byStore[key] || []).slice();
            const last = series[series.length - 1];
            if (last && last.date === todayStr) {
                series[series.length - 1] = { date: todayStr, price: price };
            } else {
                series.push({ date: todayStr, price: price });
            }
            byStore[key] = series;
        };

        // Dagens pris fra overlay vinder over nattens snapshot for alle viste butikker
        if (Array.isArray(allowedStoreLabels) && storePricesByLabel) {
            allowedStoreLabels.forEach(label => {
                const key = _storeLabelToKey(label);
                const entry = storePricesByLabel[label];
                const p = parseFloat(entry && entry.price != null ? entry.price : entry) || 0;
                patchTodayPrice(key, p);
            });
        } else if (curPrice > 0) {
            patchTodayPrice(selectedKey, curPrice);
        }

        const storeKeys = Object.keys(byStore).sort((a, b) => {
            const ia = HISTORY_STORE_ORDER.indexOf(a), ib = HISTORY_STORE_ORDER.indexOf(b);
            return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
        });
        // Kun ét datapunkt: tegn en flad linje 30 dage tilbage (per butik)
        storeKeys.forEach(k => {
            if (byStore[k].length === 1) {
                const past = new Date();
                past.setDate(past.getDate() - 30);
                byStore[k].unshift({
                    date: past.toISOString().split('T')[0],
                    price: byStore[k][0].price
                });
            }
        });

        const dateSet = new Set();
        storeKeys.forEach(k => byStore[k].forEach(r => r && r.date && dateSet.add(r.date)));
        const dates = Array.from(dateSet).sort();
        const labels = dates.map(d => { const [, m, dd] = d.split('-'); return `${dd}/${m}`; });

        // Fast farve for butikker med eget slot; resten får første ledige
        const colorFor = {};
        const used = new Set();
        storeKeys.forEach(k => {
            const idx = HISTORY_STORE_ORDER.indexOf(k);
            if (idx >= 0 && idx < HISTORY_PALETTE.length) {
                colorFor[k] = HISTORY_PALETTE[idx];
                used.add(idx);
            }
        });
        let nextSlot = 0;
        storeKeys.forEach(k => {
            if (colorFor[k]) return;
            while (nextSlot < HISTORY_PALETTE.length && used.has(nextSlot)) nextSlot++;
            colorFor[k] = nextSlot < HISTORY_PALETTE.length ? HISTORY_PALETTE[nextSlot++] : HISTORY_FALLBACK_COLOR;
        });

        const hexToRgba = (hex, a) => {
            const n = parseInt(hex.slice(1), 16);
            return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
        };

        const datasets = storeKeys.map(key => {
            const priceByDate = {};
            byStore[key].forEach(r => { priceByDate[r.date] = r.price; });
            const selected = key === selectedKey;
            const color = colorFor[key];
            return {
                label: _storeKeyToLabel(key),
                data: dates.map(d => priceByDate[d] !== undefined ? priceByDate[d] : null),
                borderColor: color,
                backgroundColor: selected ? hexToRgba(color, 0.08) : 'transparent',
                borderWidth: selected ? 3 : 2,
                fill: selected,
                tension: 0.3,
                spanGaps: true,
                pointRadius: (selected || byStore[key].length === 1) ? 3 : 0,
                pointHoverRadius: 6,
                pointBackgroundColor: color,
                order: selected ? 0 : 1
            };
        });

        // Indsigt og opsummering ud fra den valgte butiks serie
        const selPrices = (byStore[selectedKey] || [])
            .map(r => r.price).filter(v => typeof v === 'number' && v > 0);
        const cur = selPrices.length ? selPrices[selPrices.length - 1] : curPrice;
        const hist = selPrices.slice(0, -1);
        const avgPrice = hist.length ? hist.reduce((a, b) => a + b, 0) / hist.length : cur;

        let insightText = 'Stabil pris';
        let insightClass = '';
        if (cur < avgPrice * 0.9) {
            insightText = 'Godt tilbud!';
            insightClass = 'great-deal';
        } else if (isSale && cur >= avgPrice * 0.98 && selPrices.length > 2) {
            insightText = 'Lille besparelse';
            insightClass = 'fake-deal';
        }
        insightBadge.textContent = insightText;
        insightBadge.className = 'price-insight-badge ' + insightClass;

        summaryEl.textContent = selPrices.length > 2
            ? `Prisen i ${_storeKeyToLabel(selectedKey)} har varieret mellem ${kr(Math.min(...selPrices))}. og ${kr(Math.max(...selPrices))}. de sidste 30 dage.`
            : `Vi holder øje med prisen for dig, så du ikke behøver.`;

        priceHistoryChart = new Chart(ctx, {
            type: 'line',
            data: { labels: labels, datasets: datasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: 'index', intersect: false },
                plugins: {
                    legend: {
                        display: datasets.length > 1,
                        position: 'bottom',
                        labels: {
                            usePointStyle: true,
                            boxWidth: 8,
                            boxHeight: 8,
                            padding: 12,
                            color: '#52514e',
                            font: { family: HISTORY_FONT, size: 11 }
                        }
                    },
                    tooltip: {
                        backgroundColor: '#111827',
                        padding: 10,
                        usePointStyle: true,
                        callbacks: {
                            label: (context) => context.parsed.y == null
                                ? undefined
                                : ` ${context.dataset.label}: ${kr(context.parsed.y)}`
                        }
                    }
                },
                scales: {
                    y: {
                        beginAtZero: false,
                        grid: { color: 'rgba(0,0,0,0.05)' },
                        ticks: {
                            maxTicksLimit: 6,
                            color: '#898781',
                            font: { family: HISTORY_FONT, size: 11 },
                            callback: (value) => kr(value)
                        }
                    },
                    x: {
                        grid: { display: false },
                        ticks: {
                            maxRotation: 0,
                            autoSkip: true,
                            maxTicksLimit: 6,
                            color: '#898781',
                            font: { family: HISTORY_FONT, size: 11 }
                        }
                    }
                }
            }
        });
    });
    }); // end loadChartJs().then
}

// Function to open product information overlay
function openOverlay(productElementOrId) {
    let productElement;
    if (typeof productElementOrId === 'string') {
        productElement = document.getElementById(productElementOrId);
    } else {
        productElement = productElementOrId;
    }

    if (!productElement) {
        console.error('Product not found:', productElementOrId);
        return;
    }

    const productId = productElement.id;

    // Get product data safely
    const imageSrc = productElement.dataset.mainImage || '';
    const titleEl = productElement.querySelector('h3');
    const title = titleEl ? titleEl.innerText : 'Ukendt vare';
    
    const descNode = productElement.querySelector('.product-description');
    const description = descNode ? descNode.innerText : '';
    
    const brandNode = productElement.querySelector('.brand');
    const brand = brandNode ? brandNode.innerText : '';

    // Check if product is on sale
    var salePriceElement = productElement.querySelector('.price.sale');
    var originalPriceElement = productElement.querySelector('.price.original');
    var regularPriceElement = productElement.querySelector('.price:not(.sale):not(.original)');
    var saleEndDateElement = productElement.querySelector('.sale-end-date');

    // Create price HTML based on whether the product is on sale
    var priceHTML = '';
    if (salePriceElement && originalPriceElement) {
        // Product is on sale - show both prices
        priceHTML = `<p class="price original">${originalPriceElement.innerText}</p>
                     <p class="price sale">${salePriceElement.innerText}</p>`;
    } else if (regularPriceElement) {
        // Regular price only
        priceHTML = `<p class="price">${regularPriceElement.innerText}</p>`;
    }

    // Insert data into overlay safely
    const overlayImg = document.getElementById('overlay-image');
    // alt sættes SAMMEN med src: markup'en har alt="" (billedet er tomt indtil
    // et produkt åbnes), så uden denne linje er sidens største billede
    // permanent ubeskrevet for skærmlæsere.
    if (overlayImg) {
        overlayImg.src = imageSrc;
        overlayImg.alt = title;
    }
    
    const overlayTitle = document.getElementById('overlay-title');
    if (overlayTitle) overlayTitle.innerText = title;
    
    const overlayDesc = document.getElementById('overlay-description');
    if (overlayDesc) overlayDesc.innerText = description;
    
    const overlayBrand = document.getElementById('overlay-brand-name');
    if (overlayBrand) overlayBrand.innerText = brand.replace('Mærke: ', '');

    // Store-only message and comparison view
    var storeOnlyMsg = document.getElementById('overlay-store-only-msg');
    var compDiv = document.getElementById('overlay-comparison');
    var genericAddBtn = document.getElementById('generic-add-to-cart-btn');

    var hasMatch = productElement.dataset.hasMatch === 'true';
    var store = productElement.dataset.store || 'Rema 1000';

    // Safe defaults - only overwritten inside the else branch below
    var cardStore = store;
    var validCards = [];
    var cards = [];
    var visibleCards = [];
    var mainCardPrice = 0;
    var rPrice = 0, bPrice = 0, mPrice = 0, mePrice = 0, sPrice = 0;
    var sbPrice = 0, brugsenPrice = 0, kvicklyPrice = 0, discount365Price = 0, lidlPrice = 0;
    var loevbjergPrice = 0, abclavprisPrice = 0;
    var nettoPrice = 0, foetexPrice = 0;
    // Compute a baseline mainCardPrice for single-store products
    var _basePriceEl = productElement.querySelector('.price.sale') || productElement.querySelector('.price:not(.sale):not(.original)');
    if (_basePriceEl) {
        mainCardPrice = parseFloat(_basePriceEl.innerText.replace(/[^\d,.]/g, '').replace(',', '.')) || 0;
    }

    if (!hasMatch) {
        if (storeOnlyMsg) {
            var storeName = store;
            storeOnlyMsg.textContent = 'Vi har endnu ikke fundet denne vare hos andre butikker - den er foreløbigt kun tilgængelig hos ' + storeName + '.';
            storeOnlyMsg.style.display = 'block';
        }
        if (compDiv) compDiv.style.display = 'none';
        if (genericAddBtn) genericAddBtn.textContent = 'Tilføj til kurv';
    } else {
        if (storeOnlyMsg) storeOnlyMsg.style.display = 'none';

        if (compDiv) {
            // Read the main price shown on the card - it belongs to the card's own store
            var mainPriceEl = productElement.querySelector('.price.sale') || productElement.querySelector('.price:not(.sale):not(.original)');
            var mainPriceText = mainPriceEl ? mainPriceEl.innerText : '0';
            var mainCardPrice = parseFloat(mainPriceText.replace(/[^\d,.]/g, '').replace(',', '.')) || 0;
            var cardStore = productElement.dataset.store || 'Rema 1000';

            var remaKgPrice = productElement.dataset.remaKgPrice || '';
            var bilkaRaw = productElement.dataset.bilkaPrice;
            var bilkaKgPrice = productElement.dataset.bilkaKgPrice || '';
            var foetexRaw = productElement.dataset.foetexPrice;
            var foetexKgPrice = productElement.dataset.foetexKgPrice || '';
            var nettoRaw = productElement.dataset.nettoPrice;
            var nettoKgPrice = productElement.dataset.nettoKgPrice || '';
            var mkRaw = productElement.dataset.mkPrice;
            var mkKgPrice = productElement.dataset.mkKgPrice || '';
            var menyRaw = productElement.dataset.menyPrice;
            var menyKgPrice = productElement.dataset.menyKgPrice || '';
            var sparRaw = productElement.dataset.sparPrice;
            var sparKgPrice = productElement.dataset.sparKgPrice || '';

            var bilkaIsSale = productElement.dataset.bilkaIsSale === 'true';
            var foetexIsSale = productElement.dataset.foetexIsSale === 'true';
            var nettoIsSale = productElement.dataset.nettoIsSale === 'true';
            var mkIsSale = productElement.dataset.mkIsSale === 'true';
            var menyIsSale = productElement.dataset.menyIsSale === 'true';
            var sparIsSale = productElement.dataset.sparIsSale === 'true';
            var remaRaw = productElement.dataset.remaPrice;
            var remaIsSale = (productElement.dataset.remaIsSale === 'true') || (cardStore === 'Rema 1000' && productElement.querySelector('.price.sale') !== null);

            // Assign the card's own price to the right store column
            var rPrice = 0, bPrice = 0, mPrice = 0, mePrice = 0, sPrice = 0;
            if (cardStore === 'Bilka') {
                bPrice = mainCardPrice;
            } else if (cardStore === 'Føtex') {
                if (foetexPrice === 0) foetexPrice = mainCardPrice;
            } else if (cardStore === 'Netto') {
                if (nettoPrice === 0) nettoPrice = mainCardPrice;
            } else if (cardStore === 'Min Købmand' || cardStore === 'Min Koebmand') {
                mPrice = mainCardPrice;
            } else if (cardStore === 'Meny') {
                mePrice = mainCardPrice;
            } else if (cardStore === 'Spar') {
                sPrice = mainCardPrice;
            } else if (cardStore === 'SuperBrugsen') {
                if (sbPrice === 0) sbPrice = mainCardPrice;
            } else if (cardStore === 'Brugsen') {
                if (brugsenPrice === 0) brugsenPrice = mainCardPrice;
            } else if (cardStore === 'Kvickly') {
                if (kvicklyPrice === 0) kvicklyPrice = mainCardPrice;
            } else if (cardStore === '365 Discount') {
                if (discount365Price === 0) discount365Price = mainCardPrice;
            } else if (cardStore === 'Lidl') {
                if (lidlPrice === 0) lidlPrice = mainCardPrice;
            } else if (cardStore === 'Løvbjerg') {
                if (loevbjergPrice === 0) loevbjergPrice = mainCardPrice;
            } else if (cardStore === 'ABC Lavpris') {
                if (abclavprisPrice === 0) abclavprisPrice = mainCardPrice;
            } else {
                rPrice = mainCardPrice; // Rema 1000 or default
            }

            // Cross-store match prices override only if not already set from the card
            if (remaRaw && remaRaw !== '') {
                var rp = parseFloat(remaRaw.replace(',', '.'));
                if (!isNaN(rp) && rp > 0) rPrice = rp;
            }
            if (bilkaRaw && bilkaRaw !== '') {
                var bp = parseFloat(bilkaRaw.replace(',', '.'));
                if (!isNaN(bp) && bp > 0) bPrice = bp;
            }
            var nettoPrice = 0;
            if (nettoRaw && nettoRaw !== '') {
                var np = parseFloat(nettoRaw.replace(',', '.'));
                if (!isNaN(np) && np > 0) nettoPrice = np;
            }
            if (cardStore === 'Netto' && nettoPrice === 0) nettoPrice = mainCardPrice;
            if (mkRaw && mkRaw !== '') {
                var mp = parseFloat(mkRaw.replace(',', '.'));
                if (!isNaN(mp) && mp > 0) mPrice = mp;
            }
            if (menyRaw && menyRaw !== '') {
                var mep = parseFloat(menyRaw.replace(',', '.'));
                if (!isNaN(mep) && mep > 0) mePrice = mep;
            }
            if (sparRaw && sparRaw !== '') {
                var sp = parseFloat(sparRaw.replace(',', '.'));
                if (!isNaN(sp) && sp > 0) sPrice = sp;
            }

            var rKgVal = parseFloat(remaKgPrice);
            document.getElementById('comp-rema-kg-price').textContent = (!isNaN(rKgVal) && rKgVal > 0) ? 'Pris pr. kg: ' + rKgVal.toFixed(2) + ' kr' : '';

            var bKgVal = parseFloat(bilkaKgPrice);
            document.getElementById('comp-bilka-kg-price').textContent = (!isNaN(bKgVal) && bKgVal > 0) ? 'Pris pr. kg: ' + bKgVal.toFixed(2) + ' kr' : '';

            var foetexPrice = 0;
            if (foetexRaw && foetexRaw !== '') {
                var fp = parseFloat(foetexRaw.replace(',', '.'));
                if (!isNaN(fp) && fp > 0) foetexPrice = fp;
            }
            if (cardStore === 'Føtex' && foetexPrice === 0) foetexPrice = mainCardPrice;
            var fKgVal = parseFloat(foetexKgPrice);
            document.getElementById('comp-foetex-kg-price').textContent = (!isNaN(fKgVal) && fKgVal > 0) ? 'Pris pr. kg: ' + fKgVal.toFixed(2) + ' kr' : '';

            var nKgVal = parseFloat(nettoKgPrice);
            document.getElementById('comp-netto-kg-price').textContent = (!isNaN(nKgVal) && nKgVal > 0) ? 'Pris pr. kg: ' + nKgVal.toFixed(2) + ' kr' : '';

            var mKgVal = parseFloat(mkKgPrice);
            document.getElementById('comp-mk-kg-price').textContent = (!isNaN(mKgVal) && mKgVal > 0) ? 'Pris pr. kg: ' + mKgVal.toFixed(2) + ' kr' : '';

            var meKgVal = parseFloat(menyKgPrice);
            document.getElementById('comp-meny-kg-price').textContent = (!isNaN(meKgVal) && meKgVal > 0) ? 'Pris pr. kg: ' + meKgVal.toFixed(2) + ' kr' : '';

            var sKgVal = parseFloat(sparKgPrice);
            document.getElementById('comp-spar-kg-price').textContent = (!isNaN(sKgVal) && sKgVal > 0) ? 'Pris pr. kg: ' + sKgVal.toFixed(2) + ' kr' : '';

            // Multi-deal badges (e.g. "Mix 2 for 36.-")
            var multiDeals = {
                'comp-rema-multideal':        productElement.dataset.remaMultideal        || '',
                'comp-bilka-multideal':       productElement.dataset.bilkaMultideal       || '',
                'comp-foetex-multideal':      productElement.dataset.foetexMultideal      || '',
                'comp-netto-multideal':       productElement.dataset.nettoMultideal       || '',
                'comp-mk-multideal':          productElement.dataset.mkMultideal          || '',
                'comp-meny-multideal':        productElement.dataset.menyMultideal        || '',
                'comp-spar-multideal':        productElement.dataset.sparMultideal        || '',
                'comp-sb-multideal':          productElement.dataset.sbMultideal          || '',
                'comp-brugsen-multideal':     productElement.dataset.brugsenMultideal     || '',
                'comp-kvickly-multideal':     productElement.dataset.kvicklyMultideal     || '',
                'comp-discount365-multideal': productElement.dataset.discount365Multideal || '',
                'comp-lidl-multideal':        productElement.dataset.lidlMultideal        || '',
                'comp-loevbjerg-multideal':   productElement.dataset.loevbjergMultideal   || '',
                'comp-abclavpris-multideal':  productElement.dataset.abclavprisMultideal  || '',
            };
            Object.entries(multiDeals).forEach(([id, text]) => {
                var el = document.getElementById(id);
                if (el) el.textContent = text;
            });

            var sbRaw = productElement.dataset.sbPrice;
            var sbKgPrice = productElement.dataset.sbKgPrice || '';
            var sbIsSale = productElement.dataset.sbIsSale === 'true';
            var sbPrice = 0;
            if (sbRaw && sbRaw !== '') {
                var sbP = parseFloat(sbRaw.replace(',', '.'));
                if (!isNaN(sbP) && sbP > 0) sbPrice = sbP;
            }
            if (cardStore === 'SuperBrugsen' && sbPrice === 0) sbPrice = mainCardPrice;

            var sbKgVal = parseFloat(sbKgPrice);
            document.getElementById('comp-sb-kg-price').textContent = (!isNaN(sbKgVal) && sbKgVal > 0) ? 'Pris pr. kg: ' + sbKgVal.toFixed(2) + ' kr' : '';

            var brugsenRaw = productElement.dataset.brugsenPrice;
            var brugsenKgPrice = productElement.dataset.brugsenKgPrice || '';
            var brugsenIsSale = productElement.dataset.brugsenIsSale === 'true';
            var brugsenPrice = 0;
            if (brugsenRaw && brugsenRaw !== '') {
                var brugsenP = parseFloat(brugsenRaw.replace(',', '.'));
                if (!isNaN(brugsenP) && brugsenP > 0) brugsenPrice = brugsenP;
            }
            if (cardStore === 'Brugsen' && brugsenPrice === 0) brugsenPrice = mainCardPrice;

            var brugsenKgVal = parseFloat(brugsenKgPrice);
            document.getElementById('comp-brugsen-kg-price').textContent = (!isNaN(brugsenKgVal) && brugsenKgVal > 0) ? 'Pris pr. kg: ' + brugsenKgVal.toFixed(2) + ' kr' : '';

            var kvicklyRaw = productElement.dataset.kvicklyPrice;
            var kvicklyKgPrice = productElement.dataset.kvicklyKgPrice || '';
            var kvicklyIsSale = productElement.dataset.kvicklyIsSale === 'true';
            var kvicklyPrice = 0;
            if (kvicklyRaw && kvicklyRaw !== '') {
                var kvP = parseFloat(kvicklyRaw.replace(',', '.'));
                if (!isNaN(kvP) && kvP > 0) kvicklyPrice = kvP;
            }
            if (cardStore === 'Kvickly' && kvicklyPrice === 0) kvicklyPrice = mainCardPrice;

            var kvKgVal = parseFloat(kvicklyKgPrice);
            document.getElementById('comp-kvickly-kg-price').textContent = (!isNaN(kvKgVal) && kvKgVal > 0) ? 'Pris pr. kg: ' + kvKgVal.toFixed(2) + ' kr' : '';

            var discount365Raw = productElement.dataset.discount365Price;
            var discount365KgPrice = productElement.dataset.discount365KgPrice || '';
            var discount365IsSale = productElement.dataset.discount365IsSale === 'true';
            var discount365Price = 0;
            if (discount365Raw && discount365Raw !== '') {
                var d365P = parseFloat(discount365Raw.replace(',', '.'));
                if (!isNaN(d365P) && d365P > 0) discount365Price = d365P;
            }
            if (cardStore === '365 Discount' && discount365Price === 0) discount365Price = mainCardPrice;

            var d365KgVal = parseFloat(discount365KgPrice);
            document.getElementById('comp-discount365-kg-price').textContent = (!isNaN(d365KgVal) && d365KgVal > 0) ? 'Pris pr. kg: ' + d365KgVal.toFixed(2) + ' kr' : '';

            var lidlRaw = productElement.dataset.lidlPrice;
            var lidlKgPrice = productElement.dataset.lidlKgPrice || '';
            var lidlIsSale = productElement.dataset.lidlIsSale === 'true';
            lidlPrice = 0;
            if (lidlRaw && lidlRaw !== '') {
                var lidlP = parseFloat(lidlRaw.replace(',', '.'));
                if (!isNaN(lidlP) && lidlP > 0) lidlPrice = lidlP;
            }
            if (cardStore === 'Lidl' && lidlPrice === 0) lidlPrice = mainCardPrice;

            var lidlKgVal = parseFloat(lidlKgPrice);
            document.getElementById('comp-lidl-kg-price').textContent = (!isNaN(lidlKgVal) && lidlKgVal > 0) ? 'Pris pr. kg: ' + lidlKgVal.toFixed(2) + ' kr' : '';

            var loevbjergRaw = productElement.dataset.loevbjergPrice;
            var loevbjergKgPrice = productElement.dataset.loevbjergKgPrice || '';
            var loevbjergIsSale = productElement.dataset.loevbjergIsSale === 'true';
            loevbjergPrice = 0;
            if (loevbjergRaw && loevbjergRaw !== '') {
                var loevP = parseFloat(loevbjergRaw.replace(',', '.'));
                if (!isNaN(loevP) && loevP > 0) loevbjergPrice = loevP;
            }
            if (cardStore === 'Løvbjerg' && loevbjergPrice === 0) loevbjergPrice = mainCardPrice;

            var loevKgVal = parseFloat(loevbjergKgPrice);
            document.getElementById('comp-loevbjerg-kg-price').textContent = (!isNaN(loevKgVal) && loevKgVal > 0) ? 'Pris pr. kg: ' + loevKgVal.toFixed(2) + ' kr' : '';

            var abclavprisRaw = productElement.dataset.abclavprisPrice;
            var abclavprisKgPrice = productElement.dataset.abclavprisKgPrice || '';
            var abclavprisIsSale = productElement.dataset.abclavprisIsSale === 'true';
            abclavprisPrice = 0;
            if (abclavprisRaw && abclavprisRaw !== '') {
                var abcP = parseFloat(abclavprisRaw.replace(',', '.'));
                if (!isNaN(abcP) && abcP > 0) abclavprisPrice = abcP;
            }
            if (cardStore === 'ABC Lavpris' && abclavprisPrice === 0) abclavprisPrice = mainCardPrice;

            var abcKgVal = parseFloat(abclavprisKgPrice);
            document.getElementById('comp-abclavpris-kg-price').textContent = (!isNaN(abcKgVal) && abcKgVal > 0) ? 'Pris pr. kg: ' + abcKgVal.toFixed(2) + ' kr' : '';

            cards = [
                { id: 'comp-card-rema',        price: rPrice,         badgeId: 'comp-badge-rema',        priceId: 'comp-rema-price',        name: 'Rema 1000',    isSale: remaIsSale },
                { id: 'comp-card-bilka',        price: bPrice,         badgeId: 'comp-badge-bilka',        priceId: 'comp-bilka-price',        name: 'Bilka',        isSale: bilkaIsSale },
                { id: 'comp-card-foetex',       price: foetexPrice,    badgeId: 'comp-badge-foetex',       priceId: 'comp-foetex-price',       name: 'Føtex',        isSale: foetexIsSale },
                { id: 'comp-card-netto',        price: nettoPrice,     badgeId: 'comp-badge-netto',        priceId: 'comp-netto-price',        name: 'Netto',        isSale: nettoIsSale },
                { id: 'comp-card-minkobmand',   price: mPrice,         badgeId: 'comp-badge-minkobmand',   priceId: 'comp-mk-price',           name: 'Min Købmand',  isSale: mkIsSale },
                { id: 'comp-card-meny',         price: mePrice,        badgeId: 'comp-badge-meny',         priceId: 'comp-meny-price',         name: 'Meny',         isSale: menyIsSale },
                { id: 'comp-card-spar',         price: sPrice,         badgeId: 'comp-badge-spar',         priceId: 'comp-spar-price',         name: 'Spar',         isSale: sparIsSale },
                { id: 'comp-card-sb',           price: sbPrice,        badgeId: 'comp-badge-sb',           priceId: 'comp-sb-price',           name: 'SuperBrugsen', isSale: sbIsSale },
                { id: 'comp-card-brugsen',      price: brugsenPrice,   badgeId: 'comp-badge-brugsen',      priceId: 'comp-brugsen-price',      name: 'Brugsen',      isSale: brugsenIsSale },
                { id: 'comp-card-kvickly',      price: kvicklyPrice,   badgeId: 'comp-badge-kvickly',      priceId: 'comp-kvickly-price',      name: 'Kvickly',      isSale: kvicklyIsSale },
                { id: 'comp-card-discount365',  price: discount365Price, badgeId: 'comp-badge-discount365', priceId: 'comp-discount365-price', name: '365 Discount', isSale: discount365IsSale },
                { id: 'comp-card-lidl',         price: lidlPrice,        badgeId: 'comp-badge-lidl',        priceId: 'comp-lidl-price',        name: 'Lidl',         isSale: lidlIsSale },
                { id: 'comp-card-loevbjerg',    price: loevbjergPrice,   badgeId: 'comp-badge-loevbjerg',   priceId: 'comp-loevbjerg-price',   name: 'Løvbjerg',     isSale: loevbjergIsSale },
                { id: 'comp-card-abclavpris',   price: abclavprisPrice,  badgeId: 'comp-badge-abclavpris',  priceId: 'comp-abclavpris-price',  name: 'ABC Lavpris',  isSale: abclavprisIsSale },
            ];

            validCards = cards.filter(c => c.price > 0 && selectedStores.has(c.name));
            validCards.sort((a, b) => a.price - b.price);
            visibleCards = validCards.slice(0, OVERLAY_COMP_MAX_STORES);

            // Kun top 5 billigste butikker i prissammenligning
            cards.forEach(c => {
                const isSelected = selectedStores.has(c.name);
                const isVisible = visibleCards.some(v => v.id === c.id);
                document.getElementById(c.id).style.display = (c.price > 0 && isSelected && isVisible) ? 'flex' : 'none';
            });

            // Get the cheapest store name for the button
            var cheapestStore = visibleCards.length > 0 ? visibleCards[0].name : 'Rema 1000';

            // Apply sorting and highlights
            visibleCards.forEach((c, idx) => {
                var el = document.getElementById(c.id);
                var bEl = document.getElementById(c.badgeId);
                var pEl = document.getElementById(c.priceId);

                el.style.order = idx + 1;

                if (c.isSale) {
                    pEl.innerHTML = `${c.price.toFixed(2)} kr <span class="comp-sale-tag">Tilbud</span>`;
                } else {
                    pEl.textContent = c.price.toFixed(2) + ' kr';
                }

                var isDark = document.body.getAttribute('data-theme') === 'dark';
                if (idx === 0) {
                    // Cheapest
                    el.style.border = '1.5px solid #2a7d4f';
                    pEl.style.color = '#2a7d4f';
                    bEl.textContent = 'Billigst';
                    bEl.style.background = isDark ? '#14532d' : '#e6f4ea';
                    bEl.style.color   = isDark ? '#bbf7d0' : '#1e7e34';
                    bEl.style.display = 'block';
                } else {
                    el.style.border = isDark ? '0.5px solid #374151' : '0.5px solid #dcdcdc';
                    pEl.style.color = isDark ? '#e5e7eb' : '#333';
                    var diff = c.price - visibleCards[0].price;
                    bEl.textContent = '+' + diff.toFixed(2) + ' kr';
                    bEl.style.background = isDark ? '#374151' : '#f1f3f4';
                    bEl.style.color   = isDark ? '#9ca3af' : '#5f6368';
                    bEl.style.display = 'block';
                }
            });

            if (genericAddBtn) genericAddBtn.textContent = 'Tilføj til kurv - ' + cheapestStore;
            compDiv.style.display = 'block';
        }
    }

    document.getElementById('overlay-price-value').innerHTML = priceHTML;

    // Handle sale end date
    var saleEndDateDisplay = document.getElementById('overlay-sale-end-date');
    if (saleEndDateElement) {
        saleEndDateDisplay.innerText = saleEndDateElement.innerText;
        saleEndDateDisplay.style.display = 'block';
    } else {
        saleEndDateDisplay.style.display = 'none';
    }

    // Reset quantity to 1
    const qEl = document.querySelector('#overlay .quantity');
    if (qEl) qEl.textContent = '1';

    // Store current product ID for add to cart functionality
    const piEl = document.querySelector('.product-info');
    if (piEl) piEl.dataset.productId = productId;

    // Billigste pris på tværs af brugerens valgte butikker - bruges som
    // current_price ved oprettelse af en prisalarm (kun til visning/logning,
    // baggrundsjobbet i updater.py genberegner selv den reelle triggerpris).
    if (piEl) piEl.dataset.cheapestPrice = String((validCards.length > 0 ? validCards[0].price : mainCardPrice) || 0);

    // Render Price History Chart
    const currentPriceVal = parseFloat(mainCardPrice) || 0;

    // Store prices for the chart logic
    const storePrices = {
        'Rema 1000':    { price: rPrice,          isSale: remaIsSale    || false },
        'Bilka':        { price: bPrice,          isSale: bilkaIsSale   || false },
        'Føtex':        { price: foetexPrice,     isSale: foetexIsSale  || false },
        'Netto':        { price: nettoPrice,      isSale: nettoIsSale   || false },
        'Min Købmand':  { price: mPrice,          isSale: mkIsSale      || false },
        'Meny':         { price: mePrice,         isSale: menyIsSale    || false },
        'Spar':         { price: sPrice,          isSale: sparIsSale    || false },
        'SuperBrugsen': { price: sbPrice,         isSale: sbIsSale      || false },
        'Brugsen':      { price: brugsenPrice,    isSale: brugsenIsSale || false },
        'Kvickly':      { price: kvicklyPrice,    isSale: kvicklyIsSale || false },
        '365 Discount': { price: discount365Price, isSale: discount365IsSale || false },
        'Lidl':         { price: lidlPrice,        isSale: lidlIsSale        || false },
        'Løvbjerg':     { price: loevbjergPrice,   isSale: loevbjergIsSale   || false },
        'ABC Lavpris':  { price: abclavprisPrice,  isSale: abclavprisIsSale  || false },
    };

    // Default to cheapest store's history
    const defaultStore = visibleCards.length > 0 ? visibleCards[0].name : cardStore;
    const defaultStoreEntry = storePrices[defaultStore] || { price: 0, isSale: false };
    const defaultPrice = defaultStoreEntry.price || currentPriceVal;
    const defaultSale = defaultStoreEntry.isSale;

    const comparisonStoreLabels = visibleCards.length
        ? visibleCards.map(c => c.name)
        : [cardStore];

    // Historikken ligger under kortets eget produkt-id; butikken vælger blot serien
    renderPriceHistoryChart(productId, defaultPrice, defaultSale, defaultStore, comparisonStoreLabels, storePrices);
    renderNutritionSection(productId);

    // Setup Click Listeners for store cards to switch history
    visibleCards.forEach(c => {
        const cardEl = document.getElementById(c.id);
        if (cardEl) {
            // Remove previous active classes
            cardEl.classList.remove('active-history');

            // Mark the default as active
            if (c.name === defaultStore) {
                cardEl.classList.add('active-history');
            }

            // Add click listener
            cardEl.onclick = () => {
                // Visual update
                document.querySelectorAll('.comp-card').forEach(el => el.classList.remove('active-history'));
                cardEl.classList.add('active-history');

                // Chart update
                renderPriceHistoryChart(productId, c.price, c.isSale, c.name, comparisonStoreLabels, storePrices);

                // Update the main add-to-cart button text
                if (genericAddBtn) genericAddBtn.textContent = 'Tilføj til kurv - ' + c.name;
            };
        }
    });

    // Show overlay
    const overlayEl = document.getElementById('overlay');
    applyOverlayLayout(overlayEl);
    document.body.classList.add('no-scroll');
}

// Function to close product information overlay
function closeOverlay() {
    const overlay = document.getElementById('overlay');
    overlay.style.display = 'none';
    document.body.classList.remove('no-scroll');
}

function handleOverlayClick(event) {
    if (event.target === document.getElementById('overlay')) closeOverlay();
}

// Function to open image zoom overlay
function openImageZoom(src) {
    const zoomOverlay = document.getElementById('image-zoom-overlay');
    const zoomedImg = document.getElementById('zoomed-image');
    if (!zoomOverlay || !zoomedImg) return;

    zoomedImg.src = src;
    // Arv produktnavnet fra billedet der blev klikket, så den forstørrede
    // udgave ikke bare hedder "Forstørret billede" for en skærmlæser.
    const sourceImg = document.getElementById('overlay-image');
    zoomedImg.alt = (sourceImg && sourceImg.alt)
        ? `${sourceImg.alt} - forstørret`
        : 'Forstørret billede';
    zoomOverlay.style.display = 'flex';
    // Force reflow
    zoomOverlay.offsetHeight;
    zoomOverlay.classList.add('active');
}

// Function to close image zoom overlay
function closeImageZoom() {
    const zoomOverlay = document.getElementById('image-zoom-overlay');
    if (!zoomOverlay) return;

    zoomOverlay.classList.remove('active');
    setTimeout(() => {
        zoomOverlay.style.display = 'none';
    }, 300);
}

// Close overlay when clicking outside
document.addEventListener('click', function (event) {
    const productOverlay = document.getElementById('overlay');
    const storeOverlay = document.getElementById('store-comparison-overlay');

    // Handle product overlay
    if (productOverlay.style.display === 'flex' && event.target === productOverlay) {
        closeOverlay();
    }

    // Handle store comparison overlay
    if (storeOverlay && storeOverlay.style.display === 'flex') {
        const content = storeOverlay.querySelector('.sco-modal');
        if (content && !content.contains(event.target)) {
            closeStoreComparison();
        }
    }
});

// Function to reattach event listeners to products
function attachProductEventListeners() {
    document.querySelectorAll('.product:not([data-listeners-attached])').forEach(product => {
        product.dataset.listenersAttached = 'true';
        product.onclick = function () { openOverlay(this); };
        const addToCartBtn = product.querySelector('.corner-box, .add-to-cart-btn');
        if (addToCartBtn) {
            addToCartBtn.onclick = (e) => { e.stopPropagation(); addToCart(e, product); };
        }
    });
}

function paginationJump(input, totalPages) {
    const page = parseInt(input.value);
    if (!isNaN(page) && page >= 1 && page <= totalPages) {
        const inSearchPanel = input.closest('#searchResults');
        if (inSearchPanel) {
            const searchInput = document.getElementById('searchInput');
            // _lastSearchQuery, ikke feltets vaerdi: feltet kan indeholde et nyt,
            // uafsluttet soegeord (man er begyndt at skrive "smoer" efter at have
            // soegt paa "maelk"), og saa hoppede man til side N i den forkerte
            // soegning. Pagineringsklik bruger allerede den rigtige.
            fetchSearchResults(_lastSearchQuery || (searchInput ? searchInput.value.trim() : ''), page, false);
        } else {
            const url = new URL(window.location.href);
            url.searchParams.set('page', page);
            window.location.href = url.toString();
        }
    } else {
        input.focus();
        input.select();
    }
}

function deleteCartItem(index) {
    const cartItem = document.querySelector(`.cart-item[data-index="${index}"]`);
    if (cartItem) cartItem.classList.add('removing');

    // Indekset gaelder kun paa klik-tidspunktet. Slettes to varer inden for
    // fade-animationens 300 ms, har den foerste splice rykket alle
    // efterfoelgende én plads op, og den anden ville ramme naboen. Vi holder
    // derfor fast i selve varen og slaar dens aktuelle plads op igen lige
    // foer den fjernes; er den allerede vaek, goer vi ingenting.
    const target = cart[index];
    if (!target) return;

    setTimeout(() => {
        const current = cart.indexOf(target);
        if (current !== -1) {
            cart.splice(current, 1);
            saveCart();
        }
        updateCartDisplay();
    }, 300);
}

function clearCart() {
    // Kontosletning, gruppe-udmeldelse og listesletning har alle en
    // bekræftelse - den mest destruktive knap i kurven havde ingen. Et
    // fejltryk tømte hele kurven med det samme, og i en delt gruppe
    // (_scheduleSharedPush) forplantede det til ALLE gruppemedlemmers kurve.
    var besked = (typeof _inSharedGroup === 'function' && _inSharedGroup())
        ? 'Tøm kurven? Det rammer hele gruppens fælles kurv og kan ikke fortrydes.'
        : 'Tøm kurven? Det kan ikke fortrydes.';
    if (!window.confirm(besked)) return;
    cart = [];
    saveCart();
    updateCartDisplay();
}


// ===== ADVANCED FILTERING =====
// Filterpanelet findes to gange på hver side (se partials/filters.html):
// scope "search" i det flydende søgepanel og scope "page" på selve siden.
// Hvert panel har sin egen tilstand og opdaterer sin egen produktliste.

function pageFilterPanel() {
    return document.querySelector('.advanced-filters:not([data-filter-scope="search"])');
}

function searchFilterPanel() {
    return document.querySelector('.advanced-filters[data-filter-scope="search"]');
}

function filterField(panel, key) {
    return panel?.querySelector(`[data-filter-key="${key}"]`) || null;
}

// Nulstiller et panels felter til udgangspunktet (uden at hente noget).
function resetFilterPanel(panel) {
    if (!panel) return;
    panel.querySelectorAll('[data-filter-key]').forEach(el => {
        if (el.type === 'checkbox') el.checked = false;
        else if (el.tagName === 'SELECT') el.selectedIndex = 0;
        else el.value = '';
    });
}

// Sidens filtre huskes på tværs af paginering/genindlæsning, men kun så længe
// man bliver på samme sti. Søgepanelets filtre gemmes bevidst ikke - de hører
// til den enkelte søgning og ryddes ved næste.
function clearStoredFilterValues() {
    Object.keys(sessionStorage)
        .filter(key => key.startsWith('filter_'))
        .forEach(key => sessionStorage.removeItem(key));
}

function applyFilters() {
    if (typeof applyAllFilters === 'function') {
        applyAllFilters(false, true);
    }
}

// Advanced Filters Initialization
function initAdvancedFilters() {
    if (initAdvancedFilters._done) return;
    initAdvancedFilters._done = true;

    // Path tracking for reset
    const currentPath = window.location.pathname;
    const lastPath = sessionStorage.getItem('lastFilterPath');

    if (lastPath && lastPath !== currentPath) {
        // Category changed, clear saved filters
        clearStoredFilterValues();
    }
    sessionStorage.setItem('lastFilterPath', currentPath);

    // Load saved filters (kun sidens panel - se clearStoredFilterValues)
    const pagePanel = pageFilterPanel();
    pagePanel?.querySelectorAll('[data-filter-key]').forEach(el => {
        const savedValue = sessionStorage.getItem(`filter_${el.dataset.filterKey}`);
        if (savedValue === null) return;
        if (el.type === 'checkbox') el.checked = savedValue === 'true';
        else el.value = savedValue;
    });

    document.querySelectorAll('.advanced-filters').forEach(panel => {
        const isSearchPanel = panel.dataset.filterScope === 'search';

        panel.querySelectorAll('[data-filter-key]').forEach(el => {
            const onChange = () => {
                if (!isSearchPanel) {
                    const val = el.type === 'checkbox' ? el.checked : el.value;
                    sessionStorage.setItem(`filter_${el.dataset.filterKey}`, val);
                }
                runPanelFilters(panel);
            };
            el.addEventListener('change', onChange);
            if (el.tagName === 'INPUT' && (el.type === 'number' || el.type === 'text')) {
                el.addEventListener('input', onChange);
            }
        });

        panel.querySelector('.filter-reset-btn')?.addEventListener('click', (e) => {
            e.preventDefault();
            resetAdvancedFilters(panel);
        });
    });

    // Run filters on load if we have saved values
    applyAllFilters(true);

    const toggleBtns = document.querySelectorAll('.advanced-filters-toggle');
    toggleBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            const container = btn.nextElementSibling; // The .advanced-filters div
            if (container && container.classList.contains('advanced-filters')) {
                const willOpen = !container.classList.contains('active');
                document.querySelectorAll('.advanced-filters.active').forEach((panel) => {
                    if (panel !== container) panel.classList.remove('active');
                });
                document.querySelectorAll('.advanced-filters-toggle.active').forEach((otherBtn) => {
                    if (otherBtn !== btn) otherBtn.classList.remove('active');
                });
                container.classList.toggle('active', willOpen);
                btn.classList.toggle('active', willOpen);
                setMobileFiltersOpen(willOpen);
            }
        });
    });

    // Close filters when clicking outside
    document.addEventListener('click', (event) => {
        const activeToggles = document.querySelectorAll('.advanced-filters-toggle.active');
        activeToggles.forEach(btn => {
            const container = btn.nextElementSibling;
            const backdrop = document.getElementById('mobile-filters-backdrop');
            if (container &&
                !btn.contains(event.target) &&
                !container.contains(event.target) &&
                !(backdrop && backdrop.contains(event.target))) {
                container.classList.remove('active');
                btn.classList.remove('active');
                setMobileFiltersOpen(false);
            }
        });
    });
}

// Et filterklik i søgepanelet må kun røre søgeresultaterne, og et klik i
// sidens panel kun sidens liste. Panelerne deler ikke længere tilstand, så
// et søgeord i headeren kan ikke længere nulstille filtrene på den
// kategoriside man står på - usynligt bag søgepanelet.
function runPanelFilters(panel) {
    if (panel?.dataset.filterScope === 'search') refreshSearchResults();
    else applyAllFilters();
}

let searchFilterTimeout;
function refreshSearchResults(isImmediate = false) {
    clearTimeout(searchFilterTimeout);
    const run = () => {
        if (_lastSearchQuery) fetchSearchResults(_lastSearchQuery, 1, false);
    };
    if (isImmediate) run();
    else searchFilterTimeout = setTimeout(run, 300);
}

function resetAdvancedFilters(panel = pageFilterPanel()) {
    resetFilterPanel(panel);

    if (panel?.dataset.filterScope === 'search') {
        refreshSearchResults(true);
        return;
    }

    clearStoredFilterValues();

    // Immediate update and reset to page 1
    const url = new URL(window.location.href);
    url.searchParams.delete('page');
    window.history.pushState({}, '', url.toString());

    applyAllFilters(false, true); // false for isInitialLoad, true for immediate
}

// Læser et panels filter-/sorteringsværdier.
function readFilterValues(panel) {
    const value = (key) => filterField(panel, key)?.value || '';
    const checked = (key) => !!filterField(panel, key)?.checked;
    return {
        sort: value('sort') || 'relevance',
        minPrice: value('min_price'),
        maxPrice: value('max_price'),
        sale: checked('sale'),
        organic: checked('organic'),
        lactose: checked('lactose'),
        minWeight: value('min_weight'),
        maxWeight: value('max_weight'),
    };
}

// Skriver filterværdierne ind i et URLSearchParams (og fjerner dem der ikke
// er sat). Deles af sidefiltreringen og søgepanelet, så /search filtrerer
// efter præcis samme parametre som en kategoriside.
function applyFilterParams(params, f) {
    if (f.sort && f.sort !== 'relevance') params.set('sort', f.sort);
    else params.delete('sort');

    if (f.minPrice) params.set('min_price', f.minPrice);
    else params.delete('min_price');

    if (f.maxPrice) params.set('max_price', f.maxPrice);
    else params.delete('max_price');

    if (f.sale) params.set('sale', 'true');
    else params.delete('sale');

    if (f.organic) params.set('organic', 'true');
    else params.delete('organic');

    if (f.lactose) params.set('lactose', 'true');
    else params.delete('lactose');

    if (f.minWeight) params.set('min_weight', f.minWeight);
    else params.delete('min_weight');

    if (f.maxWeight) params.set('max_weight', f.maxWeight);
    else params.delete('max_weight');

    return params;
}

let filterTimeout;
/**
 * Tilbage/frem-navigation efter et filter-, sorterings- eller butiksskift.
 *
 * Filterændringer opdaterer URL'en med pushState, men der fandtes ingen
 * popstate-lytter overhovedet: et tryk på tilbage ændrede adresselinjen uden
 * at ændre en eneste vare på siden. Her henter vi indholdet for den URL man
 * lander på, og genindsætter det - samme XHR-vej som filtrene selv bruger.
 *
 * Filterpanelets felter synkroniseres IKKE tilbage fra URL'en her; det ville
 * kræve en fuld tovejs-mapping mellem parametre og felter. Indholdet er det
 * væsentlige - felterne kan stå og vise den seneste indstilling, indtil siden
 * indlæses igen.
 */
function handleHistoryNavigation() {
    const dynamicContent = document.getElementById('dynamic-content');
    if (!dynamicContent) return;
    const url = window.location.pathname + window.location.search;
    dynamicContent.style.opacity = '0.5';
    fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
        .then(r => {
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.text();
        })
        .then(html => {
            const doc = new DOMParser().parseFromString(html, 'text/html');
            const newContent = doc.getElementById('dynamic-content');
            dynamicContent.innerHTML = newContent ? newContent.innerHTML : html;
            dynamicContent.style.opacity = '1';
            if (typeof attachProductEventListeners === 'function') attachProductEventListeners();
            if (typeof applyStoreFilters === 'function') applyStoreFilters();
        })
        .catch(err => {
            // Kan indholdet ikke hentes, er en almindelig sideindlæsning
            // stadig korrekt - bedre end at lade brugeren se den forrige
            // sides varer under en ny adresse.
            console.error('Historik-navigation fejlede:', err);
            window.location.reload();
        });
}

window.addEventListener('popstate', handleHistoryNavigation);

function applyAllFilters(isInitialLoad = false, isImmediate = false) {
    clearTimeout(filterTimeout);

    const run = () => {
        // Sidens eget panel - søgepanelets filtre går gennem refreshSearchResults()
        // og rører aldrig #dynamic-content, som ligger skjult bag søgepanelet.
        const f = readFilterValues(pageFilterPanel());
        const { sort, minPrice, maxPrice, sale, organic, lactose, minWeight, maxWeight } = f;

        // Collect params, preserving existing ones like 'stores'
        const params = new URLSearchParams(window.location.search);

        // Inject current selectedStores into params (omit when all stores are selected)
        if (typeof selectedStores !== 'undefined' && selectedStores.size > 0 && selectedStores.size < ALL_STORES.length) {
            params.set('stores', Array.from(selectedStores).join(','));
        } else {
            params.delete('stores');
        }

        // Remove old pagination when filter changes manually
        if (!isInitialLoad) params.delete('page');

        applyFilterParams(params, f);

        // Subcategory is managed by the pill bar - preserve if present
        const activePill = document.querySelector('.subcategory-pill.active[data-sub]:not([data-sub=""])');
        if (activePill) params.set('subcategory', activePill.dataset.sub);
        else params.delete('subcategory');

        // Handle page parameter
        const urlParams = new URLSearchParams(window.location.search);
        const currentPage = urlParams.get('page');

        // If it's a manual filter change, we should reset to page 1.
        // If it's initial load, we should preserve the page from URL.
        if (isInitialLoad && currentPage) {
            params.set('page', currentPage);
        }

        const isHomePage = window.location.pathname === '/' || window.location.pathname.endsWith('index.html') || window.location.pathname === '';
        const _cleanCategoryPaths = ['/Mejeri', '/Koed_og_fisk', '/Frugt_og_groent', '/Broed_og_kager', '/Kolonial', '/Frost', '/Drikkevarer', '/Slik', '/ugens_tilbud'];
        const isCategoryPage = (window.location.pathname.endsWith('.html') && !window.location.pathname.endsWith('index.html'))
            || _cleanCategoryPaths.includes(window.location.pathname);
        const isSearchPage = window.location.pathname.includes('/search');

        if (isHomePage || isCategoryPage || isSearchPage) {
            // Global Server-side filtering
            const baseUrl = window.location.pathname || '/';
            const fullUrl = `${baseUrl}?${params.toString()}`;
            const dynamicContent = document.getElementById('dynamic-content');

            // Initial load: serveren har LIGE renderet denne URL. Er de
            // beregnede parametre de samme, ville vi hente nøjagtig det
            // samme indhold én gang til - en fuld ekstra rendering på hver
            // eneste sideindlæsning, også for besøgende uden gemte filtre.
            // Det er dyrt på et 10 ms CPU-budget, hvor en cold render koster
            // 1,07-1,42 s mod 76 ms for et cache-hit.
            if (isInitialLoad) {
                const current = new URLSearchParams(window.location.search);
                current.delete('page');
                const wanted = new URLSearchParams(params.toString());
                wanted.delete('page');
                current.sort();
                wanted.sort();
                if (current.toString() === wanted.toString()) {
                    if (dynamicContent) dynamicContent.style.opacity = '1';
                    return;
                }
            }

            // Update URL without reload. replaceState ved initial load: en
            // pushState dér lagde en ekstra history-post oven på siden selv,
            // så første tryk på tilbage-knappen tilsyneladende ikke gjorde
            // noget. Kun brugerens egne filterændringer skal give et nyt
            // trin i historikken.
            if (isInitialLoad) {
                window.history.replaceState({ madshopperFilters: true }, '', fullUrl);
            } else {
                window.history.pushState({ madshopperFilters: true }, '', fullUrl);
            }

            // Show loading state
            if (dynamicContent) dynamicContent.style.opacity = '0.5';

            fetch(fullUrl, {
                headers: { 'X-Requested-With': 'XMLHttpRequest' }
            })
                // Uden dette tjek parsede en 500/1101-fejlside som HTML, og
                // hele fejlsidens markup (med header/footer) blev sat som
                // #dynamic-content's indhold - samme mønster som de to
                // søskende-kald (linje 459, 3594) allerede beskytter sig mod.
                .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.text(); })
                .then(html => {
                    if (dynamicContent) {
                        const parser = new DOMParser();
                        const doc = parser.parseFromString(html, 'text/html');
                        const newContent = doc.getElementById('dynamic-content');
                        dynamicContent.innerHTML = newContent ? newContent.innerHTML : html;
                        dynamicContent.style.opacity = '1';

                        // Critical: Re-attach event listeners to new products
                        if (typeof attachProductEventListeners === 'function') {
                            attachProductEventListeners();
                        }

                        // Critical: Re-apply store filters visibility
                        if (typeof applyStoreFilters === 'function') {
                            applyStoreFilters();
                        }
                    }
                })
                .catch(err => {
                    console.error('Filter error:', err);
                    if (dynamicContent) dynamicContent.style.opacity = '1';
                });
        } else {
            // Client-side filtering for Home page
            const products = document.querySelectorAll('.product');
            products.forEach(p => {
                let isVisible = true;
                const price = parseFloat(p.querySelector('.price-main, .price-sale')?.innerText) || 0;
                const weightG = parseFloat(p.dataset.weightG) || 0;

                if (minPrice && price < parseFloat(minPrice)) isVisible = false;
                if (maxPrice && price > parseFloat(maxPrice)) isVisible = false;
                if (sale && !p.querySelector('.sale-badge')) isVisible = false;
                if (organic && p.dataset.isOrganic !== 'true') isVisible = false;
                if (lactose && p.dataset.isLactoseFree !== 'true') isVisible = false;
                if (minWeight && weightG < parseFloat(minWeight)) isVisible = false;
                if (maxWeight && weightG > parseFloat(maxWeight)) isVisible = false;
                
                // Also check store selection for client-side
                const store = p.dataset.store || 'Rema 1000';
                if (typeof selectedStores !== 'undefined' && !selectedStores.has(store)) isVisible = false;

                p.style.display = isVisible ? '' : 'none';
            });

            if (sort !== 'relevance') {
                sortProductsInGrid(sort);
            }
        }
    };

    if (isInitialLoad || isImmediate) {
        run();
    } else {
        filterTimeout = setTimeout(run, 300);
    }
}

function sortProductsInGrid(type) {
    const containers = document.querySelectorAll('.products');
    containers.forEach(container => {
        const productElements = Array.from(container.querySelectorAll('.product'));

        productElements.sort((a, b) => {
            const priceA = parseFloat(a.querySelector('.price-main, .price-sale')?.innerText) || 0;
            const priceB = parseFloat(b.querySelector('.price-main, .price-sale')?.innerText) || 0;
            const nameA = a.querySelector('h3')?.innerText || '';
            const nameB = b.querySelector('h3')?.innerText || '';
            const kgPriceA = parseFloat(a.dataset.remaKgPrice || a.dataset.bilkaKgPrice || a.dataset.mkKgPrice || a.dataset.menyKgPrice || a.dataset.sparKgPrice) || 999999;
            const kgPriceB = parseFloat(b.dataset.remaKgPrice || b.dataset.bilkaKgPrice || b.dataset.mkKgPrice || b.dataset.menyKgPrice || b.dataset.sparKgPrice) || 999999;

            if (type === 'price-asc') return priceA - priceB;
            if (type === 'price-desc') return priceB - priceA;
            if (type === 'kg-price-asc') return kgPriceA - kgPriceB;
            if (type === 'name-asc') return nameA.localeCompare(nameB);
            return 0;
        });

        productElements.forEach(el => container.appendChild(el));
    });
}




// ===== SETTINGS LOGIC ===== //

function toggleSettings() {
    const panel = document.getElementById('settings-panel');
    const overlay = document.getElementById('settings-overlay');
    if (panel.classList.contains('active')) {
        panel.classList.remove('active');
        overlay.classList.remove('active');
        document.body.classList.remove('panel-open');
    } else {
        panel.classList.add('active');
        overlay.classList.add('active');
        if (isMobileViewport()) document.body.classList.add('panel-open');
        // Always refresh checkboxes to reflect any changes made via frontpage buttons
        syncSettingsCheckboxes();
    }
}

function initSettings() {
    // Load Dark Mode
    const isDark = localStorage.getItem('madshopper_darkmode') === 'true';
    if (isDark) {
        document.body.setAttribute('data-theme', 'dark');
        const toggle = document.getElementById('darkModeToggle');
        if (toggle) toggle.checked = true;
    }

    // Sync settings checkboxes and filter buttons from current selectedStores
    // (already correctly restored by initAllStores - do not override)
    syncSettingsCheckboxes();
    syncFilterButtons();
    // Do NOT call applyFilters() here - initAdvancedFilters handles the initial
    // product load and preserves the current page number. Calling applyFilters()
    // with isInitialLoad=false would delete the page param and reset to page 1.

    // Load Misc Settings
    const pushState = localStorage.getItem('madshopper_push') === 'true';
    const emailState = localStorage.getItem('madshopper_email') === 'true';
    if (document.getElementById('pushToggle')) document.getElementById('pushToggle').checked = pushState;
    if (document.getElementById('emailToggle')) document.getElementById('emailToggle').checked = emailState;
}

function toggleDarkMode() {
    const isDark = document.getElementById('darkModeToggle').checked;
    if (isDark) {
        document.body.setAttribute('data-theme', 'dark');
        localStorage.setItem('madshopper_darkmode', 'true');
    } else {
        document.body.removeAttribute('data-theme');
        localStorage.setItem('madshopper_darkmode', 'false');
    }
}

function saveStoreDefaults() {
    const checkboxes = document.querySelectorAll('.store-checkbox input[type="checkbox"]');
    const defaults = [];
    checkboxes.forEach(cb => {
        if (cb.checked) defaults.push(cb.value);
    });

    // Must keep at least 1 store active
    if (defaults.length === 0) return;

    // Apply to current session
    selectedStores.clear();
    defaults.forEach(s => selectedStores.add(s));
    saveStoreFilters();

    // Sync both UIs from single source of truth
    syncFilterButtons();
    syncSettingsCheckboxes();

    // Refresh products view
    applyFilters();

    const searchResults = document.getElementById('searchResults');
    if (searchResults && searchResults.classList.contains('visible')) {
        refreshSearchResults(true);
    }
}

function saveMiscSettings() {
    const push = document.getElementById('pushToggle').checked;
    const email = document.getElementById('emailToggle').checked;
    localStorage.setItem('madshopper_push', push ? 'true' : 'false');
    localStorage.setItem('madshopper_email', email ? 'true' : 'false');
}

// Ensure initSettings is called on DOM load

// ── Saved Lists + live delt kurv (gruppe, kræver konto) ─────────────────────

var PENDING_SHARE_KEY = 'madshopper_pending_share';
var MAX_SAVED_LISTS = 10;
var _sharedState = null;          // {token, title, revision, members, ...}
var _sharedPollTimer = null;
var _sharedPushTimer = null;
var _sharedApplyingRemote = false;
var _pendingJoinToken = null;
var _personalCartSync = null;     // auth.js's scheduleSync, chained when shared

function _authUser() {
    return (window.AuthBridge && window.AuthBridge.getUser)
        ? window.AuthBridge.getUser()
        : null;
}

function _requireAccount() {
    if (_authUser()) return true;
    if (window.AuthBridge && window.AuthBridge.requireAuth) {
        window.AuthBridge.requireAuth();
    } else if (typeof window.openAuthModal === 'function') {
        window.openAuthModal('login');
    } else {
        alert('Log ind for at bruge denne funktion.');
    }
    return false;
}

function _sbClient() {
    return (window.AuthBridge && window.AuthBridge.getClient)
        ? window.AuthBridge.getClient()
        : null;
}

function _rpc(name) {
    return (window.AuthBridge && window.AuthBridge.rpcName)
        ? window.AuthBridge.rpcName(name)
        : name;
}

async function _ensureDisplayName() {
    if (window.AuthBridge && typeof window.AuthBridge.ensureDisplayName === 'function') {
        return await window.AuthBridge.ensureDisplayName();
    }
    return '';
}

function _savedListsKey() {
    var u = _authUser();
    return u ? ('savedLists:' + u.id) : null;
}

function _inSharedGroup() {
    return !!( _sharedState && _sharedState.token);
}

function _personalSavedLists() {
    var key = _savedListsKey();
    if (!key) return [];
    var lists = safeJSONParse(key, []);
    if ((!lists || lists.length === 0)) {
        var legacy = safeJSONParse('savedLists', []);
        if (legacy && legacy.length) {
            lists = legacy;
            try { localStorage.removeItem('savedLists'); } catch (e) { /* ignorér */ }
        }
    }
    if (lists.length > MAX_SAVED_LISTS) {
        lists = lists.slice(0, MAX_SAVED_LISTS);
        _writePersonalSavedLists(lists);
    }
    return lists;
}

function _writePersonalSavedLists(lists) {
    var key = _savedListsKey();
    if (!key) return;
    try {
        localStorage.setItem(key, JSON.stringify((lists || []).slice(0, MAX_SAVED_LISTS)));
    } catch (e) { /* ignorér */ }
}

function _groupListsFromState() {
    return (_sharedState && _sharedState.saved_lists ? _sharedState.saved_lists : []).map(function (l) {
        return {
            id: l.id,
            name: l.name || 'Liste',
            createdAt: l.created_at || l.createdAt || '',
            items: _rowsToCartItems(l.items || [])
        };
    }).slice(0, MAX_SAVED_LISTS);
}

function _listsToCompact(lists) {
    return (lists || []).slice(0, MAX_SAVED_LISTS).map(function (l) {
        return {
            id: String(l.id || '').slice(0, 40),
            name: String(l.name || 'Liste').trim().slice(0, 80) || 'Liste',
            created_at: String(l.createdAt || l.created_at || '').slice(0, 40),
            items: _cartToShareRows(l.items || [])
        };
    }).filter(function (l) { return l.items && l.items.length; });
}

function getSavedLists() {
    if (_inSharedGroup()) return _groupListsFromState();
    return _personalSavedLists();
}

async function _persistSavedLists(lists) {
    lists = (lists || []).slice(0, MAX_SAVED_LISTS);
    if (_inSharedGroup()) {
        var sb = _sbClient();
        if (!sb) return false;
        try {
            var res = await sb.rpc(_rpc('push_shared_saved_lists'), {
                p_lists: _listsToCompact(lists)
            });
            if (res.error) {
                console.error('[saved-lists]', res.error);
                return false;
            }
            var data = res.data || {};
            if (!data.ok) {
                if (data.error === 'lists_full') {
                    alert('Gruppen kan maks have ' + MAX_SAVED_LISTS + ' gemte lister.');
                }
                return false;
            }
            _sharedState = Object.assign({}, _sharedState, data);
            return true;
        } catch (err) {
            console.error('[saved-lists]', err);
            return false;
        }
    }
    _writePersonalSavedLists(lists);
    return true;
}

function switchCartTab(tab) {
    const cartTab = document.getElementById('cart-tab-cart');
    const listsTab = document.getElementById('cart-tab-lists');
    const btnCart = document.getElementById('tab-cart');
    const btnLists = document.getElementById('tab-lists');
    const clearBtn = document.getElementById('clear-cart-btn');

    if (tab === 'cart') {
        cartTab.style.display = '';
        listsTab.style.display = 'none';
        btnCart.classList.add('active');
        btnLists.classList.remove('active');
        if (clearBtn) clearBtn.style.display = cart.length > 0 ? 'flex' : 'none';
    } else {
        cartTab.style.display = 'none';
        listsTab.style.display = '';
        btnCart.classList.remove('active');
        btnLists.classList.add('active');
        if (clearBtn) clearBtn.style.display = 'none';
        renderSavedLists();
    }
}

async function saveCurrentCartAsList() {
    if (cart.length === 0) return;
    if (!_requireAccount()) return;

    const existing = getSavedLists();
    if (existing.length >= MAX_SAVED_LISTS) {
        alert('Du kan maks have ' + MAX_SAVED_LISTS + ' gemte lister. Slet en først.');
        return;
    }

    const name = prompt('Giv listen et navn:', 'Ugens kurv');
    if (!name || !name.trim()) return;

    const lists = existing.slice();
    lists.unshift({
        id: Date.now().toString(),
        name: name.trim(),
        createdAt: new Date().toLocaleDateString('da-DK'),
        items: JSON.parse(JSON.stringify(cart))
    });
    const ok = await _persistSavedLists(lists.slice(0, MAX_SAVED_LISTS));
    if (!ok) {
        alert('Kunne ikke gemme listen. Prøv igen.');
        return;
    }
    updateListsBadge();
    switchCartTab('lists');
}

function loadSavedList(id) {
    if (!_requireAccount()) return;
    const list = getSavedLists().find(l => l.id === id);
    if (!list) return;
    cart = JSON.parse(JSON.stringify(list.items));
    saveCart(); // synker live til gruppen hvis I er i én
    switchCartTab('cart');
}

async function deleteSavedList(id) {
    if (!_requireAccount()) return;
    const lists = getSavedLists().filter(l => l.id !== id);
    const ok = await _persistSavedLists(lists);
    if (!ok) {
        alert('Kunne ikke slette listen. Prøv igen.');
        return;
    }
    updateListsBadge();
    renderSavedLists();
}

function updateListsBadge() {
    const badge = document.getElementById('lists-count-badge');
    if (!badge) return;
    if (!_authUser()) {
        badge.style.display = 'none';
        return;
    }
    const count = getSavedLists().length;
    if (count > 0) {
        badge.textContent = count;
        badge.style.display = 'inline-flex';
    } else {
        badge.style.display = 'none';
    }
}

function renderSavedLists() {
    const container = document.getElementById('saved-lists-container');
    if (!container) return;

    if (!_authUser()) {
        container.innerHTML = `
            <div class="saved-lists-empty">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/>
                    <circle cx="12" cy="7" r="4"/>
                </svg>
                <p>Log ind for at gemme lister</p>
                <span>Dine gemte lister følger din konto på denne enhed</span>
                <button type="button" class="saved-list-load-btn" style="margin-top:8px;" onclick="openAuthModal('login')">Log ind</button>
            </div>`;
        return;
    }

    const inGroup = _inSharedGroup();
    const lists = getSavedLists();
    if (lists.length === 0) {
        container.innerHTML = `
            <div class="saved-lists-empty">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2"/>
                    <rect x="9" y="3" width="6" height="4" rx="1"/>
                    <line x1="9" y1="12" x2="15" y2="12"/><line x1="9" y1="16" x2="13" y2="16"/>
                </svg>
                <p>${inGroup ? 'Ingen fælles lister endnu' : 'Ingen gemte lister endnu'}</p>
                <span>${inGroup
                    ? 'Gem kurven som liste — hele gruppen kan indlæse den (max ' + MAX_SAVED_LISTS + ')'
                    : 'Gem din kurv som en liste (max ' + MAX_SAVED_LISTS + ')'}</span>
            </div>`;
        return;
    }

    // Id'et maa ALDRIG interpoleres ind i en onclick-streng. For faelleslister
    // kommer det fra serveren, hvor push_shared_saved_lists kun afkorter til 40
    // tegn uden at sanere tegn - et gruppemedlem kunne dermed faa vilkaarlig JS
    // til at koere hos alle andre ved klik paa Indlaes/Slet. Vi lader det i
    // stedet leve i en data-attribut (escapet som alt andet) og laeser det i en
    // delegeret listener, hvor det kun kan vaere en streng.
    container.innerHTML = lists.map(list => `
        <div class="saved-list-item">
            <div class="saved-list-info">
                <span class="saved-list-name">${escapeHtml(list.name)}</span>
                <span class="saved-list-meta">${(list.items || []).length} varer &middot; ${escapeHtml(list.createdAt || '')}${inGroup ? ' · fælles' : ''}</span>
            </div>
            <div class="saved-list-actions">
                <button class="saved-list-load-btn" data-list-action="load" data-list-id="${escapeHtml(list.id)}">Indlæs</button>
                <button class="saved-list-delete-btn" data-list-action="delete" data-list-id="${escapeHtml(list.id)}" aria-label="Slet liste">&times;</button>
            </div>
        </div>`).join('');

    if (!container.dataset.listenerAttached) {
        container.dataset.listenerAttached = '1';
        container.addEventListener('click', function (e) {
            const btn = e.target.closest('[data-list-action]');
            if (!btn || !container.contains(btn)) return;
            const id = btn.dataset.listId || '';
            if (!id) return;
            if (btn.dataset.listAction === 'load') loadSavedList(id);
            else deleteSavedList(id);
        });
    }
}

function _cartToShareRows(items) {
    return (items || []).slice(0, 100).map(function (it) {
        var q = parseInt(it.quantity, 10);
        if (isNaN(q) || q < 1) q = 1;
        if (q > 99) q = 99;
        return {
            p: String(it.id || '').slice(0, 64),
            q: q,
            n: (it.name || '').slice(0, 120),
            i: (it.image || '').slice(0, 300),
            s: (it.store || '').slice(0, 40),
            pr: (it.price != null && !isNaN(it.price)) ? Number(it.price) : null
        };
    }).filter(function (r) { return r.p; });
}

function _rowsToCartItems(rows) {
    return (rows || []).map(function (r) {
        return {
            id: r.p,
            name: r.n || '',
            image: r.i || '',
            store: r.s || '',
            price: (r.pr != null ? Number(r.pr) : 0),
            quantity: r.q || 1
        };
    });
}

function _inviteUrl(token) {
    return window.location.origin + '/?liste=' + encodeURIComponent(token);
}

function _updateSharedCartUI() {
    var banner = document.getElementById('shared-cart-banner');
    var nameEl = document.getElementById('shared-cart-banner-name');
    var metaEl = document.getElementById('shared-cart-banner-meta');
    var membersEl = document.getElementById('shared-cart-banner-members');
    var titleEl = document.getElementById('cart-panel-title');
    var btnLabel = document.getElementById('share-list-btn-label');
    var tabCart = document.getElementById('tab-cart');
    var tabListsLabel = document.getElementById('tab-lists-label');

    if (_sharedState && _sharedState.ok !== false && _sharedState.token) {
        if (banner) banner.style.display = 'flex';
        if (nameEl) nameEl.textContent = _sharedState.title || 'Delt kurv';
        if (metaEl) {
            metaEl.textContent = 'Live · ' + (_sharedState.members || 1) + ' / ' +
                (_sharedState.max_members || 6) + ' personer';
        }
        if (membersEl) {
            var list = _sharedState.member_list || [];
            if (list.length) {
                membersEl.textContent = 'Med: ' + list.map(function (m) {
                    var label = (m && m.name) ? String(m.name) : 'Medlem';
                    return m && m.me ? (label + ' (dig)') : label;
                }).join(', ');
            } else {
                membersEl.textContent = '';
            }
        }
        if (titleEl) titleEl.textContent = _sharedState.title || 'Delt kurv';
        if (btnLabel) btnLabel.textContent = 'Inviter';
        if (tabCart) tabCart.textContent = 'Delt kurv';
        if (tabListsLabel) tabListsLabel.textContent = 'Gruppens lister';
        updateListsBadge();
        var listsTab = document.getElementById('cart-tab-lists');
        if (listsTab && listsTab.style.display !== 'none') renderSavedLists();
    } else {
        if (banner) banner.style.display = 'none';
        if (membersEl) membersEl.textContent = '';
        if (titleEl) titleEl.textContent = 'Din kurv';
        if (btnLabel) btnLabel.textContent = 'Del kurv';
        if (tabCart) tabCart.textContent = 'Din kurv';
        if (tabListsLabel) tabListsLabel.textContent = 'Mine lister';
        updateListsBadge();
    }
}

function _attachSharedCartSync() {
    if (!window.CartBridge) return;
    if (!_personalCartSync && typeof window.CartBridge._onChange === 'function') {
        _personalCartSync = window.CartBridge._onChange;
    }
    window.CartBridge._onChange = function (c) {
        if (_personalCartSync) _personalCartSync(c);
        if (_sharedState && _sharedState.token && !_sharedApplyingRemote) {
            _scheduleSharedPush(c);
        }
    };
}

function _detachSharedCartOnly() {
    // Behold personal sync; stop shared push/poll.
    if (window.CartBridge) {
        window.CartBridge._onChange = _personalCartSync || window.CartBridge._onChange;
    }
}

function _scheduleSharedPush(c) {
    if (_sharedPushTimer) clearTimeout(_sharedPushTimer);
    // Fastholder IKKE `c` som et snapshot til selve pushet: falder en poll
    // (_pullSharedCart) eller en ny _enterSharedCart ind i de 450 ms, rebinder
    // den `cart` til en frisk reference - og et gemt snapshot fra planlægnings-
    // tidspunktet ville så sende DEN forældede kurv af sted og rulle den
    // friskere ændring tilbage for hele gruppen. _pushSharedCart() uden
    // argument læser derfor den AKTUELLE kurv, først når timeren rent
    // faktisk fyrer (samme rodfejl og fix som _pullSharedCart-guarden
    // nedenfor løser i den anden retning).
    _sharedPushTimer = setTimeout(function () {
        _sharedPushTimer = null;
        _pushSharedCart();
    }, 450);
}

async function _pushSharedCart(c) {
    if (!_sharedState || !_sharedState.token) return;
    var sb = _sbClient();
    if (!sb) return;
    try {
        var res = await sb.rpc(_rpc('push_shared_cart'), {
            p_items: _cartToShareRows(c || cart)
        });
        if (res.error) {
            console.error('[shared-push]', res.error);
            return;
        }
        var data = res.data || {};
        if (data.ok) {
            _sharedState = Object.assign({}, _sharedState, data);
            _updateSharedCartUI();
        } else if (data.error === 'none') {
            _stopSharedCart(false);
        }
    } catch (err) {
        console.error('[shared-push]', err);
    }
}

async function _pullSharedCart() {
    if (!_authUser() || !_sharedState) return;
    // Ventende push vinder - samme mønster som refreshCart() i auth.js. Uden
    // denne guard kunne en poll anvende en fjern-ændring OVEN I en lokal
    // ændring der endnu ikke er nået serveren, og den efterfølgende push
    // (som nu altid læser den friskeste kurv, se _scheduleSharedPush) ville
    // sende den forkerte, delvist-anvendte tilstand af sted.
    if (_sharedPushTimer) return;
    var sb = _sbClient();
    if (!sb) return;
    try {
        var res = await sb.rpc(_rpc('get_my_shared_cart'));
        if (res.error) return;
        var data = res.data || {};
        if (!data.ok) {
            if (data.error === 'none') _stopSharedCart(false);
            return;
        }
        var me = _authUser();
        var remoteRev = Number(data.revision) || 0;
        var localRev = Number(_sharedState.revision) || 0;
        var prevListsJson = JSON.stringify(_sharedState.saved_lists || []);
        _sharedState = Object.assign({}, _sharedState, data);
        _updateSharedCartUI();
        if (remoteRev > localRev && data.updated_by && me && data.updated_by !== me.id) {
            _sharedApplyingRemote = true;
            try {
                if (window.CartBridge) {
                    window.CartBridge.applyFromServer(_rowsToCartItems(data.items));
                } else {
                    cart = _rowsToCartItems(data.items);
                    try { localStorage.setItem('cart', JSON.stringify(cart)); } catch (e) {}
                    updateCartDisplay();
                    updateCartCount();
                }
            } finally {
                _sharedApplyingRemote = false;
            }
        } else if (remoteRev > localRev) {
            // Egen revision fra anden fane / race - opdater revision, undgå loop.
            _sharedState.revision = remoteRev;
        }
        // Gemte lister kan være ændret af andre - opdater fanen.
        if (JSON.stringify(_sharedState.saved_lists || []) !== prevListsJson) {
            updateListsBadge();
            var listsTab = document.getElementById('cart-tab-lists');
            if (listsTab && listsTab.style.display !== 'none') renderSavedLists();
        }
    } catch (err) {
        console.error('[shared-pull]', err);
    }
}

function _startSharedPoll() {
    if (_sharedPollTimer) clearInterval(_sharedPollTimer);
    // Poll KUN mens fanen er synlig. Hvert 2,5. sekund doegnet rundt er ~34.000
    // RPC-kald pr. fane pr. doegn - med tre faner aabne over 100.000, mod en
    // gratis Supabase-plan hvor egress allerede er godt brugt. En skjult fane
    // har ingen at vise aendringen til; vi henter i stedet med det samme, naar
    // den bliver synlig igen.
    _sharedPollTimer = setInterval(function () {
        if (document.visibilityState === 'hidden') return;
        _pullSharedCart();
    }, 2500);
}

document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'visible' && _sharedPollTimer) {
        _pullSharedCart();
    }
});

function _stopSharedCart(keepLocal) {
    if (_sharedPollTimer) { clearInterval(_sharedPollTimer); _sharedPollTimer = null; }
    if (_sharedPushTimer) { clearTimeout(_sharedPushTimer); _sharedPushTimer = null; }
    _sharedState = null;
    // Ved logout er CartBridge._onChange allerede null - genopret ikke personal sync.
    if (_authUser() && window.CartBridge && _personalCartSync) {
        window.CartBridge._onChange = _personalCartSync;
    }
    if (!_authUser()) _personalCartSync = null;
    _updateSharedCartUI();
    if (!keepLocal) { /* local cart beholder sidste indhold */ }
}

function _enterSharedCart(data) {
    if (!data || !data.ok) return;
    _sharedState = data;
    _sharedApplyingRemote = true;
    try {
        if (window.CartBridge) {
            window.CartBridge.applyFromServer(_rowsToCartItems(data.items || []));
        } else {
            cart = _rowsToCartItems(data.items || []);
            try { localStorage.setItem('cart', JSON.stringify(cart)); } catch (e) {}
            updateCartDisplay();
            updateCartCount();
        }
    } finally {
        _sharedApplyingRemote = false;
    }
    _attachSharedCartSync();
    _startSharedPoll();
    _updateSharedCartUI();
    // Private gemte lister merges ind i gruppen (max 10). Overskud slettes.
    _mergePersonalListsIntoGroup();
}

async function _mergePersonalListsIntoGroup() {
    if (!_inSharedGroup()) return;
    var personal = _personalSavedLists();
    if (!personal.length) return;

    // Gruppens lister først (allerede i gruppen beholder deres plads).
    var group = _groupListsFromState().slice(0, MAX_SAVED_LISTS);
    var seen = {};
    var merged = [];
    group.forEach(function (l) {
        if (!l || !l.id || seen[l.id]) return;
        seen[l.id] = true;
        merged.push(l);
    });

    // Personlige kandidater i UI-rækkefølge (øverst = index 0 = nyeste via unshift).
    var candidates = [];
    personal.forEach(function (l) {
        if (!l || !l.id || seen[l.id]) return;
        candidates.push(l);
    });

    var room = MAX_SAVED_LISTS - merged.length;
    // Tag fra toppen først; det der ikke er plads til (nederst) droppes af pladsen i gruppen.
    var toMerge = room > 0 ? candidates.slice(0, room) : [];
    toMerge.forEach(function (l) {
        seen[l.id] = true;
        merged.push(l);
    });

    if (toMerge.length === 0) {
        // Gruppen er allerede fuld (room === 0) - intet flyttes. De private
        // lister må IKKE slettes her: før denne rettelse blev de slettet
        // ubetinget, selvom intet nogensinde blev gemt i gruppen.
        if (candidates.length > 0) {
            alert('Gruppen har allerede ' + MAX_SAVED_LISTS + ' gemte lister. Dine egne lister forbliver private på denne enhed.');
        }
        return;
    }

    var ok = await _persistSavedLists(merged.slice(0, MAX_SAVED_LISTS));
    if (!ok) {
        // Pushet fejlede (netværk, RLS, "lists_full") - de private lister
        // ligger stadig urørt lokalt, i stedet for at være slettet uden at
        // være gemt noget sted.
        alert('Dine lister kunne ikke flyttes til gruppen. De ligger stadig kun lokalt på denne enhed - prøv igen senere.');
        return;
    }

    // Slet først de private kopier EFTER et bekræftet push, så de aldrig kan
    // forsvinde sporløst.
    _writePersonalSavedLists([]);
    updateListsBadge();
    var listsTab = document.getElementById('cart-tab-lists');
    if (listsTab && listsTab.style.display !== 'none') renderSavedLists();
}

async function _loadMySharedCart() {
    if (!_authUser()) return;
    var sb = _sbClient();
    if (!sb) return;
    try {
        var res = await sb.rpc(_rpc('get_my_shared_cart'));
        if (res.error) return;
        var data = res.data || {};
        if (data.ok) _enterSharedCart(data);
        else _updateSharedCartUI();
    } catch (e) { /* ignorér */ }
}

async function shareCurrentCart() {
    if (!_requireAccount()) return;
    var sb = _sbClient();
    if (!sb) {
        alert('Deling er midlertidigt utilgængelig.');
        return;
    }

    var btn = document.getElementById('share-list-btn');
    if (btn) btn.disabled = true;

    try {
        // Allerede i gruppe → vis invite-link.
        if (_sharedState && _sharedState.token) {
            openShareListModal(
                _inviteUrl(_sharedState.token),
                _sharedState.title,
                _sharedState.members || 1,
                _sharedState.max_members || 6
            );
            return;
        }

        var myName = await _ensureDisplayName();
        if (!myName) return;

        var title = prompt('Giv gruppen et navn:', '');
        if (title === null) return;
        title = String(title).trim().slice(0, 80);
        if (!title) {
            alert('Gruppen skal have et navn.');
            return;
        }

        var res = await sb.rpc(_rpc('create_shared_cart'), {
            p_items: _cartToShareRows(cart),
            p_title: title,
            p_name: myName
        });
        if (res.error) {
            console.error('[share]', res.error);
            alert('Kunne ikke oprette gruppen. Prøv igen.');
            return;
        }
        var data = res.data || {};
        if (!data.ok) {
            var msg = {
                login: 'Log ind for at dele kurven.',
                title: 'Gruppen skal have et navn.',
                empty: 'Kurven er tom.'
            }[data.error] || 'Kunne ikke oprette gruppen.';
            alert(msg);
            if (data.error === 'login') _requireAccount();
            return;
        }

        _enterSharedCart(data);
        openShareListModal(
            _inviteUrl(data.token),
            data.title,
            data.members || 1,
            data.max_members || 6
        );

        if (navigator.share) {
            try {
                await navigator.share({
                    title: (data.title || 'Delt kurv') + ' – MadShopper',
                    text: 'Tilslut vores delte indkøbskurv på MadShopper',
                    url: _inviteUrl(data.token)
                });
            } catch (e) { /* annulleret */ }
        }
    } catch (err) {
        console.error('[share]', err);
        alert('Noget gik galt. Prøv igen.');
    } finally {
        if (btn) btn.disabled = false;
    }
}

async function leaveSharedCart() {
    if (!_requireAccount()) return;
    if (!_sharedState) return;
    if (!window.confirm('Meld dig ud af «' + (_sharedState.title || 'gruppen') + '»? Din lokale kurv beholder varerne.')) {
        return;
    }
    var sb = _sbClient();
    if (!sb) return;
    try {
        var res = await sb.rpc(_rpc('leave_shared_cart'));
        if (res.error) {
            console.error('[leave]', res.error);
            alert('Kunne ikke melde dig ud. Prøv igen.');
            return;
        }
        _stopSharedCart(true);
    } catch (err) {
        console.error('[leave]', err);
        alert('Noget gik galt. Prøv igen.');
    }
}

function openShareListModal(url, title, members, maxMembers) {
    var modal = document.getElementById('share-list-modal');
    var input = document.getElementById('share-list-url');
    var meta = document.getElementById('share-list-meta');
    var heading = document.getElementById('share-list-title');
    if (heading) heading.textContent = title ? ('Inviter til «' + title + '»') : 'Inviter til gruppen';
    if (input) input.value = url;
    if (meta) meta.textContent = 'Pladser: ' + members + ' / ' + maxMembers + ' personer';
    if (modal) modal.style.display = 'flex';
}

function closeShareListModal(event) {
    if (event && event.target !== event.currentTarget) return;
    var modal = document.getElementById('share-list-modal');
    if (modal) modal.style.display = 'none';
}

function copyShareListUrl() {
    var input = document.getElementById('share-list-url');
    if (!input || !input.value) return;
    var btn = document.querySelector('.share-list-copy-btn');
    function done() {
        if (btn) {
            var prev = btn.textContent;
            btn.textContent = 'Kopieret!';
            setTimeout(function () { btn.textContent = prev; }, 1500);
        }
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(input.value).then(done).catch(function () {
            input.select();
            try { document.execCommand('copy'); done(); } catch (e) {}
        });
    } else {
        input.select();
        try { document.execCommand('copy'); done(); } catch (e) {}
    }
}

function _shareErrorText(code) {
    return {
        login: 'Log ind for at tilslutte gruppen.',
        not_found: 'Gruppen findes ikke – linket kan være forkert.',
        full: 'Gruppen er fuld (maks 6 personer).',
        lists_full: 'Gruppen har allerede maks 10 gemte lister.',
        none: 'Du er ikke medlem af en gruppe.',
        name: 'Skriv dit navn først.'
    }[code] || 'Kunne ikke tilslutte gruppen.';
}

async function handleSharedCartInvite(token) {
    token = String(token || '').trim().toLowerCase();
    if (token.length < 8) return;

    if (!_authUser()) {
        try { sessionStorage.setItem(PENDING_SHARE_KEY, token); } catch (e) {}
        _requireAccount();
        return;
    }

    // Allerede i samme gruppe?
    if (_sharedState && _sharedState.token === token) {
        openShareListModal(
            _inviteUrl(token),
            _sharedState.title,
            _sharedState.members || 1,
            _sharedState.max_members || 6
        );
        _clearListeParam();
        return;
    }

    _pendingJoinToken = token;
    var modal = document.getElementById('claim-list-modal');
    var title = document.getElementById('claim-list-title');
    var sub = document.getElementById('claim-list-subtitle');
    var meta = document.getElementById('claim-list-meta');
    var err = document.getElementById('claim-list-error');
    var actions = document.getElementById('claim-list-actions');
    if (err) { err.style.display = 'none'; err.textContent = ''; }
    if (actions) actions.style.display = 'flex';
    if (title) title.textContent = 'Tilslut gruppe';
    if (sub) {
        sub.textContent = _sharedState
            ? 'Du er allerede i en anden gruppe. Hvis du tilslutter dig, meldes du automatisk ud af den gamle.'
            : 'Du bliver en del af den delte live-kurv, indtil du melder dig ud.';
    }
    if (meta) meta.textContent = 'Link: …' + token.slice(-6);
    if (modal) modal.style.display = 'flex';
    _clearListeParam();
}

async function confirmJoinSharedCart() {
    if (!_pendingJoinToken) return;
    if (!_requireAccount()) return;
    var sb = _sbClient();
    if (!sb) return;

    var myName = await _ensureDisplayName();
    if (!myName) return;

    try {
        var res = await sb.rpc(_rpc('join_shared_cart'), {
            p_token: _pendingJoinToken,
            p_name: myName
        });
        if (res.error) {
            console.error('[join]', res.error);
            var em = (res.error.message || '').toLowerCase();
            openClaimListError(_shareErrorText(
                em.indexOf('shared_cart_full') >= 0 ? 'full' : 'not_found'
            ));
            return;
        }
        var data = res.data || {};
        if (!data.ok) {
            openClaimListError(_shareErrorText(data.error));
            return;
        }
        _pendingJoinToken = null;
        closeClaimListModal();
        _enterSharedCart(data);
        try { sessionStorage.removeItem(PENDING_SHARE_KEY); } catch (e) {}
        var panel = document.getElementById('cart-panel');
        if (panel && !panel.classList.contains('active') && typeof toggleCart === 'function') {
            toggleCart();
        }
        switchCartTab('cart');
    } catch (err) {
        console.error('[join]', err);
        openClaimListError('Noget gik galt. Prøv igen.');
    }
}

function openClaimListError(message) {
    var modal = document.getElementById('claim-list-modal');
    var title = document.getElementById('claim-list-title');
    var sub = document.getElementById('claim-list-subtitle');
    var meta = document.getElementById('claim-list-meta');
    var err = document.getElementById('claim-list-error');
    var actions = document.getElementById('claim-list-actions');
    if (title) title.textContent = 'Kunne ikke tilslutte';
    if (sub) sub.textContent = '';
    if (meta) meta.textContent = '';
    if (actions) actions.style.display = 'none';
    if (err) { err.textContent = message; err.style.display = 'block'; }
    if (modal) modal.style.display = 'flex';
    _pendingJoinToken = null;
}

function closeClaimListModal(event) {
    if (event && event.target !== event.currentTarget) return;
    var modal = document.getElementById('claim-list-modal');
    if (modal) modal.style.display = 'none';
    _pendingJoinToken = null;
}

function _clearListeParam() {
    try {
        var u = new URL(window.location.href);
        if (u.searchParams.has('liste')) {
            u.searchParams.delete('liste');
            history.replaceState(null, '', u.pathname + u.search + u.hash);
        }
    } catch (e) {}
}

function _initShareLinkFromUrl() {
    try {
        var params = new URLSearchParams(window.location.search);
        var token = params.get('liste');
        if (token) {
            handleSharedCartInvite(token);
            return;
        }
        var pending = sessionStorage.getItem(PENDING_SHARE_KEY);
        if (pending && _authUser()) handleSharedCartInvite(pending);
    } catch (e) {}
}

function _bindAuthShareHooks() {
    if (!window.AuthBridge) return;
    window.AuthBridge.onSignedIn = function () {
        updateListsBadge();
        loadPersonalSavingsWidget();
        _attachSharedCartSync();
        _loadMySharedCart().then(function () {
            try {
                var pending = sessionStorage.getItem(PENDING_SHARE_KEY);
                if (pending) handleSharedCartInvite(pending);
            } catch (e) {}
        });
    };
    window.AuthBridge.onSignedOut = function () {
        _stopSharedCart(true);
        updateListsBadge();
        renderPersonalSavingsWidget({ available: false, message: 'Log ind for at tracke besparelse' });
    };
}

// ── Personlig besparelseswidget (forside) ───────────────────────────────────

var _DK_MONTHS = [
    '', 'januar', 'februar', 'marts', 'april', 'maj', 'juni',
    'juli', 'august', 'september', 'oktober', 'november', 'december'
];

function _formatKr(n) {
    var v = Number(n);
    if (isNaN(v)) v = 0;
    return v.toLocaleString('da-DK', { minimumFractionDigits: 0, maximumFractionDigits: 2 });
}

function _monthLabel(monthKey) {
    if (!monthKey || typeof monthKey !== 'string') return '';
    var parts = monthKey.split('-');
    if (parts.length < 2) return monthKey;
    var m = parseInt(parts[1], 10);
    var name = _DK_MONTHS[m] || monthKey;
    return name.charAt(0).toUpperCase() + name.slice(1);
}

function renderPersonalSavingsWidget(data) {
    var el = document.getElementById('personalSavingsWidget');
    if (!el) return;
    data = data || {};

    if (!data.available) {
        el.classList.add('savings-widget--login');
        el.innerHTML =
            '<div class="savings-icon" aria-hidden="true">' +
            '<svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 1v22"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>' +
            '</div>' +
            '<div class="savings-content">' +
            '<div class="savings-label">Personlig besparelse</div>' +
            '<div class="savings-amount">Log ind for at tracke besparelse</div>' +
            '</div>' +
            '<button type="button" class="savings-badge savings-badge--btn" id="savingsLoginBtn">Log ind</button>';
        var btn = document.getElementById('savingsLoginBtn');
        if (btn) {
            btn.onclick = function () { _requireAccount(); };
        }
        el.onclick = function (e) {
            if (e.target && e.target.id === 'savingsLoginBtn') return;
            _requireAccount();
        };
        el.setAttribute('role', 'button');
        el.tabIndex = 0;
        return;
    }

    el.classList.remove('savings-widget--login');
    el.onclick = null;
    el.removeAttribute('role');
    el.removeAttribute('tabindex');

    var amount = _formatKr(data.amount);
    var topPct = Math.max(1, Math.min(100, parseInt(data.top_pct, 10) || 100));
    var prevHtml = '';
    if (data.show_prev && Number(data.prev_amount) > 0) {
        prevHtml =
            '<div class="savings-prev">I ' + escapeHtml(_monthLabel(data.prev_month_key)) +
            ' sparede du ' + escapeHtml(_formatKr(data.prev_amount)) + ' kr</div>';
    }

    el.innerHTML =
        '<div class="savings-icon" aria-hidden="true">' +
        '<svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 1v22"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>' +
        '</div>' +
        '<div class="savings-content">' +
        '<div class="savings-label">Personlig besparelse</div>' +
        '<div class="savings-amount">Du har sparet ' + escapeHtml(amount) + ' <span>kr</span> denne måned</div>' +
        prevHtml +
        '</div>' +
        '<div class="savings-badge">Top ' + topPct + '%</div>';
}

async function loadPersonalSavingsWidget() {
    var el = document.getElementById('personalSavingsWidget');
    if (!el) return;
    if (!_authUser()) {
        renderPersonalSavingsWidget({ available: false, message: 'Log ind for at tracke besparelse' });
        return;
    }
    var sb = _sbClient();
    if (!sb) {
        renderPersonalSavingsWidget({ available: false, message: 'Log ind for at tracke besparelse' });
        return;
    }
    try {
        var res = await sb.rpc(_rpc('get_personal_savings'));
        if (res && res.data) {
            renderPersonalSavingsWidget(res.data);
        } else {
            renderPersonalSavingsWidget({
                available: true, amount: 0, top_pct: 100,
                show_prev: false, prev_amount: 0, prev_month_key: ''
            });
        }
    } catch (e) {
        renderPersonalSavingsWidget({
            available: true, amount: 0, top_pct: 100,
            show_prev: false, prev_amount: 0, prev_month_key: ''
        });
    }
}

document.addEventListener('DOMContentLoaded', function () {
    _bindAuthShareHooks();
    loadPersonalSavingsWidget();
    setTimeout(function () {
        _bindAuthShareHooks();
        updateListsBadge();
        loadPersonalSavingsWidget();
        if (_authUser()) {
            _attachSharedCartSync();
            _loadMySharedCart().then(_initShareLinkFromUrl);
        } else {
            _initShareLinkFromUrl();
        }
    }, 500);
});


function acceptAlternative(oldId, altData) {
    const index = cart.findIndex(c => c.id === oldId);
    if (index === -1) return;
    
    const oldItem = cart[index];
    
    const newItem = {
        id: 'product' + altData.alt_id,
        name: altData.alt_name,
        store: altData.alt_store,
        price: altData.alt_price,
        storePrices: altData.alt_storePrices,
        image: altData.alt_image,
        category: altData.alt_category,
        unitMeasure: altData.alt_unitMeasure,
        kgPrice: altData.alt_kgPrice,
        quantity: oldItem.quantity,
        storeMultiDeals: {}
    };
    
    cart[index] = newItem;
    saveCart();
    updateCartDisplay();
    
    showReference();
}


/* ===== FOKUS-FÆLDE I MODALER =====================================
 * Ingen af overlayene holdt på tastaturfokus: åbnede man kurven, kunne man
 * tabbe videre ned i siden bagved, mens skærmlæseren stadig troede den var i
 * panelet. Off-screen-panelerne er desuden kun skjult med transform, og
 * transformede elementer bliver i tab-rækkefølgen - så man kunne tabbe ind i
 * et usynligt panel.
 *
 * Her holdes fokus inde i det øverste åbne lag, og fokus gives tilbage til det
 * element man kom fra, når laget lukkes. inert sættes på de lukkede paneler,
 * så de forsvinder helt ud af tab-rækkefølgen.
 */
(function () {
    const FOKUSERBARE = [
        'a[href]', 'button:not([disabled])', 'input:not([disabled])',
        'select:not([disabled])', 'textarea:not([disabled])', '[tabindex]:not([tabindex="-1"])',
    ].join(',');

    // Øverst i listen = øverst i z-index. Første match der er åben, fanger fokus.
    const LAG = [
        { id: 'auth-modal',   erAaben: el => el.classList.contains('active') },
        { id: 'cart-panel',   erAaben: el => el.classList.contains('active') },
        { id: 'settings-panel', erAaben: el => el.classList.contains('active') },
        { id: 'overlay',      erAaben: el => getComputedStyle(el).display !== 'none' },
    ];

    // Sideindholdet BAG et åbent lag. Tastaturfælden ovenfor forhindrede
    // allerede Tab i at forlade laget, men blev aldrig gjort inert - en
    // skærmlæsers browse-mode (som ikke bruger Tab) kunne stadig nå ind i
    // <main>/header/footer, mens modalen var åben (fundet under QA-audit
    // 2026-08-17). Selectorerne matcher <header>/<main>/<footer> i base.html.
    const BAGGRUND = ['header', '#nav-menu', 'main', 'footer'];

    let sidsteFokus = null;

    function aabentLag() {
        for (const lag of LAG) {
            const el = document.getElementById(lag.id);
            if (el && lag.erAaben(el)) return el;
        }
        return null;
    }

    document.addEventListener('keydown', function (e) {
        if (e.key !== 'Tab') return;
        const lag = aabentLag();
        if (!lag) return;
        const felter = Array.from(lag.querySelectorAll(FOKUSERBARE))
            .filter(el => el.offsetParent !== null || el === document.activeElement);
        if (!felter.length) return;
        const foerste = felter[0];
        const sidste = felter[felter.length - 1];
        // Staar fokus HELT uden for laget (fx fordi brugeren aabnede panelet med
        // musen), matcher hverken foerste eller sidste, og Tab ville bare loebe
        // videre ned i siden bagved. Traek det ind foerst.
        if (!lag.contains(document.activeElement)) {
            e.preventDefault();
            (e.shiftKey ? sidste : foerste).focus();
            return;
        }
        if (e.shiftKey && document.activeElement === foerste) {
            e.preventDefault();
            sidste.focus();
        } else if (!e.shiftKey && document.activeElement === sidste) {
            e.preventDefault();
            foerste.focus();
        }
    });

    // Hold inert og fokus-retur ajour, når et lag åbner eller lukker.
    const obs = new MutationObserver(function () {
        const lag = aabentLag();
        LAG.forEach(function (l) {
            const el = document.getElementById(l.id);
            if (!el) return;
            const skjult = el !== lag;
            if (skjult) el.setAttribute('inert', '');
            else el.removeAttribute('inert');
            el.setAttribute('aria-hidden', skjult ? 'true' : 'false');
        });
        BAGGRUND.forEach(function (sel) {
            const el = document.querySelector(sel);
            if (!el) return;
            if (lag) el.setAttribute('inert', '');
            else el.removeAttribute('inert');
        });
        if (lag && !lag.contains(document.activeElement)) {
            sidsteFokus = document.activeElement;
            // Panelet glider ind med en transition, saa foerste element kan
            // endnu ikke vaere fokuserbart i samme tick.
            setTimeout(function () {
                if (aabentLag() !== lag) return;
                const felter = Array.from(lag.querySelectorAll(FOKUSERBARE))
                    .filter(el => el.offsetParent !== null);
                if (felter.length) felter[0].focus();
            }, 60);
        } else if (!lag && sidsteFokus) {
            try { sidsteFokus.focus(); } catch (e) { /* elementet kan vaere vaek */ }
            sidsteFokus = null;
        }
    });

    LAG.forEach(function (l) {
        const el = document.getElementById(l.id);
        if (el) obs.observe(el, { attributes: true, attributeFilter: ['class', 'style'] });
    });
})();
