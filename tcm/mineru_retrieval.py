"""Read-only lexical research retrieval; no answer generation or entity approval."""
import json
from pathlib import Path
import re
import sqlite3
import unicodedata

from tcm.mineru_pharmacopoeia import digest
from tcm.mineru_guards import VERSION as GUARD_VERSION, context_check, field_body, reference_target

VERSION = 1
PARAMETERS = {'tokenizer': 'nfkc_lower_cjk_unigrams_bigrams_latin_words',
              'bm25_weights': [3.0, 2.0, 1.0], 'max_query_chars': 500,
              'default_k': 5, 'max_k': 20, 'appendices_in_routed_search': False}
INTENTS = {
    'origin': ('来源', '药用部位', '入药部位', '哪个部位', '什么植物', '来自', '取自哪一部分', '取自哪个部分'),
    '性味与归经': ('性味', '归经', '归哪', '入哪些经'),
    '功能与主治': ('功能', '主治', '功效', '作用', '用于', '病证', '药效'),
    '用法与用量': ('用法', '用量', '用多少', '用几克', '煎服量', '多少克', '每次几克', '剂量'),
    '注意': ('注意', '禁忌', '禁用', '慎用', '不宜', '使用警示', '使用警告'),
    '贮藏': ('贮藏', '储存', '保存', '存放', '储藏'),
    '炮制': ('炮制', '怎么加工'),
    '性状': ('性状', '外观'),
    '鉴别': ('鉴别',), '检查': ('检查',), '含量测定': ('含量测定',),
}
# Deliberately finite glyph pairs, not medical aliases or a pinyin dictionary.
# Query-only: do not change normalized()/terms() or the frozen FTS index recipe.
NAME_SCRIPT_PAIRS = {'黃': '黄', '黨': '党', '參': '参', '烏': '乌'}
QUERY_RULE_VERSION = 1


def normalized(text):
    return ''.join(unicodedata.normalize('NFKC', text).lower().split())


def name_key(text):
    return normalized(text).translate(str.maketrans(NAME_SCRIPT_PAIRS))


def terms(text):
    result = []
    for run in re.findall(r'[\u4e00-\u9fff]+|[a-z0-9]+', normalized(text)):
        if re.fullmatch(r'[\u4e00-\u9fff]+', run):
            result.extend(run)
            result.extend(run[i:i + 2] for i in range(len(run) - 1))
        else:
            result.append(run)
    return result


def name_spans(query, names):
    """Prefer longest overlapping names, but retain separately mentioned entities."""
    text = name_key(query)
    spans = []
    for name in names:
        value = name_key(name)
        if len(value) < 2:
            continue
        start = 0
        while (position := text.find(value, start)) != -1:
            spans.append((position, position + len(value), name))
            start = position + 1
    kept = [s for s in spans if not any(a <= s[0] and b >= s[1] and b - a > s[1] - s[0]
                                       for a, b, _ in spans)]
    return kept


def resolve_names(query, names):
    return sorted({s[2] for s in name_spans(query, names)})


class ResearchRetriever:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.receipt = json.loads((self.directory / 'manifest.json').read_text(encoding='utf-8'))
        path = self.directory / 'research.sqlite3'
        if digest(path) != self.receipt['index_sha256']:
            raise ValueError('Research index changed since build')
        self.connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute('PRAGMA query_only=ON')
        meta = {r[0]: json.loads(r[1]) for r in self.connection.execute('SELECT key,value FROM metadata')}
        if meta['view_sha256'] != self.receipt['view_sha256'] or meta['parameters'] != PARAMETERS:
            self.close()
            raise ValueError('Index recipe/corpus mismatch; rebuild required')
        self.names = [r[0] for r in self.connection.execute('SELECT DISTINCT name FROM entries')]

    def close(self):
        self.connection.close()

    def query(self, question, method='name_field', k=5, name=None, field=None):
        if not isinstance(question, str) or len(question) > PARAMETERS['max_query_chars']:
            raise ValueError('Query must be text of at most 500 characters')
        if type(k) is not int or not 1 <= k <= PARAMETERS['max_k']:
            raise ValueError('k must be between 1 and 20')
        if method not in ('name_field', 'bm25'):
            raise ValueError('Unknown retrieval method')
        result = {'method': method, 'question': question, 'status': 'candidates', 'hits': [],
                  'research_only': True, 'human_signoff': False, 'answer_generated': False,
                  'guard_version': GUARD_VERSION,
                  'view_sha256': self.receipt['view_sha256'],
                  'query_interpretation': {'version': QUERY_RULE_VERSION, 'name_forms': [], 'field_matches': []}}
        if not question.strip() and not (name and field):
            return {**result, 'status': 'empty_query'}
        if method == 'bm25' and (name is not None or field is not None):
            raise ValueError('Explicit filters require name_field mode')
        if (name is not None and not isinstance(name, str)) or (field is not None and not isinstance(field, str)):
            raise ValueError('Name and field must be text')
        query_names = name_spans(name or question, self.names)
        checked_names, variants, rejected = context_check(normalized(name or question), query_names,
            field_words=[word for aliases in INTENTS.values() for word in aliases])
        if name:
            # Exact glyph-normalized names only; no fuzzy or parent-name substitution.
            checked_names = [n for n in self.names if name_key(n) == name_key(name)]
        if variants:
            return {**result, 'status': 'variant_scope_unresolved', 'lookup_complete': False,
                    'requested_variants': variants,
                    'message': '未解析指定炮制品范围；不能直接使用母药字段。请核对相关条目原页。',
                    'related_entries': self.related_entries([v['parent_name'] for v in variants])}
        if rejected:
            return {**result, 'status': 'name_not_resolved', 'lookup_complete': False,
                    'message': '药名上下文无法确认，请明确使用候选药名查询。'}
        result['query_interpretation']['name_forms'] = [
            {'query_form': normalized(name or question)[a:b], 'indexed_name': n,
             'rule': 'script_variant' if normalized(name or question)[a:b] != normalized(n)
                     else 'exact_or_nfkc_whitespace'}
            for a, b, n in query_names if n in checked_names]
        if method == 'bm25':
            if name is not None or field is not None:
                raise ValueError('Explicit filters require name_field mode')
            tokens = sorted(set(terms(question)))
            if not tokens:
                return {**result, 'status': 'no_match'}
            expression = ' OR '.join('"' + token + '"' for token in tokens)
            rows = self.connection.execute(
                'SELECT c.*, bm25(fts,3.0,2.0,1.0) AS rank FROM fts '
                'JOIN chunks c ON c.rowid=fts.rowid WHERE fts MATCH ? ORDER BY rank,c.chunk_id LIMIT ?',
                (expression, k)).fetchall()
        else:
            names = checked_names
            if not names:
                return {**result, 'status': 'name_not_resolved'}
            if len(names) > 1:
                if len({name_key(n) for n in names}) == 1:
                    return {**result, 'status': 'ambiguous_name', 'names': names,
                            'message': '字形归一化匹配多个来源药名，不能自动合并或选择。'}
                return {**result, 'status': 'multiple_names', 'names': names}
            entry_ids = [r[0] for r in self.connection.execute('SELECT entry_id FROM entries WHERE name=?', (names[0],))]
            if len(entry_ids) > 1:
                return {**result, 'status': 'ambiguous_name', 'entry_ids': entry_ids}
            if field:
                labels = [field] if field in INTENTS or field == '性味' else []
            else:
                labels = [label for label, aliases in INTENTS.items() if any(a in normalized(question) for a in aliases)]
                result['query_interpretation']['field_matches'] = [
                    {'query_term': alias, 'field': label}
                    for label, aliases in INTENTS.items() for alias in aliases if alias in normalized(question)]
            if not labels:
                return {**result, 'status': 'field_not_resolved', 'names': names}
            if '性味与归经' in labels:
                labels.append('性味')
            labels = sorted(set(labels))
            placeholders = ','.join('?' for _ in labels)
            rows = self.connection.execute(
                f"SELECT * FROM chunks WHERE entry_id=? AND label IN ({placeholders}) "
                "AND scope NOT LIKE 'appendix:%' ORDER BY field_index", (entry_ids[0], *labels)).fetchall()
            if not rows:
                return {**result, 'status': 'field_not_observed', 'names': names, 'fields': labels}
            # Multiple occurrences can refer to processed variants with different values.
            if any(sum(r['label'] == label for r in rows) > 1 for label in labels):
                result['status'] = 'ambiguous_field'
                result['message'] = '同一药名下有重复字段，可能涉及炮制品；请核对原文范围。'
            result.update(names=names, fields=labels, field_occurrences=len(rows))
            rows = rows[:k]
        for row in rows:
            hit = self.hit(row)
            target = reference_target(row['text'])
            if target or not field_body(row['text']):
                hit['incomplete_reason'] = 'reference_unresolved' if target else 'empty_field_body'
                hit['reference_target_candidate'] = target
                # Headers may share a following reference, e.g. 用量/注意/贮藏 同甘草.
                # Show the separate source witness without copying its value or inferring equivalence.
                witnesses = [] if target else self.connection.execute(
                    'SELECT * FROM chunks WHERE entry_id=? AND scope=? AND field_index>? AND field_index<=? ORDER BY field_index',
                    (row['entry_id'], row['scope'], row['field_index'], row['field_index'] + 2)).fetchall()
                for witness in witnesses:
                    candidate = reference_target(witness['text'])
                    if candidate:
                        hit['reference_target_candidate'] = candidate
                        hit['reference_witness'] = self.hit(witness)
                        hit['incomplete_reason'] = 'reference_unresolved'
                        break
                    if field_body(witness['text']):
                        break
                result.setdefault('unresolved_evidence', []).append(hit)
            else:
                result['hits'].append(hit)
        if result.get('unresolved_evidence'):
            result.update(status='reference_unresolved' if not result['hits'] else 'partial_evidence',
                          lookup_complete=False,
                          message='部分字段仅有标题或引用其他条目，引用尚未展开，不能视为完整字段查询。')
        elif not result['hits']:
            result['status'] = 'no_match'
        return result

    @staticmethod
    def hit(row):
        return {'chunk_id': row['chunk_id'], 'entry_id': row['entry_id'], 'name': row['name'],
                'label': row['label'], 'scope': row['scope'], 'text': row['text'], 'raw_text': row['raw_text'],
                **json.loads(row['payload']), 'score': -row['rank'] if 'rank' in row.keys() else None}

    def related_entries(self, names):
        result = []
        for name in sorted(set(names)):
            for row in self.connection.execute('SELECT * FROM chunks WHERE name=? ORDER BY field_index LIMIT 1', (name,)):
                result.append({'name': row['name'], 'entry_id': row['entry_id'],
                               'chunk_id': row['chunk_id'], 'title_evidence': json.loads(row['payload'])['title_evidence']})
        return result
