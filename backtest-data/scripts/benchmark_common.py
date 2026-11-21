"""Deterministic feature encoding shared by training and scoring."""
import csv
from datetime import datetime
import numpy as np

LEARNED = ['f_pricing_page', 'f_impressum', 'f_customer_logos', 'f_jobs_page',
           'age_le_18', 'm_grant_score', 'has_legal_entity']
MISSING = ['snapshot_missing', 'age_missing']
ALIASES = {'f_legal_entity': 'has_legal_entity', 'age_months_le_18': 'age_le_18'}
QUALITY = {'m_grant_score', 'm_gh_has_open_source', 'm_gh_external_score',
           'm_patent_verified_before_asof', 'm_ph_score_before_asof', 'f_customer_logos'}
OPPORTUNITY = {'f_pricing_page', 'f_impressum', 'has_legal_entity', 'f_gmbh_ig',
               'f_waitlist', 'f_jobs_page', 'age_le_18'}

def read_csv(path):
    with open(path, encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))

def number(value):
    if value is None or str(value).strip().lower() in {'', 'nan', 'none', 'null', 'unknown', 'n/a'}:
        return None
    v = str(value).strip().lower()
    if v in {'true', 'yes'}:
        return 1.
    if v in {'false', 'no'}:
        return 0.
    try:
        x = float(v)
    except ValueError as e:
        raise ValueError(f'Expected numeric feature; got {value!r}') from e
    return x if np.isfinite(x) else None

def parse_date(value):
    value = str(value or '').strip()
    for fmt in ('%d/%m/%Y', '%Y-%m-%d', '%Y-%m-%dT%H:%M:%S', '%Y%m%d%H%M%S', '%Y%m%d'):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass
    raise ValueError(f'Unsupported date {value!r}')

def canonical_prior(prior):
    out = {}
    for key, weight in prior['weights'].items():
        name = ALIASES.get(key, key)
        if name in out:
            raise ValueError(f'Duplicate canonical prior feature: {name}')
        if name.startswith(('info_', 'cb_', 'y_')):
            raise ValueError(f'Forbidden prior predictor: {name}')
        out[name] = float(weight)
    return out

def feature_value(row, name):
    if name == 'age_le_18':
        age = number(row.get('f_age_months'))
        return None if age is None else float(age <= 18)
    if name == 'has_legal_entity':
        value = str(row.get('f_legal_entity', '') or '').strip()
        return None if not value else float(value != '0')
    return number(row.get(name))

def build_features(rows, prior):
    weights = canonical_prior(prior)
    names = list(dict.fromkeys([*weights, *LEARNED]))
    raw = [[feature_value(row, name) for name in names] for row in rows]
    observed = np.array([[v is not None for v in row] for row in raw], dtype=bool)
    values = np.array([[0. if v is None else v for v in row] for row in raw])
    website = [name for name in names if name.startswith('f_') or name == 'has_legal_entity']
    missing = np.array([[float(any(feature_value(row, n) is None for n in website)),
                         float(feature_value(row, 'age_le_18') is None)] for row in rows])
    band = np.zeros((len(rows), 2))
    for i, row in enumerate(rows):
        year = parse_date(row['meta_asof_date']).year if row.get('meta_asof_date') else 2025
        if year < 2019:
            raise ValueError(f'As-of year before supported base band: {year}')
        band[i] = [float(2022 <= year <= 2023), float(year >= 2024)]
    prior_vector = np.array([weights.get(n, 0.) for n in names])
    return {'names': names, 'values': values, 'observed': observed, 'M': missing,
            'X': values[:, [names.index(n) for n in LEARNED]], 'B': band,
            'S': values @ prior_vector, 'prior': weights}

def group_mapping(names):
    # Any additional prior quality feature belongs to Quality; explicit opportunity list is fixed.
    return {name: 'opportunity' if name in OPPORTUNITY else 'quality' for name in names}

def raw_scores(features, final_weights, groups):
    contributions = features['values'] * np.array([final_weights[n] for n in features['names']])
    result = {group: contributions[:, [groups[n] == group for n in features['names']]].sum(axis=1)
              for group in ('quality', 'opportunity')}
    result['total'] = result['quality'] + result['opportunity']
    return result, contributions

def percentile(value, reference):
    reference = np.asarray(reference)
    return float(100 * (np.searchsorted(reference, value, side='left') +
                        .5 * (np.searchsorted(reference, value, side='right') -
                              np.searchsorted(reference, value, side='left'))) / len(reference))
