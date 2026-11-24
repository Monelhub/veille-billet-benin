#!/usr/bin/env python3
"""
Veille "vols spéciaux Paris–Cotonou" (Bénin Tours / gouvernement béninois).

Surveille voyage.benin.bj (site officiel de vente), gouv.bj et Google Actualités,
et envoie une alerte (Telegram, notification push ntfy, email) à la moindre
modification utile.

Aucune dépendance : Python 3.8+ standard uniquement.

Usage :
  python veille_benin.py --once                 # une vérification puis sortie
  python veille_benin.py --loop --interval 60   # boucle infinie (PC, serveur, Raspberry)
  python veille_benin.py --loop --interval 45 --duration 270   # boucle limitée (GitHub Actions)
  python veille_benin.py --test                 # envoie un message de test sur tous les canaux

Configuration par variables d'environnement (toutes facultatives, au moins un canal conseillé) :
  TELEGRAM_TOKEN, TELEGRAM_CHAT_ID   (plusieurs chat_id séparés par des virgules)
  NTFY_TOPIC  [NTFY_SERVER=https://ntfy.sh]
  SMTP_HOST, SMTP_PORT(465), SMTP_USER, SMTP_PASS, EMAIL_TO (virgules), EMAIL_FROM
  STATE_FILE (défaut : etat_veille.json)
  HEARTBEAT_HOUR (défaut : 9, heure de Paris du message quotidien "je tourne toujours")
"""

import argparse
import hashlib
import html
import json
import os
import random
import re
import smtplib
import ssl
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

# ---------------------------------------------------------------------------
# Ce qu'on surveille
# ---------------------------------------------------------------------------

OFFICIAL_URL = "https://voyage.benin.bj/"

# Mots dont la DISPARITION sur le site officiel = les ventes ouvrent probablement.
GONE_TRIGGERS = ["bientôt disponible", "bientot disponible", "communiquées ultérieurement",
                 "communiquees ulterieurement"]
# Mots dont l'APPARITION sur le site officiel = alerte maximale.
APPEAR_TRIGGERS = ["réserver", "reserver", "réservation", "reservation", "acheter",
                   "billet", "aller-retour", "aller simple", "€", "eur ", "paiement",
                   "places", "décembre", "decembre", "janvier", "2027", "passager"]

# Mots-clés pour filtrer les nouveautés sur les pages "bavardes" et dans les actus.
TOPIC_KEYWORDS = ["vol", "vols", "cotonou", "paris", "affrètement", "affretement",
                  "benin tours", "bénin tours", "lunajet", "charter", "billet",
                  "diaspora", "fin d'année", "fêtes", "voyage.benin.bj"]

GNEWS = "https://news.google.com/rss/search?hl=fr&gl=FR&ceid=FR:fr&q="
NEWS_QUERIES = [
    '"voyage.benin.bj"',
    '"Benin Tours" vols',
    'vols Paris Cotonou gouvernement',
    'affrètement vols Cotonou',
    'vols spéciaux Cotonou fêtes',
]

PAGES = [
    # critical=True : toute modification du texte = alerte ; déclencheurs = URGENCE
    {"id": "voyage.benin.bj", "url": OFFICIAL_URL, "critical": True},
    {"id": "voyage.benin.bj (www)", "url": "https://www.voyage.benin.bj/", "critical": True,
     "optional": True},
    # pages bavardes : alerte seulement si une NOUVELLE ligne parle du sujet
    {"id": "gouv.bj", "url": "https://www.gouv.bj/", "critical": False},
    {"id": "gouv.bj actualités", "url": "https://www.gouv.bj/actualites/", "critical": False,
     "optional": True},
    {"id": "benin.bj", "url": "https://benin.bj/", "critical": False, "optional": True},
]

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
PARIS = timezone(timedelta(hours=1))  # heure d'hiver ; suffit pour le heartbeat
URGENT_REPEAT = 3                    # nb de rappels pour une alerte urgente
UNREACHABLE_ALERT_AFTER = 3          # échecs consécutifs avant alerte "site injoignable"


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------

def now_paris():
    return datetime.now(PARIS)


def log(msg):
    print(f"[{now_paris():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def fetch(url, timeout=25):
    """Renvoie (status, url_finale, octets). Lève une exception en cas d'échec réseau."""
    sep = "&" if "?" in url else "?"
    # paramètre anti-cache pour les pages (pas pour Google News)
    real = url if "news.google" in url or url.startswith("file:") else f"{url}{sep}_nc={int(time.time())}"
    req = urllib.request.Request(real, headers={
        "User-Agent": USER_AGENT,
        "Accept-Language": "fr-FR,fr;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return getattr(r, "status", 200), r.geturl(), r.read()


BLOCK_TAGS = r"</?(p|div|br|li|ul|ol|h[1-6]|tr|td|th|section|article|header|footer|title|button|a|span|option)\b[^>]*>"


def html_to_lines(raw_bytes):
    text = raw_bytes.decode("utf-8", errors="replace")
    text = re.sub(r"(?is)<(script|style|noscript|svg)\b.*?</\1>", " ", text)
    text = re.sub(r"(?is)<!--.*?-->", " ", text)
    # garder title / meta description, utiles sur une page "bientôt disponible"
    metas = re.findall(r'(?is)<meta[^>]+(?:name|property)=["\'](?:description|og:title|og:description)["\'][^>]*content=["\']([^"\']*)', text)
    text = re.sub(BLOCK_TAGS, "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    lines = [re.sub(r"\s+", " ", l).strip() for l in text.split("\n")]
    lines += [f"[meta] {html.unescape(m).strip()}" for m in metas]
    return [l for l in lines if l]


def asset_signature(raw_bytes):
    """Empreinte des fichiers JS/CSS référencés : change quand le site est redéployé."""
    text = raw_bytes.decode("utf-8", errors="replace")
    assets = sorted(set(re.findall(r'(?:src|href)=["\']([^"\']+\.(?:js|css)[^"\']*)', text, re.I)))
    return hashlib.sha256("\n".join(assets).encode()).hexdigest()[:16], assets


def links_of(raw_bytes):
    text = raw_bytes.decode("utf-8", errors="replace")
    return sorted(set(re.findall(r'href=["\']([^"\'#]+)', text, re.I)))


def sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def contains_any(text, words):
    low = text.lower()
    return [w for w in words if w in low]


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

def _post(url, data=None, headers=None, timeout=20):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status


def notify(title, body, level="info", dry_run=False):
    """level : info | important | urgent"""
    icon = {"info": "ℹ️", "important": "⚠️", "urgent": "🚨🚨🚨"}[level]
    full = f"{icon} {title}\n\n{body}\n\n— Veille Bénin, {now_paris():%d/%m %H:%M}"
    if dry_run:
        print("----- NOTIFICATION (dry-run) -----\n" + full + "\n---------------------------------")
        return
    sent = 0

    tok, chats = os.getenv("TELEGRAM_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if tok and chats:
        for chat in [c.strip() for c in chats.split(",") if c.strip()]:
            try:
                data = urllib.parse.urlencode({
                    "chat_id": chat, "text": full[:4000],
                    "disable_web_page_preview": "false",
                }).encode()
                _post(f"https://api.telegram.org/bot{tok}/sendMessage", data)
                sent += 1
            except Exception as e:
                log(f"Telegram KO ({chat}) : {e}")

    topic = os.getenv("NTFY_TOPIC")
    if topic:
        server = os.getenv("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
        prio = {"info": "default", "important": "high", "urgent": "max"}[level]
        try:
            # paramètres en query-string : évite les soucis d'accents dans les en-têtes HTTP
            qs = urllib.parse.urlencode({
                "title": title, "priority": prio, "click": OFFICIAL_URL,
                "tags": "rotating_light" if level == "urgent" else "airplane",
            })
            _post(f"{server}/{topic}?{qs}", body.encode("utf-8")[:4000],
                  {"Content-Type": "text/plain; charset=utf-8"})
            sent += 1
        except Exception as e:
            log(f"ntfy KO : {e}")

    host, to = os.getenv("SMTP_HOST"), os.getenv("EMAIL_TO")
    if host and to:
        try:
            msg = EmailMessage()
            msg["Subject"] = f"{'[URGENT] ' if level == 'urgent' else ''}{title}"
            msg["From"] = os.getenv("EMAIL_FROM") or os.getenv("SMTP_USER")
            msg["To"] = to
            if level == "urgent":
                msg["X-Priority"] = "1"
                msg["Importance"] = "high"
            msg.set_content(full)
            port = int(os.getenv("SMTP_PORT", "465"))
            ctx = ssl.create_default_context()
            if port == 465:
                s = smtplib.SMTP_SSL(host, port, context=ctx, timeout=30)
            else:
                s = smtplib.SMTP(host, port, timeout=30)
                s.starttls(context=ctx)
            s.login(os.getenv("SMTP_USER"), os.getenv("SMTP_PASS"))
            s.send_message(msg)
            s.quit()
            sent += 1
        except Exception as e:
            log(f"Email KO : {e}")

    if sent == 0:
        log("AUCUN canal de notification n'a fonctionné ! Message :\n" + full)
    else:
        log(f"Notification envoyée ({level}) sur {sent} canal(aux) : {title}")


# ---------------------------------------------------------------------------
# Vérifications
# ---------------------------------------------------------------------------

def check_page(src, state, alerts):
    sid = src["id"]
    st = state.setdefault("pages", {}).setdefault(sid, {})
    try:
        status, final_url, raw = fetch(src["url"])
    except Exception as e:
        st["fails"] = st.get("fails", 0) + 1
        log(f"{sid} : échec ({st['fails']}) {e}")
        if src.get("critical") and not src.get("optional") and st["fails"] == UNREACHABLE_ALERT_AFTER:
            alerts.append(("important", f"{sid} ne répond plus",
                           f"Le site officiel est injoignable depuis {st['fails']} vérifications "
                           f"({e}).\nÇa peut être une panne… ou un afflux de visiteurs parce que "
                           f"les ventes viennent d'ouvrir. Va voir : {src['url']}"))
        return
    if st.get("fails", 0) >= UNREACHABLE_ALERT_AFTER and src.get("critical") and not src.get("optional"):
        alerts.append(("important", f"{sid} répond de nouveau", f"Va vérifier : {src['url']}"))
    st["fails"] = 0

    lines = html_to_lines(raw)
    text = "\n".join(lines)
    text_hash = sha(text)
    assets_hash, assets = asset_signature(raw)
    links = links_of(raw)
    first = "text_hash" not in st

    if first:
        st.update(text_hash=text_hash, lines=lines[:400], assets=assets_hash, links=links[:300],
                  final_url=final_url, status=status)
        gone_now = contains_any(text, GONE_TRIGGERS)
        log(f"{sid} : état initial enregistré ({len(lines)} lignes, "
            f"'bientôt disponible' présent : {bool(gone_now)})")
        # Si, dès le départ, le site officiel n'est plus en "bientôt disponible" : on prévient.
        if src.get("critical") and not gone_now and contains_any(text, APPEAR_TRIGGERS):
            alerts.append(("urgent", "Le site officiel semble DÉJÀ ouvert",
                           f"{src['url']} ne dit pas « bientôt disponible » et contient : "
                           f"{', '.join(contains_any(text, APPEAR_TRIGGERS))}.\nVa voir MAINTENANT."))
        return

    old_lines = set(st.get("lines", []))
    new_lines = [l for l in lines if l not in old_lines]
    removed = [l for l in st.get("lines", []) if l not in set(lines)]
    old_text = "\n".join(st.get("lines", []))

    if src.get("critical"):
        redirected = final_url.split("?")[0] != st.get("final_url", "").split("?")[0]
        if text_hash != st["text_hash"] or redirected:
            was_soon = bool(contains_any(old_text, GONE_TRIGGERS))
            is_soon = bool(contains_any(text, GONE_TRIGGERS))
            appeared = [w for w in contains_any(text, APPEAR_TRIGGERS)
                        if w not in contains_any(old_text, APPEAR_TRIGGERS)]
            level = "urgent" if (was_soon and not is_soon) or appeared or redirected else "important"
            body = [f"Le site officiel a changé : {src['url']}"]
            if redirected:
                body.append(f"Il redirige maintenant vers : {final_url}")
            if was_soon and not is_soon:
                body.append("« Bientôt disponible » a DISPARU → les ventes ouvrent sans doute.")
            if appeared:
                body.append("Nouveaux mots : " + ", ".join(appeared))
            if new_lines:
                body.append("Nouveau texte :\n- " + "\n- ".join(new_lines[:15]))
            if removed:
                body.append("Texte retiré :\n- " + "\n- ".join(removed[:8]))
            if level == "urgent":
                body.append("👉 Ouvre le site TOUT DE SUITE, prépare passeport + carte bancaire.")
            alerts.append((level, "VENTES BÉNIN : le site officiel a changé" if level == "urgent"
                           else "voyage.benin.bj a été modifié", "\n\n".join(body)))
        elif assets_hash != st.get("assets") or links != st.get("links"):
            new_links = [l for l in links if l not in set(st.get("links", []))]
            alerts.append(("info", "voyage.benin.bj : modification technique",
                           "Le texte est identique mais le code du site a été redéployé "
                           "(fichiers JS/CSS ou liens). Souvent signe qu'une mise en ligne se prépare."
                           + (("\nNouveaux liens : " + ", ".join(new_links[:10])) if new_links else "")
                           + f"\n{src['url']}"))
    else:
        hits = [l for l in new_lines if contains_any(l, TOPIC_KEYWORDS)
                and contains_any(l, ["vol", "cotonou", "affr", "benin tours", "bénin tours", "lunajet", "billet"])]
        if hits:
            alerts.append(("important", f"{sid} : nouvelle info possible sur les vols",
                           "Nouvelles lignes :\n- " + "\n- ".join(hits[:10]) + f"\n\n{src['url']}"))

    st.update(text_hash=text_hash, lines=lines[:400], assets=assets_hash, links=links[:300],
              final_url=final_url, status=status)


def check_news(state, alerts):
    seen = state.setdefault("news_seen", {})
    first_run = not state.get("news_initialized")
    fresh = []
    for q in NEWS_QUERIES:
        try:
            _, _, raw = fetch(GNEWS + urllib.parse.quote(q))
            root = ET.fromstring(raw)
        except Exception as e:
            log(f"Google News '{q}' : échec {e}")
            continue
        for item in root.iter("item"):
            guid = (item.findtext("guid") or item.findtext("link") or "").strip()
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub = (item.findtext("pubDate") or "").strip()
            if not guid or guid in seen:
                continue
            seen[guid] = int(time.time())
            if first_run:
                continue
            # ne garder que le récent (72 h)
            try:
                from email.utils import parsedate_to_datetime
                if datetime.now(timezone.utc) - parsedate_to_datetime(pub) > timedelta(hours=72):
                    continue
            except Exception:
                pass
            if contains_any(title, TOPIC_KEYWORDS):
                fresh.append(f"• {title}\n  {link}")
    # purge > 30 jours
    cutoff = time.time() - 30 * 86400
    state["news_seen"] = {k: v for k, v in seen.items() if v > cutoff}
    state["news_initialized"] = True
    if fresh:
        alerts.append(("important", f"{len(fresh)} nouvel(le)s article(s) sur les vols Bénin",
                       "\n\n".join(fresh[:8]) +
                       "\n\n⚠️ Ne réserve QUE sur voyage.benin.bj : de faux sites circulent."))


def heartbeat(state, dry_run):
    hour = int(os.getenv("HEARTBEAT_HOUR", "9"))
    n = now_paris()
    today = n.strftime("%Y-%m-%d")
    if n.hour >= hour and state.get("last_heartbeat") != today:
        p = state.get("pages", {}).get("voyage.benin.bj", {})
        soon = contains_any("\n".join(p.get("lines", [])), GONE_TRIGGERS)
        notify("Veille Bénin active",
               f"Je surveille toujours voyage.benin.bj, gouv.bj et les actus.\n"
               f"État du site officiel : {'« bientôt disponible » (pas encore de vente)' if soon else 'à vérifier'}\n"
               f"Vérifications depuis hier : {state.get('checks_since_hb', 0)}",
               "info", dry_run)
        state["last_heartbeat"] = today
        state["checks_since_hb"] = 0


def run_once(state, dry_run=False):
    alerts = []
    for src in PAGES:
        check_page(src, state, alerts)
    # Les actus changent moins vite : toutes les 5 minutes suffisent
    if time.time() - state.get("last_news", 0) > 290:
        check_news(state, alerts)
        state["last_news"] = time.time()
    state["checks_since_hb"] = state.get("checks_since_hb", 0) + 1
    state["last_check"] = now_paris().isoformat()

    for level, title, body in alerts:
        repeats = URGENT_REPEAT if level == "urgent" else 1
        for i in range(repeats):
            notify(title if i == 0 else f"RAPPEL {i}/{repeats - 1} — {title}", body, level, dry_run)
            if i < repeats - 1 and not dry_run:
                time.sleep(20)
    heartbeat(state, dry_run)
    return alerts


def load_state(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(path, state):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--interval", type=int, default=60, help="secondes entre deux vérifications")
    ap.add_argument("--duration", type=int, default=0, help="arrêter la boucle après N secondes (0 = jamais)")
    ap.add_argument("--test", action="store_true", help="envoyer une notification de test")
    ap.add_argument("--dry-run", action="store_true", help="afficher au lieu d'envoyer")
    a = ap.parse_args()

    state_path = os.getenv("STATE_FILE", "etat_veille.json")

    if a.test:
        notify("Test Veille Bénin", "Si tu lis ceci, les alertes fonctionnent ✅\n"
               f"Site surveillé : {OFFICIAL_URL}", "urgent", a.dry_run)
        return

    state = load_state(state_path)
    start = time.time()
    while True:
        try:
            run_once(state, a.dry_run)
        except Exception as e:
            log(f"Erreur inattendue : {e!r}")
        save_state(state_path, state)
        if not a.loop:
            break
        if a.duration and time.time() - start + a.interval > a.duration:
            break
        time.sleep(a.interval + random.uniform(-5, 5))


if __name__ == "__main__":
    sys.exit(main())
