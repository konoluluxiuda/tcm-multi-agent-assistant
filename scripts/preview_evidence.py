"""Launch localhost chat with synthetic AI evidence and optional read-only local research."""
import argparse
import json
import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def preview_environment(directory, *, local_research=False, local_herbs=False,
                        herb_database=None, herb_version='local-2026-10-10'):
    directory = Path(directory).resolve()
    env = {
        'ENABLE_EVIDENCE_ASSISTANT': '1',
        'ENABLE_EVIDENCE_AUTO_ROUTING': '1',
        'EVIDENCE_ASSISTANT_DATA_MODE': 'synthetic',
        'RESEARCH_TOOLS_ONLY': '1',
        'ENABLE_NATURAL_ROUTING': '1',
        'ENABLE_PHARMACOPOEIA_RESEARCH': '1' if local_research else '0',
        'ENABLE_PUBMED': '0',
        'ENABLE_IMAGE_ANALYSIS': '0',
        'ENABLE_SPEECH': '0',
        'ENABLE_WEB_SEARCH': '0',
        'LANGCHAIN_TRACING_V2': 'false',
        'LANGSMITH_TRACING': 'false',
        # Legacy Config constructs an unused embedding client at startup.
        # Keep it independent of real embedding credentials/services in this profile.
        'EMBEDDING_MODEL': 'preview-unused',
        'EMBEDDING_API_KEY': 'preview-unused',
        'EMBEDDING_BASE_URL': 'http://127.0.0.1:9/v1',
        'EMBEDDING_DIM': '1024',
        'CHAT_MEMORY_DB': str(directory / 'memory.sqlite3'),
        'CHAT_COOKIE_NAME': 'tcm_synthetic_preview',
        'TCM_DB': str(directory / 'unavailable-herbs.sqlite3'),
        'PUBMED_DB': str(directory / 'pubmed.sqlite3'),
        'SOURCE_MANIFEST_PATH': str(directory / 'empty-sources.json'),
    }
    if local_research:
        env.update({
            'PHARMACOPOEIA_INDEX': str(ROOT / 'var/mineru_retrieval_v2'),
            'PHARMACOPOEIA_PDF_HIGH': str(ROOT / 'data/《中国药典》2025年版 一部.pdf'),
            'PHARMACOPOEIA_PDF_FULL': str(ROOT / 'data/《中国药典》2025年版 一部 全本.pdf'),
        })
    if local_herbs:
        env['TCM_DB']=str(Path(herb_database or ROOT / 'var/tcm/herbs.sqlite3').resolve())
        env['TCM_DATASET_VERSION']=herb_version
    return env


def prepare(directory, *, local_research=False, local_herbs=False,
            herb_database=None, herb_version='local-2026-10-10'):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    env = preview_environment(directory, local_research=local_research, local_herbs=local_herbs,
                              herb_database=herb_database, herb_version=herb_version)
    if local_herbs:
        if not isinstance(herb_version,str) or not herb_version.strip():
            raise ValueError('Select an explicit ETCM dataset version')
        from tcm.assistant_info import herb_inventory
        try:
            inventory=herb_inventory(env['TCM_DB'],herb_version)
            if inventory['status']!='ok' or inventory['source_record_count']==0:
                raise ValueError('No valid records in selected dataset')
        except (OSError,ValueError,sqlite3.Error,KeyError,TypeError) as error:
            raise ValueError('ETCM database/version is not ready; import or select a valid snapshot first') from error
    elif Path(env['TCM_DB']).exists():
        raise ValueError('Preview requires an absent structured database; choose another --state directory')
    manifest = Path(env['SOURCE_MANIFEST_PATH'])
    empty = {'version': 1, 'sources': {}}
    if manifest.exists():
        if json.loads(manifest.read_text(encoding='utf-8')) != empty:
            raise ValueError('Preview source allowlist must be empty')
    else:
        with manifest.open('x', encoding='utf-8') as output:
            json.dump(empty, output)
    return env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=7875)
    parser.add_argument('--state', type=Path, default=ROOT / 'var/evidence_assistant_v1/preview')
    parser.add_argument('--with-local-research', action='store_true',
                        help='Enable read-only local pharmacopoeia commands; AI evidence stays synthetic')
    parser.add_argument('--with-local-herbs', action='store_true',help='Enable the existing ETCM SQLite snapshot read-only')
    parser.add_argument('--herb-db',type=Path,help='ETCM database; requires --with-local-herbs')
    parser.add_argument('--herb-version',default='local-2026-10-10',help='Local ETCM snapshot version')
    parser.add_argument('--etcm-enrichment',type=Path,help='Optional derived Chinese/relationship catalog; requires local herbs')
    parser.add_argument('--etcm-source',type=Path,help='Optional original ETCM snapshot for bounded ingredient details; requires enrichment')
    parser.add_argument('--etcm-targets',type=Path,help='Optional audited target/ingredient SQLite index; requires enrichment')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('--port must be between 1 and 65535')
    if args.herb_db and not args.with_local_herbs:
        parser.error('--herb-db requires --with-local-herbs')
    if args.etcm_enrichment and not args.with_local_herbs:parser.error('--etcm-enrichment requires --with-local-herbs')
    if args.etcm_source and not args.etcm_enrichment:parser.error('--etcm-source requires --etcm-enrichment')
    if args.etcm_targets and not args.etcm_enrichment:parser.error('--etcm-targets requires --etcm-enrichment')
    os.environ['ETCM_TARGET_DB']=''
    if args.etcm_targets:
        from tcm.target_ingredients import connect as target_connect, check_binding
        target_con, target_meta = target_connect(args.etcm_targets)
        target_con.close()
        check_binding(target_meta, args.etcm_enrichment)
        os.environ['ETCM_TARGET_DB']=str(args.etcm_targets.resolve())
    if args.etcm_source and not all((args.etcm_source/name).is_dir() for name in ('etcm_herbs','etcm_ingredients')):
        parser.error('--etcm-source must contain etcm_herbs and etcm_ingredients')
    os.environ['ETCM_SOURCE_ROOT']=str(args.etcm_source.resolve()) if args.etcm_source else ''
    os.environ.update(prepare(args.state, local_research=args.with_local_research,
                             local_herbs=args.with_local_herbs,herb_database=args.herb_db,
                             herb_version=args.herb_version))
    if args.etcm_enrichment:
        from contextlib import closing
        from tcm.etcm_enrichment import connect
        with closing(connect(args.etcm_enrichment)) as con:
            import hashlib
            metadata={row['key']:json.loads(row['value']) for row in con.execute('SELECT key,value FROM metadata')}
            if (metadata['dataset_version']!=args.herb_version or
                    metadata['base_database_sha256']!=hashlib.sha256(Path(os.environ['TCM_DB']).read_bytes()).hexdigest()):
                raise ValueError('ETCM enrichment does not match selected database/version')
        os.environ['ETCM_ENRICHMENT_DB']=str(args.etcm_enrichment.resolve())
    else:os.environ['ETCM_ENRICHMENT_DB']=''
    os.chdir(ROOT)  # The application resolves templates and .env relative to this root.
    print(f'Source preview: http://127.0.0.1:{args.port}/', flush=True)
    print('AI requests use fictional evidence. Local read-only research: '+('enabled' if args.with_local_research else 'disabled'), flush=True)
    print('Local read-only ETCM: '+('enabled ('+args.herb_version+')' if args.with_local_herbs else 'disabled'),flush=True)
    import uvicorn
    uvicorn.run('app:app', host='127.0.0.1', port=args.port, log_level='warning')


if __name__ == '__main__':
    main()
