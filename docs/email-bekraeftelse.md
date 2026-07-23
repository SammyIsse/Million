# Email+adgangskode-login (udskudt) + branded email

**Status (2026-07-23):** Login er **kun Google** for nu. Email+adgangskode-login
er **skjult** i login-modalen. Der sendes **ingen** emails fra siden overhovedet.

Email+adgangskode blev droppet for nu, fordi det uundgåeligt trækker email-krav
med sig (bekræftelses-mail ved oprettelse, og password-nulstilling senere), og vi
ville hverken sende mails fra Supabases eget domæne eller tilføje en ekstern
email-service. Google-login dækker behovet uden emails.

## Hvad der allerede virker (skal IKKE bygges igen)

Backend + frontend understøtter allerede email+adgangskode fuldt ud:

- `static/js/auth.js` → `submitForm()` håndterer signup + login, og både
  bekræftelse TIL ("tjek din email") og FRA (direkte login). Sender
  `emailRedirectTo: window.location.origin`.
- RLS, `carts`-tabel, kurv-synk, `delete_own_account` — ens uanset login-metode,
  testet 12/12.
- Supabase Email-provider + "Allow new users to sign up" er slået **til**.

Der mangler kun: vise UI-blokken igen + håndtere emails (én af to veje nedenfor).

## Sådan genaktiveres email+adgangskode senere

### Trin 1 — vis UI-blokken igen
I `templates/base.html`, i `#auth-view-login`: email+adgangskode-blokken (divider
+ `<form id="auth-form">` + `.auth-switch`) er pakket ind i en Jinja-kommentar
markeret `--- EMAIL+ADGANGSKODE SKJULT ---`. Fjern kommentar-markørerne (`{#` i
toppen og `#}` i bunden) — koden er intakt. Bump `?v=` på base.html's CSS/JS og
genstart/deploy.

### Trin 2 — vælg én af to veje til emails

**Vej A — ingen bekræftelses-mail (hurtigst, ingen ekstern service):**
Slå email-bekræftelse fra via Supabase Management API (dashboard-toggle fandtes
ikke i UI'en pr. juli 2026):
```bash
curl -X PATCH "https://api.supabase.com/v1/projects/oxzxingkbsnqzpmjtktr/config/auth" \
  -H "Authorization: Bearer <SUPABASE_PERSONAL_ACCESS_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"mailer_autoconfirm": true}'
```
Token laves på `https://supabase.com/dashboard/account/tokens` (starter `sbp_`).
Så logger email-brugere direkte ind uden mail. Ulempe: uverificerede emails +
ingen "glemt adgangskode" (den kræver mail → så Vej B).

**Vej B — branded emails (custom SMTP, fx Resend):**
1. Opret konto hos Resend (gratis ~3.000/md).
2. Verificér `madshopper.dk` → SPF+DKIM DNS-poster i **Cloudflare DNS**.
3. Supabase → Authentication → Emails → **SMTP Settings** → custom SMTP til,
   **Sender email** = `konto@madshopper.dk`, **Sender name** = `MadShopper`.
4. Tilret evt. teksten under Authentication → Emails → Templates.
Giver både branded bekræftelses-mail OG password-nulstilling, og fjerner
produktions-rate-limit.

## Anbefaling
Til lancering er **Google-login alene fint**. Vil du senere have email+adgangskode
med password-nulstilling og professionelt look, så tag **Vej B**.
