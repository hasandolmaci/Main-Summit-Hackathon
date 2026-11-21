"""Focused regression checks for score identity, missingness, and eligibility."""
import json
import hashlib
from pathlib import Path
import unittest
import numpy as np
from scipy.special import expit
from benchmark_common import build_features, percentile, read_csv, raw_scores
from score_startups import eligibility, score_rows
from train_benchmark import design, fit_prior_lr, LEARNED

ROOT = Path(__file__).resolve().parents[1]

class BenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prior = json.loads((ROOT/'prior_weights.json').read_text())
        cls.model = json.loads((ROOT/'models/benchmark_weights.json').read_text())
        cls.rows = read_csv(ROOT/'backtest.csv')

    def test_prior_exact_and_aliases_once(self):
        f = build_features(self.rows, self.prior)
        manual = []
        for row in self.rows:
            total = 0.
            for key, weight in self.prior['weights'].items():
                if key == 'age_months_le_18':
                    value = float(float(row['f_age_months']) <= 18) if row['f_age_months'] else 0.
                elif key == 'f_legal_entity':
                    value = float(bool(row[key]) and row[key] != '0')
                else:
                    value = float(row.get(key) or 0)
                total += weight*value
            manual.append(total)
        np.testing.assert_allclose(f['S'], manual, atol=1e-14)
        self.assertEqual(len(f['names']), 13)

    def test_no_feature_data_is_missing(self):
        f = build_features([{}], self.prior)
        self.assertEqual(f['observed'].sum(), 0)
        self.assertEqual(f['S'][0], 0)
        np.testing.assert_array_equal(f['M'], [[1, 1]])
        np.testing.assert_array_equal(f['B'], [[0, 1]])

    def test_age_boundary_and_legal_zero(self):
        f = build_features([{'f_age_months': '18', 'f_legal_entity': '0'},
                            {'f_age_months': '18.01', 'f_legal_entity': 'GmbH'}], self.prior)
        np.testing.assert_array_equal(f['values'][:, f['names'].index('age_le_18')], [1, 0])
        np.testing.assert_array_equal(f['values'][:, f['names'].index('has_legal_entity')], [0, 1])
        self.assertTrue(f['observed'][:, f['names'].index('has_legal_entity')].all())

    def test_tie_aware_percentile(self):
        self.assertEqual(percentile(2, [1, 2, 2, 3]), 50.)
        self.assertEqual(percentile(0, [1, 2, 2, 3]), 0.)
        self.assertEqual(percentile(4, [1, 2, 2, 3]), 100.)

    def test_frozen_reference_reproduction(self):
        self.assertEqual(hashlib.sha256((ROOT/'backtest.csv').read_bytes()).hexdigest(), self.model['backtest_sha256'])
        f = build_features(self.rows, self.prior)
        raw, _ = raw_scores(f, self.model['final_weights'], self.model['group_mapping'])
        for name in raw:
            np.testing.assert_array_equal(np.sort(raw[name]), self.model['reference_distributions'][name])
        np.testing.assert_allclose(raw['total'], raw['quality']+raw['opportunity'])

    def test_logit_decomposition(self):
        f = build_features(self.rows, self.prior)
        final = f['values'] @ np.array([self.model['final_weights'][n] for n in f['names']])
        anchored = self.model['k']*f['S'] + f['X'] @ np.array([self.model['deltas'][n] for n in LEARNED])
        np.testing.assert_allclose(final, anchored, atol=1e-14)

    def test_fail_overrides_unknown(self):
        cases = [({}, 'ELIGIBLE_UNCHECKED'), ({'gate_german': ''}, 'REVIEW'),
                 ({'gate_german': '1'}, 'ELIGIBLE'),
                 ({'gate_german': '', 'cb_has_equity_round': '1'}, 'INELIGIBLE'),
                 ({'info_is_german': '0'}, 'INELIGIBLE'),
                 ({'info_not_german': '1'}, 'INELIGIBLE'),
                 ({'info_country': 'Austria'}, 'INELIGIBLE'),
                 ({'info_is_german': 'not German'}, 'INELIGIBLE'),
                 ({'gate_age': 'fail: too old'}, 'INELIGIBLE'),
                 ({'y_funding_status': 'FUNDED_WITHIN'}, 'INELIGIBLE'),
                 ({'y_funding_status': 'NO_FUNDING_FOUND', 'cb_has_equity_round': ''}, 'REVIEW')]
        for row, expected in cases:
            with self.subTest(row=row):
                self.assertEqual(eligibility(row)[0], expected)

    def test_year_and_label_do_not_change_displayed_merit(self):
        row = self.rows[0].copy()
        altered = {**row, 'meta_asof_date': '2025-01-01', 'y_label': '1',
                   'info_li_employees_now': '999999'}
        a, b = score_rows([row, altered], self.model)
        for key in ('quality_0_100', 'opportunity_0_100', 'total_0_100', 'coverage_pct', 'top3_signals'):
            self.assertEqual(a[key], b[key])

    def test_fitted_objective_gradient(self):
        f = build_features(self.rows, self.prior)
        idx = np.array([i for i, row in enumerate(self.rows) if row['y_label'] != ''])
        y = np.array([int(self.rows[i]['y_label']) for i in idx])
        A, penalties = design(f, 'full')
        A = A[idx]
        penalties[3:] = self.model['lam']
        offset = self.model['k']*f['S'][idx]
        theta = fit_prior_lr(A, y, offset, penalties)
        gradient = A.T @ (expit(A @ theta + offset)-y)+2*penalties*theta
        self.assertLess(np.max(np.abs(gradient)), 1e-4)

    def test_discovery_no_invented_signals(self):
        scored = score_rows(read_csv(ROOT/'discovery_leads.csv'), self.model)
        self.assertEqual(len(scored), 27)
        for row in scored:
            self.assertEqual(row['status'], 'ELIGIBLE_UNCHECKED')
            self.assertEqual(row['coverage_pct'], '0.00')
            self.assertEqual(row['top3_signals'], '')

if __name__ == '__main__':
    unittest.main()
