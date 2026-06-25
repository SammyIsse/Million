"""
Hent og gem Føtex+ id_token til brug i foetex_plus_priser.py.

Auth-flow:
  1. POST accounts.login til Gigya  → loginToken + UID
  2. POST accounts.getJWT til Gigya → Gigya JWT
  3. POST /tokenised til p-idp.dsgapps.dk → DSG id_token

Kræver:
  FOETEX_EMAIL       - din Føtex+ e-mail
  FOETEX_PASSWORD    - dit Føtex+ kodeord
  FOETEX_GIGYA_KEY   - Gigya site-API-key for Føtex+ appen
                       (kan captures med Charles Proxy / mitmproxy mens
                        appen logger ind — se 'Sådan finder du Gigya-nøglen'
                        nedenfor)

Sådan finder du Gigya-nøglen:
  1. Installer Charles Proxy eller mitmproxy på din PC/Mac
  2. Konfigurer din telefon til at bruge proxyen
  3. Åbn Føtex+ appen og log ind
  4. Kig efter en request til accounts.eu1.gigya.com der indeholder &apiKey=3_...
  5. Kopiér værdien og sæt FOETEX_GIGYA_KEY i .env
"""
import os, json, sys, requests
from dotenv import load_dotenv

load_dotenv()

GIGYA_BASE = 'https://accounts.eu1.gigya.com'
IDP_URL    = 'https://p-idp.dsgapps.dk/tokenised'
TOKEN_FILE = os.path.join(os.path.dirname(__file__), '_foetex_token.json')


def gigya_login(api_key: str, email: str, password: str) -> dict:
    r = requests.post(f'{GIGYA_BASE}/accounts.login', data={
        'apiKey':   api_key,
        'loginID':  email,
        'password': password,
        'format':   'json',
    }, timeout=15)
    r.raise_for_status()
    data = r.json()
    if data.get('errorCode', 0) != 0:
        raise RuntimeError(f"Gigya login fejl: {data.get('errorMessage')} ({data.get('errorCode')})")
    return data


def gigya_get_jwt(api_key: str, uid: str, uid_sig: str, sig_timestamp: str) -> str:
    r = requests.post(f'{GIGYA_BASE}/accounts.getJWT', data={
        'apiKey':       api_key,
        'UID':          uid,
        'UIDSignature': uid_sig,
        'signatureTimestamp': sig_timestamp,
        'fields':       'email,profile.email',
        'format':       'json',
    }, timeout=15)
    r.raise_for_status()
    data = r.json()
    if data.get('errorCode', 0) != 0:
        raise RuntimeError(f"Gigya getJWT fejl: {data.get('errorMessage')} ({data.get('errorCode')})")
    return data['id_token']


def exchange_for_dsg_token(gigya_jwt: str) -> str:
    r = requests.post(IDP_URL, json={
        'grant_type': 'urn:idp.dsgapps.dk:params:oauth:grant-type:gigya-exchange',
        'id_token':   gigya_jwt,
    }, timeout=15)
    r.raise_for_status()
    data = r.json()
    token = data.get('id_token') or data.get('access_token')
    if not token:
        raise RuntimeError(f"Ingen token i IDP-svar: {data}")
    return token


def main():
    api_key  = os.getenv('FOETEX_GIGYA_KEY', '')
    email    = os.getenv('FOETEX_EMAIL', '')
    password = os.getenv('FOETEX_PASSWORD', '')

    if not api_key:
        print('FEJL: FOETEX_GIGYA_KEY mangler i .env')
        print('Se kommentaren øverst i denne fil for vejledning.')
        sys.exit(1)
    if not email or not password:
        print('FEJL: FOETEX_EMAIL og FOETEX_PASSWORD skal sættes i .env')
        sys.exit(1)

    print(f'Logger ind som {email}...')
    login_data = gigya_login(api_key, email, password)
    uid       = login_data['UID']
    uid_sig   = login_data['UIDSignature']
    sig_ts    = login_data['signatureTimestamp']
    print('  Gigya login OK')

    gigya_jwt = gigya_get_jwt(api_key, uid, uid_sig, sig_ts)
    print('  Gigya JWT hentet')

    dsg_token = exchange_for_dsg_token(gigya_jwt)
    print('  DSG id_token hentet')

    with open(TOKEN_FILE, 'w') as f:
        json.dump({'id_token': dsg_token}, f)
    print(f'  Token gemt i {TOKEN_FILE}')
    print('\nKør nu: python scraper/foetex_plus_priser.py')


if __name__ == '__main__':
    main()
