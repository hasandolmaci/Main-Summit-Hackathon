"""Point-in-time patent searches using only the Python standard library.

Google totals are search-result counts (its default family grouping), not a
deduplicated legal application register. Each name variant is searched separately;
use the larger total, never their sum. Dates and matched assignees are retained in
evidence. Publication must also predate the cutoff to avoid hindsight leakage.
Blocked/failed searches, ambiguous response schemas, and short names stay blank.
"""

import concurrent.futures
import csv
import datetime as dt
import hashlib
import html
import json
from pathlib import Path
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "raw/cache/m_patents"
FIELDS = ["name", "website", "asof_date", "patent_applications_before",
          "patent_first_priority_date", "patent_evidence", "patent_score"]
LOCK = threading.Lock()
HOST_LOCKS = {}
LAST_REQUEST = {}
BLOCKS = {}
STATS = {"http": 0, "cache": 0, "failed": 0, "blocked_host_skips": 0}


def access_blocked(status, body):
    text = body[:10000].lower()
    return status in (401, 403, 429) or any(marker in text for marker in (
        b"sending automated queries", b"unusual traffic", b"<title>just a moment"))


def fetch(url):
    """Cache body (base64), status, headers, and URL for every HTTP response.

    After repeated access-denied responses (including cached blocks), stop hitting
    that host for this run. Skipped requests are failures, never negative results.
    """
    import base64
    key = hashlib.sha1(url.encode()).hexdigest()
    path = CACHE / key
    host = urllib.parse.urlsplit(url).netloc
    if path.exists():
        record = json.loads(path.read_text(encoding="utf-8"))
        body = base64.b64decode(record["body_base64"])
        blocked = access_blocked(record["status"], body)
        with LOCK:
            STATS["cache"] += 1
            if blocked:
                BLOCKS[host] = BLOCKS.get(host, 0) + 1
        if record["status"] == 200 and not blocked:
            return body
        return None
    with LOCK:
        host_lock = HOST_LOCKS.setdefault(host, threading.Lock())
    for attempt in range(2):
        with host_lock:
            if BLOCKS.get(host, 0) >= 3:
                with LOCK:
                    STATS["blocked_host_skips"] += 1
                return None
            time.sleep(max(0, 1.1 - (time.monotonic() - LAST_REQUEST.get(host, 0))))
            LAST_REQUEST[host] = time.monotonic()
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (research script)",
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://" + host + "/",
            })
            try:
                try:
                    response = urllib.request.urlopen(req, timeout=25)
                except urllib.error.HTTPError as exc:
                    response = exc
                with response:
                    body = response.read()
                    status = response.code
                    record = {"url": url, "status": status,
                              "retrieved_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                              "headers": dict(response.headers.items()),
                              "body_base64": base64.b64encode(body).decode("ascii")}
                # Retries also retain every response, while the URL hash is the
                # canonical response used on a subsequent run.
                if path.exists():
                    previous = path.with_name(key + ".attempt1")
                    if not previous.exists():
                        previous.write_bytes(path.read_bytes())
                path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
                with LOCK:
                    STATS["http"] += 1
                blocked = access_blocked(status, body)
                BLOCKS[host] = BLOCKS.get(host, 0) + 1 if blocked else 0
                if status == 200 and not blocked:
                    return body
                if blocked and status != 429:
                    return None
                if status not in (429, 500, 502, 503, 504):
                    return None
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                with LOCK:
                    STATS["failed"] += 1
                # No HTTP response exists for a transport error.
                print(f"Transport failure ({host}): {type(exc).__name__}", flush=True)
        if attempt == 0:
            time.sleep(3)
    return None


def clean_name(name):
    name = re.sub(r"\b(?:gmbh|ug|ag|haftungsbeschr(?:ae|ä)nkt)\b", " ", name, flags=re.I)
    return re.sub(r"\s+", " ", name).strip(" ,().-\t")


def plain(value):
    return html.unescape(re.sub(r"<[^>]*>", "", str(value or ""))).strip()


def date_value(value):
    text = str(value or "")
    match = re.search(r"(\d{4})[-/]?(\d{2})[-/]?(\d{2})", text)
    if not match:
        return ""
    try:
        return dt.date(*map(int, match.groups())).isoformat()
    except ValueError:
        return ""


def normalized(value):
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower())


def total_value(value):
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    if isinstance(value, str) and re.fullmatch(r"\d[\d, ]*", value):
        return int(re.sub(r"[, ]", "", value))
    return None


def parse_google(body, name, cutoff, url):
    data = json.loads(body)
    results = data.get("results", {})
    total = total_value(results.get("total_num_results"))
    if total is None:
        return None
    if total == 0:
        return (0, "", "Google Patents; no results; " + url)
    entries = [entry.get("patent", {}) for cluster in results.get("cluster", [])
               for entry in cluster.get("result", [])]
    if not entries:
        return None
    first = entries[0]
    priority = date_value(first.get("priority_date"))
    publication_date = date_value(first.get("publication_date"))
    assignee = plain(first.get("assignee"))
    publication = plain(first.get("publication_number"))
    # Substring matches can still be common-name collisions; preserve the actual
    # assignee for review. Reject outright mismatches instead of labelling zero.
    if (not priority or not publication_date or priority >= cutoff or
            publication_date >= cutoff or not assignee or not publication or
            normalized(name) not in normalized(assignee)):
        return None
    evidence = (f"assignee={assignee}; publication={publication}; "
                f"publication_date={publication_date}; priority_date={priority}; {url}")
    return total, priority, evidence


def parse_espacenet(body, name, cutoff, url):
    """Accept only explicit totals and structured patent records, never HTML."""
    data = json.loads(body)
    if not isinstance(data, dict):
        return None
    total = total_value(data.get("totalResults", data.get("total_num_results")))
    if total is None:
        return None
    if total == 0:
        return 0, "", "Espacenet; no results; " + url
    records = data.get("results", [])
    if not isinstance(records, list) or not records or not isinstance(records[0], dict):
        return None
    first = records[0]
    priority = date_value(first.get("priorityDate"))
    published = date_value(first.get("publicationDate"))
    assignee = plain(first.get("applicant", first.get("assignee")))
    publication = plain(first.get("publicationNumber"))
    if (not priority or not published or priority >= cutoff or published >= cutoff
            or not publication or normalized(name) not in normalized(assignee)):
        return None
    # The fallback request is not date-filtered, so a global total cannot be used.
    # Only a complete single-record response has a provable historic count here.
    if total != 1:
        return None
    return 1, priority, f"assignee={assignee}; publication={publication}; publication_date={published}; {url}"


def process(row):
    output = {field: row.get(field, "") if field in FIELDS[:3] else "" for field in FIELDS}
    name = clean_name(row["name"])
    if len(name) < 4:
        return output
    try:
        cutoff = dt.date.fromisoformat(row["asof_date"]).isoformat()
    except ValueError:
        return output
    compact_date = cutoff.replace("-", "")
    variants = list(dict.fromkeys([name, re.sub(r"\s+", "", name)]))
    found = []
    for variant in variants:
        query = urllib.parse.urlencode({"assignee": variant}) + "&before=priority:" + compact_date + "&before=publication:" + compact_date
        url = "https://patents.google.com/xhr/query?url=" + urllib.parse.quote(query, safe="") + "&exp="
        body = fetch(url)
        if body:
            try:
                result = parse_google(body, name, cutoff, url)
                if result is not None:
                    found.append(result)
            except (ValueError, TypeError, AttributeError, KeyError):
                pass
    # A positive variant suffices; negatives require every variant to succeed.
    positives = [result for result in found if result[0] > 0]
    result = max(positives, key=lambda item: item[0]) if positives else None
    if not result and len(found) == len(variants):
        result = found[0]
    if result is None:
        url = "https://worldwide.espacenet.com/3.2/rest-services/search?q=" + urllib.parse.quote(f'pa="{name}"', safe="")
        body = fetch(url)
        if body:
            try:
                result = parse_espacenet(body, name, cutoff, url)
            except (ValueError, TypeError, AttributeError, KeyError):
                pass
    if result is not None:
        output.update(patent_applications_before=result[0], patent_first_priority_date=result[1],
                      patent_evidence=result[2], patent_score=int(result[0] > 0))
    return output


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    with (ROOT / "raw/cohort_short.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    output = [None] * len(rows)
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(process, row): index for index, row in enumerate(rows)}
        for number, future in enumerate(concurrent.futures.as_completed(futures), 1):
            index = futures[future]
            output[index] = future.result()
            if number % 20 == 0 or number == len(rows):
                print(f"Processed {number}/{len(rows)}; {STATS}", flush=True)
    destination = ROOT / "raw/m_patents.csv"
    with destination.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(output)
    print(f"\nWrote {len(output)} rows to {destination}")
    for field in FIELDS:
        filled = sum(row[field] != "" for row in output)
        print(f"{field}: fill {filled}/{len(output)} ({filled / len(output):.1%}); missing {1 - filled / len(output):.1%}")
    print("\n5 example rows:")
    for row in output[:5]:
        print(json.dumps(row, ensure_ascii=False))
    print("HTTP/cache summary:", STATS)
    print("Host access-denied counters:", BLOCKS)


if __name__ == "__main__":
    main()
