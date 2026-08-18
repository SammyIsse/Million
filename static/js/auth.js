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
  // Gemmer access_token/refresh_token fra selve PASSWORD_RECOVERY-hændelsen.
  // Set 18-08-2026: supabase-js's interne session forsvandt mellem hændelsen
  // (som ÅBNER "sæt ny adgangskode") og selve submit et lille øjeblik efter -
  // getSession() viste null, og updateUser() kastede AuthSessionMissingError,
  // selvom hændelsen ubestrideligt havde leveret en gyldig session (ellers
  // ville email-tjekket nedenfor aldrig have matchet og visningen aldrig
  // være åbnet). Rodårsagen i biblioteket er ikke fundet, men vi HAR
  // allerede de rigtige tokens fra hændelsen - genanvend dem eksplicit lige
  // før updateUser i stedet for at stole på klientens interne tilstand.
  var _recoveryTokens = null;
  // MIDLERTIDIGT (fjernes igen når session_not_found-fejlen er fundet,
  // 18-08-2026): logger hver eneste onAuthStateChange-hændelse siden
  // sideindlæsning, inkl. JWT'ens session_id-claim (samme felt Supabase
  // klager over i "session_not_found") - to gættede rettelser har ikke
  // virket, så vi observerer i stedet for at gætte en tredje gang.
  var _eventLog = [];
  var _pageLoadTs = Date.now();
  function _jwtSessionId(token) {
    try {
      var parts = token.split('.');
      var b64 = parts[1].replace(/-/g, '+').replace(/_/g, '/');
      var json = JSON.parse(atob(b64));
      return json.session_id || null;
    } catch (e) { return 'decode-fejl'; }
  }
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

  /* --------------------------------------------- nulstil-kvittering (app-paritet)
     App'en (AuthContext.tsx) har allerede dette lag, fordi dens deep-link-
     flow accepterer ETHVERT link maerket type=recovery uden det. Webben har
     samme svaghed, blot via en anden kanal: en Supabase-genereret reset-mail
     til en angribers EGEN konto er et helt almindeligt, gyldigt link til
     madshopper.dk/#access_token=...&type=recovery - videresender angriberen
     det link til et offer, og offeret klikker det (mens de er logget ud),
     fanger detectSessionInUrl:true tokenerne automatisk og logger offeret
     ind paa angriberens konto, som "sæt ny adgangskode" saa lader offeret
     saette adgangskoden paa uden at vide det (session fixation). Kvitteringen
     sikrer, at DENNE enhed selv har bedt om nulstilling for netop den email,
     inden vi overhovedet aabner "sæt ny adgangskode". */
  var PENDING_RESET_KEY = 'pendingPasswordReset';
  var PENDING_RESET_TTL_MS = 60 * 60 * 1000; // 1 time - matcher linkets typiske levetid

  function _readPendingResetEmail() {
    var raw = _readLS(PENDING_RESET_KEY);
    if (!raw) return null;
    try {
      var parsed = JSON.parse(raw);
      if (parsed && parsed.email && typeof parsed.ts === 'number' &&
          (Date.now() - parsed.ts) < PENDING_RESET_TTL_MS) {
        return parsed.email;
      }
    } catch (e) { /* ugyldig/korrupt kvittering - behandles som "ingen" */ }
    return null;
  }

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
    // ok:false betyder "opslaget fejlede", IKKE "serveren har en tom kurv" -
    // de to må aldrig forveksles af kalderne, for et fejlet opslag ville
    // ellers blive tolket som en ægte tom server-kurv og kunne overskrive
    // (eller pushe over) en rigtig, gemt kurv. Se refreshCart/handleSignedIn.
    if (!SB || !currentUser) return { items: [], updatedAt: null, ok: true };
    try {
      var res = await SB.from(CARTS)
        .select('items,updated_at')
        .eq('user_id', currentUser.id)
        .maybeSingle();
      if (res.error) return { items: [], updatedAt: null, ok: false };
      return {
        items: (res.data && res.data.items) ? res.data.items : [],
        updatedAt: (res.data && res.data.updated_at) || null,
        ok: true
      };
    } catch (e) { return { items: [], updatedAt: null, ok: false }; }
  }

  /* --------------------------------------------- samtidig-skrivning (faner)

     pushCart var et ubetinget upsert: to faner (eller enheder) der begge
     aendrer kurven inden for samme 800ms-debounce-vindue overskrev hinanden
     fuldstaendigt - den sidste der naaede Postgres vandt, og den foerstes
     aendring forsvandt sporloest, uden fejl nogen steder (produktionsrevision
     18-08-2026, blokerer #2). Kvitterings-mekanismen ovenfor (OWNER_KEY/
     SYNCED_KEY) beskyttede kun LAESNINGER (pull ved login/visibility-change)
     mod det samme problem - ikke selve skrivningen.

     Fixet: skriv betinget (UPDATE ... WHERE updated_at = vores kvittering) i
     stedet for et blankt upsert. Rammer opdateringen 0 raekker, har en anden
     enhed skrevet siden vores sidste kvittering - saa henter vi serverens
     friske kurv, fletter den ind (samme union-logik som ved login), og
     skriver fletningen i stedet for blindt at overskrive. */
  async function pushCart(cart) {
    if (!SB || !currentUser) return;
    try {
      var rows = cartToRows(cart);
      var expectedUpdatedAt = _readLS(SYNCED_KEY);
      var haveReceipt = _readLS(OWNER_KEY) === currentUser.id && !!expectedUpdatedAt;

      var res;
      if (haveReceipt) {
        res = await SB.from(CARTS)
          .update({ items: rows })
          .eq('user_id', currentUser.id)
          .eq('updated_at', expectedUpdatedAt)
          .select('updated_at')
          .maybeSingle();
        if (res && !res.error && !res.data) {
          // 0 raekker ramt: en anden enhed skrev siden vores kvittering.
          // Flet i stedet for at fortsaette blindt.
          var server = await pullCart();
          if (!server.ok) return;   // kan ikke flette uden serverens data - proev igen senere
          var merged = mergeCarts(cart, server.items);
          if (window.CartBridge) window.CartBridge.applyFromServer(merged);
          rows = cartToRows(merged);
          res = await SB.from(CARTS).upsert(
            { user_id: currentUser.id, items: rows },
            { onConflict: 'user_id' }
          ).select('updated_at').maybeSingle();
        }
      } else {
        // Foerste skrivning for denne enhed (ingen kvittering endnu) - almindeligt upsert.
        res = await SB.from(CARTS).upsert(
          { user_id: currentUser.id, items: rows },
          { onConflict: 'user_id' }
        ).select('updated_at').maybeSingle();
      }

      // select() henter raekken tilbage, saa vi faar serverens nye updated_at
      // og kan gemme den som kvittering - se kommentaren ved pullCart.
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
  // Hent kurven igen naar fanen bliver synlig. Uden dette hentede webben kun
  // ved login, saa en aendring lavet paa telefonen kom foerst frem ved naeste
  // sideindlaesning - selvom kurven laever at foelge med paa alle enheder.
  // Samme afgoerelse som ved login: staar serverens updated_at uaendret siden
  // vores kvittering, har ingen anden enhed skrevet, og vi roerer intet.
  async function refreshCart() {
    if (!SB || !currentUser || syncTimer) return;   // ventende push vinder
    var server = await pullCart();
    // Et fejlet opslag (offline, udløbet token, RLS) er IKKE det samme som
    // "serveren har en tom kurv" - server.items er [] i begge tilfælde. Uden
    // dette tjek tømte en enkelt netværksfejl kurven i UI'et og lokalt,
    // fordi server.updatedAt (null) aldrig matchede den gemte kvittering.
    if (!server.ok) return;
    var syncedAt = _readLS(SYNCED_KEY);
    if (_readLS(OWNER_KEY) === currentUser.id &&
        String(server.updatedAt || '') === syncedAt) return;
    if (window.CartBridge) window.CartBridge.applyFromServer(rowsToCart(server.items));
    if (server.updatedAt) {
      _writeLS(OWNER_KEY, currentUser.id);
      _writeLS(SYNCED_KEY, String(server.updatedAt));
    }
  }

  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'hidden') flushCart();
    else refreshCart();
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

    // Et fejlet opslag (offline, RLS, udløbet session) er IKKE en pålidelig
    // "tom kurv" - server.items er [] i begge tilfælde. Behandlede vi det
    // som ægte tomt, kunne det enten overskrive en gæstekurv med ingenting,
    // eller (værre) PUSHE en tom kurv til serveren nedenfor og slette
    // brugerens rigtige, gemte kurv. Ved fejl: rør intet, og lad et senere
    // scheduleSync/refreshCart prøve igen.
    if (server.ok) {
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
    }
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
  // aria-labelledby på #auth-modal peger statisk på login-visningens
  // overskrift (auth-title) i markuppet - de tre andre visninger havde ingen
  // ID'ede overskrifter, saa dialogens tilgaengelige navn gik tomt for
  // skaermlaesere paa 3 af 4 visninger (fundet under QA-audit 2026-08-17).
  var AUTH_VIEW_TITLE_IDS = {
    login: 'auth-title', account: 'auth-account-title',
    reset: 'auth-reset-title', newpassword: 'auth-newpassword-title'
  };
  var currentView = 'login';
  function showView(name) {
    currentView = name;
    AUTH_VIEWS.forEach(function (v) {
      var elv = el('auth-view-' + v);
      if (elv) elv.style.display = (v === name) ? 'block' : 'none';
    });
    var modal = el('auth-modal');
    if (modal) modal.setAttribute('aria-labelledby', AUTH_VIEW_TITLE_IDS[name] || 'auth-title');
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
      if (!turnstileVistTid) turnstileVistTid = Date.now();
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
      ensureAppleSdk();   // viser Apple-knappen, hvis __APPLE_CLIENT_ID er sat
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
    if (m.indexOf('email not confirmed') >= 0) return 'Bekræft din email, før du logger ind.';
    // Fra signup-hook'et (scripts/supabase-signup-turnstile-hook.sql), hvis det
    // er aktiveret - Supabase pakker hook-fejlens 'message' ind som res.error.message.
    if (m.indexOf('bot-tjek') >= 0) return err.message;
    if (m.indexOf('rate') >= 0 || m.indexOf('too many') >= 0) return 'For mange forsøg - vent lidt og prøv igen.';
    if (m.indexOf('network') >= 0 || m.indexOf('fetch') >= 0) return 'Ingen forbindelse. Tjek dit netværk.';
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
  // Hvornaar signup-formularen sidst blev vist. Bruges til at skelne "brugeren
  // har ikke loest udfordringen endnu" fra "bot-tjekket kom aldrig i gang".
  var turnstileVistTid = 0;
  var TURNSTILE_TAALMODIGHED_MS = 8000;

  function turnstileFejlede(containerId) {
    // Turnstile kan fejle HELT TAVST: hverken token, error-callback eller
    // timeout-callback. Set paa madshopper.dk 10-08-2026, hvor sitekey'en ikke
    // svarede - widget'en rendrede kun sit skjulte input og lavede aldrig en
    // udfordring. Brugeren fik saa beskeden "Bekraeft venligst at du ikke er en
    // robot", selvom der intet var at bekraefte. Er der gaaet rigelig tid uden
    // token, er det ikke brugerens skyld.
    if (!turnstileVistTid) return false;
    return (Date.now() - turnstileVistTid) > TURNSTILE_TAALMODIGHED_MS;
  }

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
      //
      // Selve VERIFICERINGEN sker IKKE laengere her, men server-side i et
      // Supabase "before user created"-hook (scripts/supabase-signup-turnstile-
      // hook.sql), fordi et rent klient-side tjek kunne omgaas ved at kalde
      // Supabases signup-endpoint direkte, uden om denne fil (produktions-
      // revision 18-08-2026, blokerer #5). Vi tjekker her KUN at widget'en
      // overhovedet har produceret et token - selve gyldigheden afgoer hook'et.
      // Et Turnstile-token er ENGANGS: kaldte vi verificerings-workeren HER
      // (som foer), ville hook'ets efterfoelgende forsoeg altid fejle, fordi
      // tokenet allerede var brugt op.
      var tsToken = '';
      if (authMode === 'signup') {
        tsToken = turnstileToken('auth-turnstile-widget');
        if (!tsToken) {
          setError(turnstileFejlede('auth-turnstile-widget')
            ? 'Bot-tjekket kunne ikke indlæses. Prøv at genindlæse siden - virker det stadig ikke, så skriv til os via Feedback.'
            : 'Bekræft venligst at du ikke er en robot.');
          return false;
        }
      }
      var res = (authMode === 'signup')
        ? await SB.auth.signUp({
            email: email, password: pw,
            // Bekræftelses-linket sender brugeren tilbage til dér, de oprettede
            // sig (localhost under test, madshopper.dk i prod). Origin skal stå
            // i Supabase' Redirect URLs-liste. turnstile_token laeses af
            // signup-hook'et (raw_user_meta_data) og fjernes ikke automatisk
            // bagefter - det er ét brugt engangs-token, ufarligt at beholde.
            options: {
              emailRedirectTo: window.location.origin,
              data: { display_name: signupName, turnstile_token: tsToken }
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

  /* ---------------------------------------------- Sign in with Apple (web)
     App-paritet (AuthContext.tsx signInApple). IKKE aktiv endnu: kræver et
     rigtigt Apple Developer-oprettet Services ID i window.__APPLE_CLIENT_ID
     (se base.html) og Apple-provideren konfigureret i Supabase (Team ID/
     Key ID/privat nøgle) - intet Apple Developer-medlemskab findes pr.
     2026-08-17. Koden er klar til at slås til den dag opsætningen er på
     plads: knappen forbliver skjult, indtil BÅDE SDK'en er loadet OG
     __APPLE_CLIENT_ID er udfyldt. */
  var appleSdkLoading = false;

  function showAppleButtonIfReady() {
    var btn = el('auth-apple-btn');
    if (btn && window.__APPLE_CLIENT_ID && window.AppleID) btn.style.display = 'flex';
  }

  function ensureAppleSdk() {
    if (!window.__APPLE_CLIENT_ID || window.AppleID || appleSdkLoading) return;
    appleSdkLoading = true;
    var s = document.createElement('script');
    s.src = 'https://appleid.cdn-apple.com/appleauth/static/jsapi/appleid/1/en_US/appleid.auth.js';
    s.async = true;
    s.onload = function () { appleSdkLoading = false; showAppleButtonIfReady(); };
    s.onerror = function () {
      appleSdkLoading = false;
      console.warn('[auth] Apple-SDK kunne ikke indlæses');
    };
    document.head.appendChild(s);
  }

  async function signInApple() {
    if (!initClient() || !window.AppleID || !window.__APPLE_CLIENT_ID) return;
    try {
      var rawNonce = randomNonce();
      var hashedNonce = await sha256Hex(rawNonce);
      window.AppleID.auth.init({
        clientId: window.__APPLE_CLIENT_ID,
        scope: 'name email',
        redirectURI: window.location.origin,
        usePopup: true,
        nonce: hashedNonce
      });
      var data = await window.AppleID.auth.signIn();
      var idToken = data && data.authorization && data.authorization.id_token;
      if (!idToken) { setError('Manglende ID-token fra Apple.'); return; }
      var res = await SB.auth.signInWithIdToken({ provider: 'apple', token: idToken, nonce: rawNonce });
      if (res.error) {
        console.error('[auth] Apple-login-fejl:', res.error);
        setError(translateErr(res.error));
        return;
      }
      closeAuthModal();   // onAuthStateChange klarer resten (kurv-synk, UI)
    } catch (err) {
      // Bruger annullerede popup'en = ingen fejl at vise.
      if (err && err.error === 'popup_closed_by_user') return;
      console.error('[auth] Apple-login-undtagelse:', err);
      setError('Apple-login mislykkedes. Prøv igen.');
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
      if (currentUser && window.CartBridge) { await pushCart(window.CartBridge.get()); }
      // Ejerskab og kvittering foelger brugeren - uden dette ville naeste
      // bruger paa samme browser arve dem og faa sin serverkurv overskrevet.
      // Ryddes EFTER pushCart: pushCart skriver dem selv igen ved succes
      // (linje 142-143), saa en rydning FOER pushet blev straks overskrevet -
      // naeste login saa "lokal kurv = serverens kvittering" og pushede den
      // (tomme) lokale kurv over den rigtige, gemte kurv.
      _writeLS(OWNER_KEY, null);
      _writeLS(SYNCED_KEY, null);
      await SB.auth.signOut();
    } catch (e) { /* ignorér */ }
    closeAuthModal();
  }

  async function deleteAccount() {
    if (!SB || !currentUser) return;
    if (!window.confirm('Er du sikker? Din konto og gemte kurv slettes permanent og kan ikke gendannes.')) return;
    // INGEN _dev-suffiks her, i modsaetning til alle andre RPC'er: der findes
    // kun EN delete_own_account (docs/native-app.md §10.3), fordi der kun er
    // ét Auth-projekt - kontoen er den samme uanset miljø. rpcName() ville
    // have kaldt delete_own_account_dev paa staging, som ikke findes, saa
    // "Slet konto" fejlede der. Appen har altid gjort det rigtige
    // (apps/mobile/src/config/env.ts::rpcName undtager netop dette navn).
    var rpcName = 'delete_own_account';
    var uid = currentUser.id;
    var deleted = false;
    try {
      var res = await SB.rpc(rpcName);
      deleted = !(res && res.error);
    } catch (e) { deleted = false; }
    if (!deleted) {
      window.alert('Kontoen kunne ikke slettes. Prøv igen eller skriv til os.');
      return;
    }
    try { await SB.auth.signOut(); } catch (e) { /* ignorér */ }
    try { localStorage.setItem('cart', '[]'); } catch (e) { /* ignorér */ }
    // Ryd ogsaa de lokalt gemte lister for denne bruger - ellers ligger de
    // som et forladt localStorage-lig efter kontoen (og RLS-raekken) er vaek.
    if (uid) {
      try { localStorage.removeItem('savedLists:' + uid); } catch (e) { /* ignorér */ }
    }
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
      // Kvittering til PASSWORD_RECOVERY-haandteringen i boot(): et link
      // accepteres kun hvis DENNE enhed selv bad om nulstilling for netop
      // denne email for nylig. Se kommentaren ved PENDING_RESET_KEY.
      _writeLS(PENDING_RESET_KEY, JSON.stringify({ email: email.trim().toLowerCase(), ts: Date.now() }));
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
  //
  // Kalder Supabases raa Auth-API direkte (PUT /auth/v1/user) med access_token
  // fra selve PASSWORD_RECOVERY-haendelsen som Bearer-header, i stedet for
  // SB.auth.updateUser(). Fundet 18-08-2026 ved rigtig produktionstest:
  // supabase-js@2.110.8's interne session-tilstand var TABT paa dette
  // tidspunkt hver eneste gang - getSession() viste null, OG et eksplicit
  // SB.auth.setSession(sammeTokens) lige foer rapporterede success UDEN at
  // rette det (updateUser() fejlede stadig med AuthSessionMissingError
  // umiddelbart efter). Et raat REST-kald med tokenet som Bearer-header
  // kraever intet "session-opslag" i klienten og er derfor immunt over for
  // hvad end der er buggy i biblioteket her.
  async function submitNewPassword(e) {
    if (e) e.preventDefault();
    if (!SB) return false;
    var pw = (el('auth-newpw') || {}).value || '';
    if (pw.length < 8) { setMsg('auth-newpw-msg', 'Adgangskoden skal være mindst 8 tegn.', true); return false; }
    setBusyBtn('auth-newpw-btn', true);
    try {
      var errMsg = null;
      var updatedUser = null;
      // MIDLERTIDIGT (fjernes naar det er bekraeftet virkende, 18-08-2026):
      // forrige "fix" saa ogsaa korrekt ud og virkede stadig ikke - vis den
      // raa fejl igen i stedet for at antage.
      var _usedSid = (_recoveryTokens && _recoveryTokens.access_token) ? _jwtSessionId(_recoveryTokens.access_token) : null;
      var _diag = 'har ikke _recoveryTokens: ' + JSON.stringify(_recoveryTokens);

      if (_recoveryTokens && _recoveryTokens.access_token) {
        var resp;
        try {
          resp = await fetch(window.__SB_URL + '/auth/v1/user', {
            method: 'PUT',
            headers: {
              'apikey': window.__SB_KEY,
              'Authorization': 'Bearer ' + _recoveryTokens.access_token,
              'Content-Type': 'application/json'
            },
            body: JSON.stringify({ password: pw })
          });
        } catch (fetchErr) {
          errMsg = 'fetch kastede: ' + (fetchErr && (fetchErr.name + ': ' + fetchErr.message));
          _diag = 'fetch selv fejlede';
        }
        if (resp) {
          var data = await resp.json().catch(function () { return null; });
          _diag = 'HTTP ' + resp.status + ' body=' + JSON.stringify(data);
          if (!resp.ok) {
            errMsg = (data && (data.msg || data.error_description || data.message)) || ('HTTP ' + resp.status);
          } else {
            updatedUser = data;
            try { await SB.auth.setSession(_recoveryTokens); } catch (e3) { /* ignorér */ }
          }
        }
      } else {
        var res = await SB.auth.updateUser({ password: pw });
        if (res.error) errMsg = res.error.message;
        else updatedUser = res.data && res.data.user;
      }

      if (errMsg) {
        var _logStr = _eventLog.map(function (l) {
          return l.t + 'ms ' + l.event + ' sid=' + l.sid + ' view=' + l.view;
        }).join(' | ');
        console.error('[auth] ny-kode-fejl:', _diag, errMsg, _eventLog);
        setMsg('auth-newpw-msg', translateErr({ message: errMsg }) +
          ' [DEBUG brugt-sid=' + _usedSid + ' | ' + _diag + ' | LOG: ' + _logStr + ']', true);
        return false;
      }
      _recoveryTokens = null;   // brugt - engangs, som selve linket
      setMsg('auth-newpw-msg', '');
      // Fjern recovery-tokenet fra URL'en, så et reload ikke gentager flowet.
      try { history.replaceState(null, '', window.location.pathname + window.location.search); } catch (e2) { /* ignorér */ }
      var user = updatedUser || currentUser;
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
      _eventLog.push({
        t: Date.now() - _pageLoadTs,
        event: event,
        hasSession: !!session,
        sid: (session && session.access_token) ? _jwtSessionId(session.access_token) : null,
        view: currentView
      });
      if (event === 'PASSWORD_RECOVERY') {
        // Duplikat-affyring: se kommentaren nedenfor ved den egentlige
        // rodårsag - ignorér enhver affyring efter den første for samme flow.
        if (currentView === 'newpassword') return;

        // Se kommentaren ved PENDING_RESET_KEY: kun fortsæt hvis DENNE enhed
        // selv har bedt om en nulstilling for netop den email sessionen er
        // for. sessionEmail kommer fra Supabase (ikke fra URL'en, som en
        // angriber kontrollerer) - en angriber kan kun fremstille gyldige
        // tokens for en konto de selv ejer, aldrig for offerets.
        var pendingEmail = _readPendingResetEmail();
        var sessionEmail = ((session && session.user && session.user.email) || '').trim().toLowerCase();
        if (!pendingEmail || sessionEmail !== pendingEmail) {
          SB.auth.signOut().catch(function () {});
          openAuthModal('reset');
          setMsg('auth-reset-msg', 'Linket matcher ikke en nulstilling, du selv har bedt om på denne enhed. Bed om et nyt link.', true);
          return;
        }
        _writeLS(PENDING_RESET_KEY, null);
        // Brugeren kom fra "glemt kode"-mailen → vis "sæt ny kode"-visningen.
        if (session && session.user) currentUser = session.user;
        // Se kommentaren ved _recoveryTokens - gemmes til brug ved selve
        // updateUser-kaldet, uafhængigt af SDK'ens egen session-tilstand.
        _recoveryTokens = (session && session.access_token && session.refresh_token)
          ? { access_token: session.access_token, refresh_token: session.refresh_token }
          : null;

        // RODÅRSAGEN (fundet 18-08-2026 ved fuld hændelseslog i produktion):
        // recovery-sessionen dør IKKE af en duplikat-hændelse eller en
        // rotation vi kunne følge med i - den blev tilbagekaldt SERVER-SIDE
        // af sig selv, ca. 600ms efter oprettelse, uden noget eksplicit
        // signOut()-kald fra vores kode nogen steder (bekræftet: hændelses-
        // loggen viste "SIGNED_OUT sid=null" spontant, aldrig udløst af os).
        // Mest sandsynlige forklaring: autoRefreshToken (sat i initClient)
        // forsøger automatisk at forny det kortlivede recovery-access-token
        // i baggrunden, og selve fornyelsesforsøget ser ud til at
        // tilbagekalde HELE sessionen (recovery-sessioner er formentlig
        // bevidst ikke fornyelige, af sikkerhedshensyn). Løsningen er at
        // løsrive klienten LOKALT fra denne session med det samme - scope:
        // 'local' rammer kun denne fane/enhed, IKKE selve access_token'et
        // server-side (det raa REST-kald i submitNewPassword bruger stadig
        // det gyldige, allerede-udstedte token direkte) - så
        // autoRefreshToken aldrig får chancen for at forsøge fornyelsen der
        // ser ud til at udløse tilbagekaldelsen.
        SB.auth.signOut({ scope: 'local' }).catch(function () {});

        openAuthModal('newpassword');
        return;
      }
      // Vi styrer selv currentUser/_recoveryTokens uafhængigt af SDK'ens
      // session-tilstand mens "sæt ny adgangskode" er åben (se ovenfor) -
      // ignorér ALT andet herfra, inklusive den SIGNED_OUT vores egen
      // lokale signOut() lige har udløst, som ellers ville nulstille
      // currentUser og tømme den lokale kurv via handleSignedOut().
      if (currentView === 'newpassword') return;

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
  window.authApple = signInApple;
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
