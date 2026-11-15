/* Minimal safe Markdown renderer. Builds DOM nodes only, never raw HTML. */
'use strict';
(function(){
const MAX_CHARS=250000;
const $tag=(name)=>document.createElement(name);
function inline(parent,raw){
 const source=String(raw||'');
 const tokens=/(\*\*[^*\n]+\*\*|`[^`\n]+`|\*[^*\n]+\*|(?<![\w])_[^_\n]+_(?![\w])|\[[^\]\n]+\]\(https?:\/\/[^\s)]+\))/g;
 let last=0,match;
 while((match=tokens.exec(source))){
  if(match.index>last)parent.append(document.createTextNode(source.slice(last,match.index)));
  const s=match[0];let el;
  if(s.startsWith('**')){el=$tag('strong');el.textContent=s.slice(2,-2);}
  else if(s.startsWith('`')){el=$tag('code');el.textContent=s.slice(1,-1);}
  else if(s.startsWith('*')||s.startsWith('_')){el=$tag('em');el.textContent=s.slice(1,-1);}
  else {const pos=s.indexOf('](');const href=s.slice(pos+2,-1);el=$tag('a');el.textContent=s.slice(1,pos);try{const url=new URL(href);if(!['https:','http:'].includes(url.protocol))throw Error();el.href=url.href;el.target='_blank';el.rel='noopener noreferrer';}catch(_){el=$tag('span');el.textContent=s;}}
  parent.append(el);last=tokens.lastIndex;
 }
 if(last<source.length)parent.append(document.createTextNode(source.slice(last)));
}
const isSep=(s)=>/^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$/.test(s.trim());
const cells=(s)=>s.trim().replace(/^\|/,'').replace(/\|$/,'').split('|').map(x=>x.trim());
function render(target,input){
 const text=String(input||'').slice(0,MAX_CHARS).replace(/\r\n?/g,'\n');
 const lines=text.split('\n');const root=document.createDocumentFragment();
 let i=0;
 while(i<lines.length){
  const raw=lines[i], s=raw.trim();
  if(!s){i++;continue;}
  if(/^```/.test(s)){
   const lang=s.slice(3).trim().split(/\s+/)[0].replace(/[^\w+.-]/g,'').slice(0,30);const body=[];i++;
   while(i<lines.length&&!/^\s*```\s*$/.test(lines[i]))body.push(lines[i++]);
   if(i<lines.length)i++;
   const pre=$tag('pre'),code=$tag('code');if(lang)code.className='language-'+lang;code.textContent=body.join('\n');pre.append(code);root.append(pre);continue;
  }
  if(i+1<lines.length&&raw.includes('|')&&isSep(lines[i+1])){
   const headings=cells(raw),table=$tag('table'),thead=$tag('thead'),tr=$tag('tr');
   for(const cell of headings){const th=$tag('th');inline(th,cell);tr.append(th);}thead.append(tr);table.append(thead);
   const tbody=$tag('tbody');i+=2;
   while(i<lines.length&&lines[i].includes('|')&&lines[i].trim()){
    const row=$tag('tr');for(const c of cells(lines[i]).slice(0,headings.length)){const td=$tag('td');inline(td,c);row.append(td);}tbody.append(row);i++;
   }
   table.append(tbody);const wrap=$tag('div');wrap.className='md-table-scroll';wrap.append(table);root.append(wrap);continue;
  }
  const heading=s.match(/^(#{1,6})\s+(.+)$/);
  if(heading){const h=$tag('h'+heading[1].length);inline(h,heading[2]);root.append(h);i++;continue;}
  if(/^([-*_]\s*){3,}$/.test(s)){root.append($tag('hr'));i++;continue;}
  if(/^>\s?/.test(s)){const block=$tag('blockquote');while(i<lines.length&&/^\s*>/.test(lines[i])){const p=$tag('p');inline(p,lines[i].replace(/^\s*>\s?/,''));block.append(p);i++;}root.append(block);continue;}
  const bullet=s.match(/^([-*+]\s+|\d+\.\s+)(.*)$/);
  if(bullet){const ordered=/^\d/.test(bullet[1]),list=$tag(ordered?'ol':'ul');
   while(i<lines.length){const m=lines[i].trim().match(/^([-*+]\s+|\d+\.\s+)(.*)$/);if(!m||(/^\d/.test(m[1])!==ordered))break;const li=$tag('li');inline(li,m[2]);list.append(li);i++;}root.append(list);continue;
  }
  const para=[];
  while(i<lines.length&&lines[i].trim()&&!/^\s*(#{1,6}\s|```|>\s?|[-*+]\s|\d+\.\s)/.test(lines[i])&&!(i+1<lines.length&&lines[i].includes('|')&&isSep(lines[i+1]))){para.push(lines[i].trim());i++;}
  if(!para.length){para.push(lines[i].trim());i++;}
  const p=$tag('p');inline(p,para.join(' '));root.append(p);
 }
 target.replaceChildren(root);
}
window.renderTaskMarkdown=render;
let renderTimer=null,latest='';
window.scheduleStreamRender=(target,text)=>{latest=text;if(renderTimer!==null)return;renderTimer=setTimeout(()=>{renderTimer=null;render(target,latest);target.scrollTop=target.scrollHeight;},100);};
window.flushStreamRender=(target)=>{if(renderTimer!==null){clearTimeout(renderTimer);renderTimer=null;}render(target,latest);};
let raw='';
window.showTaskResult=(value)=>{raw=String(value||'');render(document.getElementById('detail-result'),raw);document.getElementById('detail-raw').textContent=raw;setMode('preview');};
function setMode(mode){const preview=mode==='preview';document.getElementById('detail-result').hidden=!preview;document.getElementById('detail-raw').hidden=preview;document.getElementById('detail-preview-btn').setAttribute('aria-pressed',String(preview));document.getElementById('detail-raw-btn').setAttribute('aria-pressed',String(!preview));}
document.addEventListener('DOMContentLoaded',()=>{document.getElementById('detail-preview-btn').addEventListener('click',()=>setMode('preview'));document.getElementById('detail-raw-btn').addEventListener('click',()=>setMode('raw'));});
})();
