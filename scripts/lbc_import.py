#!/usr/bin/env python3
"""
Import de véhicules LeBonCoin + LaCentrale → CRM Flease
- ScraperAPI avec render=true comme méthode principale
- Fallback requête directe si pas de clé ScraperAPI
- Détection automatique du site (LBC vs LaCentrale) via l'URL
"""

import base64
import gzip
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone

GITHUB_TOKEN   = os.environ["GITHUB_TOKEN"]
GITHUB_REPO    = os.environ.get("GITHUB_REPO", "AlexGuichard/crm-kanban")
CRM_FILE       = "data/crm.json"
SCRAPERAPI_KEY = os.environ.get("SCRAPERAPI_KEY", "")

HEADERS_BROWSER = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
}


# ── Helpers ────────────────────────────────────────────────────────────────

def detect_source(url: str) -> str:
    """Détecte le site source à partir de l'URL."""
    if "lacentrale.fr" in url:
        return "lacentrale"
    return "leboncoin"


def make_vehicle_base(url: str, source: str) -> dict:
    """Crée un objet véhicule de base avec les champs par défaut."""
    return {
        "id":               str(uuid.uuid4()),
        "url_annonce":      url,
        "source":           source,
        "column":           "sourcing",
        "sub_stage":        "a_contacter",
        "priority":         3,
        "documents":        {"carte_grise": False, "facture_achat": False, "certificat_cession": False},
        "created_at":       datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        "historique":       [{"date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                              "action": f"Importé depuis {source}"}],
    }


# ── Fetch ───────────────────────────────────────────────────────────────────

def scraper_api_fetch(url: str, render: bool) -> str:
    """Appelle ScraperAPI avec ou sans rendu JS."""
    api_url = (
        f"http://api.scraperapi.com"
        f"?api_key={SCRAPERAPI_KEY}"
        f"&url={urllib.parse.quote(url, safe='')}"
        f"&country_code=fr"
        + ("&render=true" if render else "")
    )
    req = urllib.request.Request(api_url, headers={"User-Agent": "python-scraperapi"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.read().decode("utf-8", errors="replace")


def fetch_page(url: str, need_next_data: bool = False) -> str:
    """Tente plusieurs méthodes pour obtenir la page."""

    if SCRAPERAPI_KEY:
        # ① ScraperAPI sans render — rapide (~5-8s)
        print("  [fetch] ScraperAPI (fast)...", flush=True)
        try:
            html = scraper_api_fetch(url, render=False)
            if not need_next_data or "__NEXT_DATA__" in html:
                print(f"  [fetch] ✅ ScraperAPI fast ({len(html):,} car)", flush=True)
                return html
            print(f"  [fetch] ⚠ Sans render: données manquantes ({len(html):,} car)", flush=True)
        except Exception as e:
            print(f"  [fetch] ⚠ ScraperAPI fast échoué: {e}", flush=True)

        # ② ScraperAPI avec render JS — plus lent (~25-40s)
        print("  [fetch] ScraperAPI render=true (JS)...", flush=True)
        try:
            html = scraper_api_fetch(url, render=True)
            if not need_next_data or "__NEXT_DATA__" in html:
                print(f"  [fetch] ✅ ScraperAPI render ({len(html):,} car)", flush=True)
                return html
            print(f"  [fetch] ⚠ Render: données manquantes ({len(html):,} car)", flush=True)
        except Exception as e:
            print(f"  [fetch] ⚠ ScraperAPI render échoué: {e}", flush=True)

    # ③ Requête directe
    print("  [fetch] Requête directe...", flush=True)
    req = urllib.request.Request(url, headers=HEADERS_BROWSER)
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read()
        if r.info().get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        html = raw.decode("utf-8", errors="replace")
    print(f"  [fetch] Direct ({len(html):,} car)", flush=True)
    return html


# ── Parse LeBonCoin ────────────────────────────────────────────────────────

def parse_lbc(html: str, url: str) -> dict:
    m = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        html, re.DOTALL
    )
    if not m:
        if "captcha" in html.lower() or "robot" in html.lower():
            raise ValueError("CAPTCHA détecté — ScraperAPI render=true nécessaire")
        raise ValueError(
            f"__NEXT_DATA__ introuvable (page {len(html):,} car). "
            f"Extrait: {html[:300]!r}"
        )

    data = json.loads(m.group(1))
    pp = data.get("props", {}).get("pageProps", {})

    ad = (
        pp.get("ad") or
        pp.get("adView", {}).get("ad") or
        pp.get("initialState", {}).get("adView", {}).get("adData", {}).get("ad")
    )
    if not ad:
        print(f"  [parse] Clés pageProps: {list(pp.keys())}", flush=True)
        raise ValueError("Objet 'ad' introuvable dans __NEXT_DATA__")

    attrs = {a.get("key", ""): (a.get("value_label") or a.get("value", ""))
             for a in ad.get("attributes", [])}
    print(f"  [parse] Attributs LBC: {json.dumps(attrs, ensure_ascii=False)}", flush=True)

    prices = ad.get("price", [])
    prix = int(prices[0]) if prices else None

    imgs = ad.get("images", {})
    photo = next(
        (imgs[k][0] for k in ("urls_large", "urls", "urls_thumb") if imgs.get(k)),
        None
    )

    loc = ad.get("location", {})
    city = loc.get("city", "") or loc.get("region_label", "")
    localisation = city + (f" ({loc['zipcode'][:2]})" if loc.get("zipcode") else "")

    owner = ad.get("owner", {})
    phones = owner.get("phone_numbers") or []
    tel = phones[0] if phones else owner.get("phone", "")

    reg = attrs.get("regdate", "")
    annee = int(reg[:4]) if reg and len(reg) >= 4 else None
    date_mec = attrs.get("issuance_date") or (str(annee) if annee else "")

    km = int(re.sub(r"\D", "", attrs.get("mileage", "0") or "0") or 0)

    boite_raw = (attrs.get("gearbox_type") or attrs.get("gearbox") or
                 attrs.get("transmission") or "").lower()
    boite = {"automatic": "Automatique", "automatique": "Automatique",
             "manual": "Manuelle", "manuelle": "Manuelle",
             "semi_auto": "Semi-auto"}.get(boite_raw, boite_raw or "")

    fuel_raw = attrs.get("fuel", "").lower()
    carburant = {"essence": "Essence", "gasoline": "Essence",
                 "diesel": "Diesel", "electric": "Électrique",
                 "hybrid": "Hybride", "lpg": "GPL"}.get(fuel_raw, attrs.get("fuel", ""))

    v = make_vehicle_base(url, "LeBonCoin")
    v.update({
        "lbc_id":           str(ad.get("list_id", "")),
        "marque":           attrs.get("brand", ""),
        "modele":           attrs.get("model", ad.get("subject", "")),
        "annee":            annee,
        "date_mec":         date_mec,
        "km":               km,
        "prix_demande":     prix,
        "carburant":        carburant,
        "boite":            boite,
        "couleur":          attrs.get("vehicule_color") or attrs.get("color", ""),
        "localisation":     localisation,
        "fournisseur_nom":  owner.get("name", ""),
        "fournisseur_tel":  tel,
        "fournisseur_type": "pro" if owner.get("type") == "pro" else "particulier",
        "photo":            photo,
        "titre":            ad.get("subject", ""),
        "description":      (ad.get("body") or "")[:300],
    })
    return v


# ── Parse LaCentrale ───────────────────────────────────────────────────────

def extract_meta(html: str, pattern: str, group: int = 1) -> str:
    """Extrait une valeur depuis une regex sur le HTML."""
    m = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
    return m.group(group).strip() if m else ""


def extract_ld_json(html: str) -> dict:
    """Extrait le JSON-LD (schema.org) de la page LaCentrale."""
    for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL):
        try:
            data = json.loads(m.group(1))
            if isinstance(data, list):
                for item in data:
                    if item.get("@type") in ("Car", "Vehicle", "Product"):
                        return item
            elif data.get("@type") in ("Car", "Vehicle", "Product"):
                return data
        except (json.JSONDecodeError, AttributeError):
            continue
    return {}


def parse_lacentrale(html: str, url: str) -> dict:
    """Parse une annonce LaCentrale depuis le HTML."""

    if "captcha" in html.lower() or "robot" in html.lower():
        raise ValueError("CAPTCHA détecté sur LaCentrale")

    if len(html) < 2000:
        raise ValueError(f"Page trop courte ({len(html)} car) — probablement bloquée")

    # ── Essai 1 : JSON-LD (schema.org) — le plus fiable ──
    ld = extract_ld_json(html)

    # ── Essai 2 : window.__INITIAL_STATE__ ou window.__DATA__ ──
    state_data = {}
    for pattern in [
        r'window\.__INITIAL_STATE__\s*=\s*({.*?});?\s*</script>',
        r'window\.__DATA__\s*=\s*({.*?});?\s*</script>',
        r'"vehicle"\s*:\s*({[^}]{50,}})',
    ]:
        m = re.search(pattern, html, re.DOTALL)
        if m:
            try:
                state_data = json.loads(m.group(1))
                print(f"  [parse] Trouvé state data via pattern", flush=True)
                break
            except json.JSONDecodeError:
                continue

    # ── Extraction depuis JSON-LD ──
    marque = ""
    modele = ""
    prix = None
    annee = None
    km = 0
    carburant = ""
    boite = ""
    couleur = ""
    photo = None

    if ld:
        print(f"  [parse] JSON-LD trouvé: {ld.get('@type', '?')}", flush=True)
        marque = ld.get("brand", {}).get("name", "") if isinstance(ld.get("brand"), dict) else str(ld.get("brand", ""))
        modele = ld.get("model", "") or ld.get("name", "")
        offers = ld.get("offers", {})
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        prix_str = str(offers.get("price", ""))
        prix = int(re.sub(r"\D", "", prix_str)) if prix_str else None
        photo = ld.get("image", None)
        if isinstance(photo, list):
            photo = photo[0] if photo else None

    # ── Extraction depuis le HTML (fallback et complément) ──

    # Titre de l'annonce
    titre = extract_meta(html, r'<h1[^>]*>(.*?)</h1>')
    titre = re.sub(r'<[^>]+>', '', titre).strip()

    # Marque/modèle depuis le titre si manquant
    if not marque and titre:
        parts = titre.split()
        if len(parts) >= 2:
            marque = marque or parts[0]
            modele = modele or " ".join(parts[1:3])

    # Prix depuis le HTML
    if not prix:
        prix_match = re.search(r'class="[^"]*price[^"]*"[^>]*>\s*([\d\s]+)\s*€', html)
        if not prix_match:
            prix_match = re.search(r'([\d\s]{4,})\s*€', html)
        if prix_match:
            prix = int(re.sub(r"\D", "", prix_match.group(1)))

    # Caractéristiques techniques depuis les blocs de specs
    specs_text = ""
    for spec_pattern in [
        r'class="[^"]*(?:specs|characteristics|technical|detail)[^"]*"[^>]*>(.*?)</(?:div|section|ul)',
        r'class="[^"]*feature[^"]*"[^>]*>(.*?)</div>',
    ]:
        specs_matches = re.findall(spec_pattern, html, re.DOTALL | re.IGNORECASE)
        specs_text = " ".join(specs_matches)
        if specs_text:
            break

    all_text = specs_text + " " + html

    # Année
    if not annee:
        year_match = re.search(r'(?:année|mise en circulation|date)[^>]*?(\b20[12]\d\b)', all_text, re.IGNORECASE)
        if year_match:
            annee = int(year_match.group(1))
        elif ld.get("productionDate"):
            try:
                annee = int(str(ld["productionDate"])[:4])
            except (ValueError, TypeError):
                pass

    # Kilométrage
    if not km:
        km_match = re.search(r'([\d\s\.]+)\s*km', all_text, re.IGNORECASE)
        if km_match:
            km = int(re.sub(r"\D", "", km_match.group(1)))

    # Carburant
    if not carburant:
        fuel_map = {
            "diesel": "Diesel", "essence": "Essence", "hybride": "Hybride",
            "électrique": "Électrique", "electrique": "Électrique",
            "gpl": "GPL", "ethanol": "Essence",
        }
        for keyword, label in fuel_map.items():
            if keyword in all_text.lower():
                carburant = label
                break

    # Boîte
    if not boite:
        if re.search(r'automatique|auto\b|bva|dsg|edc', all_text, re.IGNORECASE):
            boite = "Automatique"
        elif re.search(r'manuelle|manuel\b|bvm', all_text, re.IGNORECASE):
            boite = "Manuelle"

    # Couleur
    if not couleur:
        color_match = re.search(r'(?:couleur|color)[^>]*?:\s*([^<,]+)', all_text, re.IGNORECASE)
        if color_match:
            couleur = color_match.group(1).strip()[:30]

    # Photo depuis og:image ou meta
    if not photo:
        og_match = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', html)
        if og_match:
            photo = og_match.group(1)

    # Vendeur
    vendor_name = ""
    vendor_match = re.search(r'class="[^"]*(?:vendor|seller|dealer|pro)[^"]*"[^>]*>([^<]+)', html, re.IGNORECASE)
    if vendor_match:
        vendor_name = vendor_match.group(1).strip()

    # Localisation
    loc_match = re.search(r'class="[^"]*(?:location|city|address)[^"]*"[^>]*>([^<]+)', html, re.IGNORECASE)
    localisation = loc_match.group(1).strip() if loc_match else ""
    if not localisation:
        loc_match2 = re.search(r'(\d{5})\s+([A-ZÀ-Ü][a-zà-ü]+(?:\s+[A-ZÀ-Ü][a-zà-ü]+)*)', html)
        if loc_match2:
            localisation = f"{loc_match2.group(2)} ({loc_match2.group(1)[:2]})"

    # Extraire l'ID LaCentrale depuis l'URL
    lc_id_match = re.search(r'/(\d{6,})', url)
    lc_id = lc_id_match.group(1) if lc_id_match else ""

    print(f"  [parse] LaCentrale: {marque} {modele} {annee} — {km} km — {prix} €", flush=True)

    v = make_vehicle_base(url, "LaCentrale")
    v.update({
        "lbc_id":           lc_id,  # réutilise le champ pour déduplication
        "marque":           marque,
        "modele":           modele,
        "annee":            annee,
        "date_mec":         str(annee) if annee else "",
        "km":               km,
        "prix_demande":     prix,
        "carburant":        carburant,
        "boite":            boite,
        "couleur":          couleur,
        "localisation":     localisation,
        "fournisseur_nom":  vendor_name,
        "fournisseur_tel":  "",
        "fournisseur_type": "pro" if vendor_name else "",
        "photo":            photo,
        "titre":            titre or f"{marque} {modele}",
        "description":      "",
    })
    return v


# ── Router ─────────────────────────────────────────────────────────────────

def parse_vehicle(html: str, url: str) -> dict:
    """Route vers le bon parser selon le site."""
    source = detect_source(url)
    if source == "lacentrale":
        return parse_lacentrale(html, url)
    return parse_lbc(html, url)


# ── GitHub API ──────────────────────────────────────────────────────────────

def gh_get(path: str) -> dict:
    req = urllib.request.Request(
        f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}",
        headers={"Authorization": f"Bearer {GITHUB_TOKEN}",
                 "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def gh_put(path: str, content_str: str, sha: str, message: str) -> None:
    body = json.dumps({
        "message": message,
        "content": base64.b64encode(content_str.encode("utf-8")).decode("ascii"),
        "sha": sha,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}",
        data=body, method="PUT",
        headers={"Authorization": f"Bearer {GITHUB_TOKEN}",
                 "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15):
        pass


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    urls_raw = os.environ.get("LBC_URLS", "").strip()
    if not urls_raw:
        print("❌ Variable LBC_URLS vide")
        sys.exit(1)

    urls = [u.strip() for u in re.split(r"[\n,;]+", urls_raw) if u.strip().startswith("http")]
    if not urls:
        print("❌ Aucune URL valide")
        sys.exit(1)

    print(f"📋 {len(urls)} URL(s) à traiter")
    for u in urls:
        print(f"   {detect_source(u)}: {u}")
    print(f"🔑 ScraperAPI: {'configurée ✅' if SCRAPERAPI_KEY else 'absente ⚠'}")

    gh_file = gh_get(CRM_FILE)
    crm = json.loads(base64.b64decode(gh_file["content"]).decode("utf-8"))
    sha = gh_file["sha"]
    existing_ids = {v.get("lbc_id") for v in crm.get("vehicles", []) if v.get("lbc_id")}

    added, errors = [], []

    for i, url in enumerate(urls):
        source = detect_source(url)
        need_next = source == "leboncoin"
        print(f"\n━━━ [{i+1}/{len(urls)}] [{source.upper()}] {url}", flush=True)
        try:
            html = fetch_page(url, need_next_data=need_next)
            v = parse_vehicle(html, url)
            if v.get("lbc_id") and v["lbc_id"] in existing_ids:
                print(f"  ⚠ Déjà présent (ID {v['lbc_id']}) — ignoré")
                continue
            crm.setdefault("vehicles", []).append(v)
            if v.get("lbc_id"):
                existing_ids.add(v["lbc_id"])
            label = f"{v['marque']} {v['modele']} {v.get('annee','?')} — {v['km']:,} km — {v.get('prix_demande','?')} €"
            added.append(label)
            print(f"  ✅ {label}")
        except Exception as e:
            errors.append(f"{url}: {e}")
            print(f"  ❌ {e}")
        if i < len(urls) - 1:
            time.sleep(2)

    if not added:
        print("\n⚠ Aucun véhicule ajouté")
        if errors:
            print("\n".join(f"  • {e}" for e in errors))
        sys.exit(1 if errors else 0)

    gh_put(CRM_FILE, json.dumps(crm, ensure_ascii=False, indent=2), sha,
           f"Import: {len(added)} véhicule(s)")
    print(f"\n✅ {len(added)} véhicule(s) ajouté(s):")
    for label in added:
        print(f"  + {label}")
    if errors:
        print(f"\n⚠ {len(errors)} erreur(s):")
        for e in errors:
            print(f"  • {e}")


if __name__ == "__main__":
    main()
