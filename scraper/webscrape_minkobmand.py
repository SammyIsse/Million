"""Min Købmand-scraper - tynd wrapper om den fælles Dagrofa-scraper.

Al scraping-logik ligger i dagrofa_scraper.py (delt med Spar og Meny,
der kører på samme webshop-platform). Min Købmand scrapes og gemmes fortsat
helt adskilt fra de andre butikker - denne fil kører kun Min Købmand.
"""

from dagrofa_scraper import run

if __name__ == "__main__":
    run("mk")
