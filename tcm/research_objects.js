/* User assertions stay distinct from verified source identity. */
window.ResearchObjectReview = {
    mount(form, question, articles) {
        const node=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n};
        const details=node('details');details.className='object-review';details.append(node('summary','可选：逐篇核对研究对象（用户标注）'));
        details.append(node('p','先填写药典查询，再添加标注。请选择你判断的文献对象和关系，并引用该篇标题或摘要中的12–300个原文字；系统只检查摘录存在，不批准对象一致。标注随文件下载，不保存到服务器。'));
        const warning=node('p');warning.setAttribute('role','status');details.append(warning);
        let target=null;
        const rows=[];
        function select(label,choices) {
            const wrap=node('label',label),input=node('select');
            for(const [value,text] of choices){const option=node('option',text);option.value=value;input.append(option)}
            wrap.append(input);return [wrap,input];
        }
        for(const article of articles.slice(0,5)) {
            const box=node('fieldset');box.dataset.reviewPmid=article.pmid;box.append(node('legend','PMID '+article.pmid));
            const enabled=node('input');enabled.type='checkbox';enabled.setAttribute('aria-label','添加PMID '+article.pmid+'的用户标注');
            const enableLabel=node('label',' 添加本条用户标注');enableLabel.prepend(enabled);box.append(enableLabel);
            const controls=node('div');controls.hidden=true;
            const [kindLabel,kind]=select('文献对象类型：',[['unknown','未确认'],['raw_herb','单味原药材'],['processed_herb','炮制品/饮片'],['formula','复方/组合'],['extract','提取物'],['constituent','成分']]);
            const [relationLabel,relation]=select('与本次药典对象的关系：',[['unverified','尚未确认'],['possibly_related','我认为可能相关'],['scope_differs','我认为范围不同'],['same_object_claimed','我主张一致（系统未验证）']]);
            const texts=[['title',null,article.title||''],...(article.abstract||[]).slice(0,100).map((a,i)=>['abstract',i,a.text||''])];
            const [sourceLabel,source]=select('摘录来源：',texts.map((s,i)=>[String(i),s[0]==='title'?'标题':'摘要段 '+(s[1]+1)]));
            const preview=node('pre',texts[0][2].slice(0,1600));preview.style.maxHeight='140px';preview.style.overflow='auto';
            const quoteLabel=node('label','原文摘录（精确复制12–300字）：'),quote=node('textarea');quote.maxLength=300;quote.minLength=12;quote.rows=3;quote.style.width='100%';quote.style.boxSizing='border-box';quoteLabel.append(quote);
            const copy=node('button','引用所选原文前300字');copy.type='button';copy.addEventListener('click',()=>{quote.value=texts[Number(source.value)][2].slice(0,300)});
            source.addEventListener('change',()=>{preview.textContent=texts[Number(source.value)][2].slice(0,1600);quote.value=''});
            enabled.addEventListener('change',()=>{
                if(enabled.checked&&!question.value.trim()){enabled.checked=false;warning.textContent='请先填写药典查询，再核对对象。';return}
                if(enabled.checked)target=question.value.trim();
                controls.hidden=!enabled.checked;
            });
            controls.append(kindLabel,node('br'),relationLabel,node('br'),sourceLabel,preview,quoteLabel,copy);box.append(controls);details.append(box);
            rows.push({article,enabled,controls,kind,relation,source,texts,quote});
        }
        question.addEventListener('input',()=>{
            if(rows.some(r=>r.enabled.checked)) {
                for(const r of rows){r.enabled.checked=false;r.controls.hidden=true;r.quote.value='';r.kind.value='unknown';r.relation.value='unverified'}
                target=null;warning.textContent='药典查询已修改，已取消旧对象标注；请针对新查询重新核对。';
            }
        });
        form.insertBefore(details,form.querySelector('button[type="submit"]'));
        return {
            collect() {
                const active=rows.filter(r=>r.enabled.checked);
                if(!active.length)return {};
                if(target!==question.value.trim())throw new Error('药典查询已改变，请重新核对对象标注。');
                const reviews=active.map(r=>{
                    if(r.quote.value.length<12)throw new Error('请提供至少12字的原文摘录，或取消该条标注。');
                    const source=r.texts[Number(r.source.value)];
                    return {pmid:r.article.pmid,kind:r.kind.value,relation:r.relation.value,source_field:source[0],abstract_index:source[1],quote:r.quote.value};
                });
                return {object_review_target:target,object_reviews:reviews};
            }
        };
    }
};
