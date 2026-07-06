#!/usr/bin/env bash
# Opsætter automatisk videresendelse af feedback til Google Sheet:
# https://docs.google.com/spreadsheets/d/1B4HvQggPFeFM9tV6etsoQPVmzSutHcmdpH4uEVxeUWg
#
# Bruger Googles officielle CLI ("clasp") til at oprette et Apps
# Script-projekt bundet til arket, pushe koden og deploye det som en
# offentlig Web App-webhook. Kræver to engangs-manuelle trin (se README
# nederst i scriptet), som IKKE kan automatiseres:
#   1. Aktivér Apps Script API på https://script.google.com/home/usersettings
#   2. Log ind via `npx clasp login` (åbner browser, du skal klikke "Tillad")
#
# Efter det gemmer scriptet webhook-URL'en i .feedback-webhook (git-ignoreret)
# og i .env, så app.py og deploy-worker.sh automatisk bruger den.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

SHEET_ID="1B4HvQggPFeFM9tV6etsoQPVmzSutHcmdpH4uEVxeUWg"
CLASP="npx --yes @google/clasp"
GAS_DIR="$(mktemp -d)/feedback-gas"
mkdir -p "$GAS_DIR"

echo "==> Tjekker Google-login (clasp)"
if ! $CLASP login --status >/dev/null 2>&1; then
  echo ""
  echo "Du skal logge ind med den Google-konto, der ejer regnearket."
  echo "Browseren åbner nu — godkend adgangen dér."
  echo ""
  echo "OBS: hvis clasp fejler med 'Apps Script API er ikke aktiveret',"
  echo "så slå den til her og prøv igen: https://script.google.com/home/usersettings"
  echo ""
  $CLASP login
fi

cat > "$GAS_DIR/appsscript.json" <<'JSON'
{
  "timeZone": "Europe/Copenhagen",
  "dependencies": {},
  "webapp": {
    "access": "ANYONE_ANONYMOUS",
    "executeAs": "USER_DEPLOYING"
  },
  "exceptionLogging": "STACKDRIVER",
  "runtimeVersion": "V8"
}
JSON

cat > "$GAS_DIR/Code.gs" <<'GS'
// Forhindrer CSV/formel-injection: Google Sheets tolker celler der starter
// med =, +, - eller @ som en formel. Webhooken er offentlig og uden login,
// så en apostrof foran den slags tegn tvinger cellen til at vise ren tekst.
function safeCell(value) {
  var s = String(value == null ? '' : value);
  return /^[=+\-@\t\r]/.test(s) ? "'" + s : s;
}

function doPost(e) {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();
  var data = JSON.parse(e.postData.contents);

  if (sheet.getLastRow() === 0) {
    sheet.appendRow(['Tidspunkt', 'Type', 'Navn', 'Email', 'Emne', 'Besked', 'Side URL']);
  }

  sheet.appendRow([
    safeCell(data.created_at || new Date().toISOString()),
    safeCell(data.type || ''),
    safeCell(data.name || ''),
    safeCell(data.email || ''),
    safeCell(data.subject || ''),
    safeCell(data.message || ''),
    safeCell(data.page_url || '')
  ]);

  return ContentService.createTextOutput(JSON.stringify({ ok: true }))
    .setMimeType(ContentService.MimeType.JSON);
}
GS

echo "==> Opretter Apps Script-projekt bundet til dit Sheet"
(
  cd "$GAS_DIR"
  $CLASP create --type sheets --title "MadShopper Feedback" --parentId "$SHEET_ID" --rootDir "$GAS_DIR"
  echo "==> Pusher script"
  $CLASP push -f
  echo "==> Deployer som Web App"
  $CLASP deploy --description "feedback-webhook" | tee "$GAS_DIR/deploy.log"
)

DEPLOYMENT_ID="$(grep -oE 'AKfycb[A-Za-z0-9_-]+' "$GAS_DIR/deploy.log" | head -1 || true)"
if [ -z "$DEPLOYMENT_ID" ]; then
  echo "Kunne ikke udtrække deployment-id automatisk."
  echo "Kør 'cd $GAS_DIR && $CLASP deployments' og find Web app-URL'en manuelt,"
  echo "og gem den derefter selv i .feedback-webhook og .env som GOOGLE_SHEET_WEBHOOK_URL."
  exit 1
fi

WEBHOOK_URL="https://script.google.com/macros/s/${DEPLOYMENT_ID}/exec"
echo ""
echo "Webhook-URL: $WEBHOOK_URL"

printf '%s' "$WEBHOOK_URL" > "$ROOT/.feedback-webhook"
echo "Gemt i .feedback-webhook (bruges automatisk af scripts/deploy-worker.sh)"

if [ -f "$ROOT/.env" ]; then
  if grep -q '^GOOGLE_SHEET_WEBHOOK_URL=' "$ROOT/.env"; then
    sed -i.bak "s#^GOOGLE_SHEET_WEBHOOK_URL=.*#GOOGLE_SHEET_WEBHOOK_URL=${WEBHOOK_URL}#" "$ROOT/.env"
    rm -f "$ROOT/.env.bak"
  else
    printf '\nGOOGLE_SHEET_WEBHOOK_URL=%s\n' "$WEBHOOK_URL" >> "$ROOT/.env"
  fi
  echo "Opdateret .env (lokal Flask-server bruger webhooken med det samme)"
fi

echo ""
echo "==> Tester webhook med en rigtig POST"
sleep 3  # Google Apps Script-deploys er ikke øjeblikkeligt aktive
curl -s -o /dev/null -w "HTTP %{http_code}\n" -X POST "$WEBHOOK_URL" \
  -H "Content-Type: application/json" \
  -d '{"type":"feedback","message":"Testbesked fra setup-feedback-sheet.sh","name":"Setup-script"}'

echo ""
echo "Tjek dit Google Sheet — der skulle nu ligge en testrække."
echo "Kør 'bash scripts/deploy-worker.sh' for at udrulle webhooken til produktionssitet."
