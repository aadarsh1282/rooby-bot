# scrape_hackathons.py
# Scrapes multiple hackathon sources (Devpost, MLH, Lu.ma, Hack Club, Hackeroos)
# and writes a merged JSON file to data/hackathons.json for Pika-Bot.

import os
import re
import json
from datetime import datetime
from typing import List, Dict, Any

import httpx
from bs4 import BeautifulSoup
from seleniumbase import SB

DATA_DIR = "data"
OUTPUT_PATH = os.path.join(DATA_DIR, "hackathons.json")
HACKEROOS_INPUT = os.path.join(DATA_DIR, "hackeroos_events.json")


def normalise_date(raw: str) -> str:
    """Light normaliser – just strips and returns a single-spaced string."""
    if not raw:
        return ""
    return " ".join(raw.split())


def make_event(
    *,
    title: str,
    url: str,
    start_date: str = "",
    location: str = "",
    source: str,
) -> Dict[str, Any]:
    return {
        "title": title.strip() if title else "",
        "url": url.strip() if url else "",
        "start_date": normalise_date(start_date),
        "location": location.strip() if location else "",
        "source": source,
    }


# -------------------------------------------------
# 0) HACKEROOS (live scrape from hackeroos.com.au)
# -------------------------------------------------

# Matches "September 8th - 28th, 2026", "March 2nd, 2026", "Oct 8-28, 2026", etc.
_DATE_RE = re.compile(
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?"
    r"|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"[\w\s,\-–]+\d{4}",
    re.I,
)


def _extract_hackeroos_events(html: str) -> List[Dict[str, Any]]:
    """Parse Hackeroos event blocks out of rendered page HTML."""
    soup = BeautifulSoup(html, "html.parser")
    events: List[Dict[str, Any]] = []

    # Locate the #whats-on section (try id, then heading text)
    section = soup.find(id="whats-on") or soup.find(id="whatson")
    if not section:
        for tag in soup.find_all(["h1", "h2", "h3"]):
            if re.search(r"what.?s\s+on", tag.get_text(), re.I):
                section = tag.find_parent(["section", "div", "main"]) or tag.parent
                break
    root = section if section else soup

    # Skip these noise headings
    _SKIP = re.compile(r"^(what.?s\s+on|events?|upcoming|hackeroos|contact|about)$", re.I)

    seen_titles: set = set()

    for heading in root.find_all(["h2", "h3", "h4"]):
        title = heading.get_text(strip=True)
        if not title or len(title) < 4 or _SKIP.match(title) or title in seen_titles:
            continue
        seen_titles.add(title)

        # Grab the nearest enclosing block for context (date, link, location)
        container = (
            heading.find_parent(["article", "li", "div"])
            or heading.parent
        )
        text_block = container.get_text(" ", strip=True)

        # Date
        dm = _DATE_RE.search(text_block)
        start_date = dm.group(0).strip() if dm else ""

        # Link — prefer the first <a> with an external href
        link_tag = container.find("a", href=re.compile(r"https?://"))
        if not link_tag:
            link_tag = container.find("a", href=True)
        event_url = (
            link_tag["href"]
            if link_tag and link_tag.get("href")
            else "https://www.hackeroos.com.au/#whats-on"
        )

        # Location / mode
        location = "Online / TBA"
        mode = None
        lower_text = text_block.lower()
        if "in-person" in lower_text or "in person" in lower_text:
            location = "In-Person"
            mode = "In-Person"
        elif any(kw in lower_text for kw in ("online", "remote", "virtual")):
            location = "Online"
            mode = "Online"
        # Try to find city name
        city_m = re.search(r"\b(Sydney|Melbourne|Brisbane|Perth|Adelaide|Canberra)\b", text_block, re.I)
        if city_m:
            location = f"{city_m.group(0)}, Australia"

        ev = make_event(
            title=title,
            url=event_url,
            start_date=start_date,
            location=location,
            source="Hackeroos",
        )
        if mode:
            ev["mode"] = mode

        events.append(ev)

    return events


def scrape_hackeroos_website() -> List[Dict[str, Any]]:
    """
    Scrape live Hackeroos events from hackeroos.com.au using SeleniumBase
    (needed because the site is React-rendered).
    Falls back to the cached hackeroos_events.json if the scrape fails.
    """
    url = "https://hackeroos.com.au/"
    try:
        with SB(uc=True, headless=True) as sb:
            sb.open(url)
            sb.sleep(5)
            html = sb.get_page_source()
    except Exception as e:
        print(f"[Hackeroos] SeleniumBase failed: {e} — falling back to cached JSON")
        return load_hackeroos_events_cache()

    events = _extract_hackeroos_events(html)

    if not events:
        print("[Hackeroos] Website scrape returned no events — falling back to cached JSON")
        return load_hackeroos_events_cache()

    print(f"[Hackeroos] Scraped {len(events)} events from hackeroos.com.au")

    # Persist as cache so the fallback stays fresh
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(HACKEROOS_INPUT, "w", encoding="utf-8") as f:
            json.dump(events, f, indent=2, ensure_ascii=False)
        print(f"[Hackeroos] Cache updated at {HACKEROOS_INPUT}")
    except Exception as e:
        print(f"[Hackeroos] Could not update cache: {e}")

    return events


def load_hackeroos_events_cache() -> List[Dict[str, Any]]:
    """Load the cached hackeroos_events.json as a fallback."""
    events: List[Dict[str, Any]] = []

    if not os.path.exists(HACKEROOS_INPUT):
        print(f"[Hackeroos] Cache file not found at {HACKEROOS_INPUT} (skipping).")
        return events

    try:
        with open(HACKEROOS_INPUT, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[Hackeroos] Failed to read cache: {e}")
        return events

    if not isinstance(data, list):
        print(f"[Hackeroos] Cache JSON is not a list (skipping).")
        return events

    for raw in data:
        if not isinstance(raw, dict):
            continue
        title = raw.get("title") or "Untitled Hackeroos Event"
        url = raw.get("url") or "https://www.hackeroos.com.au/#whats-on"
        start_date = raw.get("start_date") or raw.get("startDate") or ""
        location = raw.get("location") or "Hackeroos / TBA"
        source = raw.get("source") or "Hackeroos"
        ev = make_event(title=title, url=url, start_date=start_date, location=location, source=source)
        for extra_key in ["end_date", "mode", "tags", "description"]:
            if extra_key in raw:
                ev[extra_key] = raw[extra_key]
        events.append(ev)

    print(f"[Hackeroos] Loaded {len(events)} events from cache {HACKEROOS_INPUT}")
    return events


# -------------------------------------------------
# 1) DEVPOST — use JSON API instead of HTML scraping
# -------------------------------------------------

def scrape_devpost() -> List[Dict]:
    """
    Fetch upcoming Devpost hackathons via their public JSON API.
    This is much more stable than scraping HTML.
    """
    base_url = "https://devpost.com/api/hackathons"
    events: List[Dict] = []

    # Safety: don't hammer them, just grab a few pages max.
    max_pages = 3
    params = {
        "status": "upcoming",
        "challenge_type": "all",
        "per_page": 50,
    }
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; PikaBotHackeroos/1.0; "
            "+https://github.com/aadarsh1282/pika-bot)"
        )
    }

    for page in range(1, max_pages + 1):
        qp = dict(params)
        qp["page"] = page

        try:
            resp = httpx.get(base_url, params=qp, headers=headers, timeout=15.0)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"[Devpost] Error on page {page}: {e}")
            break

        hacks = data.get("hackathons") or []
        if not hacks:
            break

        for h in hacks:
            title = h.get("title") or ""
            url = h.get("url") or ""
            loc_obj = h.get("displayed_location") or {}
            location = loc_obj.get("location") or "Online / TBA"
            # e.g. "Oct 31 - Dec 05, 2025"
            start_str = h.get("submission_period_dates") or ""

            events.append(
                make_event(
                    title=title,
                    url=url,
                    start_date=start_str,
                    location=location,
                    source="Devpost",
                )
            )

    print(f"[Devpost] Collected {len(events)} events from API")
    return events


# -------------------------------------------------
# 2) MLH — parsed date + location + mode
# -------------------------------------------------

def scrape_mlh() -> List[Dict]:
    """
    Scrape upcoming MLH hackathons from the events page.

    We parse:
      - name  (clean event name)
      - start_date (e.g. "Feb 14th - 15th, 2026")
      - location (e.g. "Raleigh , North Carolina" or "Everywhere , Online")
      - mode (e.g. "In-Person Only", "Online Digital Only")
    """
    url = "https://mlh.io/events"
    events: List[Dict] = []

    with SB(uc=True, headless=True) as sb:
        sb.open(url)
        sb.sleep(4)
        html = sb.get_page_source()

    soup = BeautifulSoup(html, "html.parser")

    # "Upcoming Events" header
    upcoming_header = soup.find(
        lambda tag: tag.name in ["h2", "h3"] and "Upcoming Events" in tag.get_text()
    )
    if not upcoming_header:
        print("[MLH] Could not find 'Upcoming Events' header")
        return events

    # Walk links until "Past Events"
    current = upcoming_header
    while current:
        current = current.find_next(["a", "h2", "h3"])
        if not current:
            break

        if current.name in ["h2", "h3"] and "Past Events" in current.get_text():
            break

        if current.name == "a" and current.has_attr("href"):
            text = current.get_text(" ", strip=True)
            href = current["href"]

            if not text or "Upcoming Events" in text:
                continue

            if not href.startswith("http"):
                href = f"https://mlh.io{href}"

            full_text = text

            # ---------------------------
            # 1) Find date segment
            # ---------------------------
            date_match = re.search(
                r"([A-Za-z]{3,9}\s+\d{1,2}(?:st|nd|rd|th)?"
                r"(?:\s*-\s*\d{1,2}(?:st|nd|rd|th)?)?"
                r"(?:,\s*\d{4})?)",
                full_text,
            )

            start_date = ""
            location = ""
            mode: str | None = None
            name = full_text  # fallback if regex fails

            mode_keywords = [
                "In-Person Only",
                "In-person Only",
                "Online Digital Only",
                "Digital Only",
                "Online Only",
                "Hybrid",
            ]

            if date_match:
                # the bit before the date is (usually) the name
                name = full_text[:date_match.start()].rstrip(" -–,")
                start_date = date_match.group(1).strip()

                # part after the date contains location + mode
                after = full_text[date_match.end():].strip()
                lower_after = after.lower()

                cut_idx = None
                found_mode = None
                for kw in mode_keywords:
                    pos = lower_after.find(kw.lower())
                    if pos != -1:
                        cut_idx = pos
                        found_mode = kw
                        break

                if cut_idx is not None:
                    loc_part = after[:cut_idx].strip()
                    mode = found_mode
                else:
                    loc_part = after

                # clean up spacing like " ,"
                location = loc_part.replace(" ,", ",").strip()
            else:
                # no date found; keep full_text as name
                name = full_text

            ev = make_event(
                title=name,
                url=href,
                start_date=start_date,
                location=location,
                source="MLH",
            )
            if mode:
                ev["mode"] = mode

            events.append(ev)

    print(f"[MLH] Collected {len(events)} events (with parsed dates/locations)")
    return events


# -------------------------------------------------
# 3) Lu.ma (hackathon tag)
# -------------------------------------------------

def scrape_luma() -> List[Dict]:
    """Scrape Lu.ma hackathons via the 'hackathon' tag page."""
    url = "https://lu.ma/tag/hackathon"
    events: List[Dict] = []

    with SB(uc=True, headless=True) as sb:
        sb.open(url)
        sb.sleep(5)  # Lu.ma is more JS-heavy
        html = sb.get_page_source()

    soup = BeautifulSoup(html, "html.parser")

    cards = soup.select("a[href*='/event/'], a[href*='lu.ma/']")

    for link in cards:
        href = link.get("href")
        if not href:
            continue

        # skip tag links
        if "/tag/" in href:
            continue

        title = link.get_text(" ", strip=True)
        if not title:
            continue

        if href.startswith("/"):
            href = f"https://lu.ma{href}"

        start_date = ""
        location = ""

        events.append(
            make_event(
                title=title,
                url=href,
                start_date=start_date,
                location=location,
                source="Lu.ma",
            )
        )

    print(f"[Lu.ma] Collected {len(events)} events")
    return events


# -------------------------------------------------
# 4) Hack Club Events
# -------------------------------------------------

def scrape_hackclub() -> List[Dict]:
    """Scrape Hack Club events page."""
    url = "https://events.hackclub.com"
    events: List[Dict] = []

    with SB(uc=True, headless=True) as sb:
        sb.open(url)
        sb.sleep(4)
        html = sb.get_page_source()

    soup = BeautifulSoup(html, "html.parser")

    cards = soup.select("a[href*='events.hackclub.com/event'], a[href*='/event/']")

    for link in cards:
        href = link.get("href")
        if not href:
            continue

        title = link.get_text(" ", strip=True)
        if not title:
            continue

        if href.startswith("/"):
            href = f"https://events.hackclub.com{href}"

        start_date = ""
        location = ""

        events.append(
            make_event(
                title=title,
                url=href,
                start_date=start_date,
                location=location,
                source="Hack Club",
            )
        )

    print(f"[Hack Club] Collected {len(events)} events")
    return events


# -------------------------------------------------
# MERGE + SAVE
# -------------------------------------------------

def merge_and_dedupe(all_lists: List[List[Dict]]) -> List[Dict]:
    """Merge lists and dedupe by URL."""
    seen = set()
    merged: List[Dict] = []

    for lst in all_lists:
        for item in lst:
            url = (item.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            merged.append(item)

    # sort by (source, title) just to keep it tidy; /hackathons will re-sort by date
    merged.sort(key=lambda x: (x.get("source", ""), x.get("title", "").lower()))
    return merged


def main():
    print("🔎 Scraping hackathons from multiple sources...")

    hackeroos_events = scrape_hackeroos_website()
    devpost_events = scrape_devpost()
    mlh_events = scrape_mlh()
    luma_events = scrape_luma()
    hackclub_events = scrape_hackclub()

    print(f"Hackeroos: {len(hackeroos_events)} events")
    print(f"Devpost:   {len(devpost_events)} events")
    print(f"MLH:       {len(mlh_events)} events")
    print(f"Lu.ma:     {len(luma_events)} events")
    print(f"Hack Club: {len(hackclub_events)} events")

    merged = merge_and_dedupe(
        [hackeroos_events, devpost_events, mlh_events, luma_events, hackclub_events]
    )
    print(f"Total after merge/dedupe: {len(merged)} events")

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)

    print(f"✅ Saved to {OUTPUT_PATH} at {datetime.utcnow().isoformat()}Z")


if __name__ == "__main__":
    main()
