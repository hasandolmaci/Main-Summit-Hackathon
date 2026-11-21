"""Train the exploratory prior-anchored benchmark; run from any directory."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
import numpy as np
from scipy.optimize import minimize
from scipy.special import expit
from sklearn.metrics import roc_auc_score, brier_score_loss, log_loss
from sklearn.model_selection import StratifiedKFold
from benchmark_common import (read_csv, build_features, LEARNED, MISSING,
                              raw_scores, group_mapping, parse_date)

ROOT = Path(__file__).resolve().parents[1]
SEED = 20261120
K_GRID = [1, 2, 4, 8]
LAM_GRID = [1, 4, 16, 64]

def validate_inputs(rows, prior):
    if len(rows) != 205:
        raise ValueError('Frozen reference must contain all 205 backtest rows')
    ids = [r['id_company'] for r in rows]
    if len(set(ids)) != len(ids):
        raise ValueError('Repeated companies require grouped CV')
    for label in ('y_label', 'y_label_incl_dead'):
        if not {r[label] for r in rows} <= {'', '0', '1'}:
            raise ValueError(f'Invalid {label}')
    dictionary = read_csv(ROOT / 'data_dictionary.csv')
    if not {r['column'] for r in dictionary}.issuperset(rows[0]):
        raise ValueError('Dictionary does not cover data columns')
    if not prior.get('frozen'):
        raise ValueError('Expected frozen prior')

def design(features, mode):
    cols = [np.ones((len(features['S']), 1)), features['B']]
    penalties = [0., .5, .5]
    if mode == 'full':
        cols += [features['X'], features['M']]
        penalties += [1.] * (len(LEARNED) + len(MISSING))
    return np.column_stack(cols), np.array(penalties)

def fit_prior_lr(A, y, offset, penalties):
    """Summed logistic loss + sum(penalty_j * theta_j**2)."""
    def objective(theta):
        z = A @ theta + offset
        loss = np.sum(np.logaddexp(0., z) - y*z) + np.dot(penalties, theta**2)
        grad = A.T @ (expit(z) - y) + 2*penalties*theta
        return loss, grad
    initial = np.zeros(A.shape[1])
    prevalence = np.clip(np.mean(y), 1e-6, 1-1e-6)
    initial[0] = np.log(prevalence/(1-prevalence)) - np.mean(offset)
    result = minimize(objective, initial, jac=True, method='L-BFGS-B',
                      options={'maxiter': 400, 'ftol': 1e-11, 'gtol': 1e-7})
    if not result.success:
        raise RuntimeError(f'Optimizer did not converge: {result.message}')
    return result.x

def metrics(y, p):
    return np.array([roc_auc_score(y, p), brier_score_loss(y, p), log_loss(y, p, labels=[0, 1])])

def cross_validate(features, y, repeats=20):
    n = len(y)
    if min(np.bincount(y, minlength=2)) < 5:
        raise ValueError('Need at least 5 examples of each class')
    full_A, _ = design(features, 'full')
    year_A, _ = design(features, 'year')
    settings = [(k, lam) for k in K_GRID for lam in LAM_GRID]
    predictions = {('full', k, lam): np.zeros((repeats, n)) for k, lam in settings}
    predictions.update({('prior-offset', k, 0): np.zeros((repeats, n)) for k in K_GRID})
    predictions[('year-only', 0, 0)] = np.zeros((repeats, n))
    predictions[('constant', 0, 0)] = np.zeros((repeats, n))
    for repeat in range(repeats):
        cv = StratifiedKFold(5, shuffle=True, random_state=SEED+repeat)
        for train, test in cv.split(full_A, y):
            predictions[('constant', 0, 0)][repeat, test] = y[train].mean()
            for key, prediction in predictions.items():
                mode, k, lam = key
                if mode == 'constant':
                    continue
                A = full_A if mode == 'full' else year_A
                penalties = np.array([0., .5, .5] + ([float(lam)]*9 if mode == 'full' else []))
                theta = fit_prior_lr(A[train], y[train], k*features['S'][train], penalties)
                prediction[repeat, test] = expit(A[test] @ theta + k*features['S'][test])
        if (repeat+1) % 5 == 0:
            print(f'  CV repeat {repeat+1}/{repeats}', flush=True)
    scores = {key: np.array([metrics(y, p) for p in prediction]) for key, prediction in predictions.items()}
    best_full = min((key for key in scores if key[0] == 'full'), key=lambda key: scores[key][:, 2].mean())
    best_prior = min((key for key in scores if key[0] == 'prior-offset'), key=lambda key: scores[key][:, 2].mean())
    keys = [('constant', 0, 0), ('year-only', 0, 0), best_prior, best_full]
    return {'keys': keys, 'best_full': best_full, 'best_prior': best_prior,
            'predictions': predictions, 'scores': scores}

def subset(features, indices):
    return {k: (v[indices] if isinstance(v, np.ndarray) else v) for k, v in features.items()}

def final_weights(features, theta, k):
    deltas = dict(zip(LEARNED, theta[3:3+len(LEARNED)]))
    return {name: float(k*features['prior'].get(name, 0.) + deltas.get(name, 0.))
            for name in features['names']}

def bootstrap_report(features, y, cv, count=1000):
    rng = np.random.default_rng(SEED+500)
    _, k, lam = cv['best_full']
    A, penalties = design(features, 'full')
    penalties[3:] = lam
    samples, adjustments, auc_samples = [], [], []
    p_full = cv['predictions'][cv['best_full']].mean(axis=0)
    p_prior = cv['predictions'][cv['best_prior']].mean(axis=0)
    while len(samples) < count:
        idx = rng.integers(0, len(y), len(y))
        if len(np.unique(y[idx])) < 2:
            continue
        theta = fit_prior_lr(A[idx], y[idx], k*features['S'][idx], penalties)
        weights = final_weights(features, theta, k)
        samples.append([weights[n] for n in features['names']])
        adjustments.append(theta[3:])
        f_auc, p_auc = roc_auc_score(y[idx], p_full[idx]), roc_auc_score(y[idx], p_prior[idx])
        auc_samples.append([f_auc, p_auc, f_auc-p_auc])
    def summarize(values):
        values = np.asarray(values)
        return {'median': float(np.median(values)), 'lower': float(np.quantile(values, .025)),
                'upper': float(np.quantile(values, .975)), 'share_positive': float(np.mean(values > 0))}
    stability = {name: summarize(np.array(samples)[:, j]) for j, name in enumerate(features['names'])}
    adjustment_stability = {name: summarize(np.array(adjustments)[:, j]) for j, name in enumerate(LEARNED+MISSING)}
    auc = {name: summarize(np.array(auc_samples)[:, j]) for j, name in enumerate(['full', 'prior-offset', 'difference'])}
    return stability, adjustment_stability, auc

def cv_table(cv):
    lines = ['| Model | k | lambda | AUC mean +/- SD | Brier mean +/- SD | Log loss mean +/- SD |',
             '|---|---:|---:|---:|---:|---:|']
    for key in cv['keys']:
        values = cv['scores'][key]
        cells = [f'{values[:, j].mean():.4f} +/- {values[:, j].std(ddof=1):.4f}' for j in range(3)]
        lines.append(f'| {key[0]} | {key[1]} | {key[2]} | ' + ' | '.join(cells) + ' |')
    return '\n'.join(lines)

def export_model(features, theta, k, lam, prior, stability, adjustment_stability):
    weights = final_weights(features, theta, k)
    groups = group_mapping(features['names'])
    raw, _ = raw_scores(features, weights, groups)
    now = datetime.now(timezone.utc).isoformat()
    model = {'schema_version': 1, 'version': 'prior-anchored-logistic-v1', 'created_at_utc': now,
             'prior_weights_original': prior['weights'], 'prior_weights': features['prior'],
             'prior_sha256': hashlib.sha256((ROOT/'prior_weights.json').read_bytes()).hexdigest(),
             'backtest_sha256': hashlib.sha256((ROOT/'backtest.csv').read_bytes()).hexdigest(),
             'k': k, 'lam': lam, 'intercept': float(theta[0]),
             'band_coefficients': dict(zip(['band_2022_23', 'band_2024_25'], map(float, theta[1:3]))),
             'deltas': dict(zip(LEARNED, map(float, theta[3:10]))),
             'missing_coefficients': dict(zip(MISSING, map(float, theta[10:]))),
             'final_weights': weights, 'group_mapping': groups,
             'feature_transforms': {'age_le_18': '1 if f_age_months <= 18, missing if blank',
                 'has_legal_entity': "1 if stripped f_legal_entity non-empty and not '0'; blank unknown, scored 0",
                 'snapshot_missing': '1 if ANY model website f_* signal (including legal entity, excluding age) is blank/absent',
                 'age_missing': '1 if f_age_months blank/absent',
                 'numeric': 'Numeric values unchanged; blank/absent -> 0',
                 'prior_aliases': {'f_legal_entity': 'has_legal_entity', 'age_months_le_18': 'age_le_18'},
                 'bands': '2019-21 base; 2022-23; >=2024 latest band; absent date -> latest band',
                 'coverage': 'Observed unique merit features / all unique merit features; excludes nuisance indicators and year',
                 'percentile': '100*(count(reference < x)+0.5*count(reference == x))/205'},
             'reference_distributions': {key: sorted(map(float, value)) for key, value in raw.items()},
             'reference': {'file': 'backtest.csv', 'rows': len(features['S']), 'includes_unlabelled': True},
             'penalty': 'summed logloss + lam*(sum(delta^2)+sum(eta^2)) + 0.5*sum(gamma^2)',
             'learned_features': LEARNED, 'missing_indicators': MISSING,
             'stability': stability, 'adjustment_stability': adjustment_stability,
             'validation': {'seed': SEED, 'folds': 5, 'repeats': 20, 'bootstrap_samples': 1000,
                            'k_grid': K_GRID, 'lam_grid': LAM_GRID},
             'display': 'Merit excludes intercept, year-band and missingness effects. No probabilities.'}
    (ROOT/'models').mkdir(exist_ok=True)
    (ROOT/'models/benchmark_weights.json').write_text(json.dumps(model, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    return model

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    start = time.time()
    rows = read_csv(ROOT/'backtest.csv')
    prior = json.loads((ROOT/'prior_weights.json').read_text(encoding='utf-8-sig'))
    validate_inputs(rows, prior)
    features = build_features(rows, prior)
    idx = np.array([i for i, r in enumerate(rows) if r['y_label'] != ''])
    y = np.array([int(rows[i]['y_label']) for i in idx])
    train = subset(features, idx)
    print(f'Main: {len(y)} labelled, {y.sum()} positives; tuning 16 full + 4 prior settings', flush=True)
    cv = cross_validate(train, y)
    _, k, lam = cv['best_full']
    A, penalties = design(train, 'full')
    penalties[3:] = lam
    theta = fit_prior_lr(A, y, k*train['S'], penalties)
    print(f'Selected k={k}, lambda={lam}; 1000 bootstrap refits', flush=True)
    stability, adjustment_stability, auc = bootstrap_report(train, y, cv)
    model = export_model(features, theta, k, lam, prior, stability, adjustment_stability)
    sens_idx = np.array([i for i, r in enumerate(rows) if r['y_label_incl_dead'] != ''])
    sens_y = np.array([int(rows[i]['y_label_incl_dead']) for i in sens_idx])
    print(f'Sensitivity: {len(sens_y)} labelled, {sens_y.sum()} positives', flush=True)
    sensitivity = cross_validate(subset(features, sens_idx), sens_y)
    counts = ['| Year band | All rows | Labelled | Positive | Negative | Rate (Wilson 95% CI) |',
              '|---|---:|---:|---:|---:|---:|']
    for label, mask in [('2019-21', features['B'].sum(axis=1)==0),
                        ('2022-23', features['B'][:, 0]==1), ('2024-25', features['B'][:, 1]==1)]:
        yy = y[mask[idx]]
        n, positive = len(yy), int(yy.sum())
        rate = positive/n
        z = 1.95996398454
        center = (rate+z*z/(2*n))/(1+z*z/n)
        half = z*np.sqrt(rate*(1-rate)/n+z*z/(4*n*n))/(1+z*z/n)
        counts.append(f'| {label} | {int(mask.sum())} | {n} | {positive} | {n-positive} | {rate:.1%} ({center-half:.1%}, {center+half:.1%}) |')
    weight_lines = ['| Feature | Prior | Final weight | Delta | Bootstrap median | 95% interval | Share positive |',
                    '|---|---:|---:|---:|---:|---:|---:|']
    for name, weight in model['final_weights'].items():
        s = stability[name]
        weight_lines.append(f"| {name} | {features['prior'].get(name, 0):.4f} | {weight:.4f} | {model['deltas'].get(name, 0):.4f} | {s['median']:.4f} | [{s['lower']:.4f}, {s['upper']:.4f}] | {s['share_positive']:.1%} |")
    kept = [r for r in rows if r['y_label'] != '' and r.get('needs_review') != '1']
    confident = [r for r in rows if r['y_label'] != '' and r.get('y_label_confident') == '1']
    lines = ['# Exploratory prior-anchored logistic benchmark', '',
             f"Generated {model['created_at_utc']}. Reference: all 205 backtest rows. Main: {len(y)} labelled, {int(y.sum())} positives, {len(y)-int(y.sum())} negatives; {len(rows)-len(y)} excluded blank labels.", '',
             '## Counts by as-of year band', '', '\n'.join(counts), '',
             '## Repeated cross-validation', '', cv_table(cv), '',
             f'Selected full k={k}, lambda={lam}. Prior-offset selects its own k={cv["best_prior"][1]} on the same folds. Each of 20 seeded repeats has five stratified folds. Metrics are computed on the complete out-of-fold vector per repeat; SD is across repeats, not across folds. Constant baseline uses training-fold prevalence. Year-only and prior-offset use band penalty 0.5 and an unpenalised intercept. Full uses summed logistic loss plus lambda times squared deltas/missing coefficients plus 0.5 times squared year coefficients.', '',
             'The constant baseline has pooled OOF AUC below 0.5 because fold-specific training prevalences differ slightly and are inversely related to held-out prevalence. It has no within-fold discrimination; each within-fold AUC is 0.5. Its zero repeat SD follows identical stratified fold class counts.', '',
             'Grid selection and reporting use the same CV runs (not nested); estimates therefore have tuning optimism. Splits stratify labels, not years: only one positive is in the oldest band. No class balancing, interactions, standardisation or feature selection.', '',
             '## Paired OOF AUC bootstrap (1000 company resamples)', '',
             'Predictions are averaged over the 20 out-of-fold predictions per company, then the same companies are resampled for both models. These intervals condition on the selected settings and fixed OOF predictions; they do not rerun CV or account for model-selection uncertainty.', '']
    for name, key in [('Full', cv['best_full']), ('Prior-offset', cv['best_prior'])]:
        s = auc[key[0]]
        point = roc_auc_score(y, cv['predictions'][key].mean(axis=0))
        lines.append(f"- {name}: AUC {point:.4f}, bootstrap 95% CI [{s['lower']:.4f}, {s['upper']:.4f}].")
    s = auc['difference']
    difference = roc_auc_score(y, cv['predictions'][cv['best_full']].mean(axis=0))-roc_auc_score(y, cv['predictions'][cv['best_prior']].mean(axis=0))
    lines += [f"- Paired full minus prior-offset: {difference:.4f}, 95% CI [{s['lower']:.4f}, {s['upper']:.4f}].", '',
              '## Final weights and stability', '', '\n'.join(weight_lines), '',
              '1000 ordinary company bootstrap refits at the selected fixed k/lambda. Intervals are percentile intervals conditional on settings. Frozen features vary only through the fixed k, so their intervals are degenerate. Learned means a prior adjustment was estimated, even when its delta is near zero.', '',
              '## Sensitivity: y_label_incl_dead', '',
              f'{len(sens_y)} labelled; {int(sens_y.sum())} positives; {len(sens_y)-int(sens_y.sum())} negatives. Full grid independently retuned.', '', cv_table(sensitivity), '',
              f'Review-excluded: {len(kept)} rows, {sum(int(r["y_label"]) for r in kept)} positives; too few for five-fold CV. Confident-only: {len(confident)} rows; unavailable.', '',
              '## Encoding and display', '',
              'The prior is reproduced using its original numeric weights, age <=18 transform, legal-evidence-present indicator, and missing-as-zero convention. Alias features are merged so legal entity and age are counted once. Seven deltas are fitted. All other signals receive k times their frozen prior coefficient; serial/founder signals have no supplied prior and are excluded.', '',
              'snapshot_missing is 1 when any model website signal is blank (including legal entity); age has a separate missing indicator. The source f_snapshot_missing flag is not used as a predictor. Blank legal evidence is unknown, not confirmed absence; this makes snapshot_missing broader than the archive-missing flag. Coverage counts observed unique merit features, excluding year and missingness indicators.', '',
              'Quality includes customers, grants, GitHub, external contributions, patents and Product Hunt. Opportunity includes pricing, Impressum, legal entity, GmbH i.G., waitlist, jobs and age <=18. Contributions are final_weight * value. Intercept, bands and missingness effects are excluded from displayed merit. New startups use the latest year band internally. Each raw sum is converted independently using 100*(# reference below + 0.5*# reference equal)/205. Reference includes unlabelled and ineligible rows and is frozen in the JSON.', '',
              'Eligibility uses funding/German/gate fields only for status, never training. Known failures override unknowns. With no gate fields status is ELIGIBLE_UNCHECKED; provided unknowns require REVIEW. Funding status on this historical backtest may describe funding after its as-of date, so scoring status is not historical eligibility and does not filter the frozen reference or training data.', '',
              '## Caveats', '',
              '**Only 13 positive examples. This is an exploratory ranking benchmark, not calibrated probabilities or investment advice.** Percentile scores are relative to a small selected cohort and do not establish predictive accuracy, causation, investment returns, discovery advantage, or representativeness for German startups. Earlier label-informed reviews mean this is not untouched confirmation. Quality and Opportunity are proposed constructs, not independently validated measures.', '',
              'Historical fields are trusted as assembled; source-by-source timestamp verification has not been rerun. info_* and cb_* never enter predictors. Website archive age is not company age and cannot establish a company-age eligibility gate. Sparse patents/GitHub signals and missingness can distort rankings. Review-excluded and confident-only data cannot support meaningful validation. Bootstrap intervals omit tuning and source-label uncertainty. The discovery file has no model inputs: zero coverage and tied percentiles contain no company-specific evidence.', '',
              f'Runtime: {time.time()-start:.1f} seconds. Seed: {SEED}.']
    (ROOT/'reports').mkdir(exist_ok=True)
    (ROOT/'reports/benchmark_report.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    # Machine-readable validation keeps grid selection and every repeat auditable.
    validation = {}
    for label, result in [('main', cv), ('incl_dead', sensitivity)]:
        validation[label] = {'selected_full': result['best_full'], 'selected_prior': result['best_prior'],
                             'repeat_metrics': {'|'.join(map(str, key)): value.tolist() for key, value in result['scores'].items()}}
    validation['auc_bootstrap'] = auc
    (ROOT/'reports/benchmark_validation.json').write_text(json.dumps(validation, indent=2)+'\n', encoding='utf-8')
    print('\nMAIN CV\n'+cv_table(cv))
    print('\nFINAL WEIGHTS\n'+'\n'.join(weight_lines))
    print('\nSENSITIVITY CV\n'+cv_table(sensitivity))
    print(f'Completed in {time.time()-start:.1f}s', flush=True)

if __name__ == '__main__':
    main()
