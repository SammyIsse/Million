with open('d:/Code... måske/static/css/styles.css', 'r', encoding='utf-8') as f:
    content = f.read()

new_css = '''
/* ===== SETTINGS PANEL ===== */
#settings-overlay {
  display: none;
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
  z-index: 300;
  backdrop-filter: blur(2px);
}
#settings-overlay.active {
  display: block;
}

#settings-panel {
  position: fixed;
  top: 0;
  right: 0;
  width: min(448px, 100vw);
  height: 100vh;
  background: var(--bg, var(--white));
  z-index: 301;
  transform: translateX(100%);
  transition: transform 0.35s cubic-bezier(0.34, 1.2, 0.64, 1);
  display: flex;
  flex-direction: column;
  box-shadow: -8px 0 32px rgba(0, 0, 0, 0.12);
  color: var(--text-main, var(--gray-900));
}

#settings-panel.active {
  transform: translateX(0);
}

.settings-content {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
}

.settings-section {
  padding-bottom: 24px;
  margin-bottom: 24px;
  border-bottom: 1px solid var(--border-color, var(--gray-200));
}

.settings-section:last-child {
  border-bottom: none;
}

.settings-section h3 {
  font-size: 1.1rem;
  margin-bottom: 12px;
  color: var(--text-main, var(--gray-900));
}

.settings-desc {
  font-size: 0.85rem;
  color: var(--text-muted, var(--gray-500));
  margin-bottom: 16px;
}

.setting-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
.setting-item strong {
  display: block;
  font-size: 0.95rem;
  color: var(--text-main, var(--gray-900));
}
.setting-item p {
  font-size: 0.8rem;
  color: var(--text-muted, var(--gray-500));
  margin: 2px 0 0;
}

/* Toggle Switch */
.toggle-switch {
  position: relative;
  display: inline-block;
  width: 44px;
  height: 24px;
}
.toggle-switch input {
  opacity: 0;
  width: 0;
  height: 0;
}
.slider {
  position: absolute;
  cursor: pointer;
  top: 0; left: 0; right: 0; bottom: 0;
  background-color: #ccc;
  transition: .4s;
}
.slider:before {
  position: absolute;
  content: "";
  height: 18px;
  width: 18px;
  left: 3px;
  bottom: 3px;
  background-color: white;
  transition: .4s;
}
input:checked + .slider {
  background-color: var(--green);
}
input:checked + .slider:before {
  transform: translateX(20px);
}
.slider.round {
  border-radius: 24px;
}
.slider.round:before {
  border-radius: 50%;
}

/* Store Toggles */
.store-toggles {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.store-checkbox {
  display: flex;
  align-items: center;
  gap: 10px;
  cursor: pointer;
  font-size: 0.95rem;
  color: var(--text-main, var(--gray-900));
}
.store-checkbox input[type="checkbox"] {
  width: 18px;
  height: 18px;
  accent-color: var(--green);
}

/* ===== DARK MODE VARIABLES ===== */
[data-theme="dark"] {
  --white: #1f2937;
  --gray-50: #111827;
  --gray-100: #374151;
  --gray-200: #4b5563;
  --gray-300: #6b7280;
  --gray-400: #9ca3af;
  --gray-500: #d1d5db;
  --gray-600: #e5e7eb;
  --gray-700: #f3f4f6;
  --gray-800: #f9fafb;
  --gray-900: #ffffff;

  --bg: #111827;
  --text-main: #ffffff;
  --text-muted: #9ca3af;
  --border-color: #374151;
}

body[data-theme="dark"] {
  background-color: var(--bg);
  color: var(--text-main);
}
'''

if '#settings-panel' not in content:
    with open('d:/Code... måske/static/css/styles.css', 'a', encoding='utf-8') as f:
        f.write('\n' + new_css)
    print('CSS appended')
else:
    print('CSS already exists')
