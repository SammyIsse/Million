# Email+adgangskode med branded mails (opsætning)

**Status (opdateret 18-08-2026):** Email+adgangskode + "glemt adgangskode" er
**live i produktion** - verificeret med en rigtig testbruger 18-08-2026, ingen
bekræftelses-mail krævet (`mailer_autoconfirm` er allerede sat). Denne fil
hævdede indtil da fejlagtigt at det stadig ventede på deploy. Det eneste
reelt udestående er **branded afsender** på selve mailene (trin 1-4
nedenfor) - password-reset-mails sendes i dag via Supabases generiske,
ubrandede standard-mailer, ikke fra `noreply@madshopper.dk`.

Mål: opret konto med email+adgangskode **uden bekræftelses-mail**, men **med**
"glemt adgangskode", og alle mails kommer branded fra **MadShopper**.

## Hvad der er bygget i koden (klar til deploy)

- `templates/base.html`: email+adgangskode-formularen er synlig igen, +
  "Glemt adgangskode?"-link, + to nye modal-visninger (`auth-view-reset`,
  `auth-view-newpassword`).
- `static/js/auth.js`:
  - `requestReset()` → `resetPasswordForEmail(email, {redirectTo: origin})`.
  - `submitNewPassword()` → `updateUser({password})` efter `PASSWORD_RECOVERY`.
  - View-håndtering (login / account / reset / newpassword).
- `static/css/styles.css`: `.auth-forgot`, `.auth-ok` (grøn kvittering).
- Redirect-URLs (madshopper.dk/** + localhost:5001/**) er allerede i Supabase.

## Opsætning der mangler (gøres af brugeren) — se hovedsamtalen for detaljer

1. **Resend** (send-only): konto → add domain `madshopper.dk`.
2. **Cloudflare DNS**: tilføj Resends SPF/DKIM-poster, **Proxy = DNS only** (grå sky).
3. **Resend → Verify**, hent **SMTP** host/port/user/pass.
4. **Supabase → Authentication → Emails → SMTP Settings**: sender email
   `noreply@madshopper.dk`, **Sender name `MadShopper`**, host/port/user/pass.
5. ~~Deaktivér bekræftelse~~ - allerede gjort, `mailer_autoconfirm` er `true`
   i produktion (bekræftet 18-08-2026).
6. ~~Deploy~~ - allerede sket, email+adgangskode har været live siden før
   18-08-2026.

## Sådan slås bekræftelse TIL igen senere
Sæt `"mailer_autoconfirm": false` i samme curl. Så sender Supabase en
bekræftelses-mail ved signup (nu branded, via SMTP'en). "Confirm sign up"-teksten
redigeres under Authentication → Emails → Templates.

## Verificeret data-/sikkerhedslag (uændret)
RLS, `carts`, kurv-synk og `delete_own_account` er ens uanset login-metode —
testet 12/12.
