"""Create standalone read-only deployment snapshots; never chmod source catalogs."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from tcm.etcm_enrichment import connect
from tcm.target_ingredients import connect as target_connect, check_binding
from tcm.mineru_pharmacopoeia import digest

ROOT = Path(__file__).resolve().parents[1]


def metadata(con):
    return {r[0]: json.loads(r[1]) for r in con.execute('SELECT key,value FROM metadata')}


def prepare(enrichment, targets, output):
    if output.exists():
        raise FileExistsError('Use a fresh runtime snapshot directory')
    with closing(connect(enrichment)) as source, closing(target_connect(targets)[0]) as target:
        source_meta, target_meta = metadata(source), metadata(target)
        check_binding(target_meta, enrichment)
        output.mkdir(parents=True, mode=0o700)
        output.chmod(0o700)
        runtime = output / 'catalogs'
        runtime.mkdir(mode=0o755)
        runtime.chmod(0o755)  # Bind only this child: host-private parent remains non-searchable.
        receipt = {'kind': 'etcm_runtime_snapshot_v1', 'dataset_version': source_meta['dataset_version'],
                   'herb_source_fingerprint': source_meta['source_fingerprint'],
                   'target_export_sha256': target_meta['export_sha256'], 'files': {},
                   'source_permissions_changed': False, 'runtime_subdirectory': 'catalogs'}
        for con, filename in ((source, 'enrichment.sqlite3'), (target, 'targets-paths.sqlite3')):
            path = runtime / filename
            with closing(sqlite3.connect(path)) as destination:
                con.backup(destination)
                if destination.execute('PRAGMA journal_mode=DELETE').fetchone()[0] != 'delete':
                    raise ValueError('Cannot materialize standalone snapshot')
                if destination.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('Snapshot integrity check failed')
            path.chmod(0o644)
            receipt['files'][filename] = {'sha256': digest(path), 'bytes': path.stat().st_size}
        if metadata(source) != source_meta or metadata(target) != target_meta:
            raise ValueError('Source metadata changed during snapshot; use another fresh directory')
        with closing(connect(runtime / 'enrichment.sqlite3')) as copied:
            if metadata(copied) != source_meta:raise ValueError('Copied herb metadata differs')
        con, copied_meta = target_connect(runtime / 'targets-paths.sqlite3')
        con.close();check_binding(copied_meta, runtime / 'enrichment.sqlite3')
        if copied_meta != target_meta:raise ValueError('Copied target metadata differs')
        (output / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
        return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--enrichment', type=Path, default=ROOT / 'var/etcm_enrichment_v1/data/enrichment.sqlite3')
    parser.add_argument('--targets', type=Path, default=ROOT / 'var/etcm_target_links_v1/targets-paths.sqlite3')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.enrichment, args.targets, args.out), ensure_ascii=False))


if __name__ == '__main__':main()
