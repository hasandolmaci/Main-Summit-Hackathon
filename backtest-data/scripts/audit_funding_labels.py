"""Reproduce the manual, quote-only audit of funded cohort rows (stdlib only).

First-round status is 1 for explicit seed/pre-seed evidence, 0 for explicit
prior institutional funding, and 'unknown' when the quote is insufficient.
An investor's name, company age, or amount alone does not prove round order.
date_consistent=1 means the quote describes a current round and contains no
contradictory timing: the supplied date is plausible, NOT independently verified.
Undated quotes cannot establish an exact announcement date. An acquisition
has date_consistent=0 because it is not the labeled company's funding round.
Confidence concerns the audit disposition, not independent date verification.
URLs and round metadata were read as context; no linked pages were fetched.
"""

import csv
from collections import Counter
from pathlib import Path


# name: acquisition, first institutional, date plausible, confidence, status, note
DECISIONS = {
    "caire": (0, "unknown", 1, "medium", "REVIEW", "Founding in 2023 postdates asof; first-round status unstated."),
    "CyberDesk": (0, "unknown", 1, "low", "REVIEW", "Named investors and current ownership do not establish first institutional round."),
    "deltaVision": (0, "unknown", 1, "low", "REVIEW", "Profitable company with 125 employees; quote does not establish round order."),
    "mo:re": (0, 1, 1, "medium", "keep", "Explicit seed financing; likely first institutional round; exact date unverified."),
    "CedarDB": (0, "unknown", 1, "low", "REVIEW", "Investment described, but no stage or prior-round history establishes first institutional funding."),
    "CinSOIL": (0, 1, 1, "medium", "keep", "Explicit pre-seed funding; likely first institutional round; exact date unverified."),
    "Otter Energy Trading": (0, "unknown", 1, "low", "REVIEW", "Investor participation is explicit; first institutional round is not established."),
    "Qualiwise": (0, "unknown", 1, "low", "REVIEW", "Six-figure investment described without stage or prior funding history."),
    "ESG-X": (1, "unknown", 0, "high", "EXCLUDE_ACQUISITION", "Dcycle acquires ESG-X; venture funding mentioned belongs to Dcycle."),
    "Radical Dot": (0, "unknown", 1, "medium", "REVIEW", "Young company funding a prototype; first institutional round remains unstated."),
    "Emidat": (0, "unknown", 1, "medium", "REVIEW", "Young company receives investment; quote does not identify first institutional round."),
    "Hula Earth": (0, "unknown", 1, "low", "REVIEW", "Investment and investors identified; round stage and funding history absent."),
    "Seal Robotics": (0, 1, 1, "medium", "keep", "Explicit pre-seed round; likely first institutional funding; exact date unverified."),
    "Qoro Quantum": (0, 1, 1, "medium", "keep", "Explicit pre-seed round; likely first institutional funding; exact date unverified."),
    "Valuemize": (0, 1, 1, "medium", "keep", "Explicit pre-seed financing; likely first institutional round; exact date unverified."),
    "Q.ANT": (0, "unknown", 1, "medium", "REVIEW", "2018 corporate spin-off; large investment alone does not establish prior institutional rounds."),
    "GEMESYS": (0, 1, 1, "medium", "keep", "Quote explicitly identifies EUR8.6M pre-seed; amount alone does not contradict stage."),
    "smartbax": (0, "unknown", 1, "low", "REVIEW", "Headline identifies investment but provides neither stage nor earlier funding history."),
    "Orbem": (0, "unknown", 1, "low", "REVIEW", "EUR30M includes debt and equity; quote provides no stage or prior-round history."),
    "Sedivention": (0, "unknown", 1, "medium", "REVIEW", "HTGF investment funds development after prototype; first institutional round not explicit."),
    "AUDAVIS": (0, "unknown", 1, "low", "REVIEW", "HTGF and private investment identified; quote does not establish round order."),
    "InContAlert": (0, "unknown", 1, "low", "REVIEW", "Institutional investors identified; quote does not establish first institutional round."),
    "MEMENTOR": (0, "unknown", 1, "low", "REVIEW", "Quote gives neither founding date nor prior-round history; first-round status unknown."),
    "MOTIONSCLOUD": (0, "unknown", 1, "low", "REVIEW", "Multiple institutional investors listed; participation does not establish first-round status."),
    "DEFTPOWER": (0, "unknown", 1, "medium", "REVIEW", "New growth capital suggests follow-on; earlier institutional funding is not explicit."),
    "ENADYNE": (0, "unknown", 1, "low", "REVIEW", "EUR7M funding headline lacks stage and earlier institutional funding history."),
    "IO-DYNAMICS": (0, "unknown", 1, "low", "REVIEW", "Founded 2018; company age and investor names do not establish round order."),
    "MEMODIO": (0, "unknown", 1, "low", "REVIEW", "Seed appears in investor name only; quote does not explicitly identify round stage."),
    "SUENA": (0, "unknown", 1, "medium", "REVIEW", "Young company funds development and pilots; first institutional round remains unstated."),
    "Reonic": (0, 0, 1, "high", "EXCLUDE_NOT_FIRST", "Point Nine and Puzzle Ventures previously invested EUR3M; EUR13M is a follow-on."),
    "Neology": (0, "unknown", 1, "low", "REVIEW", "Investment-round headline lacks stage, investors, and earlier funding history."),
    "retoflow": (0, "unknown", 1, "low", "REVIEW", "Investment in Fraunhofer spin-off identified; quote does not establish first-round status."),
    "zentur.io": (0, "unknown", 1, "low", "REVIEW", "Quote gives neither founding date nor prior-round history; first-round status unknown."),
}

FIELDS = [
    "name", "asof_date", "funding_status", "is_acquisition",
    "is_first_institutional", "date_consistent", "confidence",
    "corrected_status", "note",
]


def main():
    root = Path(__file__).resolve().parents[1]
    with (root / "raw/labels_cohort_short.csv").open(
        encoding="utf-8-sig", newline=""
    ) as source:
        rows = [r for r in csv.DictReader(source)
                if r["funding_status"] in {"FUNDED_WITHIN", "FUNDED_LATER"}]
    names = [r["name"] for r in rows]
    if len(names) != len(set(names)) or set(names) != set(DECISIONS):
        raise ValueError("Funded rows changed; manually review decisions before writing.")

    audited = []
    for row in rows:
        decision = DECISIONS[row["name"]]
        if len(decision[-1].split()) > 15:
            raise ValueError("Note exceeds 15 words: " + row["name"])
        audited.append(dict(zip(FIELDS, (
            row["name"], row["asof_date"], row["funding_status"], *decision
        ))))

    with (root / "raw/label_audit.csv").open(
        "w", encoding="utf-8", newline=""
    ) as output:
        writer = csv.DictWriter(output, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(audited)

    counts = Counter(r["corrected_status"] for r in audited)
    print("Audited rows:", len(audited))
    for status in ("keep", "EXCLUDE_ACQUISITION", "EXCLUDE_NOT_FIRST", "REVIEW"):
        print(f"{status}: {counts[status]}")


if __name__ == "__main__":
    main()
