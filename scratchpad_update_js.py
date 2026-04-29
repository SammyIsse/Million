with open('d:/Code... måske/static/js/script.js', 'r', encoding='utf-8') as f:
    content = f.read()

new_js = '''
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

    // Load Store Defaults
    const defaultStoresStr = localStorage.getItem('cartspotter_stores');
    if (defaultStoresStr) {
        const defaultStores = JSON.parse(defaultStoresStr);
        // Ensure checkboxes reflect saved state
        const checkboxes = document.querySelectorAll('.store-checkbox input[type="checkbox"]');
        checkboxes.forEach(cb => {
            cb.checked = defaultStores.includes(cb.value);
        });
        
        // Update the app's selectedStores immediately
        selectedStores.clear();
        defaultStores.forEach(s => selectedStores.add(s));
        
        // Update header UI if it exists
        document.querySelectorAll('.store-filter-btn').forEach(btn => {
            const store = btn.getAttribute('data-store');
            if (selectedStores.has(store)) {
                btn.classList.remove('inactive');
            } else {
                btn.classList.add('inactive');
            }
        });
    }

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
    localStorage.setItem('cartspotter_stores', JSON.stringify(defaults));
    
    // Also apply them immediately to the current session
    selectedStores.clear();
    defaults.forEach(s => selectedStores.add(s));
    
    // Update header UI
    document.querySelectorAll('.store-filter-btn').forEach(btn => {
        const store = btn.getAttribute('data-store');
        if (selectedStores.has(store)) {
            btn.classList.remove('inactive');
        } else {
            btn.classList.add('inactive');
        }
    });

    // Refresh products view
    applyFilters();
}

function saveMiscSettings() {
    const push = document.getElementById('pushToggle').checked;
    const email = document.getElementById('emailToggle').checked;
    localStorage.setItem('cartspotter_push', push ? 'true' : 'false');
    localStorage.setItem('cartspotter_email', email ? 'true' : 'false');
}

// Ensure initSettings is called on DOM load
document.addEventListener('DOMContentLoaded', () => {
    initSettings();
});
'''

if 'toggleSettings()' not in content:
    with open('d:/Code... måske/static/js/script.js', 'a', encoding='utf-8') as f:
        f.write('\n' + new_js)
    print('JS appended')
else:
    print('JS already exists')
