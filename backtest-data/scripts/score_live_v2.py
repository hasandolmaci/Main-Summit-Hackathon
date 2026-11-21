"""Score the merged live cohort using frozen v2 reference and v1 live patches.

Does not rebuild live_leads.csv, change y_ fields, or refit a reference.
Extra --map/--reference arguments can be passed through to the v2 scorer.
"""
import csv
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import eligibility
import peer_scoring
import score_startups_v2

SECTOR = {'software/ai': 'digital_tech', 'hardware/deeptech': 'deep_tech',
          'health': 'life_science', 'energy/climate': 'deep_tech', 'other': 'unclear'}


def main():
    with (ROOT / 'raw/live_metrics.csv').open(encoding='utf-8-sig', newline='') as stream:
        metrics = list(csv.DictReader(stream))
    sectors = {
        peer_scoring.normalise_name(row['name']):
            'non_tech' if row.get('is_consulting_or_agency') == '1'
            else SECTOR.get(row.get('sector', ''), 'unclear')
        for row in metrics
    }
    old_programmes, old_lookup = eligibility.GERMAN_PROGRAMMES, peer_scoring.sector_lookup
    old_argv = sys.argv
    try:
        eligibility.GERMAN_PROGRAMMES = old_programmes | {'linkedin_live_de'}
        peer_scoring.sector_lookup = lambda *a, **k: sectors
        sys.argv = ['score_startups_v2.py', '--in', str(ROOT / 'live_leads_v2.csv'),
                    '--out', str(ROOT / 'reports/scored_live_v2.csv'), *old_argv[1:]]
        if '--fit-reference' in sys.argv or '--asof-view' in sys.argv:
            raise ValueError('Live scoring must not refit or use historical eligibility')
        score_startups_v2.main()
    finally:
        eligibility.GERMAN_PROGRAMMES, peer_scoring.sector_lookup = old_programmes, old_lookup
        sys.argv = old_argv


if __name__ == '__main__':
    main()
