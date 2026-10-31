"""One native planning request followed by host execution, for a future authorized P pilot."""
import hashlib
from tcm.dataops.data_access import canonical
from tcm.dataops.evaluation import FixedPlanDriver

STAGES=('query_records','inspect_sources','lookup_document_fields','build_artifact','verify_delivery','finish_task')
PLAN_TOOL={'type':'function','function':{'name':'set_plan','description':'一次性提交按顺序执行的资料核对阶段。',
    'parameters':{'type':'object','properties':{'steps':{'type':'array','minItems':6,'maxItems':7,
        'items':{'type':'string','enum':[*STAGES,'compare_fields']}}},'required':['steps'],'additionalProperties':False}}}


def parse_plan(reply):
    if len(reply.tool_calls)!=1 or reply.tool_calls[0]['name']!='set_plan':
        raise ValueError('Exactly one set_plan action is required')
    args=reply.tool_calls[0]['args']
    if set(args)!={'steps'} or not isinstance(args['steps'],list):raise ValueError('Invalid plan schema')
    steps=args['steps']
    if (not 6<=len(steps)<=7 or len(set(steps))!=len(steps) or
        set(steps)-set((*STAGES,'compare_fields')) or any(x not in steps for x in STAGES) or
        [x for x in steps if x in STAGES]!=list(STAGES)):
        raise ValueError('Plan must preserve prerequisite and delivery stages')
    if 'compare_fields' in steps and not steps.index('lookup_document_fields')<steps.index('compare_fields')<steps.index('build_artifact'):
        raise ValueError('Comparison must follow reads and precede delivery')
    return steps


def request_plan(chat_model, goal, question, *, authorized=False):
    if not authorized:raise PermissionError('Synthetic-provider planning requires explicit authorization')
    if getattr(chat_model,'max_retries',None)!=0 or not 0<(getattr(chat_model,'max_tokens',None) or 0)<=1200:
        raise ValueError('Configure retries=0 and output<=1200 tokens')
    model=chat_model.bind_tools([PLAN_TOOL],parallel_tool_calls=False,tool_choice='required')
    messages=[{'role':'system','content':'为资料核对任务制定一次性计划，只调用set_plan。必须按依赖顺序保留查询、'
        '来源核对、字段读取、构建、验证、结束；可在读取后增加比较。缓存、暂时读取失败和人工候选选择由宿主处理，'
        '不要虚构来源ID；没有工具观察供你重规划。'},
        {'role':'user','content':canonical({'original_question':question,'goal':goal})}]
    from tcm.tracing import observation
    with observation('dataops.plan', generation=True, model=getattr(chat_model, 'model_name', None),
                     input={'messages': messages, 'tools': [PLAN_TOOL]},
                     metadata={'strategy': 'P', 'provider_request_attempts': 1}) as trace:
        reply=model.invoke(messages)
        usage=reply.usage_metadata or {}
        trace.update(usage_details={k: v for k, v in usage.items()
                                  if k in ('input_tokens', 'output_tokens', 'total_tokens') and type(v) is int} or None)
        steps=parse_plan(reply)
        trace.update(output={'steps': steps})
        return steps,usage


class NativePlanDriver(FixedPlanDriver):
    def __init__(self, steps):
        # Use the common mechanical guards; a valid plan cannot relax the goal.
        self.plan=tuple(steps)
        self.label='native_P_once_plan:'+hashlib.sha256(canonical(steps).encode()).hexdigest()[:16]

    def decide(self, context):
        if context['ambiguities']:return super().decide(context)
        needed={'query_records':not context['selected'],'inspect_sources':bool(context['uninspected']),
            'lookup_document_fields':bool(context['unread_fields']),'build_artifact':not context['artifact'],
            'verify_delivery':not context['verified'],'finish_task':True,
            'compare_fields':not any(s['tool']=='compare_fields' for s in context['plan'])}
        tool=next(tool for tool in self.plan if needed[tool])
        return {'tool':tool,'arguments':{'purpose':'执行一次性模型计划；后续观察不再交给规划模型'}}
