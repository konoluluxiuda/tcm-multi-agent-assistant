"""Whole-request attribute intents; preserve the full named object for lookup."""
import re

PROFILE = r'(?:相关|基本|详细)?(?:属性|信息|资料|概况|情况)'
PREFIX = r'(?:(?:请帮我|帮我|麻烦你|麻烦|请)\s*)?(?:我(?:现在)?(?:需要|想要|想|希望|要)\s*)?'
VERB = r'(?:查看|查询|查一下|查一查|了解一下|了解|看看|看一下|介绍一下|介绍|看|查)'
NAME = r'(?P<name>[\u4e00-\u9fffA-Za-z0-9 .():_\-]{1,100}?)'


def parse(text):
    if not isinstance(text,str) or len(text)>400:return None
    body=text.strip().rstrip('？?。！!').strip()
    if re.fullmatch(PREFIX+r'(?:'+VERB+r')?\s*(?:它|这味药|刚才那味药)的?'+PROFILE+r'(?:有哪些|是什么|呢)?',body):
        return {'followup':True,'profile':True}
    patterns=(PREFIX+r'(?:'+VERB+r')?\s*(?:中药材|中药|药材)?\s*'+NAME+r'(?:的|有哪些|有什么)?'+PROFILE+r'(?:有哪些|是什么|呢)?',
              PREFIX+VERB+r'\s*(?:关于|有关)'+NAME+r'的?'+PROFILE)
    for pattern in patterns:
        match=re.fullmatch(pattern,body)
        if match:
            name=match['name'].strip()
            # Additional actions and unnamed pairs are not part of one herb name.
            if re.search(r'和|与|及|查看|查询|需要|我|帮我|文献|论文|价格|还有|再查',name):return None
            return {'name':name,'profile':True,'resolve_unique_name':True}
    return None
