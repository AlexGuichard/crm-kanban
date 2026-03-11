#!/usr/bin/env python3
"""
Script de briefing matinal — lit les emails Gmail non lus des dernières 24h
et génère morning_briefing.json pour le CRM Flease.
"""

import imaplib
import email
from email.header import decode_header
import json
import os
import re
from datetime import datetime, timedelta, timezone
import base64

GMAIL_USER     = os.environ["GMAIL_USER"]
GMAIL_PASSWORD = os.environ["GMAIL_PASSWORD"]   # mot de passe d'application Google
MAX_EMAILS     = 20

PRIORITY_KEYWORDS = {
    "urgent": ["urgent", "urgence", "asap", "immédiat", "critique", "important", "relance"],
    "normal": ["devis", "contrat", "livraison", "transport", "facture", "virement", "document", "papier"],
}

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
    """Extrait le texte du corps de l'email."""
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
    # Nettoyer: retirer URLs, balises HTML, espaces multiples
    body = re.sub(r"<[^>]+>", " ", body)
    body = re.sub(r"http\S+", "", body)
    body = re.sub(r"\s+", " ", body).strip()
    return body[:300]

def classify_priority(subject, body):
    text = (subject + " " + body).lower()
    for kw in PRIORITY_KEYWORDS["urgent"]:
        if kw in text:
            return "urgent"
    for kw in PRIORITY_KEYWORDS["normal"]:
        if kw in text:
            return "normal"
    return "info"

def make_summary(body, subject):
    """Génère un résumé court du corps de l'email."""
    sentences = re.split(r"[.!?\n]", body)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 20]
    if sentences:
        return sentences[0][:120]
    return subject[:120]

def fetch_emails():
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(GMAIL_USER, GMAIL_PASSWORD)
    mail.select("INBOX")

    # Emails depuis hier
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%d-%b-%Y")
    _, search_data = mail.search(None, f'(UNSEEN SINCE "{since}")')

    ids = search_data[0].split()
    ids = ids[-MAX_EMAILS:]  # garder les plus récents

    emails = []
    for eid in reversed(ids):
        _, msg_data = mail.fetch(eid, "(RFC822)")
        raw = msg_data[0][1]
        msg = email.message_from_bytes(raw)

        subject = decode_str(msg.get("Subject", "(sans objet)"))
        from_   = decode_str(msg.get("From", ""))
        body    = get_body(msg)
        priority = classify_priority(subject, body)
        summary  = make_summary(body, subject)

        # Extraire juste l'adresse email du champ From
        match = re.search(r"<([^>]+)>", from_)
        from_clean = match.group(1) if match else from_

        emails.append({
            "from":     from_clean,
            "subject":  subject,
            "priority": priority,
            "summary":  summary,
        })

    mail.logout()
    return emails

def main():
    print(f"Connexion Gmail ({GMAIL_USER})…")
    try:
        emails = fetch_emails()
    except Exception as e:
        print(f"Erreur IMAP: {e}")
        emails = []

    # Alertes CRM depuis les données GitHub si disponibles
    crm_alerts = os.environ.get("CRM_ALERTS_TEXT", "")

    briefing = {
        "date":         datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        "emails":       emails,
        "crm_alerts":   crm_alerts,
        "summary":      f"{len(emails)} email(s) non lu(s) des dernières 24h",
    }

    output_path = os.path.join(os.environ.get("GITHUB_WORKSPACE", "."), "data", "morning_briefing.json")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(briefing, f, ensure_ascii=False, indent=2)

    print(f"✅ Briefing généré: {len(emails)} emails → {output_path}")
    for e in emails:
        print(f"  [{e['priority'].upper():6}] {e['from']} — {e['subject'][:60]}")

if __name__ == "__main__":
    main()
