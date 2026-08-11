"""
Dio 2 — Verifikacija
Pokreni: python -m scripts.verify.main

Ulaz: redovi sa status='new'
Izlaz: status → 'verified' / 'manual_review' / 'rejected'

Kriteriji odbacivanja:
  - Web-scan: detektiran webshop
  - CW blokada
  - CW prihodi eksplicitno 0 EUR
Batch update na kraju runa (1 read_all + 1 batchUpdate, ne 1 po redu).
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from scripts.lib.rate_limit import RateLimiter, rate_limited_session, BatchPausedException
from scripts.lib.sheets import SheetsClient
from scripts.verify.companywall_detail import scrape_detail, name_similarity, NAME_SIMILARITY_THRESHOLD
from scripts.verify.provjera_hr import check_company
from scripts.verify.web_scan import scan_website, extract_homepage_text
from scripts.verify.claude_summary import generate_opis, generate_opis_fallback

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("verify")

MAX_PER_RUN = int(os.environ.get("MAX_VERIFY_PER_RUN", "80"))
MIN_REVENUE_EUR = int(os.environ.get("MIN_REVENUE_EUR", "0"))
_max_rev_raw = os.environ.get("MAX_REVENUE_EUR", "").strip()
MAX_REVENUE_EUR: int | None = int(_max_rev_raw) if _max_rev_raw else None


def run():
    log.info("=== Verify start ===")
    client = SheetsClient()

    all_rows = client.read_all()
    log.info("Ukupno redova u Sheetu: %d", len(all_rows))
    if all_rows:
        statuses = {}
        for r in all_rows:
            s = r.get("status", "(prazno)")
            statuses[s] = statuses.get(s, 0) + 1
        log.info("Distribucija statusa: %s", statuses)
        sample = all_rows[0]
        log.info("Primjer [row %d]: lead_id=%r naziv=%r status=%r web=%r",
                 sample.get("_row"), sample.get("lead_id"), sample.get("naziv_firme"),
                 sample.get("status"), sample.get("web", "")[:60])

    new_rows = [r for r in all_rows if r.get("status") == "new"]
    log.info("Pronađeno %d 'new' redova za verifikaciju", len(new_rows))

    if not new_rows:
        log.info("Nema novih redova — završeno.")
        return

    # CW limiter mjeri HTTP zahtjeve prema CW-u, ne broj firmi.
    # Svaka firma radi 1-2 CW zahtjeva; daj 4× buffer da nikad ne bude usko grlo.
    limiter = RateLimiter(max_per_run=MAX_PER_RUN * 4)
    all_updates: list[dict] = []
    processed = 0
    # Faza 2c: in-batch OIB praćenje — oib → (lead_id, naziv_firme)
    batch_oib_assignments: dict[str, tuple[str, str]] = {}

    with rate_limited_session(max_per_run=MAX_PER_RUN * 3) as (session, _):
        for row in new_rows:
            if processed >= MAX_PER_RUN:
                log.info("Dosegnut limit od %d po runu — stajemo.", MAX_PER_RUN)
                break

            lead_id = row.get("lead_id", "")
            naziv = row.get("naziv_firme", "")
            log.info("[%s] Verificiram: %s", lead_id, naziv)

            updates = {"lead_id": lead_id}

            # --- Korak 1: Web-scan (samo ako ima pravi web URL, ne CW) ---
            web_url = row.get("web", "")
            if web_url and "companywall.hr" in web_url:
                web_url = ""

            if web_url:
                try:
                    scan_result = scan_website(session, web_url)
                    if scan_result["is_shop"]:
                        log.info("[%s] REJECTED (web-scan): %s", lead_id, scan_result["reason"])
                        updates["status"] = "rejected"
                        updates["opis"] = f"Odbačeno: {scan_result['reason']}"
                        all_updates.append(updates)
                        processed += 1
                        continue
                except Exception as e:
                    log.warning("[%s] web-scan greška: %s", lead_id, e)

            # --- Korak 2: CompanyWall scrape ---
            try:
                cw_data = scrape_detail(row, limiter)
            except BatchPausedException as e:
                log.error("Batch pauza: %s — zaustavljam run", e)
                break

            processed += 1

            if not cw_data.get("_cw_found"):
                # --- Faza 2b: name mismatch — CW search vratio krivu firmu ---
                if cw_data.get("_name_mismatch"):
                    found_name = cw_data.get("_cw_found_name", "?")
                    sim = cw_data.get("_similarity", 0.0)
                    log.warning(
                        "[%s] manual_review: CW naziv ne odgovara '%s' ↔ '%s' (sličnost=%.0f%%)",
                        lead_id, naziv, found_name, sim * 100,
                    )
                    updates["status"] = "manual_review"
                    updates["opis"] = (
                        f"CW lookup nesiguran: pronađeno '{found_name}' ≠ traženo '{naziv}' "
                        f"({sim:.0%} sličnost) — ručna provjera"
                    )
                    all_updates.append(updates)
                    continue

                # --- Fallback: provjera.hr ---
                log.info("[%s] CW nije našao '%s' — pokušavam provjera.hr", lead_id, naziv)
                try:
                    provjera = check_company(session, naziv)
                    if provjera.get("found"):
                        if provjera.get("oib"):
                            updates["oib"] = provjera["oib"]
                        if not provjera.get("active", True):
                            updates["status"] = "rejected"
                            updates["opis"] = "Neaktivna firma (provjera.hr)"
                        else:
                            updates["status"] = "manual_review"
                    else:
                        updates["status"] = "manual_review"
                except Exception as e:
                    log.warning("[%s] provjera.hr greška: %s", lead_id, e)
                    updates["status"] = "manual_review"

                all_updates.append(updates)
                continue

            # --- Firma nađena na CW ---

            # Provjera 0 (Faza 2c): in-batch OIB kolizija — mora biti prva provjera
            oib_candidate = cw_data.get("oib", "").strip().lstrip("'")
            if oib_candidate and len(oib_candidate) >= 8:
                existing_assignment = batch_oib_assignments.get(oib_candidate)
                if existing_assignment:
                    existing_lid, existing_naziv = existing_assignment
                    log.error(
                        "OIB KOLIZIJA UNUTAR BATCHA: OIB %s dodijeljen '%s' (%s) I '%s' (%s) "
                        "— oba → manual_review",
                        oib_candidate, existing_naziv, existing_lid, naziv, lead_id,
                    )
                    # Retroaktivno popravi prethodni zapis — makni OIB/kontakte jer im ne vjerujemo
                    for prev in all_updates:
                        if prev.get("lead_id") == existing_lid:
                            prev["status"] = "manual_review"
                            prev["opis"] = (
                                f"OIB kolizija unutar batcha: OIB {oib_candidate} "
                                f"konflikt s {lead_id} ({naziv}) — ručna provjera"
                            )
                            for field in ("oib", "vlasnik", "telefon"):
                                prev.pop(field, None)
                            break
                    updates["status"] = "manual_review"
                    updates["opis"] = (
                        f"OIB kolizija unutar batcha: OIB {oib_candidate} "
                        f"konflikt s {existing_lid} ({existing_naziv}) — ručna provjera"
                    )
                    all_updates.append(updates)
                    continue
                batch_oib_assignments[oib_candidate] = (lead_id, naziv)

            # Provjera 1: blokada
            if cw_data.get("cw_blocked"):
                log.info("[%s] REJECTED (u blokadi): %s", lead_id, naziv)
                updates.update({
                    "oib": cw_data.get("oib", ""),
                    "status": "rejected",
                    "opis": "Odbačeno: firma u blokadi",
                })
                all_updates.append(updates)
                continue

            # Provjera 2: prihodi — raspon [MIN_REVENUE_EUR, MAX_REVENUE_EUR]
            prihod = cw_data.get("prihod")
            if prihod is not None:
                if prihod < MIN_REVENUE_EUR:
                    log.info("[%s] REJECTED (prihodi %d < min %d EUR): %s", lead_id, prihod, MIN_REVENUE_EUR, naziv)
                    updates.update({
                        "oib": cw_data.get("oib", ""),
                        "status": "rejected",
                        "opis": f"Odbačeno: prihodi {prihod} EUR < min {MIN_REVENUE_EUR} EUR",
                    })
                    all_updates.append(updates)
                    continue
                if MAX_REVENUE_EUR is not None and prihod > MAX_REVENUE_EUR:
                    log.info("[%s] REJECTED (prihodi %d > max %d EUR): %s", lead_id, prihod, MAX_REVENUE_EUR, naziv)
                    updates.update({
                        "oib": cw_data.get("oib", ""),
                        "status": "rejected",
                        "opis": f"Odbačeno: prihodi {prihod} EUR > max {MAX_REVENUE_EUR} EUR",
                    })
                    all_updates.append(updates)
                    continue

            # Upiši CW podatke
            updates.update({
                "oib": cw_data.get("oib", ""),
                "vlasnik": cw_data.get("vlasnik", ""),
                "telefon": cw_data.get("telefon", ""),
                "broj_zaposlenih": cw_data.get("broj_zaposlenih", ""),
                "godina_osnutka": cw_data.get("godina_osnutka", ""),
            })

            # Status: verified ako ima kontakt, inače manual_review
            if cw_data.get("_has_mobile") or cw_data.get("telefon"):
                updates["status"] = "verified"
            else:
                log.info("[%s] manual_review (nema mobitela): %s", lead_id, naziv)
                updates["status"] = "manual_review"

            # --- Korak 3: Opis (Claude API) — web-scan ako postoji URL ---
            if updates.get("status") == "verified" and web_url:
                try:
                    web_text = extract_homepage_text(session, web_url)
                    if web_text:
                        opis = generate_opis(naziv, web_text)
                        if opis:
                            updates["opis"] = opis
                except Exception as e:
                    log.warning("[%s] opis greška: %s", lead_id, e)

            # --- Korak 3b: Fallback opis za firme bez web URL-a (naziv + grad) ---
            if not updates.get("opis") and updates.get("status") == "verified":
                try:
                    fallback = generate_opis_fallback(naziv, grad=row.get("grad", ""))
                    if fallback:
                        updates["opis"] = fallback
                        log.debug("[%s] fallback opis: %s", lead_id, fallback)
                except Exception as e:
                    log.warning("[%s] fallback opis greška: %s", lead_id, e)

            log.info("[%s] → status: %s", lead_id, updates.get("status"))
            all_updates.append(updates)

    # --- Batch write svih promjena ---
    if all_updates:
        log.info("Batch update: %d redova...", len(all_updates))
        _last_err = None
        for _attempt in range(3):
            try:
                if _attempt > 0:
                    # Rebuild klijenta — svježi HTTP socket i token refresh.
                    # SSL/keepalive timeout može pasti nakon dugog runa.
                    log.info("Batch update retry %d/2 — rebuild Sheets klijenta...", _attempt)
                    client = SheetsClient()
                client.batch_update(all_updates)
                _last_err = None
                break
            except Exception as e:
                _last_err = e
                log.warning("Batch update pokušaj %d/3 greška: %s", _attempt + 1, e)
        if _last_err:
            log.error("Batch update neuspješan nakon 3 pokušaja — podaci NISU upisani: %s", _last_err)
            return  # ne nastavljaj s dedupom, Sheet nije ažuriran

    # --- Faza 3: Post-write safety net ---
    # Provjera upravo zapisanih redova — zadnja linija obrane ako Faza 2 propusti nešto.
    if all_updates:
        _oib_safety_check(client, {upd["lead_id"] for upd in all_updates})

    # --- Sekundarni OIB dedupe ---
    # Collect nema OIB pa isti OIB može doći iz Places i CW odvojeno.
    # Ovaj pass se pokreće NAKON verify (koji upisuje OIB) da uhvati takve duplikate.
    _dedup_by_oib(client)

    log.info("=== Verify završen: %d obrađenih redova ===", processed)


def _oib_safety_check(client, processed_lead_ids: set[str]) -> None:
    """
    Faza 3: post-write safety net.
    Čita upravo zapisane redove i grupira po OIB-u.
    Ako isti OIB ima različite nazive firmi → logička nemogućnost (OIB je jedinstven) →
    hard error + prebacivanje u manual_review čak i ako su prošli kao verified/qualified.
    """
    from collections import defaultdict
    all_rows = client.read_all()
    processed_rows = [r for r in all_rows if r.get("lead_id") in processed_lead_ids]

    oib_groups: dict[str, list] = defaultdict(list)
    for row in processed_rows:
        oib = row.get("oib", "").strip().lstrip("'")
        if oib and len(oib) >= 8:
            oib_groups[oib].append(row)

    corrections = []
    for oib, group in oib_groups.items():
        if len(group) < 2:
            continue
        names = [r.get("naziv_firme", "") for r in group]
        worst_sim = min(
            name_similarity(names[i], names[j])
            for i in range(len(names))
            for j in range(i + 1, len(names))
        )
        if worst_sim >= NAME_SIMILARITY_THRESHOLD:
            continue  # Legitimni duplikati iste firme — _dedup_by_oib ih rješava

        lead_ids_str = ", ".join(r["lead_id"] for r in group)
        names_str = ", ".join(f"'{n}'" for n in names)
        log.error(
            "FAZA 3 SAFETY NET: OIB %s → %d različitih firmi u batchu (%s) [sličnost=%.0f%%] "
            "— prebacujem sve u manual_review",
            oib, len(group), names_str, worst_sim * 100,
        )
        for row in group:
            if row.get("status") not in ("manual_review", "rejected"):
                corrections.append({
                    "lead_id": row["lead_id"],
                    "status": "manual_review",
                    "opis": (
                        f"Faza 3 safety net: OIB {oib} dodijeljen {len(group)} različitih firmi "
                        f"({names_str}) — ručna provjera"
                    ),
                })

    if corrections:
        log.warning("Faza 3: ispravljam %d redova s OIB kolizijom u Sheetu", len(corrections))
        try:
            client.batch_update(corrections)
        except Exception as e:
            log.error("Faza 3 safety net batch_update greška: %s", e)
    else:
        log.info("Faza 3 safety net: nema OIB kolizija u upravo obrađenim redovima ✓")


def _completeness(row: dict) -> int:
    """Broj popunjenih ključnih polja — koristi se kao tie-breaker u OIB dedupeu."""
    return sum(
        1 for f in ("vlasnik", "telefon", "opis", "broj_zaposlenih")
        if str(row.get(f, "")).strip()
    )


def _dedup_by_oib(client) -> None:
    """
    Sekundarni dedupe po OIB-u — pokreće se nakon verify jer collect nema OIB.
    Za svaki OIB s ≥2 reda: zadržava zapis s najviše popunjenih ključnih polja
    (vlasnik, telefon, opis, broj_zaposlenih). Tie-breaker: manji _row (stariji).
    Preskače redove koji su već 'rejected'.
    """
    from collections import defaultdict
    all_rows = client.read_all()
    oib_groups: dict[str, list] = defaultdict(list)
    for row in all_rows:
        oib = row.get("oib", "").strip().lstrip("'")  # makni apostrofni prefix ako postoji
        if oib and len(oib) >= 8:  # OIB je 11 znakova, ignoriramo kratke/prazne
            oib_groups[oib].append(row)

    dedup_updates = []
    for oib, group in oib_groups.items():
        if len(group) < 2:
            continue
        # Sortiraj: kompletnost silazno, _row uzlazno (tie-breaker = stariji)
        group.sort(key=lambda r: (-_completeness(r), r["_row"]))
        keep = group[0]
        for dup in group[1:]:
            if dup.get("status") == "rejected":
                continue
            # Provjeri da je ovo pravi duplikat (ista firma, različiti redovi),
            # a ne kolizija (različite firme s istim OIB-om = greška podataka).
            # Kolizije bi trebale biti uhvaćene u Fazi 2c/3, ali osigurajmo se.
            sim = name_similarity(keep.get("naziv_firme", ""), dup.get("naziv_firme", ""))
            if sim < NAME_SIMILARITY_THRESHOLD:
                log.warning(
                    "OIB dedupe: OIB %s — '%s' i '%s' imaju sličnost=%.0f%% < %.0f%% "
                    "→ nije duplikat nego kolizija podataka, preskačem dedupe",
                    oib, keep.get("naziv_firme", ""), dup.get("naziv_firme", ""),
                    sim * 100, NAME_SIMILARITY_THRESHOLD * 100,
                )
                continue
            log.info(
                "OIB duplikat %s: zadržavam row %d (%s, score=%d), odbacujem row %d (%s, score=%d)",
                oib,
                keep["_row"], keep.get("lead_id"), _completeness(keep),
                dup["_row"],  dup.get("lead_id"),  _completeness(dup),
            )
            dedup_updates.append({
                "lead_id": dup["lead_id"],
                "status": "rejected",
                "opis": f"Duplikat OIB {oib} (zadržan: {keep.get('lead_id', '?')})",
            })

    if dedup_updates:
        log.info("OIB dedupe: odbacujem %d duplikata", len(dedup_updates))
        try:
            client.batch_update(dedup_updates)
        except Exception as e:
            log.error("OIB dedupe batch_update greška: %s", e)
    else:
        log.info("OIB dedupe: nema duplikata")


if __name__ == "__main__":
    run()
