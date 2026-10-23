"""Registered read-only analyses shared by rule and model-selected chat calls."""
from typing import Annotated, Literal
from time import perf_counter
import os
import re
import sqlite3

from pydantic import Field, TypeAdapter, ValidationError, model_validator
from tcm.task_tools import Contract, ToolResult, ToolError
from tcm import target_similarity


class ProfileArgs(Contract):
    name: str = Field(min_length=1,max_length=100)

    @model_validator(mode='after')
    def complete_name(self):
        if self.name!=self.name.strip() or re.search(r'[\n\r]',self.name):raise ValueError('Invalid name')
        return self


class SimilarityArgs(ProfileArgs):
    metric: Literal['jaccard','common_count'] = 'jaccard'
    top_k: int = Field(default=10,ge=1,le=20)


class ProfileCall(Contract):
    tool: Literal['herb_target_profile']
    arguments: ProfileArgs


class SimilarityCall(Contract):
    tool: Literal['herb_target_similarity']
    arguments: SimilarityArgs


class DiseaseArgs(ProfileArgs):
    name: str = Field(min_length=1,max_length=300)
    page: int = Field(default=1,ge=1,le=10000)


class DiseaseRankingArgs(ProfileArgs):
    name: str = Field(min_length=1,max_length=300)
    metric: Literal['jaccard','common_count'] = 'jaccard'
    top_k: int = Field(default=10,ge=1,le=20)


class DiseaseCall(Contract):
    tool: Literal['disease_gene_profile']
    arguments: DiseaseArgs


class DiseaseRankingCall(Contract):
    tool: Literal['rank_herbs_for_gene_set']
    arguments: DiseaseRankingArgs


class DiseaseSearchArgs(Contract):
    name: str = Field(default='',max_length=100)
    page: int = Field(default=1,ge=1,le=10000)

    @model_validator(mode='after')
    def bounded_term(self):
        if self.name!=self.name.strip() or re.search(r'[\n\r]',self.name):raise ValueError('Invalid search term')
        return self


class DiseaseSearchCall(Contract):
    tool: Literal['search_disease_sources']
    arguments: DiseaseSearchArgs


AnalysisCall = Annotated[ProfileCall | SimilarityCall | DiseaseCall | DiseaseRankingCall | DiseaseSearchCall, Field(discriminator='tool')]
CALL = TypeAdapter(AnalysisCall)


class AnalysisSource(Contract):
    source_record_id: str
    name: str
    name_pinyin: str
    source: dict
    capture_status: list[dict]
    name_identity_verified: Literal[False]


class TargetProfile(Contract):
    source: AnalysisSource
    target_ids: list[str]
    target_count: int = Field(ge=0)
    source_row_count: int = Field(ge=0)
    unidentified_rows: int = Field(ge=0)
    duplicate_identifier_rows: int = Field(ge=0)

    @model_validator(mode='after')
    def counts(self):
        if self.target_count!=len(set(self.target_ids)) or len(self.target_ids)!=self.target_count:
            raise ValueError('Invalid target count')
        if self.target_count+self.unidentified_rows+self.duplicate_identifier_rows!=self.source_row_count:
            raise ValueError('Invalid row count')
        return self


class Coverage(Contract):
    source_records: int = Field(ge=1)
    with_targets: int = Field(ge=0)
    without_targets: int = Field(ge=0)
    distinct_identifiers: int = Field(ge=0)

    @model_validator(mode='after')
    def counts(self):
        if self.source_records!=self.with_targets+self.without_targets:raise ValueError('Invalid coverage')
        return self


class ProfileData(Contract):
    algorithm_version: str
    dataset_version: str
    catalog_revision: str
    profile: TargetProfile
    coverage: Coverage


class SimilarityItem(Contract):
    source_record_id: str
    source: AnalysisSource
    shared_ids: list[str]
    query_only_ids: list[str]
    candidate_only_ids: list[str]
    shared_count: int = Field(ge=1)
    union_count: int = Field(ge=1)
    target_count: int = Field(ge=1)
    jaccard: float = Field(gt=0,le=1)

    @model_validator(mode='after')
    def counts(self):
        a,b,c=map(set,(self.shared_ids,self.query_only_ids,self.candidate_only_ids))
        if (len(a)!=len(self.shared_ids) or len(b)!=len(self.query_only_ids) or len(c)!=len(self.candidate_only_ids)
                or a&b or a&c or b&c or len(a)!=self.shared_count or len(a|b|c)!=self.union_count
                or len(a|c)!=self.target_count or abs(self.jaccard-self.shared_count/self.union_count)>1e-12
                or self.source_record_id!=self.source.source_record_id):raise ValueError('Invalid similarity row')
        return self


class SimilarityData(ProfileData):
    metric: Literal['jaccard','common_count']
    top_k: int = Field(ge=1,le=20)
    eligible_peers: int = Field(ge=0)
    overlapping_peers: int = Field(ge=0)
    items: list[SimilarityItem] = Field(max_length=20)

    @model_validator(mode='after')
    def peers(self):
        if (len(self.items)!=min(self.top_k,self.overlapping_peers) or self.overlapping_peers>self.eligible_peers
                or self.eligible_peers!=self.coverage.with_targets-int(self.profile.target_count>0)):
            raise ValueError('Invalid candidate counts')
        seen=set();query=set(self.profile.target_ids)
        for item in self.items:
            if (item.source_record_id==self.profile.source.source_record_id or item.source_record_id in seen
                    or set(item.shared_ids)|set(item.query_only_ids)!=query):raise ValueError('Invalid candidate scope')
            seen.add(item.source_record_id)
        return self


class DiseaseData(Contract):
    record_id: str
    dataset_version: str
    catalog_revision: str
    profile: dict
    target_ids: list[str]
    rows: list[dict] = Field(max_length=20)
    page: int = Field(ge=1,le=10000)
    has_next: bool
    identical_set_sources: int = Field(ge=0)

    @model_validator(mode='after')
    def counts(self):
        if (len(self.target_ids)!=len(set(self.target_ids)) or len(self.target_ids)!=self.profile.get('target_count')
                or self.record_id!='etcm-disease:'+self.profile.get('source',{}).get('file_sha256','')):
            raise ValueError('Invalid disease source or counts')
        return self


class DiseaseCandidateItem(SimilarityItem):
    evidence: list[dict] = Field(max_length=3)
    expected_shared_count: float = Field(ge=0,allow_inf_nan=False)
    observed_minus_expected: float = Field(allow_inf_nan=False)

    @model_validator(mode='after')
    def witnesses(self):
        if [p.get('uniprot') for p in self.evidence]!=self.shared_ids[:3]:raise ValueError('Invalid evidence scope')
        for pair in self.evidence:
            for side in ('herb_rows','disease_rows'):
                if not 1<=len(pair.get(side,[]))<=3:raise ValueError('Missing source witness')
        return self


class SizeContext(Contract):
    background_size: int = Field(ge=0)
    query_background_count: int = Field(ge=0)


class DiseaseRankingData(DiseaseData):
    size_context: SizeContext
    algorithm_version: str
    herb_dataset_version: str
    coverage: Coverage
    metric: Literal['jaccard','common_count']
    top_k: int = Field(ge=1,le=20)
    eligible_peers: int = Field(ge=0)
    overlapping_peers: int = Field(ge=0)
    items: list[DiseaseCandidateItem] = Field(max_length=20)
    background_target_ids: list[str]
    outside_background_ids: list[str]

    @model_validator(mode='after')
    def ranking_counts(self):
        targets=set(self.target_ids);inside=set(self.background_target_ids);outside=set(self.outside_background_ids)
        if (inside&outside or inside|outside!=targets or len(inside)!=len(self.background_target_ids)
                or len(outside)!=len(self.outside_background_ids) or self.eligible_peers!=self.coverage.with_targets
                or not len(self.items)==min(self.top_k,self.overlapping_peers)<=self.eligible_peers
                or self.overlapping_peers>self.eligible_peers):
            raise ValueError('Invalid disease coverage')
        seen=set()
        if (self.size_context.background_size!=self.coverage.distinct_identifiers
                or self.size_context.query_background_count!=len(inside)
                or len(inside)>self.size_context.background_size):
            raise ValueError('Invalid size background')
        for item in self.items:
            n=self.size_context.background_size
            expected=len(inside)*item.target_count/n if n else 0.0
            if (item.target_count>n or abs(item.expected_shared_count-expected)>1e-9
                    or abs(item.observed_minus_expected-(item.shared_count-expected))>1e-9):
                raise ValueError('Invalid size expectation')
            if item.source_record_id in seen or set(item.shared_ids)|set(item.query_only_ids)!=targets:
                raise ValueError('Invalid disease candidate')
            seen.add(item.source_record_id)
        return self


class DiseaseSearchItem(Contract):
    record_id: str
    name: str
    target_count: int = Field(ge=0)
    capture_state: str
    species_status: str
    source: dict
    match_kind: Literal['browse','exact','prefix','keywords']


class DiseaseSearchData(Contract):
    query: str
    dataset_version: str
    catalog_revision: str
    total: int = Field(ge=0)
    page: int = Field(ge=1,le=10000)
    has_next: bool
    items: list[DiseaseSearchItem] = Field(max_length=20)

    @model_validator(mode='after')
    def counts(self):
        if len(self.items)!=min(20,max(0,self.total-(self.page-1)*20)) or self.has_next!=(self.page*20<self.total):
            raise ValueError('Invalid result page')
        if len({r.record_id for r in self.items})!=len(self.items):raise ValueError('Duplicate source')
        if any(r.record_id!='etcm-disease:'+r.source.get('file_sha256','') for r in self.items):raise ValueError('Invalid source binding')
        return self


# Both descriptions and model argument schemas come from this fixed registry.
SPECS = {
    'herb_target_profile': (ProfileArgs,ProfileData,'查询一味药的来源靶点画像：去重UniProt、缺失行、重复行和来源。','靶点画像'),
    'herb_target_similarity': (SimilarityArgs,SimilarityData,'寻找与一味药靶点集合最相似的药材来源。默认Jaccard，可按common_count共同靶点数排序；前1至20条。不做疾病治疗推荐或新靶点预测。','药材靶点相似度'),
    'disease_gene_profile': (DiseaseArgs,DiseaseData,'按疾病来源原名或ID查询来源靶点集合与原始记录，page默认1。不猜译名或疾病别名。','疾病靶点资料'),
    'rank_herbs_for_gene_set': (DiseaseRankingArgs,DiseaseRankingData,'按明确疾病来源的靶点集合排序药材来源，默认Jaccard/前10个，也支持common_count；最多20。不做治疗推荐、药效或新靶点预测。','疾病关联候选排序'),
    'search_disease_sources': (DiseaseSearchArgs,DiseaseSearchData,'按疾病名称关键词查找来源；name逐字来自问句，不翻译或猜别名；明确浏览疾病目录时name留空。分页20项，结果只供选择，不自动分析候选。','疾病名称查找'),
}


def tool_specs():
    return [{'name':name,'description':spec[2],'parameters':spec[0].model_json_schema(),
             'label':spec[3],'result_schema':spec[1].model_json_schema(),'read_only':True,
             'configured':bool(os.getenv('ETCM_DISEASE_DB')) if name in ('disease_gene_profile','search_disease_sources') else
                 bool(os.getenv('TCM_DB') and os.getenv('ETCM_ENRICHMENT_DB') and (name!='rank_herbs_for_gene_set' or os.getenv('ETCM_DISEASE_DB')))} for name,spec in SPECS.items()]


def invoke(call, record=None, version=None, *, expected_revision=None):
    started=perf_counter();name=call.get('tool','unknown') if isinstance(call,dict) else 'unknown'
    try:
        validated=CALL.validate_python(call)
    except (ValidationError,TypeError):
        return ToolResult(tool=str(name),status='error',error=ToolError(code='invalid_arguments'),elapsed_ms=0)
    path=os.getenv('ETCM_ENRICHMENT_DB');base=os.getenv('TCM_DB')
    try:
        if name=='search_disease_sources':
            from tcm.disease_search import query
            data=query(validated,expected_revision=expected_revision)
        elif name in ('disease_gene_profile','rank_herbs_for_gene_set'):
            from tcm.disease_analysis import query
            data=query(validated,expected_revision=expected_revision)
        else:
            if not path or not base:raise ValueError('Analysis not configured')
            parameters=validated.arguments.model_dump(exclude={'name'})
            data=target_similarity.query(path,base,record,version,tool=name,expected_revision=expected_revision,**parameters)
        try:typed=SPECS[name][1].model_validate(data)
        except ValidationError:
            return ToolResult(tool=name,status='error',error=ToolError(code='invalid_result'),elapsed_ms=(perf_counter()-started)*1000)
        if isinstance(typed,DiseaseSearchData):empty=not typed.items
        else:empty=(not typed.target_ids or isinstance(typed,DiseaseRankingData) and not typed.items) if isinstance(typed,DiseaseData) else (not typed.profile.target_count or isinstance(typed,SimilarityData) and not typed.items)
        return ToolResult(tool=name,status='empty' if empty else 'ok',data=typed,elapsed_ms=(perf_counter()-started)*1000)
    except target_similarity.SnapshotChanged:code='snapshot_changed'
    except (OSError,ValueError,sqlite3.Error,KeyError,TypeError):code='unavailable'
    return ToolResult(tool=str(name),status='error',error=ToolError(code=code),elapsed_ms=(perf_counter()-started)*1000)


def command(call, source_id=None):
    parsed=CALL.validate_python(call);args=parsed.arguments
    name=source_id or args.name
    if parsed.tool=='search_disease_sources':return '/analysis diseases'+(' '+name if name else '')+f' --page {args.page}'
    if parsed.tool=='disease_gene_profile':return f'/analysis disease {name} --page {args.page}'
    if parsed.tool=='rank_herbs_for_gene_set':return f'/analysis disease-rank {name} --top-k {args.top_k} --metric {args.metric}'
    if parsed.tool=='herb_target_profile':return '/analysis profile '+name
    return f'/analysis similarity {name} --top-k {args.top_k} --metric {args.metric}'


def execute_turn(request, focus=None):
    from tcm.chat import execute_turn as herb_turn
    result={'topic':'target_analysis','status':'ok','llm_calls':0,'network_calls':0}
    replay=bool(request.get('followup'));expected=None
    if replay:
        if not focus or focus.get('kind')!='etcm_analysis_focus_v1' or request.get('action','replay')!='replay':
            return {**result,'status':'clarification_needed','message':'请先查询明确药材的靶点画像或相似度。'},None
        expected=focus.get('catalog_revision')
        if not isinstance(expected,str) or not expected:
            return {**result,'status':'clarification_needed','message':'上次分析版本不可用，请重新查询。'},None
        call=focus.get('call')
    else:call=request.get('call')
    try:parsed=CALL.validate_python(call)
    except (ValidationError,TypeError):
        return {**result,'status':'clarification_needed','message':'可问“白术的靶点画像”或“与白术靶点最相似的药材，前10个”。最多返回20个候选。'},None
    if parsed.tool=='search_disease_sources':
        from tcm.disease_search import execute_turn as search_turn
        return search_turn(parsed,expected_revision=expected)
    if parsed.tool in ('disease_gene_profile','rank_herbs_for_gene_set'):
        from tcm.disease_analysis import execute_turn as disease_turn
        return disease_turn(parsed,expected_revision=expected)
    name=parsed.arguments.name
    herb_request={'source_record_id':name} if name.startswith('etcm2-local:') else {'name':name}
    found,_=herb_turn({**herb_request,'profile':True,'resolve_unique_name':True},None)
    if found['status']!='ok':
        options=[{'label':r['fields']['name_pinyin']['value'],'source':r['source'],
                  'query':command(call,r['source_record_id'])} for r in found.get('records',[])]
        return {**result,'status':'unavailable' if found['status'] in ('database_unavailable','dataset_not_found') else 'clarification_needed',
                'message':'请先选择要分析的药材来源。' if options else '未找到可分析的药材来源，请检查药名或本地数据库。','options':options},None
    tool_result=invoke(call,found['records'][0],found['dataset_version'],expected_revision=expected)
    result['analysis_result']=tool_result.model_dump()
    if tool_result.status=='error':
        code=tool_result.error.code
        return {**result,'status':'unavailable','message':'来源或分析版本已改变，请重新查询。' if code=='snapshot_changed' else '分析资料暂不可用，未生成相似度结果。'},None
    data=tool_result.data;source=data.profile.source
    result['message']=f'{source.name}：{data.profile.target_count}个来源靶点（按UniProt去重）。'
    if isinstance(data,SimilarityData):
        label='Jaccard相似度' if data.metric=='jaccard' else '共同靶点数'
        result['message']+=f' 按{label}排序，显示{len(data.items)}个相似来源。'
    if not data.profile.target_count:result['message']='当前来源没有可用于分析的明确UniProt记录，暂不能计算相似度。'
    result['notes']=['相似度依据当前来源靶点集合，不代表药效相似、替代用药或新的靶点预测。',
                     '中文名仅用作来源候选标签；未合并同名来源。无记录的药材列为未覆盖，抓取状态和出处可展开。']
    current=parsed.model_dump();current['arguments']['name']=source.source_record_id
    result['replay_query']=command(current)
    new_focus={'kind':'etcm_analysis_focus_v1','call':current,'catalog_revision':data.catalog_revision}
    return result,new_focus
