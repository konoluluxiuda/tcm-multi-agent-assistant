"""Read a generated unreviewed candidate card; no models, database writes or web calls."""
import argparse
import json
from pathlib import Path
from tcm.pharmacopoeia import query_candidates

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('name');p.add_argument('field')
    p.add_argument('--data',type=Path,default=Path('var/pharmacopoeia_candidates/candidates.json'))
    a=p.parse_args()
    print(json.dumps(query_candidates(json.loads(a.data.read_text()),a.name,a.field),ensure_ascii=False,indent=2))
