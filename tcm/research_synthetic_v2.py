"""Versioned synthetic fixtures matching the public disease tool contract.

The original v1 fixture remains unchanged so the recorded live failure can be
reproduced. These rows are invented, never extracted from a real database.
"""
from tcm.research_run_demo import SID, synthetic_tool as legacy_tool

VERSION = 'synthetic-v2-contract-aligned'
NAME = 'Synthetic disease'


async def synthetic_tool(name, args, *, missing_rows=False):
    args = dict(args)
    if name == 'disease_gene_profile':
        # Like the local catalog: accept a unique exact source name or its ID.
        if args['name'] not in (SID, NAME):
            raise ValueError('Unknown synthetic disease source')
        if args.get('page', 1) != 1:
            raise ValueError('Synthetic fixture has one page')
        args['name'] = SID
    elif name == 'search_disease_sources':
        if args.get('page', 1) != 1:
            raise ValueError('Synthetic fixture has one page')
    else:
        raise ValueError('Unknown synthetic tool')
    result = await legacy_tool(name, args)
    data = result['data']
    data['dataset_version'] = VERSION
    if name == 'search_disease_sources':
        data['page_size'] = args.get('page_size', 20)
        # Unknown names must not resolve to a fabricated source.
        if args['name'] not in ('', NAME):
            data.update(total=0, items=[])
            result['status'] = 'empty'
    else:
        data['profile'].update(target_row_count=0 if missing_rows else 1,
                               species_status='synthetic',
                               detail_status='missing' if missing_rows else 'available')
        if not missing_rows:
            data['rows'] = [{'uniprot':'P12345', 'gene':'SYNTHETIC_GENE',
                             'json_pointer':'/synthetic/targets/0',
                             'raw_row':{'UniProt':'P12345','Gene':'SYNTHETIC_GENE',
                                        'synthetic':True}}]
    return result
