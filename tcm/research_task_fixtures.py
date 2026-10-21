"""Fictional research-task tools. No corpus files, HTTP, model, or real PMID access."""
from copy import deepcopy
import hashlib
from pathlib import Path

NAMES = ['示例甲草', '示例乙草']
QUESTION = '核对示例甲草和示例乙草的共同靶点。由用户确认来源并选择至多两个靶点，查找各靶点的药材本体研究占位记录，区分成分研究、撤稿及对象不符，交付来源表和证据缺口。所有资料均为合成测试数据。'
PROFILES = ('retry', 'direct', 'ingredient_only', 'withdrawn', 'wrong_object', 'unavailable', 'no_common')


class SyntheticResearchTools:
    data_mode = 'synthetic_only'
    version = 'research-task-fixtures-v1'

    def __init__(self, profile='retry'):
        if profile not in PROFILES:raise ValueError('Unknown synthetic profile')
        self.profile = profile
        self.fingerprint = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

    def sources(self, names):
        return [{'name': name, 'options': (
            [{'id': 'SYN-A1', 'label': '示例甲草来源一（虚构）'},
             {'id': 'SYN-A2', 'label': '示例甲草来源二（虚构，身份未合并）'}]
            if name == NAMES[0] else [{'id': 'SYN-B1', 'label': '示例乙草来源一（虚构）'}])}
                for name in names]

    def common(self, sources):
        if self.profile == 'no_common':return []
        return [{'id': target, 'relation_id': 'REL-'+target,
                 'relation_scope': 'source_declared_common_target_not_mechanism',
                 'witnesses': [{'source_id': source, 'json_pointer': '/synthetic/targets/'+target,
                                'raw_row': {'target': target, 'source': source}} for source in sources]}
                for target in ('SYN-T1','SYN-T2')]

    def search(self, target, variant):
        if self.profile == 'unavailable':return {'status':'unavailable','records':[]}
        if self.profile == 'retry' and variant == 'initial':return {'status':'empty','records':[]}
        prefix = target+'-'
        rows = [self.study(prefix+'H'), self.study(prefix+'I')]
        return {'status':'ok','records':[{'id':r['id'],'title':r['title'],'target_id':r['target_id']} for r in rows]}

    def study(self, identifier):
        target, suffix = identifier.rsplit('-',1)
        herb = suffix == 'H'
        return deepcopy({'id':identifier, 'target_id':target,
            'title':('药材本体' if herb else '单一成分')+'合成研究占位 '+identifier,
            'subject_scope': 'herb' if herb else 'ingredient',
            'herb_source_ids': ['SYN-A1','SYN-B1'] if self.profile != 'wrong_object' else ['SYN-OTHER'],
            'retracted': self.profile == 'withdrawn',
            'subject_scope_override': 'ingredient' if self.profile == 'ingredient_only' else None,
            'evidence_id':'EV-'+identifier, 'source_kind':'fictional_record',
            'source_locator':'synthetic://research-task/'+identifier,
            'text':'这是软件流程测试占位记录，不描述真实药材、实验、疗效或作用机制。'})
