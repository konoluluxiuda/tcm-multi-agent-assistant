"""Bounded chat grammar for registered target analyses."""
import re


def parse(text, *, explicit=False):
    if not isinstance(text,str):return None
    text=text.strip()
    request={'topic':'target_analysis'}
    if explicit:
        if not re.match(r'^/analysis(?:\s|$)',text):return None
        body=text[len('/analysis'):].strip()
        profile=re.fullmatch(r'profile\s+([^\n\r]{1,100})',body)
        similarity=re.fullmatch(r'similarity\s+([^\n\r]{1,100}?)(?: --top-k ([0-9]{1,3}))?(?: --metric (jaccard|common_count))?',body)
        if profile:return {**request,'call':{'tool':'herb_target_profile','arguments':{'name':profile[1]}}}
        if similarity:return {**request,'call':{'tool':'herb_target_similarity','arguments':{'name':similarity[1],'top_k':int(similarity[2] or 10),'metric':similarity[3] or 'jaccard'}}}
        return request
    if len(text)>400:return None
    text=text.rstrip('？?。！!').strip()
    if text in ('回看分析结果','刚才的分析结果'):
        return {**request,'followup':True,'action':'replay'}
    clauses=[s.strip() for s in re.split(r'[，,；;。！？?!]',text) if s.strip()]
    if not clauses:return None
    head=re.sub(r'^(?:请帮我|帮我|请)?\s*(?:查询|查看|查一下)?\s*','',clauses[0])
    name=r'(?P<name>[\u4e00-\u9fffA-Za-z0-9 .():_\-]{1,100}?)'
    profile=re.fullmatch(name+r'的?(?:靶点画像|靶点概况)',head)
    patterns=(r'(?:哪些药材与|哪些药材的靶点与)'+name+r'的?靶点(?:集合)?(?:最)?相似',
              r'(?:查找|寻找|找出|找)?(?:与|和)'+name+r'的?靶点(?:集合)?(?:最)?相似的药材(?:有哪些)?',
              name+r'的?靶点相似药材(?:有哪些)?')
    matched=profile or next((m for p in patterns if (m:=re.fullmatch(p,head))),None)
    if matched is None:return None
    entity=matched['name'].strip()
    if re.search(r'和|与|及|不要|不是|哪些|我|请|查询',entity):return None
    args={'name':entity};top_seen=metric_seen=False
    for clause in clauses[1:]:
        top=re.fullmatch(r'(?:显示|列出|列|返回)?前([0-9]{1,3})个(?:药材|来源)?',clause)
        metric=re.fullmatch(r'按(共同靶点数|Jaccard(?:相似度)?)排序',clause,re.I)
        if top and not profile and not top_seen:args['top_k']=int(top[1]);top_seen=True
        elif metric and not profile and not metric_seen:args['metric']='common_count' if metric[1]=='共同靶点数' else 'jaccard';metric_seen=True
        elif re.fullmatch(r'(?:并)?(?:显示|保留|附上|给出)(?:共同靶点和依据|来源|出处|依据)',clause):pass
        else:return None
    return {**request,'call':{'tool':'herb_target_profile' if profile else 'herb_target_similarity','arguments':args}}
