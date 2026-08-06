"""
Web-scan homepage firme za cjenik/checkout signale.
Ako nađe signal → firma ima online shop → status 'rejected' (nije quote-based).
"""
import logging
import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

# Ključne riječi koje ukazuju na B2C online shop (nije quote-based)
SHOP_KEYWORDS = [
    "dodaj u košaricu", "add to cart", "kupi odmah", "kupi online",
    "naruči online", "online shop", "webshop", "web shop",
    "checkout", "košarica", "shopping cart",
    "woocommerce", "shopify",
]

# URL pattern signali
SHOP_URL_PATTERNS = [
    r"/shop/", r"/store/", r"/cart/", r"/checkout/",
    r"/webshop/", r"/web-shop/", r"/kosarica/",
]

# Ključne riječi cjenika — prisutnost SAMA nije dovoljna (može biti "upit za cijenu")
PRICE_LIST_KEYWORDS = [
    "cjenik", "cjenici", "pricelist", "price list",
    "cijena po komadu", "cijena po m2", "cijena po m²",
]

# Fraze koje ISKLJUČUJU (firma prima upite, nije B2C shop)
QUOTE_INDICATORS = [
    "zatražite ponudu", "tražite ponudu", "upit za ponudu",
    "besplatna procjena", "besplatni pregled", "besplatna konzultacija",
    "kontaktirajte nas za cijenu", "pošaljite upit",
]


def scan_website(session, url: str) -> dict:
    """
    Dohvaća homepage i analizira signale.
    Vraća {'is_shop': bool, 'reason': str}.
    """
    if not url:
        return {"is_shop": False, "reason": "no_url"}

    # Normaliziraj URL
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        resp = session.get(url, timeout=15, allow_redirects=True)
        # Ignoriramo 404, 5xx — ne možemo znati bez stranice
        if resp.status_code >= 400:
            return {"is_shop": False, "reason": f"http_{resp.status_code}"}
    except Exception as e:
        log.debug("web_scan greška za %s: %s", url, e)
        return {"is_shop": False, "reason": "connection_error"}

    html = resp.text.lower()
    soup = BeautifulSoup(resp.text, "html.parser")
    page_text = soup.get_text(separator=" ").lower()

    # Provjeri URL-ove linkova na stranici
    all_hrefs = [a.get("href", "").lower() for a in soup.find_all("a", href=True)]
    for pattern in SHOP_URL_PATTERNS:
        matches = [h for h in all_hrefs if re.search(pattern, h)]
        if matches:
            return {"is_shop": True, "reason": f"shop_url:{pattern}"}

    # Provjeri shop ključne riječi u HTML-u/tekstu
    for kw in SHOP_KEYWORDS:
        if kw in html:
            # Provjeri nije li to samo mention bez prave funkcionalnosti
            # (npr. "mi nemamo web shop") — gruba provjera
            context_start = html.find(kw)
            context = html[max(0, context_start - 60):context_start + 60]
            if not any(neg in context for neg in ["nema", "nemamo", "ne nudimo"]):
                return {"is_shop": True, "reason": f"shop_keyword:{kw}"}

    # Provjeri cjenik BEZ quote indikatora
    has_price_list = any(kw in page_text for kw in PRICE_LIST_KEYWORDS)
    has_quote_flow = any(kw in page_text for kw in QUOTE_INDICATORS)

    if has_price_list and not has_quote_flow:
        return {"is_shop": True, "reason": "price_list_no_quote"}

    return {"is_shop": False, "reason": "clean"}


def extract_homepage_text(session, url: str, max_chars: int = 3000) -> str:
    """
    Dohvaća homepage tekst za Claude API sažetak.
    Fokusira se na about/main sadržaj, ne navigaciju.
    """
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        resp = session.get(url, timeout=15, allow_redirects=True)
        if resp.status_code >= 400:
            return ""
    except Exception:
        return ""

    soup = BeautifulSoup(resp.text, "html.parser")

    # Ukloni navigaciju, footer, skripte
    for tag in soup(["nav", "footer", "script", "style", "header"]):
        tag.decompose()

    # Prioritiziramo main/article sadržaj
    main = soup.find("main") or soup.find("article") or soup.find(id="content") or soup.body
    text = main.get_text(separator=" ", strip=True) if main else ""

    # Normaliziraj whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]
