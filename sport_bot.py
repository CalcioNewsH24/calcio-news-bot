#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bot Telegram: canale notizie CALCIO + CALCIOMERCATO in automatico.
Legge i feed RSS delle fonti, filtra solo calcio, scarta le notizie vecchie
e pubblica sul canale (max N per ciclo, niente duplicati).

Uso:
  1) crea il bot con @BotFather -> token
  2) aggiungi il bot come ADMIN del canale (permesso: pubblica messaggi)
  3) compila BOT_TOKEN e CHANNEL qui sotto
  4) avvia:   python3 sport_bot.py            (controlla le fonti ogni POLL_MINUTES)
              python3 sport_bot.py --dry-run  (stampa a video senza inviare)
"""
import sys, time, json, os, re, html
import feedparser
import requests

# ───────────────────────── CONFIG ─────────────────────────
# Legge i valori dalle variabili d'ambiente (usate su GitHub Actions)
# oppure usa i valori scritti qui sotto (per l'uso locale sul PC).
BOT_TOKEN = os.environ.get("BOT_TOKEN", "INSERISCI_QUI_IL_TOKEN")  # da @BotFather
CHANNEL   = os.environ.get("CHANNEL", "@tuo_canale")               # username del canale (bot admin)

# Fonti Sicure. mode="calcio": il feed parla SOLO di calcio -> tutto ok.
# mode="mixed": feed generalista -> pubblica solo le notizie con parola chiave calcio.
FEEDS = {
    "ANSA Calcio":            {"url": "https://www.ansa.it/sito/notizie/sport/calcio/calcio_rss.xml", "mode": "calcio"},
    "Gazzetta dello Sport":   {"url": "https://www.gazzetta.it/rss/calcio.xml", "mode": "calcio"},
    "Corriere dello Sport":   {"url": "https://www.corrieredellosport.it/rss/calcio", "mode": "calcio"},
    "TuttoMercatoWeb":        {"url": "https://www.tuttomercatoweb.com/rss", "mode": "calcio"},
    "Sky TG24":               {"url": "https://tg24.sky.it/rss/tg24.xml", "mode": "mixed"},
    "Rai News":               {"url": "https://www.rainews.it/rss/sport", "mode": "mixed"},
    # NOTA: solo fonti italiane. Se in futuro aggiungi fonti straniere
    # (es. BBC Football) serve anche la traduzione automatica, non inclusa.
}

# Parole chiave per le fonti "mixed" (bastano, sono generiche: coprono tutto il calcio)
CALCIO_KEYS = ["calcio", "calciomercato", "serie a", "serie b", "champions",
               "europa league", "conference", "nazionale", "azzurri",
               "coppa italia", "supercoppa", "premier league", "liga", "bundesliga",
               "ligue 1", "atleta", "attaccante", "difensore",
               "centrocampista", "portiere", "allenatore", "arbitro", "fifa", "uefa"]

# Per le fonti GENERALISTE (Sky TG24, Rai) usiamo SOLO parole inequivocabili:
# i nomi delle squadre (roma, inter, milan...) possono comparire in notizie
# di cronaca ("Roma: ...") e genererebbero falsi positivi.
MIXED_KEYS = ["calcio", "calciomercato", "serie a", "serie b", "champions",
              "europa league", "conference league", "coppa italia",
              "supercoppa", "premier league", "fifa", "uefa", "azzurri"]

POLL_MINUTES  = 10
MAX_PER_CYCLE = 5
MAX_PER_SOURCE = 3
MAX_AGE_HOURS = 36          # scarta notizie piu vecchie (i feed a volte contengono vecchie)
KEYWORDS_ONLY = []          # opzionale: ["inter", "juventus"] -> solo quella squadra
BLACKLIST = ["in diretta", "diretta live", "live blog"]

SEEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen.json")
# ────────────────────────────────────────────────────────────

API = "https://api.telegram.org/bot{}/{}"

def load_seen():
    try:
        with open(SEEN_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()

def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(seen)[-5000:], f)

def clean(text):
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    return re.sub(r"\s+", " ", text).strip()

def age_hours(entry):
    st = entry.get("published_parsed") or entry.get("updated_parsed")
    if not st:
        return 0  # senza data la teniamo (non sappiamo che sia vecchia)
    import calendar
    return max(0, (time.time() - calendar.timegm(st)) / 3600.0)

def matches(mode, title, summary):
    blob = (title + " " + summary).lower()
    if any(b in blob for b in BLACKLIST):
        return False
    def word_match(kw, text):
        # corrispondenza a PAROLA INTERA: "inter" non fa match con "interagisce"
        return re.search(r"\b" + re.escape(kw.lower()) + r"\b", text.lower())
    if mode == "mixed" and not any(word_match(k, title) for k in MIXED_KEYS):
        return False
    if KEYWORDS_ONLY and not any(word_match(k, blob) for k in KEYWORDS_ONLY):
        return False
    return True

def collect():
    seen = load_seen()
    per_source = {}
    for source, cfg in FEEDS.items():
        taken = 0
        d = feedparser.parse(cfg["url"])
        per_source[source] = []
        for e in d.entries:
            link = e.get("link", "")
            uid  = e.get("id") or link
            if not link or uid in seen:
                continue
            if age_hours(e) > MAX_AGE_HOURS:
                continue
            title   = clean(e.get("title", ""))
            summary = clean(e.get("summary", ""))[:220]
            if not title or not matches(cfg["mode"], title, summary):
                continue
            per_source[source].append({"id": uid, "source": source, "title": title,
                                       "summary": summary, "link": link})
            taken += 1
            if taken >= MAX_PER_SOURCE * 3:
                break
    # ROTAZIONE EQUA: prende a turno una notizia da ogni fonte,
    # max MAX_PER_SOURCE per fonte, cosi il canale non diventa "solo ANSA".
    news = []
    taken_per_source = {s: 0 for s in per_source}
    while len(news) < MAX_PER_CYCLE * 3:
        added = False
        for source, items in per_source.items():
            if taken_per_source[source] >= MAX_PER_SOURCE or not items:
                continue
            news.append(items.pop(0))
            taken_per_source[source] += 1
            added = True
        if not added:
            break
    # ANTI-DUPLICATI di titolo: scarta la seconda versione della stessa notizia
    seen_titles = set()
    unique = []
    for n in news:
        key = re.sub(r"[^a-z0-9 ]", "", n["title"].lower())[:45]
        if key in seen_titles:
            continue
        seen_titles.add(key)
        unique.append(n)
    return unique, seen

def format_message(n):
    return (f"⚽ <b>{n['title']}</b>"
            f"\n\n{n['summary']}…"
            f"\n\n🔗 <a href=\"{n['link']}\">Leggi su {n['source']}</a>")

def send(msg):
    r = requests.post(API.format(BOT_TOKEN, "sendMessage"), data={
        "chat_id": CHANNEL, "text": msg, "parse_mode": "HTML"}, timeout=15)
    return r.ok, r.text if not r.ok else ""

def main():
    dry = "--dry-run" in sys.argv
    cycle = 0
    while True:
        cycle += 1
        news, seen = collect()
        sent = 0
        for n in news:
            if sent >= MAX_PER_CYCLE:
                break
            if dry:
                print(f"[{n['source']}] {n['title']}\n  {n['link']}")
            else:
                ok, err = send(format_message(n))
                if not ok:
                    print("ERRORE invio:", err)
                    if "chat not found" in err or "Forbidden" in err:
                        print("→ Controlla che il bot sia ADMIN del canale e CHANNEL sia corretto.")
                    break
            seen.add(n["id"])
            sent += 1
            time.sleep(2)
        save_seen(seen)
        print(f"[ciclo {cycle}] pubblicate {sent} notizie")
        if dry or "--once" in sys.argv or os.environ.get("CI") == "true":
            break
        time.sleep(POLL_MINUTES * 60)

if __name__ == "__main__":
    main()
