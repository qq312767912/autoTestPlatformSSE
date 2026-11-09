/** One locator for platform dialogs and exported reports, with recomputed hunk ranges. */
export function relevantDiffFragment(diff: string, targetLine?: number, evidence = '', side: 'old'|'new' = 'new'): string {
  type Row = {raw:string;old:number|null;new:number|null;hunk:number};
  const rows:Row[]=[];
  let old=0, current=0, hunk=0, active=false;
  for (const raw of String(diff || '').split(/\r?\n/)) {
    const match=raw.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
    if(match){old=Number(match[1]);current=Number(match[2]);hunk++;active=true;continue}
    if(!active || /^(---|\+\+\+|\\)/.test(raw))continue;
    rows.push({raw,old:raw.startsWith('+')?null:old,new:raw.startsWith('-')?null:current,hunk});
    if(!raw.startsWith('+'))old++;
    if(!raw.startsWith('-'))current++;
  }
  if(!rows.length)return diff;
  const ignored=new Set(['swift','code','self','true','false','return','private','public','static','func','class','await','Task']);
  const tokens=new Set((evidence.match(/[A-Za-z_$][\w$]{3,}/g)||[]).filter(t=>!ignored.has(t)));
  const needles=[...Array.from(evidence.matchAll(/`([^`]+)`/g),m=>m[1]),...evidence.split(/\r?\n/)].map(s=>s.trim()).filter(s=>s.length>=4);
  const scores=rows.map(row=>{
    const code=row.raw.slice(1).trim();
    if(!code || /^(\/\/|#|\/\*|\*|import\s)/.test(code) || /^[{}();,]+$/.test(code))return 0;
    const exact=Math.max(0,...needles.filter(n=>code.includes(n)).map(n=>n.length));
    const words=new Set(code.match(/[A-Za-z_$][\w$]{3,}/g)||[]);
    return exact*10+Array.from(tokens).filter(t=>words.has(t)).length*5;
  });
  const best=Math.max(...scores);
  let anchor=-1;
  if(best>0){
    const matches=scores.map((score,i)=>score===best?i:-1).filter(i=>i>=0);
    anchor=matches.sort((a,b)=>Math.abs((rows[a][side]||0)-(targetLine||0))-Math.abs((rows[b][side]||0)-(targetLine||0)))[0];
  }else if(targetLine){
    anchor=rows.findIndex(row=>row[side]===targetLine);
    const code=anchor>=0?rows[anchor].raw.slice(1).trim():'';
    if(!code || /^(\/\/|#|\/\*|\*|import\s)/.test(code) || /^[{}();,]+$/.test(code))anchor=-1;
  }
  // No real anchor: show the full diff instead of claiming the file header is relevant.
  if(anchor<0)return diff;
  let from=Math.max(0,anchor-6),to=Math.min(rows.length,anchor+9);
  while(from<anchor && rows[from].hunk!==rows[anchor].hunk)from++;
  while(to>anchor+1 && rows[to-1].hunk!==rows[anchor].hunk)to--;
  const excerpt=rows.slice(from,to);
  const olds=excerpt.map(r=>r.old).filter((v):v is number=>v!==null);
  const news=excerpt.map(r=>r.new).filter((v):v is number=>v!==null);
  return [`@@ -${olds[0]??0},${olds.length} +${news[0]??0},${news.length} @@`,...excerpt.map(row=>row.raw)].join('\n');
}
