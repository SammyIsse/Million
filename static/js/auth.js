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
  /* ---------------------------------------------- kurv-ejerskab og kvittering

     Fletningen var en ren union med Math.max paa antal. Det er RIGTIGT naar en
     gaest logger ind: gaestens varer skal laegges til den gemte kurv. Men den
     samme fletning koerte ogsaa naar en allerede indlogget bruger genindlaeste
     siden - og da kan en union ikke udtrykke en SLETNING. Havde brugeren
     fjernet en vare uden at pushet naaede frem, kom varen tilbage ved naeste
     indlaesning og blev cementeret paa serveren.

     Vi kan ikke bare sammenligne tidsstempler: browserens ur og Postgres' ur
     er ikke det samme, og et ur der gaar bare lidt forkert ville enten aldrig
     eller altid lade lokalt vinde. I stedet gemmer vi serverens EGET
     updated_at som en kvittering, hver gang vi selv har skrevet. Er serverens
     vaerdi uaendret siden vores kvittering, er vi den sidste der skrev - saa er
     den lokale kurv sandheden, inklusive dens sletninger. Er den anderledes,
     har en anden enhed skrevet, og serveren vinder. Helt uafhaengigt af ure. */
  var OWNER_KEY = 'cartOwner';
  var SYNCED_KEY = 'cartSyncedAt';

  function _readLS(key) {
    try { return localStorage.getItem(key); } catch (e) { return null; }
  }

  function _writeLS(key, value) {
    try {
      if (value === null) localStorage.removeItem(key);
      else localStorage.setItem(key, value);
    } catch (e) { /* privat browsing o.l. - saa falder vi tilbage til fletning */ }
  }

  async function pullCart() {
    if (!SB || !currentUser) return { items: [], updatedAt: null };
    try {
      var res = await SB.from(CARTS)
        .select('items,updated_at')
        .eq('user_id', currentUser.id)
        .maybeSingle();
      if (res.error) return { items: [], updatedAt: null };
      return {
        items: (res.data && res.data.items) ? res.data.items : [],
        updatedAt: (res.data && res.data.updated_at) || null
      };
    } catch (e) { return { items: [], updatedAt: null }; }
  }

  async function pushCart(cart) {
    if (!SB || !currentUser) return;
    try {
      // select() henter raekken tilbage, saa vi faar serverens nye updated_at
      // og kan gemme den som kvittering - se kommentaren ved pullCart.
      var res = await SB.from(CARTS).upsert(
        { user_id: currentUser.id, items: cartToRows(cart) },
        { onConflict: 'user_id' }
      ).select('updated_at').maybeSingle();
      if (res && !res.error && res.data && res.data.updated_at) {
        _writeLS(OWNER_KEY, currentUser.id);
        _writeLS(SYNCED_KEY, String(res.data.updated_at));
      }
    } catch (e) { /* stille - kurven ligger stadig lokalt */ }
  }

  var pendingCart = null;

  function scheduleSync(cart) {
    if (!currentUser) return;
    pendingCart = cart;
    if (syncTimer) clearTimeout(syncTimer);
    syncTimer = setTimeout(function () { syncTimer = null; flushCart(); }, 800);
  }

  // Send en ventende kurv af sted NU. Debouncen er 800 ms, og navigation
  // inden for det vindue afbrød pushet: aendringen naaede aldrig serveren, og
  // ved naeste sideindlaesning fletter vi den gamle serverkurv ind igen - saa
  // en netop slettet vare dukkede op paa ny. logout() flushede korrekt, men
  // et almindeligt klik paa et link gjorde ikke.
  function flushCart() {
    if (syncTimer) { clearTimeout(syncTimer); syncTimer = null; }
    if (!pendingCart || !currentUser) return;
    var cart = pendingCart;
    pendingCart = null;
    pushCart(cart);
  }

  // visibilitychange->hidden fyrer paalideligt FOER navigation i moderne
  // browsere; pagehide er sikkerhedsnettet (bl.a. iOS Safari).
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'hidden') flushCart();
  });
  window.addEventListener('pagehide', flushCart);

  /* ----------------------------------------------------- auth-state → kurv/UI */
  async function handleSignedIn(user) {
    currentUser = user;
    updateAuthUI();
    // Synk kun én gang pr. login-overgang i denne page-load.
    if (lastSyncedUid === user.id) return;
    lastSyncedUid = user.id;

    var localCart = (window.CartBridge && window.CartBridge.get()) ? window.CartBridge.get() : [];
    var server = await pullCart();
    var owner = _readLS(OWNER_KEY);
    var syncedAt = _readLS(SYNCED_KEY);

    var resolved;
    if (owner !== user.id || !syncedAt) {
      // Gaestekurv, en anden brugers kurv, eller vi har aldrig skrevet foer:
      // flet, saa varer lagt i kurven inden login foelger med over.
      resolved = mergeCarts(localCart, server.items);
    } else if (String(server.updatedAt || '') === syncedAt) {
      // Serveren staar praecis som vi sidst efterlod den, saa ingen anden
      // enhed har rettet. Den lokale kurv er nyeste sandhed - OGSAA naar den
      // indeholder faerre varer, hvilket er hele pointen: en sletning der ikke
      // naaede frem foer, blev genoplivet af unionen.
      resolved = localCart;
    } else {
      // En anden enhed har skrevet siden. Den vinder; vores lokale kopi var
      // bygget paa noget aeldre.
      resolved = rowsToCart(server.items);
    }

    if (window.CartBridge) window.CartBridge.applyFromServer(resolved);
    // Skub tilbage, så begge sider er ens (og vi får en frisk kvittering).
    await pushCart(resolved);
    // Fremtidige lokale ændringer synkes.
    if (window.CartBridge) window.CartBridge._onChange = scheduleSync;
    try {
      if (window.AuthBridge && typeof window.AuthBridge.onSignedIn === 'function') {
        window.AuthBridge.onSignedIn(user);
      }
    } catch (e) { /* ignorér */ }
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
    try {
      if (window.AuthBridge && typeof window.AuthBridge.onSignedOut === 'function') {
        window.AuthBridge.onSignedOut();
      }
    } catch (e) { /* ignorér */ }
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

  // Generisk besked (fejl = rød, ellers grøn "ok") + travl-knap til de andre
  // formularer (reset / ny adgangskode).
  function setMsg(id, text, isError) {
    var e = el(id);
    if (!e) return;
    e.textContent = text || '';
    e.style.display = text ? 'block' : 'none';
    e.classList.toggle('auth-ok', !!text && !isError);
  }
  function setBusyBtn(id, b) {
    var btn = el(id);
    if (btn) { btn.disabled = b; btn.classList.toggle('is-busy', b); }
  }

  // Modalen har fire visninger: login, account, reset (anmod om link),
  // newpassword (sæt ny kode efter mail-link). currentView sikrer, at
  // updateAuthUI ikke overskriver et igangværende reset-/recovery-flow.
  var AUTH_VIEWS = ['login', 'account', 'reset', 'newpassword'];
  var currentView = 'login';
  function showView(name) {
    currentView = name;
    AUTH_VIEWS.forEach(function (v) {
      var elv = el('auth-view-' + v);
      if (elv) elv.style.display = (v === name) ? 'block' : 'none';
    });
  }

  function normalizeDisplayName(raw) {
    return String(raw || '')
      .replace(/[\u0000-\u001f\u007f]/g, '')
      .trim()
      .slice(0, 40);
  }

  function readUserDisplayName(user) {
    var meta = (user && user.user_metadata) || {};
    return normalizeDisplayName(
      meta.display_name || meta.full_name || meta.name || ''
    );
  }

  function getDisplayName() {
    return readUserDisplayName(currentUser);
  }

  // Kræver et navn før del/join. Prompt'er hvis mangler; gemmer i user_metadata.
  async function ensureDisplayName() {
    if (!currentUser || !SB) return '';
    var existing = getDisplayName();
    if (existing) return existing;
    var suggested = '';
    var email = currentUser.email || '';
    if (email.indexOf('@') > 0) suggested = email.split('@')[0].slice(0, 40);
    var raw = window.prompt('Hvad skal de andre kalde dig i den delte kurv?', suggested);
    if (raw === null) return '';
    var nm = normalizeDisplayName(raw);
    if (!nm) {
      alert('Skriv et navn (max 40 tegn).');
      return '';
    }
    var ok = await saveDisplayName(nm);
    return ok ? nm : '';
  }

  async function saveDisplayName(raw) {
    if (!SB || !currentUser) return false;
    var nm = normalizeDisplayName(raw);
    if (!nm) return false;
    try {
      var res = await SB.auth.updateUser({ data: { display_name: nm } });
      if (res.error) {
        console.error('[auth] display_name:', res.error);
        return false;
      }
      if (res.data && res.data.user) currentUser = res.data.user;
      try {
        await SB.rpc(
          String('set_my_display_name') + (window.__SB_RPC_SUFFIX || ''),
          { p_name: nm }
        );
      } catch (e) { /* ikke i gruppe */ }
      updateAuthUI();
      return true;
    } catch (e) {
      console.error('[auth] display_name:', e);
      return false;
    }
  }

  async function saveDisplayNameFromAccount() {
    var input = el('auth-account-name');
    var nm = normalizeDisplayName(input && input.value);
    if (!nm) {
      setMsg('auth-name-msg', 'Skriv et navn (max 40 tegn).', true);
      return;
    }
    var ok = await saveDisplayName(nm);
    setMsg('auth-name-msg', ok ? 'Navnet er gemt.' : 'Kunne ikke gemme navnet.', !ok);
    if (input) input.value = getDisplayName();
  }

  function updateAuthUI() {
    var loggedIn = !!currentUser;
    var toggle = el('auth-toggle-btn');
    if (toggle) {
      toggle.classList.toggle('logged-in', loggedIn);
      toggle.setAttribute('aria-label', loggedIn ? 'Din konto' : 'Log ind');
    }
    var emailEl = el('auth-account-email');
    if (emailEl && currentUser) emailEl.textContent = currentUser.email || '';
    var nameInput = el('auth-account-name');
    if (nameInput && currentUser) nameInput.value = getDisplayName();
    // Skift ikke visning midt i et reset-/ny-kode-flow.
    if (currentView === 'reset' || currentView === 'newpassword') return;
    showView(loggedIn ? 'account' : 'login');
    if (!loggedIn) applyMode();
  }

  function applyMode() {
    var title = el('auth-title');
    var sub = el('auth-submit-btn');
    var switchText = el('auth-switch-text');
    var switchBtn = el('auth-switch-btn');
    var pw = el('auth-password');
    var nameRow = el('auth-name-row');
    var turnstileRow = el('auth-turnstile-row');
    if (authMode === 'signup') {
      if (title) title.textContent = 'Opret konto';
      if (sub) sub.textContent = 'Opret konto';
      if (switchText) switchText.textContent = 'Har du allerede en konto?';
      if (switchBtn) switchBtn.textContent = 'Log ind';
      if (pw) pw.setAttribute('autocomplete', 'new-password');
      if (nameRow) nameRow.style.display = 'block';
      if (turnstileRow) turnstileRow.style.display = 'block';
    } else {
      if (title) title.textContent = 'Log ind';
      if (sub) sub.textContent = 'Log ind';
      if (switchText) switchText.textContent = 'Ny bruger?';
      if (switchBtn) switchBtn.textContent = 'Opret konto';
      if (pw) pw.setAttribute('autocomplete', 'current-password');
      if (nameRow) nameRow.style.display = 'none';
      if (turnstileRow) turnstileRow.style.display = 'none';
    }
    var forgot = el('auth-forgot-row');
    if (forgot) forgot.style.display = (authMode === 'signup') ? 'none' : 'block';
    setError('');
  }

  function openAuthModal(view) {
    if (!initClient()) { alert('Login er midlertidigt utilgængeligt.'); return; }
    var overlay = el('auth-overlay');
    var modal = el('auth-modal');
    if (overlay) overlay.classList.add('active');
    if (modal) { modal.classList.add('active'); modal.setAttribute('aria-hidden', 'false'); }
    document.body.style.overflow = 'hidden';
    showView(view || (currentUser ? 'account' : 'login'));
    if (currentView === 'login') {
      applyMode();
      ensureGsi();   // render Google-knappen (GSI) i den nu-synlige container
      var em = el('auth-email');
      if (em) setTimeout(function () { em.focus(); }, 50);
    }
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

  // Turnstile-hjaelpere. getResponse()/reset() tager enten et widget-id fra
  // render() eller en CSS-selector - et BART element-id bliver laest som
  // tag-selector, rammer intet, og biblioteket KASTER saa ("Could not find
  // widget for provided container"). Widget'en rendres implicit via klassen
  // cf-turnstile, saa det skjulte input er den paalidelige kilde; getResponse
  // med rigtig selector er reserve. Begge er pakket ind, fordi et kast fra
  // reset() i en finally-blok ellers springer oprydningen bagefter over og
  // efterlader knappen permanent laast.
  function turnstileToken(containerId) {
    var input = document.querySelector('#' + containerId + ' [name="cf-turnstile-response"]');
    if (input && input.value) return input.value;
    try {
      if (typeof turnstile !== 'undefined' && turnstile.getResponse) {
        return turnstile.getResponse('#' + containerId) || '';
      }
    } catch (err) {
      console.warn('[auth] Turnstile getResponse fejlede:', err);
    }
    return '';
  }

  function turnstileReset(containerId) {
    try {
      if (typeof turnstile !== 'undefined' && turnstile.reset) {
        turnstile.reset('#' + containerId);
      }
    } catch (err) {
      console.warn('[auth] Turnstile reset fejlede:', err);
    }
  }

  async function submitForm(e) {
    if (e) e.preventDefault();
    if (!initClient()) return false;
    var email = (el('auth-email') || {}).value || '';
    var pw = (el('auth-password') || {}).value || '';
    var signupName = normalizeDisplayName((el('auth-name') || {}).value || '');
    if (!email || !pw) { setError('Udfyld email og adgangskode.'); return false; }
    if (authMode === 'signup' && !signupName) {
      setError('Skriv dit navn, så andre kan se dig i en delt kurv.');
      return false;
    }
    setError(''); setBusy(true);
    try {
      // Turnstile: bot-tjek foer selve konto-oprettelsen. Login roeres ikke -
      // risikoen her er automatiseret signup-spam, ikke gentagne login-forsoeg.
      if (authMode === 'signup') {
        var tsToken = turnstileToken('auth-turnstile-widget');
        if (!tsToken) { setError('Bekræft venligst at du ikke er en robot.'); return false; }
        var verifyRes = await fetch('https://turnstile-siteverify-madshopper.kasp478g.workers.dev', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ token: tsToken })
        });
        var verifyData = await verifyRes.json().catch(function () { return null; });
        if (!verifyData || !verifyData.success) {
          setError('Bot-tjek fejlede. Prøv igen.');
          return false;
        }
      }
      var res = (authMode === 'signup')
        ? await SB.auth.signUp({
            email: email, password: pw,
            // Bekræftelses-linket sender brugeren tilbage til dér, de oprettede
            // sig (localhost under test, madshopper.dk i prod). Origin skal stå
            // i Supabase' Redirect URLs-liste.
            options: {
              emailRedirectTo: window.location.origin,
              data: { display_name: signupName }
            }
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
      if (authMode === 'signup') turnstileReset('auth-turnstile-widget');
      setBusy(false);
    }
    return false;
  }

  // Fallback: klassisk redirect-login (viser supabase.co). Bruges kun hvis GSI
  // ikke kan loade eller ID-token-loginet fejler.
  async function signInGoogle() {
    if (!initClient()) return;
    try {
      await SB.auth.signInWithOAuth({
        provider: 'google',
        options: { redirectTo: window.location.origin }
      });
    } catch (e) { setError('Google-login mislykkedes. Prøv igen.'); }
  }

  /* ------------------------------------------ Google Identity Services (GSI) */
  // ID-token-flow: Google-prompten er bundet til vores egen origin, så
  // samtykkeskærmen viser madshopper.dk i stedet for supabase.co. Falder tilbage
  // til signInGoogle() (redirect) hvis noget fejler, så login altid virker.
  var gsiRendered = false;
  var gsiNonceRaw = null;

  function randomNonce() {
    var a = new Uint8Array(16);
    crypto.getRandomValues(a);
    return Array.from(a).map(function (b) { return b.toString(16).padStart(2, '0'); }).join('');
  }
  async function sha256Hex(str) {
    var buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(str));
    return Array.from(new Uint8Array(buf)).map(function (b) { return b.toString(16).padStart(2, '0'); }).join('');
  }
  function showGoogleFallback() {
    var fb = el('auth-google-fallback');
    if (fb && !gsiRendered) fb.style.display = 'flex';
  }

  async function handleGoogleCredential(response) {
    if (!initClient() || !response || !response.credential) return;
    try {
      var res = await SB.auth.signInWithIdToken({
        provider: 'google',
        token: response.credential,
        nonce: gsiNonceRaw || undefined
      });
      if (res.error) {
        console.error('[auth] signInWithIdToken-fejl → falder tilbage til redirect:', res.error);
        signInGoogle();
        return;
      }
      closeAuthModal();   // onAuthStateChange klarer resten (kurv-synk, UI)
    } catch (err) {
      console.error('[auth] GSI-undtagelse → falder tilbage til redirect:', err);
      signInGoogle();
    }
  }

  async function renderGsiButton() {
    if (gsiRendered) return;
    var g = window.google;
    var container = el('gsi-button');
    if (!g || !g.accounts || !g.accounts.id || !window.__GOOGLE_CLIENT_ID || !container) return;
    try {
      gsiNonceRaw = randomNonce();
      var hashed = await sha256Hex(gsiNonceRaw);
      g.accounts.id.initialize({
        client_id: window.__GOOGLE_CLIENT_ID,
        callback: handleGoogleCredential,
        nonce: hashed
      });
      container.innerHTML = '';
      g.accounts.id.renderButton(container, {
        type: 'standard', theme: 'outline', size: 'large',
        text: 'continue_with', shape: 'rectangular',
        logo_alignment: 'center', width: 320
      });
      gsiRendered = true;
      var fb = el('auth-google-fallback');
      if (fb) fb.style.display = 'none';
    } catch (err) {
      console.error('[auth] GSI-init fejlede → viser fallback-knap:', err);
      showGoogleFallback();
    }
  }

  // GSI-scriptet loader async; vent op til ~5s på det, ellers vis fallback.
  var gsiEnsuring = false;
  function ensureGsi() {
    if (gsiRendered || gsiEnsuring) return;
    if (window.google && window.google.accounts && window.google.accounts.id) {
      renderGsiButton();
      return;
    }
    gsiEnsuring = true;
    var tries = 0;
    var iv = setInterval(function () {
      tries++;
      if (window.google && window.google.accounts && window.google.accounts.id) {
        clearInterval(iv); gsiEnsuring = false;
        renderGsiButton();
      } else if (tries > 20) {
        clearInterval(iv); gsiEnsuring = false;
        showGoogleFallback();
      }
    }, 250);
  }

  async function logout() {
    if (!SB) return;
    try {
      // Skub evt. ventende kurv-ændring til serveren FØR vi rydder lokalt, så
      // intet tabes hvis debounce-timeren ikke er fyret endnu.
      if (syncTimer) { clearTimeout(syncTimer); syncTimer = null; }
      pendingCart = null;
      // Ejerskab og kvittering foelger brugeren - uden dette ville naeste
      // bruger paa samme browser arve dem og faa sin serverkurv overskrevet.
      _writeLS(OWNER_KEY, null);
      _writeLS(SYNCED_KEY, null);
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

  /* ---------------------------------------------------- glemt adgangskode */
  function showLogin() { authMode = 'login'; showView('login'); applyMode(); ensureGsi(); }

  function showReset() {
    showView('reset');
    setMsg('auth-reset-msg', '');
    var target = el('auth-reset-email'), src = el('auth-email');
    if (target) {
      if (src && src.value) target.value = src.value;   // genbrug indtastet email
      setTimeout(function () { target.focus(); }, 50);
    }
  }

  // Sender et nulstillingslink. redirectTo skal stå i Supabase' Redirect URLs.
  async function requestReset(e) {
    if (e) e.preventDefault();
    if (!initClient()) return false;
    var email = (el('auth-reset-email') || {}).value || '';
    if (!email) { setMsg('auth-reset-msg', 'Indtast din email.', true); return false; }
    setBusyBtn('auth-reset-btn', true);
    try {
      var res = await SB.auth.resetPasswordForEmail(email, { redirectTo: window.location.origin });
      if (res.error) {
        console.error('[auth] reset-fejl:', res.error);
        setMsg('auth-reset-msg', translateErr(res.error), true);
        return false;
      }
      setMsg('auth-reset-msg', 'Tjek din email for et link til at nulstille adgangskoden.', false);
    } catch (err) {
      console.error('[auth] reset-undtagelse:', err);
      setMsg('auth-reset-msg', 'Noget gik galt. Prøv igen.', true);
    } finally {
      setBusyBtn('auth-reset-btn', false);
    }
    return false;
  }

  // Sætter den nye adgangskode efter klik på mail-linket (PASSWORD_RECOVERY).
  async function submitNewPassword(e) {
    if (e) e.preventDefault();
    if (!SB) return false;
    var pw = (el('auth-newpw') || {}).value || '';
    if (pw.length < 8) { setMsg('auth-newpw-msg', 'Adgangskoden skal være mindst 8 tegn.', true); return false; }
    setBusyBtn('auth-newpw-btn', true);
    try {
      var res = await SB.auth.updateUser({ password: pw });
      if (res.error) {
        console.error('[auth] ny-kode-fejl:', res.error);
        setMsg('auth-newpw-msg', translateErr(res.error), true);
        return false;
      }
      setMsg('auth-newpw-msg', '');
      // Fjern recovery-tokenet fra URL'en, så et reload ikke gentager flowet.
      try { history.replaceState(null, '', window.location.pathname + window.location.search); } catch (e2) { /* ignorér */ }
      var user = (res.data && res.data.user) ? res.data.user : currentUser;
      if (user) { lastSyncedUid = null; handleSignedIn(user); }
      showView('account');   // vis kontovisningen som kvittering (nu logget ind)
    } catch (err) {
      console.error('[auth] ny-kode-undtagelse:', err);
      setMsg('auth-newpw-msg', 'Noget gik galt. Prøv igen.', true);
    } finally {
      setBusyBtn('auth-newpw-btn', false);
    }
    return false;
  }

  /* --------------------------------------------------------------- opstart */
  function boot() {
    var sb = initClient();
    if (!sb) return;                 // supabase-js ikke loadet → login deaktiveret
    applyMode();
    sb.auth.onAuthStateChange(function (event, session) {
      if (event === 'PASSWORD_RECOVERY') {
        // Brugeren kom fra "glemt kode"-mailen → vis "sæt ny kode"-visningen.
        if (session && session.user) currentUser = session.user;
        openAuthModal('newpassword');
        return;
      }
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
  window.authShowReset = showReset;
  window.authShowLogin = showLogin;
  window.authRequestReset = requestReset;
  window.authSubmitNewPassword = submitNewPassword;
  window.authSaveDisplayName = saveDisplayNameFromAccount;

  // Bro til script.js (gem/del lister kræver konto).
  window.AuthBridge = {
    getUser: function () { return currentUser; },
    getClient: function () { return initClient(); },
    rpcName: function (base) {
      return String(base || '') + (window.__SB_RPC_SUFFIX || '');
    },
    requireAuth: function () {
      if (currentUser) return true;
      openAuthModal('login');
      return false;
    },
    getDisplayName: getDisplayName,
    ensureDisplayName: ensureDisplayName,
    // Kald efter login/logout - script.js hægtet shared-cart sync her.
    onSignedIn: null,
    onSignedOut: null
  };

  if (document.readyState !== 'loading') boot();
  else document.addEventListener('DOMContentLoaded', boot);
})();
