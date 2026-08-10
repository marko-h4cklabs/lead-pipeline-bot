"""
Claude API — generira 1-2 rečenice opis čime se firma bavi.
Model: claude-haiku-4-5-20251001 (najjeftiniji, dovoljan za sažetak).
API key: env ANTHROPIC_API_KEY
"""
import logging
import os

log = logging.getLogger(__name__)

PROMPT_TEMPLATE = """Napiši 1-2 rečenice (max 60 riječi) koje opisuju čime se ova firma bavi.
Piši u trećem licu, bez marketinških fraza. Samo fakti iz teksta.
Ako tekst ne sadrži dovoljno info, napiši: "Firma pruža [vrsta usluge] u [grad ako poznat]."

Naziv firme: {naziv}
Tekst s web stranice:
{web_text}

Odgovor (samo opis, bez dodatnih komentara):"""

FALLBACK_PROMPT = """Napiši jednu kratku rečenicu (max 15 riječi) koja opisuje čime se ova firma bavi, na temelju naziva i grada.
Piši u trećem licu. Forma: "[Naziv] pruža [usluge] u [gradu]." ili slično.

Naziv firme: {naziv}
Grad: {grad}

Odgovor (samo jedna rečenica, bez komentara):"""


def generate_opis_fallback(naziv: str, grad: str = "") -> str:
    """
    Generira jednu rečenicu opisa bez web teksta — samo iz naziva i grada.
    Koristi se kao fallback za CW leadove koji nemaju vlastitu web stranicu.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return ""
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=60,
            messages=[{
                "role": "user",
                "content": FALLBACK_PROMPT.format(naziv=naziv, grad=grad or "?")
            }]
        )
        text = message.content[0].text.strip()
        # Uzmi samo prvu rečenicu
        first = text.split(".")[0].strip()
        return (first + ".") if first else ""
    except Exception as e:
        log.warning("Claude fallback opis greška za '%s': %s", naziv, e)
        return ""


def generate_opis(naziv: str, web_text: str) -> str:
    """
    Generira kratki opis firme preko Claude API-ja.
    Vraća prazan string ako ANTHROPIC_API_KEY nije postavljen.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        log.warning("ANTHROPIC_API_KEY nije postavljen — preskačem opis generiranje")
        return ""

    if not web_text.strip():
        return ""

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            messages=[{
                "role": "user",
                "content": PROMPT_TEMPLATE.format(naziv=naziv, web_text=web_text[:2000])
            }]
        )
        opis = message.content[0].text.strip()
        # Sanitizacija: samo prva 1-2 rečenice
        sentences = [s.strip() for s in opis.split(".") if s.strip()]
        return ". ".join(sentences[:2]) + ("." if sentences else "")
    except Exception as e:
        log.warning("Claude API greška za '%s': %s", naziv, e)
        return ""
