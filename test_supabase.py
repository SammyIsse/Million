from supabase import create_client
from dotenv import load_dotenv
import os
from postgrest.exceptions import APIError

load_dotenv()

url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_KEY")

print("URL:", url)
print("KEY:", key[:20] if key else None)

if not url or not key:
    print("Error: SUPABASE_URL or SUPABASE_KEY is missing in your .env file!")
    exit(1)

supabase = create_client(url, key)

tables_to_test = ["price_history", "cart_popularity", "price_alerts", "feedback"]

print("\nTester forbindelse til dine Supabase-tabeller:")
for table in tables_to_test:
    try:
        response = supabase.table(table).select("*").limit(1).execute()
        print(f"✅ Tabel '{table}' findes og virker!")
    except APIError as e:
        if e.code == 'PGRST205' or "Could not find" in str(e):
            print(f"❌ Tabel '{table}' findes IKKE i din Supabase database endnu.")
        else:
            print(f"⚠️ Tabel '{table}' fejlede med en anden fejl: {e}")
    except Exception as e:
        print(f"⚠️ Tabel '{table}' fejlede: {e}")

