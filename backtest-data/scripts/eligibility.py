"""Deterministic eligibility gates; no learned model weights are used.

As-of mode deliberately ignores current equity, portfolio and death indicators.
The portfolio and alive gates pass as exemptions in that mode. Missing evidence
otherwise stays unknown; employee count is a review flag, never a hard gate.
"""

from __future__ import annotations

import csv
import json
import math
import re
from itertools import islice
from pathlib import Path


GERMAN_PROGRAMMES = frozenset({
    "unternehmertum_prototyping_grant", "falling_walls", "baystartup",
    "science4life", "spinlab_leipzig",
})
_NONCOMMERCIAL = re.compile(
    r"\be\s*\.\s*v\s*\.|\bggmbh\b|\bsailing\s+team\b|\binitiative\b",
    re.IGNORECASE,
)
_NOISE = re.compile(
    r"\b(?:hobby project|student assignment|student project|"
    r"company[- ]less open[- ]source)\b", re.IGNORECASE,
)


def _text(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.casefold() in {"", "nan", "none", "null", "n/a"} else text


def _number(value: object) -> float | None:
    try:
        number = float(_text(value))
    except (ValueError, TypeError, OverflowError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def evaluate(row: dict, sector: str | None, asof_view: bool = True) -> dict:
    """Return score, status, reasons, review flags and all eleven gate states.

    Gate states are ``pass``, ``fail`` or ``unknown``. Reasons explain failed
    and unknown gates (and historical exemptions). Blank ownership evidence
    cannot establish independence. Known sector categories other than
    non_tech pass; a missing/unclear sector remains unknown.

    In as-of mode only FUNDED_BEFORE and EXCLUDE_NOT_FIRST fail funding;
    FUNDED_WITHIN, FUNDED_LATER and CB_ROUND_NO_DATE explicitly pass.
    Other exclusion labels remain unknown in this mode.
    """
    gates: dict[str, str] = {}
    reasons: list[str] = []
    review_flags: list[str] = []

    def gate(name: str, state: str, reason: str = "") -> None:
        gates[name] = state
        if reason:
            reasons.append(f"{name}: {reason}")

    funding = _text(row.get("y_funding_status")).upper()
    equity = _text(row.get("cb_has_equity_round")) == "1"
    if asof_view:
        if funding in {"FUNDED_BEFORE", "EXCLUDE_NOT_FIRST"}:
            gate("not_funded", "fail", f"pre-as-of funding ({funding})")
        elif funding in {"FUNDED_WITHIN", "FUNDED_LATER", "CB_ROUND_NO_DATE"}:
            gate("not_funded", "pass", f"{funding} treated as after-as-of funding")
        elif funding == "NO_FUNDING_FOUND":
            gate("not_funded", "pass")
        else:
            gate("not_funded", "unknown", "as-of funding not established")
    elif equity or funding in {
        "FUNDED_WITHIN", "FUNDED_LATER", "FUNDED_BEFORE", "CB_ROUND_NO_DATE",
    } or funding.startswith("EXCLUDE_"):
        gate("not_funded", "fail", f"funding/exclusion evidence ({funding or 'equity round'})")
    elif funding == "NO_FUNDING_FOUND":
        gate("not_funded", "pass")
    else:
        gate("not_funded", "unknown", "funding status missing or unknown")

    for name, field in (
        ("not_in_htgf_portfolio", "info_e_in_htgf_portfolio"),
        ("alive", "y_dead"),
    ):
        value = _text(row.get(field))
        if asof_view:
            gate(name, "pass", "current indicator not applied in as-of view")
        elif value == "1":
            gate(name, "fail", f"{field}=1")
        elif value == "0":
            gate(name, "pass")
        else:
            gate(name, "unknown", f"{field} missing or unknown")

    source = _text(row.get("meta_cohort_source")).casefold()
    if source in GERMAN_PROGRAMMES:
        gate("german", "pass")
    else:
        gate("german", "unknown", "source does not establish a German operating base")

    parent = _text(row.get("s_parent_company_mentioned"))
    if parent:
        gate("independent", "fail", f"parent company mentioned: {parent}")
    else:
        gate("independent", "unknown", "ownership not established")

    identity = " ".join(_text(row.get(key)) for key in ("id_name", "s_legal_form"))
    if _NONCOMMERCIAL.search(identity):
        gate("commercial", "fail", "noncommercial name or legal form")
    elif identity.strip():
        gate("commercial", "pass")
    else:
        gate("commercial", "unknown", "name and legal form missing")

    category = _text(sector).casefold()
    if category == "non_tech":
        gate("investable_sector", "fail", "sector is non_tech")
    elif category and category != "unclear":
        gate("investable_sector", "pass")
    else:
        gate("investable_sector", "unknown", "sector missing or unclear")

    # Only explicit historical descriptions provide noise evidence. No current
    # activity, popularity or account metrics are used as historical proxies.
    description = _text(row.get("f_description_text"))
    noise = _NOISE.search(description)
    if noise:
        gate("not_noise", "fail", f"explicit noise signal: {noise.group(0)}")
    else:
        gate("not_noise", "unknown", "no conclusive noise assessment")

    age = _number(row.get("f_age_months"))
    if age is None:
        gate("age_le_3y", "unknown", "age missing or invalid")
    elif age > 36:
        # f_age_months is website/domain archive age, not company age: a domain can
        # long predate the startup. An old domain needs review, it is not proof of failure.
        gate("age_le_3y", "unknown", f"website age {age:g} months exceeds 36 (domain age is not company age)")
    else:
        gate("age_le_3y", "pass")

    gate("active_90d", "unknown", "activity within 90 days not verified")
    gate("not_faked", "unknown", "authenticity not verified")
    people = _number(row.get("info_li_employees_now"))
    if people is not None and people > 25:
        review_flags.append("more_than_25_people")

    passes = sum(state == "pass" for state in gates.values())
    unknowns = sum(state == "unknown" for state in gates.values())
    if "fail" in gates.values():
        score = 0.0
        status = "INELIGIBLE"
    else:
        score = 100.0 * (passes + 0.5 * unknowns) / len(gates)
        status = "ELIGIBLE_REVIEW" if review_flags or unknowns > 4 else "ELIGIBLE"
    return {
        "eligibility_score": score,
        "status": status,
        "reasons": reasons,
        "review_flags": review_flags,
        "gates": gates,
    }


def _self_test() -> None:
    """Evaluate and print the first five backtest rows, using sector matches."""
    root = Path(__file__).resolve().parents[1]
    sectors: dict[tuple[str, str], str] = {}
    with (root / "raw" / "relevance_v2.csv").open(encoding="utf-8-sig", newline="") as stream:
        for item in csv.DictReader(stream):
            key = (_text(item.get("source")).casefold(), _text(item.get("name")).casefold())
            sectors[key] = item["category"]
    with (root / "backtest.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(islice(csv.DictReader(stream), 5))
    assert len(rows) == 5, "Self-test requires five backtest rows"
    for row in rows:
        key = (_text(row.get("meta_cohort_source")).casefold(), _text(row.get("id_name")).casefold())
        result = evaluate(row, sectors.get(key))
        assert 0 <= result["eligibility_score"] <= 100
        assert len(result["gates"]) == 11
        assert set(result["gates"].values()) <= {"pass", "fail", "unknown"}
        assert result["status"] in {"ELIGIBLE", "ELIGIBLE_REVIEW", "INELIGIBLE"}
        if "fail" in result["gates"].values():
            assert result["status"] == "INELIGIBLE" and result["eligibility_score"] == 0
        print(json.dumps({"id_company": row["id_company"], **result}, ensure_ascii=False))


if __name__ == "__main__":
    _self_test()
