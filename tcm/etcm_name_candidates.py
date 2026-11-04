"""Read title-declared pinyin, including OCR-glued Chinese/pinyin/Latin headings.

No transliteration, edit distance, suffix removal or identity approval is used.
"""
import re

VERSION='anchored_heading_v2'


def title_pinyin(text,name):
    if not isinstance(text,str):return None
    start=len(text)-len(text.lstrip());body=text[start:].rstrip()
    if body.startswith(name):
        start+=len(name);body=body[len(name):]
        space=len(body)-len(body.lstrip());start+=space;body=body.lstrip()
    elif re.search(r'[\u4e00-\u9fff]',body):return None
    # The first uppercase Latin-word run separates DanshenSALVIAE as well as
    # Danshen SALVIAE. All-uppercase pinyin remains ambiguous and is rejected.
    latin=re.search(r'(?<![A-Z])[A-Z]{3,}',body)
    if not latin:return None
    raw=body[:latin.start()];pinyin=raw.rstrip()
    if (not re.fullmatch(r'[A-Za-z][A-Za-z -]{1,69}',pinyin) or
            not re.search('[a-z]',pinyin) or not re.fullmatch(r'[A-Z][A-Z\s-]{2,160}',body[latin.start():])):
        return None
    return {'value':pinyin,'normalized':re.sub(r'[ -]','',pinyin).casefold(),
            'span':[start,start+len(pinyin)],'parser_version':VERSION}
