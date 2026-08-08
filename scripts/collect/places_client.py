"""
Google Places API (New) klijent — textSearch za B2B niše u HR.

Koristi Places API (New): POST /v1/places:searchText
API key: env GOOGLE_PLACES_API_KEY
"""
import logging
import os
import time
from typing import Iterator

import requests

log = logging.getLogger(__name__)

PLACES_API_URL = "https://places.googleapis.com/v1/places:searchText"

# Sve županije HR osim Međimurske (20) i Varaždinske (05)
# Koristimo ih kao search bias regione — Places API nema direktni county filter
# pa dodajemo naziv županije u query kao fallback
HR_ZUPANIJI = [
    "Zagreb", "Karlovac", "Sisak", "Bjelovar", "Koprivnica",
    "Virovitica", "Požega", "Slavonski Brod", "Osijek", "Vukovar",
    "Zadar", "Šibenik", "Split", "Dubrovnik", "Rijeka",
    "Pula", "Gospić", "Krapina",
]

# Quote-based niše — servisne firme bez cjenika online
NISE_QUERIES = [
    "građevinska firma", "adaptacija stanova", "keramičar", "soboslikar",
    "vodoinstalater", "električar", "klimatizacija i ventilacija",
    "krovopokrivač", "fasader", "stolar", "bravar", "autolakirer",
    "servis kotlova", "dimnjačar", "odvoz smeća firma",
    "usluge čišćenja firme", "krajobrazno uređenje", "hortikultura",
    "zaštitarska tvrtka", "selitbene usluge",
]

FIELD_MASK = ",".join([
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.nationalPhoneNumber",
    "places.internationalPhoneNumber",
    "places.websiteUri",
    "places.businessStatus",
    "places.types",
    "places.addressComponents",
])


def _api_key() -> str:
    key = os.environ.get("GOOGLE_PLACES_API_KEY", "")
    if not key:
        raise ValueError("GOOGLE_PLACES_API_KEY nije postavljen")
    return key


def _parse_address_components(components: list[dict]) -> tuple[str, str]:
    """Izvlači grad i županiju iz addressComponents."""
    grad = ""
    zupanija = ""
    for comp in components:
        types = comp.get("types", [])
        long_name = comp.get("longText", "")
        if "locality" in types or "postal_town" in types:
            grad = long_name
        if "administrative_area_level_1" in types:
            zupanija = long_name
    return grad, zupanija


def search_places(
    query: str,
    location_bias_circle_center: tuple[float, float] | None = None,
    location_bias_radius_m: int = 50000,
    max_results: int = 20,
) -> list[dict]:
    """
    Jedno textSearch za danu nišu/query.
    Vraća listu dikt-ova u formatu kompatibilnom s lead shemom.
    """
    api_key = _api_key()
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": FIELD_MASK,
    }
    body = {
        "textQuery": query,
        "languageCode": "hr",
        "regionCode": "HR",
        "maxResultCount": min(max_results, 20),
    }
    if location_bias_circle_center:
        lat, lng = location_bias_circle_center
        body["locationBias"] = {
            "circle": {
                "center": {"latitude": lat, "longitude": lng},
                "radius": location_bias_radius_m,
            }
        }

    try:
        resp = requests.post(PLACES_API_URL, json=body, headers=headers, timeout=15)
        if resp.status_code != 200:
            log.error("Places API HTTP %d za '%s': %s", resp.status_code, query, resp.text[:500])
            return []
        resp.raise_for_status()
    except requests.RequestException as e:
        log.error("Places API greška za query '%s': %s", query, e)
        return []

    data = resp.json()
    if "error" in data:
        log.error("Places API error za '%s': %s", query, data["error"])
        return []
    places = data.get("places", [])
    if not places:
        log.info("Places API prazan odgovor za '%s': %s", query, str(data)[:300])
    elif query == NISE_QUERIES[0]:
        # Log prvog rezultata prve query za debug
        log.info("Places sample[0] za '%s': %s", query, str(places[0])[:400])
    results = []
    skipped_no_contact = 0
    for p in places:
        name = p.get("displayName", {}).get("text", "")
        phone = p.get("nationalPhoneNumber", "") or p.get("internationalPhoneNumber", "")
        web = p.get("websiteUri", "")
        address = p.get("formattedAddress", "")
        components = p.get("addressComponents", [])
        grad, zupanija = _parse_address_components(components)

        if not phone and not web:
            skipped_no_contact += 1
            continue

        results.append({
            "naziv_firme": name,
            "telefon": phone,
            "web": web,
            "grad": grad,
            "zupanija": zupanija,
            "izvor": "Google Places",
        })
    if skipped_no_contact:
        log.info("Places '%s': preskočeno %d (bez kontakta/weba)", query, skipped_no_contact)
    log.info("Places: '%s' → %d rezultata", query, len(results))
    return results


def iter_all_searches(
    delay_between_queries: float = 2.0,
    queries: list[str] | None = None,
    excluded_counties: set[str] | None = None,
) -> Iterator[dict]:
    """
    Prolazi kroz nišne upite, vraća lead dict-ove jedan po jedan.

    queries: lista upita; None = koristi default NISE_QUERIES
    excluded_counties: set imena županija za preskočiti (partial match, case-insensitive)
                      Napomena: filter radi samo za Places (vraća zupanija);
                      CW collect faza nema county podatak.
    """
    q_list = queries if queries is not None else NISE_QUERIES
    seen_place_ids: set[str] = set()
    for query in q_list:
        results = search_places(query)
        for r in results:
            if excluded_counties:
                zupanija = r.get("zupanija", "")
                if any(exc.lower() in zupanija.lower() for exc in excluded_counties):
                    log.debug("Places skip (isključena županija '%s'): %s", zupanija, r.get("naziv_firme"))
                    continue
            pid = r.get("_place_id", "")
            if pid and pid in seen_place_ids:
                continue
            if pid:
                seen_place_ids.add(pid)
            yield r
        time.sleep(delay_between_queries)
