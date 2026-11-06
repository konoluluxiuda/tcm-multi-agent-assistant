function citations(card,hit,kind,label,destination=card){
 const values=hit[kind]||[];if(!values.length)return;
 const details=el('details');details.append(el('summary',`${label}（${values.length} 处；逐处核对，红框为定位范围）`));
 values.forEach((c,i)=>{const s=c.source,box=el('div',undefined,'citation');
  const source=s.source_document==='high'?'分册 PDF':s.source_document==='full'?'全本 PDF':s.source_document;
  box.append(el('div',`${source} · 物理第 ${s.source_pdf_page} 页 · 药材章节第 ${s.chapter_pdf_page??c.chapter_page??'未知'} 页`));
  const printed=s.observed_printed_page!=null?`识别印刷页：${s.observed_printed_page}（仍需人工核对）`:`预期印刷页：${s.expected_printed_page??'未知'}（未逐页确认）`;
  box.append(el('p',`${printed} · 来源 SHA256：${s.source_sha256.slice(0,16)}…`, 'muted meta'));
  const button=el('button','查看原页');button.type='button';
  button.addEventListener('click',async()=>{button.disabled=true;button.textContent='加载原页……';
   try{const response=await fetch((card.dataset.pageEndpoint||'/api/page')+'?'+new URLSearchParams({chunk:hit.chunk_id,kind,index:i}));
    if(!response.ok){const data=await response.json();throw new Error(data.error||'原页加载失败')}
    const blob=await response.blob(),url=URL.createObjectURL(blob),image=el('img');image.alt=`${source}物理第 ${s.source_pdf_page} 页，红框为证据定位`;
    image.onload=()=>URL.revokeObjectURL(url);image.onerror=()=>{URL.revokeObjectURL(url);image.remove();button.disabled=false;button.textContent='图片解码失败，点击重试'};image.src=url;box.append(image);button.remove();
   }catch(error){button.disabled=false;button.textContent='重试查看原页';let err=box.querySelector('.error');if(!err){err=el('p',undefined,'error');box.append(err)}err.textContent=error.message}
  });box.append(button);details.append(box);
 });destination.append(details);
}
// Display-only conversion of complete numeric quantity formulas. Unknown math
// stays literal; never evaluate LaTeX, convert units or change source records.
function readableEvidenceText(value){
 if(typeof value!=='string')return value;
 return value.replace(/(?<!\$)\$([^$\n]+)\$(?!\$)/g,(original,body)=>{
  const match=body.trim().match(/^(\d+(?:\.\d+)?)(?:\s*(?:\\sim|~)\s*(\d+(?:\.\d+)?))?\s*(?:\\mathrm\{(g|mg|kg|mL|ml|L|μg|µg|ug)\}|(g|mg|kg|mL|ml|L|μg|µg|ug))$/);
  return match?match[1]+(match[2]?'–'+match[2]:'')+' '+(match[3]||match[4]):original;
 });
}
function render(hit,options={}){const card=el('article');card.dataset.researchHit='true';card.append(el('h3',`${hit.name} · ${hit.label==='origin'?'来源':hit.label}`));
 let displayed=readableEvidenceText(hit.text);
 const fieldPrefix='【'+hit.label+'】';
 if(options.compact && displayed.startsWith(fieldPrefix))displayed=displayed.slice(fieldPrefix.length).trimStart();
 const text=el('div',displayed);text.style.whiteSpace='pre-wrap';text.dataset.resultText='true';card.append(text);
 const proof=el('details');proof.dataset.resultDetails='true';proof.append(el('summary','出处与校订信息'));
 if(displayed!==hit.text)proof.append(el('h4','来源文本（保留原始格式）'),el('pre',hit.text));
 if(hit.raw_text!==hit.text)proof.append(el('h4','OCR原文'),el('pre',hit.raw_text));
 if(hit.raw_name!==hit.name)proof.append(el('p',`OCR药名：${hit.raw_name}；当前名称：${hit.name}`));
 proof.append(el('p',`审核状态：${hit.review_status}；字段校订 ${hit.field_overlay_ids.length} 项；条目校订 ${hit.entry_overlay_ids.length} 项。药典摘录尚需按原页核对。`));
 if(hit.raw_flags.length)proof.append(el('p','检查标记：'+hit.raw_flags.join('、')));
 citations(card,hit,'evidence','字段原页',proof);citations(card,hit,'title_evidence','标题原页',proof);card.append(proof);return card;
}

function exportEvidence(container,data){
 const interpreted=data?.query_interpretation;
 if(interpreted){const descriptions=[];
  for(const item of interpreted.name_forms||[])if(item.rule==='script_variant')descriptions.push(`药名字形：${item.query_form} → ${item.indexed_name}`);
  for(const item of interpreted.field_matches||[])if(item.query_term!==item.field)descriptions.push(`字段：${item.query_term} → ${item.field}`);
  if(descriptions.length){const info=el('details');info.dataset.resultDetails='true';info.append(el('summary','检索说明'),el('p',[...new Set(descriptions)].join('；')+'。仅用于定位来源。'));container.append(info)}
 }
 const allowed=['candidates','partial_evidence','reference_unresolved','ambiguous_field','field_not_observed','variant_scope_unresolved'];
 if(!data||data.method!=='name_field'||!allowed.includes(data.status)||!/^[a-f0-9]{64}$/.test(data.index_sha256||''))return;
 const button=el('button','导出本轮证据 Markdown');button.type='button';button.dataset.evidenceExport='true';
 const note=el('p','','muted');
 button.addEventListener('click',async()=>{button.disabled=true;note.textContent='正在核对来源并导出……';
  try{const response=await fetch('/api/research/report?'+new URLSearchParams({q:data.question,snapshot:data.index_sha256}));
   if(!response.ok){const error=await response.json();throw new Error(error.error||'导出失败，请重新检索')}
   const blob=await response.blob(),url=URL.createObjectURL(blob),link=el('a');link.href=url;link.download='tcm-evidence.md';
   document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);
   note.textContent='已导出本轮证据；未人工审核，请按页码核对原文。';
  }catch(error){note.textContent=error.message}finally{button.disabled=false}
 });container.append(button,note);
}
