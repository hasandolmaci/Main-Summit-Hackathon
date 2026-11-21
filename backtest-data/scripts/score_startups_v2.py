"""User-format merit scores, eligibility and frozen sector/age peer percentiles."""
from __future__ import annotations
import argparse
from collections import Counter
import csv
from datetime import datetime
import json
import math
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True
import eligibility
import peer_scoring

ROOT = Path(__file__).resolve().parents[1]
FIELDS = ['name', 'status', 'eligibility_score', 'ineligible_reasons', 'review_flags',
          'quality_0_100', 'quality_observed_0_100', 'opportunity_0_100', 'peer_group', 'peer_level',
          'coverage_collectable_pct', 'coverage_full_pct', 'ranked', 'call_priority',
          'top3_quality_signals', 'top3_opportunity_signals']
UNKNOWN = {'', 'unknown', 'none', 'null', 'nan', 'n/a', 'not checked', 'unchecked'}
ABSENT = {'0', 'false', 'no', 'absent', '[]', '{}'}
# Fixed caps independent of batch composition and funding outcomes.
ATTENTION_CAP, HEAD_START_CAP_DAYS = 100.0, 365.0


def read_csv(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def text(value):
    value = str(value if value is not None else '').strip()
    return '' if value.casefold() in UNKNOWN else value


def number(value):
    value = text(value)
    if value.casefold() in {'true', 'yes'}:
        return 1.0
    if value.casefold() in {'false', 'no'}:
        return 0.0
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) and result >= 0 else None


def binary(value):
    value = number(value)
    return None if value is None else float(value > 0)


def presence(value):
    value = text(value)
    return None if not value else float(value.casefold() not in ABSENT)


def clipped(value):
    value = number(value)
    return None if value is None else min(value, 1.0)


def maximum(values):
    observed = [v for v in values if v is not None]
    return max(observed) if observed else None


def date(value):
    for fmt in ('%d/%m/%Y', '%Y-%m-%d', '%Y-%m-%dT%H:%M:%S', '%Y%m%d%H%M%S', '%Y%m%d'):
        try:
            return datetime.strptime(text(value), fmt)
        except ValueError:
            pass
    return None


def capped_log(value, cap):
    return min(math.log1p(max(0.0, value)) / math.log1p(cap), 1.0)


def metric_value(metric, row):
    """Return 0..1, or None for insufficient evidence (zero merit, unobserved).

    Context columns alone do not establish observation. Compound proxies can
    be partially observed; missing components contribute zero. Text rubrics
    are conservative screening rules and require manual review.
    """
    name, columns = metric['metric'], metric['columns']
    if not columns or metric['transform'].startswith('uncollected'):
        return None
    n = lambda key: number(row.get(key))
    b = lambda key: binary(row.get(key))
    p = lambda key: presence(row.get(key))
    t = lambda key: text(row.get(key))
    description = t('f_description_text')
    if metric['transform'].startswith('founder_count_ge_2'):
        count = n(columns[0])
        return float(count >= 2) if count is not None and count >= 1 else None
    if name == 'Founder background':
        values = [clipped(row.get(key)) for key in columns]
        return sum(v or 0 for v in values) / len(values) if any(v is not None for v in values) else None
    if name.startswith('Technical substance'):
        return maximum([b('f_product_live'), b('m_gh_has_open_source'), b('m_gh_public_repos_before')])
    if name == 'Patents':
        return b('m_patent_verified_before_asof')
    if name.startswith('Product readiness'):
        cert, cutoff = date(row.get('f_first_cert_date')), date(row.get('meta_asof_date'))
        cert_value = float(cert <= cutoff) if cert and cutoff else None
        return maximum([b('f_product_live'), b('f_waitlist'), cert_value])
    if name.startswith('Experimentation tools'):
        return maximum([p(key) for key in columns])
    if name == 'Outside open-source contributions':
        return clipped(row.get('m_gh_external_score'))
    if name == 'Online attention / web mentions':
        news = n('e_news_mentions_before_12m')
        if news is None:
            news = n('e_news_mentions_before_total')
        hn = n('f_hn_posts_before')
        return capped_log((news or 0) + (hn or 0), ATTENTION_CAP) if news is not None or hn is not None else None
    if name == 'Launch-platform spread':
        values = [b(key) for key in columns]
        return sum(v or 0 for v in values) / 3 if any(v is not None for v in values) else None
    if name == 'Clear paying buyer and costly problem':
        buyer = re.search(r'\b(customers?|clients?|manufacturers?|hospitals?|clinics?|retailers?|production managers?|businesses|enterprises|companies)\b', description, re.I)
        problem = re.search(r'\b(reduc\w* costs?|sav\w* (?:time|money|costs?)|costly|downtime|inefficien\w*|shortage|manual processes|expensive)\b', description, re.I)
        return 1.0 if buyer and problem else None
    if name == 'Regulated or hardware market':
        return 1.0 if re.search(r'\b(hardware|medical devices?|medtech|clinical|pharma\w*|regulated|robot\w*|semiconductor\w*|sensors?)\b', description, re.I) else None
    if name == 'University spin-off origin':
        return 1.0 if re.search(r'(?:university|universit\w*|hochschule|tu munich|tum).{0,100}(?:spin[- ]?off|ausgr.ndung)|(?:spin[- ]?off|ausgr.ndung).{0,100}(?:university|universit\w*|hochschule|tu munich|tum)', description, re.I) else None
    if name.startswith('Programme validation'):
        return clipped(row.get('m_grant_score'))
    if name.startswith('Raise-soon score'):
        # Broad f_gmbh_ig includes ordinary GmbH/UG: require formation-stage text.
        formation_text = ' '.join(t(key) for key in ('f_description_text', 's_legal_form'))
        formation = 1.0 if re.search(r'\bi\s*\.\s*g\s*\.|in gr.ndung', formation_text, re.I) else None
        titles = t('s_job_titles')
        founder_job = 1.0 if re.search(r'founding[- ]engineer|gr.ndungsingenieur', titles, re.I) else (0.0 if titles else None)
        return maximum([formation, b('f_pricing_page'), founder_job])
    if name.startswith('Venture age'):
        age = n('f_age_months')
        return float(age <= 18) if age is not None else None
    if name.startswith('Head start in days'):
        cutoff, press = date(row.get('meta_asof_date')), date(row.get('e_news_first_mention_date'))
        starts = [date(row.get(key)) for key in ('f_first_capture', 'f_first_cert_date')]
        starts = [d for d in starts if d and cutoff and d <= cutoff]
        if not cutoff or not press or press > cutoff or not starts:
            return None
        return capped_log(max(0, (press - min(starts)).days), HEAD_START_CAP_DAYS)
    if name == 'Hackathon-to-company signal':
        # No formation dates: project + entity cannot establish subsequent formation.
        return 0.0 if n('e_devpost_projects_before') == 0 else None
    if name.startswith('Hiring mix'):
        engineering, sales = n('s_careers_engineering_roles'), n('s_careers_sales_roles')
        if engineering is None or sales is None:
            return None
        return engineering / (engineering + sales) if engineering + sales else 0.0
    if name == 'Own domain':
        # Assembled historical company-site proxy; never discovery lookup identifiers.
        domain = t('f_domain').lower().removeprefix('https://').removeprefix('http://').split('/')[0]
        if not domain:
            return None
        hosted = ('github.com', 'linkedin.com', 'notion.site', 'wixsite.com', 'wordpress.com')
        return float('.' in domain and not any(domain == h or domain.endswith('.' + h) for h in hosted))
    if name == 'Impressum with named founders':
        impressum, named = b('f_impressum'), b('s_founders_named_in_impressum')
        if impressum == 0 or named == 0:
            return 0.0
        return 1.0 if impressum == named == 1 else None
    if name == 'Legal entity':
        return p('f_legal_entity')
    if name == 'Pricing / pilot offer':
        pilot = bool(re.search(r'\b(pilot (?:offer|program\w*|project)|(?:book|request|start) a pilot)\b', description, re.I))
        return 1.0 if pilot else b('f_pricing_page')
    if name == 'Open roles':
        titles = p('s_job_titles')
        if titles is not None:
            return titles
        if b('f_jobs_page') == 0 or b('s_jobs_page_found') == 0:
            return 0.0
        return None  # Careers page alone is not an open role.
    if name.startswith('Thesis match'):
        thesis = re.search(r'\b(ai|artificial intelligence|machine learning|deep tech|industrial|robot\w*|semiconductor\w*|quantum)\b', description, re.I)
        product = re.search(r'\b(platform|software|saas|product|devices?|systems?)\b', description, re.I)
        return 1.0 if thesis and product else None
    if name == 'No contact or founder name (penalty)':
        contact, founder = b('s_contact_email_present'), p('s_founder_names')
        if contact == 0 or founder == 0:
            return 1.0
        return 0.0 if contact == founder == 1 else None
    if name == 'No legal entity yet (penalty)':
        entity = p('f_legal_entity')
        return 1 - entity if entity is not None else None
    if len(columns) == 1 and metric['transform'].startswith(('binary', 'score_0_1')):
        return b(columns[0]) if metric['transform'].startswith('binary') else clipped(row.get(columns[0]))
    raise ValueError(f'No transform implemented for metric: {name}')


def metric_weight(metric, model, prior):
    source = metric['weight_source']
    if source == 'default':
        return float(metric.get('default_weight', 1.0))
    pattern = r'by feature name:\s*(\w+)' if source == 'learned' else r'respecting model aliases:\s*(\w+)'
    match = re.search(pattern, metric['transform'])
    if not match:
        raise ValueError(f'Missing weight feature in {metric["metric"]}')
    aliases = model.get('feature_transforms', {}).get('prior_aliases', {})
    canonical = lambda key: aliases.get(key, key)
    weights = model['final_weights'] if source == 'learned' else {canonical(k): v for k, v in prior['weights'].items()}
    return float(weights[canonical(match.group(1))])


def prepare_rows(rows, spec, model, prior, sectors, asof_view=False):
    prepared = []
    for row in rows:
        name = row.get('name') or row.get('id_name') or row.get('id_company') or ''
        sector = sectors.get(peer_scoring.normalise_name(name), 'unclear')
        gates = eligibility.evaluate(row, sector, asof_view=asof_view)
        result = {'name': name, 'sector': sector, 'band': peer_scoring.age_band(row.get('f_age_months')),
                  **gates, 'signals': {}}
        observed_mass = collectable_mass = full_mass = 0.0
        for category in ('quality', 'opportunity'):
            numerator = denominator = observed_denominator = 0.0
            signals = []
            for metric in spec[category]:
                weight = metric_weight(metric, model, prior)
                value = metric_value(metric, row)
                if metric['metric'] == 'Pricing / pilot offer' and value == 1 and binary(row.get('f_pricing_page')) != 1:
                    weight = float(metric.get('default_weight', 1.0))
                mass = abs(weight)
                denominator += mass
                full_mass += mass
                if metric['columns']:
                    collectable_mass += mass
                if value is not None:
                    observed_mass += mass
                    observed_denominator += mass
                contribution = (-mass if metric['is_penalty'] else weight) * (value or 0.0)
                numerator += contribution
                if contribution:
                    signals.append((metric['metric'], contribution, metric['weight_source']))
            result[f'{category}_raw'] = numerator / denominator if denominator else 0.0
            if category == 'quality':
                result['quality_observed_raw'] = numerator / observed_denominator if observed_denominator else 0.0
            result['signals'][category] = '; '.join(
                f'{name}:{value / denominator:+.5f} [{source}]'
                for name, value, source in sorted(signals, key=lambda s: (-abs(s[1]), s[0]))[:3])
        result['coverage_collectable_pct'] = peer_scoring.coverage(observed_mass, collectable_mass)
        result['coverage_full_pct'] = peer_scoring.coverage(observed_mass, full_mass)
        result['ineligible_reasons'] = '; '.join(gates['reasons'])
        flags = list(gates['review_flags'])
        if text(row.get('f_description_text')):
            flags.append('text_rubrics_require_manual_review')
        result['review_flags'] = '; '.join(flags)
        prepared.append(result)
    return prepared


def score_rows(prepared, reference):
    output = []
    for row in prepared:
        eligible = row['status'] != 'INELIGIBLE'
        result = {key: row[key] for key in ('name', 'status', 'eligibility_score',
                  'ineligible_reasons', 'review_flags', 'coverage_collectable_pct', 'coverage_full_pct')}
        result.update(peer_group=peer_scoring.peer_group(row['sector'], row['band']),
                      peer_level='', ranked=eligible and row['coverage_collectable_pct'] >= 70,
                      call_priority='')
        for kind in ('quality', 'opportunity'):
            score, level = peer_scoring.percentile(reference, row[f'{kind}_raw'], row['sector'], row['band'], kind)
            result[f'{kind}_0_100'] = score if eligible else ''
            result[f'top3_{kind}_signals'] = row['signals'][kind] if eligible else ''
            if eligible:
                result['peer_level'] = level
        observed_score, _ = observed_percentile(reference, row['quality_observed_raw'], row['sector'], row['band'])
        result['quality_observed_0_100'] = observed_score if eligible else ''
        output.append(result)
    ordered = sorted((r for r in output if r['ranked']),
                     key=lambda r: (-min(9, int(r['quality_0_100'] // 10)), -r['opportunity_0_100']))
    for priority, row in enumerate(ordered, 1):
        row['call_priority'] = priority
    return output


def fit_reference_v2(members):
    """Add observed-quality distributions for the identical reference members."""
    reference = peer_scoring.fit_reference(members)
    observed = peer_scoring.fit_reference([
        {**row, 'quality_raw': row['quality_observed_raw']} for row in members])
    for section in ('peer_groups', 'sectors'):
        for key, bucket in reference[section].items():
            bucket['quality_observed'] = observed[section][key]['quality']
    reference['global']['quality_observed'] = observed['global']['quality']
    return reference


def observed_percentile(reference, value, sector, band):
    # Reuse the original percentile and fallback policy without changing shared modules.
    bucket = lambda b: {**b, 'quality': b['quality_observed']}
    observed_reference = {
        'global': bucket(reference['global']),
        'peer_groups': {key: bucket(b) for key, b in reference['peer_groups'].items()},
        'sectors': {key: bucket(b) for key, b in reference['sectors'].items()},
    }
    return peer_scoring.percentile(observed_reference, value, sector, band, 'quality')


def validate_observed_reference(reference):
    buckets = [reference['global'], *reference['peer_groups'].values(), *reference['sectors'].values()]
    for bucket in buckets:
        values = bucket.get('quality_observed')
        if (not isinstance(values, list) or len(values) != len(bucket['quality'])
                or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in values)
                or values != sorted(values)):
            raise ValueError('Missing/invalid observed-quality reference; fit a v2 reference')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--in', dest='input', required=True, type=Path)
    parser.add_argument('--out', dest='output', required=True, type=Path)
    parser.add_argument('--asof-view', action='store_true', help='Apply historical eligibility gates')
    parser.add_argument('--fit-reference', action='store_true', help='Explicitly fit and save the peer reference')
    parser.add_argument('--map', dest='map_path', type=Path, default=ROOT / 'models/scoring_map_v2.json')
    parser.add_argument('--reference', type=Path, default=ROOT / 'models/peer_reference_v2.json')
    args = parser.parse_args()
    output_path = args.output.resolve()
    if not output_path.is_relative_to((ROOT / 'reports').resolve()):
        raise ValueError('Output must be under reports/')
    load = lambda path: json.loads((ROOT / path).read_text(encoding='utf-8'))
    spec, model, prior = load(args.map_path), load('models/benchmark_weights.json'), load('prior_weights.json')
    sectors = peer_scoring.sector_lookup(ROOT / 'raw/relevance_v2.csv')
    prepared = prepare_rows(read_csv(args.input), spec, model, prior, sectors, args.asof_view)
    reference_path = args.reference
    if args.fit_reference:
        members = [row for row in prepared if row['status'] != 'INELIGIBLE']
        if not members:
            raise ValueError('Cannot fit peer reference without non-ineligible rows')
        reference = fit_reference_v2(members)
        peer_scoring.save_reference(reference, reference_path)
        print(f'Saved reference with {len(members)} non-ineligible rows to {reference_path}')
    else:
        reference = peer_scoring.load_reference(reference_path)
    validate_observed_reference(reference)
    rows = score_rows(prepared, reference)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f'Wrote {len(rows)} rows to {output_path}; statuses: {dict(Counter(r["status"] for r in rows))}; ranked: {sum(r["ranked"] for r in rows)}')


if __name__ == '__main__':
    main()
