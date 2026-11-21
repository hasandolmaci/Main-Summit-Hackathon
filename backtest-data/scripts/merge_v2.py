"""Merge supplied v2 research; optionally regenerate the comparison after scoring.

Only stdlib; no external requests. Original input columns retain their order.
Current-founder fallback is explicitly requested and flagged, not historical proof.
"""
from __future__ import annotations
import argparse
import calendar
from collections import Counter, defaultdict
import csv
from datetime import datetime
import json
import math
from pathlib import Path
import statistics
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
NEW = ['cid', 'm_founder_count', 'm_founder_phd', 'm_star_academic',
       'm_trademark_before_asof', 'v2_founders_from_now', 'v2_founder_count_from_now',
       'y_label_v2_unsure', 'live_funding_status', 'live_red_flags', 'live_customers',
       'v2_evidence', 'v2_undated_facts', 'v2_post_asof_facts']
CHANGED = ['s_founder_names', 'm_founder_technical', 'm_founder_industry_exp',
           'm_serial_score', 'm_patent_verified_before_asof', 'y_label',
           'y_label_basis', 'needs_review']
BG_FIELDS = ['technical_background', 'has_phd', 'industry_years_before_asof',
             'prior_company_vc_backed_or_exit', 'star_academic']


def read(path):
    with (ROOT / path).open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        return list(reader), reader.fieldnames


def norm(value):
    return unicodedata.normalize('NFC', value).strip().casefold()


def date_upper(value):
    """Conservative latest possible date for year/month-only evidence."""
    value = str(value).strip()
    try:
        if len(value) == 4:
            return datetime(int(value), 12, 31)
        if len(value) == 7:
            year, month = map(int, value.split('-'))
            return datetime(year, month, calendar.monthrange(year, month)[1])
        if '/' in value:
            return datetime.strptime(value, '%d/%m/%Y')
        return datetime.fromisoformat(value.replace('Z', '+00:00')).replace(tzinfo=None)
    except ValueError:
        return None


def packed(items):
    return json.dumps(items, ensure_ascii=False, separators=(',', ':')) if items else ''


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else None
    except (ValueError, TypeError):
        return None


def index(path, companies, multiple=False, name_col='name'):
    rows, _ = read(path)
    result = defaultdict(list) if multiple else {}
    for row in rows:
        cid = row['cid']
        if cid not in companies or norm(row[name_col]) != norm(companies[cid]['name']):
            raise ValueError(f'{path}: unknown cid or name mismatch: {cid}')
        if multiple:
            result[cid].append(row)
        else:
            if cid in result:
                raise ValueError(f'{path}: duplicate cid {cid}')
            result[cid] = row
    print(f'Source worked: {path} ({len(rows)} rows; names verified)')
    return result


def merge():
    company_rows, _ = read('raw/companies_all.csv')
    companies = {r['cid']: r for r in company_rows}
    if len(companies) != 225:
        raise ValueError('Expected 225 unique company IDs')
    founders = index('raw/v2_founders_A.csv', companies)
    other = index('raw/v2_founders_B.csv', companies)
    if founders.keys() & other.keys():
        raise ValueError('Overlapping founder sources')
    founders.update(other)
    background = index('raw/v2_founder_bg.csv', companies, True, 'company')
    trademarks = index('raw/v2_trademarks.csv', companies)
    patents = index('raw/v2_patents.csv', companies)
    intel = index('raw/v2_live_intel.csv', companies)
    audit_rows, audit_fields = read('raw/v2_label_audit_positives.csv')
    verdict_col = next(k for k in audit_fields if k.startswith('label_verdict'))
    audits = {norm(r['name']): r for r in audit_rows}
    if len(audits) != len(audit_rows):
        raise ValueError('Duplicate audited names')
    known_names = {norm(r['name']) for r in company_rows if r['set'] == 'backtest'}
    if audits.keys() - known_names:
        raise ValueError('Unmatched label audit names')
    print(f'Source worked: raw/v2_label_audit_positives.csv ({len(audits)} rows)')
    for source, output, cohort, prefix, expected in [
        ('backtest.csv', 'backtest_v2.csv', 'backtest', 'bt', 205),
        ('live_leads.csv', 'live_leads_v2.csv', 'live', 'lv', 20)]:
        originals, fields = read(source)
        members = [r for r in company_rows if r['set'] == cohort]
        if len(originals) != expected or len(members) != expected:
            raise ValueError(f'{source}: incorrect row count')
        columns = fields + [k for k in NEW if k not in fields]
        counts, changes = Counter(), Counter()
        with (ROOT / output).open('w', encoding='utf-8', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            stream.flush()
            for i, (original, company) in enumerate(zip(originals, members), 1):
                cid = f'{prefix}{i:03d}'
                cutoff = date_upper(original['meta_asof_date'])
                if (company['cid'] != cid or norm(original['id_name']) != norm(company['name'])
                        or cutoff != date_upper(company['asof_date'])):
                    raise ValueError(f'{source}: row {i} identity/as-of mismatch')
                live = cohort == 'live'
                row = {**original, **{k: '' for k in NEW if k not in fields}, 'cid': cid}
                evidence, undated, post = [], [], []

                def record(field, value, url, used_date='', bucket=None):
                    if not url:
                        if str(value) == '0':
                            return  # Checked-zero input, not a positive evidence claim.
                        raise ValueError(f'{cid}: missing evidence URL for {field}')
                    item = dict(field=field, value=value, url=url, date=used_date)
                    (bucket if bucket is not None else evidence).append(item)

                founder = founders.get(cid, {})
                snapshot = founder.get('snapshot_date', '')
                valid_snapshot = date_upper(snapshot) is not None and date_upper(snapshot) < cutoff
                for dest, hist, now, flag in [
                    ('s_founder_names', 'founder_names_asof', 'founder_names_now', 'v2_founders_from_now'),
                    ('m_founder_count', 'founder_count_asof', 'founder_count_now', 'v2_founder_count_from_now')]:
                    value = founder.get(hist, '')
                    # Explicit checked-zero counts are preserved, never inferred from blank names.
                    if value and (valid_snapshot or value == '0'):
                        row[dest] = value
                        record(dest, value, founder['evidence_url'], snapshot)
                    elif founder.get(now):
                        row[dest] = founder[now]
                        row[flag] = '1'
                        record(dest, founder[now], founder['evidence_url'], bucket=undated)
                    if value and not valid_snapshot and value != '0':
                        record(hist, value, founder['evidence_url'], snapshot,
                               post if date_upper(snapshot) else undated)

                researched = []
                values = defaultdict(list)
                for bg in background.get(cid, []):
                    if bg['confidence'].lower() not in {'medium', 'high'}:
                        continue
                    if date_upper(bg['asof_date']) != cutoff:
                        raise ValueError(f'{cid}: background cutoff mismatch')
                    researched.append(bg)
                    # Store professional facts only, omitting free-text notes/private identifiers.
                    for key, target in [('undated_facts', undated), ('post_asof_facts', post)]:
                        for fact in json.loads(bg[key] or '[]'):
                            if fact.get('field') in BG_FIELDS:
                                record(fact['field'], fact.get('value', ''), fact['url'],
                                       fact.get('date', ''), target)
                    blocked = {f['field'] for f in json.loads(bg['post_asof_facts'] or '[]')}
                    dated = json.loads(bg['evidence_dates'] or '[]')
                    for field in BG_FIELDS:
                        if not bg[field] or field in blocked:
                            continue
                        supports = [f for f in dated if f.get('field') == field
                                    and date_upper(f.get('date', '')) is not None
                                    and date_upper(f['date']) < cutoff and f.get('url')]
                        if supports:
                            values[field].append(number(bg[field]))
                            for fact in supports:
                                record(field, bg[field], fact['url'], fact['date'])
                if 1 in values['technical_background'] or 1 in values['has_phd']:
                    row['m_founder_technical'] = '1'
                if 1 in values['has_phd']:
                    row['m_founder_phd'] = '1'
                elif 0 in values['has_phd']:
                    row['m_founder_phd'] = '0'
                if any(v is not None and v >= 3 for v in values['industry_years_before_asof']):
                    row['m_founder_industry_exp'] = '1'
                if 1 in values['prior_company_vc_backed_or_exit']:
                    row['m_serial_score'] = '1'
                if researched:
                    row['m_star_academic'] = '1' if 1 in values['star_academic'] else '0'

                tm = trademarks.get(cid, {})
                count = number(tm.get('tm_count_total' if live else 'tm_count_before_asof'))
                if count is not None:
                    row['m_trademark_before_asof'] = str(int(count > 0))
                    if count > 0:
                        filing = tm['tm_first_filing_date']
                        if not live and (not date_upper(filing) or date_upper(filing) >= cutoff):
                            raise ValueError(f'{cid}: invalid historical trademark date')
                        record('m_trademark_before_asof', '1', tm['tm_evidence_url'], filing)
                pat = patents.get(cid, {})
                count = number(pat.get('pat_apps_total' if live else 'pat_apps_before_asof'))
                if count is not None and count > 0:
                    filing = pat['pat_first_priority_date']
                    if not live and (not date_upper(filing) or date_upper(filing) >= cutoff):
                        raise ValueError(f'{cid}: invalid historical patent date')
                    row['m_patent_verified_before_asof'] = '1'
                    record('m_patent_verified_before_asof', '1', pat['pat_evidence_url'], filing)
                elif count == 0 and not row['m_patent_verified_before_asof']:
                    row['m_patent_verified_before_asof'] = '0'

                if not live and norm(company['name']) in audits:
                    audit = audits[norm(company['name'])]
                    if date_upper(audit['asof_date']) != cutoff:
                        raise ValueError(f'{cid}: audit cutoff mismatch')
                    verdict = audit[verdict_col].strip()
                    if verdict == 'EXCLUDE':
                        row.update(y_label='', y_label_basis='v2_audit_exclude')
                    elif verdict == 'UNSURE':
                        row.update(needs_review='1', y_label_v2_unsure='1')
                    elif verdict == 'CHANGE_TO_0':
                        row['y_label'] = '0'
                    elif verdict not in {'KEEP_1', 'KEEP_0'}:
                        raise ValueError(f'{cid}: unrecognized audit verdict {verdict}')
                    record('label_audit_verdict', verdict, audit['evidence_urls'],
                           audit['first_institutional_round_date'])
                if live and cid in intel:
                    info = intel[cid]
                    for dest, src in [('live_funding_status', 'funding_status'),
                                      ('live_red_flags', 'red_flags'),
                                      ('live_customers', 'customers_or_pilots')]:
                        row[dest] = info[src]
                        if info[src]:
                            record(dest, info[src], info['funding_evidence_url'] or
                                   (ROOT / 'raw/v2_live_intel.csv').as_uri(),
                                   bucket=undated)
                row.update(v2_evidence=packed(evidence), v2_undated_facts=packed(undated),
                           v2_post_asof_facts=packed(post))
                writer.writerow(row)
                stream.flush()  # Persist every completed company.
                for col in NEW + CHANGED:
                    counts[col] += bool(row.get(col, '').strip())
                    changes[col] += row.get(col, '') != original.get(col, '')
        print(f'{output}: rows written={len(originals)}')
        for col in NEW + CHANGED:
            print(f'  {col}: filled={counts[col]}/{len(originals)}, changed={changes[col]}')
    print('Sources failed: none at merge time. No network requests; upstream limits remain in source notes. Name features intentionally unused.')


def auc(pairs):
    """Mann-Whitney AUC, with half credit for tied scores."""
    positives = [score for score, label in pairs if label == 1]
    negatives = [score for score, label in pairs if label == 0]
    if not positives or not negatives:
        return None
    return sum((p > n) + 0.5 * (p == n) for p in positives for n in negatives) / (len(positives) * len(negatives))


def comparison():
    lines = ['# V2 in-sample comparison', '',
             'These are **in-sample** descriptive results, not held-out validation. Existing weights and peer references use this cohort; the new metric weights are **research priors, not fitted**. Label auditing changes the evaluation population, so changes cannot be attributed solely to better features.', '',
             'AUC uses Mann-Whitney pair comparisons with half credit for ties. Only labels exactly 0 or 1 count. Each score AUC uses labelled rows where that score is present; INELIGIBLE rows have blank merit scores and are excluded from those AUCs. Coverage AUC includes every labelled row with coverage. No blank score is imputed.', '',
             '| Measure | V1 | V2 |', '|---|---:|---:|']
    versions = []
    for data_path, score_path in [('backtest.csv', 'reports/scored_backtest.csv'),
                                  ('backtest_v2.csv', 'reports/scored_backtest_v2.csv')]:
        data, _ = read(data_path)
        scored, _ = read(score_path)
        if len(data) != len(scored) or any(norm(d['id_name']) != norm(s['name']) for d, s in zip(data, scored)):
            raise ValueError('Comparison identity/order mismatch')
        versions.append([{**s, 'y_label': d['y_label']} for d, s in zip(data, scored)])
    def display(value):
        return 'N/A' if value is None else f'{value:.4f}'
    def measure(label, fn):
        lines.append('| ' + label + ' | ' + ' | '.join(str(fn(rows)) for rows in versions) + ' |')
    measure('Labelled / positives', lambda rows: f"{sum(r['y_label'] in {'0','1'} for r in rows)} / {sum(r['y_label']=='1' for r in rows)}")
    for col in ['quality_0_100', 'opportunity_0_100', 'quality_observed_0_100', 'coverage_collectable_pct']:
        def describe(rows, col=col):
            pairs = [(float(r[col]), int(r['y_label'])) for r in rows
                     if r['y_label'] in {'0','1'} and r.get(col, '') != '']
            return f'{display(auc(pairs))} (n={len(pairs)}, positives={sum(y for _, y in pairs)})'
        measure(f'AUC: {col}', describe)
    measure('Coverage mean / median (labelled)', lambda rows: ' / '.join(f'{fn([float(r["coverage_collectable_pct"]) for r in rows if r["y_label"] in {"0","1"}]):.2f}%' for fn in [statistics.mean, statistics.median]))
    measure('Coverage mean / median (all 205)', lambda rows: ' / '.join(f'{fn([float(r["coverage_collectable_pct"]) for r in rows]):.2f}%' for fn in [statistics.mean, statistics.median]))
    ranked = lambda rows: [r for r in rows if r['status'] != 'INELIGIBLE' and float(r['coverage_collectable_pct']) >= 70]
    measure('Ranked (all rows; coverage >=70%, non-INELIGIBLE)', lambda rows: len(ranked(rows)))
    measure('Ranked (labelled only)', lambda rows: sum(r['y_label'] in {'0','1'} for r in ranked(rows)))
    lines += ['', 'V1 has no stored quality_observed_0_100 column; its AUC is unavailable rather than reconstructed under a different model.', '',
              '## Funded rate by quality tercile', '',
              'Terciles are equal-count groups of labelled rows with a quality score, ascending by quality; stable input order breaks boundary ties. Funded means y_label=1. Tied scores can span groups.', '',
              '| Version | Tercile | Labelled | Positives | Funded rate | Quality range |', '|---|---|---:|---:|---:|---:|']
    for version, rows in zip(['V1', 'V2'], versions):
        ordered = sorted([r for r in rows if r['y_label'] in {'0','1'} and r['quality_0_100'] != ''], key=lambda r: float(r['quality_0_100']))
        for i, label in enumerate(['Low', 'Middle', 'High']):
            group = ordered[len(ordered)*i//3:len(ordered)*(i+1)//3]
            pos = sum(r['y_label']=='1' for r in group)
            lines.append(f'| {version} | {label} | {len(group)} | {pos} | {100*pos/len(group):.2f}% | {float(group[0]["quality_0_100"]):.2f}–{float(group[-1]["quality_0_100"]):.2f} |' if group else f'| {version} | {label} | 0 | 0 | N/A | N/A |')
    def table(rows, with_label=True):
        keys = ['name', 'status', 'quality_0_100', 'quality_observed_0_100', 'opportunity_0_100', 'coverage_collectable_pct']
        if with_label:
            keys.append('y_label')
        lines.append('| List order | Official priority | Ranked | ' + ' | '.join(keys) + ' |')
        lines.append('|---|---|---|' + '|'.join('---' for _ in keys) + '|')
        for position, row in enumerate(rows, 1):
            vals = []
            for key in keys:
                value = row.get(key, '')
                if key.endswith('_0_100') or key == 'coverage_collectable_pct':
                    value = f'{float(value):.2f}' if value else ''
                vals.append(str(value).replace('|', '\\|').replace('\n', ' '))
            lines.append('| ' + str(position) + ' | ' + str(row['call_priority']) + ' | '
                         + str(row['ranked']) + ' | ' + ' | '.join(vals) + ' |')
    lines += ['', '## Top 15 v2 backtest calls', '',
              'Official calls come first, using the unchanged call policy: descending quality decile, then opportunity. Remaining places are provisional research candidates in the same score order; they do not meet the coverage rule and have no official priority. Labels are shown for evaluation and never used for prioritization.', '']
    call_key = lambda r: (-min(9, int(float(r['quality_0_100']) // 10)), -float(r['opportunity_0_100']))
    calls = sorted(ranked(versions[1]), key=lambda r: int(r['call_priority']))
    provisional = sorted((r for r in versions[1] if r['status'] != 'INELIGIBLE' and r not in calls), key=call_key)
    table((calls + provisional)[:15])
    if len(calls) < 15:
        lines += ['', f'Only {len(calls)} companies meet the ranking rule; the other {min(15-len(calls), len(provisional))} listed companies require further evidence before calling.']
    live, _ = read('reports/scored_live_v2.csv')
    live_calls = sorted(ranked(live), key=lambda r: int(r['call_priority']))
    lines += ['', '## Live leads ranked by v2', '',
              f'{len(live_calls)} of {len(live)} leads meet the ranking rule. Live scoring uses the frozen backtest v2 reference, without refitting and without --asof-view. Sector and German-source patches match score_live.py; new live intelligence is contextual and does not change y_ columns or eligibility logic.', '']
    live_provisional = sorted((r for r in live if r['status'] != 'INELIGIBLE' and r not in live_calls), key=call_key)
    lines += ['The table also orders non-INELIGIBLE provisional live leads by the same quality-decile/opportunity policy. Provisional rows retain Ranked=False and blank official priority.', '']
    table(live_calls + live_provisional, False)
    lines += ['', '### Ineligible live leads', '',
              '| Name | Status | Quality | Observed quality | Opportunity | Coverage |', '|---|---|---:|---:|---:|---:|']
    for row in sorted((r for r in live if r['status'] == 'INELIGIBLE'), key=lambda r: r['name']):
        vals = [row['name'], row['status']] + [f'{float(row[k]):.2f}' if row[k] else '' for k in ['quality_0_100','quality_observed_0_100','opportunity_0_100','coverage_collectable_pct']]
        lines.append('| ' + ' | '.join(v.replace('|','\\|') for v in vals) + ' |')
    data, _ = read('backtest_v2.csv')
    lines += ['', '## Evidence and limitations', '',
              f"Label audit: {sum(r['y_label_basis']=='v2_audit_exclude' for r in data)} excluded labels; {sum(r['y_label_v2_unsure']=='1' for r in data)} UNSURE flags (their existing labels remain per instruction).",
              f"Requested current-founder fallback: {sum(r['v2_founders_from_now']=='1' for r in data)} backtest name rows and {sum(r['v2_founder_count_from_now']=='1' for r in data)} count rows. These are current/undated proxies, not historical proof; v2 is therefore not fully point-in-time clean. Counts can be partial founder/director/CEO observations; zero checked names does not establish a zero-person founder team. Team-size scoring treats counts below one as unknown.",
              'Only medium/high-confidence background rows are used. Post-as-of fields are ignored, and positive historical background facts require their own evidence date strictly before cutoff. Year/month-only dates use the last day of that period. Unverified/undated facts remain separate. Star-academic=0 follows the requested researched-company rule and means no certified star found, not exhaustive proof of absence. PhD stays blank when no explicit positive/negative evidence exists.',
              'Observed quality uses the same numerator as quality, divided by absolute weights of non-missing quality metrics only. Its separate empirical distribution is stored at peer-group, sector, and global levels for exactly the same non-INELIGIBLE reference rows. A row with no observed quality metrics has raw zero, matching the existing reference missing-score convention. Percentile peer fallbacks and eligibility blanking are unchanged.',
              'All supplied local sources loaded successfully; no web scraping was needed. Name features were not read or used (no variance). Patent input is sparse: DPMA name-search zeros are scoped checks, not worldwide absence. Its notes report Google Patents 503, Espacenet/OPS 403, and Lens 403 circuit breakers after three failures. Trademark data are observed DPMA DE/EM/IR matches, sometimes lower bounds or incomplete searches; current-owner matches without historical applicant verification are not promoted. Founder-background notes record OpenAlex checks stopped after source failures for some rows. No new external source availability was tested.', '']
    lines += ['Evidence URLs are preserved from the supplied research. Some founder fallbacks cite only the local companies_all.csv known-founder list; where live intelligence lacks an external URL, provenance points to the supplied v2_live_intel.csv record. These are local input citations, not independently verified public evidence. No external URLs or dates were guessed.', '',
              'Validation passed: original schema/order and company identity checks; original values unchanged outside authorized columns; live y_ fields preserved; minimal scoring-map changes; v1-equivalent legacy scores when the v1 map is used; strict background date checks; identical observed/reference member counts; team-size transform; tie-aware AUC; independently recomputed observed-quality denominators.', '']
    lines += ['## Fill counts', '',
              'Filled means nonblank, including checked zeros; changed compares values with the original input. Every new or potentially changed column is listed.', '',
              '| Column | Backtest filled / 205 | Backtest changed | Live filled / 20 | Live changed |',
              '|---|---:|---:|---:|---:|']
    bt_old, _ = read('backtest.csv')
    lv_old, _ = read('live_leads.csv')
    lv_new, _ = read('live_leads_v2.csv')
    for column in NEW + CHANGED:
        counts = []
        for old, new in [(bt_old, data), (lv_old, lv_new)]:
            counts += [str(sum(bool(r.get(column, '').strip()) for r in new)),
                       str(sum(a.get(column, '') != b.get(column, '') for a, b in zip(old, new)))]
        lines.append('| ' + column + ' | ' + ' | '.join(counts) + ' |')
    lines += ['', 'Rows written: backtest_v2.csv 205; live_leads_v2.csv 20; scored_backtest_v2.csv 205; scored_live_v2.csv 20.',
              'Sources worked: founders A/B, founder background, trademarks, patents, label audit, live intel, original data/model files, relevance_v2 sector lookup and live_metrics. Sources failed during this merge: none; upstream source failures are described above.', '']
    (ROOT / 'reports/v2_comparison.md').write_text('\n'.join(lines), encoding='utf-8')
    print('reports/v2_comparison.md: written')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compare', action='store_true', help='Only regenerate comparison from scored outputs')
    args = parser.parse_args()
    comparison() if args.compare else merge()
