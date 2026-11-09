"""Locate a finding in actual diff code; never treat an empty line as evidence."""
import re


def locate_diff(finding, diff_text, context_lines=6):
    rows=[]; old=new=None
    for raw in str(diff_text or '').splitlines():
        h=re.match(r'@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@',raw)
        if h:
            old,new=int(h[1]),int(h[2]);continue
        if old is None or raw.startswith(('---','+++','\\')):continue
        kind='added' if raw.startswith('+') else 'removed' if raw.startswith('-') else 'context'
        rows.append({'type':kind,'old_line':old if kind!='added' else None,'new_line':new if kind!='removed' else None,'content':raw[1:] if raw[:1] in '+- ' else raw})
        old+=kind!='added';new+=kind!='removed'
    if not rows:return {}
    evidence=str(finding.get('evidence') or '')
    needles=[x.strip() for x in re.findall(r'`([^`]+)`',evidence) if len(x.strip())>=4]
    needles += [x.strip() for x in evidence.splitlines() if len(x.strip())>=8]
    tokens=set(re.findall(r'[A-Za-z_$][\w$]{3,}',evidence)) - {'swift','code','self','true','false','return','private','public','static','func','class','let','var','await','Task'}
    def score(row):
        code=row['content'].strip()
        if not code or code.startswith(('//','#','/*','*','import ')) or re.fullmatch(r'[{}();,]+',code):return 0
        exact=max([len(n) for n in needles if n in code]+[0])
        overlap=len(tokens & set(re.findall(r'[A-Za-z_$][\w$]{3,}',code)))
        return exact*10+overlap*5
    scores=[score(r) for r in rows];best=max(scores)
    target=finding.get('line_start');side='old_line' if finding.get('line_side')=='old' else 'new_line'
    if best:
        candidates=[i for i,v in enumerate(scores) if v==best]
        anchor=min(candidates,key=lambda i:abs((rows[i][side] or 0)-(target or 0)))
        located=True
    else:
        anchor=next((i for i,r in enumerate(rows) if target is not None and r[side]==target),None)
        if anchor is not None:
            code=rows[anchor]['content'].strip()
            if not code or code.startswith(('//','#','/*','*','import ')) or re.fullmatch(r'[{}();,]+',code):
                anchor=None
        located=anchor is not None
        if anchor is None:return {'diff_location_status':'unresolved','related_diff':str(diff_text),'change_lines':[], 'line_start':None}
    a=max(0,anchor-context_lines);b=min(len(rows),anchor+context_lines+1)
    # Do not cross an omitted hunk region.
    for i in range(anchor,a,-1):
        prev,cur=rows[i-1],rows[i]
        if cur['new_line'] is not None and prev['new_line'] is not None and cur['new_line']-prev['new_line']>1:a=i;break
    for i in range(anchor,b-1):
        cur,nxt=rows[i],rows[i+1]
        if nxt['new_line'] is not None and cur['new_line'] is not None and nxt['new_line']-cur['new_line']>1:b=i+1;break
    excerpt=rows[a:b]
    olds=[r['old_line'] for r in excerpt if r['old_line'] is not None];news=[r['new_line'] for r in excerpt if r['new_line'] is not None]
    header=f"@@ -{olds[0] if olds else 0},{len(olds)} +{news[0] if news else 0},{len(news)} @@"
    selected=rows[anchor];side='old' if selected['type']=='removed' else 'new'
    return {'diff_header':header,'change_lines':excerpt,'line_side':side,'line_start':selected[side+'_line'],
            'diff_location_status':'evidence' if best else 'line',
            'related_diff':'\n'.join([header]+[{'added':'+','removed':'-','context':' '}[r['type']]+r['content'] for r in excerpt])}
