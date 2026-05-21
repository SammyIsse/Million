from supabase import create_client
from dotenv import load_dotenv
import os

load_dotenv()

# Hent Supabase URL og Key
url = os.environ.get("NEXT_PUBLIC_SUPABASE_URL") or os.environ.get("SUPABASE_URL")
key = os.environ.get("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY") or os.environ.get("SUPABASE_KEY")
if key and (key.startswith("http://") or key.startswith("https://")):
    key = os.environ.get("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY")

print(f"Forbinder til Supabase URL: {url}")
if key == "HIDDEN" or not key:
    print("\n[!] Bemærk: Erstat 'HIDDEN' med din rigtige Supabase anon/publishable key i din .env fil.")
else:
    try:
        supabase = create_client(url, key)
        print("Supabase klient initialiseret succesfuldt.")
        
        # Test indsættelse i feedback tabellen
        print("Forsøger at indsætte testrække i 'feedback' tabellen...")
        response = supabase.table("feedback").insert({
            "feedback_type": "feedback",
            "message": "Dette er en test-feedback besked fra test.py",
            "created_at": "2026-05-20T21:00:00"
        }).execute()
        print("Succes! Række indsat:")
        print(response)
    except Exception as e:
        print(f"\n[Fejl] Kunne ikke udføre test-forespørgsel: {e}")