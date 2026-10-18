function citations(card,hit,kind,label){
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
 });card.append(details);
}
function render(hit){const card=el('article');card.append(el('h2',`${hit.name} · ${hit.label==='origin'?'来源':hit.label}`),el('span','未人工审核','badge'));
 if(hit.raw_name!==hit.name)card.append(el('p',`OCR 药名：${hit.raw_name} → AI 修订候选：${hit.name}`));
 card.append(el('p',`范围：${hit.scope} · 人工审核状态：${hit.review_status} · 本字段 AI 修订 ${hit.field_overlay_ids.length} 项；条目累计修订 ${hit.entry_overlay_ids.length} 项`,'muted meta'));
 if(hit.raw_flags.length)card.append(el('p','原始检查标记：'+hit.raw_flags.join('、'),'muted meta'));
 const texts=el('div',undefined,'texts');for(const [title,text]of [['OCR 原文（不含 AI 文本修订）',hit.raw_text],['研究视图（包含已启用 AI 修订）',hit.text]]){const panel=el('section');panel.append(el('h3',title),el('pre',text));texts.append(panel)}card.append(texts);
 if(hit.raw_text===hit.text)card.append(el('p','本字段文本无 AI 修改；药名或其他字段可能存在修订。','muted'));
 citations(card,hit,'evidence','字段原页证据');citations(card,hit,'title_evidence','标题原页证据');return card;
}
