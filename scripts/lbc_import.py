#!/usr/bin/env python3
"""
Import de véhicules LeBonCoin → CRM Flease
- ScraperAPI avec render=true comme méthode principale (LBC est Next.js)
- Fallback requête directe si pas de clé ScraperAPI
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


def fetch_page(url: str) -> str:
    """Tente plusieurs méthodes pour obtenir la page LBC."""

    if SCRAPERAPI_KEY:
        # ① ScraperAPI sans render — rapide (~5-8s), fonctionne pour LBC
        print("  [fetch] ScraperAPI (fast)...", flush=True)
        try:
            html = scraper_api_fetch(url, render=False)
            if "__NEXT_DATA__" in html:
                print(f"  [fetch] ✅ __NEXT_DATA__ via ScraperAPI ({len(html):,} car)", flush=True)
                return html
            print(f"  [fetch] ⚠ Sans render: __NEXT_DATA__ absent ({len(html):,} car)", flush=True)
            print(f"  [fetch]   Extrait: {html[:200]!r}", flush=True)
        except Exception as e:
            print(f"  [fetch] ⚠ ScraperAPI fast échoué: {e}", flush=True)

        # ② ScraperAPI avec render JS — plus lent (~25-40s), si la page nécessite JS
        print("  [fetch] ScraperAPI render=true (JS)...", flush=True)
        try:
            html = scraper_api_fetch(url, render=True)
            if "__NEXT_DATA__" in html:
                print(f"  [fetch] ✅ __NEXT_DATA__ via ScraperAPI render ({len(html):,} car)", flush=True)
                return html
            print(f"  [fetch] ⚠ Render: __NEXT_DATA__ absent ({len(html):,} car)", flush=True)
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
    if "__NEXT_DATA__" in html:
        print(f"  [fetch] ✅ __NEXT_DATA__ direct ({len(html):,} car)", flush=True)
    else:
        print(f"  [fetch] ⚠ Direct: __NEXT_DATA__ absent ({len(html):,} car)", flush=True)
    return html


# ── Parse ───────────────────────────────────────────────────────────────────

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

    # Cherche l'objet 'ad' dans différentes structures Next.js
    ad = (
        pp.get("ad") or
        pp.get("adView", {}).get("ad") or
        pp.get("initialState", {}).get("adView", {}).get("adData", {}).get("ad")
    )
    if not ad:
        print(f"  [parse] Clés pageProps: {list(pp.keys())}", flush=True)
        raise ValueError("Objet 'ad' introuvable dans __NEXT_DATA__")

    # Attributs
    attrs = {a.get("key", ""): (a.get("value_label") or a.get("value", ""))
             for a in ad.get("attributes", [])}
    print(f"  [parse] Attributs: {json.dumps(attrs, ensure_ascii=False)}", flush=True)

    # Prix
    prices = ad.get("price", [])
    prix = int(prices[0]) if prices else None

    # Photo
    imgs = ad.get("images", {})
    photo = next(
        (imgs[k][0] for k in ("urls_large", "urls", "urls_thumb") if imgs.get(k)),
        None
    )

    # Localisation
    loc = ad.get("location", {})
    city = loc.get("city", "") or loc.get("region_label", "")
    localisation = city + (f" ({loc['zipcode'][:2]})" if loc.get("zipcode") else "")

    # Fournisseur
    owner = ad.get("owner", {})
    phones = owner.get("phone_numbers") or []
    tel = phones[0] if phones else owner.get("phone", "")

    # Année
    reg = attrs.get("regdate", "")
    annee = int(reg[:4]) if reg and len(reg) >= 4 else None
    date_mec = attrs.get("issuance_date") or (str(annee) if annee else "")

    # Km
    km = int(re.sub(r"\D", "", attrs.get("mileage", "0") or "0") or 0)

    # Boîte
    boite_raw = (attrs.get("gearbox_type") or attrs.get("gearbox") or
                 attrs.get("transmission") or "").lower()
    boite = {"automatic": "Automatique", "automatique": "Automatique",
             "manual": "Manuelle", "manuelle": "Manuelle",
             "semi_auto": "Semi-auto"}.get(boite_raw, boite_raw or "")

    # Carburant
    fuel_raw = attrs.get("fuel", "").lower()
    carburant = {"essence": "Essence", "gasoline": "Essence",
                 "diesel": "Diesel", "electric": "Électrique",
                 "hybrid": "Hybride", "lpg": "GPL"}.get(fuel_raw, attrs.get("fuel", ""))

    return {
        "id":               str(uuid.uuid4()),
        "lbc_id":           str(ad.get("list_id", "")),
        "url_annonce":      url,
        "source":           "LeBonCoin",
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
        "column":           "sourcing",
        "sub_stage":        "a_contacter",
        "priority":         3,
        "documents":        {"carte_grise": False, "facture_achat": False, "certificat_cession": False},
        "created_at":       datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        "historique":       [{"date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                              "action": "Importé depuis LBC"}],
    }


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
    print(f"🔑 ScraperAPI: {'configurée ✅' if SCRAPERAPI_KEY else 'absente ⚠'}")

    gh_file = gh_get(CRM_FILE)
    crm = json.loads(base64.b64decode(gh_file["content"]).decode("utf-8"))
    sha = gh_file["sha"]
    existing_ids = {v.get("lbc_id") for v in crm.get("vehicles", []) if v.get("lbc_id")}

    added, errors = [], []

    for i, url in enumerate(urls):
        print(f"\n━━━ [{i+1}/{len(urls)}] {url}", flush=True)
        try:
            html = fetch_page(url)
            v = parse_lbc(html, url)
            if v["lbc_id"] and v["lbc_id"] in existing_ids:
                print(f"  ⚠ Déjà présent (ID {v['lbc_id']}) — ignoré")
                continue
            crm.setdefault("vehicles", []).append(v)
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
           f"Import LBC: {len(added)} véhicule(s)")
    print(f"\n✅ {len(added)} véhicule(s) ajouté(s):")
    for label in added:
        print(f"  + {label}")
    if errors:
        print(f"\n⚠ {len(errors)} erreur(s):")
        for e in errors:
            print(f"  • {e}")


if __name__ == "__main__":
    main()
