#!/usr/bin/env python3
"""
Import de véhicules LeBonCoin → CRM Flease
Accepte une ou plusieurs URLs LBC, scrape les données et les écrit dans crm.json sur GitHub.
"""

import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
import urllib.request
import urllib.error

GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
GITHUB_REPO  = os.environ.get("GITHUB_REPO", "AlexGuichard/crm-kanban")
CRM_FILE     = "data/crm.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9",
    "Accept-Encoding": "gzip, deflate",
}


# ── Scraping LBC ─────────────────────────────────────────────

def fetch_page(url):
    """Télécharge la page LBC et retourne le HTML."""
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            content = resp.read()
            # Décompression gzip si nécessaire
            if resp.info().get("Content-Encoding") == "gzip":
                import gzip
                content = gzip.decompress(content)
            return content.decode("utf-8", errors="replace")
    except Exception as e:
        # Fallback ScraperAPI si disponible
        scraperapi_key = os.environ.get("SCRAPERAPI_KEY")
        if scraperapi_key:
            api_url = f"http://api.scraperapi.com?api_key={scraperapi_key}&url={url}"
            req2 = urllib.request.Request(api_url, headers={"User-Agent": "python"})
            with urllib.request.urlopen(req2, timeout=30) as resp2:
                return resp2.read().decode("utf-8", errors="replace")
        raise e


def parse_lbc(html, url):
    """Extrait les données du véhicule depuis __NEXT_DATA__."""
    match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.DOTALL)
    if not match:
        raise ValueError("__NEXT_DATA__ introuvable — LBC a peut-être changé sa structure")

    data = json.loads(match.group(1))

    # Cherche l'annonce dans la hiérarchie Next.js
    ad = None
    try:
        ad = data["props"]["pageProps"]["ad"]
    except KeyError:
        pass
    if not ad:
        try:
            ad = data["props"]["pageProps"]["adView"]["ad"]
        except KeyError:
            pass
    if not ad:
        raise ValueError("Structure de l'annonce introuvable dans __NEXT_DATA__")

    # Attributs clés/valeurs (kilomètres, année, carburant, boîte, etc.)
    attrs = {}
    for a in ad.get("attributes", []):
        attrs[a.get("key", "")] = a.get("value_label") or a.get("value", "")

    # Prix
    prices = ad.get("price", [])
    prix = int(prices[0]) if prices else None

    # Photos
    images = ad.get("images", {})
    photo_url = None
    for key in ("urls_large", "urls", "urls_thumb"):
        urls = images.get(key, [])
        if urls:
            photo_url = urls[0]
            break

    # Localisation
    loc = ad.get("location", {})
    localisation = loc.get("city", "") or loc.get("region_label", "")
    if loc.get("zipcode"):
        localisation += f" ({loc['zipcode'][:2]})"

    # Vendeur
    owner = ad.get("owner", {})
    vendeur_type   = "pro" if owner.get("type") == "pro" else "particulier"
    vendeur_nom    = owner.get("name", "")
    # Téléphone : LBC le stocke parfois dans owner.phone ou owner.phone_numbers
    phones = owner.get("phone_numbers") or []
    vendeur_tel = phones[0] if phones else owner.get("phone", "")

    # Année (regdate = "2019-01" → 2019)
    annee_raw = attrs.get("regdate", "")
    annee = int(annee_raw[:4]) if annee_raw and len(annee_raw) >= 4 else None

    vehicle = {
        "id":              str(uuid.uuid4()),
        "lbc_id":          str(ad.get("list_id", "")),
        "url_annonce":     url,
        "source":          "LeBonCoin",
        "marque":          attrs.get("brand", ""),
        "modele":          attrs.get("model", ad.get("subject", "")),
        "annee":           annee,
        "km":              int(re.sub(r"\D", "", attrs.get("mileage", "0")) or 0),
        "prix_demande":    prix,
        "carburant":       attrs.get("fuel", ""),
        "boite":           attrs.get("gearbox_type", ""),
        "couleur":         attrs.get("color", ""),
        "localisation":    localisation,
        "fournisseur_nom": vendeur_nom,
        "fournisseur_tel": vendeur_tel,
        "fournisseur_type": vendeur_type,
        "photo":           photo_url,
        "titre":           ad.get("subject", ""),
        "description":     ad.get("body", "")[:300] if ad.get("body") else "",
        "column":          "sourcing",
        "sub_stage":       "a_contacter",
        "urgence":         "urg_3",
        "created_at":      datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        "historique":      [{"date": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "action": "Importé depuis LBC"}],
    }
    return vehicle


# ── GitHub API ───────────────────────────────────────────────

def gh_get(path):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def gh_put(path, content_str, sha, message):
    import base64
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    body = json.dumps({
        "message": message,
        "content": base64.b64encode(content_str.encode()).decode(),
        "sha": sha,
    }).encode()
    req = urllib.request.Request(url, data=body, method="PUT", headers={
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


# ── Main ─────────────────────────────────────────────────────

def main():
    urls_raw = os.environ.get("LBC_URLS", "").strip()
    if not urls_raw:
        print("❌ Aucune URL fournie (variable LBC_URLS vide)")
        sys.exit(1)

    urls = [u.strip() for u in re.split(r"[\n,;]+", urls_raw) if u.strip().startswith("http")]
    if not urls:
        print("❌ Aucune URL valide trouvée")
        sys.exit(1)

    print(f"📋 {len(urls)} URL(s) à traiter")

    # Charger crm.json depuis GitHub
    gh_file = gh_get(CRM_FILE)
    import base64
    crm = json.loads(base64.b64decode(gh_file["content"]).decode())
    sha = gh_file["sha"]

    existing_lbc_ids = {v.get("lbc_id") for v in crm.get("vehicles", []) if v.get("lbc_id")}

    added = []
    errors = []

    for i, url in enumerate(urls):
        print(f"\n[{i+1}/{len(urls)}] {url}")
        try:
            html = fetch_page(url)
            vehicle = parse_lbc(html, url)

            if vehicle["lbc_id"] and vehicle["lbc_id"] in existing_lbc_ids:
                print(f"  ⚠ Déjà dans le CRM (LBC ID: {vehicle['lbc_id']}) — ignoré")
                continue

            crm.setdefault("vehicles", []).append(vehicle)
            existing_lbc_ids.add(vehicle["lbc_id"])
            added.append(f"{vehicle['marque']} {vehicle['modele']} ({vehicle.get('annee','?')}) — {vehicle.get('prix','?')} €")
            print(f"  ✅ {added[-1]}")
        except Exception as e:
            errors.append(f"{url}: {e}")
            print(f"  ❌ Erreur: {e}")

        if i < len(urls) - 1:
            time.sleep(1)  # pause entre requêtes

    if not added:
        print("\n⚠ Aucun véhicule ajouté")
        if errors:
            print("Erreurs:", "\n".join(errors))
        sys.exit(1 if errors else 0)

    # Sauvegarder crm.json sur GitHub
    msg = f"Import LBC: {len(added)} véhicule(s) ajouté(s)"
    gh_put(CRM_FILE, json.dumps(crm, ensure_ascii=False, indent=2), sha, msg)
    print(f"\n✅ {msg}")
    for v in added:
        print(f"  + {v}")
    if errors:
        print(f"\n⚠ {len(errors)} erreur(s):")
        for e in errors:
            print(f"  - {e}")


if __name__ == "__main__":
    main()
