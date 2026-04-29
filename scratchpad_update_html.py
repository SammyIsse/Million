with open('d:/Code... måske/templates/base.html', 'r', encoding='utf-8') as f:
    content = f.read()

settings_html = '''
  <!-- Settings overlay -->
  <div id="settings-overlay" onclick="toggleSettings()"></div>

  <!-- Settings Panel -->
  <div id="settings-panel">
    <div class="cart-header">
      <div class="cart-header-left">
        <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="3"></circle>
          <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path>
        </svg>
        <h2>Indstillinger</h2>
      </div>
      <button class="close-button" onclick="toggleSettings()">&#x2715;</button>
    </div>
    
    <div class="settings-content">
      <!-- Theme Setting -->
      <div class="settings-section">
        <h3>Tema og Udseende</h3>
        <div class="setting-item">
          <div>
            <strong>Dark Mode</strong>
            <p>Skift mellem lyst og mørkt design</p>
          </div>
          <label class="toggle-switch">
            <input type="checkbox" id="darkModeToggle" onchange="toggleDarkMode()">
            <span class="slider round"></span>
          </label>
        </div>
      </div>

      <!-- Store Defaults -->
      <div class="settings-section">
        <h3>Standardbutikker</h3>
        <p class="settings-desc">Vælg hvilke butikker der automatisk vises, når du åbner appen.</p>
        <div class="store-toggles">
          <label class="store-checkbox"><input type="checkbox" value="Rema 1000" checked onchange="saveStoreDefaults()"> <span>Rema 1000</span></label>
          <label class="store-checkbox"><input type="checkbox" value="Bilka" checked onchange="saveStoreDefaults()"> <span>Bilka</span></label>
          <label class="store-checkbox"><input type="checkbox" value="Meny" checked onchange="saveStoreDefaults()"> <span>Meny</span></label>
          <label class="store-checkbox"><input type="checkbox" value="Spar" checked onchange="saveStoreDefaults()"> <span>Spar</span></label>
          <label class="store-checkbox"><input type="checkbox" value="Min Købmand" checked onchange="saveStoreDefaults()"> <span>Min Købmand</span></label>
        </div>
      </div>

      <!-- Notifications -->
      <div class="settings-section">
        <h3>Notifikationer</h3>
        <div class="setting-item">
          <div>
            <strong>Push-beskeder</strong>
            <p>Få besked om nye tilbud</p>
          </div>
          <label class="toggle-switch">
            <input type="checkbox" id="pushToggle" onchange="saveMiscSettings()">
            <span class="slider round"></span>
          </label>
        </div>
        <div class="setting-item">
          <div>
            <strong>Nyhedsbreve</strong>
            <p>Modtag ugens bedste tilbud på mail</p>
          </div>
          <label class="toggle-switch">
            <input type="checkbox" id="emailToggle" onchange="saveMiscSettings()">
            <span class="slider round"></span>
          </label>
        </div>
      </div>
    </div>
  </div>

'''

if 'id="settings-panel"' not in content:
    new_content = content.replace('  <!-- Product Overlay -->', settings_html + '  <!-- Product Overlay -->')
    with open('d:/Code... måske/templates/base.html', 'w', encoding='utf-8') as f:
        f.write(new_content)
    print('Settings HTML inserted')
else:
    print('Settings HTML already exists')
