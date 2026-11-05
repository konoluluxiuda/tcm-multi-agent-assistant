"""Replay saved initial drafts against read-only data; never constructs a model."""
import hashlib
import json
from pathlib import Path
import socket
from unittest.mock import patch
from pydantic import ValidationError
from tcm.dataops.requirements_v2 import RequirementsDraftV2
from tcm.dataops.requirements_feedback import preflight
from tcm.dataops.requirements_understanding import correction_fields
from tcm.dataops.research_sources import ReleaseSources
from tcm.dataops.research_contracts import ResearchGoal
from tcm.dataops.baseline import collect, validate_result
from tcm.dataops.artifacts import publish, verify


def main(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    previous = Path('var/requirements_feedback_v1/live_regression_v1/cases')
    summary = {'real_model_requests': 0, 'scope': 'saved initial draft + mechanical planner + real read-only data + XLSX; not a semantic re-review or full scenario score', 'cases': []}
    with patch.object(socket.socket, 'connect', side_effect=AssertionError('No network')), \
            patch.object(socket, 'getaddrinfo', side_effect=AssertionError('No DNS')), \
            ReleaseSources('var/data_releases/r1_names_20261101') as sources:
        catalog = sources.name_catalog()
        for case, number in [('HV2-29',14), ('HV2-32',17)]:
            path = previous / (case+'_W') / f'response-{number}.private.json'
            raw = json.loads(path.read_text())
            args = json.loads(raw['body']['choices'][0]['message']['tool_calls'][0]['function']['arguments'])
            draft = RequirementsDraftV2.model_validate(args)
            prepared, feedback, repairable = preflight(draft, catalog, sources=sources)
            assert prepared['status']=='ready' and repairable
            goal = ResearchGoal.model_validate(prepared['goal'])
            assert goal.document_fields == []
            result = collect(sources, goal.legacy().model_dump())
            assert len(result['rows']) == len(draft.objects)
            checked = validate_result(result)
            folder = root / case
            folder.mkdir()
            artifact = publish(folder, result)
            # publish already performs exact workbook read-back validation.
            files = list(folder.glob('*.xlsx'))
            assert len(files)==1
            verify(files[0], result)
            (folder/'result.private.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
            summary['cases'].append({'scenario':case,'saved_response_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                'mechanical_status':prepared['status'],'attributes':goal.attributes,'document_fields':goal.document_fields,
                'records':len(result['rows']),'validation':checked,'xlsx_sha256':hashlib.sha256(files[0].read_bytes()).hexdigest(),
                'missing_cells':sum(c['value'] is None for r in result['rows'] for c in r['cells'].values())})
            malformed = json.loads((previous/(case+'_W')/f'response-{number+1}.private.json').read_text())
            malformed = json.loads(malformed['body']['choices'][0]['message']['tool_calls'][0]['function']['arguments'])
            try:RequirementsDraftV2.model_validate(malformed)
            except ValidationError as exc:
                summary['cases'][-1]['saved_clarification_rejection']=correction_fields(exc)[0]['code']
            else:raise AssertionError('Malformed saved clarification was silently accepted')
        relation_path=previous/'HV2-20_W/response-11.private.json'
        raw=json.loads(relation_path.read_text())
        draft=RequirementsDraftV2.model_validate_json(raw['body']['choices'][0]['message']['tool_calls'][0]['function']['arguments'])
        _,feedback,repairable=preflight(draft,catalog,sources=sources)
        assert not repairable and feedback['checks'][0]['code']=='full_relation_read_unsupported'
        summary['relation_all_guard']='preserved; saved all request still rejected, new prompt effect not evaluated'
    (root/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',required=True,type=Path)
    main(parser.parse_args().out)
