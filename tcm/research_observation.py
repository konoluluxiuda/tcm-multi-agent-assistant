"""Business result identity and page consistency checks for the shared loop."""
import json
from tcm.research_business_tools import SPECS,REPORT_TOOLS
from tcm.task_tools import ToolResult


def validate(name, args, raw, evidence, *, max_bytes=48000):
    if name in REPORT_TOOLS:
        from tcm.research_report_tools import validate as validate_report
        payload=validate_report(name,args,raw)
        if len(json.dumps(payload,ensure_ascii=False).encode())>max_bytes:raise ValueError('Observation exceeds context budget')
        return payload
    result = ToolResult[SPECS[name][1]].model_validate(raw)
    if result.tool != name:
        raise ValueError('Tool name mismatch')
    if result.status != 'error':
        data = SPECS[name][1].model_validate(result.data).model_dump()
        if name == 'search_disease_sources':
            if any(data[k] != args[a] for k, a in (('query', 'name'), ('page', 'page'), ('page_size', 'page_size'))):
                raise ValueError('Search request mismatch')
            empty = not data['items']
        elif name in ('disease_gene_profile', 'rank_herbs_for_gene_set'):
            if ((args['name'].startswith('etcm-disease:') and data['record_id'] != args['name'])
                    or data['page'] != args.get('page', 1)):
                raise ValueError('Disease request mismatch')
            empty = not data['target_ids'] or name == 'rank_herbs_for_gene_set' and not data['items']
            if name == 'disease_gene_profile':
                total_rows = data['profile'].get('target_row_count')
                if type(total_rows) is int:
                    expected = min(20, max(0, total_rows-(data['page']-1)*20))
                    if len(data['rows']) != expected or data['has_next'] != (data['page']*20 < total_rows):
                        raise ValueError('Original row coverage mismatch')
        else:
            if args['name'].startswith('etcm2-local:') and data['profile']['source']['source_record_id'] != args['name']:
                raise ValueError('Herb identity mismatch')
            empty = not data['profile']['target_count'] or name == 'herb_target_similarity' and not data['items']
        if name in ('herb_target_similarity', 'rank_herbs_for_gene_set'):
            if any(data[k] != args[k] for k in ('metric', 'top_k')):
                raise ValueError('Ranking request mismatch')
        if (result.status == 'empty') != bool(empty):
            raise ValueError('Invalid empty status')
        for old in evidence.values():
            if old.get('status') not in ('ok', 'empty') or old.get('tool') != name:
                continue
            previous = old['data']
            if any(previous[k] != data[k] for k in ('dataset_version', 'catalog_revision')):
                raise ValueError('Source version changed')
            if name == 'disease_gene_profile' and previous['record_id'] == data['record_id']:
                if previous['target_ids'] != data['target_ids'] or previous['profile'] != data['profile']:
                    raise ValueError('Profile changed between pages')
                old_rows = {r.get('json_pointer') for r in previous['rows'] if r.get('json_pointer')}
                if any(r.get('json_pointer') in old_rows for r in data['rows']):
                    raise ValueError('Original rows overlap between pages')
        # Disease search/profile/ranking have distinct revision algorithms. Bind
        # their common dataset version here; local transport pins all DB files.
        if name in ('disease_gene_profile', 'rank_herbs_for_gene_set'):
            for old in evidence.values():
                if old.get('status') not in ('ok', 'empty') or old.get('tool') != 'search_disease_sources':
                    continue
                for item in old['data']['items']:
                    if item['record_id'] == data['record_id']:
                        if old['data']['dataset_version'] != data['dataset_version'] or item['target_count'] != len(data['target_ids']):
                            raise ValueError('Discovery and profile differ')
    payload = result.model_dump()
    if len(json.dumps(payload,ensure_ascii=False).encode()) > max_bytes:
        raise ValueError('Observation exceeds context budget')
    return payload
