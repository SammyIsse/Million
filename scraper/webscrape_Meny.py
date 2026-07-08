"""Meny-scraper - tynd wrapper om den fælles Dagrofa-scraper.

Al scraping-logik ligger i dagrofa_scraper.py (delt med Spar og Min Købmand,
der kører på samme webshop-platform). Meny scrapes og gemmes fortsat helt
adskilt fra de andre butikker - denne fil kører kun Meny.
"""

from dagrofa_scraper import run

if __name__ == "__main__":
    run("meny")
