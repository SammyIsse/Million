"""Spar-scraper - tynd wrapper om den fælles Dagrofa-scraper.

Al scraping-logik ligger i dagrofa_scraper.py (delt med Meny og Min Købmand,
der kører på samme webshop-platform). Spar scrapes og gemmes fortsat helt
adskilt fra de andre butikker - denne fil kører kun Spar.
"""

from dagrofa_scraper import run

if __name__ == "__main__":
    run("spar")
