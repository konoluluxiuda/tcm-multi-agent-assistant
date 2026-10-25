"""Disease directory intents; consumes the entire request, never guesses entities."""
import re

NUMBER = r'(?:[0-9]{1,4}|[一二两三四五六七八九十]{1,3})'
POLITE = r'(?:(?:请|麻烦)(?:你)?(?:帮我)?|帮我)?\s*'
IDS = r'(?:\s*[，,]?\s*(?:并)?(?:及其|以及|和对应的|及|和|与|附上|带上|给出|显示)\s*(?:对应的)?(?:来源\s*)?(?:id|编号))?'


def number(value):
    if value.isdigit(): return int(value)
    digits = dict(zip('一二三四五六七八九', range(1, 10))); digits['两'] = 2
    if value in digits: return digits[value]
    if value == '十': return 10
    if re.fullmatch('[二三四五六七八九]?十[一二三四五六七八九]?', value):
        left, right = value.split('十')
        return digits.get(left, 1) * 10 + digits.get(right, 0)
    return None


def parse(text):
    compact = re.sub(r'\s+', '', text)
    if re.fullmatch(POLITE + r'(?:数据库(?:里|中)?|目前|当前|这里)?(?:都)?(?:有|收录了?)?(?:哪些|什么)疾病(?:来源|记录)?' + IDS, compact, re.I) or compact in ('浏览疾病目录','查看疾病目录','疾病目录','查看疾病列表'):
        return request()
    sample = re.fullmatch(POLITE + r'(?:随便|任意)?(?:提供|给出|给我|给|列出|列举|举例|举|看看|看一下)(?:一下)?\s*(?:前)?(' + NUMBER + r'|几个|一些)\s*(?:个|种|条)?(?:疾病来源|疾病)(?:名称|例子)?' + IDS, text, re.I)
    if sample:
        value = sample[1]; count = 5 if value in ('几个', '一些') else number(value)
        if count is None or not 1 <= count <= 20:
            return {'topic':'target_analysis','directory_error':'count'}
        return request(page_size=count)
    more = re.fullmatch(POLITE + r'(?:再给(?:我)?|再来|换|再看)\s*(' + NUMBER + r')\s*(?:个|种|条)(?:疾病|疾病来源)' + IDS, text, re.I)
    if more:
        count = number(more[1])
        if count is None or not 1 <= count <= 20: return {'topic':'target_analysis','directory_error':'count'}
        return {'topic':'target_analysis','followup':True,'action':'directory_next','page_size':count}
    page = re.fullmatch(r'(?:疾病目录|疾病列表|疾病)(?:的)?(下一页|上一页)',compact)
    if page:
        return {'topic':'target_analysis','followup':True,'action':'directory_next' if page[1]=='下一页' else 'directory_previous'}
    return None


def request(**args):
    return {'topic':'target_analysis','call':{'tool':'search_disease_sources','arguments':{'name':'',**args}}}
