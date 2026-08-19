#!/usr/bin/env bash
# Installerer Playwright + Chromium i CI, robust nok til at det faktisk lykkes.
#
# Brug:  bash scripts/install-playwright-ci.sh [version]
#
# HISTORIKKEN, kort - laes den foer du "forenkler" scriptet:
#
# 1) Oprindeligt et bart `npx playwright install --with-deps chromium`.
#    Det HANG af og til i det uendelige og aad hele jobbets timeout.
# 2) ff28c7d pakkede det ind i `timeout 240` + 3 forsoeg. Det gjorde ondt
#    vaerre, og fejlen ramte fire koersler paa én gang 19-08-2026:
#
#      forsoeg 1: timeout 240 udloeb mens apt-get stadig hentede pakkelister
#      forsoeg 2: E: Could not get lock ... held by process 2481 (apt-get)
#      forsoeg 3: E: Could not get lock ... held by process 2481 (apt-get)
#
#    `timeout` draeber nemlig KUN sin egen wrapper. Den apt-get, den satte i
#    gang, lever videre og holder /var/lib/apt/lists/lock - saa hvert
#    efterfoelgende forsoeg fejler ØJEBLIKKELIGT. Retry-loekken kunne per
#    konstruktion aldrig lykkes efter en timeout.
#
# Derfor goer dette script tre ting, som alle tre er noedvendige:
#
#   a) laengere timeout (240s var simpelthen for lidt - apt var stadig i gang
#      med at hente pakkelister da den udloeb),
#   b) rydder op efter en timeout: draeber den efterladte apt-get og VENTER
#      paa at laasen slippes, foer der proeves igen,
#   c) sidste forsoeg dropper `--with-deps` og henter kun selve browseren.
#      GitHubs ubuntu-runnere har i forvejen stort set alle Chromium-
#      afhaengigheder installeret, saa apt-trinnet er det skroebelige led -
#      ikke noget vi faktisk har brug for.
set -uo pipefail

VERSION="${1:-1.61.1}"
TIMEOUT_S="${PLAYWRIGHT_INSTALL_TIMEOUT:-420}"

npm install "playwright@${VERSION}" || exit 1

# Venter (maks. 60s) paa at apt-laasen er fri igen efter en afbrudt koersel.
vent_paa_apt_laas() {
  for _ in $(seq 1 30); do
    if ! sudo fuser /var/lib/apt/lists/lock /var/lib/dpkg/lock-frontend >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  echo "::warning::apt-laasen blev aldrig fri - fortsaetter alligevel"
}

for forsoeg in 1 2 3; do
  if [ "$forsoeg" -eq 3 ]; then
    # Sidste forsoeg: kun browser-binaeren, intet apt. Se punkt (c) ovenfor.
    echo "Forsøg 3/3: henter kun Chromium-binæren (uden --with-deps)"
    if timeout "$TIMEOUT_S" npx playwright install chromium; then
      echo "Playwright installeret (forsøg 3/3, uden systemafhængigheder)"
      exit 0
    fi
  elif timeout "$TIMEOUT_S" npx playwright install --with-deps chromium; then
    echo "Playwright installeret (forsøg ${forsoeg}/3)"
    exit 0
  fi

  echo "::warning::Playwright-install fejlede/hængte (forsøg ${forsoeg}/3)"
  # Ryd op efter os selv, ellers arver naeste forsoeg en laast apt.
  sudo pkill -f 'apt-get' 2>/dev/null || true
  sudo pkill -f 'playwright.*install' 2>/dev/null || true
  vent_paa_apt_laas
done

echo "::error::Playwright kunne ikke installeres efter 3 forsøg"
exit 1
