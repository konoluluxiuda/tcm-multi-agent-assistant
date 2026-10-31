"""Immutable, text-only XLSX publication and exact read-back validation."""
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from tcm.dataops.data_access import canonical

SHEETS = ('结果', '问题', '来源', '运行说明')


def tables(result):
    goal = result['goal']
    fields = goal['attributes'] + goal['document_fields']
    header = ['来源ID', '名称', '对应状态']
    for f in fields:
        header += [f, f + '·状态', f + '·出处']
    rows = [header]
    for record in result['rows']:
        row = [record['source_id'], record['name'], record['mapping_status']]
        for f in fields:
            cell = record['cells'][f]
            value = cell['value']
            row += [canonical(value) if isinstance(value, (list, dict)) else '' if value is None else str(value),
                    cell['status'], ', '.join(cell['evidence_ids'])]
        rows.append(row)
    problems = [['来源ID', '字段', '问题类型', '说明']] + [[x['source_id'], x['field'], x['kind'], x['message']] for x in result['issues']]
    evidence = [['证据ID', '类型', '来源ID', '原始位置与文本']]
    evidence_items=result['evidence'].items()
    if result.get('research',{}).get('artifact_format')=='sorted-evidence-v1':
        evidence_items=sorted(evidence_items)
    for key, value in evidence_items:
        evidence.append([key, value['kind'], value['source_id'], canonical(value['source'])])
    info = [['项目', '值'], ['执行方式', result.get('execution_label', 'F：固定本地流程，0次模型调用')], ['目标', canonical(goal)],
            ['来源版本', canonical(result['source_revision'])], ['任务状态', result['status']],
            ['数据覆盖', canonical(result['coverage'])], ['身份说明', '唯一候选仍未经身份确认；两侧字段并列保留。'],
            ['校验范围', '源字段一致性和出处定位；不等于科学内容或OCR人工审核。']]
    output = dict(zip(SHEETS, (rows, problems, evidence, info)))
    if 'diff' in result:
        output['版本差异'] = [['来源ID', '字段', '旧值与状态', '新值与状态']] + [
            [d['source_id'], d['field'], canonical(d['before']), canonical(d['after'])] for d in result['diff']]
    if 'research' in result:
        selection=result['research']
        info.extend([['目标记录数',str(selection['target_count'])],['合格记录数',str(selection['qualified_count'])],
                     ['候选范围',str(selection['candidate_pool_count'])],['完整性','候选核对不代表穷尽全库或身份确认']])
        output['候选核对']=[['来源ID','核对状态','满足字段条件','已选择']] + [[
            x['source_id'],x['status'],str(x['eligible']),str(x['selected'])] for x in selection['candidate_states']]
    return output


def verify(path, result):
    from openpyxl import load_workbook
    workbook = load_workbook(path, read_only=True, data_only=False)
    try:
        expected = tables(result)
        if workbook.sheetnames != list(expected):
            raise ValueError('Workbook sheet mismatch')
        cells = 0
        for name, table in expected.items():
            sheet = workbook[name]
            actual = list(sheet.iter_rows())
            if len(actual) != len(table):
                raise ValueError('Workbook row mismatch')
            for actual_row, wanted in zip(actual, table):
                if len(actual_row) != len(wanted):
                    raise ValueError('Workbook column mismatch')
                for cell, value in zip(actual_row, wanted):
                    if cell.data_type == 'f' or (cell.value or '') != str(value):
                        raise ValueError('Workbook cell or source mismatch')
                    cells += 1
        return {'status': 'passed', 'sheets': len(expected), 'verified_cells': cells,
                'rows': len(result['rows']), 'method': 'reopen_compare_every_cell_v1'}
    finally:
        workbook.close()


def publish(directory, result, *, version=1, reconcile=False):
    from openpyxl import Workbook
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if type(version) is not int or version < 1:
        raise ValueError('Invalid artifact version')
    destination = directory / ('v' + str(version) + '.xlsx')
    if destination.exists():
        if not reconcile or destination.is_symlink():
            raise ValueError('Immutable artifact already exists')
        check = verify(destination, result)
        data = destination.read_bytes()
        return {'filename': destination.name, 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data),
                'version': version, 'verification': check}
    temporary = directory / (uuid4().hex + '.xlsx')
    book = Workbook()
    book.remove(book.active)
    try:
        for name, table in tables(result).items():
            sheet = book.create_sheet(name)
            for row_index, row in enumerate(table, 1):
                for index, value in enumerate(row, 1):
                    text = str(value)
                    if len(text) > 32767:
                        raise ValueError('Cell exceeds XLSX capacity; no truncation allowed')
                    # Explicit string storage preserves leading '=' without executing it.
                    cell = sheet.cell(row_index, index)
                    cell.value = text
                    cell.data_type = 's'
            sheet.freeze_panes = 'A2'
            sheet.auto_filter.ref = sheet.dimensions
        book.save(temporary)
        check = verify(temporary, result)
        data = temporary.read_bytes()
        with temporary.open('rb') as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        return {'filename': destination.name, 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data),
                'version': version, 'verification': check}
    finally:
        book.close()
        temporary.unlink(missing_ok=True)
