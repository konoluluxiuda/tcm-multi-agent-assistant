"""Source-bound object-scope triage and user annotations; never entity approval."""
import hashlib
import re

VERSION = 'research_object_scope_v1'
KINDS = {'unknown':'未确认', 'raw_herb':'单味原药材', 'processed_herb':'炮制品/饮片',
         'formula':'复方/组合', 'extract':'提取物', 'constituent':'成分'}
RELATIONS = {'unverified':'尚未确认', 'possibly_related':'用户认为可能相关',
             'scope_differs':'用户认为对象范围不同', 'same_object_claimed':'用户主张对象一致（系统未验证）'}
PATTERNS = {
    'raw_herb': r'\b(?:single[- ]herb|raw herb|crude drug|whole herb)\b|单味药|原药材',
    'processed_herb': r'\b(?:processed|stir[- ]fried|prepared slices)\b|炮制|饮片',
    'formula': r'\b(?:formula|formulation|mixture|multi[- ]herb|combination)\b|复方|配方',
    'extract': r'\bextracts?\b|提取物|浸膏',
    'constituent': r'\b(?:constituents?|compounds?|isolated|purified|polysaccharides?|saponins?|flavonoids?)\b|单体|成分|多糖|皂苷|黄酮',
}


def sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def sources(article):
    yield 'title', None, article.get('title') or ''
    for index, section in enumerate(article.get('abstract') or []):
        yield 'abstract', index, section.get('text') or ''


def scope_signals(article):
    result={'pmid':article['pmid'],'version':VERSION,'status':'needs_object_review',
            'object_identity_verified':False,'direct_attribution_allowed':False,'signals':[],
            'scanned_sources':0,'truncated':False,'full_text_read':False}
    all_sources=list(sources(article))
    for field,index,text in all_sources[:21]:
        prefix=text[:8000];result['scanned_sources']+=1
        result['truncated'] |= len(prefix)<len(text)
        for kind,pattern in PATTERNS.items():
            for match in re.finditer(pattern,prefix,re.I):
                if len(result['signals'])>=12:
                    result['truncated']=True;return result
                result['signals'].append({'kind_hint':kind,'source_field':field,'abstract_index':index,
                                          'start':match.start(),'end':match.end(),'quote':match.group(),
                                          'source_sha256':sha(text)})
    result['truncated'] |= len(all_sources)>21
    return result


def validate_reviews(articles, reviews, question, review_target):
    if not isinstance(reviews,list) or len(reviews)>5:
        raise ValueError('At most five object annotations')
    if not reviews:
        if review_target is not None:raise ValueError('Unexpected annotation target')
        return []
    if review_target!=question:
        raise ValueError('Object annotations refer to another local query')
    by_pmid={a['pmid']:a for a in articles[:5]};seen=set();checked=[]
    for item in reviews:
        if not isinstance(item,dict) or set(item)!={'pmid','kind','relation','source_field','abstract_index','quote'}:
            raise ValueError('Unexpected annotation shape')
        pmid=item['pmid'];kind=item['kind'];relation=item['relation']
        if pmid not in by_pmid or pmid in seen or kind not in KINDS or relation not in RELATIONS:
            raise ValueError('Invalid annotation identity or classification')
        seen.add(pmid)
        field=item['source_field'];index=item['abstract_index'];quote=item['quote']
        if field not in ('title','abstract') or (field=='title' and index is not None) or (field=='abstract' and (type(index) is not int or not 0<=index<100)):
            raise ValueError('Invalid source selector')
        text=next((text for f,i,text in sources(by_pmid[pmid]) if f==field and i==index),None)
        if not isinstance(quote,str) or not 12<=len(quote)<=300 or text is None or quote not in text:
            raise ValueError('Quotation is absent from this frozen source')
        start=text.index(quote)
        checked.append({**item,'source_sha256':sha(text),'quote_sha256':sha(quote),
                        'source_start':start,'source_end':start+len(quote),'version':VERSION,
                        'origin':'user_annotation','object_identity_verified':False,
                        'direct_attribution_allowed':False,'quality_appraised':False,
                        'scope_warning':kind in ('processed_herb','formula','extract','constituent')})
    return checked
