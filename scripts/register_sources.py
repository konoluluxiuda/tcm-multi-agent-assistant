"""Publish only explicitly selected PDFs and optional corresponding RAG pictures.

Writes metadata only. Choosing files here is the administrator's publishing act.
"""
import argparse
import hashlib
import json
from pathlib import Path

from agents.source_links import local_source_path, source_id
from source_access import MAX_SOURCE_BYTES, file_asset


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('.'))
    parser.add_argument('--documents', nargs='+', required=True)
    parser.add_argument('--include-derived-images', action='store_true')
    parser.add_argument('--out', type=Path, default=Path('resources/source_manifest.json'))
    args = parser.parse_args()
    root = args.root.resolve()
    chosen = set()
    for value in args.documents:
        path = local_source_path(value)
        if not path or Path(path).suffix.lower() != '.pdf' or Path(path).parts[1] not in ('raw','raw_extras'):
            parser.error('Select a PDF under data/raw or data/raw_extras')
        chosen.add(path)
        if args.include_derived_images:
            for picture in (root/'data/parsed_docs').glob(Path(path).stem + '-picture-*.png'):
                tail = picture.stem.rsplit('-picture-', 1)[-1]
                if tail.isdecimal(): chosen.add(picture.relative_to(root).as_posix())
    entries = {}
    for path in sorted(chosen):
        file = root/path
        if file.resolve() != file or not file.is_file():parser.error('Source is missing or symlinked: ' + path)
        if file.stat().st_size > MAX_SOURCE_BYTES:parser.error("Source exceeds size limit: " + path)
        digest = hashlib.sha256(file.read_bytes()).hexdigest()
        if file_asset(root, path, digest) is None:parser.error('Unsupported source bytes: ' + path)
        entries[source_id(path)] = {'path':path, 'sha256':digest}
    if args.out.resolve() in {(root/p).resolve() for p in chosen}:
        parser.error('Manifest must not overwrite a source')
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps({'version':1,'sources':entries},ensure_ascii=False,indent=2)+'\n')
    print('Registered source files:',len(entries))


if __name__ == '__main__':
    main()
