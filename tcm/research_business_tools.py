"""One A-loop registry: retain analysis contracts and add existing report capabilities."""
from typing import Annotated,Literal
from pydantic import Field,TypeAdapter,model_validator
from tcm.task_tools import Contract
from tcm.analysis_tools import AnalysisCall,SPECS as ANALYSIS_SPECS,ProfileArgs

PharmaField=Literal['来源','性味与归经','功能与主治','用法与用量','注意','贮藏','炮制','性状','鉴别','检查','含量测定']
AttributeField=Literal['family','medicinal_part','properties','flavors','meridians','habitat','efficacy_class','indications','reference_pmids','latin_name','english_name','name_pinyin']
REPORT_TOOLS=('pharmacopoeia_lookup','herb_attributes','pair_evidence_report')


class PharmaArgs(ProfileArgs):
    fields:list[PharmaField]=Field(min_length=1,max_length=4)

    @model_validator(mode='after')
    def unique_fields(self):
        if len(set(self.fields))!=len(self.fields):raise ValueError('Duplicate fields')
        return self


class AttributeArgs(ProfileArgs):
    fields:list[AttributeField]=Field(min_length=1,max_length=12)

    @model_validator(mode='after')
    def unique_fields(self):
        if len(set(self.fields))!=len(self.fields):raise ValueError('Duplicate fields')
        return self


class PairArgs(Contract):
    herbs:list[str]=Field(min_length=2,max_length=2)
    fields:list[PharmaField]=Field(min_length=1,max_length=4)
    attribute_fields:list[AttributeField]=Field(default_factory=list,max_length=4)
    source_ids:list[str]|None=Field(default=None,min_length=2,max_length=2)
    all_targets:bool=False

    @model_validator(mode='after')
    def distinct(self):
        from tcm.pair_research_report import validate_pair
        self.herbs=validate_pair(self.herbs)
        if len(set(self.fields))!=len(self.fields) or len(set(self.attribute_fields))!=len(self.attribute_fields):
            raise ValueError('Duplicate fields')
        if self.source_ids is not None:
            import re
            if len(set(self.source_ids))!=2 or any(not re.fullmatch(r'etcm2-local:[0-9a-f]{64}',s) for s in self.source_ids):
                raise ValueError('Two distinct source IDs required')
        return self


class PharmaCall(Contract):
    tool:Literal['pharmacopoeia_lookup']
    arguments:PharmaArgs


class AttributeCall(Contract):
    tool:Literal['herb_attributes']
    arguments:AttributeArgs


class PairCall(Contract):
    tool:Literal['pair_evidence_report']
    arguments:PairArgs


BusinessCall=Annotated[AnalysisCall|PharmaCall|AttributeCall|PairCall,Field(discriminator='tool')]
CALL=TypeAdapter(BusinessCall)


class ReportData(Contract):
    request:dict
    complete:bool
    missing:list[str]
    report:dict
    dataset_version:str
    catalog_revision:str
    leaf_calls:int=Field(ge=0,le=14)


SPECS={**ANALYSIS_SPECS,
    'pharmacopoeia_lookup':(PharmaArgs,ReportData,'查询明确药材的药典字段原文与原页出处；fields必须逐项来自问题，不生成用药结论。','药典字段'),
    'herb_attributes':(AttributeArgs,ReportData,'查询已选择ETCM来源的基础属性，保留原值和JSON位置；缺字段保持缺失。','ETCM属性'),
    'pair_evidence_report':(PairArgs,ReportData,'复用两药报告子图：双方药典字段、ETCM属性、共同UniProt与双方原始记录。宿主选择source_ids；all_targets仅在明确要求全部共同靶点时开启。内部读取计入总预算，不是治疗或机制推断。','两药证据报告')}


def maximum_cost(name,args):
    return (14 if args.get('all_targets') else 5) if name=='pair_evidence_report' else 1


def fulfilled(name,payload):
    return payload.get('status') in ('ok','empty') and (name not in REPORT_TOOLS or payload['data']['complete'])


def tool_specs():
    return [{'name':name,'description':spec[2],'parameters':spec[0].model_json_schema(),
             'label':spec[3],'read_only':True} for name,spec in SPECS.items()]
