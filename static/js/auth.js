/* MadShopper - brugerkonti + gemt kurv (client-side via supabase-js).
 *
 * Al kurv-data beskyttes af Postgres RLS på carts-tabellen: en indlogget bruger
 * kan KUN læse/skrive sin egen række (auth.uid() = user_id). Se
 * scripts/supabase-carts.sql. Browseren bruger den offentlige publishable nøgle
 * (window.__SB_KEY), aldrig en service-nøgle.
 *
 * Kurven gemmes KOMPAKT: kun {p:id, q:antal, n:navn, i:billede, s:butik, pr:pris}.
 * Sammenligningspriser genhentes live fra /api/products ved visning (se
 * showReference i script.js), så vi hverken duplikerer produktdata eller gemmer
 * forældede priser - minimal plads, friske priser.
 */
(function () {
  'use strict';

  var SB = null;                                   // supabase-klient (lazy)
  var CARTS = window.__SB_CARTS || 'carts';        // tabelnavn (carts / carts_dev)
  var authMode = 'login';                          // 'login' | 'signup'
  var currentUser = null;
  var lastSyncedUid = null;                         // undgå dobbelt-synk pr. load
  var syncTimer = null;

  /* ----------------------------------------------------------------- klient */
  function initClient() {
    if (SB) return SB;
    if (!window.supabase || !window.__SB_URL || !window.__SB_KEY) return null;
    SB = window.supabase.createClient(window.__SB_URL, window.__SB_KEY, {
      auth: {
        persistSession: true,       // session i localStorage (30-dages login)
        autoRefreshToken: true,     // fornyer access-token lydløst
        detectSessionInUrl: true    // fanger tokens efter Google-redirect
      }
    });
    return SB;
  }

  /* ----------------------------------------------------- kurv-mapping (kompakt) */
  function cartToRows(cart) {
    var out = [];
    (cart || []).forEach(function (it) {
      if (!it || !it.id) return;
      var q = parseInt(it.quantity, 10);
      if (isNaN(q) || q < 1) q = 1;
      if (q > 99) q = 99;
      out.push({
        p: String(it.id).slice(0, 64),
        q: q,
        n: (it.name || '').slice(0, 120),
        i: (it.image || '').slice(0, 300),
        s: (it.store || '').slice(0, 40),
        pr: (it.price != null && !isNaN(it.price)) ? Number(it.price) : null
      });
    });
    return out.slice(0, 100);   // samme loft som CHECK-constrainten i databasen
  }

  function rowsToCart(rows) {
    return (rows || []).map(function (r) {
      return {
        id: r.p,
        name: r.n || '',
        image: r.i || '',
        store: r.s || '',
        price: (r.pr != null ? r.pr : 0),
        quantity: r.q || 1
      };
    });
  }

  // Flet lokal (anonym) kurv med server-kurven. Lokale rige felter (fx
  // storePrices til visning) bevares; antal tager det største, så gentagne logins
  // ikke dobler mængder.
  function mergeCarts(localCart, serverRows) {
    var byId = {};
    rowsToCart(serverRows).forEach(function (it) { byId[it.id] = it; });
    (localCart || []).forEach(function (it) {
      if (!it || !it.id) return;
      var prevQ = byId[it.id] ? (byId[it.id].quantity || 1) : 0;
      byId[it.id] = Object.assign({}, byId[it.id] || {}, it, {
        quantity: Math.max(it.quantity || 1, prevQ)
      });
    });
    return Object.keys(byId).map(function (k) { return byId[k]; });
  }

  /* ------------------------------------------------------------- synk til/fra */
  async function pullCart() {
    if (!SB || !currentUser) return [];
    try {
      var res = await SB.from(CARTS).select('items').eq('user_id', currentUser.id).maybeSingle();
      if (res.error) return [];
      return (res.data && res.data.items) ? res.data.items : [];
    } catch (e) { return []; }
  }

  async function pushCart(cart) {
    if (!SB || !currentUser) return;
    try {
      await SB.from(CARTS).upsert(
        { user_id: currentUser.id, items: cartToRows(cart) },
        { onConflict: 'user_id' }
      );
    } catch (e) { /* stille - kurven ligger stadig lokalt */ }
  }

  function scheduleSync(cart) {
    if (!currentUser) return;
    if (syncTimer) clearTimeout(syncTimer);
    syncTimer = setTimeout(function () { pushCart(cart); }, 800);
  }

  /* ----------------------------------------------------- auth-state → kurv/UI */
  async function handleSignedIn(user) {
    currentUser = user;
    updateAuthUI();
    // Synk kun én gang pr. login-overgang i denne page-load.
    if (lastSyncedUid === user.id) return;
    lastSyncedUid = user.id;

    var localCart = (window.CartBridge && window.CartBridge.get()) ? window.CartBridge.get() : [];
    var serverRows = await pullCart();
    var merged = mergeCarts(localCart, serverRows);
    if (window.CartBridge) window.CartBridge.applyFromServer(merged);
    // Skub den flettede kurv tilbage, så begge sider er ens.
    await pushCart(merged);
    // Fremtidige lokale ændringer synkes.
    if (window.CartBridge) window.CartBridge._onChange = scheduleSync;
  }

  // clearLocal er KUN sandt ved en rigtig log ud (event 'SIGNED_OUT'), ikke ved
  // et anonymt sidebesøg (INITIAL_SESSION uden session) - ellers ville en anonym
  // brugers localStorage-kurv blive tømt ved hver indlæsning.
  function handleSignedOut(clearLocal) {
    currentUser = null;
    lastSyncedUid = null;
    if (window.CartBridge) {
      window.CartBridge._onChange = null;
      if (clearLocal) window.CartBridge.applyFromServer([]);
    }
    updateAuthUI();
  }

  /* --------------------------------------------------------------------- UI */
  function el(id) { return document.getElementById(id); }

  function setError(msg) {
    var e = el('auth-error');
    if (e) { e.textContent = msg || ''; e.style.display = msg ? 'block' : 'none'; }
  }

  function setBusy(b) {
    var btn = el('auth-submit-btn');
    if (btn) { btn.disabled = b; btn.classList.toggle('is-busy', b); }
  }

  function updateAuthUI() {
    var loggedIn = !!currentUser;
    var toggle = el('auth-toggle-btn');
    if (toggle) {
      toggle.classList.toggle('logged-in', loggedIn);
      toggle.setAttribute('aria-label', loggedIn ? 'Din konto' : 'Log ind');
    }
    var viewLogin = el('auth-view-login');
    var viewAccount = el('auth-view-account');
    if (viewLogin) viewLogin.style.display = loggedIn ? 'none' : 'block';
    if (viewAccount) viewAccount.style.display = loggedIn ? 'block' : 'none';
    var emailEl = el('auth-account-email');
    if (emailEl && currentUser) emailEl.textContent = currentUser.email || '';
  }

  function applyMode() {
    var title = el('auth-title');
    var sub = el('auth-submit-btn');
    var switchText = el('auth-switch-text');
    var switchBtn = el('auth-switch-btn');
    var pw = el('auth-password');
    if (authMode === 'signup') {
      if (title) title.textContent = 'Opret konto';
      if (sub) sub.textContent = 'Opret konto';
      if (switchText) switchText.textContent = 'Har du allerede en konto?';
      if (switchBtn) switchBtn.textContent = 'Log ind';
      if (pw) pw.setAttribute('autocomplete', 'new-password');
    } else {
      if (title) title.textContent = 'Log ind';
      if (sub) sub.textContent = 'Log ind';
      if (switchText) switchText.textContent = 'Ny bruger?';
      if (switchBtn) switchBtn.textContent = 'Opret konto';
      if (pw) pw.setAttribute('autocomplete', 'current-password');
    }
    setError('');
  }

  function openAuthModal() {
    if (!initClient()) { alert('Login er midlertidigt utilgængeligt.'); return; }
    var overlay = el('auth-overlay');
    var modal = el('auth-modal');
    if (overlay) overlay.classList.add('active');
    if (modal) { modal.classList.add('active'); modal.setAttribute('aria-hidden', 'false'); }
    document.body.style.overflow = 'hidden';
    if (!currentUser) { var em = el('auth-email'); if (em) setTimeout(function () { em.focus(); }, 50); }
  }

  function closeAuthModal() {
    var overlay = el('auth-overlay');
    var modal = el('auth-modal');
    if (overlay) overlay.classList.remove('active');
    if (modal) { modal.classList.remove('active'); modal.setAttribute('aria-hidden', 'true'); }
    document.body.style.overflow = '';
    setError('');
  }

  function toggleMode() {
    authMode = (authMode === 'login') ? 'signup' : 'login';
    applyMode();
  }

  function translateErr(err) {
    var m = (err && err.message ? err.message : '').toLowerCase();
    if (m.indexOf('invalid login') >= 0) return 'Forkert email eller adgangskode.';
    if (m.indexOf('already registered') >= 0 || m.indexOf('already been registered') >= 0)
      return 'Der findes allerede en konto med denne email. Prøv at logge ind.';
    if (m.indexOf('password') >= 0 && (m.indexOf('least') >= 0 || m.indexOf('short') >= 0 || m.indexOf('6 characters') >= 0 || m.indexOf('8 characters') >= 0))
      return 'Adgangskoden skal være mindst 8 tegn.';
    if (m.indexOf('weak') >= 0) return 'Adgangskoden er for svag - vælg en længere.';
    if (m.indexOf('email') >= 0 && m.indexOf('valid') >= 0) return 'Indtast en gyldig email.';
    if (m.indexOf('rate') >= 0) return 'For mange forsøg - vent lidt og prøv igen.';
    return 'Noget gik galt. Prøv igen.';
  }

  async function submitForm(e) {
    if (e) e.preventDefault();
    if (!initClient()) return false;
    var email = (el('auth-email') || {}).value || '';
    var pw = (el('auth-password') || {}).value || '';
    if (!email || !pw) { setError('Udfyld email og adgangskode.'); return false; }
    setError(''); setBusy(true);
    try {
      var res = (authMode === 'signup')
        ? await SB.auth.signUp({
            email: email, password: pw,
            // Bekræftelses-linket sender brugeren tilbage til dér, de oprettede
            // sig (localhost under test, madshopper.dk i prod). Origin skal stå
            // i Supabase' Redirect URLs-liste.
            options: { emailRedirectTo: window.location.origin }
          })
        : await SB.auth.signInWithPassword({ email: email, password: pw });
      if (res.error) { console.error('[auth] Supabase-fejl:', res.error.status, res.error.message, res.error); setError(translateErr(res.error)); return false; }
      // Email-bekræftelse er slået FRA i v1, så signup returnerer en session og
      // logger direkte ind → onAuthStateChange lukker modalen. Skulle bekræftelse
      // være slået til, får brugeren besked her.
      if (res.data && res.data.session) { closeAuthModal(); }
      else if (authMode === 'signup') { setError('Tjek din email for at bekræfte kontoen.'); }
    } catch (err) {
      console.error('[auth] Undtagelse under login/signup:', err);
      setError('Noget gik galt. Prøv igen.');
    } finally {
      setBusy(false);
    }
    return false;
  }

  async function signInGoogle() {
    if (!initClient()) return;
    try {
      await SB.auth.signInWithOAuth({
        provider: 'google',
        options: { redirectTo: window.location.origin }
      });
    } catch (e) { setError('Google-login mislykkedes. Prøv igen.'); }
  }

  async function logout() {
    if (!SB) return;
    try {
      // Skub evt. ventende kurv-ændring til serveren FØR vi rydder lokalt, så
      // intet tabes hvis debounce-timeren ikke er fyret endnu.
      if (syncTimer) { clearTimeout(syncTimer); syncTimer = null; }
      if (currentUser && window.CartBridge) { await pushCart(window.CartBridge.get()); }
      await SB.auth.signOut();
    } catch (e) { /* ignorér */ }
    closeAuthModal();
  }

  async function deleteAccount() {
    if (!SB || !currentUser) return;
    if (!window.confirm('Er du sikker? Din konto og gemte kurv slettes permanent og kan ikke gendannes.')) return;
    try { await SB.rpc('delete_own_account'); } catch (e) { /* fortsæt til signOut */ }
    try { await SB.auth.signOut(); } catch (e) { /* ignorér */ }
    try { localStorage.setItem('cart', '[]'); } catch (e) { /* ignorér */ }
    if (window.CartBridge) window.CartBridge.applyFromServer([]);
    closeAuthModal();
  }

  /* --------------------------------------------------------------- opstart */
  function boot() {
    var sb = initClient();
    if (!sb) return;                 // supabase-js ikke loadet → login deaktiveret
    applyMode();
    sb.auth.onAuthStateChange(function (event, session) {
      if (session && session.user) handleSignedIn(session.user);
      else handleSignedOut(event === 'SIGNED_OUT');
    });
    // Luk modal på Escape.
    document.addEventListener('keydown', function (ev) {
      if (ev.key === 'Escape') {
        var modal = el('auth-modal');
        if (modal && modal.classList.contains('active')) closeAuthModal();
      }
    });
  }

  // Eksponér de funktioner base.html's inline-handlers kalder.
  window.openAuthModal = openAuthModal;
  window.closeAuthModal = closeAuthModal;
  window.authToggleMode = toggleMode;
  window.authSubmit = submitForm;
  window.authGoogle = signInGoogle;
  window.authLogout = logout;
  window.authDeleteAccount = deleteAccount;

  if (document.readyState !== 'loading') boot();
  else document.addEventListener('DOMContentLoaded', boot);
})();
