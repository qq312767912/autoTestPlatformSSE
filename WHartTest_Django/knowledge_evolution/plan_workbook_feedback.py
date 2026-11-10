"""在方案覆盖大纲原页逐条提供人工反馈与自动统计。"""
import re
from copy import copy
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill, Border, Side
from openpyxl.formatting.rule import FormulaRule
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.utils import get_column_letter, quote_sheetname


def _entries(text):
    # 保留标题、正文和续行；仅在显式编号处拆分，不要求 TP 编号。
    lines = str(text).splitlines()
    result = []
    for line in lines:
        if re.match(r'^\s*(?:\d+(?:\.\d+)*[、.．）)]|\[?TP-\d+)', line):
            result.append(line.strip())
        elif re.match(r'^\s*[一二三四五六七八九十]+[、．.]', line):
            result.append(line.strip())
        elif result:
            result[-1] += '\n' + line
        elif line.strip():
            result.append(line.strip())
    return result


def enhance_plan_workbook(path):
    if not str(path).lower().endswith('.xlsx'):
        return
    book = load_workbook(path)
    if '方案标记统计' in book.sheetnames or '方案确认处理结果' in book.sheetnames:
        return
    sources = []
    for sheet in list(book):
        labels = [c for row in sheet for c in row if c.value and str(c.value).strip() == '测试覆盖大纲']
        if not labels:
            continue
        label = labels[0]
        section_end = label.row
        for merged in sheet.merged_cells.ranges:
            if label.coordinate in merged:
                section_end = merged.max_row
        targets = {}
        for row in sheet.iter_rows(min_row=label.row, max_row=section_end):
            for cell in row:
                if cell.column > label.column and cell.value:
                    targets[cell.row] = (cell.column, _entries(cell.value))
        if not targets:
            continue
        old_columns = sheet.max_column
        original = list(sheet.merged_cells.ranges)
        snapshots = [[(c.value, copy(c._style)) for c in row] for row in sheet]
        heights = {r: sheet.row_dimensions[r].height for r in range(1, sheet.max_row+1)}
        for merged in original:
            sheet.unmerge_cells(str(merged))
        sheet.delete_rows(1, sheet.max_row)
        mapping = {}
        marked = []
        groups = {}
        current_group = sheet.title
        for number, cells in enumerate(snapshots, 1):
            column, entries = targets.get(number, (0, []))
            mapping[number] = sheet.max_row + 1 if number > 1 else 1
            start = mapping[number]
            for offset in range(max(1, len(entries))):
                dest = start + offset
                for col, (value, style) in enumerate(cells, 1):
                    cell = sheet.cell(dest, col, entries[offset] if col == column and entries else value)
                    cell._style = copy(style)
                sheet.row_dimensions[dest].height = max(42, min(100, len(entries[offset]) // 65 * 18 + 36)) if entries else heights[number]
                if entries and re.match(r'^[一二三四五六七八九十]+[、．.]', entries[offset]):
                    current_group = entries[offset].split('\n')[0]
                if entries and re.match(r'^\s*(?:\d|\[?TP-)', entries[offset]):
                    marked.append(dest)
                    groups[dest] = current_group
        for merged in original:
            start = mapping[merged.min_row]
            end = mapping[merged.max_row] + max(1, len(targets.get(merged.max_row, (0, []))[1])) - 1
            if merged.min_row in targets and merged.min_col == targets[merged.min_row][0]:
                for r in range(start, end+1):
                    if merged.max_col > merged.min_col:
                        sheet.merge_cells(start_row=r, end_row=r, start_column=merged.min_col, end_column=merged.max_col)
            else:
                sheet.merge_cells(start_row=start, end_row=end, start_column=merged.min_col, end_column=merged.max_col)
        for offset, title in enumerate(['人工标记结果', '修改点', '不采纳原因'], 1):
            col = old_columns + offset
            sheet.cell(2, col, title)
            sheet.cell(2, col).fill = PatternFill('solid', fgColor='FFF2CC')
            sheet.cell(2, col).font = Font(bold=True, color='9C5700')
            sheet.column_dimensions[get_column_letter(col)].width = 20 if offset == 1 else 45
            for r in marked:
                sheet.cell(r, col).fill = PatternFill('solid', fgColor='FFF2CC')
                sheet.cell(r, col).alignment = Alignment(wrap_text=True, vertical='top')
        letter = get_column_letter(old_columns+1)
        validation = DataValidation(type='list', formula1='"采纳,不采纳,待修改"', allow_blank=True)
        validation.showErrorMessage = True
        validation.errorStyle = 'stop'
        sheet.add_data_validation(validation)
        for r in marked:
            validation.add(f'{letter}{r}')
            for verdict, color in [('采纳','E2F0D9'), ('不采纳','FCE4D6'), ('待修改','FFF2CC')]:
                sheet.conditional_formatting.add(f'{letter}{r}:{get_column_letter(old_columns+3)}{r}', FormulaRule(formula=[f'${letter}{r}="{verdict}"'], fill=PatternFill('solid', fgColor=color)))
        sheet.print_area = f'A1:{get_column_letter(old_columns+3)}{sheet.max_row}'
        sources.extend((sheet.title, f'{letter}{r}', groups[r]) for r in marked)
    if not sources:
        return
    stats = book.create_sheet('方案确认处理结果')
    stats.sheet_properties.tabColor = '366965'
    stats.sheet_view.showGridLines = False
    stats.merge_cells('A1:G1')
    stats['A1'] = '测试方案确认处理结果'
    stats.merge_cells('A2:G2')
    stats['A2'] = '根据测试覆盖大纲的人工标记自动统计；空白为未标记。父条目和子条目各计一条，采纳率＝采纳条数÷全部条目数。'
    stats.merge_cells('A3:G3')
    stats['A3'] = '填写说明：待修改请填写修改点；不采纳请填写原因。填写原方案页后，Excel / WPS 将自动更新本页。'
    stats.append(['总体统计', '数值'])
    def count_formula(entries, verdict):
        terms = [f'COUNTIF({quote_sheetname(name)}!{cell},"{verdict}")' for name, cell, _ in entries]
        return '=' + '+'.join(terms) if terms else '=0'
    stats.append(['大纲条目总数', len(sources)])
    stats.append(['已标记', '=SUM(B7:B9)'])
    for verdict in ('采纳', '不采纳', '待修改'):
        stats.append([verdict, count_formula(sources, verdict)])
    stats.append(['未标记', count_formula(sources, '')])
    stats.append(['采纳率', '=IFERROR(B7/B5,0)'])
    stats.append(['标记完成率', '=IFERROR(B6/B5,0)'])
    stats['B11'].number_format = stats['B12'].number_format = '0.00%'
    stats.append([])
    stats.append(['大纲模块', '条目总数', '采纳', '不采纳', '待修改', '未标记', '采纳率'])
    for group in dict.fromkeys(entry[2] for entry in sources):
        entries = [entry for entry in sources if entry[2] == group]
        r = stats.max_row + 1
        stats.append([group, len(entries), *(count_formula(entries, v) for v in ('采纳','不采纳','待修改','')), f'=IFERROR(C{r}/B{r},0)'])
        stats.cell(r,7).number_format = '0.00%'
    thin = Side(style='thin', color='D5E2EC')
    for row in stats.iter_rows(min_row=4):
        for cell in row:
            cell.font = Font(name='微软雅黑', size=11, color='243E50')
            cell.alignment = Alignment(vertical='center', wrap_text=True, horizontal='left' if cell.column == 1 else 'right')
            cell.border = Border(left=thin, right=thin, top=thin, bottom=thin)
            cell.fill = PatternFill('solid', fgColor='F6FAFC' if cell.row % 2 == 0 else 'FFFFFF')
        stats.row_dimensions[row[0].row].height = 30
    for r in (4,14):
        for cell in stats[r]:
            cell.fill = PatternFill('solid', fgColor='366965')
            cell.font = Font(name='微软雅黑', bold=True, color='FFFFFF', size=11)
            cell.alignment = Alignment(horizontal='center', vertical='center')
    stats['A1'].font = Font(name='微软雅黑', size=20, bold=True, color='FFFFFF')
    stats['A1'].fill = PatternFill('solid', fgColor='366965')
    stats['A1'].alignment = Alignment(vertical='center')
    stats.row_dimensions[1].height = 44
    for r in (2,3):
        stats.row_dimensions[r].height = 32
        stats.cell(r,1).font = Font(name='微软雅黑', size=10, italic=True, color='366965')
        stats.cell(r,1).fill = PatternFill('solid', fgColor='EBF3F3')
        stats.cell(r,1).alignment = Alignment(wrap_text=True, vertical='center')
    for r, color in ((7,'E2F0D9'),(8,'FCE4D6'),(9,'FFF2CC'),(11,'E2F0D9'),(12,'EBF3F3')):
        for cell in stats[r][:2]:
            cell.fill = PatternFill('solid', fgColor=color)
            cell.font = Font(name='微软雅黑', bold=True, color='243E50')
    stats.column_dimensions['A'].width = 46
    for col in 'BCDEFG':
        stats.column_dimensions[col].width = 16
    stats.freeze_panes = 'B15'
    stats.auto_filter.ref = f'A14:G{stats.max_row}'
    stats.print_options.horizontalCentered = True
    stats.sheet_properties.pageSetUpPr.fitToPage = True
    stats.page_setup.orientation = 'landscape'
    stats.page_setup.paperSize = stats.PAPERSIZE_A4
    stats.page_setup.fitToWidth = 1
    stats.page_setup.fitToHeight = 0
    stats.print_area = f'A1:G{stats.max_row}'
    book.save(path)
