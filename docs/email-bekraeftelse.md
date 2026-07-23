# Email+adgangskode med branded mails (opsætning)

**Status (2026-07-23):** Koden til email+adgangskode + "glemt adgangskode" er
**bygget og lokalt verificeret**, men endnu **ikke deployet** (venter på Supabase-
opsætningen nedenfor, så det går live i rigtig tilstand). Produktion kører stadig
Google-only indtil deploy.

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
5. **Deaktivér bekræftelse** via Management API:
   ```bash
   curl -X PATCH "https://api.supabase.com/v1/projects/oxzxingkbsnqzpmjtktr/config/auth" \
     -H "Authorization: Bearer <SUPABASE_PERSONAL_ACCESS_TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{"mailer_autoconfirm": true}'
   ```
   (Token: `https://supabase.com/dashboard/account/tokens`, starter `sbp_`.)
6. **Deploy** (push til main → deploy-edge.yml) → email+adgangskode live, ingen
   bekræftelses-mail, branded "glemt kode" fra MadShopper.

## Sådan slås bekræftelse TIL igen senere
Sæt `"mailer_autoconfirm": false` i samme curl. Så sender Supabase en
bekræftelses-mail ved signup (nu branded, via SMTP'en). "Confirm sign up"-teksten
redigeres under Authentication → Emails → Templates.

## Verificeret data-/sikkerhedslag (uændret)
RLS, `carts`, kurv-synk og `delete_own_account` er ens uanset login-metode —
testet 12/12.
