"""Research-only correction overlays; raw OCR and source offsets are immutable."""
from copy import deepcopy
import hashlib
import json

from tcm.mineru_pharmacopoeia import segment_text, source_anchor


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def entry_identity(entry):
    return {k: entry[k] for k in ('id', 'name_zh_candidate', 'title_block_ids', 'chapter_pages')}


def rebase_entries(old_entries, old_blocks, new_entries, new_blocks):
    """Match immutable source title anchors, never sequential IDs or drug names."""
    by_anchor = {}
    for entry in new_entries:
        key = fingerprint(source_anchor(entry, new_blocks))
        if key in by_anchor:
            raise ValueError('Ambiguous new source anchor')
        by_anchor[key] = entry
    result = {}
    for old in old_entries:
        key = fingerprint(source_anchor(old, old_blocks))
        if key not in by_anchor:
            raise ValueError('Old source title has no new match')
        new = by_anchor[key]
        if (old['name_zh_candidate'] != new['name_zh_candidate']
                or old['title_block_ids'] != new['title_block_ids']
                or any(old_blocks[k]['raw'] != new_blocks[k]['raw'] for k in old['title_block_ids'])):
            raise ValueError('Title evidence changed during rebase')
        if new['id'] in result.values():
            raise ValueError('Multiple old entries map to one new title')
        result[old['id']] = new['id']
    return result


def rebased_target(proposal, old_entry, old_blocks, new_entry, new_blocks):
    old = target_for(proposal, old_entry, old_blocks)
    new = target_for(proposal, new_entry, new_blocks)
    if old['kind'] != new['kind'] or old['before'] != new['before'] or old['after'] != new['after']:
        raise ValueError('Revision meaning changed during rebase')
    if old['kind'] == 'replace_span':
        for key in ('segment', 'start', 'end'):
            if old[key] != new[key]:
                raise ValueError('Revision source span moved during rebase')
        block_id = old['segment']['block_id']
        if (old_blocks[block_id]['raw'] != new_blocks[block_id]['raw']
                or old_blocks[block_id]['source'] != new_blocks[block_id]['source']):
            raise ValueError('Revision source block changed during rebase')
    return new


def field_text(field, blocks):
    return '\n'.join(segment_text(s, blocks) if 'start' in s else blocks[s['block_id']]['raw']['text']
                     for s in field['segments']
                     if 'start' in s or blocks[s['block_id']]['raw']['type'] == 'equation')


def target_for(proposal, entry, blocks):
    """Resolve a proposal once, rejecting ambiguous or changed observations."""
    kind = proposal['target']
    if kind == 'name_zh_candidate':
        if entry['name_zh_candidate'] != proposal['observed']:
            raise ValueError('Name observation changed')
        return {'kind': 'replace_name', 'before': proposal['observed'],
                'after': proposal['proposed_source_reading']}
    if kind == 'omitted_title_lines':
        additions = {}
        for source, flag, key in [('proposed_pinyin_reading', 'pinyin_title_not_observed', 'pinyin'),
                                  ('proposed_latin_reading', 'latin_title_not_observed', 'latin')]:
            value = proposal.get(source)
            if value is not None:
                if flag not in entry['title_flags']:
                    raise ValueError('Title line is no longer missing')
                additions[key] = value
        if not additions:
            raise ValueError('Empty title addition')
        return {'kind': 'add_title_lines', 'before': None, 'after': additions}
    label = 'origin' if kind == 'origin_text' else kind
    fields = [(i, f) for i, f in enumerate(entry['fields']) if f['label'] == label
              and (kind != 'origin_text' or f['scope'] == 'monograph')]
    matches = []
    needle = proposal['observed']
    if not isinstance(needle, str) or not needle:
        raise ValueError('Empty replacement observation')
    for field_index, field in fields:
        for segment_index, segment in enumerate(field['segments']):
            if 'start' not in segment:
                continue
            text = segment_text(segment, blocks)
            start = 0
            while (position := text.find(needle, start)) != -1:
                matches.append({'field_index': field_index, 'segment_index': segment_index,
                                'segment': deepcopy(segment), 'start': segment['start'] + position,
                                'end': segment['start'] + position + len(needle)})
                start = position + 1
    if len(matches) != 1:
        raise ValueError('Field replacement must match exactly one source span')
    return {'kind': 'replace_span', **matches[0], 'before': needle,
            'after': proposal['proposed_source_reading']}


def citation(segment, blocks):
    block = blocks[segment['block_id']]
    result = {'block_id': block['id'], 'chapter_page': block['chapter_page'],
              'source': deepcopy(block['source']), 'bbox_0_1000': block['raw']['bbox'],
              'asset_paths': block.get('asset_paths', []), 'block_type': block['raw']['type']}
    if 'start' in segment:
        segment_text(segment, blocks)  # Validate offsets even when only emitting evidence.
        result.update({k: segment[k] for k in ('start', 'end', 'text_source', 'item_index') if k in segment})
        if 'text_source' in segment:
            matches = [ref for ref in block.get('caption_evidence', [])
                       if ref['text_source'] == segment['text_source'] and ref['item_index'] == segment['item_index']]
            if len(matches) != 1:
                raise ValueError('Caption citation geometry is not unique')
            result['bbox_0_1000'] = matches[0]['bbox_0_1000']
            result['layout_sha256'] = matches[0]['layout_sha256']
    return result


def render_view(parsed, blocks, ledger, entries_sha, blocks_sha):
    """Apply explicitly enabled AI revisions to a separate view, never approval state."""
    if ledger['entries_sha256'] != entries_sha or ledger['blocks_sha256'] != blocks_sha:
        raise ValueError('Revision ledger targets a different corpus')
    if ledger.get('human_signoff') is not False:
        raise ValueError('This renderer only handles AI research revisions')
    originals = {e['id']: e for e in parsed['entries']}
    if len(originals) != len(parsed['entries']):
        raise ValueError('Duplicate entry IDs')
    overlay_ids = set()
    by_entry = {}
    occupied = {}
    for overlay in ledger['overlays']:
        if overlay['id'] in overlay_ids:
            raise ValueError('Duplicate overlay ID')
        overlay_ids.add(overlay['id'])
        entry = originals[overlay['entry_id']]
        if overlay['entry_identity_sha256'] != fingerprint(entry_identity(entry)):
            raise ValueError('Revision entry identity changed')
        if overlay.get('human_signoff') is not False or overlay.get('review_status') != 'ai_proposed_pending_human_review':
            raise ValueError('Invalid AI overlay review status')
        if type(overlay.get('enabled')) is not bool:
            raise ValueError('Overlay enabled must be boolean')
        target = overlay['target']
        # Re-resolve against the original evidence, so edited span offsets cannot drift.
        if target != target_for(overlay['proposal'], entry, blocks):
            raise ValueError('Overlay target no longer matches original evidence')
        if not overlay['enabled']:
            continue
        kind = target['kind']
        key = (entry['id'], kind, target.get('field_index'), target.get('segment_index'))
        if kind == 'replace_span':
            span = (target['start'], target['end'])
            if any(a < span[1] and span[0] < b for a, b in occupied.get(key, [])):
                raise ValueError('Overlapping source replacements')
            occupied.setdefault(key, []).append(span)
        elif key in occupied:
            raise ValueError('Conflicting title/name revisions')
        else:
            occupied[key] = []
        by_entry.setdefault(entry['id'], []).append(overlay)
    views = []
    for entry in parsed['entries']:
        overlays = by_entry.get(entry['id'], [])
        view = {'id': entry['id'], 'raw_name_zh_candidate': entry['name_zh_candidate'],
                'name_zh_candidate': entry['name_zh_candidate'], 'chapter_pages': entry['chapter_pages'],
                'title_evidence': [citation({'block_id': k}, blocks) for k in entry['title_block_ids']],
                'title_additions': {}, 'fields': [], 'raw_flags': list(entry['flags']),
                'applied_overlay_ids': [o['id'] for o in overlays], 'human_signoff': False,
                'review_status': 'ai_revision_pending_human_review' if overlays else entry['review_status'],
                'production_eligible': False, 'identity_mapping_verified': False}
        for overlay in overlays:
            target = overlay['target']
            if target['kind'] == 'replace_name':
                view['name_zh_candidate'] = target['after']
            elif target['kind'] == 'add_title_lines':
                view['title_additions'] = {'values': target['after'], 'overlay_id': overlay['id'],
                                           'evidence': overlay['evidence'], 'status': overlay['review_status']}
        for i, field in enumerate(entry['fields']):
            raw_text = field_text(field, blocks)
            values = []
            for j, segment in enumerate(field['segments']):
                if 'start' not in segment:
                    if blocks[segment['block_id']]['raw']['type'] == 'equation':
                        values.append(blocks[segment['block_id']]['raw']['text'])
                    continue
                text = segment_text(segment, blocks)
                patches = [o['target'] for o in overlays if o['target']['kind'] == 'replace_span'
                           and o['target']['field_index'] == i and o['target']['segment_index'] == j]
                for patch in sorted(patches, key=lambda p: p['start'], reverse=True):
                    start, end = patch['start'] - segment['start'], patch['end'] - segment['start']
                    if text[start:end] != patch['before']:
                        raise ValueError('Source observation changed during rendering')
                    text = text[:start] + patch['after'] + text[end:]
                values.append(text)
            view['fields'].append({'scope': field['scope'], 'label': field['label'],
                                   'raw_text': raw_text, 'text': '\n'.join(values),
                                   'evidence': [citation(s, blocks) for s in field['segments']],
                                   'overlay_ids': [o['id'] for o in overlays
                                                   if o['target']['kind'] == 'replace_span'
                                                   and o['target']['field_index'] == i]})
        views.append(view)
    return {'kind': 'mineru_research_view', 'entries_sha256': entries_sha,
            'blocks_sha256': blocks_sha, 'ledger_sha256': fingerprint(ledger),
            'human_signoff': False, 'production_eligible': False, 'entries': views}
