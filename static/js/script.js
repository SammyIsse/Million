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

    // Get product details
    const name = productElement.querySelector('h3').innerText;
    const priceElement = productElement.querySelector('.price');
    const price = parseFloat(priceElement.innerText.replace(' DKK', ''));
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
    
    // Show animation
    productElement.classList.add('added-to-cart');
    setTimeout(() => {
        productElement.classList.remove('added-to-cart');
    }, 300);

    saveCart();
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

// Function to filter products based on search input
function filterProducts() {
    const searchQuery = document.getElementById('searchInput').value.toLowerCase().trim();
    const products = document.querySelectorAll('.product');
    let found = false;
    
    products.forEach(product => {
        const productName = product.querySelector('h3').textContent.toLowerCase();
        const productBrand = product.querySelector('.brand').textContent.toLowerCase();
        
        // Search only in name and brand
        if (productName.includes(searchQuery) || productBrand.includes(searchQuery)) {
            product.style.display = 'block';
            found = true;
        } else {
            product.style.display = 'none';
        }
    });
    
    // Log search results for debugging
    console.log(`Search query: "${searchQuery}" found matches: ${found}`);
}

// Function to open product information overlay
function openOverlay(productId) {
    var productElement = document.getElementById(productId);

    if (!productElement) {
        console.error('Product not found:', productId);
        return;
    }

    // Get product data
    var images = productElement.querySelectorAll('img');
    if (images.length > 1) {
        var secondImageSrc = images[1].src;
    } else {
        console.log('Second image not found.');
    }
    
    var title = productElement.querySelector('h3').innerText;
    var description = productElement.querySelector('p:nth-of-type(2)').innerText;
    var brand = productElement.querySelector('.brand').innerText;
    var price = productElement.querySelector('.price').innerText.replace(' DKK', '');

    // Insert data into overlay
    document.getElementById('overlay-image').src = secondImageSrc;
    document.getElementById('overlay-title').innerText = title;
    document.getElementById('overlay-description').innerText = description;
    document.getElementById('overlay-brand-name').innerText = brand.replace('Mærke: ', '');
    document.getElementById('overlay-price-value').innerText = price + ' DKK';

    // Store current product ID for add to cart functionality
    document.querySelector('.product-info').dataset.productId = productId;

    // Show overlay
    document.getElementById('overlay').style.display = 'block';
    document.body.classList.add('no-scroll');
}

// Function to close product information overlay
function closeOverlay() {
    document.getElementById('overlay').style.display = 'none';
    document.body.classList.remove('no-scroll');
}

// Close overlay when clicking outside
window.onclick = function(event) {
    var overlay = document.getElementById('overlay');
    if (event.target === overlay) {
        closeOverlay();
    }
}

// Document ready event listener
document.addEventListener('DOMContentLoaded', function() {
    // Search functionality
    const searchInput = document.getElementById('searchInput');
    if (searchInput) {
        // Add click event to redirect to search page
        searchInput.addEventListener('click', function() {
            if (window.location.pathname !== '/search') {
                window.location.href = '/search';
            }
        });

        // Handle input for live search
        searchInput.addEventListener('input', function() {
            const query = this.value.trim();
            if (window.location.pathname === '/search') {
                // Update URL without reloading the page
                const newUrl = query ? `/search?q=${encodeURIComponent(query)}` : '/search';
                window.history.replaceState({}, '', newUrl);
                
                // Fetch and display new results
                fetch(`/search?q=${encodeURIComponent(query)}`)
                    .then(response => response.text())
                    .then(html => {
                        const parser = new DOMParser();
                        const doc = parser.parseFromString(html, 'text/html');
                        const newProducts = doc.querySelector('.products');
                        const newPagination = doc.querySelector('.pagination');
                        
                        // Update the products and pagination
                        document.querySelector('.products').innerHTML = newProducts.innerHTML;
                        const paginationContainer = document.querySelector('.pagination');
                        if (paginationContainer) {
                            paginationContainer.innerHTML = newPagination ? newPagination.innerHTML : '';
                        }
                        
                        // Reattach event listeners to new products
                        attachProductEventListeners();
                    });
            }
        });

        // Initialize search page
        if (window.location.pathname === '/search') {
            searchInput.focus();
            const urlParams = new URLSearchParams(window.location.search);
            const queryParam = urlParams.get('q') || '';
            searchInput.value = queryParam;
        }
    }

    // Initialize cart display
    updateCartDisplay();
    updateCartCount();
    
    // Initial attachment of event listeners
    attachProductEventListeners();
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
