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
document.addEventListener('click', function(event) {
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
document.addEventListener('keydown', function(event) {
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

/** Rema-shelfpris + evt. matchet Bilka-pris fra produktkort (data-bilka-price). */
function parsePricesFromProductCard(productElement) {
    const salePriceElement = productElement.querySelector('.price.sale');
    const regularPriceElement = productElement.querySelector('.price:not(.sale):not(.original)');
    let remaPrice;
    if (salePriceElement) {
        remaPrice = parseDKKPrice(salePriceElement.innerText);
    } else if (regularPriceElement) {
        remaPrice = parseDKKPrice(regularPriceElement.innerText);
    } else {
        return null;
    }
    if (Number.isNaN(remaPrice)) return null;

    const bilkaRaw = productElement.dataset.bilkaPrice;
    let bilkaPrice = null;
    if (bilkaRaw !== undefined && bilkaRaw !== '') {
        const p = parseFloat(String(bilkaRaw).replace(',', '.'));
        if (!Number.isNaN(p)) bilkaPrice = p;
    }
    return { remaPrice, bilkaPrice };
}

function saveCart() {
    localStorage.setItem('cart', JSON.stringify(cart));
    updateCartDisplay();
    updateCartCount();
}

function addToCart(event, productId) {
    // Prevent event bubbling
    event.stopPropagation();
    
    const productElement = document.getElementById(productId);
    if (!productElement) {
        console.error('Product not found:', productId);
        return;
    }

    // Get the button that was clicked
    const addToCartBtn = event.target;
    
    // Get product details
    const name = productElement.querySelector('h3').innerText;
    const parsed = parsePricesFromProductCard(productElement);
    if (!parsed) {
        console.error('Price element not found');
        return;
    }
    const { remaPrice, bilkaPrice } = parsed;
    const image = productElement.querySelector('.product-image').src;
    const category = productElement.dataset.category || 'Andre varer';
    const unitMeasure = productElement.dataset.remaWeight || '';
    const kgPrice = productElement.dataset.remaKgPrice || '';

    // Check if product already exists in cart
    const existingItem = cart.find(item => item.id === productId);
    
    if (existingItem) {
        existingItem.quantity += 1;
    } else {
        cart.push({
            id: productId,
            name: name,
            price: remaPrice,
            remaPrice: remaPrice,
            bilkaPrice: bilkaPrice,
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
    const cartCount = document.getElementById('cart-count');
    const totalItems = cart.reduce((sum, item) => sum + item.quantity, 0);
    cartCount.textContent = totalItems;
    cartCount.style.display = totalItems > 0 ? 'flex' : 'none';
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
            
            // Calculate item total
            const unitRema = item.remaPrice != null ? item.remaPrice : item.price;
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
    cartTotalPrice.textContent = `${total.toFixed(2)} kr`;
    
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

function updateCartStorage() {
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
        .then(({ stores, linesWithoutBilka, remaOnlyItems, bilkaOnlyItems }) => {
            const storeComparisons = stores.slice();
            storeComparisons.sort((a, b) => a.totalPrice - b.totalPrice);
            
            const remaStore = stores.find(s => s.name === 'Rema 1000');
            const bilkaStore = stores.find(s => s.name === 'Bilka');
            const rTotal = remaStore ? remaStore.totalPrice : 0;
            const bTotal = bilkaStore ? bilkaStore.totalPrice : 0;

            for (let i = 0; i < storeComparisons.length; i++) {
                const store = storeComparisons[i];
                const rowElement = document.getElementById(`store-row-${i + 1}`);
                
                const logoImg = rowElement.querySelector('.store-logo');
                logoImg.src = `/static/images/${store.name === 'Bilka' ? 'bilka-logo.png' : 'Rema1000-logo.png'}`;
                
                rowElement.querySelector('.store-name').textContent = store.name;
                rowElement.querySelector('.store-price').textContent = `${store.totalPrice.toFixed(2)} kr`;
            }

            const slot1 = document.getElementById('store-exclusive-slot-1');
            const slot2 = document.getElementById('store-exclusive-slot-2');
            if (slot1 && slot2) {
                const firstName = storeComparisons[0] ? storeComparisons[0].name : '';
                if (firstName === 'Rema 1000') {
                    slot1.innerHTML = buildExclusiveSlotHtml('Kun hos Rema 1000 (ikke hos Bilka):', remaOnlyItems);
                    slot2.innerHTML = buildExclusiveSlotHtml('Kun hos Bilka (ikke hos Rema):', bilkaOnlyItems);
                } else {
                    slot1.innerHTML = buildExclusiveSlotHtml('Kun hos Bilka (ikke hos Rema):', bilkaOnlyItems);
                    slot2.innerHTML = buildExclusiveSlotHtml('Kun hos Rema 1000 (ikke hos Bilka):', remaOnlyItems);
                }
                slot1.hidden = !slot1.innerHTML.trim();
                slot2.hidden = !slot2.innerHTML.trim();
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
        { name: 'Bilka', totalPrice: 0 }
    ];
    let linesWithoutBilka = 0;
    const remaOnlyItems = [];
    const bilkaOnlyItems = [];

    const cartProducts = JSON.parse(localStorage.getItem('cart')) || [];
    
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

        let remaPrice =
            cartItem.remaPrice != null && !Number.isNaN(Number(cartItem.remaPrice))
                ? Number(cartItem.remaPrice)
                : null;
        if (remaPrice == null && cartItem.price != null) {
            remaPrice = Number(cartItem.price);
        }

        let bilkaPrice =
            cartItem.bilkaPrice != null && cartItem.bilkaPrice !== ''
                ? Number(cartItem.bilkaPrice)
                : null;
        if (Number.isNaN(bilkaPrice)) bilkaPrice = null;

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
        }

        if (remaPrice != null && !Number.isNaN(remaPrice)) {
            stores[0].totalPrice += remaPrice * quantity;
        }
        if (bilkaPrice != null && !Number.isNaN(bilkaPrice)) {
            stores[1].totalPrice += bilkaPrice * quantity;
        } else if (remaPrice != null && !Number.isNaN(remaPrice)) {
            linesWithoutBilka += 1;
        }

        const hasRema = remaPrice != null && !Number.isNaN(remaPrice);
        const hasBilka = bilkaPrice != null && !Number.isNaN(bilkaPrice);
        if (hasRema && !hasBilka) {
            remaOnlyItems.push({
                name: cartItem.name || 'Vare',
                image: cartItem.image || '',
                unitPrice: remaPrice,
                quantity: quantity
            });
        } else if (hasBilka && !hasRema) {
            bilkaOnlyItems.push({
                name: cartItem.name || 'Vare',
                image: cartItem.image || '',
                unitPrice: bilkaPrice,
                quantity: quantity
            });
        }
    });

    stores[0].totalPrice = parseFloat(stores[0].totalPrice.toFixed(2));
    stores[1].totalPrice = parseFloat(stores[1].totalPrice.toFixed(2));

    return { stores, linesWithoutBilka, remaOnlyItems, bilkaOnlyItems };
}

function getProductPrice(product) {
    const salePrice = product['/product/sale_price'];
    const regularPrice = product['/product/price'];
    return salePrice && !isNaN(salePrice) ? parseFloat(salePrice) : parseFloat(regularPrice);
}

// Add event listener for ESC key to close store comparison overlay
document.addEventListener('keydown', function(event) {
    if (event.key === 'Escape') {
        const storeComparisonOverlay = document.getElementById('store-comparison-overlay');
        if (storeComparisonOverlay.style.display === 'flex') {
            closeStoreComparison();
        }
    }
});

// Close store comparison overlay when clicking outside
document.addEventListener('click', function(event) {
    const overlay = document.getElementById('store-comparison-overlay');
    const content = document.querySelector('.comparison-content');
    
    if (overlay.style.display === 'flex' && 
        !content.contains(event.target) && 
        event.target !== overlay) {
        closeStoreComparison();
    }
});

// Document ready event listener
document.addEventListener('DOMContentLoaded', function() {
    // Search functionality
    const searchInput = document.getElementById('searchInput');
    if (searchInput) {
        searchInput.addEventListener('input', performSearch);
    }

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
        
        fetch(`/search?q=${encodeURIComponent(query)}`)
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
document.addEventListener('keydown', function(event) {
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
    const quantityElement = document.querySelector('.product-info .quantity');
    let quantity = parseInt(quantityElement.textContent);
    quantity = Math.max(1, quantity + change); // Ensure quantity doesn't go below 1
    quantityElement.textContent = quantity;
}

// Function to add to cart from overlay
function addToCartFromOverlay(event) {
    event.preventDefault();
    const addToCartBtn = event.target;
    const productId = document.querySelector('.product-info').dataset.productId;
    const quantity = parseInt(document.querySelector('.product-info .quantity').textContent);
    const overlay = document.getElementById('overlay');
    
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
    const { remaPrice, bilkaPrice } = parsed;
    const image = productElement.querySelector('.product-image').src;
    const category = productElement.dataset.category || 'Andre varer';
    const unitMeasure = productElement.dataset.remaWeight || '';
    const kgPrice = productElement.dataset.remaKgPrice || '';

    // Check if product already exists in cart
    const existingItem = cart.find(item => item.id === productId);
    
    if (existingItem) {
        existingItem.quantity += quantity;
    } else {
        cart.push({
            id: productId,
            name: name,
            price: remaPrice,
            remaPrice: remaPrice,
            bilkaPrice: bilkaPrice,
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
function openOverlay(productId) {
    var productElement = document.getElementById(productId);

    if (!productElement) {
        console.error('Product not found:', productId);
        return;
    }

    // Fetch product information
    fetch(`/product/${productId.replace('product', '')}`)
        .then(response => response.json())
        .catch(error => console.error('Error:', error));

    // Get product data
    var productImage = productElement.querySelector('.product-image');
    var imageSrc = productImage ? productImage.src : '';
    
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
    var store    = productElement.dataset.store || 'Rema 1000';

    if (!hasMatch) {
        if (storeOnlyMsg) {
            var storeName = store === 'Bilka' ? 'Bilka' : 'Rema 1000';
            storeOnlyMsg.textContent = 'Vi har endnu ikke fundet denne vare hos andre butikker — den er foreløbigt kun tilgængelig hos ' + storeName + '.';
            storeOnlyMsg.style.display = 'block';
        }
        if (compDiv) compDiv.style.display = 'none';
        if (genericAddBtn) genericAddBtn.textContent = 'Tilføj til kurv';
    } else {
        if (storeOnlyMsg) storeOnlyMsg.style.display = 'none';
        
        if (compDiv) {
            var remaPriceEl = productElement.querySelector('.price.sale') || productElement.querySelector('.price:not(.sale):not(.original)');
            var rPrice = remaPriceEl ? parseFloat(remaPriceEl.innerText.replace(' DKK', '').replace(',', '.')) : 0;
            
            var remaKgPrice = productElement.dataset.remaKgPrice || '';
            var bilkaName = productElement.dataset.bilkaName || 'Bilka Match';
            var bilkaRaw = productElement.dataset.bilkaPrice;
            var bPrice = bilkaRaw ? parseFloat(bilkaRaw.replace(',', '.')) : 0;
            var bilkaKgPrice = productElement.dataset.bilkaKgPrice || '';

            document.getElementById('comp-rema-kg-price').textContent = remaKgPrice ? 'Pris pr. kg: ' + parseFloat(remaKgPrice).toFixed(2) + ' kr' : '';
            document.getElementById('comp-bilka-name').textContent = bilkaName;
            document.getElementById('comp-bilka-kg-price').textContent = bilkaKgPrice ? 'Pris pr. kg: ' + parseFloat(bilkaKgPrice).toFixed(2) + ' kr' : '';
            
            var rCard = document.getElementById('comp-card-rema');
            var bCard = document.getElementById('comp-card-bilka');
            var rBadge = document.getElementById('comp-badge-rema');
            var bBadge = document.getElementById('comp-badge-bilka');
            var rPriceText = document.getElementById('comp-rema-price');
            var bPriceText = document.getElementById('comp-bilka-price');
            var compAddBtn = document.getElementById('comp-add-to-cart-btn');

            rPriceText.textContent = rPrice ? rPrice.toFixed(2) + ' kr' : '';
            bPriceText.textContent = bPrice ? bPrice.toFixed(2) + ' kr' : '';

            // Reset logic
            rCard.style.border = '0.5px solid #dcdcdc';
            bCard.style.border = '0.5px solid #dcdcdc';
            rPriceText.style.color = '#333';
            bPriceText.style.color = '#333';
            rCard.style.order = '1';
            bCard.style.order = '2';

            var cheapestStore = 'Rema 1000';

            if (rPrice > 0 && bPrice > 0) {
                if (rPrice <= bPrice) {
                    cheapestStore = 'Rema 1000';
                    // Rema is cheapest
                    rCard.style.border = '1.5px solid #2a7d4f';
                    rCard.style.order = '1';
                    bCard.style.order = '2';
                    rPriceText.style.color = '#2a7d4f';
                    
                    rBadge.textContent = 'Billigst';
                    rBadge.style.background = '#e6f4ea';
                    rBadge.style.color = '#1e7e34';
                    rBadge.style.display = 'block';
                    
                    var diff = bPrice - rPrice;
                    bBadge.textContent = '+' + diff.toFixed(2) + ' kr';
                    bBadge.style.background = '#f1f3f4';
                    bBadge.style.color = '#5f6368';
                    bBadge.style.display = 'block';
                } else {
                    cheapestStore = bilkaName;
                    // Bilka is cheapest
                    bCard.style.border = '1.5px solid #2a7d4f';
                    bCard.style.order = '1';
                    rCard.style.order = '2';
                    bPriceText.style.color = '#2a7d4f';
                    
                    bBadge.textContent = 'Billigst';
                    bBadge.style.background = '#e6f4ea';
                    bBadge.style.color = '#1e7e34';
                    bBadge.style.display = 'block';
                    
                    var diff = bPrice - rPrice;
                    rBadge.textContent = '+' + Math.abs(diff).toFixed(2) + ' kr';
                    rBadge.style.background = '#f1f3f4';
                    rBadge.style.color = '#5f6368';
                    rBadge.style.display = 'block';
                }
            } else {
                rBadge.style.display = 'none';
                bBadge.style.display = 'none';
            }

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
    document.querySelector('.product-info .quantity').textContent = '1';

    // Store current product ID for add to cart functionality
    document.querySelector('.product-info').dataset.productId = productId;

    // Show overlay
    document.getElementById('overlay').style.display = 'block';
    document.body.classList.add('no-scroll');
}

// Function to close product information overlay
function closeOverlay() {
    const overlay = document.getElementById('overlay');
    overlay.classList.add('closing');
    setTimeout(() => {
        overlay.classList.remove('closing');
        overlay.style.display = 'none';
        document.body.classList.remove('no-scroll');
    }, 500); // Increased fade-out time
}

// Close overlay when clicking outside
document.addEventListener('click', function(event) {
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
        product.onclick = function() {
            openOverlay(this.id);
        };
        
        const addToCartBtn = product.querySelector('.corner-box');
        if (addToCartBtn) {
            addToCartBtn.onclick = (e) => addToCart(e, product.id);
        }
    });
}

// Add pagination handler
window.loadPage = function(page) {
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

