#!/usr/bin/env python3
"""
Script de briefing — lit les emails Gmail, détecte les réponses envoyées,
classe par criticité réelle et génère morning_briefing.json pour le CRM Flease.
"""

import imaplib
import email
from email.header import decode_header
import json
import os
import re
from datetime import datetime, timedelta, timezone
import hashlib

GMAIL_USER     = os.environ["GMAIL_USER"]
GMAIL_PASSWORD = os.environ["GMAIL_PASSWORD"]
MAX_EMAILS     = 40

# ── Mots-clés métier LLD / achat véhicule ────────────────────────────────────
CRITICAL_KW = [
    "urgent", "urgence", "asap", "immédiat", "critique", "relance",
    "deal", "offre", "contre-offre", "prix ferme", "vendu", "réservé",
    "virement", "paiement", "bon de commande", "bc signé", "acompte",
    "document manquant", "carte grise", "certificat de cession",
    "problème", "litige", "accident", "retard livraison",
]

HIGH_KW = [
    "devis", "estimation", "disponibilité", "intéressé", "rdv",
    "rendez-vous", "visite", "essai", "réservation", "transport",
    "livraison", "facture", "contrat", "signature", "papiers",
    "immatriculation", "contrôle technique", "ct", "revision",
    "garantie", "expertise",
]

MEDIUM_KW = [
    "information", "renseignement", "demande", "question", "suivi",
    "annonce", "véhicule", "voiture", "km", "kilométrage",
    "essai gratuit", "rappel", "confirmation",
]

# ── Expéditeurs à ignorer (newsletters, notifications) ───────────────────────
IGNORE_SENDERS = [
    "noreply", "no-reply", "donotreply", "notification", "newsletter",
    "mailer", "info@leboncoin", "alert@", "support@", "billing@",
    "invoice@", "receipt@", "automated@", "robot@",
]

IGNORE_SUBJECTS = [
    "désabonner", "unsubscribe", "newsletter", "promotion", "offre spéciale",
    "confirmez votre", "verify your", "password reset", "mot de passe",
]


def decode_str(s):
    if not s:
        return ""
    parts = decode_header(s)
    result = []
    for part, enc in parts:
        if isinstance(part, bytes):
            try:
                result.append(part.decode(enc or "utf-8", errors="replace"))
            except Exception:
                result.append(part.decode("utf-8", errors="replace"))
        else:
            result.append(str(part))
    return "".join(result)


def get_body(msg):
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            disp = str(part.get("Content-Disposition", ""))
            if ct == "text/plain" and "attachment" not in disp:
                try:
                    charset = part.get_content_charset() or "utf-8"
                    body = part.get_payload(decode=True).decode(charset, errors="replace")
                    break
                except Exception:
                    pass
    else:
        try:
            charset = msg.get_content_charset() or "utf-8"
            body = msg.get_payload(decode=True).decode(charset, errors="replace")
        except Exception:
            pass
    body = re.sub(r"<[^>]+>", " ", body)
    body = re.sub(r"http\S+", "", body)
    body = re.sub(r"\s+", " ", body).strip()
    return body[:500]


def classify_priority(subject, body, from_email):
    text = (subject + " " + body[:200]).lower()

    # Ignore: newsletter / robots
    for ignore in IGNORE_SENDERS:
        if ignore in from_email.lower():
            return "ignore"
    for ignore in IGNORE_SUBJECTS:
        if ignore in text:
            return "ignore"

    # Critique
    for kw in CRITICAL_KW:
        if kw in text:
            return "critical"

    # Haute priorité
    for kw in HIGH_KW:
        if kw in text:
            return "high"

    # Email entrant avec question (?) = toujours au moins moyen
    if "?" in body[:300]:
        return "medium"

    # Moyenne priorité
    for kw in MEDIUM_KW:
        if kw in text:
            return "medium"

    return "low"


def make_summary(body, subject):
    # Enlever les citations (lignes commençant par >)
    lines = [l for l in body.split("\n") if not l.strip().startswith(">")]
    clean = " ".join(lines).strip()
    sentences = re.split(r"[.!?\n]", clean)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 15]
    if sentences:
        return sentences[0][:150]
    return subject[:150]


def make_id(from_email, subject):
    """ID stable basé sur expéditeur + sujet normalisé."""
    norm_subj = re.sub(r"^(re|fw|fwd|tr):\s*", "", subject.lower(), flags=re.IGNORECASE).strip()
    key = f"{from_email.lower()}|{norm_subj}"
    return hashlib.md5(key.encode()).hexdigest()[:12]


def fetch_sent_thread_ids(mail, hours=48):
    """Récupère les Message-ID des emails envoyés dans les dernières N heures."""
    sent_ids = set()
    try:
        # Essayer différents noms de dossier Envoyés
        for folder in ['"[Gmail]/Sent Mail"', '"[Gmail]/Envoyés"', "Sent", "Sent Items"]:
            status, _ = mail.select(folder)
            if status == "OK":
                since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%d-%b-%Y")
                _, data = mail.search(None, f'SINCE "{since}"')
                ids = data[0].split()
                for eid in ids[-50:]:
                    try:
                        _, msg_data = mail.fetch(eid, "(BODY[HEADER.FIELDS (SUBJECT FROM IN-REPLY-TO)])")
                        raw = msg_data[0][1]
                        msg = email.message_from_bytes(raw)
                        subj = decode_str(msg.get("Subject", ""))
                        in_reply_to = msg.get("In-Reply-To", "")
                        # Stocker sujet normalisé des envois
                        norm = re.sub(r"^(re|fw|fwd|tr):\s*", "", subj.lower(), flags=re.IGNORECASE).strip()
                        sent_ids.add(norm)
                        if in_reply_to:
                            sent_ids.add(in_reply_to.strip())
                    except Exception:
                        pass
                break
    except Exception as e:
        print(f"  [sent] Impossible de lire les envois: {e}")
    return sent_ids


def fetch_emails():
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(GMAIL_USER, GMAIL_PASSWORD)

    # ── Récupérer les sujets des emails envoyés (pour détecter les réponses) ──
    print("  Analyse des emails envoyés…")
    sent_subjects = fetch_sent_thread_ids(mail, hours=72)
    print(f"  {len(sent_subjects)} threads envoyés trouvés")

    # ── Boîte de réception ────────────────────────────────────────────────────
    mail.select("INBOX")
    since = (datetime.now(timezone.utc) - timedelta(hours=48)).strftime("%d-%b-%Y")
    _, search_data = mail.search(None, f'SINCE "{since}"')

    ids = search_data[0].split()
    ids = ids[-MAX_EMAILS:]

    emails = []
    seen_ids = set()

    for eid in reversed(ids):
        try:
            _, msg_data = mail.fetch(eid, "(RFC822)")
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)

            subject  = decode_str(msg.get("Subject", "(sans objet)"))
            from_raw = decode_str(msg.get("From", ""))
            date_str = msg.get("Date", "")
            msg_id   = msg.get("Message-ID", "")

            # Extraire email propre
            match = re.search(r"<([^>]+)>", from_raw)
            from_clean = match.group(1) if match else from_raw.strip()
            from_name  = from_raw.replace(f"<{from_clean}>", "").strip().strip('"')

            body     = get_body(msg)
            priority = classify_priority(subject, body, from_clean)

            if priority == "ignore":
                continue

            summary  = make_summary(body, subject)
            email_id = make_id(from_clean, subject)

            # Dédoublonnage
            if email_id in seen_ids:
                continue
            seen_ids.add(email_id)

            # ── Détection réponse envoyée ────────────────────────────────────
            norm_subj = re.sub(r"^(re|fw|fwd|tr):\s*", "", subject.lower(), flags=re.IGNORECASE).strip()
            replied = (norm_subj in sent_subjects) or (msg_id and msg_id.strip() in sent_subjects)

            # Date lisible
            try:
                dt = email.utils.parsedate_to_datetime(date_str)
                date_iso = dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
                date_display = dt.astimezone(timezone.utc).strftime("%d/%m %H:%M")
            except Exception:
                date_iso = ""
                date_display = ""

            # Flag "attend une réponse" : email entrant avec question, non répondu
            needs_reply = (
                not replied and
                priority in ("critical", "high") and
                ("?" in body[:400] or any(kw in (subject + body[:200]).lower() for kw in ["pouvez", "pourriez", "est-ce que", "disponible", "intéressé"]))
            )

            emails.append({
                "id":           email_id,
                "from":         from_clean,
                "from_name":    from_name or from_clean.split("@")[0],
                "subject":      subject,
                "priority":     priority,
                "summary":      summary,
                "date":         date_iso,
                "date_display": date_display,
                "replied":      replied,
                "needs_reply":  needs_reply,
            })
        except Exception as e:
            print(f"  [email] Erreur sur email {eid}: {e}")

    mail.logout()

    # Tri : critiques non répondus en premier
    priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    emails.sort(key=lambda e: (
        1 if e["replied"] else 0,
        priority_order.get(e["priority"], 4),
        e["date"]
    ))

    return emails


def main():
    print(f"Connexion Gmail ({GMAIL_USER})…")
    try:
        emails = fetch_emails()
    except Exception as e:
        print(f"Erreur: {e}")
        import traceback; traceback.print_exc()
        emails = []

    # Calcul du résumé
    unreplied_critical = [e for e in emails if e["priority"] == "critical" and not e["replied"]]
    unreplied_high     = [e for e in emails if e["priority"] == "high"     and not e["replied"]]
    needs_reply_count  = len([e for e in emails if e.get("needs_reply")])

    summary = {
        "critical": len([e for e in emails if e["priority"] == "critical"]),
        "high":     len([e for e in emails if e["priority"] == "high"]),
        "medium":   len([e for e in emails if e["priority"] == "medium"]),
        "low":      len([e for e in emails if e["priority"] == "low"]),
        "replied":  len([e for e in emails if e["replied"]]),
        "needs_reply": needs_reply_count,
    }

    briefing = {
        "date":              datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "generated_at":      datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        "emails":            emails,
        "summary":           summary,
        "unreplied_critical": [e["subject"] for e in unreplied_critical[:3]],
    }

    output_path = os.path.join(os.environ.get("GITHUB_WORKSPACE", "."), "data", "morning_briefing.json")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(briefing, f, ensure_ascii=False, indent=2)

    print(f"\n✅ Briefing généré: {len(emails)} emails")
    print(f"   🔴 Critiques non répondus : {len(unreplied_critical)}")
    print(f"   🟠 Hauts non répondus     : {len(unreplied_high)}")
    print(f"   💬 Attendent une réponse  : {needs_reply_count}")
    for e in emails[:10]:
        replied_str = "✓ répondu" if e["replied"] else ("❗ réponse att." if e.get("needs_reply") else "")
        print(f"  [{e['priority'].upper():8}] {e['from'][:30]:<30} {replied_str} — {e['subject'][:50]}")


if __name__ == "__main__":
    main()
