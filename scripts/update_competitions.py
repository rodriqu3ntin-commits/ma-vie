#!/usr/bin/env python3
"""Ma vie — collecte des compétitions de JJB / grappling à venir en France.

Sources :
  - CFJJB (cfjjb.com/competitions/calendrier-competitions)
  - Smoothcomp (événements en France, JJB / grappling / no-gi)
  - France Judo Jiu-Jitsu (publie ses compétitions sur francejujitsu.smoothcomp.com)

Écrit competitions.json à la racine du site. Lancé chaque jour par GitHub Actions.
Aucune dépendance externe (bibliothèque standard uniquement).
"""
import datetime as dt
import html
import json
import re
import sys
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
OUT = Path(__file__).resolve().parent.parent / "competitions.json"
TODAY = dt.date.today()
MONTHS = {m: i + 1 for i, m in enumerate(
    ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
     "septembre", "octobre", "novembre", "décembre"])}
MONTHS.update({"fevrier": 2, "aout": 8, "decembre": 12})
# Catégories Smoothcomp : 1 = jiu-jitsu brésilien, 7 = grappling / no-gi, 4 = ADCC
SMOOTH_GROUPS = {"1", "7", "4"}
SKIP_TITLE = re.compile(r"luta\s*livre|s[ée]minaire|seminar|referee|arbitr|stage technique|open mat", re.I)


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "fr-FR,fr;q=0.9"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", "replace")


# ---------------------------------------------------------------- CFJJB
class CfjjbParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.cards, self.cur, self.depth, self.cap, self.buf = [], None, 0, None, ""

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "li" and "competition-card" in (a.get("class") or ""):
            self.cur = {"title": "", "spans": [], "links": [], "kinds": (a.get("data-epreuves") or "").split()}
            self.depth = 1
            return
        if not self.cur:
            return
        if tag == "li":
            self.depth += 1
        if tag == "h3":
            self.cap, self.buf = "title", ""
        elif tag == "span" and self.cap is None:
            self.cap, self.buf = "span", ""
        elif tag == "a" and a.get("href"):
            self.cur["links"].append(a["href"])

    def handle_endtag(self, tag):
        if not self.cur:
            return
        if tag == "h3" and self.cap == "title":
            self.cur["title"] = " ".join(self.buf.split())
            self.cap = None
        elif tag == "span" and self.cap == "span":
            t = " ".join(self.buf.split())
            if t:
                self.cur["spans"].append(t)
            self.cap = None
        elif tag == "li":
            self.depth -= 1
            if self.depth == 0:
                self.cards.append(self.cur)
                self.cur = None

    def handle_data(self, data):
        if self.cur and self.cap:
            self.buf += data


def parse_fr_dates(text):
    """« Samedi 31 janvier et dimanche 1 février 2027 » -> (2027-01-31, 2027-02-01)."""
    t = text.lower()
    year = re.findall(r"(20\d\d)", t)
    if not year:
        return None, None
    year = int(year[-1])
    toks = re.findall(r"\b(\d{1,2})(?:er)?\b\s*(janvier|f[ée]vrier|mars|avril|mai|juin|juillet|ao[uû]t|septembre|octobre|novembre|d[ée]cembre)?", t)
    toks = [(int(d), m) for d, m in toks if int(d) <= 31]
    if not toks:
        return None, None
    days, month = [], None
    for d, m in reversed(toks):  # le mois s'applique aux jours qui le précèdent
        if m:
            month = MONTHS.get(m.replace("û", "u"), MONTHS.get(m))
        days.append((d, month))
    days.reverse()
    out = []
    for d, m in days:
        if not m:
            continue
        try:
            out.append(dt.date(year, m, d))
        except ValueError:
            pass
    if not out:
        return None, None
    start, end = min(out), max(out)
    if (end - start).days > 20:  # « 31 décembre 2026 et 1 janvier 2027 »
        start = start.replace(year=start.year - 1) if start.month == 12 else start
    return start, end


def cfjjb():
    p = CfjjbParser()
    p.feed(get("https://cfjjb.com/competitions/calendrier-competitions"))
    out = []
    for c in p.cards:
        date_txt = next((s for s in c["spans"] if re.search(r"20\d\d", s)), "")
        start, end = parse_fr_dates(date_txt)
        if not start or not c["title"]:
            continue
        place = next((s for s in c["spans"] if "·" in s and not re.search(r"20\d\d|Juv[ée]nile|Adulte|Master|Inscri|r[ée]sultat|organisateur", s, re.I)), "")
        venue, _, city = place.rpartition("·")
        links = [l for l in c["links"] if not l.endswith("/resultats")]
        reg = next((l for l in links if "/competitions/info/" in l), links[0] if links else "https://cfjjb.com/competitions/calendrier-competitions")
        kinds = c["kinds"]
        tags = [t for t, k in (("GI", "gi"), ("NO GI", "nogi")) if k in kinds]
        if any(k.startswith("kids") for k in kinds):
            tags.append("Kids")
        out.append({"title": c["title"], "start": start.isoformat(), "end": end.isoformat(),
                    "city": (city or place).strip(), "venue": venue.strip(), "source": "CFJJB",
                    "url": html.unescape(reg), "tags": tags})
    return out


# ---------------------------------------------------------------- Smoothcomp + France Judo
def smoothcomp():
    page = get("https://smoothcomp.com/fr/events/upcoming")
    m = re.search(r"var events = (\[.*?\]);\s*\n", page, re.S)
    if not m:
        raise RuntimeError("liste Smoothcomp introuvable (page modifiée ou bloquée)")
    out = []
    for e in json.loads(m.group(1)):
        if e.get("location_country") != "FR" or e.get("eventEnded"):
            continue
        if not SMOOTH_GROUPS.intersection(set(map(str, e.get("categoryGroups") or []))):
            continue
        title = " ".join(html.unescape(e.get("title", "")).split())
        if SKIP_TITLE.search(title):
            continue
        url = e.get("url", "")
        src = "France Judo" if "francejujitsu." in url else "Smoothcomp"
        groups = set(map(str, e.get("categoryGroups") or []))
        tags = []
        if re.search(r"no[\s-]?gi", title, re.I) or ("7" in groups and "1" not in groups):
            tags.append("NO GI")
        if re.search(r"\bgi\b", title, re.I) or "1" in groups:
            tags.insert(0, "GI")
        if re.search(r"kids|enfant|u1[0-6]", title, re.I):
            tags.append("Kids")
        out.append({"title": title, "start": e.get("startdate"), "end": e.get("enddate") or e.get("startdate"),
                    "city": " ".join((e.get("location_city") or "").split()), "venue": "", "source": src,
                    "url": url, "tags": list(dict.fromkeys(tags))})
    return out


def main():
    events, errors = [], []
    for name, fn in (("CFJJB", cfjjb), ("Smoothcomp", smoothcomp)):
        try:
            got = fn()
            print(f"{name}: {len(got)} compétitions", file=sys.stderr)
            events += got
        except Exception as exc:  # une source en panne ne bloque pas les autres
            errors.append(f"{name}: {exc}")
            print(f"{name}: ERREUR {exc}", file=sys.stderr)
    # garder les données précédentes d'une source en panne
    if errors and OUT.exists():
        old = json.loads(OUT.read_text())
        failed = {e.split(":")[0] for e in errors}
        keep = {"Smoothcomp", "France Judo"} if "Smoothcomp" in failed else set()
        if "CFJJB" in failed:
            keep.add("CFJJB")
        events += [e for e in old.get("events", []) if e.get("source") in keep]
    events = [e for e in events if e.get("end") and e["end"] >= TODAY.isoformat()]
    seen, uniq = set(), []
    for e in sorted(events, key=lambda e: (e["start"], e["title"].lower())):
        k = (e["start"], e["title"].lower(), e["source"])
        if k not in seen:
            seen.add(k)
            uniq.append(e)
    data = {"updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"),
            "sources": ["CFJJB", "Smoothcomp", "France Judo"], "errors": errors, "events": uniq}
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n")
    print(f"{len(uniq)} compétitions écrites dans {OUT.name}", file=sys.stderr)
    if len(errors) == 2:
        sys.exit(1)


if __name__ == "__main__":
    main()
