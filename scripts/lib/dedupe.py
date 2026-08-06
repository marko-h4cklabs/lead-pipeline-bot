"""
Troslojna deduplikacija:
  1. OIB — direktni match
  2. naziv_firme (normalizirani) + telefon (normalizirani)
  3. naziv_firme (normalizirani) + web domena (eTLD+1)

Svaki match na bilo kojoj razini = duplikat.
"""
import re
import unicodedata
from urllib.parse import urlparse


def normalize_name(name: str) -> str:
    """Lowercase, bez d.o.o./j.d.o.o./d.d./obrt sufiksa, bez interpunkcije, normalizirani whitespace."""
    if not name:
        return ""
    s = name.lower()
    # ukloni pravne forme
    s = re.sub(
        r"\b(d\.?o\.?o\.?|j\.?d\.?o\.?o\.?|d\.?d\.?|obrt|j\.?t\.?d\.?|k\.?d\.?|g\.?i\.?u\.?)\b",
        "",
        s,
    )
    # ukloni dijakritike
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    # ukloni sve ne-alfanumeričko
    s = re.sub(r"[^a-z0-9\s]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def normalize_phone(phone: str) -> str:
    """Samo znamenke, bez + i vodećih nula."""
    if not phone:
        return ""
    digits = re.sub(r"\D", "", phone)
    # ukloni međunarodni prefix 385 (Hrvatska)
    if digits.startswith("385"):
        digits = digits[3:]
    # ukloni vodeće nule
    digits = digits.lstrip("0")
    return digits


def extract_domain(url: str) -> str:
    """Vrati eTLD+1 (npr. 'firma.hr') iz URL-a ili domene."""
    if not url:
        return ""
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return ""
    host = host.lower().removeprefix("www.")
    # Uzmi zadnje 2 dijela (eTLD+1 za .hr, .com itd.)
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


class DupeChecker:
    """
    Inicijaliziraj s listom postojećih redova (dict) iz Sheeta,
    pa pozovi is_duplicate(candidate) za svaki novi lead.
    """

    def __init__(self, existing: list[dict]):
        self._oibs: set[str] = set()
        self._name_phone: set[tuple[str, str]] = set()
        self._name_domain: set[tuple[str, str]] = set()

        for row in existing:
            oib = (row.get("oib") or "").strip()
            if oib:
                self._oibs.add(oib)

            nn = normalize_name(row.get("naziv_firme", ""))
            np = normalize_phone(row.get("telefon", ""))
            nd = extract_domain(row.get("web", ""))

            if nn and np:
                self._name_phone.add((nn, np))
            if nn and nd:
                self._name_domain.add((nn, nd))

    def is_duplicate(self, candidate: dict) -> tuple[bool, str]:
        """
        Vraća (True, razlog) ako je duplikat, inače (False, "").
        """
        oib = (candidate.get("oib") or "").strip()
        if oib and oib in self._oibs:
            return True, f"OIB match: {oib}"

        nn = normalize_name(candidate.get("naziv_firme", ""))
        np = normalize_phone(candidate.get("telefon", ""))
        nd = extract_domain(candidate.get("web", ""))

        if nn and np and (nn, np) in self._name_phone:
            return True, f"naziv+telefon match: {nn} / {np}"

        if nn and nd and (nn, nd) in self._name_domain:
            return True, f"naziv+domena match: {nn} / {nd}"

        return False, ""

    def register(self, candidate: dict) -> None:
        """Registriraj novi (ne-duplikat) lead u checker, za intra-batch dedupe."""
        oib = (candidate.get("oib") or "").strip()
        if oib:
            self._oibs.add(oib)

        nn = normalize_name(candidate.get("naziv_firme", ""))
        np = normalize_phone(candidate.get("telefon", ""))
        nd = extract_domain(candidate.get("web", ""))

        if nn and np:
            self._name_phone.add((nn, np))
        if nn and nd:
            self._name_domain.add((nn, nd))
