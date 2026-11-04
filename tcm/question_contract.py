"""Question-only spans and grounded field requirements, shared by rules and proposals.

This is a bounded contract compiler, not a medical entity linker. It never reads
catalogs, fills source IDs, or changes the original question.
"""
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class QuestionSpan:
    text: str
    start: int
    end: int


def spans(question):
    result=[];start=0;stack=[]
    pairs={'(':')','（':'）','[':']','“':'”','"':'"'}
    for i,char in enumerate(question):
        if stack and char==stack[-1]:stack.pop();continue
        if char in pairs:stack.append(pairs[char]);continue
        if stack:continue
        if char not in '，,；;。！？?!\n\r.':continue
        # Latin names and version numbers contain commas/decimal points. A
        # Chinese task verb on either side still starts a separate requirement.
        if char in ',.':
            left=question[start:i].rstrip();right=question[i+1:].lstrip()
            if left and right and re.fullmatch('[A-Za-z0-9]',left[-1]) and re.fullmatch('[A-Za-z0-9]',right[0]):continue
        lo=start;hi=i
        while lo<hi and question[lo].isspace():lo+=1
        while hi>lo and question[hi-1].isspace():hi-=1
        if lo<hi:result.append(QuestionSpan(question[lo:hi],lo,hi))
        start=i+1
    lo=start;hi=len(question)
    while lo<hi and question[lo].isspace():lo+=1
    while hi>lo and question[hi-1].isspace():hi-=1
    if lo<hi:result.append(QuestionSpan(question[lo:hi],lo,hi))
    # Keep the established inline transition convention for model clause IDs.
    expanded=[]
    for part in result:
        cuts=[0]+[m.start() for m in re.finditer(r'然后(?=查询|读取|查看|查找|按)|并且(?=查询|读取|查看|查找|按)',part.text) if m.start()]+[len(part.text)]
        for a,b in zip(cuts,cuts[1:]):
            raw=part.text[a:b];trim=len(raw)-len(raw.lstrip());text=raw.strip()
            if text:expanded.append(QuestionSpan(text,part.start+a+trim,part.start+a+trim+len(text)))
    return expanded


def clauses(question):return [p.text for p in spans(question)]


def relation_tasks(text):
    """A fully consumed coordinated request, never an ingredient→target join."""
    text=strip_lead(text)
    match=re.fullmatch(r'(?:查询|查看|读取|查一下|看看|查)?\s*(.{1,256}?)'
                      r'(?:各自?|分别)?(?:有哪些|有什么|的)(?:化学)?'
                      r'(成分(?:和|与|及|、)靶点|靶点(?:和|与|及|、)成分|成分|靶点)(?:有哪些|是什么)?',text)
    if not match:return None
    names=[n.strip() for n in re.split(r'和|与|及|、',match[1])]
    if not 1<=len(names)<=4 or len(set(names))!=len(names):return None
    from tcm.intent_planner import grounded
    if any(not re.fullmatch(r'[\u4e00-\u9fffA-Za-z0-9 .():_\-]{1,100}',n) or
           re.search(r'我|不要|不查|只有|比较|整理|药典|的|各|分别',n) or not grounded(n,text) for n in names):return None
    kinds=([{'成分':'ingredient','靶点':'target'}[match[2]]] if match[2] in ('成分','靶点') else
           ['ingredient','target'] if match[2].startswith('成分') else ['target','ingredient'])
    return [{'tool':'herb_relation_records','arguments':{'name':n,'kind':k,'page':1}} for n in names for k in kinds]


def strip_lead(text):
    return re.sub(r'^(?:(?:请帮我|帮我|麻烦你|麻烦|我想知道|我想了解|告诉我|想知道|想了解|请|然后|并且|同时|并|再|先|分别|把|将)\s*)+','',text).strip()


def field_tasks(text,_depth=0):
    """Consume complete field units; field conjunctions stay inside one unit.

    A conjunction between objects is split only if BOTH sides compile. Unknown
    fields or trailing tasks reject the whole clause rather than disappearing.
    """
    from tcm.research_report_plan import ATTR,PHARMA,NAME,fields
    if _depth>4 or len(text)>1000:return None
    text=strip_lead(text).rstrip('？?。！!').strip()
    text=re.sub(r'(?:整理到一起|整理在一起|汇总到一起|汇总|整理)$','',text).strip()
    command=r'(?:查询|查看|读取|查一下|看一下|核对|查)?\s*'
    for tag,tool,mapping in [('药典','pharmacopoeia_lookup',PHARMA),('ETCM','herb_attributes',ATTR)]:
        patterns=[command+tag+r'(?:中|里)?(?:的)?\s*'+NAME+r'的(.+)',
                  command+NAME+r'(?:的|在)'+tag+r'(?:中|里)?(?:的)?(.+)']
        for pattern in patterns:
            match=re.fullmatch(pattern,text,re.I)
            if match and match[1].strip() not in ('它','这味药','刚才那味药','刚才的药材') and not re.search(r'和|与|及|比较|对比|对照|整理|我|不要|不是|关于|有关|想知道|想了解|告诉|需要|希望',match[1]):
                requested=fields(match[2],mapping,12 if tool=='herb_attributes' else 4)
                if requested:return [{'tool':tool,'arguments':{'name':match[1].strip(),'fields':requested}}]
    # Structural-only fields have an unambiguous local source. Shared fields
    # such as 归经 do not implicitly choose ETCM over the pharmacopoeia.
    match=re.fullmatch(command+NAME+r'的(.+)',text)
    if match and match[1].strip() not in ('它','这味药','刚才那味药','刚才的药材') and not re.search(r'和|与|及|比较|对比|对照|整理|我|不要|不是|关于|有关|想知道|想了解|告诉|需要|希望|药典|ETCM',match[1],re.I):
        requested=fields(match[2],ATTR,12)
        if requested and any(token in match[2] for token in ('科名','药用部位','药性','药味','产地','功效分类','适应证','文献ID','拉丁名','英文名','拼音')):
            return [{'tool':'herb_attributes','arguments':{'name':match[1].strip(),'fields':requested}}]
    for match in list(re.finditer('和|以及|并且',text))[:8]:
        left=field_tasks(text[:match.start()],_depth+1);right=field_tasks(text[match.end():],_depth+1)
        if left and right:return left+right
    return None


def proposed_field_task(text,call):
    """Validate a model proposal from literal slots, independent of word order.

    Only a small filler vocabulary may remain after removing the literal object,
    source, and field aliases. Unknown requirements therefore fail closed.
    """
    from tcm.research_report_plan import ATTR,PHARMA,fields
    name=call.arguments.name
    if text.count(name)!=1:return False
    source='药典' if call.tool=='pharmacopoeia_lookup' else 'ETCM'
    if len(re.findall(re.escape(source),text,re.I))!=1:return False
    remainder=text.replace(name,' ',1)
    remainder=re.sub(re.escape(source),' ',remainder,count=1,flags=re.I)
    # Deliberately separate filler from aliases: “来源” is a field only when
    # requested, while “出处” is a presentation requirement.
    remainder=re.sub(r'请帮我|帮我|麻烦你|麻烦|请|核实|核对|告诉我|给我|列一下|列出|查询|查看|读取|查一下|看一下|想知道|想了解|分别|中的|中|里的|里|在|关于|内容|对应|分别|是什么|是多少|有哪些|以及|和|与|及|、|的|呢|吗|\s','',remainder)
    mapping=PHARMA if call.tool=='pharmacopoeia_lookup' else ATTR
    requested=fields(remainder,mapping,12 if call.tool=='herb_attributes' else 4)
    return requested==call.arguments.fields
