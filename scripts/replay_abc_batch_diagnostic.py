"""Replay recorded first rejected tool batches locally; no model continuation."""
import argparse
import asyncio
import json
import os
from pathlib import Path
for key in ('LANGCHAIN_TRACING', 'LANGCHAIN_TRACING_V2', 'LANGSMITH_TRACING'):
    os.environ[key] = 'false'
from scripts.check_pair_protocol import synthetic_fixture, backend_for
from experiments.architecture_candidates.pair_protocol import PairProtocol, drivers


async def diagnose(record):
    results = []
    with synthetic_fixture() as fixture:
        for run in record['results']:
            if run['candidate'] not in ('A_protocol', 'B_deepagents', 'C_protocol') or run.get('reason') != 'invalid_action':
                continue
            actions = run['model_observations'][0]['tool_calls']
            protocol = PairProtocol(('Alpha','Beta'), '功能与主治、注意事项和全部共同靶点', backend_for(fixture))
            _, children = drivers('C_protocol')
            role = 'supervisor' if run['candidate'] == 'C_protocol' else 'flat'
            protocol.admit_batch(actions, role)
            admitted = []
            for call in actions:
                await protocol.action(call['name'], call['args'], role, children)
                admitted.append({'tool':call['name'], 'arguments':call['args']})
            results.append({'candidate':run['candidate'], 'first_response_actions':len(actions),
                'serial_replay_admitted':admitted, 'local_tool_calls':protocol.tools,
                'child_sdk_substitute_calls':protocol.models, 'full_task_replayed':False})
    return {'status':'passed','external_model_calls':0,'results':results,
            'limitation':'Only first-response actions replayed serially; not a repaired model rerun or concurrency test'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--record',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    report = asyncio.run(diagnose(json.loads(args.record.read_text())))
    with args.output.open('x') as stream:
        stream.write(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status':report['status'],'external_model_calls':0}))


if __name__ == '__main__':
    main()
