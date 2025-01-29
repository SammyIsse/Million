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
    
    // Check if the product is on sale
    const salePriceElement = productElement.querySelector('.price.sale');
    const regularPriceElement = productElement.querySelector('.price:not(.sale):not(.original)');
    const originalPriceElement = productElement.querySelector('.price.original');
    
    // Use sale price if available, otherwise use regular price
    let price;
    if (salePriceElement) {
        price = parseFloat(salePriceElement.innerText.replace(' DKK', ''));
    } else if (regularPriceElement) {
        price = parseFloat(regularPriceElement.innerText.replace(' DKK', ''));
    } else {
        console.error('Price element not found');
        return;
    }
    
    const image = productElement.querySelector('.product-image').src;

    // Check if product already exists in cart
    const existingItem = cart.find(item => item.id === productId);
    
    if (existingItem) {
        existingItem.quantity += 1;
    } else {
        cart.push({
            id: productId,
            name: name,
            price: price,
            image: image,
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
    
    cart.forEach((item, index) => {
        // Create cart item element
        const cartItem = document.createElement('div');
        cartItem.className = 'cart-item';
        
        // Calculate item total
        const itemTotal = item.price * item.quantity;
        total += itemTotal;
        
        cartItem.innerHTML = `
            <div class="cart-item-top">
                <div class="cart-item-image">
                    <img src="${item.image}" alt="${item.name}">
                </div>
                <div class="cart-item-details">
                    <h3>${item.name}</h3>
                    <div class="cart-item-price">${item.price.toFixed(2)} kr</div>
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
    
    // Update total price display with 2 decimal places
    cartTotalPrice.textContent = `${total.toFixed(2)} kr`;
    
    // Update cart count
    updateCartCount();
}

function updateQuantity(index, change) {
    cart[index].quantity += change;
    
    if (cart[index].quantity <= 0) {
        cart.splice(index, 1);
    }
    
    localStorage.setItem('cart', JSON.stringify(cart));
    updateCartDisplay();
}

function showReference() {
    // Implement the reference functionality here
    console.log('Show reference clicked');
}

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
});

// Function to perform AJAX search
let searchTimeout = null;

function performSearch() {
    const searchInput = document.getElementById('searchInput');
    const searchResults = document.getElementById('searchResults');
    const productsContainer = searchResults.querySelector('.products');
    const searchTitle = searchResults.querySelector('.search-title');
    const query = searchInput.value.trim();

    console.log('Starting search with query:', query);

    // Clear previous timeout
    if (searchTimeout) {
        clearTimeout(searchTimeout);
    }

    // Handle empty query
    if (!query) {
        console.log('Empty query, hiding search results');
        searchResults.style.display = 'none';
        document.body.classList.remove('search-active');
        return;
    }

    // Delay the search to prevent too many requests
    searchTimeout = setTimeout(() => {
        // Show loading state
        console.log('Showing loading state');
        productsContainer.innerHTML = '<div class="loading">Søger...</div>';
        searchTitle.textContent = `Søgeresultater for "${query}"`;

        fetch(`/search?q=${encodeURIComponent(query)}`)
            .then(response => {
                console.log('Search response status:', response.status);
                return response.json();
            })
            .then(data => {
                console.log('Search response data:', data);
                if (data.html) {
                    console.log('Received HTML content, updating results');
                    productsContainer.innerHTML = data.html;
                    attachProductEventListeners();
                    
                    // Show search results and hide other content
                    searchResults.style.display = 'block';
                    document.body.classList.add('search-active');
                } else {
                    console.log('No HTML content in response');
                    productsContainer.innerHTML = '<div class="no-results">Ingen resultater fundet</div>';
                }
            })
            .catch(error => {
                console.error('Search error:', error);
                productsContainer.innerHTML = '<div class="error">Der opstod en fejl under søgningen</div>';
            });
    }, 300);
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
    
    // Check if the product is on sale
    const salePriceElement = productElement.querySelector('.price.sale');
    const regularPriceElement = productElement.querySelector('.price:not(.sale):not(.original)');
    
    // Use sale price if available, otherwise use regular price
    let price;
    if (salePriceElement) {
        price = parseFloat(salePriceElement.innerText.replace(' DKK', ''));
    } else if (regularPriceElement) {
        price = parseFloat(regularPriceElement.innerText.replace(' DKK', ''));
    } else {
        console.error('Price element not found');
        return;
    }
    
    const image = productElement.querySelector('.product-image').src;

    // Check if product already exists in cart
    const existingItem = cart.find(item => item.id === productId);
    
    if (existingItem) {
        existingItem.quantity += quantity;
    } else {
        cart.push({
            id: productId,
            name: name,
            price: price,
            image: image,
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
    var description = productElement.querySelector('p:nth-of-type(2)').innerText;
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
window.onclick = function(event) {
    var overlay = document.getElementById('overlay');
    if (event.target === overlay) {
        closeOverlay();
    }
}

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

