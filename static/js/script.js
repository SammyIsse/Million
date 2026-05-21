// Menu functionality
let priceHistoryChart = null;
function toggleMenu() {
    const menu = document.getElementById('nav-menu');
    const hamburger = document.querySelector('.hamburger-menu');
    const overlay = document.getElementById('menu-overlay');
    const body = document.body;

    menu.classList.toggle('active');
    hamburger.classList.toggle('active');
    overlay.classList.toggle('active');

    // Toggle body scroll
    if (menu.classList.contains('active')) {
        body.style.overflow = 'hidden';
    } else {
        body.style.overflow = '';
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
    } else {
        body.style.overflow = '';
    }
}

// Close menu and cart when clicking outside
document.addEventListener('click', function (event) {
    const menu = document.getElementById('nav-menu');
    const hamburger = document.querySelector('.hamburger-menu');
    const menuOverlay = document.getElementById('menu-overlay');
    const cartPanel = document.getElementById('cart-panel');
    const cartOverlay = document.getElementById('cart-overlay');
    const cartIcon = document.querySelector('.cart-icon');

    // Handle menu clicks
    if (menu.classList.contains('active') &&
        (event.target === menuOverlay || (!menu.contains(event.target) && !hamburger.contains(event.target)))) {
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

        if (menu.classList.contains('active')) {
            toggleMenu();
        }
        if (cartPanel.classList.contains('active')) {
            toggleCart();
        }
    }
});

// Store Filter State
// ALL_STORES is populated dynamically from /api/stores on DOMContentLoaded.
// Each entry: { key: 'bilka', label: 'Bilka', logo: '/static/images/bilka-logo.png' }
let ALL_STORES = [];
let selectedStores = new Set();

function saveStoreFilters() {
    const storesArray = Array.from(selectedStores);
    localStorage.setItem('selectedStores', JSON.stringify(storesArray));
    document.cookie = "cartspotter_stores=" + encodeURIComponent(JSON.stringify(storesArray)) + ";path=/;max-age=31536000";
    updateInternalLinks();
    if (typeof closeAutocomplete === 'function') closeAutocomplete();
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
    const internalLinks = document.querySelectorAll('.logo-link, .category-nav a, .nav-category-grid a, a[href*=".html"], a[href^="/search"], .product-type h2 a');
    
    internalLinks.forEach(link => {
        try {
            const url = new URL(link.href, window.location.origin);
            // Only modify links that are on the same domain
            if (url.origin === window.location.origin) {
                url.searchParams.set('stores', stores);
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
        
        // Store filtering is handled client-side — no server reload needed
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

            // If search results are visible, refresh them
            const searchResults = document.getElementById('searchResults');
            if (searchResults && searchResults.classList.contains('visible') && typeof performSearch === 'function') {
                performSearch();
            }

            // Update cart summary if open
            if (typeof updateCartDisplay === 'function') {
                updateCartDisplay();
            }
        });
    });

    // Apply initial visual state from selectedStores
    syncFilterButtons();
    applyStoreFilters();
    updateInternalLinks();
    syncUrlWithLocalStorage();
}

/**
 * Fetches updated content from the server based on selected stores
 * and replaces the dynamic-content container.
 */
function updateDynamicStoreContent() {
    const dynamicContainer = document.getElementById('dynamic-content');
    if (!dynamicContainer) return;

    dynamicContainer.style.opacity = '0.5';
    dynamicContainer.style.pointerEvents = 'none';

    const storesParam = Array.from(selectedStores).join(',');

    // Update the browser URL first so any subsequent filter calls use the correct stores
    const urlObj = new URL(window.location.href);
    urlObj.searchParams.set('stores', storesParam);
    urlObj.searchParams.delete('page'); // reset to page 1 when store selection changes
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
let cart = JSON.parse(localStorage.getItem('cart')) || [];
let scoByStoreOpen = false;

function toggleScoByStore() {
    scoByStoreOpen = !scoByStoreOpen;
    const btn = document.getElementById('sco-group-store-btn');
    const label = document.getElementById('sco-group-store-label');
    if (btn) btn.classList.toggle('active', scoByStoreOpen);
    if (label) label.textContent = scoByStoreOpen ? 'Skjul varer' : 'Vis varer';

    // Show or hide all sco-store-items containers
    for (let rank = 1; rank <= 5; rank++) {
        const el = document.getElementById(`sco-items-${rank}`);
        if (!el) continue;
        if (!scoByStoreOpen) { el.style.display = 'none'; continue; }
        el.style.display = 'block';
    }
    if (scoByStoreOpen) renderScoByStore();
}

function renderScoByStore() {
    const isValidPrice = (p) => p != null && !isNaN(p) && Number(p) > 0;

    // Build a map: storeName → rank (1 = winner, 2-5 = ranked)
    const rankForStore = {};
    const winnerName = (document.getElementById('sco-winner-name') || {}).textContent || '';
    if (winnerName) rankForStore[winnerName] = 1;
    for (let r = 2; r <= 5; r++) {
        const nameEl = document.getElementById(`sco-name-${r}`);
        if (nameEl && nameEl.textContent) rankForStore[nameEl.textContent] = r;
    }

    // Group cart items by cheapest selected store
    const grouped = {};
    cart.forEach(item => {
        let prices = item.storePrices;
        if (!prices) {
            prices = {};
            const legacyMap = {
                'Rema 1000': item.remaPrice, 'Bilka': item.bilkaPrice,
                'Min Købmand': item.mkPrice, 'Meny': item.menyPrice, 'Spar': item.sparPrice
            };
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
        if (!grouped[store]) grouped[store] = [];
        grouped[store].push({ item, price });
    });

    // Clear all item containers first
    for (let r = 1; r <= 5; r++) {
        const el = document.getElementById(`sco-items-${r}`);
        if (el) el.innerHTML = '';
    }

    // Populate each store's container
    for (const [store, entries] of Object.entries(grouped)) {
        const rank = rankForStore[store];
        if (!rank) continue;
        const container = document.getElementById(`sco-items-${rank}`);
        if (!container) continue;
        container.innerHTML = entries.map(({ item, price }) => `
            <div class="sco-store-item">
                <img class="sco-store-item-img" src="${escapeHtml(item.image || '')}" alt="${escapeHtml(item.name)}" onerror="this.style.display='none'">
                <span class="sco-store-item-name">${escapeHtml(stripStoreBrand(item.name))}${item.quantity > 1 ? ` <span class="sco-store-item-qty">×${item.quantity}</span>` : ''}</span>
                <span class="sco-store-item-price">${(price * item.quantity).toFixed(2)} kr</span>
            </div>`).join('');
    }
}

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
    localStorage.setItem('cart', JSON.stringify(cart));
    updateCartDisplay();
    updateCartCount();
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

    // Record popularity (fire-and-forget)
    fetch('/api/cart-event', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ product_id: productId.replace(/^product/, '') })
    }).catch(() => {});

    // Reset animations and text after delay
    setTimeout(() => {
        btn.classList.remove('clicked');
        // Restore original HTML if available
        if (btn.dataset.originalHtml) {
            btn.innerHTML = btn.dataset.originalHtml;
            delete btn.dataset.originalHtml;
        }
    }, 1000);

    // Update Personal Savings
    const prices = Object.values(storePrices).filter(p => p != null && !isNaN(p));
    if (prices.length > 1) {
        const maxPrice = Math.max(...prices);
        const saving = maxPrice - mainPrice;
        if (saving > 0) {
            addPotentialSaving(saving);
        }
    }
}

// Personal Savings Tracker Logic
let monthlySavings = parseFloat(localStorage.getItem('monthlySavings')) || 342.50;

function initSavingsTracker() {
    const widget = document.getElementById('personalSavingsWidget');
    if (!widget) return;

    if (!localStorage.getItem('monthlySavings')) {
        localStorage.setItem('monthlySavings', monthlySavings.toFixed(2));
    }
    updateSavingsDisplay();
    widget.style.display = 'flex';
}

function toggleAlertForm() {
    const form = document.getElementById('alert-form');
    if (form) {
        form.style.display = form.style.display === 'none' ? 'block' : 'none';

        // Request notification permission if not granted
        if (Notification.permission === 'default') {
            Notification.requestPermission();
        }
    }
}

async function savePriceAlert() {
    const targetPrice = parseFloat(document.getElementById('target-price-input').value);
    const productId = document.querySelector('.product-info').dataset.productId;
    const productName = document.getElementById('overlay-title').innerText;
    const currentPrice = parseFloat(document.getElementById('overlay-price-value').querySelector('.price:not(.original)')?.innerText) || 0;

    if (!targetPrice || targetPrice <= 0) {
        alert('Indtast venligst en gyldig målpris.');
        return;
    }

    try {
        const response = await fetch('/api/create-alert', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                product_id: productId,
                product_name: productName,
                target_price: targetPrice,
                current_price: currentPrice
            })
        });

        const data = await response.json();
        if (data.success) {
            const btn = document.querySelector('.alert-toggle-btn');
            btn.innerHTML = '✅ Alert sat!';
            btn.style.color = '#16A34A';
            document.getElementById('alert-form').style.display = 'none';

            // Show confirmation notification
            if (Notification.permission === 'granted') {
                new Notification('CartSpotter Alert', {
                    body: `Vi giver dig besked når ${productName} falder under ${targetPrice} kr.`,
                    icon: '/static/img/logo.png' // Ensure you have a logo or remove this
                });
            }
        }
    } catch (error) {
        console.error('Alert error:', error);
    }
}

function updateSavingsDisplay() {
    const savingsValue = document.getElementById('savingsValue');
    if (savingsValue) {
        savingsValue.textContent = monthlySavings.toLocaleString('da-DK', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }
}

function addPotentialSaving(saving) {
    if (saving > 0) {
        monthlySavings += saving;
        localStorage.setItem('monthlySavings', monthlySavings.toFixed(2));
        updateSavingsDisplay();

        const amountEl = document.querySelector('.savings-amount');
        if (amountEl) {
            amountEl.style.transform = 'scale(1.05)';
            amountEl.style.transition = 'transform 0.2s cubic-bezier(0.34, 1.56, 0.64, 1)';
            setTimeout(() => amountEl.style.transform = 'scale(1)', 200);
        }
    }
}

function removeFromCart(productId) {
    cart = cart.filter(item => item.id !== productId);
    saveCart();
}

function clearCart() {
    cart = [];
    localStorage.setItem('cart', JSON.stringify(cart));
    updateCartDisplay();
}

function updateCartCount() {
    const cartBadge = document.getElementById('cart-badge');
    if (!cartBadge) return;

    // FORCED: Read directly from localStorage
    const actualCart = JSON.parse(localStorage.getItem('cart')) || [];
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

            const allPrices = item.storePrices
                ? Object.values(item.storePrices)
                : [item.remaPrice, item.bilkaPrice, item.mkPrice, item.menyPrice, item.sparPrice];
            let unit = allPrices.find(p => isValidPrice(p)) ?? item.price ?? 0;
            if (!isValidPrice(unit)) unit = 0;
            total += unit * item.quantity;

            let extraInfo = '';
            const infoArr = [];
            if (item.unitMeasure) infoArr.push(item.unitMeasure);
            if (item.kgPrice) infoArr.push(`${item.kgPrice} kr/kg`);
            if (infoArr.length > 0) extraInfo = `<div class="cart-item-extra">${infoArr.join(' | ')}</div>`;

            const multiDealHtml = item.multiDeal ? `<div class="cart-item-multideal">${item.multiDeal}</div>` : '';

            cartItem.innerHTML = `
                <button class="delete-item-btn" onclick="deleteCartItem(${index})">&times;</button>
                <div class="cart-item-top">
                    <div class="cart-item-image">
                        <img src="${item.image}" alt="${item.name}">
                    </div>
                    <div class="cart-item-details">
                        <h4 class="cart-item-title">${stripStoreBrand(item.name)}</h4>
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
                        <div class="cart-store-name">${name}</div>
                        <div class="cart-store-total">${price.toFixed(2)} kr</div>
                    </div>`
                ).join('');
            }

            const savingsEl = document.getElementById('cart-best-savings-text');
            if (savingsEl && sorted.length >= 1) {
                if (sorted.length >= 2) {
                    const saved = sorted[sorted.length - 1][1] - sorted[0][1];
                    savingsEl.textContent = saved > 0.01
                        ? `Spar op til ${saved.toFixed(2)} kr — klik for at sammenligne`
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
    const newQuantity = cart[index].quantity + change;
    const cartItem = document.querySelector(`.cart-item[data-index="${index}"]`);

    if (newQuantity <= 0) {
        // Add fade-out animation
        if (cartItem) cartItem.classList.add('removing');

        // Wait for animation to complete before removing
        setTimeout(() => {
            cart.splice(index, 1);
            saveCart();
            updateCartDisplay();
        }, 300); // Match this with CSS animation duration
        return;
    }

    cart[index].quantity = newQuantity;
    saveCart();
    updateCartDisplay();
}

function showReference() {
    const button = document.querySelector('.show-reference-btn');

    // Prevent multiple clicks
    if (button.classList.contains('loading')) {
        return;
    }

    const cartProducts = JSON.parse(localStorage.getItem('cart')) || [];
    if (cartProducts.length === 0) {
        alert('Kurven er tom — tilføj varer før du sammenligner priser.');
        return;
    }

    // Add loading state
    button.classList.add('loading');

    const overlay = document.getElementById('store-comparison-overlay');
    const summaryEl = document.getElementById('comparison-summary');

    calculateStoreComparisons()
        .then(({ stores, linesWithoutMatches, exclusiveItems, partialItems }) => {
            const storeComparisons = stores.slice();
            storeComparisons.sort((a, b) => a.totalPrice - b.totalPrice);

            if (storeComparisons.length === 0) {
                if (summaryEl) summaryEl.textContent = 'Ingen prisdata fundet for de valgte butikker.';
                overlay.style.display = 'flex';
                document.body.style.overflow = 'hidden';
                return;
            }

            // Winner (rank 1)
            const winner = storeComparisons[0];
            const cheapestPrice = winner.totalPrice;
            const mostExpensivePrice = storeComparisons[storeComparisons.length - 1].totalPrice;
            const saving = mostExpensivePrice - cheapestPrice;

            let winnerMissingText = '';
            if (winner.missingDetails && winner.missingDetails.length > 0) {
                const missingNames = winner.missingDetails.map(d => escapeHtml(d.name)).join(', ');
                winnerMissingText = `<div style="font-size: 0.8em; color: #BA7517; margin-top: 2px; font-weight: normal;">Mangler: ${missingNames}</div>`;
            }
            
            document.getElementById('sco-winner-name').innerHTML  = `${escapeHtml(winner.name)} <span style="font-size: 0.85em; color: var(--gray-500); margin-left: 8px;">· ${winner.coverage}/${winner.totalItems} varer</span>${winnerMissingText}`;
            document.getElementById('sco-winner-price').textContent = cheapestPrice.toFixed(2) + ' kr';
            document.getElementById('sco-winner-save').textContent  =
                saving > 0.01 ? `Spar ${saving.toFixed(2)} kr ift. dyreste butik` : '';

            const winnerLogoEl = document.getElementById('sco-winner-logo');
            if (winnerLogoEl) {
                const winnerEntry = ALL_STORES.find(s => s.label === winner.name);
                if (winnerEntry) {
                    winnerLogoEl.src = winnerEntry.logo;
                    winnerLogoEl.alt = winner.name;
                    winnerLogoEl.style.display = 'block';
                }
            }

            // Hide ranks 2–5 initially, then fill
            for (let i = 2; i <= 5; i++) {
                const row = document.getElementById(`store-row-${i}`);
                if (row) row.style.display = 'none';
            }

            for (let i = 1; i < Math.min(storeComparisons.length, 5); i++) {
                const s    = storeComparisons[i];
                const rank = i + 1;
                const row  = document.getElementById(`store-row-${rank}`);
                if (!row) continue;

                const diff = s.totalPrice - cheapestPrice;
                const storeEntry = ALL_STORES.find(st => st.label === s.name);
                row.style.display = 'block';
                const logoEl = document.getElementById(`sco-logo-${rank}`);
                if (logoEl && storeEntry) { logoEl.src = storeEntry.logo; logoEl.alt = s.name; }
                let missingText = '';
                if (s.missingDetails && s.missingDetails.length > 0) {
                    const missingNames = s.missingDetails.map(d => escapeHtml(d.name)).join(', ');
                    missingText = `<div style="font-size: 0.75em; color: #BA7517; margin-top: 2px; white-space: normal; line-height: 1.2; font-weight: normal;">Mangler: ${missingNames}</div>`;
                }
                
                document.getElementById(`sco-name-${rank}`).innerHTML  = `${escapeHtml(s.name)} <span style="font-size: 0.85em; color: var(--gray-500); margin-left: 4px;">· ${s.coverage}/${s.totalItems} varer</span>${missingText}`;
                document.getElementById(`sco-diff-${rank}`).textContent  =
                    diff < 0.01 ? 'Samme pris' : `+${diff.toFixed(2)} kr`;
                document.getElementById(`sco-price-${rank}`).textContent = s.totalPrice.toFixed(2) + ' kr';
            }

            // Missing items (collapsible)
            const missingWrap  = document.getElementById('sco-missing-wrap');
            const missingBody  = document.getElementById('sco-missing-body');
            const missingLabel = document.getElementById('sco-missing-label');
            if (missingWrap && missingBody && missingLabel) {
                if (partialItems.length > 0) {
                    const n = partialItems.length;
                    missingLabel.textContent = `${n} vare${n > 1 ? 'r' : ''} mangler hos nogle butikker`;
                    missingBody.innerHTML = partialItems.map(item => `
                        <div class="sco-missing-item">
                            <img class="sco-missing-item-img"
                                 src="${escapeHtml(item.image || '')}"
                                 alt="${escapeHtml(item.name)}"
                                 onerror="this.style.display='none'">
                            <div>
                                <div class="sco-missing-item-name">${escapeHtml(item.name)}</div>
                                <div class="sco-missing-item-stores">Mangler hos: ${item.missingStores.map(s => escapeHtml(s)).join(', ')}</div>
                            </div>
                        </div>`).join('');
                    missingWrap.style.display = 'block';
                } else {
                    missingWrap.style.display = 'none';
                }
            }

            // Fetch Alternatives – deduplicate by cart_id so each product appears once
            const seenCartIds = new Set();
            const allMissingItems = [];
            storeComparisons.forEach(s => {
                if (s.missingDetails) {
                    s.missingDetails.forEach(item => {
                        if (!seenCartIds.has(item.cart_id)) {
                            seenCartIds.add(item.cart_id);
                            allMissingItems.push(item);
                        }
                    });
                }
            });

            const altContainer = document.getElementById('sco-alternatives-container');
            if (altContainer) {
                altContainer.innerHTML = '';
                altContainer.style.display = 'none';
            }

            if (allMissingItems.length > 0 && altContainer) {
                fetch('/api/alternatives', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ missing_items: allMissingItems })
                })
                .then(r => r.json())
                .then(data => {
                    if (data.success && data.alternatives && data.alternatives.length > 0) {
                        renderAlternatives(data.alternatives);
                    }
                })
                .catch(err => console.error('Error fetching alternatives:', err));
            }

            overlay.style.display = 'flex';
            document.body.style.overflow = 'hidden';
        })
        .catch(error => {
            console.error('Error calculating store comparisons:', error);
            if (summaryEl) summaryEl.textContent = 'Kunne ikke hente priser — prøv igen.';
        })
        .finally(() => {
            button.classList.remove('loading');
        });
}

function escapeHtml(text) {
    const d = document.createElement('div');
    d.textContent = text == null ? '' : String(text);
    return d.innerHTML;
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
    const body = document.getElementById('sco-missing-body');
    const btn  = document.getElementById('sco-missing-toggle');
    if (body) body.classList.remove('open');
    if (btn)  { btn.classList.remove('open'); btn.setAttribute('aria-expanded', 'false'); }
    // Reset by-store toggle
    scoByStoreOpen = false;
    const scoBtn = document.getElementById('sco-group-store-btn');
    const scoLabel = document.getElementById('sco-group-store-label');
    if (scoBtn) scoBtn.classList.remove('active');
    if (scoLabel) scoLabel.textContent = 'Vis varer';
    for (let r = 1; r <= 5; r++) {
        const el = document.getElementById(`sco-items-${r}`);
        if (el) { el.style.display = 'none'; el.innerHTML = ''; }
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
    let linesWithoutMatches = 0;
    const exclusiveItems = Object.fromEntries(allLabels.map(l => [l, []]));
    const partialItems = [];
    // We collect raw partial data first, then filter after storeTotals is complete
    const rawPartials = [];

    const cartProducts = JSON.parse(localStorage.getItem('cart')) || [];

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
            }
        }
        
        // Track missing details per store
        for (const label of selectedStores) {
            if (prices[label] == null || Number.isNaN(Number(prices[label])) || Number(prices[label]) <= 0) {
                missingDetails[label].push({
                    cart_id: cartItem.id,
                    name: stripStoreBrand(cartItem.name || 'Vare'),
                    category: cartItem.category || '',
                    weight_str: cartItem.unitMeasure || '',
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

    // Build partialItems now that storeTotals is complete — only show stores visible in comparison
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

    return { stores, linesWithoutMatches, exclusiveItems, partialItems };
}

function getProductPrice(product) {
    const salePrice = product['/product/sale_price'];
    const regularPrice = product['/product/price'];
    return salePrice && !isNaN(salePrice) ? parseFloat(salePrice) : parseFloat(regularPrice);
}

// Add event listener for ESC key to close store comparison overlay
document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') {
        const storeComparisonOverlay = document.getElementById('store-comparison-overlay');
        if (storeComparisonOverlay.style.display === 'flex') {
            closeStoreComparison();
        }
    }
});

// Close store comparison overlay when clicking outside
document.addEventListener('click', function (event) {
    const overlay = document.getElementById('store-comparison-overlay');
    const content = document.querySelector('.comparison-content');

    if (overlay.style.display === 'flex' &&
        !content.contains(event.target) &&
        event.target !== overlay) {
        closeStoreComparison();
    }
});

async function initAllStores() {
    try {
        const res  = await fetch('/api/stores');
        const data = await res.json();
        ALL_STORES = data.stores; // [{key, label, logo}, ...]
    } catch {
        ALL_STORES = [];
    }

    const allLabels = ALL_STORES.map(s => s.label);
    const urlStores = new URLSearchParams(window.location.search).get('stores');

    if (urlStores) {
        // URL takes precedence — user followed a link with an explicit store selection
        selectedStores = new Set(urlStores.split(',').filter(s => allLabels.includes(s)));
        if (selectedStores.size === 0) selectedStores = new Set(allLabels);
    } else {
        const saved = JSON.parse(localStorage.getItem('selectedStores'));
        const prevKnown = new Set(JSON.parse(localStorage.getItem('knownStores')) || []);

        if (saved && Array.isArray(saved) && saved.length > 0) {
            selectedStores = new Set(saved);
            // Only add stores that are genuinely new (never seen before)
            allLabels.forEach(label => {
                if (!prevKnown.has(label)) selectedStores.add(label);
            });
        } else {
            selectedStores = new Set(allLabels);
        }
    }

    localStorage.setItem('knownStores', JSON.stringify(allLabels));
    localStorage.setItem('selectedStores', JSON.stringify([...selectedStores]));
    saveStoreFilters();

    // Search functionality — only trigger on Enter, not on every keystroke
    const searchInput = document.getElementById('searchInput');
    if (searchInput) {
        searchInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                closeAutocomplete();
                performSearch();
            }
        });
    }

    initStoreFilters();
    updateCartDisplay();
    updateCartCount();
    attachProductEventListeners();

    const referenceBtn = document.querySelector('.show-reference-btn');
    if (referenceBtn && !referenceBtn.querySelector('.button-text')) {
        const buttonText = referenceBtn.textContent;
        referenceBtn.innerHTML = `
            <span class="button-text">${buttonText}</span>
            <div class="loading-spinner"></div>
        `;
    }

    if (typeof initAdvancedFilters === 'function') initAdvancedFilters();
    if (typeof initSavingsTracker === 'function')  initSavingsTracker();
    if (typeof initSettings === 'function')        initSettings();
    if (typeof initAutocomplete === 'function')    initAutocomplete();
    updateListsBadge();
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

function performSearch() {
    const searchInput = document.getElementById('searchInput');
    const searchResults = document.getElementById('searchResults');
    const productsContainer = searchResults.querySelector('.products');
    const searchTitle = searchResults.querySelector('.search-title');
    const query = searchInput.value.trim();

    if (searchTimeout) {
        clearTimeout(searchTimeout);
    }

    if (!query) {
        searchResults.classList.remove('visible');
        setTimeout(() => {
            searchResults.style.display = 'none';
            document.body.classList.remove('search-active');
        }, 300);
        return;
    }

    // Reset filters when starting a search
    resetAdvancedFilters();

    searchTimeout = setTimeout(() => {
        searchResults.style.display = 'block';
        searchTitle.textContent = `Søgeresultater for "${query}"`;

        const storesParam = Array.from(selectedStores).join(',');
        fetch(`/search?q=${encodeURIComponent(query)}&stores=${encodeURIComponent(storesParam)}`)
            .then(response => response.json())
            .then(data => {
                if (data.html) {
                    productsContainer.innerHTML = data.html;
                    attachProductEventListeners();

                    // Force reflow and add visibility classes
                    requestAnimationFrame(() => {
                        searchResults.classList.add('visible');
                        productsContainer.classList.add('visible');
                        document.body.classList.add('search-active');
                        applyStoreFilters();
                    });
                } else {
                    productsContainer.innerHTML = '<div class="no-results">Ingen resultater fundet</div>';
                }
            })
            .catch(error => {
                console.error('Search error:', error);
                productsContainer.innerHTML = '<div class="error">Der opstod en fejl under søgningen</div>';
            });
    }, 500);
}

// ===== AUTOCOMPLETE =====
let _acTimeout = null;
let _acIndex = -1;   // current keyboard-focused row index

function initAutocomplete() {
    const input = document.getElementById('searchInput');
    const dropdown = document.getElementById('autocomplete-dropdown');
    if (!input || !dropdown) return;

    // Input event — debounced fetch
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
    const dropdown = document.getElementById('autocomplete-dropdown');
    if (dropdown) dropdown.classList.remove('open');
    _acIndex = -1;
}

async function fetchAutocomplete(query) {
    try {
        const storesParam = Array.from(selectedStores).join(',');
        const url = `/api/autocomplete?q=${encodeURIComponent(query)}&stores=${encodeURIComponent(storesParam)}`;
        const res = await fetch(url);
        const data = await res.json();
        renderAutocomplete(data.suggestions || [], query);
    } catch (err) {
        console.error('Autocomplete fetch error:', err);
    }
}

function renderAutocomplete(suggestions, query) {
    const dropdown = document.getElementById('autocomplete-dropdown');
    const input    = document.getElementById('searchInput');
    if (!dropdown) return;

    if (suggestions.length === 0) {
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

    let html = suggestions.map((s, idx) => {
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
    html += `<div class="ac-footer" onclick="selectAutocomplete(${JSON.stringify(query)})">
        Se alle resultater for "${escHtml(query)}" →
    </div>`;

    dropdown.innerHTML = html;
    dropdown.classList.add('open');
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

        searchResults.style.display = 'none';
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
            quantity: quantity
        });
    }

    // Update Personal Savings
    const prices = Object.values(storePrices).filter(p => p != null && !isNaN(p));
    if (prices.length > 1) {
        const maxPrice = Math.max(...prices);
        const saving = (maxPrice - mainPrice) * quantity;
        if (saving > 0) {
            addPotentialSaving(saving);
        }
    }

    // Show animation on the product card and button
    productElement.classList.add('added-to-cart');
    addToCartBtn.classList.add('clicked');
    addToCartBtn.textContent = 'Tilføjet';

    // Save cart and animate overlay closing
    saveCart();

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

function renderPriceHistoryChart(productId, currentPrice, isSale) {
    const ctx = document.getElementById('priceHistoryChart').getContext('2d');
    const insightBadge = document.getElementById('price-insight-badge');
    const summaryEl = document.getElementById('history-summary');

    // Destroy previous chart if exists
    if (priceHistoryChart) {
        priceHistoryChart.destroy();
    }

    // Fetch real history from API
    fetch(`/api/price-history/${productId.replace('product', '')}`)
        .then(r => r.json())
        .then(data => {
            let labels = [];
            let prices = [];
            const todayStr = new Date().toISOString().split('T')[0];

            if (data.success && data.history && data.history.length > 0) {
                // We have real data!
                labels = data.history.map(h => {
                    const [y, m, d] = h.date.split('-');
                    return `${d}-${m}-${y}`;
                });
                prices = data.history.map(h => h.price);

                // Append or UPDATE current price
                const [ty, tm, td] = todayStr.split('-');
                const dToday = `${td}-${tm}-${ty}`;

                if (labels[labels.length - 1] === dToday) {
                    // Update today's entry to match current UI price exactly
                    prices[prices.length - 1] = currentPrice;
                } else {
                    labels.push(dToday);
                    prices.push(currentPrice);
                }
            } else {
                // No history yet, show today's price as a stable line
                const thirtyDaysAgo = new Date();
                thirtyDaysAgo.setDate(thirtyDaysAgo.getDate() - 30);
                const fallbackDate = thirtyDaysAgo.toISOString().split('T')[0];

                const [fy, fm, fd] = fallbackDate.split('-');
                const [ty, tm, td] = todayStr.split('-');

                labels = [`${fd}-${fm}-${fy}`, `${td}-${tm}-${ty}`];
                prices = [currentPrice, currentPrice];
            }

            // Determine insights based on real history
            let insightText = "Stabil pris";
            let insightClass = "";
            const avgPrice = prices.slice(0, -1).length > 0
                ? prices.slice(0, -1).reduce((a, b) => a + b, 0) / (prices.length - 1)
                : currentPrice;
            const minPrice = Math.min(...prices);

            if (currentPrice < avgPrice * 0.9) {
                insightText = "Godt tilbud!";
                insightClass = "great-deal";
            } else if (isSale && currentPrice >= avgPrice * 0.98 && prices.length > 2) {
                insightText = "Lille besparelse";
                insightClass = "fake-deal";
            }

            insightBadge.textContent = insightText;
            insightBadge.className = 'price-insight-badge ' + insightClass;

            summaryEl.textContent = prices.length > 2
                ? `Prisen har varieret mellem ${minPrice.toFixed(2).replace('.', ',')} kr. og ${Math.max(...prices).toFixed(2).replace('.', ',')} kr. de sidste 30 dage.`
                : `Vi holder øje med prisen for dig, så du ikke behøver.`;

            priceHistoryChart = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: labels,
                    datasets: [{
                        label: 'Pris (kr)',
                        data: prices,
                        borderColor: '#16A34A',
                        backgroundColor: 'rgba(22, 163, 74, 0.1)',
                        borderWidth: 3,
                        fill: true,
                        tension: 0.4,
                        pointRadius: 4,
                        pointBackgroundColor: '#16A34A'
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { display: false },
                        tooltip: {
                            backgroundColor: '#111827',
                            padding: 10,
                            callbacks: {
                                label: (context) => `Pris: ${context.parsed.y.toFixed(2).replace('.', ',')} kr`
                            }
                        }
                    },
                    scales: {
                        y: {
                            beginAtZero: false,
                            grid: { color: 'rgba(0,0,0,0.05)' },
                            ticks: {
                                stepSize: 0.5,
                                callback: (value) => value.toFixed(2).replace('.', ',') + ' kr'
                            }
                        },
                        x: {
                            grid: { display: false },
                            ticks: {
                                display: labels.length < 15,
                                maxRotation: 0,
                                autoSkip: true
                            }
                        }
                    }
                }
            });
        });
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

    // Fetch product information (non-blocking)
    const pidClean = productId.replace('product', '');
    if (pidClean) {
        fetch(`/product/${pidClean}`)
            .then(response => response.json())
            .catch(error => console.error('Error fetching product info:', error));
    }

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
    if (overlayImg) overlayImg.src = imageSrc;
    
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

    if (!hasMatch) {
        if (storeOnlyMsg) {
            var storeName = store;
            storeOnlyMsg.textContent = 'Vi har endnu ikke fundet denne vare hos andre butikker — den er foreløbigt kun tilgængelig hos ' + storeName + '.';
            storeOnlyMsg.style.display = 'block';
        }
        if (compDiv) compDiv.style.display = 'none';
        if (genericAddBtn) genericAddBtn.textContent = 'Tilføj til kurv';
    } else {
        if (storeOnlyMsg) storeOnlyMsg.style.display = 'none';

        if (compDiv) {
            // Read the main price shown on the card — it belongs to the card's own store
            var mainPriceEl = productElement.querySelector('.price.sale') || productElement.querySelector('.price:not(.sale):not(.original)');
            var mainPriceText = mainPriceEl ? mainPriceEl.innerText : '0';
            var mainCardPrice = parseFloat(mainPriceText.replace(/[^\d,.]/g, '').replace(',', '.')) || 0;
            var cardStore = productElement.dataset.store || 'Rema 1000';

            var remaKgPrice = productElement.dataset.remaKgPrice || '';
            var bilkaRaw = productElement.dataset.bilkaPrice;
            var bilkaKgPrice = productElement.dataset.bilkaKgPrice || '';
            var mkRaw = productElement.dataset.mkPrice;
            var mkKgPrice = productElement.dataset.mkKgPrice || '';
            var menyRaw = productElement.dataset.menyPrice;
            var menyKgPrice = productElement.dataset.menyKgPrice || '';
            var sparRaw = productElement.dataset.sparPrice;
            var sparKgPrice = productElement.dataset.sparKgPrice || '';

            var bilkaIsSale = productElement.dataset.bilkaIsSale === 'true';
            var mkIsSale = productElement.dataset.mkIsSale === 'true';
            var menyIsSale = productElement.dataset.menyIsSale === 'true';
            var sparIsSale = productElement.dataset.sparIsSale === 'true';
            var remaRaw = productElement.dataset.remaPrice;
            var remaIsSale = (productElement.dataset.remaIsSale === 'true') || (cardStore === 'Rema 1000' && productElement.querySelector('.price.sale') !== null);

            // Assign the card's own price to the right store column
            var rPrice = 0, bPrice = 0, mPrice = 0, mePrice = 0, sPrice = 0;
            if (cardStore === 'Bilka') {
                bPrice = mainCardPrice;
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
                'comp-mk-multideal':          productElement.dataset.mkMultideal          || '',
                'comp-meny-multideal':        productElement.dataset.menyMultideal        || '',
                'comp-spar-multideal':        productElement.dataset.sparMultideal        || '',
                'comp-sb-multideal':          productElement.dataset.sbMultideal          || '',
                'comp-brugsen-multideal':     productElement.dataset.brugsenMultideal     || '',
                'comp-kvickly-multideal':     productElement.dataset.kvicklyMultideal     || '',
                'comp-discount365-multideal': productElement.dataset.discount365Multideal || '',
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

            var cards = [
                { id: 'comp-card-rema',        price: rPrice,         badgeId: 'comp-badge-rema',        priceId: 'comp-rema-price',        name: 'Rema 1000',    isSale: remaIsSale },
                { id: 'comp-card-bilka',        price: bPrice,         badgeId: 'comp-badge-bilka',        priceId: 'comp-bilka-price',        name: 'Bilka',        isSale: bilkaIsSale },
                { id: 'comp-card-minkobmand',   price: mPrice,         badgeId: 'comp-badge-minkobmand',   priceId: 'comp-mk-price',           name: 'Min Købmand',  isSale: mkIsSale },
                { id: 'comp-card-meny',         price: mePrice,        badgeId: 'comp-badge-meny',         priceId: 'comp-meny-price',         name: 'Meny',         isSale: menyIsSale },
                { id: 'comp-card-spar',         price: sPrice,         badgeId: 'comp-badge-spar',         priceId: 'comp-spar-price',         name: 'Spar',         isSale: sparIsSale },
                { id: 'comp-card-sb',           price: sbPrice,        badgeId: 'comp-badge-sb',           priceId: 'comp-sb-price',           name: 'SuperBrugsen', isSale: sbIsSale },
                { id: 'comp-card-brugsen',      price: brugsenPrice,   badgeId: 'comp-badge-brugsen',      priceId: 'comp-brugsen-price',      name: 'Brugsen',      isSale: brugsenIsSale },
                { id: 'comp-card-kvickly',      price: kvicklyPrice,   badgeId: 'comp-badge-kvickly',      priceId: 'comp-kvickly-price',      name: 'Kvickly',      isSale: kvicklyIsSale },
                { id: 'comp-card-discount365',  price: discount365Price, badgeId: 'comp-badge-discount365', priceId: 'comp-discount365-price', name: '365 Discount', isSale: discount365IsSale },
            ];

            // Hide cards with 0 price OR unselected stores
            cards.forEach(c => {
                const isSelected = selectedStores.has(c.name);
                document.getElementById(c.id).style.display = (c.price > 0 && isSelected) ? 'flex' : 'none';
            });

            var validCards = cards.filter(c => c.price > 0 && selectedStores.has(c.name));
            validCards.sort((a, b) => a.price - b.price);

            // Get the cheapest store name for the button
            var cheapestStore = validCards.length > 0 ? validCards[0].name : 'Rema 1000';

            // Apply sorting and highlights
            validCards.forEach((c, idx) => {
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
                    var diff = c.price - validCards[0].price;
                    bEl.textContent = '+' + diff.toFixed(2) + ' kr';
                    bEl.style.background = isDark ? '#374151' : '#f1f3f4';
                    bEl.style.color   = isDark ? '#9ca3af' : '#5f6368';
                    bEl.style.display = 'block';
                }
            });

            if (genericAddBtn) genericAddBtn.textContent = 'Tilføj til kurv — ' + cheapestStore;
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

    // Render Price History Chart
    const currentPriceVal = parseFloat(mainCardPrice) || 0;
    const isActuallyOnSale = (salePriceElement !== null);

    // Store IDs for history switching
    const storeIds = {
        'Rema 1000': productElement.dataset.remaId,
        'Bilka': productElement.dataset.bilkaId,
        'Min Købmand': productElement.dataset.mkId,
        'Meny': productElement.dataset.menyId,
        'Spar': productElement.dataset.sparId
    };

    // Store prices for the chart logic
    const storePrices = {
        'Rema 1000':    { price: rPrice,          isSale: remaIsSale },
        'Bilka':        { price: bPrice,          isSale: bilkaIsSale },
        'Min Købmand':  { price: mPrice,          isSale: mkIsSale },
        'Meny':         { price: mePrice,         isSale: menyIsSale },
        'Spar':         { price: sPrice,          isSale: sparIsSale },
        'SuperBrugsen': { price: sbPrice,         isSale: sbIsSale },
        'Brugsen':      { price: brugsenPrice,    isSale: brugsenIsSale },
        'Kvickly':      { price: kvicklyPrice,    isSale: kvicklyIsSale },
        '365 Discount': { price: discount365Price, isSale: discount365IsSale },
    };

    // Default to cheapest store's history
    const defaultStore = validCards.length > 0 ? validCards[0].name : cardStore;
    const defaultId = storeIds[defaultStore] || productId;
    const defaultPrice = storePrices[defaultStore].price || currentPriceVal;
    const defaultSale = storePrices[defaultStore].isSale;

    renderPriceHistoryChart(defaultId, defaultPrice, defaultSale);

    // Setup Click Listeners for store cards to switch history
    cards.forEach(c => {
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
                const sId = storeIds[c.name] || productId;
                renderPriceHistoryChart(sId, c.price, c.isSale);

                // Update the main add-to-cart button text
                if (genericAddBtn) genericAddBtn.textContent = 'Tilføj til kurv — ' + c.name;
            };
        }
    });

    // Show overlay
    const overlayEl = document.getElementById('overlay');
    overlayEl.style.display = 'flex';
    overlayEl.style.alignItems = 'center';
    overlayEl.style.justifyContent = 'center';
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
    if (productOverlay.style.display === 'block' && event.target === productOverlay) {
        closeOverlay();
    }

    // Handle store comparison overlay
    if (storeOverlay.style.display === 'flex') {
        const content = storeOverlay.querySelector('.comparison-content');
        if (!content.contains(event.target)) {
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

// Add pagination handler
window.loadPage = function (page) {
    const query = new URLSearchParams(window.location.search).get('q') || '';
    fetch(`/search?q=${encodeURIComponent(query)}&page=${page}`, {
        headers: { 'X-Requested-With': 'XMLHttpRequest' }
    })
        .then(response => response.json())
        .then(data => {
            document.querySelector('.products').innerHTML = data.html;
            window.scrollTo({ top: 0, behavior: 'smooth' });
            attachProductEventListeners();
        });
};

function deleteCartItem(index) {
    const cartItem = document.querySelector(`.cart-item[data-index="${index}"]`);
    if (cartItem) cartItem.classList.add('removing');

    setTimeout(() => {
        cart.splice(index, 1);
        saveCart();
        updateCartDisplay();
    }, 300);
}

function clearCart() {
    cart = [];
    saveCart();
    updateCartDisplay();
}


// Advanced Filtering Logic
function updatePriceLabel(value) {
    const label = document.getElementById('priceLimitLabel');
    if (label) label.textContent = value + ' kr';
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

    const filterIds = [
        'sortSelect', 'minPrice', 'maxPrice', 'saleFilter',
        'organicFilter', 'lactoseFilter', 'minWeight', 'maxWeight'
    ];

    // Path tracking for reset
    const currentPath = window.location.pathname;
    const lastPath = sessionStorage.getItem('lastFilterPath');

    if (lastPath && lastPath !== currentPath) {
        // Category changed, clear saved filters
        filterIds.forEach(id => sessionStorage.removeItem(`filter_${id}`));
    }
    sessionStorage.setItem('lastFilterPath', currentPath);

    // Load saved filters
    filterIds.forEach(id => {
        const savedValue = sessionStorage.getItem(`filter_${id}`);
        if (savedValue !== null) {
            const elements = document.querySelectorAll(`#${id}`);
            elements.forEach(el => {
                if (el.type === 'checkbox') {
                    el.checked = savedValue === 'true';
                } else {
                    el.value = savedValue;
                }
            });
        }
    });

    filterIds.forEach(id => {
        const elements = document.querySelectorAll(`#${id}`);
        elements.forEach(el => {
            el.addEventListener('change', () => {
                const val = el.type === 'checkbox' ? el.checked : el.value;
                sessionStorage.setItem(`filter_${id}`, val);
                syncFilterElements(id, val);
                applyAllFilters();
            });
            if (el.tagName === 'INPUT' && (el.type === 'number' || el.type === 'text')) {
                el.addEventListener('input', () => {
                    const val = el.value;
                    sessionStorage.setItem(`filter_${id}`, val);
                    syncFilterElements(id, val);
                    applyAllFilters();
                });
            }
        });
    });

    // Run filters on load if we have saved values
    applyAllFilters(true);

    function syncFilterElements(id, value) {
        const elements = document.querySelectorAll(`#${id}`);
        elements.forEach(el => {
            if (el.type === 'checkbox') {
                el.checked = value;
            } else {
                el.value = value;
            }
        });
    }

    const resetBtns = document.querySelectorAll('#resetFilters, .filter-reset-btn');
    resetBtns.forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.preventDefault();
            resetAdvancedFilters();
        });
    });

    const toggleBtns = document.querySelectorAll('.advanced-filters-toggle');
    toggleBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            const container = btn.nextElementSibling; // The .advanced-filters div
            if (container && container.classList.contains('advanced-filters')) {
                container.classList.toggle('active');
                btn.classList.toggle('active');
            }
        });
    });

    // Close filters when clicking outside
    document.addEventListener('click', (event) => {
        const activeToggles = document.querySelectorAll('.advanced-filters-toggle.active');
        activeToggles.forEach(btn => {
            const container = btn.nextElementSibling;
            if (container &&
                !btn.contains(event.target) &&
                !container.contains(event.target)) {
                container.classList.remove('active');
                btn.classList.remove('active');
            }
        });
    });
}

function resetAdvancedFilters() {
    const filterIds = [
        'sortSelect', 'minPrice', 'maxPrice', 'saleFilter',
        'organicFilter', 'lactoseFilter'
    ];

    filterIds.forEach(id => sessionStorage.removeItem(`filter_${id}`));

    document.querySelectorAll('#sortSelect').forEach(el => el.value = 'relevance');
    document.querySelectorAll('#minPrice').forEach(el => el.value = '');
    document.querySelectorAll('#maxPrice').forEach(el => el.value = '');
    document.querySelectorAll('#saleFilter').forEach(el => el.checked = false);
    document.querySelectorAll('#organicFilter').forEach(el => el.checked = false);
    document.querySelectorAll('#lactoseFilter').forEach(el => el.checked = false);

    // Immediate update and reset to page 1
    const url = new URL(window.location.href);
    url.searchParams.delete('page');
    window.history.pushState({}, '', url.toString());

    applyAllFilters(false, true); // false for isInitialLoad, true for immediate
}

let filterTimeout;
function applyAllFilters(isInitialLoad = false, isImmediate = false) {
    clearTimeout(filterTimeout);

    const run = () => {
        const sort = document.getElementById('sortSelect')?.value || 'relevance';
        const minPrice = document.getElementById('minPrice')?.value || '';
        const maxPrice = document.getElementById('maxPrice')?.value || '';
        const sale = document.getElementById('saleFilter')?.checked;
        const organic = document.getElementById('organicFilter')?.checked;
        const lactose = document.getElementById('lactoseFilter')?.checked;
        const minWeight = document.getElementById('minWeight')?.value || '';
        const maxWeight = document.getElementById('maxWeight')?.value || '';

        // Collect params, preserving existing ones like 'stores'
        const params = new URLSearchParams(window.location.search);
        
        // Inject current selectedStores into params
        if (typeof selectedStores !== 'undefined' && selectedStores.size > 0) {
            params.set('stores', Array.from(selectedStores).join(','));
        } else {
            params.delete('stores');
        }
        
        // Remove old pagination when filter changes manually
        if (!isInitialLoad) params.delete('page');
        if (sort && sort !== 'relevance') params.set('sort', sort);
        else params.delete('sort');
        
        if (minPrice) params.set('min_price', minPrice);
        else params.delete('min_price');
        
        if (maxPrice) params.set('max_price', maxPrice);
        else params.delete('max_price');
        
        if (sale) params.set('sale', 'true');
        else params.delete('sale');
        
        if (organic) params.set('organic', 'true');
        else params.delete('organic');
        
        if (lactose) params.set('lactose', 'true');
        else params.delete('lactose');
        
        if (minWeight) params.set('min_weight', minWeight);
        else params.delete('min_weight');

        if (maxWeight) params.set('max_weight', maxWeight);
        else params.delete('max_weight');

        // Subcategory is managed by the pill bar — preserve if present
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
        const isCategoryPage = window.location.pathname.endsWith('.html') && !window.location.pathname.endsWith('index.html');
        const isSearchPage = window.location.pathname.includes('/search');

        if (isHomePage || isCategoryPage || isSearchPage) {
            // Global Server-side filtering
            const baseUrl = window.location.pathname || '/';
            const fullUrl = `${baseUrl}?${params.toString()}`;

            // Update URL without reload
            window.history.pushState({}, '', fullUrl);

            // Show loading state
            const dynamicContent = document.getElementById('dynamic-content');
            if (dynamicContent) dynamicContent.style.opacity = '0.5';

            fetch(fullUrl, {
                headers: { 'X-Requested-With': 'XMLHttpRequest' }
            })
                .then(r => r.text())
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
    } else {
        panel.classList.add('active');
        overlay.classList.add('active');
        // Always refresh checkboxes to reflect any changes made via frontpage buttons
        syncSettingsCheckboxes();
    }
}

function initSettings() {
    // Load Dark Mode
    const isDark = localStorage.getItem('cartspotter_darkmode') === 'true';
    if (isDark) {
        document.body.setAttribute('data-theme', 'dark');
        const toggle = document.getElementById('darkModeToggle');
        if (toggle) toggle.checked = true;
    }

    // Sync settings checkboxes and filter buttons from current selectedStores
    // (already correctly restored by initAllStores — do not override)
    syncSettingsCheckboxes();
    syncFilterButtons();
    // Do NOT call applyFilters() here — initAdvancedFilters handles the initial
    // product load and preserves the current page number. Calling applyFilters()
    // with isInitialLoad=false would delete the page param and reset to page 1.

    // Load Misc Settings
    const pushState = localStorage.getItem('cartspotter_push') === 'true';
    const emailState = localStorage.getItem('cartspotter_email') === 'true';
    if (document.getElementById('pushToggle')) document.getElementById('pushToggle').checked = pushState;
    if (document.getElementById('emailToggle')) document.getElementById('emailToggle').checked = emailState;
}

function toggleDarkMode() {
    const isDark = document.getElementById('darkModeToggle').checked;
    if (isDark) {
        document.body.setAttribute('data-theme', 'dark');
        localStorage.setItem('cartspotter_darkmode', 'true');
    } else {
        document.body.removeAttribute('data-theme');
        localStorage.setItem('cartspotter_darkmode', 'false');
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

    localStorage.setItem('cartspotter_stores', JSON.stringify(defaults));

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
    if (searchResults && searchResults.classList.contains('visible') && typeof performSearch === 'function') {
        performSearch();
    }
}

function saveMiscSettings() {
    const push = document.getElementById('pushToggle').checked;
    const email = document.getElementById('emailToggle').checked;
    localStorage.setItem('cartspotter_push', push ? 'true' : 'false');
    localStorage.setItem('cartspotter_email', email ? 'true' : 'false');
}

// Ensure initSettings is called on DOM load

// ── Saved Lists ─────────────────────────────────────────────────────────────

function getSavedLists() {
    return JSON.parse(localStorage.getItem('savedLists')) || [];
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
        // restore clear button visibility based on cart state
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

function saveCurrentCartAsList() {
    if (cart.length === 0) return;
    const name = prompt('Giv listen et navn:', 'Ugens kurv');
    if (!name || !name.trim()) return;

    const lists = getSavedLists();
    lists.unshift({
        id: Date.now().toString(),
        name: name.trim(),
        createdAt: new Date().toLocaleDateString('da-DK'),
        items: JSON.parse(JSON.stringify(cart))
    });
    localStorage.setItem('savedLists', JSON.stringify(lists));
    updateListsBadge();

    // Switch to lists tab to confirm
    switchCartTab('lists');
}

function loadSavedList(id) {
    const list = getSavedLists().find(l => l.id === id);
    if (!list) return;
    cart = JSON.parse(JSON.stringify(list.items));
    saveCart();
    switchCartTab('cart');
}

function deleteSavedList(id) {
    const lists = getSavedLists().filter(l => l.id !== id);
    localStorage.setItem('savedLists', JSON.stringify(lists));
    updateListsBadge();
    renderSavedLists();
}

function updateListsBadge() {
    const badge = document.getElementById('lists-count-badge');
    if (!badge) return;
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

    const lists = getSavedLists();
    if (lists.length === 0) {
        container.innerHTML = `
            <div class="saved-lists-empty">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2"/>
                    <rect x="9" y="3" width="6" height="4" rx="1"/>
                    <line x1="9" y1="12" x2="15" y2="12"/><line x1="9" y1="16" x2="13" y2="16"/>
                </svg>
                <p>Ingen gemte lister endnu</p>
                <span>Gem din kurv som en liste for nemt at genbruge den</span>
            </div>`;
        return;
    }

    container.innerHTML = lists.map(list => `
        <div class="saved-list-item">
            <div class="saved-list-info">
                <span class="saved-list-name">${escapeHtml(list.name)}</span>
                <span class="saved-list-meta">${list.items.length} varer &middot; ${list.createdAt}</span>
            </div>
            <div class="saved-list-actions">
                <button class="saved-list-load-btn" onclick="loadSavedList('${list.id}')">Indl├ªs</button>
                <button class="saved-list-delete-btn" onclick="deleteSavedList('${list.id}')" aria-label="Slet liste">&times;</button>
            </div>
        </div>`).join('');
}



function renderAlternatives(alternatives) {
    const container = document.getElementById('sco-alternatives-container');
    if (!container) return;

    let html = `<div class="sco-alternatives-header">Foreslåede alternativer til manglende varer</div>`;
    
    alternatives.forEach(alt => {
        const altData = JSON.stringify(alt).replace(/"/g, '&quot;');
        html += `
            <div class="sco-alternative-card">
                <div class="sco-alt-info">
                    <img src="${escapeHtml(alt.alt_image)}" alt="${escapeHtml(alt.alt_name)}" onerror="this.style.display='none'">
                    <div>
                        <div class="sco-alt-store">${escapeHtml(alt.store)} mangler vare</div>
                        <div class="sco-alt-name">Brug <strong>${escapeHtml(alt.alt_name)}</strong> til ${alt.alt_price.toFixed(2)} kr i stedet?</div>
                    </div>
                </div>
                <button class="sco-alt-accept-btn" onclick="acceptAlternative('${escapeHtml(alt.cart_id)}', ${altData})">Accepter for alle</button>
            </div>
        `;
    });
    
    container.innerHTML = html;
    container.style.display = 'block';
}

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
