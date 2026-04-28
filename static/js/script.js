// Menu functionality
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

        if (menu.classList.contains('active')) {
            toggleMenu();
        }
        if (cartPanel.classList.contains('active')) {
            toggleCart();
        }
    }
});

// Store Filter State
let selectedStores = new Set(JSON.parse(localStorage.getItem('selectedStores')) || ['Rema 1000', 'Bilka', 'Meny', 'Spar', 'Min Købmand']);

function saveStoreFilters() {
    localStorage.setItem('selectedStores', JSON.stringify(Array.from(selectedStores)));
}

function initStoreFilters() {
    const filterButtons = document.querySelectorAll('.store-filter-btn');
    if (filterButtons.length === 0) {
        // Even if no buttons, we should still apply filters (for category pages)
        applyStoreFilters();
        return;
    }

    filterButtons.forEach(btn => {
        const store = btn.dataset.store;

        // Initial state from localStorage
        if (!selectedStores.has(store)) {
            btn.classList.add('inactive');
        }

        btn.addEventListener('click', () => {
            if (selectedStores.has(store)) {
                if (selectedStores.size > 1) { // Prevent unselecting all
                    selectedStores.delete(store);
                    btn.classList.add('inactive');
                }
            } else {
                selectedStores.add(store);
                btn.classList.remove('inactive');
            }
            saveStoreFilters();

            // Trigger server-side update for "tilfældige varer" and filled gaps
            updateDynamicStoreContent();

            // If search results are visible, refresh them to reflect new store selection
            const searchResults = document.getElementById('searchResults');
            if (searchResults && searchResults.classList.contains('visible') && typeof performSearch === 'function') {
                performSearch();
            }

            // Also update cart summary if open
            if (typeof updateCartDisplay === 'function') {
                updateCartDisplay();
            }
        });
    });

    // Initial apply for UI state
    applyStoreFilters();
}

/**
 * Fetches updated content from the server based on selected stores
 * and replaces the dynamic-content container.
 */
function updateDynamicStoreContent() {
    const dynamicContainer = document.getElementById('dynamic-content');
    if (!dynamicContainer) return;

    // Show loading state (optional)
    dynamicContainer.style.opacity = '0.5';
    dynamicContainer.style.pointerEvents = 'none';

    const storesParam = Array.from(selectedStores).join(',');
    const url = new URL(window.location.href);
    url.searchParams.set('stores', storesParam);

    fetch(url, {
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

            // The server might return a partial (index_products.html) or full page
            // We look for dynamic-content in the response
            let newContent = doc.getElementById('dynamic-content');

            if (newContent) {
                dynamicContainer.innerHTML = newContent.innerHTML;
            } else {
                // Fallback if the partial doesn't have the ID or it's a raw partial
                dynamicContainer.innerHTML = html;
            }

            // Re-attach listeners for new products
            if (typeof attachProductEventListeners === 'function') {
                attachProductEventListeners();
                if (typeof applyAllFilters === 'function') applyAllFilters();
            }

            // Reset styles
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
        // Normalize Min Købmand naming variations
        if (store === 'Min Koebmand') store = 'Min Købmand';

        if (selectedStores.has(store)) {
            p.classList.remove('store-hidden');
        } else {
            p.classList.add('store-hidden');
        }
    });
}

// Cart functionality with localStorage
let cart = JSON.parse(localStorage.getItem('cart')) || [];

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

    let remaPrice = null;
    let bilkaPrice = null;
    let mkPrice = null;
    let menyPrice = null;
    let sparPrice = null;

    const store = productElement.dataset.store || 'Rema 1000';

    // The main price shown on the card belongs to the store that "owns" the card
    if (store === 'Rema 1000') {
        remaPrice = mainPrice;
    } else if (store === 'Bilka') {
        bilkaPrice = mainPrice;
    } else if (store === 'Min Købmand' || store === 'Min Koebmand') {
        mkPrice = mainPrice;
    } else if (store === 'Meny') {
        menyPrice = mainPrice;
    } else if (store === 'Spar') {
        sparPrice = mainPrice;
    } else {
        remaPrice = mainPrice; // Fallback
    }

    const bilkaRaw = productElement.dataset.bilkaPrice;
    if (bilkaRaw !== undefined && bilkaRaw !== '') {
        const p = parseFloat(String(bilkaRaw).replace(',', '.'));
        if (!Number.isNaN(p)) bilkaPrice = p;
    }

    const mkRaw = productElement.dataset.mkPrice;
    if (mkRaw !== undefined && mkRaw !== '') {
        const p = parseFloat(String(mkRaw).replace(',', '.'));
        if (!Number.isNaN(p)) mkPrice = p;
    }

    const menyRaw = productElement.dataset.menyPrice;
    if (menyRaw !== undefined && menyRaw !== '') {
        const p = parseFloat(String(menyRaw).replace(',', '.'));
        if (!Number.isNaN(p)) menyPrice = p;
    }

    const sparRaw = productElement.dataset.sparPrice;
    if (sparRaw !== undefined && sparRaw !== '') {
        const p = parseFloat(String(sparRaw).replace(',', '.'));
        if (!Number.isNaN(p)) sparPrice = p;
    }

    const remaRaw = productElement.dataset.remaPrice;
    if (remaRaw !== undefined && remaRaw !== '') {
        const p = parseFloat(String(remaRaw).replace(',', '.'));
        if (!Number.isNaN(p)) remaPrice = p;
    }

    return { remaPrice, bilkaPrice, mkPrice, menyPrice, sparPrice, mainPrice };
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
    const { remaPrice, bilkaPrice, mkPrice, menyPrice, sparPrice, mainPrice } = parsed;
    const image = productElement.querySelector('.product-image').src;
    const category = productElement.dataset.category || 'Andre varer';
    const unitMeasure = productElement.dataset.remaWeight || '';
    const kgPrice = productElement.dataset.remaKgPrice || '';
    const store = productElement.dataset.store || 'Rema 1000';

    // Check if product already exists in cart
    const existingItem = cart.find(item => item.id === productId);

    if (existingItem) {
        existingItem.quantity += 1;
    } else {
        cart.push({
            id: productId,
            name: name,
            store: store,
            price: mainPrice, // Store the primary visible price as fallback
            remaPrice: remaPrice,
            bilkaPrice: bilkaPrice,
            mkPrice: mkPrice,
            menyPrice: menyPrice,
            sparPrice: sparPrice,
            image: image,
            category: category,
            unitMeasure: unitMeasure,
            kgPrice: kgPrice,
            quantity: 1
        });
    }

    // Show animations and change text
    productElement.classList.add('added-to-cart');
    addToCartBtn.classList.add('clicked');
    const originalText = addToCartBtn.textContent;
    addToCartBtn.textContent = 'Tilføjet';

    // Save cart
    saveCart();

    // Reset animations and text after delay
    setTimeout(() => {
        productElement.classList.remove('added-to-cart');
        addToCartBtn.classList.remove('clicked');
        addToCartBtn.textContent = originalText;
    }, 1000);
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

    console.log('BADGE UPDATE (localStorage):', totalItems, JSON.stringify(actualCart));

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

    // Group items by category
    const groupedCart = {};
    cart.forEach((item, index) => {
        const cat = item.category || 'Andre varer';
        if (!groupedCart[cat]) groupedCart[cat] = [];
        groupedCart[cat].push({ ...item, originalIndex: index });
    });

    for (const [category, items] of Object.entries(groupedCart)) {
        // Create category header
        const catHeader = document.createElement('h3');
        catHeader.className = 'cart-category-header';
        catHeader.textContent = category;
        cartItems.appendChild(catHeader);

        items.forEach(item => {
            const index = item.originalIndex;
            const cartItem = document.createElement('div');
            cartItem.className = 'cart-item';
            cartItem.dataset.index = index;

            // Calculate item total using the valid primary price or rema as default
            let unitRema = item.remaPrice;
            if (unitRema == null) unitRema = item.bilkaPrice;
            if (unitRema == null) unitRema = item.mkPrice;
            if (unitRema == null) unitRema = item.menyPrice;
            if (unitRema == null) unitRema = item.sparPrice;
            if (unitRema == null) unitRema = item.price;

            const itemTotal = unitRema * item.quantity;
            total += itemTotal;

            let extraInfo = '';
            let weightText = item.unitMeasure ? `${item.unitMeasure}` : '';
            let kgPriceText = item.kgPrice ? `${item.kgPrice} kr/kg` : '';
            let infoArr = [];
            if (weightText) infoArr.push(weightText);
            if (kgPriceText) infoArr.push(kgPriceText);

            if (infoArr.length > 0) {
                // Not using escapeHtml directly to avoid scoping issues with hoisting, 
                // but since it's just plain numbers/text from dataset, it's safe.
                extraInfo = `<div class="cart-item-extra">${infoArr.join(' | ')}</div>`;
            }

            cartItem.innerHTML = `
                <button class="delete-item-btn" onclick="deleteCartItem(${index})">&times;</button>
                <div class="cart-item-top">
                    <div class="cart-item-image">
                        <img src="${item.image}" alt="${item.name}">
                    </div>
                    <div class="cart-item-details">
                        <h4 class="cart-item-title">${item.name}</h4>
                        ${extraInfo}
                        <div class="cart-item-price">${unitRema.toFixed(2)} kr</div>
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

    // Show/hide cart footer
    const footerSection = document.getElementById('cart-footer-section');
    const storeGrid = document.getElementById('cart-store-grid'); // may be null if removed
    if (footerSection) {
        if (cart.length === 0) {
            footerSection.style.display = 'none';
            // Show empty state
            if (!cartItems.querySelector('.cart-empty')) {
                const emptyDiv = document.createElement('div');
                emptyDiv.className = 'cart-empty';
                emptyDiv.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M6 2L3 6v14a2 2 0 002 2h14a2 2 0 002-2V6l-3-4z"/><line x1="3" y1="6" x2="21" y2="6"/><path d="M16 10a4 4 0 01-8 0"/></svg><p>Din kurv er tom</p><button class="cart-empty-btn" onclick="toggleCart()">Start indkøb</button>`;
                cartItems.appendChild(emptyDiv);
            }
        } else {
            footerSection.style.display = 'flex';
            // Build store summary
            const stores = {};
            cart.forEach(item => {
                let rp = item.remaPrice;
                let bp = item.bilkaPrice;
                let mp = item.mkPrice;
                let mep = item.menyPrice;
                let sp = item.sparPrice;

                // If an item has NO prices recorded, fallback to item.price for Rema
                // (should rarely happen with new parsing)
                if (rp == null && bp == null && mp == null && mep == null && sp == null) {
                    rp = item.price;
                }

                if (rp != null) { stores['Rema 1000'] = (stores['Rema 1000'] || 0) + rp * item.quantity; }
                if (bp != null) { stores['Bilka'] = (stores['Bilka'] || 0) + bp * item.quantity; }
                if (mp != null) { stores['Min Købmand'] = (stores['Min Købmand'] || 0) + mp * item.quantity; }
                if (mep != null) { stores['Meny'] = (stores['Meny'] || 0) + mep * item.quantity; }
                if (sp != null) { stores['Spar'] = (stores['Spar'] || 0) + sp * item.quantity; }
            });
            const sorted = Object.entries(stores)
                .filter(([name]) => selectedStores.has(name))
                .sort((a, b) => a[1] - b[1]);
            storeGrid.innerHTML = sorted.map(([name, price], i) =>
                `<div class="cart-store-box${i === 0 ? ' winner' : ''}">
                    <div class="cart-store-name">${name}</div>
                    <div class="cart-store-total">${price.toFixed(2)} kr</div>
                </div>`
            ).join('');
            const savingsEl = document.getElementById('cart-best-savings-text');
            if (savingsEl && sorted.length >= 2) {
                const diff = sorted[sorted.length - 1][1] - sorted[0][1];
                savingsEl.textContent = `Spar ${diff.toFixed(2)} kr hos ${sorted[0][0]}`;
            } else if (savingsEl && sorted.length === 1) {
                savingsEl.textContent = `Alle varer hos ${sorted[0][0]}`;
            }
        }
    }

    // Update cart count
    updateCartCount();
}

let pendingRemovalIndex = null;
let pendingProductTitle = '';

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

function saveCart() {
    localStorage.setItem('cart', JSON.stringify(cart));
    updateCartDisplay();
    updateCartCount();
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
        .then(({ stores, linesWithoutMatches, remaOnlyItems, bilkaOnlyItems, mkOnlyItems }) => {
            const storeComparisons = stores.slice();
            storeComparisons.sort((a, b) => a.totalPrice - b.totalPrice);

            // Hide all rows initially
            for (let i = 1; i <= 3; i++) {
                const row = document.getElementById(`store-row-${i}`);
                if (row) {
                    row.style.display = 'none';
                    const rank = row.previousElementSibling;
                    if (rank && rank.classList.contains('rank-row')) rank.style.display = 'none';
                }
            }

            for (let i = 0; i < Math.min(storeComparisons.length, 3); i++) {
                const store = storeComparisons[i];
                const rowElement = document.getElementById(`store-row-${i + 1}`);
                if (!rowElement) continue;

                rowElement.style.display = 'flex';
                const rank = rowElement.previousElementSibling;
                if (rank && rank.classList.contains('rank-row')) rank.style.display = 'block';

                const logoImg = rowElement.querySelector('.store-logo');
                let logoName = 'Rema1000-logo.png';
                if (store.name === 'Bilka') logoName = 'bilka-logo.png';
                if (store.name === 'Min Købmand') logoName = 'Min_kobmand_logo.png';
                if (store.name === 'Meny') logoName = 'meny-logo.png';
                if (store.name === 'Spar') logoName = 'spar-logo.png';
                logoImg.src = `/static/images/${logoName}`;

                rowElement.querySelector('.store-name').textContent = store.name;
                rowElement.querySelector('.store-price').textContent = `${store.totalPrice.toFixed(2)} kr`;
            }

            const slot1 = document.getElementById('store-exclusive-slot-1');
            const slot2 = document.getElementById('store-exclusive-slot-2');
            const slot3 = document.getElementById('store-exclusive-slot-3');

            const getExclusives = (name) => {
                if (name === 'Rema 1000') return remaOnlyItems;
                if (name === 'Bilka') return bilkaOnlyItems;
                if (name === 'Min Købmand') return mkOnlyItems;
                if (name === 'Meny') return menyOnlyItems;
                if (name === 'Spar') return sparOnlyItems;
                return [];
            };

            if (slot1) {
                const name = storeComparisons[0]?.name;
                slot1.innerHTML = buildExclusiveSlotHtml(`Kun hos ${name}:`, getExclusives(name));
                slot1.hidden = !slot1.innerHTML.trim();
            }
            if (slot2) {
                const name = storeComparisons[1]?.name;
                slot2.innerHTML = buildExclusiveSlotHtml(`Kun hos ${name}:`, getExclusives(name));
                slot2.hidden = !slot2.innerHTML.trim();
            }
            if (slot3) {
                const name = storeComparisons[2]?.name;
                slot3.innerHTML = buildExclusiveSlotHtml(`Kun hos ${name}:`, getExclusives(name));
                slot3.hidden = !slot3.innerHTML.trim();
            }

            if (summaryEl) {
                if (bTotal <= 0 && linesWithoutBilka > 0) {
                    summaryEl.textContent =
                        `Samlet Rema 1000: ${rTotal.toFixed(2)} kr. Ingen Bilka-pris for disse varer — kun Rema kan vises.`;
                } else if (linesWithoutBilka > 0) {
                    summaryEl.textContent =
                        `Rema 1000: ${rTotal.toFixed(2)} kr · Bilka: ${bTotal.toFixed(2)} kr. Bemærk: ${linesWithoutBilka} varer ikke findes i de øvrige butikker, så deres samlede pris dækker kun de varer, der kan sammenlignes.`;
                } else if (Math.abs(rTotal - bTotal) < 0.01) {
                    summaryEl.textContent =
                        `Samme pris i begge butikker: ${rTotal.toFixed(2)} kr for hele kurven.`;
                } else {
                    const cheapest = storeComparisons[0];
                    const other = storeComparisons[1];
                    const diff = Math.abs(other.totalPrice - cheapest.totalPrice);
                    summaryEl.textContent =
                        `${cheapest.name} er ${diff.toFixed(2)} kr billigere end ${other.name} (Rema 1000: ${rTotal.toFixed(2)} kr · Bilka: ${bTotal.toFixed(2)} kr).`;
                }
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
    const overlay = document.getElementById('store-comparison-overlay');
    overlay.style.display = 'none';
    document.body.style.overflow = '';
    const summaryEl = document.getElementById('comparison-summary');
    if (summaryEl) summaryEl.textContent = '';
    const slot1 = document.getElementById('store-exclusive-slot-1');
    const slot2 = document.getElementById('store-exclusive-slot-2');
    if (slot1) {
        slot1.innerHTML = '';
        slot1.hidden = true;
    }
    if (slot2) {
        slot2.innerHTML = '';
        slot2.hidden = true;
    }
}

async function calculateStoreComparisons() {
    const stores = [
        { name: 'Rema 1000', totalPrice: 0 },
        { name: 'Bilka', totalPrice: 0 },
        { name: 'Min Købmand', totalPrice: 0 },
        { name: 'Meny', totalPrice: 0 },
        { name: 'Spar', totalPrice: 0 }
    ];
    let linesWithoutMatches = 0;
    const remaOnlyItems = [];
    const bilkaOnlyItems = [];
    const mkOnlyItems = [];
    const menyOnlyItems = [];
    const sparOnlyItems = [];

    const cartProducts = JSON.parse(localStorage.getItem('cart')) || [];

    // Check if Rema is selected
    const remaSelected = selectedStores.has('Rema 1000');
    const bilkaSelected = selectedStores.has('Bilka');
    const mkSelected = selectedStores.has('Min Købmand');
    const menySelected = selectedStores.has('Meny');
    const sparSelected = selectedStores.has('Spar');

    let remaMap = null;
    try {
        const response = await fetch('/api/products');
        const data = await response.json();
        if (data.success) {
            remaMap = new Map(
                data.rema_products.map(p => [String(p['/product/id']), p])
            );
        } else {
            console.error('Failed to get products:', data.error);
        }
    } catch (error) {
        console.error('Error calculating prices:', error);
    }

    cartProducts.forEach(cartItem => {
        const productId = String(cartItem.id.replace('product', ''));
        const quantity = cartItem.quantity;
        const itemStore = cartItem.store || null; // Saved since the latest fix

        let remaPrice =
            cartItem.remaPrice != null && !Number.isNaN(Number(cartItem.remaPrice))
                ? Number(cartItem.remaPrice)
                : null;

        let bilkaPrice =
            cartItem.bilkaPrice != null && cartItem.bilkaPrice !== '' && !Number.isNaN(Number(cartItem.bilkaPrice))
                ? Number(cartItem.bilkaPrice)
                : null;

        let mkPrice =
            cartItem.mkPrice != null && cartItem.mkPrice !== '' && !Number.isNaN(Number(cartItem.mkPrice))
                ? Number(cartItem.mkPrice)
                : null;

        let menyPrice =
            cartItem.menyPrice != null && cartItem.menyPrice !== '' && !Number.isNaN(Number(cartItem.menyPrice))
                ? Number(cartItem.menyPrice)
                : null;

        let sparPrice =
            cartItem.sparPrice != null && cartItem.sparPrice !== '' && !Number.isNaN(Number(cartItem.sparPrice))
                ? Number(cartItem.sparPrice)
                : null;

        // --- Backwards-compatibility migration for old cart items ---
        // Old items have the visible price in remaPrice even for Bilka/MK cards.
        // Use the saved store field (or id prefix) to re-bucket correctly.
        const inferredStore = itemStore
            || (productId.startsWith('bilka_') ? 'Bilka'
                : productId.startsWith('mk_') ? 'Min Købmand'
                    : 'Rema 1000');

        if (inferredStore === 'Bilka' && bilkaPrice == null && remaPrice != null) {
            // Old item: price was saved as remaPrice, but it belongs to Bilka
            bilkaPrice = remaPrice;
            remaPrice = null;
        } else if ((inferredStore === 'Min Købmand' || inferredStore === 'Min Koebmand') && mkPrice == null && remaPrice != null) {
            mkPrice = remaPrice;
            remaPrice = null;
        } else if (inferredStore === 'Meny' && menyPrice == null && remaPrice != null) {
            menyPrice = remaPrice;
            remaPrice = null;
        } else if (inferredStore === 'Spar' && sparPrice == null && remaPrice != null) {
            sparPrice = remaPrice;
            remaPrice = null;
        }

        // Final fallback: truly no prices at all → put under Rema
        if (remaPrice == null && bilkaPrice == null && mkPrice == null && menyPrice == null && sparPrice == null && cartItem.price != null) {
            remaPrice = Number(cartItem.price);
        }

        // Enhance with live API data if available
        const remaProduct = remaMap ? remaMap.get(productId) : null;
        if (remaProduct) {
            if (remaPrice == null || Number.isNaN(remaPrice)) {
                remaPrice = getProductPrice(remaProduct);
            }
            if (bilkaPrice == null) {
                const m = remaProduct['/product/bilka_match'];
                if (m && m.price != null && !Number.isNaN(Number(m.price))) {
                    bilkaPrice = parseFloat(m.price);
                }
            }
            if (mkPrice == null) {
                const m = remaProduct['/product/mk_match'];
                if (m && m.price != null && !Number.isNaN(Number(m.price))) {
                    mkPrice = parseFloat(m.price);
                }
            }
            if (menyPrice == null) {
                const m = remaProduct['/product/meny_match'];
                if (m && m.price != null && !Number.isNaN(Number(m.price))) {
                    menyPrice = parseFloat(m.price);
                }
            }
            if (sparPrice == null) {
                const m = remaProduct['/product/spar_match'];
                if (m && m.price != null && !Number.isNaN(Number(m.price))) {
                    sparPrice = parseFloat(m.price);
                }
            }
        }

        if (remaSelected && remaPrice != null && !Number.isNaN(remaPrice)) {
            stores[0].totalPrice += remaPrice * quantity;
        }
        if (bilkaSelected && bilkaPrice != null && !Number.isNaN(bilkaPrice)) {
            stores[1].totalPrice += bilkaPrice * quantity;
        }
        if (mkSelected && mkPrice != null && !Number.isNaN(mkPrice)) {
            stores[2].totalPrice += mkPrice * quantity;
        }
        if (menySelected && menyPrice != null && !Number.isNaN(menyPrice)) {
            stores[3].totalPrice += menyPrice * quantity;
        }
        if (sparSelected && sparPrice != null && !Number.isNaN(sparPrice)) {
            stores[4].totalPrice += sparPrice * quantity;
        }

        if (remaPrice != null && bilkaPrice == null && mkPrice == null && menyPrice == null && sparPrice == null) {
            linesWithoutMatches += 1;
        }

        const hasRema = remaPrice != null && !Number.isNaN(remaPrice);
        const hasBilka = bilkaPrice != null && !Number.isNaN(bilkaPrice);
        const hasMK = mkPrice != null && !Number.isNaN(mkPrice);
        const hasMeny = menyPrice != null && !Number.isNaN(menyPrice);
        const hasSpar = sparPrice != null && !Number.isNaN(sparPrice);

        if (hasRema && !hasBilka && !hasMK && !hasMeny && !hasSpar) {
            remaOnlyItems.push({
                name: cartItem.name || 'Vare',
                image: cartItem.image || '',
                unitPrice: remaPrice,
                quantity: quantity
            });
        } else if (hasBilka && !hasRema && !hasMK && !hasMeny && !hasSpar) {
            bilkaOnlyItems.push({
                name: cartItem.name || 'Vare',
                image: cartItem.image || '',
                unitPrice: bilkaPrice,
                quantity: quantity
            });
        } else if (hasMK && !hasRema && !hasBilka && !hasMeny && !hasSpar) {
            mkOnlyItems.push({
                name: cartItem.name || 'Vare',
                image: cartItem.image || '',
                unitPrice: mkPrice,
                quantity: quantity
            });
        } else if (hasMeny && !hasRema && !hasBilka && !hasMK && !hasSpar) {
            menyOnlyItems.push({
                name: cartItem.name || 'Vare',
                image: cartItem.image || '',
                unitPrice: menyPrice,
                quantity: quantity
            });
        } else if (hasSpar && !hasRema && !hasBilka && !hasMK && !hasMeny) {
            sparOnlyItems.push({
                name: cartItem.name || 'Vare',
                image: cartItem.image || '',
                unitPrice: sparPrice,
                quantity: quantity
            });
        }
    });

    stores[0].totalPrice = parseFloat(stores[0].totalPrice.toFixed(2));
    stores[1].totalPrice = parseFloat(stores[1].totalPrice.toFixed(2));
    stores[2].totalPrice = parseFloat(stores[2].totalPrice.toFixed(2));
    stores[3].totalPrice = parseFloat(stores[3].totalPrice.toFixed(2));
    stores[4].totalPrice = parseFloat(stores[4].totalPrice.toFixed(2));

    // Return only selected stores
    const filteredStores = stores.filter(s => selectedStores.has(s.name));
    return { stores: filteredStores, linesWithoutMatches, remaOnlyItems, bilkaOnlyItems, mkOnlyItems, menyOnlyItems, sparOnlyItems };
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

// Document ready event listener
document.addEventListener('DOMContentLoaded', function () {
    // Search functionality
    const searchInput = document.getElementById('searchInput');
    if (searchInput) {
        searchInput.addEventListener('input', performSearch);
    }

    // Initialize store filters
    initStoreFilters();

    // Initialize cart display
    updateCartDisplay();
    updateCartCount();

    // Initial attachment of event listeners
    attachProductEventListeners();

    // Initialize reference button
    const referenceBtn = document.querySelector('.show-reference-btn');
    if (referenceBtn && !referenceBtn.querySelector('.button-text')) {
        const buttonText = referenceBtn.textContent;
        referenceBtn.innerHTML = `
            <span class="button-text">${buttonText}</span>
            <div class="loading-spinner"></div>
        `;
    }
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
    const { remaPrice, bilkaPrice, mkPrice, menyPrice, sparPrice, mainPrice } = parsed;
    const image = productElement.querySelector('.product-image').src;
    const category = productElement.dataset.category || 'Andre varer';
    const unitMeasure = productElement.dataset.remaWeight || '';
    const kgPrice = productElement.dataset.remaKgPrice || '';
    const store = productElement.dataset.store || 'Rema 1000';

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
            remaPrice: remaPrice,
            bilkaPrice: bilkaPrice,
            mkPrice: mkPrice,
            menyPrice: menyPrice,
            sparPrice: sparPrice,
            image: image,
            category: category,
            unitMeasure: unitMeasure,
            kgPrice: kgPrice,
            quantity: quantity
        });
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

    // Fetch product information
    fetch(`/product/${productId.replace('product', '')}`)
        .then(response => response.json())
        .catch(error => console.error('Error:', error));

    // Get product data
    var imageSrc = productElement.dataset.mainImage || '';

    var title = productElement.querySelector('h3').innerText;
    var descNode = productElement.querySelector('.product-description');
    var description = descNode ? descNode.innerText : '';
    var brand = productElement.querySelector('.brand').innerText;

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

    // Insert data into overlay
    document.getElementById('overlay-image').src = imageSrc;
    document.getElementById('overlay-title').innerText = title;
    document.getElementById('overlay-description').innerText = description;
    document.getElementById('overlay-brand-name').innerText = brand.replace('Mærke: ', '');

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
            var mainCardPrice = mainPriceEl ? parseFloat(mainPriceEl.innerText.replace(' DKK', '').replace(',', '.').trim()) : 0;
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

            var cards = [
                { id: 'comp-card-rema', price: rPrice, badgeId: 'comp-badge-rema', priceId: 'comp-rema-price', name: 'Rema 1000', isSale: remaIsSale },
                { id: 'comp-card-bilka', price: bPrice, badgeId: 'comp-badge-bilka', priceId: 'comp-bilka-price', name: 'Bilka', isSale: bilkaIsSale },
                { id: 'comp-card-minkobmand', price: mPrice, badgeId: 'comp-badge-minkobmand', priceId: 'comp-mk-price', name: 'Min Købmand', isSale: mkIsSale },
                { id: 'comp-card-meny', price: mePrice, badgeId: 'comp-badge-meny', priceId: 'comp-meny-price', name: 'Meny', isSale: menyIsSale },
                { id: 'comp-card-spar', price: sPrice, badgeId: 'comp-badge-spar', priceId: 'comp-spar-price', name: 'Spar', isSale: sparIsSale }
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

                if (idx === 0) {
                    // Cheapest
                    el.style.border = '1.5px solid #2a7d4f';
                    pEl.style.color = '#2a7d4f';
                    bEl.textContent = 'Billigst';
                    bEl.style.background = '#e6f4ea';
                    bEl.style.color = '#1e7e34';
                    bEl.style.display = 'block';
                } else {
                    el.style.border = '0.5px solid #dcdcdc';
                    pEl.style.color = '#333';
                    var diff = c.price - validCards[0].price;
                    bEl.textContent = '+' + diff.toFixed(2) + ' kr';
                    bEl.style.background = '#f1f3f4';
                    bEl.style.color = '#5f6368';
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
    const products = document.querySelectorAll('.product');
    products.forEach(product => {
        product.onclick = function () {
            openOverlay(this);
        };

        const addToCartBtn = product.querySelector('.corner-box, .add-to-cart-btn');
        if (addToCartBtn) {
            addToCartBtn.onclick = (e) => {
                e.stopPropagation();
                addToCart(e, product);
            };
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


// Advanced Filtering Logic
function updatePriceLabel(value) {
    const label = document.getElementById('priceLimitLabel');
    if (label) label.textContent = value + ' kr';
}

function applyFilters() {
    const products = document.querySelectorAll('.product');
    const sortType = document.getElementById('sortSelect').value;
    const maxPrice = parseFloat(document.getElementById('priceRange').value);
    const showOnlySale = document.getElementById('saleFilter').checked;
    const showOnlyOrganic = document.getElementById('organicFilter').checked;
    const showOnlyLactoseFree = document.getElementById('lactoseFilter').checked;
    const minWeight = parseFloat(document.getElementById('weightFilter').value) || 0;

    products.forEach(product => {
        const price = parseFloat(product.dataset.price);
        const isSale = product.dataset.remaIsSale === 'true';
        const isOrganic = product.dataset.isOrganic === 'true';
        const isLactoseFree = product.dataset.isLactoseFree === 'true';
        const weightG = parseFloat(product.dataset.weightG) || 0;
        const store = product.dataset.store;

        let visible = true;

        // Store filter (already handled by applyStoreFilters, but let's be safe)
        if (typeof selectedStores !== 'undefined' && !selectedStores.has(store)) visible = false;

        // Advanced filters
        if (price > maxPrice) visible = false;
        if (showOnlySale && !isSale) visible = false;
        if (showOnlyOrganic && !isOrganic) visible = false;
        if (showOnlyLactoseFree && !isLactoseFree) visible = false;
        if (weightG < minWeight) visible = false;

        if (visible) {
            product.style.display = '';
            product.classList.remove('filter-hidden');
        } else {
            product.style.display = 'none';
            product.classList.add('filter-hidden');
        }
    });

    // Sorting
    sortProducts(sortType);
}

// Advanced Filters Initialization
function initAdvancedFilters() {
    const filters = [
        'sortSelect', 'minPrice', 'maxPrice', 'saleFilter',
        'organicFilter', 'lactoseFilter', 'minWeight', 'maxWeight'
    ];

    filters.forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            el.addEventListener('change', applyAllFilters);
            if (el.tagName === 'INPUT' && el.type === 'number') {
                el.addEventListener('input', applyAllFilters);
            }
        }
    });

    const resetBtn = document.getElementById('resetFilters');
    if (resetBtn) {
        resetBtn.addEventListener('click', resetAdvancedFilters);
    }

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
    document.getElementById('sortSelect').value = 'relevance';
    document.getElementById('minPrice').value = '';
    document.getElementById('maxPrice').value = '';
    document.getElementById('saleFilter').checked = false;
    document.getElementById('organicFilter').checked = false;
    document.getElementById('lactoseFilter').checked = false;
    document.getElementById('minWeight').value = '';
    document.getElementById('maxWeight').value = '';

    applyAllFilters();
}

function applyAllFilters() {
    const products = document.querySelectorAll('.product');
    const sortType = document.getElementById('sortSelect')?.value || 'relevance';
    const minPrice = parseFloat(document.getElementById('minPrice')?.value) || 0;
    const maxPrice = parseFloat(document.getElementById('maxPrice')?.value) || Infinity;
    const saleOnly = document.getElementById('saleFilter')?.checked;
    const organicOnly = document.getElementById('organicFilter')?.checked;
    const lactoseOnly = document.getElementById('lactoseFilter')?.checked;
    const minWeight = parseFloat(document.getElementById('minWeight')?.value) || 0;
    const maxWeight = parseFloat(document.getElementById('maxWeight')?.value) || Infinity;

    products.forEach(p => {
        // Get store visibility from existing selectedStores logic
        const store = p.dataset.store || 'Rema 1000';
        let isVisible = selectedStores.has(store);

        if (isVisible) {
            // Price Filter
            const price = parseFloat(p.querySelector('.price-main, .price-sale')?.innerText) || 0;
            if (price < minPrice || price > maxPrice) isVisible = false;

            // Sale Filter
            if (isVisible && saleOnly) {
                const isSale = p.querySelector('.sale-badge') !== null;
                if (!isSale) isVisible = false;
            }

            // Organic Filter
            if (isVisible && organicOnly) {
                if (p.dataset.isOrganic !== 'true') isVisible = false;
            }

            // Lactose Filter
            if (isVisible && lactoseOnly) {
                if (p.dataset.isLactoseFree !== 'true') isVisible = false;
            }

            // Weight Filter
            if (isVisible) {
                const weightG = parseFloat(p.dataset.weightG);
                if (!isNaN(weightG)) {
                    if (weightG < minWeight || weightG > maxWeight) isVisible = false;
                } else if (minWeight > 0 || maxWeight < Infinity) {
                    // If we have a weight filter but product has no weight data, hide it?
                    // Let's be lenient and show it unless specifically filtered out
                    // Actually, if minWeight > 0 and no weight data, we should probably hide it
                    if (minWeight > 0) isVisible = false;
                }
            }
        }

        if (isVisible) {
            p.style.display = '';
            p.classList.remove('filtered-out');
        } else {
            p.style.display = 'none';
            p.classList.add('filtered-out');
        }
    });

    if (sortType !== 'relevance') {
        sortProductsInGrid(sortType);
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

// Call init on load
document.addEventListener('DOMContentLoaded', () => {
    initStoreFilters();
    initAdvancedFilters();
});

