"""Local collection descriptions; counts come from the live inventory tool."""
import re

DATASETS = ('etcm', 'pharmacopoeia')
ALIASES = r'(?:ETCM(?:2(?:\.0)?)?|(?:中国)?药典(?:2025(?:年版)?)?)'
COLLECTION = r'(?:(?:当前|本地|上面|上述|这|那)(?:的)?)?(?:两(?:个|种|份))?(?:数据库|数据集|资料库|数据|资料)'


def parse(question):
    text = re.sub(r'\s+', '', question).rstrip('？?。！!')
    text = re.sub(r'^(?:我想问的是|我问的是|我的意思是|我想了解|我想知道)', '', text)
    target = rf'(?:{COLLECTION}|{ALIASES}(?:和|与|、|及){ALIASES}|{ALIASES}(?:数据库|数据集|资料库)?)'
    prefix = r'(?:请|请帮我|帮我|能否|能不能)?(?:分别|简单|详细)?'
    patterns = (
        prefix + r'(?:介绍|解释|说明|讲讲|说说)(?:一下)?(?:这)?' + target + r'(?:分别是什么|有什么区别|的区别|的内容)?(?:吗)?',
        prefix + target + r'(?:分别是什么|是什么|有什么区别|有何区别|的区别|分别有什么用|各有什么用)',
        prefix + target + r'(?:的)?(?:介绍|说明)',
    )
    if not any(re.fullmatch(p, text, re.I) for p in patterns):
        return None
    selected = []
    if re.search(r'ETCM', text, re.I):selected.append('etcm')
    if '药典' in text:selected.append('pharmacopoeia')
    return {'topic': 'dataset_info', 'datasets': selected or list(DATASETS)}


def describe(inventory, selected=None):
    selected = selected or list(DATASETS)
    if not isinstance(selected, list) or any(x not in DATASETS for x in selected) or len(set(selected)) != len(selected):
        raise ValueError('Unknown collection')
    herbs, pharma = inventory['herbs'], inventory['pharmacopoeia']
    cards = []
    if 'etcm' in selected:
        count = f"当前导入{herbs['source_record_count']}条药材来源记录。" if herbs['status']=='ok' else '当前记录数不可读取。'
        cards.append({'id':'etcm','title':'ETCM 2.0：药材与分子关联资料',
            'summary':count+' 本项目使用下载的JSON，包含药材名称、药用部位、性味归经等属性，以及来源列出的成分、靶点和疾病富集关联。',
            'use':'适合查药材属性、成分、共同靶点和原始记录位置。',
            'example':'例如：白术有哪些成分；当归和川芎有哪些共同靶点。',
            'availability':herbs['status']})
    if 'pharmacopoeia' in selected:
        count = f"当前索引{pharma['entry_count']}个条目、{pharma['field_chunk_count']}个字段块。" if pharma['status']=='ok' else '当前药典索引未启用或不可读取。'
        cards.append({'id':'pharmacopoeia','title':'中国药典：药材标准与记载',
            'summary':count+' 本项目使用提供的2025年版一部PDF，经识别整理为来源、性味归经、功能主治、用法用量、注意、鉴别及检查等字段，并保留原页位置。',
            'use':'适合核对原文、比较药材记载并查看出处。',
            'example':'例如：白术的功能与主治；比较当归和川芎的用法用量。',
            'availability':pharma['status']})
    # Descriptions are available even if a data service is disabled; no stale counts.
    lines = ['\n'.join((c['title'], c['summary'], c['use'], c['example'])) for c in cards]
    if len(cards)==2:lines.append('两者互补：ETCM用于查看分子关联，药典用于核对药材记载和标准。')
    return {'status':'ok','topic':'dataset_info','llm_calls':0,'network_calls':0,
            'datasets':cards,'message':'\n\n'.join(lines),
            'notes':['数量描述当前本地导入范围，不代表完整原库；ETCM记录与药典条目尚未完成逐一对应。',
                     'ETCM来源关联不等于本项目新预测或已验证药效；药典OCR提取尚未完成人工审核。']}
