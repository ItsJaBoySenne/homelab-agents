const $ = id => document.getElementById(id);
let busy = false;
// HLA_UI_V5: one SVG icon set for the whole app (no emoji or unicode glyphs: they differ per device and need a
// font). ico(name) → an inline <svg class="ic">; withIcon(button, name, label) puts icon + label into a button.
const ICONS={clip:'<path d="m20.5 11.5-8.4 8.4a5 5 0 0 1-7.1-7.1l8.5-8.5a3.3 3.3 0 0 1 4.7 4.7l-8.4 8.4a1.7 1.7 0 0 1-2.4-2.4l7.7-7.7"/>',
 bell:'<path d="M6 9a6 6 0 1 1 12 0c0 6 2.5 8 2.5 8h-17S6 15 6 9"/><path d="M10.3 20.5a1.9 1.9 0 0 0 3.4 0"/>',
 clock:'<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
 sidebar:'<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><path d="M9.5 4.5v15"/>',
 x:'<path d="m6.5 6.5 11 11M17.5 6.5l-11 11"/>',
 repeat:'<path d="m17 3 3.5 3.5L17 10"/><path d="M4 11.5V10a3.5 3.5 0 0 1 3.5-3.5h13"/><path d="m7 21-3.5-3.5L7 14"/><path d="M20 12.5V14a3.5 3.5 0 0 1-3.5 3.5h-13"/>',
 list:'<path d="M9 6.5h11M9 12h11M9 17.5h11M4.5 6.5h.01M4.5 12h.01M4.5 17.5h.01"/>',
 stop:'<rect x="6.5" y="6.5" width="11" height="11" rx="2"/>',
 flag:'<path d="M5 21V4"/><path d="M5 4.5h11.5l-2 4 2 4H5"/>',
 plus:'<path d="M12 5v14M5 12h14"/>',
 refresh:'<path d="M20 12a8 8 0 1 1-2.6-5.9L20 8.5"/><path d="M20 3.5v5h-5"/>',
 more:'<circle cx="5.5" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="18.5" cy="12" r="1.2"/>',
 check:'<path d="m19.5 6.5-10 10-5-5"/>',
 alert:'<path d="M12 3.5 2.5 20h19L12 3.5z"/><path d="M12 10v4M12 17h.01"/>',
 gear:'<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
 file:'<path d="M14 3.5H7A2.5 2.5 0 0 0 4.5 6v12A2.5 2.5 0 0 0 7 20.5h10a2.5 2.5 0 0 0 2.5-2.5V9z"/><path d="M14 3.5V9h5.5"/>',
 image:'<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><circle cx="9" cy="10" r="1.8"/><path d="m20.5 16-5-5-8.5 8.5"/>',
 pause:'<path d="M9 6.5v11M15 6.5v11"/>',
 orb:'<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/>',
 arrow:'<path d="M5 12h14M13 6l6 6-6 6"/>',
 back:'<path d="M19 12H5M11 6l-6 6 6 6"/>',
 project:'<path d="M4 7.5 12 3.5l8 4v9l-8 4-8-4z"/><path d="M4 7.5 12 11.5l8-4M12 11.5v9"/>',
 chat:'<path d="M20.5 12a8 8 0 0 1-11.6 7.1L3.5 20.5l1.4-5A8 8 0 1 1 20.5 12z"/>',
 server:'<rect x="3.5" y="4.5" width="17" height="6.5" rx="1.5"/><rect x="3.5" y="13" width="17" height="6.5" rx="1.5"/><path d="M7 7.8h.01M7 16.2h.01"/>',
 agent:'<circle cx="12" cy="8" r="3.8"/><path d="M4.5 20.5a7.5 7.5 0 0 1 15 0"/>',
 task:'<circle cx="12" cy="12" r="2.6"/>',
 search:'<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2"/>',
 mic:'<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>',
 car:'<path d="M5 16.5V12l2-5h10l2 5v4.5"/><path d="M3.5 12h17v4.5h-17z"/><circle cx="7.5" cy="16.5" r="1.6"/><circle cx="16.5" cy="16.5" r="1.6"/>',
 power:'<path d="M12 3.5V12"/><path d="M6.4 6.6a8 8 0 1 0 11.2 0"/>'};
function ico(name){const s=document.createElementNS('http://www.w3.org/2000/svg','svg');s.setAttribute('viewBox','0 0 24 24');s.setAttribute('aria-hidden','true');
 s.setAttribute('class','ic ic-'+name);s.innerHTML=ICONS[name]||'';return s}
// People in logs: the shared dashboard login reads as "You" (an Authelia user keeps its name).
function actorName(a){return a==='dashboard_session'||a==='dashboard_session (via Chief)'?(a.includes('Chief')?'You (via Chief)':'You'):(a||'—')}
function withIcon(b,name,label){if(!b)return b;b.replaceChildren(ico(name));if(label!=null&&label!=='')b.append(document.createTextNode(' '),Object.assign(document.createElement('span'),{textContent:label}));return b}
// ── HLA_DIALOGS_V1: in-page dialogs instead of the browser's small confirm/prompt popups ─────────────────────
// uiConfirm(message,{title,ok,cancel,danger,detail})  → Promise<boolean>
// uiPrompt(title,{label,value,placeholder,multiline,rows,hint,ok,required,maxLength,message,danger}) → Promise<string|null>
// uiForm(title,[{name,label,type:'text'|'number'|'textarea'|'select',value,options,min,max,step,required,hint,placeholder,
//   rows,maxLength,pattern,patternMessage}],{ok,intro,danger}) → Promise<object|null>; null means cancelled.
// A native <dialog> opened with showModal(): focus stays inside, Escape cancels, the page behind is inert. One dialog
// at a time (later ones wait in line); all text is set with textContent. Without <dialog> support: native popups.
const DLG_NATIVE=!(typeof HTMLDialogElement==='function'&&typeof HTMLDialogElement.prototype.showModal==='function');
let dlgChain=Promise.resolve(),dlgSeq=0,dlgLastFocus=null;
// Where focus returns: a button that disabled itself while busy (whileBusy) is no longer document.activeElement.
document.addEventListener('focusin',e=>{if(!e.target.closest?.('dialog.ui-dialog'))dlgLastFocus=e.target},true);
document.addEventListener('pointerdown',e=>{const t=e.target.closest?.('button,input,select,textarea,a,[tabindex]');if(t&&!t.closest('dialog.ui-dialog'))dlgLastFocus=t},true);
function dlgNode(tag,cls,text){const e=document.createElement(tag);if(cls)e.className=cls;if(text!=null)e.textContent=String(text);return e}
function dlgRestoreFocus(t){
 if(!t||t===document.body||!t.isConnected||typeof t.focus!=='function')return;
 if(!t.disabled){t.focus({preventScroll:true});return}
 // Still disabled while its action runs: focus it once it is enabled again, unless the user has moved on.
 const mo=new MutationObserver(()=>{if(t.disabled)return;mo.disconnect();
  const a=document.activeElement;if(t.isConnected&&(!a||a===document.body))t.focus({preventScroll:true})});
 mo.observe(t,{attributes:true,attributeFilter:['disabled']});setTimeout(()=>mo.disconnect(),60000);
}
// Opens one dialog once the previous one has closed. o.collect() returns the result, or undefined to stay open.
function dlgShow(o){
 const open=()=>new Promise(resolve=>{
  const id='ui-dlg-'+(++dlgSeq),a=document.activeElement,trigger=a&&a!==document.body?a:dlgLastFocus;
  const d=dlgNode('dialog','ui-dialog'+(o.danger?' ui-dialog-danger':''));d.id=id;d.setAttribute('aria-labelledby',id+'-title');
  const form=dlgNode('form','ui-dialog-form');form.noValidate=true;
  const title=dlgNode('h2','ui-dialog-title',o.title);title.id=id+'-title';
  const body=dlgNode('div','ui-dialog-body'),described=[];
  if(o.message){const p=dlgNode('p','ui-dialog-message',o.message);p.id=id+'-msg';described.push(p.id);body.append(p)}
  if(o.detail){const pre=dlgNode('pre','ui-dialog-detail',o.detail);pre.id=id+'-detail';described.push(pre.id);body.append(pre)}
  if(described.length)d.setAttribute('aria-describedby',described.join(' '));
  if(o.content)body.append(o.content);
  const acts=dlgNode('div','ui-dialog-actions');
  const cancel=dlgNode('button','ghost ui-dialog-cancel',o.cancel||'Cancel');cancel.type='button';
  const ok=dlgNode('button',(o.danger?'danger ':'')+'ui-dialog-ok',o.ok||'OK');ok.type='submit';
  if(o.noCancel)acts.append(ok);else acts.append(cancel,ok);form.append(title,body,acts);d.append(form);document.body.append(d);
  let done=false;
  const finish=v=>{if(done)return;done=true;if(d.open)d.close();d.remove();dlgRestoreFocus(trigger);resolve(v)};
  cancel.addEventListener('click',()=>finish(null));
  d.addEventListener('cancel',e=>{e.preventDefault();finish(null)});  // Escape
  d.addEventListener('close',()=>finish(null));
  form.addEventListener('submit',e=>{e.preventDefault();const v=o.collect?o.collect():true;if(v!==undefined)finish(v)});
  // Enter confirms from a single-line field; Ctrl/Cmd+Enter from a textarea. On a button, Enter does what the button says.
  d.addEventListener('keydown',e=>{
   if(e.key!=='Enter'||e.isComposing)return;const tag=e.target.tagName;
   if(tag==='TEXTAREA'){if(e.ctrlKey||e.metaKey){e.preventDefault();form.requestSubmit(ok)}return}
   if(tag==='INPUT'){e.preventDefault();form.requestSubmit(ok)}});
  d.showModal();
  const first=o.focus&&o.focus();(first||(o.danger?cancel:ok)).focus();
 });
 const run=dlgChain.then(open,open);dlgChain=run.catch(()=>{});return run;
}
function uiConfirm(message,opts={}){
 if(DLG_NATIVE)return Promise.resolve(window.confirm(message+(opts.detail?'\n\n'+opts.detail:'')));
 return dlgShow({title:opts.title||'Please confirm',message,detail:opts.detail,ok:opts.ok,cancel:opts.cancel,danger:!!opts.danger}).then(v=>v===true);
}
function uiPrompt(title,opts={}){
 const field={name:'value',label:opts.label||'',type:opts.multiline?'textarea':'text',value:opts.value??'',placeholder:opts.placeholder,
  rows:opts.rows,hint:opts.hint,required:opts.required,maxLength:opts.maxLength,pattern:opts.pattern,patternMessage:opts.patternMessage};
 return uiForm(title,[field],{ok:opts.ok,intro:opts.message,danger:opts.danger,cancel:opts.cancel}).then(r=>r===null?null:r.value);
}
function dlgFieldError(f,input){
 const v=f.type==='textarea'?input.value:input.value.trim();
 if(f.type==='number'&&input.validity&&input.validity.badInput)return 'Enter a number.';
 if(!v.trim())return f.required?'This field is required.':'';
 if(f.type==='number'){
  const n=Number(v);if(!Number.isFinite(n))return 'Enter a number.';
  if(f.min!=null&&n<Number(f.min))return 'Use '+f.min+' or more.';
  if(f.max!=null&&n>Number(f.max))return 'Use '+f.max+' or less.';
  const step=f.step==null||f.step==='any'?0:Number(f.step);  // only an explicit step restricts values
  if(step>0){const q=(n-Number(f.min||0))/step;if(Math.abs(q-Math.round(q))>1e-9)return 'Use a multiple of '+step+'.'}
 }
 if(f.maxLength&&v.length>f.maxLength)return 'Use at most '+f.maxLength+' characters.';
 if(f.pattern&&!new RegExp(f.pattern).test(v))return f.patternMessage||'This value is not valid.';
 return '';
}
let dlgFieldSeq=0;
function uiForm(title,fields,opts={}){
 if(DLG_NATIVE){  // fallback: one native prompt per field
  const out={};
  for(const f of fields){
   const v=window.prompt((f.label||title)+(f.hint?' ('+f.hint+')':''),f.value??'');if(v===null)return Promise.resolve(null);
   out[f.name]=f.type==='number'?(v.trim()===''?null:Number(v)):v}
  return Promise.resolve(out);
 }
 const box=dlgNode('div','ui-dialog-fields'),inputs=[],pre='ui-f'+(++dlgFieldSeq)+'-';let tried=false;
 for(const f of fields){
  const wrap=dlgNode('div','ui-field'),id=pre+f.name;let input;
  if(f.type==='textarea'){input=dlgNode('textarea');input.rows=Math.max(6,f.rows||6)}
  else if(f.type==='select'){input=dlgNode('select');
   for(const o of f.options||[]){const [value,label]=Array.isArray(o)?o:typeof o==='object'?[o.value,o.label]:[o,o];input.add(new Option(String(label),String(value)))}}
  else{input=dlgNode('input');input.type=f.type==='number'?'number':'text';input.autocomplete='off';
   if(f.type==='number'){input.step=f.step!=null?String(f.step):'any';if(f.min!=null)input.min=String(f.min);if(f.max!=null)input.max=String(f.max);input.inputMode='decimal'}
   else input.spellcheck=false}
  input.id=id;input.name=f.name;input.className='ui-field-input';
  if(f.placeholder)input.placeholder=f.placeholder;
  if(f.maxLength&&f.type!=='select'&&f.type!=='number')input.maxLength=f.maxLength;
  if(f.required)input.setAttribute('aria-required','true');
  input.value=f.value==null?'':String(f.value);
  if(f.label){const l=dlgNode('label','ui-field-label',f.label);l.htmlFor=id;wrap.append(l)}else input.setAttribute('aria-label',title);
  wrap.append(input);const describedBy=[];
  if(f.hint){const h=dlgNode('p','ui-field-hint',f.hint);h.id=id+'-hint';describedBy.push(h.id);wrap.append(h)}
  const err=dlgNode('p','ui-field-error');err.id=id+'-error';err.hidden=true;describedBy.push(err.id);wrap.append(err);
  input.setAttribute('aria-describedby',describedBy.join(' '));
  const check=()=>{const m=dlgFieldError(f,input);err.textContent=m;err.hidden=!m;
   if(m)input.setAttribute('aria-invalid','true');else input.removeAttribute('aria-invalid');return !m};
  input.addEventListener('input',()=>{if(tried||!err.hidden)check()});
  input.addEventListener('blur',()=>{if(input.value!=='')check()});
  inputs.push({f,input,check});box.append(wrap);
 }
 return dlgShow({title,message:opts.intro,detail:opts.detail,content:box,ok:opts.ok||'Save',cancel:opts.cancel,danger:!!opts.danger,
  focus:()=>{const i=inputs[0]&&inputs[0].input;if(i&&i.tagName==='INPUT'&&i.type==='text')i.select();return i},
  collect:()=>{tried=true;const bad=inputs.filter(x=>!x.check());if(bad.length){bad[0].input.focus();return undefined}
   const out={};for(const {f,input} of inputs){const v=input.value;out[f.name]=f.type==='number'?(v.trim()===''?null:Number(v)):v}return out}});
}
// ── end HLA_DIALOGS_V1 ─────────────────────────────────────────────────────────────────────────────────────────
// ── HLA_UI_V4: shared pieces for the Phase-4 pages (see ui/DESIGN.md §8): a reusable drawer, a contextual action
// menu, inline icons and a metric strip. Function declarations, so any block can use them. ─────────────────────────
const V4_ICONS={x:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg>',
 down:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m7 10 5 5 5-5"/></svg>',
 dots:'<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="5" cy="12" r="1.4"/><circle cx="12" cy="12" r="1.4"/><circle cx="19" cy="12" r="1.4"/></svg>',
 plus:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>',
 ext:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/></svg>'};
const V4_DRAWERS={};
// drawer(id) → {root,title,meta,actions,body,open(),close(),isOpen,onclose}: right side on wide screens, bottom sheet on
// phones; Escape and the backdrop close it, focus returns to what opened it, the page behind is inert while it is open.
function drawer(id,wide){
 if(V4_DRAWERS[id])return V4_DRAWERS[id];
 const root=el('div','drawer v4-drawer'+(wide?' drawer-wide':''));root.id=id;root.hidden=true;
 const back=el('div','drawer-backdrop'),panel=el('div','drawer-panel');panel.setAttribute('role','dialog');panel.setAttribute('aria-modal','true');
 const head=el('div','drawer-head'),titles=el('div','drawer-title'),h=el('h2');h.id=id+'-title';panel.setAttribute('aria-labelledby',h.id);
 const meta=el('div','drawer-meta muted');titles.append(h,meta);
 const acts=el('div','drawer-actions'),extra=el('div','drawer-extra'),x=el('button','icon-btn drawer-close');x.type='button';x.setAttribute('aria-label','Close');x.innerHTML=V4_ICONS.x;
 acts.append(extra,x);head.append(titles,acts);const body=el('div','drawer-body');panel.append(head,body);root.append(back,panel);
 let last=null;
 const d={root,title:h,meta,actions:extra,body,onclose:null,get isOpen(){return !root.hidden},
  open(){if(!root.isConnected)$('workspace').append(root);
   if(root.hidden){last=document.activeElement;root.hidden=false;document.body.classList.add('drawer-open');
    for(const n of $('workspace').children)if(n!==root)n.inert=true;setTimeout(()=>{if(!root.contains(document.activeElement))x.focus()},0)}},
  close(){if(root.hidden)return;root.hidden=true;
   if(!document.querySelector('.drawer:not([hidden])'))document.body.classList.remove('drawer-open');
   for(const n of $('workspace').children)n.inert=false;if(d.onclose)d.onclose();if(last&&document.contains(last))last.focus()}};
 x.onclick=()=>d.close();back.onclick=()=>d.close();
 document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!root.hidden&&!document.querySelector('dialog[open]'))d.close()});
 return V4_DRAWERS[id]=d}
// facts(list) → <dl class="facts">: [label, value(text|Node|null)], empty values are skipped.
function factList(rows,cls){const dl=el('dl','facts kv'+(cls?' '+cls:''));
 for(const [k,v] of rows){if(v==null||v===''||(Array.isArray(v)&&!v.length))continue;const dd=el('dd');
  if(v instanceof Node)dd.append(v);else dd.textContent=Array.isArray(v)?v.join(', '):String(v);dl.append(el('dt',null,k),dd)}
 return dl}
// actionMenu(label, items): one button that opens a menu of actions (items: {label, run, danger, disabled, title} or null
// for a separator). Arrow keys move, Escape closes, a click outside closes.
function actionMenu(label,items,cls){
 const wrap=el('div','menu-wrap');const btn=el('button','secondary small menu-btn'+(cls?' '+cls:''));btn.type='button';
 btn.setAttribute('aria-haspopup','menu');btn.setAttribute('aria-expanded','false');btn.append(label);btn.insertAdjacentHTML('beforeend',V4_ICONS.down);
 let menu=null;
 const close=()=>{if(!menu)return;menu.remove();menu=null;btn.setAttribute('aria-expanded','false');document.removeEventListener('pointerdown',outside,true)};
 const outside=e=>{if(!wrap.contains(e.target))close()};
 btn.onclick=()=>{if(menu){close();return}
  menu=el('div','menu');menu.setAttribute('role','menu');
  for(const it of items){if(!it){menu.append(el('div','menu-sep'));continue}
   const b=el('button','menu-item'+(it.danger?' menu-danger':''),it.label);b.type='button';b.setAttribute('role','menuitem');b.disabled=!!it.disabled;if(it.title)b.title=it.title;
   b.onclick=async()=>{close();btn.disabled=true;try{await it.run()}catch(e){notice(e.message)}finally{if(btn.isConnected)btn.disabled=false}};menu.append(b)}
  menu.onkeydown=e=>{const all=[...menu.querySelectorAll('.menu-item:not(:disabled)')],i=all.indexOf(document.activeElement);
   if(e.key==='ArrowDown'){e.preventDefault();all[(i+1)%all.length]?.focus()}else if(e.key==='ArrowUp'){e.preventDefault();all[(i-1+all.length)%all.length]?.focus()}
   else if(e.key==='Escape'){e.preventDefault();e.stopPropagation();close();btn.focus()}else if(e.key==='Tab')close()};
  wrap.append(menu);btn.setAttribute('aria-expanded','true');document.addEventListener('pointerdown',outside,true);menu.querySelector('.menu-item:not(:disabled)')?.focus()};
 wrap.append(btn);return wrap}
// metric(label, value, foot, opts) → a tile of a .metric-strip (opts: {tone:'warn'|'bad'|'ok', bar:0..1, onclick, id}).
function metric(label,value,foot,o){o=o||{};const m=el(o.onclick?'button':'div','metric-tile'+(o.tone?' tone-'+o.tone:''));
 if(o.onclick){m.type='button';m.onclick=o.onclick}m.setAttribute('role','listitem');if(o.id)m.id=o.id;
 m.append(el('span','metric-label',label),el('strong','metric-value',value));
 if(o.bar!=null){const b=el('span','metric-bar'),i=el('i');i.style.width=Math.max(0,Math.min(100,o.bar*100))+'%';b.append(i);m.append(b)}
 if(foot)m.append(el('span','metric-foot',foot));return m}
// A tab strip (.tabs) whose buttons carry data-tab; shows the matching [data-pane] siblings inside `scope`.
function paneTabs(tabs,scope,onchange){
 const show=key=>{for(const b of tabs.querySelectorAll('button[data-tab]'))b.setAttribute('aria-selected',String(b.dataset.tab===key));
  for(const p of scope.querySelectorAll('[data-pane]'))p.hidden=p.dataset.pane!==key;if(onchange)onchange(key)};
 tabs.addEventListener('click',e=>{const b=e.target.closest('button[data-tab]');if(b)show(b.dataset.tab)});
 tabs.addEventListener('keydown',e=>{if(!['ArrowLeft','ArrowRight'].includes(e.key))return;const all=[...tabs.querySelectorAll('button[data-tab]')];
  const i=all.indexOf(document.activeElement);if(i<0)return;const n=all[(i+(e.key==='ArrowRight'?1:all.length-1))%all.length];n.focus();n.click()});
 return show}
// Errors read as sentences: validation lists, proxy error pages and a lost network become plain messages.
function errorText(status,data){
  const d=data&&data.detail;
  if(Array.isArray(d))return d.map(x=>{const f=(x.loc||[]).filter(p=>p!=='body'&&p!=='query').join(' ');
    return (f?f.replace(/_/g,' ')+': ':'')+String(x.msg||'invalid value').replace(/^Value error, /,'')}).join('; ');
  if(typeof d==='string'&&d)return d;
  if(d&&typeof d==='object')return d.message||d.error||JSON.stringify(d);
  if(status===502||status===503||status===504)return 'The server is not answering right now ('+status+'). Try again in a moment.';
  if(status===413)return 'That is too large to send.';
  return 'Request failed ('+status+').';
}
async function api(path, opts={}) {
  let r;
  try{r = await fetch('/ui/api/' + path, {credentials:'same-origin', ...opts})}
  catch(e){throw new Error(navigator.onLine===false?'You are offline. Check the connection and try again.':'Cannot reach the agent platform. Check the connection and try again.')}
  const data = await r.json().catch(()=>({}));
  // The session ended (expired, logged out elsewhere): back to sign-in instead of failing every request.
  if (r.status===401 && !/^(login|session)$/.test(path) && !$('workspace').hidden) sessionExpired();
  if (!r.ok) throw new Error(errorText(r.status,data));
  return data;
}
function sessionExpired(){showWorkspace(false);stopBackground();$('login-error').textContent='Your session ended. Sign in again to continue.';$('password').focus()}
// Notices are toasts: always in view, never shift the layout, and clear themselves.
let noticeTimer=null;
document.addEventListener('click',e=>{if(e.target.id==='notice'){e.target.textContent='';clearTimeout(noticeTimer)}});
function notice(text) {
  if(text&&$('workspace').hidden)return;  // signed out: the sign-in form says what happened
  const box=$('notice');clearTimeout(noticeTimer);box.textContent=text||'';
  if(text)noticeTimer=setTimeout(()=>{box.textContent=''},Math.min(12000,4000+String(text).length*60));
}
function fmt(v) { return Number(v || 0).toLocaleString(); }
function cost(v) { return '$' + Number(v || 0).toFixed(5); }
function showWorkspace(yes) { $('login').hidden = yes; $('workspace').hidden = !yes; }
async function refresh() {
  const stats=await api('summary');
  $('m-tasks').textContent=fmt(stats.tasks); $('m-completed').textContent=fmt(stats.completed);
  $('m-cost').textContent=cost(stats.openrouter_cost_usd);
  $('m-tokens').textContent=fmt(Number(stats.input_tokens)+Number(stats.output_tokens));
  $('u-input').textContent=fmt(stats.input_tokens); $('u-output').textContent=fmt(stats.output_tokens);
  $('u-tsinput').textContent=fmt(stats.typesafe_input_tokens); $('u-tsoutput').textContent=fmt(stats.typesafe_output_tokens);
  loadApprover();loadPolicy().catch(()=>{});loadAttachmentLimits();
  // Model and agent pickers need a session, so they load (again) after login and on Refresh.
  loadRoutingModels();loadAgents().catch(()=>{});
  if(!$('tasks').hidden)await loadTasks();
  if(!$('threads').hidden)await Promise.all([loadThreads(),currentThread?openThread(currentThread):null]);
  if(!$('knowledge').hidden)await Promise.all([loadKnowledge(),loadFacts()]);
  if(!$('queue').hidden)await loadQueue();
  if(!$('audit').hidden)await loadAudit();
  if(!$('projects').hidden)await loadProjects().then(()=>currentProject&&openProject(currentProject));
  await loadInbox().catch(()=>{});
}
const VIEW_TITLES={overview:'Overview',tasks:'Tasks',threads:'Conversations',projects:'Projects',knowledge:'Knowledge',queue:'Queue',agents:'Agents',policy:'Security',audit:'Activity',infrastructure:'Servers'};
function switchView(view) {store.set('view',view);for(const name of Object.keys(VIEW_TITLES)) $(name).hidden=name!==view;document.body.dataset.view=view;$('page-title').textContent=VIEW_TITLES[view];document.querySelectorAll('button[data-view]').forEach(b=>{const on=b.dataset.view===view;b.classList.toggle('active',on);if(on)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current')});}
$('login-form').addEventListener('submit',async e=>{e.preventDefault();$('login-error').textContent='';try{await api('login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:$('password').value})});$('password').value='';showWorkspace(true);$('page-title').tabIndex=-1;$('page-title').focus();await refresh();startBackground();if(!openFromLink(location.hash))restorePlace();initPush()}catch(err){$('login-error').textContent=err.message}});
$('task-form').addEventListener('submit',async e=>{
  e.preventDefault();if(busy)return;busy=true;$('submit-task').disabled=true;
  const preview=$('stream-preview'),status=$('stream-status');preview.hidden=false;renderTaskMarkdown(preview,'');status.textContent='Starting...';
  try {
    const attachment_ids=await taskTray.ids();
    const task=await api('tasks/start-stream',{method:'POST',headers:{'Content-Type':'application/json','x-agent-ui':'1'},body:JSON.stringify({task:$('task-text').value,max_output_tokens:Number($('max-tokens').value)||null,model_override:$('task-model-override').value||null,workspace:$('task-workspace').value,agent_id:$('task-agent').value||null,attachment_ids})});
    taskTray.clear(false);
    const es=new EventSource('/ui/api/tasks/'+encodeURIComponent(task.id)+'/events');
    es.addEventListener('content',evt=>{const message=JSON.parse(evt.data);scheduleStreamRender(preview,message.text||'');status.textContent='Generating...';});
    es.addEventListener('done',async evt=>{es.close();const data=JSON.parse(evt.data);flushStreamRender(preview);
      // Plain words for the outcome, and a way to the task (approve it, read it in full, continue it).
      status.textContent=data.status==='human_approval_required'?'Paused by the risk review: nothing has run yet. ':
        'Task '+(STATUS_LABELS[data.status]||data.status).toLowerCase()+(data.error?' — '+data.error:'')+'. ';
      const open=el('button','link-button',data.status==='human_approval_required'?'Review and approve':'Open task');open.type='button';
      open.onclick=()=>openTaskDetail(task.id);status.append(open);
      busy=false;$('submit-task').disabled=false;await refresh();});
    es.addEventListener('error',()=>{if(es.readyState===EventSource.CLOSED){status.textContent='Stream interrupted; task continues on server. Check Task history.';busy=false;$('submit-task').disabled=false;}});
    $('task-text').value='';
  }catch(err){status.textContent=err.message;busy=false;$('submit-task').disabled=false;}
});
const taskTray=fileTray($('task-attachments'));
$('task-attach').addEventListener('click',()=>$('task-file').click());
$('task-file').addEventListener('change',e=>{taskTray.add([...e.target.files]);e.target.value=''});
$('task-text').addEventListener('paste',e=>{const files=[...(e.clipboardData?.files||[])];if(!files.length)return;e.preventDefault();taskTray.add(files.map(pastedName))});
$('logout').addEventListener('click',async()=>{try{await api('logout',{method:'POST',headers:{'x-agent-ui':'1'}})}finally{showWorkspace(false);stopBackground();notice('')}});
// HLA_UI_V4: on phones the header's avatar opens a small sheet: who approves, the palette, Log out.
$('profile-mobile').addEventListener('click',()=>{
 const d=drawer('profile-sheet');d.title.textContent='Owner';d.meta.replaceChildren(el('span',null,approverLine()));d.actions.replaceChildren();
 const list=el('div','sheet-menu');
 const item=(label,fn,id,cls)=>{const b=el('button','menu-item'+(cls?' '+cls:''),label);b.type='button';if(id)b.id=id;b.onclick=()=>{d.close();fn()};list.append(b)};
 item('Command palette',()=>paletteOpen());
 item('Log out',()=>$('logout').click(),'logout-mobile','menu-danger');
 d.body.replaceChildren(el('p','fineprint',approverHint()),list);d.open()});
if('serviceWorker' in navigator)window.addEventListener('load',()=>navigator.serviceWorker.register('/ui/sw.js',{scope:'/ui/'}).catch(()=>{}));
// Links from notifications: /ui/#project=<id> opens that project (also when the app is already open).
// Also #thread=<id> (a conversation), #task=<id> (the task drawer), #inbox (Overview's "Needs you") and #websites.
function openFromLink(hash){const m=/^#(project|thread|task)=([0-9a-f-]{36})$/.exec(hash||'');
 if(hash==='#chief'||hash==='#/chief'){history.replaceState(null,'',location.pathname);openView('chief');return true}  // HLA_VOICE_V1: car mode
 if(hash==='#websites'){history.replaceState(null,'',location.pathname);openWebsite();return true}  // HLA_WEBSITES_V1
 if(!m&&hash!=='#inbox')return false;history.replaceState(null,'',location.pathname);
 if(!m){openView('overview');$('inbox-panel').scrollIntoView({block:'start'});return true}
 openItem(m[1],m[2]).catch(e=>notice(e.message));return true}
window.addEventListener('hashchange',()=>{if($('workspace').hidden===false)openFromLink(location.hash)});
if('serviceWorker' in navigator)navigator.serviceWorker.addEventListener('message',e=>{if(e.data&&e.data.type==='open'){const u=new URL(e.data.url);openFromLink(u.hash)}});
// Push notifications for this device (push.py): the bell switches them on or off.
const pushSupported=()=>'serviceWorker' in navigator&&'PushManager' in window&&'Notification' in window;
async function pushSubscription(){const reg=await navigator.serviceWorker.ready;return [reg,await reg.pushManager.getSubscription()]}
function showPush(on){const b=$('push-toggle');b.hidden=false;b.classList.toggle('on',on);b.title=on?'Notifications are on for this device':'Get a notification when a task finishes or needs you';
 b.querySelector('span').textContent=on?'On':'Notify';b.setAttribute('aria-pressed',on?'true':'false')}
async function initPush(){if(!pushSupported())return;try{const [,sub]=await pushSubscription();
 const st=await api('push?'+new URLSearchParams({endpoint:sub?sub.endpoint:''}));
 // The browser has a subscription the server lost (or the reverse): register it again quietly.
 if(sub&&!st.subscribed&&Notification.permission==='granted')await savePush(sub);
 showPush(!!sub&&Notification.permission==='granted')}catch(e){}}
async function savePush(sub){await api('push/subscribe',{method:'POST',headers:{'Content-Type':'application/json','x-agent-ui':'1'},
 body:JSON.stringify({...sub.toJSON(),label:(navigator.userAgentData?.platform||navigator.platform||'device').slice(0,60)})})}
function pushKey(b64){const s=atob(b64.replace(/-/g,'+').replace(/_/g,'/')+'='.repeat((4-b64.length%4)%4));return Uint8Array.from(s,c=>c.charCodeAt(0))}
$('push-toggle').addEventListener('click',async()=>{const b=$('push-toggle');b.disabled=true;try{
 const [reg,sub]=await pushSubscription();
 if(sub&&Notification.permission==='granted'){
  if(!await uiConfirm('Notifications are on for this device. Turn them off?',{title:'Turn off notifications',ok:'Turn off'}))return;
  await api('push/unsubscribe',{method:'POST',headers:{'Content-Type':'application/json','x-agent-ui':'1'},body:JSON.stringify({endpoint:sub.endpoint})});
  await sub.unsubscribe();showPush(false);notice('Notifications are off for this device.');return}
 const perm=await Notification.requestPermission();
 if(perm!=='granted')return notice('Notifications are blocked for this site. Allow them in the browser or phone settings, then try again.');
 const st=await api('push');
 const fresh=sub||await reg.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:pushKey(st.public_key)});
 await savePush(fresh);showPush(true);
 await api('push/test',{method:'POST',headers:{'Content-Type':'application/json','x-agent-ui':'1'},body:JSON.stringify({endpoint:fresh.endpoint})});
 notice('Notifications are on. A test notification is on its way.')}
 catch(e){notice('Notifications: '+e.message)}finally{b.disabled=false}});
// Conversations: the composer grows with what you type; the list can be hidden for a wider chat (remembered).
function growComposer(){const t=$('thread-input');t.style.height='auto';t.style.height=Math.min(t.scrollHeight+2,Math.round(window.innerHeight*0.4))+'px'}
$('thread-input').addEventListener('input',growComposer);
function setListCollapsed(on){document.querySelector('.chat-layout').classList.toggle('list-collapsed',on);const b=$('thread-list-toggle');
 b.setAttribute('aria-pressed',on?'true':'false');b.title=on?'Show the conversation list':'Hide the conversation list (more room for the chat)';
 withIcon(b,'sidebar','List');try{localStorage.setItem('chatListCollapsed',on?'1':'0')}catch(e){}}
$('thread-list-toggle').addEventListener('click',()=>setListCollapsed(!document.querySelector('.chat-layout').classList.contains('list-collapsed')));
try{if(localStorage.getItem('chatListCollapsed')==='1')setListCollapsed(true)}catch(e){}
$('refresh').addEventListener('click',()=>refresh().then(()=>notice('Refreshed')).catch(e=>notice(e.message)));
document.querySelectorAll('button[data-view]').forEach(b=>b.addEventListener('click',()=>switchView(b.dataset.view)));
api('session').then(async s=>{showWorkspace(s.authenticated);if(s.authenticated){await refresh();startBackground();if(!openFromLink(location.hash))restorePlace();initPush()}}).catch(e=>{showWorkspace(false);$('login-error').textContent=navigator.onLine===false||e instanceof TypeError?'Offline: the agent platform is not reachable. It reconnects when you are back on the network.':e.message});
window.addEventListener('online',()=>{if(!$('login').hidden&&$('login-error').textContent.startsWith('Offline'))location.reload()});

let editingKnowledgeId=null;
// Who wrote a note: you, an agent (knowledge_save) or the platform (a finished project task's handoff).
const ORIGIN_LABELS={human:['You','origin-human'],agent:['Agent','origin-agent'],auto:['From a task','origin-auto']};
function originPill(entry){const [label,cls]=ORIGIN_LABELS[entry.origin]||ORIGIN_LABELS.human;const p=el('span','pill origin-pill '+cls,label);
 p.title=entry.origin==='agent'?'Saved by '+(entry.author||'an agent'):entry.origin==='auto'?'Captured from a project task handoff':'Written by '+(entry.author||'you');return p}
function kbMeta(entry){return [entry.origin==='agent'&&entry.author?entry.author:null,entry.source,'updated '+when(entry.updated_at)].filter(Boolean).join(' · ')}
// Project choices for the Knowledge page (filter and "belongs to"), per workspace.
async function loadKbProjects(workspace){
 const data=await api('projects').catch(()=>({projects:[]}));const mine=data.projects.filter(p=>p.workspace===workspace);
 for(const [id,first] of [['kb-project',2],['kb-note-project',1]]){const sel=$(id),keep=sel.value;
  while(sel.options.length>first)sel.remove(first);for(const p of mine)sel.add(new Option(p.title,p.id));
  sel.value=[...sel.options].some(o=>o.value===keep)?keep:''}}
// Edit mode says so: what Save does, how to leave it, and which entry is being edited.
function markEditing(listId,id){document.querySelectorAll('#'+listId+' .editing').forEach(e=>e.classList.remove('editing'));
 if(id){const box=document.querySelector('#'+listId+' [data-id="'+id+'"]');if(box)box.classList.add('editing')}}
// HLA_UI_V4: the Knowledge library. Notes are compact rows (title, a one-line description, where they belong, last
// updated); a row opens the note in a drawer (full text, Edit, Delete). New note and Edit use the same form in that
// drawer. Recorded facts are the second tab.
let kbEntries=[],kbOpenId=null,kbSearchTimer=null;
const kbShowTab=paneTabs($('kb-tabs'),$('knowledge'),key=>{$('kb-new').hidden=key!=='notes'});
function kbParkForm(){const f=$('kb-form');if(f&&f.parentElement!==$('kb-form-home'))$('kb-form-home').append(f)}
function resetKnowledgeForm(){editingKnowledgeId=null;$('kb-form').reset();$('kb-form-title').textContent='Add knowledge';
 $('kb-submit').textContent='Save note';markEditing('kb-list',null);
 const v=$('kb-project').value;$('kb-note-project').value=v&&v!=='none'?v:''}
function kbDescription(body){
 const lines=String(body||'').split('\n'),plain=lines.filter(x=>!/^\s*#/.test(x)),pick=plain.length?plain:lines;
 const line=pick.map(x=>x.replace(/^[#>*\-\s`|]+/,'').replace(/[*_`]/g,'').trim()).find(x=>x.length>2)||'';
 return line.length>160?line.slice(0,157)+'…':line}
function kbRow(entry){
 const row=el('div','kb-entry row-item');row.dataset.id=entry.id;row.tabIndex=0;row.setAttribute('role','button');
 const title=el('div','row-title kb-title-row');title.append(el('strong',null,entry.title),originPill(entry));
 if(entry.project_title){const pj=el('button','link-pill','◈ '+entry.project_title);pj.type='button';pj.title='Open the project';
  pj.onclick=e=>{e.stopPropagation();openItem('project',entry.project_id)};title.append(pj)}
 const side=el('div','row-side');side.append(el('span',null,entry.project_title?'Project':'Workspace'),el('time',null,when(entry.updated_at)));
 row.append(title,side,el('div','row-sub',kbDescription(entry.body)));
 row.onclick=()=>kbOpenNote(entry.id);row.onkeydown=e=>{if(e.target===row&&(e.key==='Enter'||e.key===' ')){e.preventDefault();kbOpenNote(entry.id)}};
 return row}
async function loadKnowledge(){
  const workspace=$('kb-workspace').value;
  await loadKbProjects(workspace);
  const q=$('kb-search').value.trim();
  const data=await api('knowledge?'+new URLSearchParams({workspace,q,project:$('kb-project').value}));
  kbEntries=data.entries;
  const list=$('kb-list');list.replaceChildren();
  $('kb-count').textContent=data.entries.length?String(data.entries.length):'';
  if(!data.entries.length){const e=el('div','empty-state');
   if(q)e.append(el('strong',null,'No notes match “'+q+'”.'),'Try other words, or another workspace or project.');
   else e.append(el('strong',null,'No notes yet.'),'Notes you or the agents save appear here. Start with New note.');
   list.append(e)}
  for(const entry of data.entries)list.append(kbRow(entry));
  if(kbOpenId&&V4_DRAWERS['kb-drawer']?.isOpen&&!editingKnowledgeId){const e=kbEntries.find(x=>x.id===kbOpenId);if(e)kbOpenNote(e.id)}
}
function kbDrawer(){const d=drawer('kb-drawer',true);d.onclose=()=>{kbOpenId=null;kbParkForm();resetKnowledgeForm()};return d}
function kbOpenNote(id){
 const entry=kbEntries.find(x=>x.id===id);if(!entry)return;
 kbOpenId=id;editingKnowledgeId=null;kbParkForm();
 const d=kbDrawer();d.title.textContent=entry.title;
 d.meta.replaceChildren(originPill(entry),el('span',null,kbMeta(entry)));
 d.actions.replaceChildren();
 const edit=el('button','secondary small','Edit');edit.type='button';edit.onclick=()=>kbEditNote(entry);
 d.actions.append(edit,actionMenu('More',[
  ...(entry.project_id?[{label:'Open project',run:async()=>{d.close();openItem('project',entry.project_id)}}]:[]),
  {label:'Delete note…',danger:true,run:async()=>{
   if(!await uiConfirm('Delete “'+entry.title+'”? Agents will no longer see it.',{title:'Delete knowledge entry',ok:'Delete entry',danger:true}))return;
   await api('knowledge/'+entry.id,{method:'DELETE',headers:{'x-agent-ui':'1'}});d.close();await loadKnowledge();notice('Knowledge deleted')}}]));
 const body=el('div','md-view rendered-result kb-note-body');renderTaskMarkdown(body,entry.body);
 const facts=factList([['Belongs to',entry.project_title||'The whole workspace'],['Source',entry.source],['Updated',entry.updated_at?new Date(entry.updated_at).toLocaleString():null]]);
 d.body.replaceChildren(body,el('h3','drawer-section','About this note'),facts);d.open();
}
function kbEditNote(entry){
 const d=kbDrawer();
 if(entry){kbOpenId=entry.id;editingKnowledgeId=entry.id;$('kb-title').value=entry.title;$('kb-body').value=entry.body;$('kb-source').value=entry.source||'';
  $('kb-note-project').value=entry.project_id||'';$('kb-form-title').textContent='Edit “'+entry.title+'”';$('kb-submit').textContent='Update note';markEditing('kb-list',entry.id)}
 else{resetKnowledgeForm();kbOpenId=null}
 d.title.textContent=entry?'Edit note':'New note';d.meta.replaceChildren(el('span',null,entry?entry.title:'Workspace '+($('kb-workspace').value==='work'?'Work':'Home')));
 d.actions.replaceChildren();d.body.replaceChildren($('kb-form'));d.open();setTimeout(()=>$('kb-title').focus(),30);
}
$('kb-new').addEventListener('click',()=>kbEditNote(null));
$('kb-form').addEventListener('submit',e=>{e.preventDefault();const payload={workspace:$('kb-workspace').value,title:$('kb-title').value,body:$('kb-body').value,source:$('kb-source').value||null,project_id:$('kb-note-project').value||null};
 const editing=editingKnowledgeId;whileBusy($('kb-submit'),async()=>{try{const saved=await api('knowledge'+(editing?'/'+editing:''),{method:editing?'PUT':'POST',headers:{'Content-Type':'application/json','x-agent-ui':'1'},body:JSON.stringify(payload)});
  editingKnowledgeId=null;await loadKnowledge();notice(editing?'Note updated.':'Note “'+payload.title+'” saved.');
  const id=editing||(saved&&saved.id);if(id&&kbEntries.some(x=>x.id===id))kbOpenNote(id);else V4_DRAWERS['kb-drawer']?.close()}catch(err){notice(err.message)}})});
$('kb-cancel').addEventListener('click',()=>{const id=editingKnowledgeId;editingKnowledgeId=null;if(id&&kbEntries.some(x=>x.id===id))kbOpenNote(id);else V4_DRAWERS['kb-drawer']?.close()});
$('kb-project').addEventListener('change',()=>{const v=$('kb-project').value;if(!editingKnowledgeId)$('kb-note-project').value=v==='none'?'':v;loadKnowledge().catch(e=>notice(e.message))});
$('kb-refresh').addEventListener('click',()=>loadKnowledge().catch(e=>notice(e.message)));
$('kb-search').addEventListener('input',()=>{clearTimeout(kbSearchTimer);kbSearchTimer=setTimeout(()=>loadKnowledge().catch(e=>notice(e.message)),300)});
$('kb-workspace').addEventListener('change',()=>{resetKnowledgeForm();loadKnowledge().catch(e=>notice(e.message))});
document.querySelector('button[data-view="knowledge"]').addEventListener('click',()=>{loadKnowledge().catch(e=>notice(e.message));loadFacts().catch(e=>notice(e.message))});

// HLA_FACTS_V1: recorded facts with provenance, freshness and the previous value.
const FACT_STATE_LABELS={fresh:'Fresh',stale:'Stale',unverified:'Unverified'};
const FACT_SOURCES={'ssh-gateway:diagnostic':'Diagnostic','ssh-gateway:maintenance':'Maintenance','ssh-gateway:command':'Shell command','proxmox-api':'Proxmox API'};
let factOffset=0,factTimer=null;
function factEl(f){
 const box=document.createElement('article');box.className='kb-entry fact fact-'+f.freshness;
 const head=document.createElement('div');head.className='kb-entry-head';
 const title=document.createElement('div');title.className='fact-title';
 const pill=document.createElement('span');pill.className='pill fact-pill fact-pill-'+f.freshness;pill.textContent=f.kind==='change'?(f.freshness==='unverified'?'Not verified':'Change'):FACT_STATE_LABELS[f.freshness];
 const key=document.createElement('code');key.textContent=f.key;title.append(pill,key);
 const del=document.createElement('button');del.className='ghost small fact-del';del.textContent='Delete';
 del.onclick=async()=>{if(!await uiConfirm('Delete this fact? Agents will no longer see it.',{title:'Delete fact',detail:f.key,ok:'Delete fact',danger:true}))return;
  try{await api('facts/'+f.id,{method:'DELETE',headers:{'x-agent-ui':'1'}});box.remove();notice('Fact deleted');refreshFactCounts()}catch(e){notice(e.message)}};
 head.append(title,del);
 const body=document.createElement('p');body.textContent=f.fact;
 const meta=document.createElement('small');
 const parts=[FACT_SOURCES[f.source]||f.source];
 if(f.verified_at)parts.push('verified '+when(f.verified_at));
 // Changes are history (kept for a year); observations are trusted until they expire.
 if(f.expires_at&&f.kind==='change')parts.push('kept until '+new Date(f.expires_at).toLocaleDateString());
 else if(f.expires_at&&f.freshness!=='unverified')parts.push((f.freshness==='stale'?'expired ':'fresh until ')+new Date(f.expires_at).toLocaleString());
 if(f.agent_name)parts.push('by '+f.agent_name);
 if(f.rechecked_at)parts.push('re-checked '+when(f.rechecked_at)+': '+(RECHECK_LABELS[f.recheck_outcome]||f.recheck_outcome||'—'));
 meta.textContent=parts.join(' · ');
 box.append(head,body,meta);
 if(f.task_id){const link=document.createElement('button');link.type='button';link.className='link-button';link.textContent='Open task';
  link.onclick=()=>openTaskDetail(f.task_id).catch(e=>notice(e.message));meta.append(' · ',link)}
 if(f.previous_fact){const prev=document.createElement('details');prev.className='fact-previous';
  const sum=document.createElement('summary');sum.textContent='Changed · previous value'+(f.previous_verified_at?' (verified '+when(f.previous_verified_at)+')':'');
  const old=document.createElement('p');old.textContent=f.previous_fact;prev.append(sum,old);box.append(prev)}
 return box;
}
function showFactCounts(c){
 const total=Object.values(c||{}).reduce((a,n)=>a+(n||0),0);if($('fact-count'))$('fact-count').textContent=total?String(total):'';
 const sel=$('fact-state');for(const o of sel.options)if(o.value)o.textContent=FACT_STATE_LABELS[o.value]+' ('+((c||{})[o.value]||0)+')';
}
async function refreshFactCounts(){
 const data=await api('facts?'+new URLSearchParams({workspace:$('kb-workspace').value,limit:'1'}));showFactCounts(data.counts);
}
async function loadFacts(more){
 if(!more)factOffset=0;
 const params=new URLSearchParams({workspace:$('kb-workspace').value,q:$('fact-search').value,state:$('fact-state').value,limit:'25',offset:String(factOffset)});
 const data=await api('facts?'+params);
 const list=$('fact-list');if(!more)list.replaceChildren();
 const c=data.counts||{};showFactCounts(c);
 if(!data.facts.length&&!more){const p=document.createElement('p');p.className='muted';
  p.className='empty-state';p.textContent=$('fact-search').value||$('fact-state').value?'No facts match.':'No facts yet. They are recorded when agents run diagnostics or make verified changes.';list.append(p)}
 for(const f of data.facts)list.append(factEl(f));
 factOffset+=data.facts.length;$('fact-pager').hidden=!data.has_more;
}
$('fact-search').addEventListener('input',()=>{clearTimeout(factTimer);factTimer=setTimeout(()=>loadFacts().catch(e=>notice(e.message)),300)});
$('fact-state').addEventListener('change',()=>loadFacts().catch(e=>notice(e.message)));
$('fact-more').addEventListener('click',()=>loadFacts(true).catch(e=>notice(e.message)));
$('kb-workspace').addEventListener('change',()=>loadFacts().catch(e=>notice(e.message)));

let editingAgentId=null;
let availableAgents=[];
async function loadAgents(){
  const [data,models]=await Promise.all([api('agents'),api('models')]);availableAgents=data.agents;
  const modelSelect=$('agent-model'),previousModel=modelSelect.value;modelSelect.replaceChildren();
  for(const model of models.models)modelSelect.add(new Option(modelOptionText(models,model),model));
  if(models.models.includes(previousModel))modelSelect.value=previousModel;
  const list=$('agent-list');list.replaceChildren();
  const active=availableAgents.filter(a=>a.enabled).length;
  $('agent-summary').textContent=`${availableAgents.length} agents configured · ${active} enabled · ${availableAgents.reduce((n,a)=>n+Number(a.task_count||0),0)} tasks processed`;
  // HLA_UI_V4: one row per agent (name, state, role, model, budget); the row opens the agent in a drawer to edit it.
  for(const agent of availableAgents){
    const row=el('div','agent-card row-item');row.dataset.id=agent.id;row.tabIndex=0;row.setAttribute('role','button');
    const title=el('div','row-title');title.append(el('strong',null,agent.name),el('span','pill '+(agent.enabled?'st-completed':'st-unknown')+' agent-state',(agent.enabled?'Enabled':'Disabled')+' · '+agent.workspace));
    const side=el('div','row-side');side.append(el('span',null,agent.task_count+' tasks'),el('span',null,'$'+Number(agent.total_cost_usd).toFixed(4)+' spent'));
    row.append(title,side,el('div','row-sub',(agent.role?agent.role+' · ':'')+agent.model+' · $'+Number(agent.daily_budget_usd).toFixed(2)+'/day limit'));
    row.onclick=()=>agentEdit(agent);row.onkeydown=e=>{if(e.target===row&&(e.key==='Enter'||e.key===' ')){e.preventDefault();agentEdit(agent)}};
    list.append(row);
  }
  if(!availableAgents.length){const e=el('div','empty-state');e.append(el('strong',null,'No agents yet.'),'Create one with New agent: a focused role and short instructions keep runs cheap.');list.append(e)}
  const select=$('task-agent'),old=select.value;select.replaceChildren();
  select.add(new Option('General worker',''));
  for(const a of availableAgents.filter(x=>x.enabled&&x.workspace===$('task-workspace').value))select.add(new Option(a.name,a.id));
  if([...select.options].some(o=>o.value===old))select.value=old;
}
function clearAgentForm(){editingAgentId=null;$('agent-form').reset();$('agent-form-title').textContent='Create agent';
 $('agent-submit').textContent='Save agent';$('agent-clear').textContent='Cancel';markEditing('agent-list',null)}
// The agent form lives in a drawer: New agent opens it empty, a row opens it filled in (Delete is in its menu).
function agentDrawer(){const d=drawer('agent-drawer',true);d.onclose=()=>{$('agent-form-home').append($('agent-form'));clearAgentForm()};return d}
function agentEdit(agent){
 const d=agentDrawer();clearAgentForm();d.actions.replaceChildren();
 if(agent){editingAgentId=agent.id;$('agent-form-title').textContent='Edit “'+agent.name+'”';$('agent-submit').textContent='Update agent';markEditing('agent-list',agent.id);
  $('agent-name').value=agent.name;$('agent-role').value=agent.role;$('agent-instructions').value=agent.instructions;
  $('agent-workspace').value=agent.workspace;$('agent-model').value=agent.model;$('agent-budget').value=agent.daily_budget_usd;$('agent-enabled').checked=agent.enabled;
  d.actions.append(actionMenu('More',[{label:'Delete agent…',danger:true,run:async()=>{
   if(!await uiConfirm('Delete '+agent.name+'? Existing task records are preserved but will lose their agent link.',{title:'Delete agent',ok:'Delete agent',danger:true}))return;
   await api('agents/'+agent.id,{method:'DELETE',headers:{'x-agent-ui':'1'}});d.close();await loadAgents();notice('Agent deleted')}}]))}
 d.title.textContent=agent?agent.name:'New agent';
 d.meta.replaceChildren(el('span',null,agent?(agent.task_count+' tasks · $'+Number(agent.total_cost_usd).toFixed(4)+' spent'):'A focused role and short instructions keep runs cheap.'));
 d.body.replaceChildren($('agent-form'));d.open();setTimeout(()=>$('agent-name').focus(),30)}
$('agent-new').addEventListener('click',()=>agentEdit(null));
$('agent-form').addEventListener('submit',async e=>{
  e.preventDefault();const payload={name:$('agent-name').value,role:$('agent-role').value,instructions:$('agent-instructions').value,workspace:$('agent-workspace').value,model:$('agent-model').value,enabled:$('agent-enabled').checked,daily_budget_usd:Number($('agent-budget').value)};
  const editing=editingAgentId;
  whileBusy($('agent-submit'),async()=>{try{await api('agents'+(editing?'/'+editing:''),{method:editing?'PUT':'POST',headers:{'Content-Type':'application/json','x-agent-ui':'1'},body:JSON.stringify(payload)});clearAgentForm();V4_DRAWERS['agent-drawer']?.close();await loadAgents();notice('Agent “'+payload.name+'” '+(editing?'updated.':'created.'))}catch(e){notice(e.message)}});
});
$('agent-clear').addEventListener('click',()=>{if(V4_DRAWERS['agent-drawer']?.isOpen)V4_DRAWERS['agent-drawer'].close();else clearAgentForm()});
$('agents-refresh').addEventListener('click',()=>loadAgents().catch(e=>notice(e.message)));
$('task-workspace').addEventListener('change',()=>loadAgents().catch(e=>notice(e.message)));
document.querySelector('button[data-view="agents"]').addEventListener('click',()=>loadAgents().catch(e=>notice(e.message)));
api('session').then(s=>{if(s.authenticated)loadAgents().catch(e=>notice(e.message))}).catch(()=>{});

async function loadQueue(){
  const d=await api('queue?limit=75');const list=$('queue-jobs');list.replaceChildren();
  if(!d.jobs.length){const p=document.createElement('p');p.className='muted';p.textContent='No queued jobs yet.';list.append(p)}
  for(const job of d.jobs){
    const e=document.createElement('article');e.className='kb-entry';
    // A readable status and when it was queued (or finished), instead of the raw status value.
    const title=el('div','queue-head');
    title.append(queueStatusPill(job.status),el('strong',null,new Date(job.created_at).toLocaleString()),
     el('span','muted',job.finished_at?(job.status==='cancelled'?'cancelled ':'finished ')+when(job.finished_at):job.started_at?'started '+when(job.started_at):''));
    if(job.api_task_id){const open=el('button','link-button','Open task');open.type='button';open.onclick=()=>openTaskDetail(job.api_task_id);title.append(open)}
    const description=document.createElement('p');description.textContent=job.task_text;
    const detail=document.createElement('div');detail.className='rendered-result';renderSimpleMarkdown(detail,job.result_text||job.error_message||'');
    e.append(title,description,detail);
    if(job.status==='queued'){
      const cancel=document.createElement('button');cancel.className='secondary';cancel.textContent='Cancel';
      cancel.onclick=async()=>{try{await api('queue/'+job.id+'/cancel',{method:'POST',headers:{'x-agent-ui':'1'}});notice('Queued task cancelled.');await loadQueue()}catch(err){notice(err.message)}};
      e.append(cancel);
    }
    list.append(e);
  }
}
async function updateQueueAgents(){
  const d=await api('agents?workspace='+encodeURIComponent($('queue-workspace').value));
  const select=$('queue-agent');const previous=select.value;select.replaceChildren();
  select.add(new Option('General worker',''));
  for(const a of d.agents.filter(x=>x.enabled))select.add(new Option(a.name,a.id));
  if([...select.options].some(o=>o.value===previous))select.value=previous;
}
$('queue-form').addEventListener('submit',async e=>{
  e.preventDefault();const btn=e.submitter;if(btn)btn.disabled=true;
  try{const payload={task:$('queue-text').value,workspace:$('queue-workspace').value,agent_id:$('queue-agent').value||null,max_output_tokens:Number($('queue-tokens').value),reasoning_effort:$('queue-effort').value};
  await api('queue',{method:'POST',headers:{'Content-Type':'application/json','x-agent-ui':'1'},body:JSON.stringify(payload)});
  $('queue-text').value='';notice('Task queued');await loadQueue();
  }catch(err){notice(err.message)}finally{if(btn)btn.disabled=false}
});
$('queue-refresh').addEventListener('click',()=>loadQueue().catch(e=>notice(e.message)));
$('queue-workspace').addEventListener('change',()=>updateQueueAgents().catch(e=>notice(e.message)));
document.querySelector('button[data-view="queue"]').addEventListener('click',()=>Promise.all([loadQueue(),updateQueueAgents()]).catch(e=>notice(e.message)));

// Render a small, safe Markdown subset without innerHTML. All text is escaped by textContent.
function renderSimpleMarkdown(target, source) {
  target.replaceChildren();
  const maxLength=25000;
  const lines=String(source||'').slice(0,maxLength).split('\n');
  for(const line of lines){
    const p=document.createElement('p');
    let raw=line.trim(), bullet=false;
    if(/^[-*]\s+/.test(raw)){raw=raw.replace(/^[-*]\s+/,'');bullet=true;}
    if(bullet) p.className='md-bullet';
    const parts=raw.split(/(\*\*[^*]+\*\*)/g);
    for(const part of parts){if(part.startsWith('**')&&part.endsWith('**')){const b=document.createElement('strong');b.textContent=part.slice(2,-2);p.append(b);}else p.append(document.createTextNode(part));}
    if(!raw) p.className='md-spacer';
    target.append(p);
  }
}
// HLA_PROXMOX_V1: the Infrastructure view. Both Proxmox nodes from the SSH gateway (/ui/api/proxmox): VMs with status,
// resources, IP and the managed tag; power, snapshots, create (spec preview first), onboard and destroy of VMs created here;
// the read-only audit token; the agents' level per node and per created server. Every change needs an approver.
let pxData=null,pxLoading=false,pxJobTimer=null;
const PX_VM_STATUS={running:['Running','completed'],stopped:['Stopped','rejected'],paused:['Paused','incomplete']};
const PX_LEVELS=[[0,'0 · off'],[1,'1 · read'],[4,'4 · power and snapshots'],[5,'5 · create with approval'],[6,'6 · rollback, destroy (approved)']];
const PX_SERVER_LEVELS=[[0,'0 · off'],[1,'1 · diagnostics'],[2,'2 · packages'],[3,'3 · restarts'],[4,'4 · deploys'],[5,'5 · approved commands'],[6,'6 · free shell']];
const PX_CAPS=[['inventory','Read'],['power','Power'],['snapshot','Snapshots'],['rollback','Rollback'],['create_vm','Create VM'],['download_image','Download image'],['destroy_vm','Destroy'],['audit_token','Audit token']];
function pxBytes(n){if(n==null)return '—';const g=n/1073741824;return g>=100?Math.round(g)+' GiB':g>=1?g.toFixed(1)+' GiB':Math.round(n/1048576)+' MiB'}
function pxPct(x){return x==null?'—':Math.round(x*100)+'%'}
function pxBusy(btn,fn){return whileBusy(btn,async()=>{try{await fn()}catch(e){notice(e.message)}})}
async function pxAction(node,body,doneText){
 const out=await api('proxmox/action',{method:'POST',headers:UI_HEADERS,body:JSON.stringify({node,...body})});
 if(out.status==='needs_owner'){
  await uiConfirm(out.detail||'The node token cannot do this.',{title:'Run this on the node',detail:(out.commands||[]).join('\n'),ok:'Done',cancel:'Close'});
  return out}
 notice(doneText||'Done.');await loadInfrastructure(true);return out}
async function loadInfrastructure(refresh){
 if(pxLoading)return;pxLoading=true;
 loadOmada(refresh);  // HLA_OMADA_V1: the controller panel loads alongside the nodes
 pxWebsitesSummary();
 const msg=$('infra-message');if(msg&&!pxData)msg.textContent='Reading both Proxmox nodes…';
 try{pxData=await api('proxmox'+(refresh===true?'?refresh=1':''));renderInfrastructure()}
 catch(err){$('px-vms').replaceChildren(el('p','error',err.message))}
 finally{pxLoading=false}
}
$('infra-refresh').addEventListener('click',()=>pxBusy($('infra-refresh'),()=>loadInfrastructure(true)));
document.querySelector('button[data-view="infrastructure"]').addEventListener('click',()=>loadInfrastructure());
$('refresh').addEventListener('click',()=>{if(!$('infrastructure').hidden)loadInfrastructure(true)});
function pxLevelSelect(target,current,levels,label){
 const wrap=el('label','px-level');wrap.append(el('span',null,label));
 const sel=el('select');sel.setAttribute('aria-label',label+' '+target);
 const opts=levels.some(([v])=>v===current)?levels:[...levels,[current,String(current)]];
 for(const [v,t] of opts)sel.add(new Option(t,String(v)));
 sel.value=String(current);sel.disabled=!pxData.editable;
 sel.onchange=async()=>{const value=Number(sel.value);sel.disabled=true;
  try{await api('proxmox/level',{method:'PUT',headers:UI_HEADERS,body:JSON.stringify({target,level:value})});notice(target+' is now at level '+value+'.');await loadInfrastructure()}
  catch(e){notice(e.message);sel.value=String(current)}finally{sel.disabled=!pxData.editable}};
 wrap.append(sel);return wrap}
// HLA_OMADA_V1: the Omada controller panel (/ui/api/omada): connection, controller version, omadacId, certificate
// fingerprint with a Trust button, sites, the agents' level and the last error. Changes need an approver.
let omData=null;
const OM_LEVELS=[[0,'0 · off'],[1,'1 · read'],[5,'5 · change with approval'],[6,'6 · change without approval']];
async function loadOmada(refresh){
 const box=$('om-body');if(!box)return;
 try{omData=await api('omada'+(refresh===true?'?refresh=1':''));renderOmada()}
 catch(err){box.replaceChildren(el('p','error',err.message))}
}
function omLevelSelect(current){
 const wrap=el('label','px-level');wrap.append(el('span',null,'Agents'));
 const sel=el('select');sel.id='om-level';sel.setAttribute('aria-label','Agents omada:controller');
 const opts=OM_LEVELS.some(([v])=>v===current)?OM_LEVELS:[...OM_LEVELS,[current,String(current)]];
 for(const [v,t] of opts)sel.add(new Option(t,String(v)));
 sel.value=String(current);sel.disabled=!omData.editable;
 sel.onchange=async()=>{const value=Number(sel.value);sel.disabled=true;
  try{await api('omada/level',{method:'PUT',headers:UI_HEADERS,body:JSON.stringify({level:value})});notice('omada:controller is now at level '+value+'.');await loadOmada()}
  catch(e){notice(e.message);sel.value=String(current)}finally{sel.disabled=!omData.editable}};
 wrap.append(sel);return wrap}
function renderOmada(){
 const d=omData,box=$('om-body');box.replaceChildren();
 if(pxData)setTimeout(renderInfraAlerts,0);  // HLA_UI_V4: the controller's state is one of the page's warnings
 if(!d.available){box.append(el('p','policy-note',d.note));return}
 const cert=d.certificate||{},certIssue=['certificate_not_trusted','certificate_changed'].includes(d.error);
 const state=d.connected?['Connected','completed']:certIssue?['Certificate to confirm','human_approval_required']:['Not connected','failed'];
 const head=el('div','px-node-head'),title=el('div','px-node-title');
 title.append(el('strong',null,'Controller'),el('span','pill st-'+state[1],state[0]));
 if(d.controller&&d.controller.controllerVer)title.append(el('span','badge badge-run','v'+d.controller.controllerVer));
 head.append(title,omLevelSelect(d.level||0));box.append(head);
 const facts=el('div','px-facts');
 facts.append(el('span',null,d.base_url||'—'));
 if(d.controller)facts.append(el('span',null,'omadacId '+d.controller.omadacId));
 if(d.app)facts.append(el('span',null,'App '+d.app));
 if(d.sites!=null)facts.append(el('span',null,d.sites+' site'+(d.sites===1?'':'s')+(d.site_names&&d.site_names.length?': '+d.site_names.join(', '):'')));
 box.append(facts);
 const c=el('div','om-cert');
 if(cert.mode==='ca')c.append(el('p','fineprint','Certificate checked against omada-ca.pem.'));
 else{
  if(cert.trusted){const p=el('p','fineprint','Trusted certificate (SHA-256) ');p.append(el('span','om-fp',cert.trusted));
   if(cert.trusted_by)p.append(' · by '+cert.trusted_by);c.append(p)}
  if((cert.state==='pending'||cert.state==='changed')&&cert.seen){
   const p=el('p',cert.state==='changed'?'error':'fineprint',cert.state==='changed'?'The controller now presents a different certificate: ':'The controller presents this certificate (SHA-256): ');
   p.append(el('span','om-fp',cert.seen));
   const btn=el('button','small','Trust this certificate…');btn.type='button';btn.id='om-trust';btn.disabled=!d.editable;
   btn.onclick=()=>pxBusy(btn,async()=>{
    if(!await uiConfirm('Compare this fingerprint with the certificate the controller shows in your browser (open the controller → certificate → SHA-256) before you trust it. Until then the gateway sends no credentials.',
     {title:'Trust Omada certificate',detail:cert.seen,ok:'Trust certificate'}))return;
    await api('omada/trust',{method:'POST',headers:UI_HEADERS,body:JSON.stringify({fingerprint:cert.seen})});
    notice('Certificate trusted.');await loadOmada(true)});
   c.append(p,btn)}
 }
 if(c.childNodes.length)box.append(c);
 if(d.error&&!(certIssue&&cert.seen))box.append(el('p','error px-error',d.detail||d.error));
 if(!d.editable)box.append(el('p','fineprint',(d.approver&&d.approver.reason)||'Only approvers can change this.'));
}
// HLA_UI_V4: Servers page = overview strip + warnings + tabs (Virtual machines | Nodes | Network | Created servers).
// A VM row opens a drawer with its facts, snapshots, agent access and its power actions in one menu; a node row opens
// a drawer with the token's rights, the agents' level, Create VM and the read-only token. Every action keeps its
// confirmation and the gateway's approval/permission checks (pxAction).
let pxQuery='',pxOpen=null,pxWsSummary=null;
const pxShowTab=paneTabs($('infra-tabs'),$('infrastructure'),key=>{$('px-search').hidden=key!=='vms'});
$('px-search').addEventListener('input',()=>{pxQuery=$('px-search').value.trim().toLowerCase();if(pxData)renderPxVms()});
function pxNodeOf(name){return (pxData?.nodes||[]).find(n=>n.node===name)}
function pxServerOf(n,vm){return (pxData.servers||[]).find(s=>s.node===n.node&&Number(s.vmid)===Number(vm.vmid))}
function renderInfrastructure(){
 const d=pxData,note=$('px-note');note.hidden=true;
 renderInfraOverview();
 if(!d.available){note.textContent=d.note;note.hidden=false;$('px-vms').replaceChildren(el('div','empty-state',d.note));$('px-nodes').replaceChildren();$('px-servers').replaceChildren();return}
 if(!d.editable){note.textContent=(d.approver&&d.approver.reason)||'Only approvers can change VMs here.';note.hidden=false}
 renderPxJobs(d.jobs||[]);renderPxVms();renderPxNodes();renderPxServers();
 // An open drawer follows the new data (e.g. after Start, the state and the menu change).
 if(pxOpen&&V4_DRAWERS['px-drawer']?.isOpen){const n=pxNodeOf(pxOpen.node);
  if(!n)V4_DRAWERS['px-drawer'].close();
  else if(pxOpen.vmid==null)pxNodeDrawer(n);
  else{const vm=[...(n.vms||[]),...(n.containers||[]).map(c=>({...c,ct:true}))].find(v=>v.vmid===pxOpen.vmid);if(vm)pxVmDrawer(n,vm);else V4_DRAWERS['px-drawer'].close()}}
}
async function pxWebsitesSummary(){try{pxWsSummary=await api('websites/summary')}catch(e){pxWsSummary=null}if(pxData)renderInfraOverview()}
function renderInfraOverview(){
 const box=$('infra-overview'),d=pxData;if(!d)return;box.replaceChildren();
 const nodes=d.nodes||[],ok=nodes.filter(n=>n.ok),vms=nodes.flatMap(n=>(n.vms||[]).filter(v=>!v.template));
 const running=vms.filter(v=>v.status==='running').length;
 const cpu=ok.filter(n=>n.status&&n.status.cpu!=null),mem=ok.filter(n=>n.status&&n.status.maxmem);
 const cpuAvg=cpu.length?cpu.reduce((a,n)=>a+n.status.cpu,0)/cpu.length:null;
 const memUsed=mem.reduce((a,n)=>a+(n.status.mem||0),0),memMax=mem.reduce((a,n)=>a+n.status.maxmem,0);
 box.append(
  metric('Proxmox nodes',d.available?ok.length+' of '+nodes.length:'—',d.available?(ok.length===nodes.length?'all healthy':nodes.filter(n=>!n.ok).map(n=>n.node).join(', ')+' unreachable'):'not configured',
   {tone:d.available&&ok.length<nodes.length?'bad':null,onclick:()=>pxShowTab('nodes')}),
  metric('Virtual machines',String(running),'running of '+vms.length+(vms.length-running?' · '+(vms.length-running)+' stopped':''),{onclick:()=>pxShowTab('vms')}),
  metric('CPU',cpuAvg==null?'—':pxPct(cpuAvg),cpu.map(n=>n.node+' '+pxPct(n.status.cpu)).join(' · ')||'no data',{bar:cpuAvg,tone:cpuAvg>.85?'warn':null}),
  metric('Memory',memMax?pxPct(memUsed/memMax):'—',memMax?pxBytes(memUsed)+' of '+pxBytes(memMax):'no data',{bar:memMax?memUsed/memMax:null,tone:memMax&&memUsed/memMax>.9?'warn':null}));
 const w=pxWsSummary,c=(w&&w.counts)||{},up=(c.up||0)+(c.protected||0);
 // The value names what needs attention (down, then degraded); all-up reads as a plain count.
 const wsVal=!(w&&w.total)?'—':c.down?c.down+' down':c.degraded?c.degraded+' degraded':up+' up';
 box.append(metric('Websites',wsVal,w&&w.total?up+' up of '+w.total+(c.down&&c.degraded?' · '+c.degraded+' degraded':''):'not checked yet',
  {tone:c.down?'bad':c.degraded?'warn':null,onclick:()=>openView('websites')}));
 renderInfraAlerts();
}
function renderInfraAlerts(){
 const box=$('infra-alerts'),d=pxData,items=[];box.replaceChildren();
 for(const n of d.nodes||[]){
  if(!n.ok)items.push(['bad',n.node+' is not reachable',n.detail||n.error||'',()=>pxNodeDrawer(n)]);
  else{const miss=Object.values(n.permissions||{}).filter(c=>c&&!c.ok);
   if(miss.length&&(n.level||0)>0)items.push(['warn',n.node+': the token lacks rights agents may need',[...new Set(miss.flatMap(c=>c.missing))].slice(0,4).join(', '),()=>pxNodeDrawer(n)])}}
 for(const j of (d.jobs||[]).filter(j=>j.status==='failed').slice(0,2))items.push(['bad','Creating '+j.name+' failed',j.error||'',null]);
 const w=pxWsSummary?.counts||{};
 if(w.down)items.push(['bad',w.down+' website'+(w.down===1?' is':'s are')+' down','',()=>openView('websites')]);
 if(omData&&omData.available&&!omData.connected)items.push(['warn','Omada controller: '+(['certificate_not_trusted','certificate_changed'].includes(omData.error)?'certificate to confirm':'not connected'),'',()=>pxShowTab('network')]);
 box.hidden=!items.length;
 for(const [tone,title,detail,go] of items){const row=el(go?'button':'div','alert-row tone-'+tone);if(go){row.type='button';row.onclick=go}
  row.append(el('span','dot '+(tone==='bad'?'bad':'warn')),el('strong',null,title));if(detail)row.append(el('span','muted',detail));box.append(row)}
}
// Virtual machines (and containers) of both nodes: Name | State | CPU | RAM | Host.
function renderPxVms(){
 const box=$('px-vms');box.replaceChildren();
 const rows=[];for(const n of pxData.nodes||[]){if(!n.ok)continue;for(const vm of n.vms||[])rows.push([n,vm]);for(const ct of n.containers||[])rows.push([n,{...ct,ct:true}])}
 const q=pxQuery,shown=rows.filter(([n,v])=>!q||[v.vmid,v.name,n.node,v.ip,...(v.tags||[])].join(' ').toLowerCase().includes(q));
 if(!rows.length){box.append(el('div','empty-state',(pxData.nodes||[]).some(n=>n.ok)?'No VMs visible to the node tokens.':'No node could be read.'));return}
 if(!shown.length){box.append(el('div','empty-state','No VMs match “'+$('px-search').value+'”.'));return}
 const t=el('table','px-table'),head=el('thead'),hr=el('tr');for(const h of ['Name','State','CPU','RAM','Host'])hr.append(el('th',null,h));head.append(hr);
 const body=el('tbody');
 for(const [n,vm] of shown)body.append(pxVmRow(n,vm));
 t.append(head,body);box.append(t);
}
function pxStatePill(vm){const [label,tone]=PX_VM_STATUS[vm.status]||[vm.status||'unknown','unknown'];return el('span','pill st-'+tone,vm.template?'Template':label)}
function pxVmRow(n,vm){
 const tr=el('tr','px-vm'+(vm.managed?' px-managed':'')+(vm.ct?' px-ct':''));tr.dataset.vmid=vm.vmid;tr.dataset.node=n.node;tr.tabIndex=0;
 const name=el('td','px-name');name.dataset.label='Name';
 const line=el('div','px-name-line');line.append(el('strong',null,vm.name||'(no name)'),el('span','px-vmid',String(vm.vmid)));
 if(vm.ct)line.append(el('span','badge','container'));
 if(vm.managed)line.append(el('span','badge px-badge-managed','managed'+(vm.managed_status&&vm.managed_status!=='ready'?' · '+vm.managed_status.replace('_',' '):'')));
 if(vm.protected)line.append(el('span','badge badge-warn','protected'));
 name.append(line);if(vm.ip)name.append(el('span','px-ip',vm.ip));
 const st=el('td','px-state');st.dataset.label='State';st.append(pxStatePill(vm));
 const cpu=el('td','px-num');cpu.dataset.label='CPU';cpu.textContent=(vm.cpus?vm.cpus+' vCPU':'—')+(vm.status==='running'&&vm.cpu!=null?' · '+pxPct(vm.cpu):'');
 const ram=el('td','px-num');ram.dataset.label='RAM';ram.textContent=vm.maxmem?(vm.status==='running'&&vm.mem?pxBytes(vm.mem)+' / ':'')+pxBytes(vm.maxmem):'—';
 const host=el('td','px-host');host.dataset.label='Host';host.textContent=n.node;
 tr.append(name,st,cpu,ram,host);
 const open=()=>pxVmDrawer(n,vm);tr.onclick=open;tr.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();open()}};
 return tr}
function pxVmActions(n,vm){
 const who=vm.vmid+' ('+(vm.name||'no name')+')',items=[],off=!pxData.editable;
 const add=(label,run,o)=>items.push({label,run,disabled:off||(o&&o.disabled),danger:o&&o.danger,title:o&&o.title});
 if(vm.ct||vm.template)return items;
 if(vm.status!=='running')add('Start',async()=>{if(await uiConfirm('Start VM '+who+' on '+n.node+'?',{title:'Start VM',ok:'Start'}))await pxAction(n.node,{operation:'start',vmid:vm.vmid},'VM '+who+' started.')});
 else{
  add('Shutdown',async()=>{if(await uiConfirm('Shut down VM '+who+' cleanly (ACPI)?',{title:'Shut down VM',ok:'Shut down'}))await pxAction(n.node,{operation:'shutdown',vmid:vm.vmid},'VM '+who+' shut down.')});
  add('Reboot',async()=>{if(await uiConfirm('Reboot VM '+who+'?',{title:'Reboot VM',ok:'Reboot'}))await pxAction(n.node,{operation:'reboot',vmid:vm.vmid},'VM '+who+' rebooted.')});
  add('Stop',async()=>{if(await uiConfirm('Stop VM '+who+' immediately? This is like pulling the power cable.',{title:'Stop VM',ok:'Stop VM',danger:true}))await pxAction(n.node,{operation:'stop',vmid:vm.vmid},'VM '+who+' stopped.')},{danger:true});
 }
 items.push(null);
 add('Take snapshot…',async()=>{
  const f=await uiForm('Snapshot of '+who,[{name:'snapname',label:'Name',value:'before-'+new Date().toISOString().slice(0,10).replace(/-/g,''),required:true,maxLength:40,
    pattern:'^[A-Za-z][A-Za-z0-9_-]{1,39}$',patternMessage:'2-40 letters, digits, - and _, starting with a letter'},{name:'description',label:'Description',maxLength:200}],{ok:'Take snapshot'});
  if(f)await pxAction(n.node,{operation:'snapshot_create',vmid:vm.vmid,snapname:f.snapname.trim(),description:f.description||''},'Snapshot '+f.snapname.trim()+' of '+who+' taken.')});
 add('Roll back…',async()=>{
  const s=await api('proxmox/snapshots',{method:'POST',headers:UI_HEADERS,body:JSON.stringify({node:n.node,vmid:vm.vmid})});
  if(!s.snapshots.length){notice('VM '+who+' has no snapshots.');return}
  const f=await uiForm('Roll back '+who,[{name:'snapname',label:'Snapshot',type:'select',value:s.snapshots[s.snapshots.length-1].name,
    options:s.snapshots.map(x=>[x.name,x.name+(x.description?' · '+x.description:'')])}],{ok:'Next',intro:'Everything on the VM since that snapshot is lost.'});
  if(!f)return;
  if(await uiConfirm('Roll VM '+who+' back to '+f.snapname+'? Changes since then are lost.',{title:'Roll back',ok:'Roll back',danger:true}))
   await pxAction(n.node,{operation:'snapshot_rollback',vmid:vm.vmid,snapname:f.snapname},'VM '+who+' rolled back to '+f.snapname+'.')},{danger:true});
 if(vm.managed&&['onboarding_failed','onboarding'].includes(vm.managed_status))
  add('Onboard again',async()=>{if(await uiConfirm('Try the onboarding of '+who+' again (SSH, sudo check, guest agent, register as a server)?',{title:'Onboard',ok:'Onboard'}))
   await pxAction(n.node,{operation:'onboard',vmid:vm.vmid},vm.name+' is now a server at level 0.')});
 if(vm.managed){items.push(null);add('Destroy…',async()=>{
  const f=await uiForm('Destroy '+who,[{name:'confirm',label:'Type '+vm.name+' to confirm',required:true,pattern:'^'+vm.name.replace(/[^a-z0-9-]/g,'')+'$',
    patternMessage:'Type the VM name exactly'}],{ok:'Destroy VM',danger:true,intro:'Stops VM '+who+', destroys it with all its disks, removes the server '+vm.name+' and its SSH key. This cannot be undone.'});
  if(f)await pxAction(n.node,{operation:'destroy_vm',vmid:vm.vmid},'VM '+who+' destroyed.')},{danger:true,title:'Only VMs created here can be destroyed'})}
 return items}
function pxVmDrawer(n,vm){
 pxOpen={node:n.node,vmid:vm.vmid};
 const d=drawer('px-drawer');d.onclose=()=>{pxOpen=null};
 d.title.textContent=vm.name||'(no name)';d.meta.replaceChildren(pxStatePill(vm),document.createTextNode(' '+(vm.ct?'Container':'VM')+' '+vm.vmid+' on '+n.node));
 d.actions.replaceChildren();const items=pxVmActions(n,vm);
 if(items.some(Boolean))d.actions.append(actionMenu('Actions',items,'px-actions-btn'));
 const b=d.body;b.replaceChildren();b.className='drawer-body px-detail';b.dataset.vmid=vm.vmid;b.dataset.node=n.node;
 if(!pxData.editable)b.append(el('p','policy-note',(pxData.approver&&pxData.approver.reason)||'Only approvers can change VMs here.'));
 const ips=[...new Set([...(vm.ip_guest||[]),...(vm.ip_config||[])])];
 b.append(el('h3','drawer-section','Overview'),factList([
  ['State',vm.template?'Template':(PX_VM_STATUS[vm.status]||[vm.status||'unknown'])[0]+(vm.uptime&&vm.status==='running'?' · up '+pxUptime(vm.uptime):'')],
  ['Host',n.node+(n.label?' · '+n.label:'')],
  ['CPU',vm.cpus?vm.cpus+' vCPU'+(vm.status==='running'&&vm.cpu!=null?' · '+pxPct(vm.cpu)+' in use':''):null],
  ['Memory',vm.maxmem?pxBytes(vm.maxmem)+(vm.status==='running'&&vm.mem?' · '+pxBytes(vm.mem)+' in use':''):null],
  ['Disk',vm.maxdisk?pxBytes(vm.maxdisk):null],
  ['IP addresses',ips.length?ips:(vm.ct?null:'none known')],
  ['Gateway',vm.gateway],['DNS',vm.nameserver],
  ['Network',(vm.bridges||[]).map((br,i)=>br+((vm.vlans||[])[i]?' · VLAN '+vm.vlans[i]:'')).join(', ')||null],
  ['Guest agent',vm.ct?null:vm.agent?'enabled':'off'],
  ['Tags',(vm.tags||[]).filter(t=>t!=='hla-managed')],
  ['Created here',vm.managed?'yes'+(vm.managed_status?' · '+vm.managed_status.replace('_',' '):''):null],
  ['Protected',vm.protected?'yes: agents cannot destroy or roll it back':null]]));
 // Agent access: the node's level, and for servers created here their own level.
 b.append(el('h3','drawer-section','Agent access'));
 const acc=el('div','px-access'),lvlName=(PX_LEVELS.find(([v])=>v===(n.level||0))||[0,String(n.level||0)])[1].replace(/^\d+ · /,'');
 acc.append(el('p','muted','Agents act on this '+(vm.ct?'container':'VM')+' through proxmox:'+n.node+' at level '+(n.level||0)+' ('+lvlName+').'));
 const srv=pxServerOf(n,vm);
 if(srv){const row=el('div','px-server');row.dataset.server=srv.name;const h=el('div','px-server-head');
  const who=el('div','px-server-who');who.append(el('span','muted','Server '),el('strong',null,srv.name),el('span','muted',' · '+srv.host));
  h.append(who,pxLevelSelect(srv.name,srv.level||0,PX_SERVER_LEVELS,'Level'));row.append(h);acc.append(row)}
 b.append(acc);
 if(!vm.ct&&!vm.template){
  b.append(el('h3','drawer-section','Snapshots'));
  const snap=el('div','px-snaps'),load=el('button','secondary small','Show snapshots');load.type='button';
  load.onclick=()=>whileBusy(load,async()=>{try{const s=await api('proxmox/snapshots',{method:'POST',headers:UI_HEADERS,body:JSON.stringify({node:n.node,vmid:vm.vmid})});
   snap.replaceChildren();if(!s.snapshots.length){snap.append(el('p','muted','No snapshots yet.'));return}
   const ul=el('ul','plain-list');for(const x of s.snapshots){const li=el('li');li.append(el('strong',null,x.name));
    if(x.description)li.append(el('span','muted',' · '+x.description));if(x.snaptime)li.append(el('span','muted',' · '+new Date(x.snaptime*1000).toLocaleString()));ul.append(li)}
   snap.append(ul)}catch(e){snap.replaceChildren(el('p','error',e.message))}});
  snap.append(load);b.append(snap)}
 d.open();
}
function pxUptime(sec){if(!sec)return '—';if(sec<3600)return Math.max(1,Math.round(sec/60))+' min';if(sec<86400)return Math.round(sec/3600)+' h';return Math.round(sec/86400)+' d'}
// Nodes: one row each (health, CPU, memory, VMs, level) → drawer.
function renderPxNodes(){
 const box=$('px-nodes');box.replaceChildren();
 for(const n of pxData.nodes||[]){
  const row=el('button','px-node');row.type='button';row.dataset.node=n.node;
  const st=n.status||{};
  const name=el('div','px-node-name');name.append(el('span','dot '+(n.ok?'ok':'bad')),el('strong',null,n.node),el('span','muted',n.label||''));
  const facts=el('div','px-node-facts');
  if(n.ok)facts.append(el('span',null,'CPU '+pxPct(st.cpu)),el('span',null,'Memory '+(st.maxmem?pxPct((st.mem||0)/st.maxmem):'—')),
   el('span',null,(n.vms||[]).length+' VMs'+((n.containers||[]).length?' · '+n.containers.length+' containers':'')));
  else facts.append(el('span','error',n.detail||n.error||'Not reachable'));
  row.append(name,facts,el('span','badge px-level-badge','Level '+(n.level||0)));row.onclick=()=>pxNodeDrawer(n);box.append(row)}
}
function pxNodeDrawer(n){
 pxOpen={node:n.node,vmid:null};
 const d=drawer('px-drawer');d.onclose=()=>{pxOpen=null};
 d.title.textContent=n.node;d.meta.replaceChildren(el('span','pill st-'+(n.ok?'completed':'failed'),n.ok?'Reachable':'Not reachable'),document.createTextNode(' '+(n.label||'')+(n.version?' · PVE '+n.version.version:'')));
 d.actions.replaceChildren();
 const create=el('button','small','Create VM');create.type='button';create.disabled=!pxData.editable||!n.ok;create.dataset.node=n.node;create.onclick=()=>{d.close();pxCreate(n)};
 d.actions.append(create,actionMenu('More',[{label:'Read-only monitoring token…',disabled:!pxData.editable||!n.ok,run:async()=>{
  const names=['PVE_'+n.node.toUpperCase()+'_AUDIT_TOKEN_ID','PVE_'+n.node.toUpperCase()+'_AUDIT_TOKEN_SECRET'];
  if(!await uiConfirm('Create a read-only monitoring token on '+n.node+'?',{title:'Read-only token',ok:'Create token',
   detail:'User hla-monitoring@pve with role PVEAuditor on /\nToken hla-monitoring@pve!monitoring (privilege separated)\nStored in Agent Generated as '+names.join(' and ')}))return;
  const out=await pxAction(n.node,{operation:'create_audit_token'},'');
  if(out.status==='created'||out.status==='exists')notice('Token '+(out.status==='exists'?'already there':'created')+': agents use '+out.token_secret+'.')}}]));
 const b=d.body;b.replaceChildren();b.className='drawer-body px-node-detail';b.dataset.node=n.node;
 const lv=el('div','px-node-level');lv.append(pxLevelSelect('proxmox:'+n.node,n.level||0,PX_LEVELS,'Agents'));
 lv.append(el('p','fineprint','Agents reach this node only at this level: 1 read, 4 power and snapshots, 5 create VMs after you approve the exact request, 6 roll back and destroy VMs created here.'));
 b.append(lv);
 if(!n.ok){b.append(el('p','error',n.detail||n.error||'Not reachable'));d.open();return}
 const st=n.status||{};
 b.append(el('h3','drawer-section','Resources'),factList([['CPU',pxPct(st.cpu)+(st.cpus?' of '+st.cpus+' cores':'')],['Memory',pxBytes(st.mem)+' of '+pxBytes(st.maxmem)],
  ['Uptime',st.uptime?pxUptime(st.uptime):null],['Guests',(n.vms||[]).length+' VMs'+((n.containers||[]).length?' · '+n.containers.length+' containers':'')],['Next free VMID',n.nextid],
  ['Storage',(n.storages||[]).filter(s=>s.active&&s.enabled).map(s=>s.storage+' '+pxBytes(s.avail)+' free')]]));
 b.append(el('h3','drawer-section','Token rights'));
 const caps=el('div','px-caps');
 for(const [k,label] of PX_CAPS){const c=(n.permissions||{})[k];if(!c)continue;
  const x=el('span','badge '+(c.ok?'px-cap-ok':'px-cap-no'),(c.ok?'':'Missing · ')+label);x.title=c.ok?'The node token allows this':'Missing: '+c.missing.join(', ');caps.append(x)}
 b.append(caps);
 const tok=n.token||{},missing=PX_CAPS.map(([k])=>(n.permissions||{})[k]).filter(c=>c&&!c.ok);
 if(tok.user||n.pve_node)b.append(el('p','fineprint','Token user '+(tok.user||'?')+(tok.privsep===true?' · privilege separation on':tok.privsep===false?' · privilege separation off':'')
  +(n.pve_node&&n.pve_node!==n.node?' · Proxmox node name '+n.pve_node:'')));
 if(missing.length){const why=el('details','px-missing');why.append(el('summary',null,'Why are rights missing?'));
  why.append(el('p','fineprint',[...new Set(missing.flatMap(c=>c.missing))].join(', ')));
  if(tok.privsep)why.append(el('p','fineprint','Privilege separation is on: Proxmox gives the token only what both the token and its user ('+tok.user+') have. Give '+tok.user+' the same role on /, or edit the token and untick Privilege Separation.'));
  b.append(why)}
 if(n.errors&&n.errors.length)b.append(el('p','fineprint px-part-errors','Not readable with this token: '+n.errors.map(e=>e.part+(e.detail?' ('+e.detail+')':'')).join('; ')));
 d.open();
}
function pxSuggestIp(n,vmid){
 const used=new Set();for(const x of pxData.nodes)for(const v of x.vms)for(const ip of [...(v.ip_config||[]),...(v.ip_guest||[])])used.add(ip);
 for(const s of pxData.servers||[])used.add(s.host);
 const net=(pxData.allowed_nets||['192.168.10.0/24'])[0],base=net.split('/')[0].split('.').slice(0,3).join('.');
 if(vmid>=2&&vmid<=254&&!used.has(base+'.'+vmid))return base+'.'+vmid+'/'+(net.split('/')[1]||'24');
 for(let i=200;i<=254;i++)if(!used.has(base+'.'+i))return base+'.'+i+'/'+(net.split('/')[1]||'24');
 return ''}
async function pxCreate(n){
 const df=n.defaults||{},lim=pxData.limits||{cores:[1,8],memory:[512,16384],disk:[8,256]};
 const storages=n.storages.filter(s=>s.content.includes('images')&&s.active&&s.enabled);
 if(!storages.length){notice(n.node+' has no storage for VM disks.');return}
 const usedIds=new Set([...(pxData.reserved_vmids||[]),...pxData.nodes.flatMap(x=>[...x.vms,...x.containers].map(v=>v.vmid))]);
 let vmid0=Math.max(n.nextid||100,...pxData.nodes.map(x=>x.nextid||100));while(usedIds.has(vmid0))vmid0++;
 let values={vmid:vmid0,name:'',cores:df.cores||2,memory:df.memory||2048,disk:df.disk||32,storage:df.storage||storages[0].storage,
  bridge:df.bridge||(n.bridges[0]||{}).iface,vlan:'',ip:'',gateway:df.gateway||'',dns:df.dns||df.gateway||''},intro='Debian 13 cloud image, user svc-hla-agent with a key the gateway keeps. The VM becomes a server at level 0.';
 values.ip=pxSuggestIp(n,values.vmid);
 for(;;){
  const f=await uiForm('Create a VM on '+n.node,[
   {name:'vmid',label:'VMID',type:'number',value:values.vmid,min:100,max:9999,step:1,required:true,hint:'Next free: '+(n.nextid??'—')+'. 100-108 and 204 are reserved.'},
   {name:'name',label:'Name (host and server name)',value:values.name,required:true,maxLength:32,pattern:'^[a-z][a-z0-9-]{0,30}[a-z0-9]$',patternMessage:'Lowercase letters, digits and -, starting with a letter',placeholder:'monitoring'},
   {name:'cores',label:'CPU cores',type:'number',value:values.cores,min:lim.cores[0],max:lim.cores[1],step:1,required:true},
   {name:'memory',label:'Memory (MiB)',type:'number',value:values.memory,min:lim.memory[0],max:lim.memory[1],step:256,required:true},
   {name:'disk',label:'Disk (GiB)',type:'number',value:values.disk,min:lim.disk[0],max:lim.disk[1],step:1,required:true},
   {name:'storage',label:'Storage',type:'select',value:values.storage,options:storages.map(s=>[s.storage,s.storage+' · '+pxBytes(s.avail)+' free'])},
   {name:'bridge',label:'Network bridge',type:'select',value:values.bridge,options:n.bridges.map(b=>[b.iface,b.iface+(b.comments?' · '+b.comments:b.cidr?' · '+b.cidr:'')])},
   {name:'vlan',label:'VLAN tag',value:values.vlan,maxLength:4,pattern:'^[0-9]{0,4}$',patternMessage:'A number 1-4094, 0 for untagged',placeholder:'automatic',hint:'Empty: the tag other VMs in the same network use. 0 = untagged.'},
   {name:'ip',label:'IP address with prefix',value:values.ip,required:true,maxLength:18,placeholder:'192.168.10.205/24',hint:'Allowed: '+(pxData.allowed_nets||[]).join(', ')},
   {name:'gateway',label:'Gateway',value:values.gateway,required:true,maxLength:15},
   {name:'dns',label:'DNS server(s)',value:values.dns,required:true,maxLength:50}],{ok:'Preview',intro});
  if(!f)return;
  values={...values,...f,vmid:Number(f.vmid),cores:Number(f.cores),memory:Number(f.memory),disk:Number(f.disk),name:f.name.trim(),ip:f.ip.trim(),gateway:f.gateway.trim(),dns:f.dns.trim(),vlan:(f.vlan||'').trim()};
  const spec={node:n.node,...values,vlan:values.vlan===''?null:Number(values.vlan),image:'debian-13'};
  let p;
  try{p=await api('proxmox/preview',{method:'POST',headers:UI_HEADERS,body:JSON.stringify(spec)})}catch(e){notice(e.message);return}
  if(!p.valid){intro='Not possible yet: '+p.detail;continue}
  const ok=await uiConfirm('Create this VM on '+n.node+'?',{title:'Create VM',ok:'Create VM',
   detail:p.approval_text+(p.warnings&&p.warnings.length?'\n\nNote: '+p.warnings.join('\nNote: '):'')});
  if(!ok)return;
  const r=await api('proxmox/create',{method:'POST',headers:UI_HEADERS,body:JSON.stringify(spec)});
  notice('Creating '+spec.name+' on '+n.node+': this takes a few minutes (image, boot, onboarding).');
  pxData.jobs=[r.job,...(pxData.jobs||[])];renderPxJobs(pxData.jobs);pxWatchJobs();return}
}
function renderPxJobs(jobs){
 const box=$('px-jobs');box.replaceChildren();
 for(const j of jobs.slice(0,4)){
  const line=el('div','px-job px-job-'+j.status);
  line.append(el('span','pill st-'+(j.status==='running'?'running':j.status==='done'?'completed':'failed'),j.status==='running'?'Creating':j.status==='done'?'Created':'Failed'),
   el('strong',null,' '+j.name+' '),el('span','muted','VM '+j.vmid+' on '+j.node+(j.ip?' · '+j.ip:'')+' · '+when(new Date(j.started*1000))));
  if(j.error)line.append(el('p','error px-job-error',j.error));
  if(j.status==='done'&&j.result&&j.result.server)line.append(el('p','muted px-job-result','Server '+j.result.server.name+' registered at level 0 · host key '+j.result.server.host_fingerprint));
  box.append(line)}
 if(jobs.some(j=>j.status==='running'))pxWatchJobs();
}
function pxWatchJobs(){
 if(pxJobTimer)return;
 pxJobTimer=setInterval(async()=>{
  if($('infrastructure').hidden)return;
  try{const {jobs}=await api('proxmox/jobs');const was=(pxData.jobs||[]).filter(j=>j.status==='running').map(j=>j.id);
   pxData.jobs=jobs;renderPxJobs(jobs);
   if(!jobs.some(j=>j.status==='running')){clearInterval(pxJobTimer);pxJobTimer=null}
   const finished=jobs.filter(j=>was.includes(j.id)&&j.status!=='running');
   if(finished.length){for(const j of finished)notice(j.status==='done'?j.name+' is ready: a server at level 0.':j.name+' failed: '+j.error);await loadInfrastructure(true)}}
  catch(e){}},4000)}
function renderPxServers(){
 const box=$('px-servers'),servers=pxData.servers||[],managed=(pxData.managed||[]).filter(m=>m.status!=='destroyed'&&!servers.some(s=>s.name===m.name));
 box.replaceChildren();
 if(!servers.length&&!managed.length){box.append(el('p','muted','None yet. Create a VM above; it appears here once it is onboarded.'));return}
 for(const s of servers){
  const row=el('div','px-server');row.dataset.server=s.name;
  const head=el('div','px-server-head');head.append(el('strong',null,s.name),el('span','muted',s.host+' · VM '+s.vmid+' on '+s.node));
  head.append(pxLevelSelect(s.name,s.level||0,PX_SERVER_LEVELS,'Level'));
  const meta=el('div','px-server-meta');
  const fp=el('code','px-fp',s.host_fingerprint);fp.title='SSH host key recorded on first contact; compare it with the VM console (ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub)';
  meta.append(el('span','muted','Host key'),fp,el('span','muted','· key '+s.key_credential+' · by '+s.created_by+' · '+when(new Date(s.created_at*1000))));
  row.append(head,meta);box.append(row)}
 for(const m of managed){
  const row=el('div','px-server px-server-pending');
  row.append(el('strong',null,m.name),el('span','muted',' VM '+m.vmid+' on '+m.node+' · '+m.status.replace('_',' ')+(m.note?': '+m.note:'')));
  box.append(row)}
}

// HLA_MODEL_ROUTING_V1 model choices come from the server-side allowlist (the model registry's enabled models).
const modelOptionText=(m,id)=>(m.labels&&m.labels[id])?m.labels[id]+' · '+id:id;
async function loadRoutingModels(){
 try {const m=await api('models');for(const id of ['task-model-override','thread-model-override']){const el=$(id);if(!el)continue;const current=el.value;el.replaceChildren(new Option('Agent default',''));for(const v of m.models)el.add(new Option(modelOptionText(m,v),v));if(m.models.includes(current))el.value=current;}}catch(err){console.warn('Model choices unavailable',err);}
}

// HLA_UI_V3: shared helpers, approvals, task history drawer and conversations.
const UI_HEADERS={'Content-Type':'application/json','x-agent-ui':'1'};
const STATUS_LABELS={human_approval_required:'Needs approval',running:'Running',completed:'Completed',incomplete:'Incomplete',
 failed:'Failed',rejected:'Rejected',expired:'Expired',cancelled:'Stopped'};
const APPROVAL_LABELS={human_approval_required:'Approval required',running:'Approved · running',completed:'Approved · completed',
 incomplete:'Approved · incomplete',failed:'Failed',rejected:'Rejected',expired:'Expired',cancelled:'Approved · stopped'};
const EVENT_LABELS={requested:'Approval requested',approved:'Approved',rejected:'Rejected',expired:'Expired',
 completed:'Run completed',incomplete:'Run incomplete',failed:'Run failed',cancelled:'Run stopped'};
let approver={allowed:true,actor:'dashboard_session',verified:false,reason:null};
let currentTaskId=null,detailPoll=null,lastFocus=null,taskSearchTimer=null;
const taskQuery={offset:0,limit:25};
let currentThread=null,threadPoll=null,threadsCache=[],threadAgents=[],threadSending=false;
let live=null; // {taskId, text, steps, source} of the run this thread is waiting on

function el(tag,cls,text){const e=document.createElement(tag);if(cls)e.className=cls;if(text!=null)e.textContent=text;return e}
function statusPill(status){const s=el('span','pill st-'+String(status||'unknown').replace(/[^a-z_]/g,''));s.textContent=STATUS_LABELS[status]||status||'Unknown';return s}
function when(v){
 if(!v)return '—';const d=new Date(v),secs=(Date.now()-d)/1000;
 if(secs<60)return 'just now';if(secs<3600)return Math.floor(secs/60)+' min ago';if(secs<86400)return Math.floor(secs/3600)+' h ago';
 return d.toLocaleDateString(undefined,{month:'short',day:'numeric'})+', '+d.toLocaleTimeString(undefined,{hour:'2-digit',minute:'2-digit'});
}
// Conversation runs store the whole transcript as the task text; people want to see what they last asked.
function latestMessage(text){return String(text||'').split('LATEST USER MESSAGE:').pop().trim()}
function emptyState(text){const d=el('div','empty-state');d.append(el('p',null,text));return d}

async function loadApprover(){
 try{
  const s=await api('session');if(!s.authenticated)return;
  approver=s.approver||approver;
  // The composer offers at most the server's output-token limit (HLA_MAX_OUTPUT_TOKENS).
  // Empty = unlimited; a typed limit is capped at HLA_MAX_OUTPUT_TOKENS when the server sets one.
  for(const id of ['thread-output-tokens','max-tokens']){const t=$(id);if(!t)continue;
   if(s.max_output_tokens){t.max=s.max_output_tokens;t.placeholder='Unlimited (up to '+s.max_output_tokens.toLocaleString()+' if set)'}
   else t.removeAttribute('max')}
  approverClient=s.client_ip||'unknown';
  const b=$('approver-badge');b.hidden=false;
  b.replaceChildren(el('span','dot '+(approver.verified?'ok':'')),el('span',null,approverLine()));b.title=approverHint();
  $('profile-sub').textContent=approver.verified?'Approver: '+approver.actor:'Approver not verified';
 }catch(e){/* badge is informational */}
}

let approverClient='unknown';
function approverLine(){return approver&&approver.verified?'Approver: '+approver.actor:'Approver not verified'}
function approverHint(){return approver&&approver.reason||(approver&&approver.verified?'Approvals are recorded as '+approver.actor+'.':
 'Approvals are recorded as dashboard_session. Set HLA_TRUSTED_PROXY_IPS to record your Authelia user (this request came from '+approverClient+').')}
// Approvals: one card for conversations and the task drawer.
const DECISION_TEXT={
 approve_read_only:'Run this request once with read-only diagnostics? The agent cannot change anything and will describe any change it recommends.',
 approve:'Approve this exact request to run once with the agent\'s normal permissions? It may change infrastructure within its gateway limits.',
 reject:'Reject this request without running it?'};
const DECISION_DIALOG={approve_read_only:{title:'Approve read-only',ok:'Approve read-only'},approve:{title:'Approve request',ok:'Approve & run'},
 reject:{title:'Reject request',ok:'Reject request',danger:true}};
async function decideApproval(taskId,action,reason){
 if(!await uiConfirm(DECISION_TEXT[action],{...DECISION_DIALOG[action],detail:action==='reject'&&(reason||'').trim()?'Reason: '+reason.trim():undefined}))return false;
 await api('tasks/'+encodeURIComponent(taskId)+'/approval',{method:'POST',headers:UI_HEADERS,body:JSON.stringify({action,reason:(reason||'').trim()||null})});
 notice(action==='reject'?'Request rejected.':action==='approve_read_only'?'Approved read-only: the agent is investigating.':'Approved: the agent is running the request.');
 return true;
}
function approvalCard(info,onDone){
 const status=info.status||'unknown';
 const card=el('div','approval-card approval-'+status);
 const head=el('div','approval-head');const pill=statusPill(status);pill.textContent=APPROVAL_LABELS[status]||status;head.append(pill);
 if(info.risk!=null)head.append(el('span','muted','TypeSafe risk '+Math.round(Number(info.risk)*100)+'%'));
 if(info.decided_by)head.append(el('span','muted',(status==='rejected'?'Rejected':'Approved')+' by '+info.decided_by+(info.decision_scope==='read_only'?' · read-only':'')));
 card.append(head);
 if(status==='expired')card.append(el('p','muted','This request waited too long and can no longer be approved. Send it again if it is still needed.'));
 if(status!=='human_approval_required')return card;
 card.append(el('p','muted','Read-only lets the agent run diagnostics and report back without changing anything. Approve & run uses the agent\'s normal tool permissions; the SSH gateway still enforces its own limits.'));
 if(!approver.allowed)card.append(el('p','error',approver.reason));
 else card.append(el('p','fineprint approver-line',(approver.verified?'You approve as '+approver.actor:'Approver not verified: recorded as dashboard_session')+'.'));  // HLA_UI_V4
 const reason=el('input');reason.maxLength=500;reason.dataset.k='reason-'+info.task_id;reason.placeholder='Reason (optional, saved with a rejection)';reason.setAttribute('aria-label','Rejection reason');
 const actions=el('div','approval-actions');
 const buttons=[['approve_read_only','secondary','Approve read-only'],['approve','','Approve & run'],['reject','ghost','Reject']].map(([action,cls,label])=>{
  const b=el('button',cls||null,label);b.type='button';b.disabled=!approver.allowed;b.dataset.action=action;return b});
 for(const b of buttons)b.onclick=async()=>{
  buttons.forEach(x=>x.disabled=true);let decided=false;
  try{decided=await decideApproval(info.task_id,b.dataset.action,reason.value)}catch(err){notice(err.message)}
  finally{if(!decided)buttons.forEach(x=>x.disabled=!approver.allowed);await onDone();refreshInbox()}
 };
 actions.append(...buttons);card.append(reason,actions);
 return card;
}

// HLA_CHAT_APPROVAL_V1: the one shell command a reply asked to run; approving runs exactly that, once.
const COMMAND_LABELS={pending:'Command awaiting approval',running:'Approved · running',ran:'Ran',failed:'Did not run',
 rejected:'Rejected',expired:'Expired',interrupted:'Interrupted'};
// HLA_PROXMOX_V1: a Proxmox request (create/destroy a VM, audit token) uses the same card with its readable spec.
const PROXMOX_LABELS={pending:'Proxmox request awaiting approval',running:'Approved · running',ran:'Approved · done',failed:'Did not run',
 rejected:'Rejected',expired:'Expired',interrupted:'Interrupted'};
// HLA_OMADA_V1: so does an Omada write (method, path and body of a site setting).
const SERVICE_CARDS={proxmox:{labels:PROXMOX_LABELS,cls:'proxmox-card',title:'Approve Proxmox request',where:s=>s.replace('proxmox:','')},
 omada:{labels:{...PROXMOX_LABELS,pending:'Omada change awaiting approval'},cls:'omada-card',title:'Approve Omada change',where:()=>'the Omada controller'}};
function commandCard(taskId,cmd,onDone){
 const status=cmd.status||'pending',ok=status==='ran'&&cmd.exit_code===0,svc=SERVICE_CARDS[cmd.kind],pve=!!svc;
 const tone=status==='pending'?'human_approval_required':ok?'completed':status==='running'?'running':['ran','failed'].includes(status)?'failed':'rejected';
 const card=el('div','approval-card command-card approval-'+tone+(pve?' service-card '+svc.cls:''));
 const head=el('div','approval-head');
 head.append(el('span','pill st-'+tone,pve?(status==='ran'&&!ok?'Approved · failed':svc.labels[status]):
  COMMAND_LABELS[status]+(status==='ran'?(cmd.timed_out?' · timed out':' · exit '+cmd.exit_code):'')),el('span','muted','on '+cmd.server));
 if(cmd.decided_by)head.append(el('span','muted',(status==='rejected'?'Rejected':'Approved')+' by '+cmd.decided_by));
 const pre=el('pre','command-text');pre.append(el('code',null,cmd.command));
 card.append(head,pre);
 if((status==='failed'||(pve&&status==='ran'&&!ok))&&cmd.error)card.append(el('p','error',cmd.error));
 if(status==='interrupted')card.append(el('p','muted','Stopped or cut off by a restart before the result came back; the command may still have run. Ask the agent to check.'));
 if(status==='expired')card.append(el('p','muted','This command waited too long and can no longer be approved. Ask the agent again if it is still needed.'));
 if(status!=='pending')return card;
 card.append(el('p','muted',pve?'Approving lets the SSH gateway do exactly this once on '+svc.where(cmd.server)+', for the agent (its level still applies). The agent then checks the result.'
  :'Approving runs this exact command once on '+cmd.server+' as the agent\'s SSH user. The agent then checks the result with read-only diagnostics.'));
 if(!approver.allowed)card.append(el('p','error',approver.reason));
 else card.append(el('p','fineprint approver-line',(approver.verified?'You approve as '+approver.actor:'Approver not verified: recorded as dashboard_session')+'.'));  // HLA_UI_V4
 const actions=el('div','approval-actions');
 const buttons=[['approve','','Approve & run once'],['reject','ghost','Reject']].map(([action,cls,label])=>{
  const b=el('button',cls||null,label);b.type='button';b.disabled=!approver.allowed;b.dataset.action=action;return b});
 for(const b of buttons)b.onclick=async()=>{
  const approve=b.dataset.action==='approve';
  if(!await uiConfirm(pve?(approve?'Do this once on '+svc.where(cmd.server)+'?':'Reject this request? Nothing will change.'):approve?'Run this exact command once on '+cmd.server+'?':'Reject this command? Nothing will run.',
   pve?(approve?{title:svc.title,detail:cmd.command,ok:'Approve'}:{title:'Reject request',detail:cmd.command,ok:'Reject request',danger:true})
   :approve?{title:'Run command',detail:cmd.command,ok:'Run once'}:{title:'Reject command',detail:cmd.command,ok:'Reject command',danger:true}))return;
  buttons.forEach(x=>x.disabled=true);
  try{await api('tasks/'+encodeURIComponent(taskId)+'/command',{method:'POST',headers:UI_HEADERS,body:JSON.stringify({action:b.dataset.action})});
   notice(approve?(pve?'Approved: the gateway runs the request once.':'Approved: running the command once.'):pve?'Request rejected.':'Command rejected.')}
  catch(err){notice(err.message);buttons.forEach(x=>x.disabled=!approver.allowed)}
  finally{await onDone();refreshInbox()}
 };
 actions.append(...buttons);card.append(actions);
 return card;
}

// Task history.
async function loadTasks(){
 const params=new URLSearchParams({limit:taskQuery.limit,offset:taskQuery.offset});
 const status=$('task-status-filter').value,ws=$('task-workspace-filter').value,q=$('task-search').value.trim();
 if(status)params.set('status',status);if(ws)params.set('workspace',ws);if(q)params.set('q',q);
 const data=await api('tasks?'+params);
 const body=$('task-rows');body.replaceChildren();
 for(const t of data.tasks){
  const tr=el('tr');tr.tabIndex=0;tr.dataset.id=t.id;if(t.id===currentTaskId)tr.classList.add('selected');
  const req=el('td','req');const text=el('div','req-text',t.display_title||t.summary||'(empty request)');text.title=t.summary||'';req.append(text);
  if(t.error_message&&!['completed','human_approval_required'].includes(t.status))req.append(el('div','req-sub',t.error_message));
  else if(t.display_context)req.append(el('div','req-sub req-ctx',t.display_context));  // HLA_UI_V5: where the run belongs
  const st=el('td','status-cell');st.append(statusPill(t.status));
  const who=el('td','who');who.append(el('div',null,t.agent_name||'General worker'),el('div','req-sub',t.worker_model||'—'));
  const created=el('td','when',when(t.created_at));created.title=new Date(t.created_at).toLocaleString();
  tr.append(req,st,who,el('td','num',subCostText(t.worker_model,t.openrouter_cost_usd,t.billing)),created);  // HLA_SUBSCRIPTIONS_V1
  tr.onclick=()=>openTaskDetail(t.id);
  tr.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();openTaskDetail(t.id)}};
  body.append(tr);
 }
 if(!data.tasks.length){const tr=el('tr','empty-row');const td=el('td',null,(status||ws||q)?'No tasks match these filters.':'No tasks yet. Launch one from Overview.');td.colSpan=5;tr.append(td);body.append(tr)}
 $('task-page').textContent=data.tasks.length?`${taskQuery.offset+1}–${taskQuery.offset+data.tasks.length}`:'';
 $('task-prev').disabled=taskQuery.offset===0;$('task-next').disabled=!data.has_more;
}
function reloadTasksFromStart(){taskQuery.offset=0;loadTasks().catch(e=>notice(e.message))}
$('task-status-filter').addEventListener('change',reloadTasksFromStart);
$('task-workspace-filter').addEventListener('change',reloadTasksFromStart);
$('task-search').addEventListener('input',()=>{clearTimeout(taskSearchTimer);taskSearchTimer=setTimeout(reloadTasksFromStart,300)});
$('task-prev').addEventListener('click',()=>{taskQuery.offset=Math.max(0,taskQuery.offset-taskQuery.limit);loadTasks().catch(e=>notice(e.message))});
$('task-next').addEventListener('click',()=>{taskQuery.offset+=taskQuery.limit;loadTasks().catch(e=>notice(e.message))});
document.querySelector('button[data-view="tasks"]').addEventListener('click',()=>loadTasks().catch(e=>notice(e.message)));

// HLA_TIMINGS_V1: where a run's time went, e.g. "first words 2.1 s · risk 0.4 s · 2 model rounds 5.3 s (first token 0.9 s) · tools 0.1 s".
function timingsText(t){
 if(!Array.isArray(t)||!t.length)return null;
 const s=ms=>(ms/1000).toFixed(1)+' s',sum=st=>t.filter(x=>x.stage===st).reduce((a,x)=>a+(x.ms||0),0),has=st=>t.some(x=>x.stage===st);
 const parts=[],first=t.find(x=>x.stage==='first_text'),rounds=t.filter(x=>x.stage==='model');
 if(first)parts.push('first words '+s(first.ms));
 for(const[st,label]of[['summary','summary'],['risk_review','risk review'],['knowledge','knowledge'],['permissions','permissions'],['snapshot','snapshot'],['prompt','prompt']])
  if(has(st))parts.push(label+' '+s(sum(st)));
 if(rounds.length){const ft=rounds.find(x=>x.first_token_ms!=null);
  parts.push(rounds.length+' model round'+(rounds.length>1?'s':'')+' '+s(sum('model'))+(ft?' (first token '+s(ft.first_token_ms)+')':''))}
 if(has('tool'))parts.push(t.filter(x=>x.stage==='tool').length+' tools '+s(sum('tool')));
 const total=t.find(x=>x.stage==='total');if(total)parts.push('total '+s(total.ms));
 return parts.join(' · ');
}
function fact(dl,label,value){if(value==null||value==='')return;const row=el('div');row.append(el('dt',null,label),el('dd',null,String(value)));dl.append(row)}
async function openTaskDetail(id){
 let d;try{d=await api('tasks/'+encodeURIComponent(id))}catch(err){notice(err.message);return}
 const alreadyOpen=currentTaskId===id&&!$('detail').hidden;
 currentTaskId=id;
 document.querySelectorAll('#task-rows tr').forEach(r=>r.classList.toggle('selected',r.dataset.id===String(id)));
 const summary=latestMessage(d.task_text);
 $('detail-title').textContent=d.display_title||(summary.length>110?summary.slice(0,108)+'…':(summary||'Task'));
 $('detail-meta').replaceChildren(statusPill(d.status),el('span',null,new Date(d.created_at).toLocaleString()));
 const facts=$('detail-facts');facts.replaceChildren();
 fact(facts,'Workspace',d.workspace==='work'?'Work':'Home');fact(facts,'Model',d.worker_model);
 fact(facts,'Tokens',d.input_tokens!=null?fmt(d.input_tokens)+' in · '+fmt(d.output_tokens)+' out':null);
 fact(facts,'Cost',isSubModel(d.worker_model,d.billing)?'Subscription (your plan)':d.openrouter_cost_usd!=null?cost(d.openrouter_cost_usd):null);  // HLA_SUBSCRIPTIONS_V1
 fact(facts,'TypeSafe risk',d.risk_probability!=null?Math.round(d.risk_probability*100)+'%':null);
 fact(facts,'Duration',d.finished_at?Math.max(0,Math.round((new Date(d.finished_at)-new Date(d.created_at))/1000))+' s':null);
 fact(facts,'Timings',timingsText(d.timings));
 if(facts.lastChild&&facts.lastChild.firstChild&&facts.lastChild.firstChild.textContent==='Timings')facts.lastChild.classList.add('fact-wide');  // HLA_UI_V4: a full line
 if(d.error_message&&d.status!=='completed')fact(facts,'Issue',d.error_message);
 $('detail-request').textContent=d.task_text||'';
 // Tool steps next to the task status: a completed task can still contain a failed tool call.
 const stepsBox=$('detail-steps');stepsBox.replaceChildren();if((d.progress||[]).length)stepsBox.append(stepsEl(d.progress,true));
 api('attachments?task_id='+encodeURIComponent(id)).then(f=>{if(currentTaskId!==id)return;const box=$('detail-files');box.replaceChildren();
  if(f.attachments.length)box.append(el('h3',null,'Files'),attachmentList(f.attachments))}).catch(()=>{});
 const box=$('hla-approval-actions');box.replaceChildren();box.hidden=d.status!=='human_approval_required';
 if(!box.hidden)box.append(approvalCard({task_id:d.id,status:d.status,risk:d.risk_probability},async()=>{await openTaskDetail(id);await loadTasks().catch(()=>{})}));
 const hist=$('detail-history');hist.replaceChildren();
 if((d.approval_events||[]).length){
  hist.append(el('h3',null,'Approval history'));const ol=el('ol','timeline');
  for(const e of d.approval_events){const li=el('li');li.append(el('strong',null,EVENT_LABELS[e.action]||e.action),
   el('span','muted',' · '+actorName(e.actor)+(e.scope==='read_only'?' · read-only':'')+(e.reason?' · '+e.reason:'')+' · '+new Date(e.created_at).toLocaleString()));ol.append(li)}
  hist.append(ol);
 }
 showTaskResult(d.result_text||(d.status==='running'?'Running…':d.status==='human_approval_required'?'Waiting for approval — nothing has run yet.':d.error_message||'No answer was saved for this task.'));
 $('hla-continue-task').hidden=d.status==='running';
 $('detail-stop').hidden=d.status!=='running';
 if(!alreadyOpen){lastFocus=document.activeElement;$('detail').hidden=false;document.body.classList.add('drawer-open');setDrawerInert(true);$('close-detail').focus()}
 clearTimeout(detailPoll);
 if(d.status==='running')detailPoll=setTimeout(()=>{if(currentTaskId===id&&!$('detail').hidden)openTaskDetail(id)},3000);
}
// The drawer is a modal dialog: everything behind it is inert (no focus, no clicks) while it is open.
function setDrawerInert(on){for(const node of $('workspace').children)if(node.id!=='detail')node.inert=on}
function closeTaskDetail(){
 $('detail').hidden=true;document.body.classList.remove('drawer-open');setDrawerInert(false);clearTimeout(detailPoll);currentTaskId=null;
 document.querySelectorAll('#task-rows tr.selected').forEach(r=>r.classList.remove('selected'));
 if(lastFocus&&document.contains(lastFocus))lastFocus.focus();
}
$('close-detail').addEventListener('click',closeTaskDetail);
$('detail-stop').addEventListener('click',async()=>{
 const id=currentTaskId;if(!id)return;const btn=$('detail-stop');btn.disabled=true;
 try{await api('tasks/'+encodeURIComponent(id)+'/cancel',{method:'POST',headers:{'x-agent-ui':'1'}});notice('Stopping the run.')}
 catch(err){notice(err.message)}finally{btn.disabled=false;setTimeout(()=>{if(currentTaskId===id)openTaskDetail(id)},800)}
});
$('detail-backdrop').addEventListener('click',closeTaskDetail);
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!$('detail').hidden)closeTaskDetail()});
$('hla-continue-task').addEventListener('click',async()=>{
 const id=currentTaskId;if(!id)return;const btn=$('hla-continue-task');btn.disabled=true;
 try{
  const data=await api('threads/from-task/'+encodeURIComponent(id),{method:'POST',headers:{'x-agent-ui':'1'}});
  closeTaskDetail();switchView('threads');
  await Promise.all([loadThreads(),loadThreadAgents()]);await openThread(data.id);
  notice(data.existing?'Opened the existing conversation for this task.':'Task imported. You can now ask a follow-up.');
  $('thread-input').focus();
 }catch(err){notice(err.message)}finally{btn.disabled=false}
});

// Conversations.
// The server searches all conversations (the list itself is the newest 100).
let threadsQuery='',showArchived=false;
async function loadThreads(){const f=$('thread-filter').value.trim(),params=new URLSearchParams();if(f)params.set('q',f);if(showArchived)params.set('archived','true');
 const data=await api('threads'+(params.toString()?'?'+params:''));
 if(f!==$('thread-filter').value.trim())return;  // an older search answered after a newer one started
 threadsCache=data.threads;threadsQuery=f;renderThreadList()}
let threadSearchTimer=null;
function renderThreadList(){
 const list=$('thread-list');list.replaceChildren();
 const raw=$('thread-filter').value.trim(),f=raw.toLowerCase();
 // The server also matches message text; filter locally only while its answer for this text is on the way.
 const rows=raw===threadsQuery?threadsCache:threadsCache.filter(t=>!f||t.title.toLowerCase().includes(f)||(t.agent_name||'').toLowerCase().includes(f));
 for(const t of rows){
  const b=el('button','thread-item');b.type='button';
  if(t.id===currentThread){b.classList.add('active');b.setAttribute('aria-current','true')}
  const top=el('div','thread-item-top');top.append(el('span','thread-item-title',t.title));
  if(t.pending_approvals)top.append(el('span','badge badge-warn',t.pending_approvals+' to approve'));
  else if(t.status==='running'){const w=el('span','badge badge-run','working');w.prepend(el('i','pulse'));top.append(w)}
  if(t.active_schedules){const sc=el('span','badge badge-sched');sc.append(ico('clock'));sc.title=t.active_schedules+' active schedule'+(t.active_schedules>1?'s':'');top.append(sc)}
  b.append(top,el('div','thread-item-meta',(t.agent_name||'General worker')+(t.workspace==='work'?' · Work':'')+' · '+when(t.updated_at)));
  b.onclick=()=>openThread(t.id).catch(e=>notice(e.message));
  list.append(b);
 }
 if(!rows.length)list.append(el('p','muted empty-list',raw?'No conversations match “'+raw+'”.':showArchived?'No archived conversations.':'No conversations yet. Start one with + New.'));
}
$('thread-filter').addEventListener('keydown',e=>{if(e.key==='Escape'&&e.target.value){e.preventDefault();e.target.value='';e.target.dispatchEvent(new Event('input'))}});
$('thread-filter').addEventListener('input',()=>{renderThreadList();clearTimeout(threadSearchTimer);threadSearchTimer=setTimeout(()=>loadThreads().catch(e=>notice(e.message)),300)});

function copyButton(text){
 const b=el('button','ghost small copy-btn','Copy');b.type='button';b.setAttribute('aria-label','Copy reply');
 b.onclick=async()=>{
  try{await navigator.clipboard.writeText(text)}
  catch{const t=el('textarea');t.value=text;document.body.append(t);t.select();document.execCommand('copy');t.remove()}
  b.textContent='Copied';setTimeout(()=>{b.textContent='Copy'},1500);
 };
 return b;
}

// HLA_ATTACHMENTS_V1: photos, files and documents. Files are uploaded as soon as they are picked, pasted or dropped
// (staged), and sent by id with the message, task or project. Images show as thumbnails, other files as chips.
let ATT_LIMITS={max_bytes:20*1024*1024,max_per_message:10};
const ATT_ICONS={image:'image',text:'file',document:'file',binary:'file'};
function fileSize(n){n=Number(n||0);return n>=1048576?(n/1048576).toFixed(1)+' MB':n>=1024?Math.round(n/1024)+' KB':n+' B'}
async function loadAttachmentLimits(){try{const d=await api('attachments/limits');ATT_LIMITS={max_bytes:d.max_bytes,max_per_message:d.max_per_message}}catch(e){}}
async function uploadAttachment(file,params){
 const qs=new URLSearchParams({name:file.name||'file',...params});let r;
 try{r=await fetch('/ui/api/attachments?'+qs,{method:'POST',credentials:'same-origin',
  headers:{'x-agent-ui':'1','Content-Type':file.type||'application/octet-stream'},body:file})}
 catch(e){throw new Error(file.name+': upload failed, the agent platform cannot be reached.')}
 const data=await r.json().catch(()=>({}));
 if(r.status===401&&!$('workspace').hidden)sessionExpired();
 if(!r.ok)throw new Error(file.name+': '+errorText(r.status,data));
 return data;
}
function attachmentChip(a,opts={}){
 const wrap=el('span','att-item'+(a.inline?' att-image':'')+(a.origin==='agent'?' att-agent':''));
 if(a.inline){
  const link=el('a','att-thumb');link.href=a.url||'#';link.target='_blank';link.rel='noopener';link.title=a.name+' · '+fileSize(a.size);
  const img=el('img');img.src=opts.src||a.url;img.alt=a.name;img.loading='lazy';link.append(img);wrap.append(link);
 }else{
  const link=el('a','att-chip');link.href=a.url?a.url+'?download=1':'#';if(a.url)link.download=a.name;link.title='Download '+a.name;
  const ai=el('span','att-icon');ai.append(ico(ATT_ICONS[a.kind]||'file'));link.append(ai,el('span','att-name',a.name),el('span','att-size',fileSize(a.size)));wrap.append(link);
  if(a.previewable&&!opts.onRemove){const pv=el('button','att-preview','Preview');pv.type='button';pv.setAttribute('aria-label','Preview '+a.name);pv.onclick=()=>previewAttachment(a);wrap.append(pv)}
 }
 if(opts.onRemove){const x=el('button','att-remove','×');x.type='button';x.title='Remove';x.setAttribute('aria-label','Remove '+a.name);x.onclick=opts.onRemove;wrap.append(x)}
 return wrap;
}
function attachmentList(list,label){
 const box=el('div','att-list');if(label)box.append(el('span','att-label',label));
 for(const a of list)box.append(attachmentChip(a));return box;
}
async function previewAttachment(a){
 const body=el('div','att-preview-body');
 if(a.inline){const img=el('img');img.src=a.url;img.alt=a.name;body.append(img)}
 else{const pre=el('pre','att-preview-text','Loading…');body.append(pre);
  api('attachments/'+a.id+'/text?length=60000').then(d=>{pre.textContent=(d.text||'(no text)')+(d.eof?'':'\n\n… the preview shows the first '
   +d.text.length.toLocaleString()+' of '+d.total_chars.toLocaleString()+' characters; download the file for all of it.')}).catch(e=>{pre.textContent=e.message})}
 const dl=el('a','att-download','⬇ Download '+a.name);dl.href=a.url+'?download=1';dl.download=a.name;body.append(dl);
 await dlgShow({title:a.name,content:body,ok:'Close',noCancel:true});
}
// A tray of files picked for the next message / task / project: uploaded at once (staged), removable before sending.
function fileTray(box,o={}){
 const items=[];let note=null;
 const t={items,
  add(files){
   for(const f of files){
    if(items.filter(i=>i.status!=='error').length>=ATT_LIMITS.max_per_message){notice('At most '+ATT_LIMITS.max_per_message+' files at a time.');break}
    if(f.size>ATT_LIMITS.max_bytes){notice(f.name+' is larger than '+fileSize(ATT_LIMITS.max_bytes)+'.');continue}
    const it={file:f,status:'uploading',preview:/^image\/(png|jpeg|gif|webp)$/.test(f.type)?URL.createObjectURL(f):null};items.push(it);
    it.promise=uploadAttachment(f,{...(o.params?o.params():{}),staged:'true'})
     .then(a=>{it.att=a;it.status='ready'}).catch(e=>{it.status='error';it.error=e.message;notice(e.message)}).finally(render);
   }
   render();
  },
  remove(it){const i=items.indexOf(it);if(i<0)return;items.splice(i,1);if(it.preview)URL.revokeObjectURL(it.preview);
   if(it.att)api('attachments/'+it.att.id,{method:'DELETE',headers:{'x-agent-ui':'1'}}).catch(()=>{});render()},
  async ids(){await Promise.all(items.map(i=>i.promise));
   if(items.some(i=>i.status==='error'))throw new Error('A file could not be uploaded: remove it (×) or attach it again.');
   return items.map(i=>i.att.id)},
  clear(drop){for(const it of items.splice(0)){if(it.preview)URL.revokeObjectURL(it.preview);
   if(drop&&it.att)api('attachments/'+it.att.id,{method:'DELETE',headers:{'x-agent-ui':'1'}}).catch(()=>{})}render()},
  has(){return items.length>0},
  hasImages(){return items.some(i=>i.preview||i.att?.kind==='image')},
  setNote(text){note=text||null;render()},
 };
 function render(){
  box.replaceChildren();box.hidden=!items.length;
  for(const it of items){
   const a=it.att||{name:it.file.name,size:it.file.size,kind:it.preview?'image':'text'};
   const chip=attachmentChip({...a,inline:!!it.preview||a.inline},{src:it.preview,onRemove:()=>t.remove(it)});
   if(it.status==='uploading'){chip.classList.add('att-uploading');chip.title='Uploading…'}
   if(it.status==='error'){chip.classList.add('att-error');chip.title=it.error}
   box.append(chip);
  }
  if(note&&items.length)box.append(el('p','att-note',note));
  if(o.onChange)o.onChange(t);
 }
 return t;
}
function pastedName(f,i){if(f.name&&f.name!=='image.png')return f;const ext=(f.type.split('/')[1]||'png').replace('jpeg','jpg');
 return new File([f],'pasted-'+new Date().toISOString().slice(0,19).replace(/[-:T]/g,'')+(i?'-'+i:'')+'.'+ext,{type:f.type})}
function filesPanel(box,list,o){
 box.replaceChildren();
 const head=el('div','files-head');head.append(el('strong',null,'Files'+(list.length?' ('+list.length+')':'')));
 if(o.hint)head.append(el('span','fineprint',o.hint));
 const up=el('button','ghost small','⬆ Upload…');up.type='button';const inp=el('input');inp.type='file';inp.multiple=true;inp.hidden=true;
 up.onclick=()=>inp.click();
 inp.onchange=()=>{const files=[...inp.files];inp.value='';whileBusy(up,async()=>{
  for(const f of files){try{await uploadAttachment(f,o.upload)}catch(e){notice(e.message)}}await o.reload()})};
 head.append(up,inp);box.append(head);
 if(!list.length){box.append(el('p','muted',o.empty||'No files yet.'));return}
 const ul=el('ul','files-list');
 for(const a of list){
  const li=el('li','files-row');li.append(attachmentChip(a),el('span','muted files-meta',(a.origin==='agent'?'Agent':'You')+' · '+fileSize(a.size)+' · '+when(a.created_at)));
  const del=el('button','ghost small','Delete');del.type='button';del.setAttribute('aria-label','Delete '+a.name);
  del.onclick=async()=>{if(!await uiConfirm('Delete '+a.name+'? Agents can no longer read it.',{title:'Delete file',ok:'Delete',danger:true}))return;
   await whileBusy(del,async()=>{try{await api('attachments/'+a.id,{method:'DELETE',headers:{'x-agent-ui':'1'}});await o.reload()}catch(e){notice(e.message)}})};
  li.append(del);ul.append(li);
 }
 box.append(ul);
}
function messageEl(msg,agentName){
 const mine=msg.role==='user',report=msg.kind==='command_result';
 const wrap=el('article','msg '+(mine?'msg-user':'msg-agent')+(report?' msg-command':''));
 const scheduled=msg.kind==='scheduled';
 const auto=msg.kind==='auto_continue';
 const head=el('div','msg-head');head.append(el('strong',null,mine&&report?'Command output':scheduled?'Scheduled':auto?'Continuing automatically':mine?'You':(agentName||'Agent')));
 if(auto)wrap.classList.add('msg-auto');
 if(msg.created_at){const t=el('time','muted',when(msg.created_at));t.title=new Date(msg.created_at).toLocaleString();head.append(t)}
 if(!mine&&msg.kind!=='approval_request'&&msg.content)head.append(copyButton(msg.content));
 const body=el('div',report&&mine?'msg-body':'msg-body rendered-result md-view');
 if(mine)body.textContent=report?msg.content:latestMessage(msg.content);
 else if(msg.kind==='approval_request')body.textContent=msg.task_status==='human_approval_required'
  ?'This request was paused by the risk review. Nothing has run yet — choose how to proceed below.'
  :'This request was paused by the risk review.';
 else if(typeof renderTaskMarkdown==='function')renderTaskMarkdown(body,msg.content);else body.textContent=msg.content;
 wrap.append(head,body);
 if((msg.attachments||[]).length)wrap.append(attachmentList(msg.attachments,mine?null:'Files'));
 if(!mine&&msg.kind!=='approval_request'&&(msg.progress||[]).length){const st=stepsEl(msg.progress,false);st.dataset.k='steps-'+msg.id;wrap.append(st)}
 const refresh=()=>Promise.all([currentThread&&openThread(currentThread),loadThreads()]).catch(e=>notice(e.message));
 if(msg.kind==='approval_request'&&msg.task_id)wrap.append(approvalCard({task_id:msg.task_id,status:msg.task_status,risk:msg.risk_probability,
  decided_by:msg.decided_by,decision_scope:msg.decision_scope},refresh));
 if(!mine&&msg.kind!=='approval_request'&&msg.pending_command&&msg.task_id)wrap.append(commandCard(msg.task_id,msg.pending_command,refresh));
 return wrap;
}
// HLA_RUNS_V1: tool steps, live replies over SSE, Stop and a single queued follow-up.
const TOOL_LABELS={server_diagnostic:'Diagnostic',server_execute_approved:'Shell command',server_maintenance:'Maintenance',
 knowledge_search:'Knowledge search',knowledge_save:'Save to knowledge',knowledge_save_diagnostic:'Save to knowledge',secrets:'Secrets',server_write_file:'Write file',server_read_file:'Read file',homeassistant:'Home Assistant',server_deploy:'Deploy',github:'GitHub',
 attachment_list:'List files',attachment_read:'Read file',attachment_create:'Create file',attachment_to_server:'Copy file to server',attachment_from_server:'Fetch file from server'};
// HLA_HTTP_TOOLS_V1: owner-defined HTTP tools arrive as http_<name>; the step target is the tool name.
function toolLabel(t){return TOOL_LABELS[t]||(/^http_/.test(t||'')?'HTTP tool':t)}
function stepIcon(busy,bad){const s=el('span','step-icon'+(busy?' step-spinner':'')+(bad?' bad':''));if(!busy)s.append(ico(bad?'x':'check'));return s}
function stepsEl(steps,open){
 const box=el('details','steps');box.open=open;
 const failed=steps.filter(st=>st.error||(st.exit_code!=null&&st.exit_code!==0)).length;
 const running=steps.filter(st=>st.status==='running').length;
 box.append(el('summary',null,steps.length+' tool call'+(steps.length===1?'':'s')+(running?' · 1 running':'')+(failed?' · '+failed+' failed':'')));
 const ol=el('ol');
 for(const st of steps){
  const busy=st.status==='running',bad=!busy&&(st.error||(st.exit_code!=null&&st.exit_code!==0));
  const li=el('li',busy?'step-running':bad?'step-bad':'step-ok');
  li.append(stepIcon(busy,bad),el('strong',null,toolLabel(st.tool)));
  li.append(stepIcon(busy,bad),el('strong',null,st.label||TOOL_LABELS[st.tool]||st.tool));
  if(st.target)li.append(el('code',null,st.target));
  li.append(el('span','muted',busy?'running…':[st.error||st.status,st.exit_code!=null?'exit '+st.exit_code:null,st.seconds!=null?st.seconds+' s':null].filter(Boolean).join(' · ')));
  ol.append(li);
 }
 box.append(ol);return box;
}
// HLA_LIVE_V1: what the agent is doing right now, with timers, while a reply is being produced.
function clock(ms){const s=Math.max(0,Math.floor(ms/1000));return Math.floor(s/60)+':'+String(s%60).padStart(2,'0')}
function liveActivity(){
 const run=live.steps.find(st=>st.status==='running');
 if(run){
  const since=live.stepSeen[run.n]??(live.stepSeen[run.n]=Date.now());
  const secs=Math.floor((Date.now()-since)/1000);
  return 'Running '+toolLabel(run.tool).toLowerCase()+(run.target?' · '+run.target:'')+(secs?' · '+secs+' s':'');
  return 'Running '+(run.label||(TOOL_LABELS[run.tool]||run.tool).toLowerCase())+(run.target?' · '+run.target:'')+(secs?' · '+secs+' s':'');
 }
 if(live.text)return 'Writing…';
 return live.steps.length?'Reviewing the results…':'Thinking…';
}
function liveEl(agentName){
 const wrap=el('article','msg msg-agent live');wrap.setAttribute('aria-busy','true');
 const head=el('div','msg-head');
 const label=el('span','live-label',liveActivity());label.setAttribute('aria-live','polite');
 head.append(el('strong',null,agentName||'Agent'),label,el('span','muted live-elapsed',clock(Date.now()-live.startedAt)));
 wrap.append(head);
 if(live.steps.length)wrap.append(stepsEl(live.steps,true));
 if(live.text){const body=el('div','msg-body rendered-result md-view streaming');renderTaskMarkdown(body,live.text);wrap.append(body)}
 else{const dots=el('div','typing');dots.setAttribute('aria-label','Agent is working');dots.append(el('span'),el('span'),el('span'));wrap.append(dots)}
 return wrap;
}
function commandLiveEl(agentName){
 const wrap=el('article','msg msg-agent live');
 const head=el('div','msg-head');head.append(el('strong',null,agentName||'Agent'),el('span','muted live-label','running the approved command…'));
 const dots=el('div','typing');dots.setAttribute('aria-label','Command running');dots.append(el('span'),el('span'),el('span'));
 wrap.append(head,dots);return wrap;
}
let liveRenderTimer=null,liveTicker=null;
function nearBottom(box){return box.scrollHeight-box.scrollTop-box.clientHeight<80}
function showJump(show){const b=$('thread-jump');if(!b)return;b.hidden=!show;if(show)b.style.bottom=($('thread-message-form').offsetHeight+14)+'px'}
function paintLive(){
 // Throttled: SSE can deliver many updates per second while tokens stream.
 if(liveRenderTimer)return;
 liveRenderTimer=setTimeout(()=>{
  liveRenderTimer=null;const box=$('thread-messages'),old=box.querySelector('.msg.live');if(!live||!old)return;
  const stick=nearBottom(box);
  old.replaceWith(liveEl(box.dataset.agent));
  // Follow the reply only while the reader is at the bottom; otherwise offer a jump instead of yanking the view.
  if(stick)box.scrollTop=box.scrollHeight;else showJump(true);
 },100);
}
const BASE_TITLE=document.title;
let titleState=null;
function setTitle(state){titleState=state;document.title=(state==='working'?'● Working · ':state==='ready'?'✓ Reply ready · ':'')+(inboxCount?'('+inboxCount+') ':'')+BASE_TITLE}
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&titleState==='ready')setTitle(null)});
function stopFollowing(){
 if(live&&live.source)live.source.close();live=null;clearTimeout(threadPoll);clearInterval(liveTicker);liveTicker=null;
 if(titleState==='working')setTitle(null);
}
function follow(threadId,taskId){
 if(live&&live.taskId===taskId)return;
 stopFollowing();
 live={taskId,text:'',steps:[],stepSeen:{},startedAt:Date.now(),source:new EventSource('/ui/api/tasks/'+encodeURIComponent(taskId)+'/events')};
 const mine=live;setTitle('working');
 // Timers tick without re-rendering the reply.
 liveTicker=setInterval(()=>{
  const node=document.querySelector('.msg.live');if(!node||live!==mine)return;
  const label=node.querySelector('.live-label'),elapsed=node.querySelector('.live-elapsed');
  if(label)label.textContent=liveActivity();if(elapsed)elapsed.textContent=clock(Date.now()-mine.startedAt);
 },1000);
 mine.source.addEventListener('content',e=>{mine.text=JSON.parse(e.data).text||'';paintLive()});
 mine.source.addEventListener('progress',e=>{mine.steps=JSON.parse(e.data).steps||[];paintLive()});
 const finish=()=>{mine.source.close();if(live===mine){live=null;clearInterval(liveTicker);liveTicker=null}
  setTitle(document.hidden?'ready':null);refreshInbox();
  // Not re-rendered while another page is shown; opening Conversations reloads it.
  if(currentThread===threadId&&!$('threads').hidden)Promise.all([openThread(threadId),loadThreads()]).catch(e=>notice(e.message))};
 mine.source.addEventListener('done',finish);
 mine.source.addEventListener('error',()=>{
  // The run continues server-side; fall back to polling the thread until it is idle again.
  if(mine.source.readyState!==EventSource.CLOSED)return;
  if(live===mine)live=null;
  threadPoll=setTimeout(()=>{if(currentThread===threadId)openThread(threadId).catch(()=>{})},3000);
 });
}
function setComposer(thread){
 const running=!!thread&&thread.status==='running',queued=!!thread&&!!thread.queued_message;
 $('thread-send').textContent=running?'Queue':'Send';
 $('thread-send').disabled=!thread||threadSending||queued;
 $('thread-send').title=running?'Runs automatically when the current reply finishes':'';
 $('thread-stop').hidden=!running;
 $('thread-state').textContent=queued?'One follow-up queued':running?'Agent working…':'';
}
function queuedEl(text){
 const wrap=el('article','msg msg-user queued');
 const head=el('div','msg-head');head.append(el('strong',null,'You'),el('span','muted','queued · sends when the current reply finishes'));
 const remove=el('button','ghost small','Remove');remove.type='button';
 remove.onclick=async()=>{remove.disabled=true;try{await api('threads/'+currentThread+'/queued',{method:'DELETE',headers:{'x-agent-ui':'1'}});await openThread(currentThread)}catch(e){notice(e.message)}};
 head.append(remove);
 wrap.append(head,el('div','msg-body',text));return wrap;
}
let currentThreadData=null;
// HLA_PERF_V1: a conversation shows its newest messages; "Show earlier" widens the window by 100.
let threadLimit=100,keepFromBottom=null;
async function openThread(id){
 const switching=currentThread!==id;
 if(switching)threadLimit=100;
 if(switching)stopFollowing();
 currentThread=id;
 $('chat-layout').classList.add('has-thread');
 $('schedule-toggle').hidden=false;$('thread-files-toggle').hidden=false;
 if(switching){$('schedule-panel').hidden=true;$('schedule-toggle').setAttribute('aria-expanded','false');
  $('thread-files-panel').hidden=true;$('thread-files-toggle').setAttribute('aria-expanded','false');threadTray.clear(true)}
 if(switching){renderThreadList();$('thread-messages').replaceChildren(emptyState('Loading…'))}
 const data=await api('threads/'+id+'?limit='+threadLimit);
 if(currentThread!==id)return;
 currentThreadData=data.thread;store.set('thread',id);
 if(switching)cancelRename();
 $('thread-menu-toggle').hidden=false;$('thread-archive').textContent=data.thread.archived_at?'Unarchive':'Archive';
 $('thread-archive').disabled=$('thread-delete').disabled=!!data.thread.project_id;
 $('thread-archive').title=$('thread-delete').title=data.thread.project_id?'Part of a project: it stays with the project':'';
 $('thread-project-back').hidden=!data.thread.project_id;$('thread-project-back').dataset.project=data.thread.project_id||'';
 if(!renaming)$('thread-heading').textContent=data.thread.title;
 $('thread-sub').textContent=(data.thread.agent_name||'General worker')+' · '+(data.thread.workspace==='work'?'Work':'Home')+' workspace'+(data.thread.archived_at?' · Archived':'');
 const box=$('thread-messages');box.dataset.agent=data.thread.agent_name||'';
 const atBottom=switching||box.scrollHeight-box.scrollTop-box.clientHeight<80,previousTop=box.scrollTop;
 const restore=switching?null:keepState(box);  // a re-render keeps a typed rejection reason and opened tool steps
 box.replaceChildren();
 if(data.has_more){const more=el('button','ghost small show-earlier','Show earlier messages');more.type='button';
  more.onclick=()=>{threadLimit+=100;keepFromBottom=box.scrollHeight-box.scrollTop;openThread(id).catch(e=>notice(e.message))};box.append(more)}
 if(!data.messages.length&&data.thread.status!=='running')box.append(emptyState('No messages yet. Ask the agent something below.'));
 for(const m of data.messages)box.append(messageEl(m,data.thread.agent_name));
 const runId=data.thread.status==='running'?data.thread.current_task_id:null;
 // An approved command runs under the finished reply's task, which has no live stream: poll until the check-up run starts.
 const commandRun=runId&&data.messages.some(m=>m.task_id===runId&&m.pending_command&&m.pending_command.status==='running');
 if(commandRun){stopFollowing();box.append(commandLiveEl(data.thread.agent_name));threadPoll=setTimeout(()=>{if(currentThread===id)openThread(id).catch(()=>{})},2500)}
 else if(runId){follow(id,runId);box.append(liveEl(data.thread.agent_name))}else stopFollowing();
 if(data.thread.queued_message)box.append(queuedEl(data.thread.queued_message));
 if(restore)restore();
 const fromBottom=keepFromBottom;keepFromBottom=null;
 // A waiting approval matters more than the newest line: show the whole card when opening a conversation.
 const pendingCard=switching?[...box.querySelectorAll('.approval-card.approval-human_approval_required')].pop():null;
 const place=()=>{
  if(fromBottom!=null)box.scrollTop=box.scrollHeight-fromBottom;
  else if(pendingCard&&pendingCard.closest('.msg').offsetHeight>box.clientHeight)box.scrollTop=pendingCard.offsetTop-12;
  else box.scrollTop=atBottom?box.scrollHeight:previousTop};
 place();
 // Off-screen messages are laid out lazily (content-visibility) at an estimated height, so positions move once
 // they render: place the view again for a few frames.
 if(atBottom||fromBottom!=null){let n=0;const settle=()=>{if(currentThread!==id||n++>6)return;place();requestAnimationFrame(settle)};requestAnimationFrame(settle)}
 // A reply that finished (or a new message) below the reader's position: offer the jump instead of moving them.
 if(!switching&&!atBottom&&!nearBottom(box))showJump(true);else if(switching)showJump(false);
 setComposer(data.thread);
}
async function sendThreadMessage(){
 if(currentThreadData&&currentThreadData.queued_message&&!threadSending)
  return notice('One follow-up is already queued. Remove it or wait for the current reply before sending another.');
 if(threadSending||!currentThread||$('thread-send').disabled)return;
 const input=$('thread-input'),typed=input.value.trim(),withFiles=threadTray.has();if(!typed&&!withFiles)return;
 const id=currentThread;threadSending=true;$('thread-send').disabled=true;
 let ids=[];
 try{ids=await threadTray.ids()}catch(err){notice(err.message);threadSending=false;$('thread-send').disabled=false;return}
 // HLA_ATTACHMENTS_V1: files alone are a message too.
 const content=typed||('Please look at the attached file'+(ids.length>1?'s':'')+'.');
 input.value='';growComposer();let sent=false;
 try{
  await api('threads/'+id+'/messages',{method:'POST',headers:UI_HEADERS,
   body:JSON.stringify({content,attachment_ids:ids,max_output_tokens:Number($('thread-output-tokens').value)||null,model_override:$('thread-model-override').value||null})});
  sent=true;if(currentThread===id)threadTray.clear(false);
 }catch(err){notice(err.message);if(!input.value)input.value=typed}
 finally{threadSending=false;if(currentThread===id)await openThread(id).catch(e=>notice(e.message));loadThreads().catch(()=>{});
  if(sent&&!$('thread-files-panel').hidden)loadThreadFiles().catch(()=>{})}
}
// The message box takes files from the attach button, the clipboard (screenshots) and drag and drop onto the conversation.
const threadTray=fileTray($('thread-attachments'),{params:()=>currentThread?{thread_id:currentThread}:{},onChange:()=>visionCheck()});
let visionSeq=0;
async function visionCheck(){
 const seq=++visionSeq;
 if(!threadTray.hasImages()||!currentThread){if(threadTray.lastNote){threadTray.lastNote=null;threadTray.setNote(null)}return}
 try{const d=await api('attachments/limits?'+new URLSearchParams({thread_id:currentThread,model:$('thread-model-override').value||''}));
  if(seq!==visionSeq)return;
  const text=d.vision===false?''+(d.model||'This model')+' cannot see images: the agent only gets their names. Pick a model that accepts images under “Model”.':null;
  if(text!==threadTray.lastNote){threadTray.lastNote=text;threadTray.setNote(text)}}catch(e){}
}
$('thread-model-override').addEventListener('change',()=>visionCheck());
$('thread-attach').addEventListener('click',()=>{if(!currentThread)return notice('Open a conversation first.');$('thread-file').click()});
$('thread-file').addEventListener('change',e=>{threadTray.add([...e.target.files]);e.target.value='';$('thread-input').focus()});
$('thread-input').addEventListener('paste',e=>{const files=[...(e.clipboardData?.files||[])];if(!files.length||!currentThread)return;
 e.preventDefault();threadTray.add(files.map(pastedName))});
{const zone=document.querySelector('.chat-main');let depth=0;
 const isFiles=e=>[...(e.dataTransfer?.types||[])].includes('Files');
 zone.addEventListener('dragenter',e=>{if(!isFiles(e)||!currentThread)return;e.preventDefault();depth++;zone.classList.add('drop-target')});
 zone.addEventListener('dragover',e=>{if(!isFiles(e)||!currentThread)return;e.preventDefault();e.dataTransfer.dropEffect='copy'});
 zone.addEventListener('dragleave',e=>{if(!isFiles(e))return;depth=Math.max(0,depth-1);if(!depth)zone.classList.remove('drop-target')});
 zone.addEventListener('drop',e=>{if(!isFiles(e)||!currentThread)return;e.preventDefault();depth=0;zone.classList.remove('drop-target');
  threadTray.add([...e.dataTransfer.files]);$('thread-input').focus()});}
async function loadThreadFiles(){
 const id=currentThread;if(!id)return;const d=await api('attachments?thread_id='+id);if(currentThread!==id)return;
 filesPanel($('thread-files-panel'),d.attachments,{upload:{thread_id:id},reload:loadThreadFiles,
  hint:'Everything sent here and every file the agent made. Uploads here are reference files the agent can read.',
  empty:'No files in this conversation yet. Attach them to a message (attach, paste or drop), or upload reference files here.'});
}
$('thread-files-toggle').addEventListener('click',()=>{const p=$('thread-files-panel'),open=p.hidden;p.hidden=!open;
 $('thread-files-toggle').setAttribute('aria-expanded',String(open));if(open)loadThreadFiles().catch(e=>notice(e.message))});
$('thread-stop').addEventListener('click',async()=>{
 if(!currentThread)return;const btn=$('thread-stop');btn.disabled=true;
 const queued=currentThreadData&&currentThreadData.queued_message;
 try{await api('threads/'+currentThread+'/cancel',{method:'POST',headers:{'x-agent-ui':'1'}});
  // Stop also drops the queued follow-up; give its text back instead of losing it.
  if(queued&&!$('thread-input').value.trim()){$('thread-input').value=queued;growComposer()}
  notice(queued?'Stopping. Your queued follow-up was not sent; it is back in the message box.'
   :'Stopping. A server command that already started finishes on the server.')}
 catch(err){notice(err.message);await openThread(currentThread).catch(()=>{})}finally{btn.disabled=false}
});
$('thread-message-form').addEventListener('submit',e=>{e.preventDefault();sendThreadMessage()});
$('thread-input').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();sendThreadMessage()}});

function toggleNewThread(show){$('thread-create').hidden=!show;$('thread-new-toggle').setAttribute('aria-expanded',String(show));if(show)$('thread-title').focus()}
$('thread-new-toggle').addEventListener('click',()=>toggleNewThread($('thread-create').hidden));
$('thread-new-cancel').addEventListener('click',()=>toggleNewThread(false));
$('thread-create').addEventListener('keydown',e=>{if(e.key==='Escape'){e.preventDefault();toggleNewThread(false);$('thread-new-toggle').focus()}});
$('thread-create').addEventListener('submit',async e=>{
 e.preventDefault();const btn=e.submitter;if(btn)btn.disabled=true;
 try{
  const data=await api('threads',{method:'POST',headers:UI_HEADERS,
   body:JSON.stringify({title:$('thread-title').value,workspace:$('thread-workspace').value,agent_id:$('thread-agent').value||null})});
  $('thread-title').value='';toggleNewThread(false);await loadThreads();await openThread(data.id);$('thread-input').focus();
 }catch(err){notice(err.message)}finally{if(btn)btn.disabled=false}
});
$('thread-back').addEventListener('click',()=>$('chat-layout').classList.remove('has-thread'));
async function loadThreadAgents(){const d=await api('agents');threadAgents=d.agents.filter(a=>a.enabled);fillThreadAgents()}
function fillThreadAgents(){
 const sel=$('thread-agent'),ws=$('thread-workspace').value,old=sel.value;sel.replaceChildren(new Option('General worker',''));
 for(const a of threadAgents.filter(a=>a.workspace===ws))sel.add(new Option(a.name,a.id));
 if([...sel.options].some(o=>o.value===old))sel.value=old;
}
$('thread-workspace').addEventListener('change',fillThreadAgents);
document.querySelector('button[data-view="threads"]').addEventListener('click',async()=>{
 try{
  await Promise.all([loadThreads(),loadThreadAgents()]);
  // On wide screens open the most recent conversation instead of an empty pane.
  const target=currentThread||(window.matchMedia('(min-width: 901px)').matches&&threadsCache[0]?.id);
  if(target)await openThread(target);else setComposer(false,'');
 }catch(e){notice(e.message)}
});

// HLA_POLICY_V1: per-agent and per-server levels, and unhinged mode. The SSH gateway enforces them.
// What each level means for a service (servers use the shared legend).
// HLA_SECRET_STORE_V1: where the gateway keeps secrets. Neutral wording unless the gateway says Infisical is the backend
// (policy and connections carry secret_store {backend, configured}).
let SECRET_BACKEND=null;
function noteSecretBackend(store){if(store&&store.backend){SECRET_BACKEND=store.backend;document.querySelectorAll('.secret-store-name').forEach(n=>n.textContent=secretStoreName())}}
function secretStoreName(){return SECRET_BACKEND==='infisical'?'Infisical':'the secret store'}
function generatedStoreName(){return SECRET_BACKEND==='infisical'?'the Agent Generated project in Infisical':'the generated secrets of the gateway\'s secret store'}
const SERVICE_LEVELS={homeassistant:'1 read states and dashboards · 4 call services, save dashboards (backed up first) · 6 anything',
 get secrets(){return 'Generated secrets ('+generatedStoreName()+'). 1 list names and use {{secret:NAME}} in commands and files · 4 create new passwords · 6 replace existing ones. Agents never see the values.'},
 github:'Pull requests on the allowed repositories below. 1 read PR status · 4 open PRs and comment · 6 merge (or 4 where merges are allowed). The token stays in the gateway.',
 mcp:'MCP server (MCP page). Tools need 4 by default, 6 when marked destructive; new tools wait for your review; per-tool levels on the MCP page. Results are untrusted data.'};
let policyData=null;
async function loadPolicy(){
 const keep=policyDirty();
 policyData=await api('policy');
 noteSecretBackend(policyData?.policy?.secret_store);
 if(!keep)renderPolicy();  // re-rendering would silently discard unsaved edits
 renderUnhinged();renderDeployApps();markPolicyDirty();loadSandbox().catch(()=>{});loadDeployments().catch(e=>notice(e.message));
}
function unhingedText(u){return u.until?'until '+new Date(u.until*1000).toLocaleString():'until you turn it off'}
function renderUnhinged(){
 const u=policyData?.policy?.unhinged||{active:false};
 $('unhinged-banner').hidden=!u.active;document.body.classList.toggle('unhinged',!!u.active);
 if(u.active)$('unhinged-banner-text').textContent='Level-5 agents run any command without approval, '+unhingedText(u)+'.';
 const pill=$('unhinged-state');pill.textContent=u.active?'On · '+unhingedText(u):'Off';pill.className='pill '+(u.active?'st-human_approval_required':'st-rejected');
 $('unhinged-toggle').textContent=u.active?'Turn off':'Turn on';$('unhinged-toggle').className=u.active?'secondary':'danger';
 $('unhinged-hours').closest('label').hidden=u.active;
 const canEdit=policyData.editable&&policyData.approver.allowed;
 $('unhinged-toggle').disabled=!canEdit;$('unhinged-banner-off').disabled=!canEdit;
}
function levelSelect(value,attr,key){
 const sel=document.createElement('select');sel.dataset[attr]=key;
 for(const [n,label] of Object.entries(policyData.policy.levels))sel.add(new Option(n+' · '+label,n));
 sel.value=String(value);return sel;
}
function renderPolicy(){
 const p=policyData.policy,canEdit=policyData.editable&&policyData.approver.allowed;
 const notes=[];
 if(!policyData.ssh_tools_enabled)notes.push('SSH tools are switched off (SSH_TOOL_ENABLED=false), so no agent gets server tools.');
 if(!policyData.editable)notes.push('Read-only: set POLICY_ADMIN_TOKEN in .env and ../ssh-gateway/.env.gateway to edit policy here.');
 else if(!policyData.approver.allowed)notes.push(policyData.approver.reason);
 $('policy-note').hidden=!notes.length;$('policy-note').textContent=notes.join(' ');
 const legend=$('level-legend');legend.replaceChildren();
 for(const [n,label] of Object.entries(p.levels)){const li=el('li');li.append(el('strong',null,n),document.createTextNode(' '+label));legend.append(li)}
 const servers=$('policy-servers');servers.replaceChildren();
 for(const [name,s] of Object.entries(p.servers)){
  const tr=el('tr');const sel=levelSelect(s.level,'server',name);sel.disabled=!canEdit;
  const projects=el('input');projects.dataset.projects=name;projects.value=(s.compose_projects||[]).join(', ');
  projects.placeholder='/srv/app, /opt/stack';projects.disabled=!canEdit;
  const td1=el('td');td1.append(sel);const td2=el('td');td2.append(projects);
  tr.append(el('td','policy-name',name),td1,td2);servers.append(tr);
 }
 // HLA_HA_V1: services (Home Assistant) have their own level meanings and no compose projects.
 for(const [name,s] of Object.entries(p.services||{})){
  const tr=el('tr');const sel=levelSelect(s.level,'server',name);sel.disabled=!canEdit;
  const td1=el('td');td1.append(sel);
  tr.append(el('td','policy-name',name),td1,el('td','req-sub',SERVICE_LEVELS[name]||(name.startsWith('mcp:')?SERVICE_LEVELS.mcp:'')));
  servers.append(tr);
 }
 const agents=$('policy-agents');agents.replaceChildren();
 const known=new Set();
 for(const a of policyData.agents){
  known.add(a.id);const tr=el('tr');const sel=levelSelect(p.agents[a.id]?.level??0,'agent',a.id);sel.disabled=!canEdit;sel.dataset.name=a.name;
  const name=el('td','policy-name');name.append(el('div',null,a.name),el('div','req-sub',a.workspace+(a.enabled?'':' · disabled')));
  const td=el('td');td.append(sel);tr.append(name,td);agents.append(tr);
 }
 for(const [id,a] of Object.entries(p.agents))if(!known.has(id)){
  const tr=el('tr');const sel=levelSelect(a.level,'agent',id);sel.disabled=!canEdit;sel.dataset.name=a.name;
  const name=el('td','policy-name');name.append(el('div',null,a.name||'Deleted agent'),el('div','req-sub','not in the agent list'));
  const td=el('td');td.append(sel);tr.append(name,td);agents.append(tr);
 }
 $('policy-save').disabled=!canEdit;$('policy-reset').disabled=!canEdit;
 renderGithubRepos();
}
function policyChange(){
 const p=policyData.policy,change={servers:{},agents:{}};
 document.querySelectorAll('#policy-servers select').forEach(sel=>{const s=sel.dataset.server;if(Number(sel.value)!==(p.servers[s]||p.services[s]).level)(change.servers[s]??={}).level=Number(sel.value)});
 document.querySelectorAll('#policy-servers input').forEach(inp=>{
  const s=inp.dataset.projects,list=inp.value.split(',').map(x=>x.trim()).filter(Boolean);
  if(JSON.stringify(list)!==JSON.stringify(p.servers[s].compose_projects||[]))(change.servers[s]??={}).compose_projects=list;
 });
 document.querySelectorAll('#policy-agents select').forEach(sel=>{const id=sel.dataset.agent,level=Number(sel.value);
  if(level!==(p.agents[id]?.level??0))change.agents[id]={name:sel.dataset.name||'',level}});
 return change;
}
async function putPolicy(change){
 const result=await api('policy',{method:'PUT',headers:UI_HEADERS,body:JSON.stringify({change})});
 policyData.policy=result;renderPolicy();renderUnhinged();renderDeployApps();markPolicyDirty();
}
$('policy-save').addEventListener('click',async()=>{
 const change=policyChange(),count=Object.keys(change.servers).length+Object.keys(change.agents).length;
 if(!count)return notice('No changes to save.');
 const raised=Object.values(change.agents).some(a=>a.level>=5)||Object.values(change.servers).some(s=>s.level>=5);
 if(!await uiConfirm('Apply '+count+' permission change'+(count>1?'s':'')+'?'+(raised?' Level 5 allows shell commands (with your approval, or without it in unhinged mode).':''),
  {title:'Save permissions',ok:'Apply '+(count>1?count+' changes':'change')}))return;
 const btn=$('policy-save');btn.disabled=true;
 try{await putPolicy(change);notice('Permissions saved.')}catch(err){notice(err.message)}finally{btn.disabled=false}
});
$('policy-reset').addEventListener('click',()=>{renderPolicy();markPolicyDirty()});
// Unsaved permission edits are kept when the page reloads policy data (navigation, Refresh).
function policyDirty(){if(!policyData||!$('policy-servers').children.length)return false;const c=policyChange();return Object.keys(c.servers).length+Object.keys(c.agents).length>0}
function markPolicyDirty(){const dirty=policyDirty();$('policy-save').textContent=dirty?'Save changes •':'Save changes';$('policy-reset').disabled=!dirty;
 $('policy-save').classList.toggle('dirty',dirty)}
$('policy-servers').addEventListener('change',markPolicyDirty);$('policy-servers').addEventListener('input',markPolicyDirty);
$('policy-agents').addEventListener('change',markPolicyDirty);
async function setUnhinged(on){
 if(on&&!await uiConfirm('Turn on unhinged mode? Level-5 agents will run ANY command, including sudo, on every server above level 0 without asking you. A prompt injection in a log or web page could then act on your servers.',
  {title:'Turn on unhinged mode',ok:'Turn on unhinged mode',danger:true}))return;
 const hours=$('unhinged-hours').value;
 try{await putPolicy({unhinged:on?{enabled:true,hours:hours?Number(hours):null}:{enabled:false}});notice(on?'Unhinged mode is on.':'Unhinged mode is off. Commands need approval again.')}
 catch(err){notice(err.message)}
}
$('unhinged-toggle').addEventListener('click',()=>setUnhinged(!policyData?.policy?.unhinged?.active));
$('unhinged-banner-off').addEventListener('click',()=>setUnhinged(false));
document.querySelector('button[data-view="policy"]').addEventListener('click',()=>loadPolicy().catch(e=>notice(e.message)));

// HLA_SANDBOX_V1: roll the sandbox VM back to its clean snapshot.
async function loadSandbox(){
 const s=await api('sandbox'),canEdit=policyData?.approver?.allowed!==false;
 $('sandbox-desc').textContent=`${s.name||'sandbox'} · ${s.host||'?'} · VM ${s.vmid||'?'} on ${s.node||'?'}. Agents at level 6 get a free shell there; Reset rolls it back to the "${s.snapshot}" snapshot.`;
 const last=s.last_reset;
 $('sandbox-status').textContent=!s.configured?'Reset not available yet: missing '+s.missing.join(', ')+'.':
  s.resetting?'Resetting…':last?`Last reset ${new Date(last.started_at).toLocaleString()} by ${last.actor}: ${last.outcome==='ok'?'done':last.outcome||'in progress'}.`:'Not reset yet.';
 $('sandbox-reset').disabled=!s.configured||s.resetting||!canEdit;
}
$('sandbox-reset').addEventListener('click',async()=>{
 if(!await uiConfirm('Reset the sandbox? Everything installed or changed on it since the clean snapshot is lost, and running agent commands there fail.',
  {title:'Reset sandbox',ok:'Reset sandbox',danger:true}))return;
 const btn=$('sandbox-reset');btn.disabled=true;$('sandbox-status').textContent='Resetting… this takes up to a couple of minutes.';
 try{await api('sandbox/reset',{method:'POST',headers:{'x-agent-ui':'1'}});notice('Sandbox reset to its clean snapshot.')}
 catch(err){notice(err.message)}finally{loadSandbox().catch(()=>{})}
});

// HLA_DEPLOY_V1: registered apps, deploy/rollback, and the gateway's deployment history.
const DEPLOY_STATUS={running:['Running','running'],deployed:['Deployed','completed'],rolled_back:['Rolled back','incomplete'],
 rollback_failed:['Rollback failed','failed'],staging_failed:['Failed in staging','failed'],failed:['Failed · nothing changed','failed'],
 error:['Error','failed'],interrupted:['Interrupted','rejected']};
let deployPoll=null;
const shortSha=s=>s?s.slice(0,7):'—';
function agentName(id){const a=(policyData?.agents||[]).find(x=>x.id===id);return a?a.name:'agent'}
function renderDeployApps(){
 if(!policyData)return;
 const box=$('deploy-apps'),apps=Object.values(policyData.policy.apps||{}),canEdit=policyData.editable&&policyData.approver.allowed;
 box.replaceChildren();$('deploy-register').hidden=!canEdit;
 if(!apps.length)box.append(el('p','muted','No apps registered yet.'));
 for(const a of apps){
  const row=el('div','deploy-app'),info=el('div','deploy-info');
  info.append(el('strong',null,a.name),el('code',null,a.server+':'+a.path),
   el('div','muted',a.repo+(a.health_url?' · health '+a.health_url:'')+(a.staging?' · tested on the sandbox first':' · no staging')));
  row.append(info);
  if(canEdit){
   const actions=el('div','deploy-actions');
   const go=el('button',null,deploying.has(a.name)?'Deploying…':'Deploy…');go.type='button';go.disabled=deploying.has(a.name);go.onclick=()=>startDeploy(a);
   const rm=el('button','ghost','Remove');rm.type='button';
   rm.onclick=async()=>{if(!await uiConfirm('Remove '+a.name+' from deployments? Nothing on the server changes.',{title:'Remove app',ok:'Remove app',danger:true}))return;
    try{await putPolicy({apps:{[a.name]:null}});notice(a.name+' removed.')}catch(e){notice(e.message)}};
   actions.append(go,rm);row.append(actions);
  }
  box.append(row);
 }
 const sel=$('dep-server');
 if(!sel.options.length)for(const s of Object.keys(policyData.policy.servers))if(s!==(policyData.policy.roles?.sandbox??'sandbox'))sel.add(new Option(s,s));
}
const deploying=new Set();  // apps with a deployment in flight (from this page or the gateway history)
async function startDeploy(a,sha){
 if(deploying.has(a.name))return notice('A deployment of '+a.name+' is already running.');
 let ref=null;
 if(sha){if(!await uiConfirm('Roll '+a.name+' back to '+shortSha(sha)+'? Staging is skipped: this commit ran before.',{title:'Roll back '+a.name,ok:'Roll back'}))return}
 else{ref=await uiPrompt('Deploy '+a.name,{label:'Branch, tag or commit',value:'main',required:true,maxLength:200,ok:'Deploy',
   hint:a.staging?'It is tested on the sandbox first.':'No staging: it goes straight to '+a.server+'.'});if(!ref||!ref.trim())return}
 if(deploying.has(a.name))return notice('A deployment of '+a.name+' is already running.');  // started while the dialog was open
 deploying.add(a.name);(startDeploy.pending??=new Set()).add(a.name);renderDeployApps();
 notice((sha?'Starting rollback of ':'Starting deploy of ')+a.name+'…');
 try{
  const r=await api('deployments',{method:'POST',headers:UI_HEADERS,body:JSON.stringify(sha?{app:a.name,sha}:{app:a.name,ref:ref.trim()})});
  notice(r.status==='started'?(sha?'Rollback started.':'Deploy started: sandbox first, then '+a.server+'. This can take several minutes.')
   :'Deploy finished: '+(DEPLOY_STATUS[r.status]||[r.status])[0]+'.');
 }catch(e){notice(e.message);deploying.delete(a.name);renderDeployApps()}
 finally{startDeploy.pending.delete(a.name)}
 loadDeployments().catch(()=>{});
}
async function loadDeployments(){
 clearTimeout(deployPoll);
 const data=await api('deployments'),body=$('deploy-history');body.replaceChildren();
 const canEdit=policyData?.editable&&policyData?.approver?.allowed,apps=policyData?.policy?.apps||{};
 if(!data.deployments.length){const tr=el('tr'),td=el('td','muted','No deployments yet.');td.colSpan=6;tr.append(td);body.append(tr)}
 // Rollback is offered on the newest successful deployment of each app: back to the commit it replaced.
 const latest={};for(const d of data.deployments)if(d.status==='deployed'&&!(d.app in latest))latest[d.app]=d.id;
 for(const d of data.deployments){
  const [label,tone]=DEPLOY_STATUS[d.status]||[d.status,'unknown'];
  const st=el('td');st.append(el('span','pill st-'+tone,label));
  const why=d.result&&d.result.error_class;if(why)st.append(el('div','muted',why.replace(/_/g,' ')));
  const ref=el('td');ref.append(el('code',null,d.ref&&d.ref!==d.sha&&!/^[0-9a-f]{40}$/.test(d.ref)?d.ref:shortSha(d.sha||d.ref)));
  if(d.sha)ref.append(el('div','muted',(d.from_sha?shortSha(d.from_sha)+' → ':'')+shortSha(d.sha)));
  const act=el('td');
  if(canEdit&&latest[d.app]===d.id&&d.from_sha&&apps[d.app]){
   const b=el('button','secondary small','Roll back to '+shortSha(d.from_sha));b.type='button';b.onclick=()=>startDeploy(apps[d.app],d.from_sha);act.append(b)}
  const by=d.requested_by&&d.requested_by.startsWith('agent:')?agentName(d.requested_by.slice(6)):d.requested_by;
  const tr=el('tr');tr.append(el('td',null,when(new Date(d.started*1000).toISOString())),el('td',null,d.app),ref,st,el('td',null,by||'—'),act);
  body.append(tr);
 }
 const running=new Set(data.deployments.filter(d=>d.status==='running').map(d=>d.app));
 // In flight here but not yet in the history: keep it until the gateway lists it (or the request failed).
 const changed=[...deploying].some(n=>!running.has(n))||[...running].some(n=>!deploying.has(n));
 for(const n of [...deploying])if(!running.has(n)&&!startDeploy.pending?.has(n))deploying.delete(n);
 for(const n of running)deploying.add(n);
 if(changed)renderDeployApps();
 if(running.size||deploying.size)deployPoll=setTimeout(()=>loadDeployments().catch(()=>{}),3000);
}
$('deploy-form').addEventListener('submit',e=>{
 e.preventDefault();const name=$('dep-name').value.trim(),btn=$('deploy-form').querySelector('button[type=submit]');
 whileBusy(btn,async()=>{try{
  await putPolicy({apps:{[name]:{server:$('dep-server').value,path:$('dep-path').value.trim(),repo:$('dep-repo').value.trim(),
   health_url:$('dep-health').value.trim(),staging:$('dep-staging').checked}}});
  $('deploy-form').reset();$('deploy-register').open=false;notice('App '+name+' registered.');
 }catch(err){notice(err.message)}});
});

// Jump to the newest message when the reader has scrolled up during a live reply.
(()=>{
 const box=$('thread-messages'),jump=el('button','jump-latest','↓ Latest');jump.type='button';jump.id='thread-jump';jump.hidden=true;
 box.after(jump);
 jump.onclick=()=>{box.scrollTo({top:box.scrollHeight,behavior:'smooth'});jump.hidden=true};
 box.addEventListener('scroll',()=>{if(nearBottom(box))jump.hidden=true},{passive:true});
})();

// HLA_SCHEDULES_V1: run a prompt in this conversation every hour/day/week.
const INTERVAL_TEXT={60:'every hour',360:'every 6 hours',720:'every 12 hours',1440:'every day',10080:'every week'};
function untilText(v){const ms=new Date(v)-Date.now();if(ms<=60000)return 'within a minute';const m=Math.round(ms/60000);
 return m<60?'in '+m+' min':m<1440?'in '+Math.round(m/60)+' h':'in '+Math.round(m/1440)+' d'}
async function loadSchedules(){
 if(!currentThread)return;
 const id=currentThread,data=await api('threads/'+id+'/schedules');if(currentThread!==id)return;
 const list=$('schedule-list');list.replaceChildren();
 if(!data.schedules.length)list.append(el('p','muted','No schedules. Add one to have this agent run a prompt regularly; replies appear here.'));
 for(const sc of data.schedules){
  const row=el('div','schedule-row'+(sc.enabled?'':' paused'));
  const info=el('div','schedule-info');info.append(el('strong',null,sc.prompt));
  info.append(el('span','muted',INTERVAL_TEXT[sc.interval_minutes]+' · '+(sc.enabled?'next '+untilText(sc.next_run_at):'paused')
   +(sc.last_run_at?' · last '+when(sc.last_run_at)+(sc.last_status?' ('+(STATUS_LABELS[sc.last_status]||sc.last_status).toLowerCase()+')':''):'')));
  const acts=el('div','schedule-actions');
  const act=(label,cls,fn)=>{const b=el('button',cls,label);b.type='button';b.onclick=async()=>{b.disabled=true;
   try{await fn();await Promise.all([loadSchedules(),loadThreads()]);if(currentThread)await openThread(currentThread)}catch(e){notice(e.message)}finally{b.disabled=false}};return b};
  acts.append(act('Run now','secondary small',()=>api('schedules/'+sc.id,{method:'PATCH',headers:UI_HEADERS,body:JSON.stringify({run_now:true})})),
   act(sc.enabled?'Pause':'Resume','ghost small',()=>api('schedules/'+sc.id,{method:'PATCH',headers:UI_HEADERS,body:JSON.stringify({enabled:!sc.enabled})})),
   act('Delete','ghost small',async()=>{if(!await uiConfirm('Delete this schedule? It stops running; replies it already posted stay.',{title:'Delete schedule',detail:sc.prompt,ok:'Delete schedule',danger:true}))throw new Error('Kept the schedule.');await api('schedules/'+sc.id,{method:'DELETE',headers:{'x-agent-ui':'1'}})}));
  row.append(info,acts);list.append(row);
 }
}
$('schedule-toggle').addEventListener('click',()=>{
 const panel=$('schedule-panel'),open=panel.hidden;panel.hidden=!open;$('schedule-toggle').setAttribute('aria-expanded',String(open));
 if(open){const first=new Date(Date.now()+5*60000);first.setSeconds(0,0);
  $('schedule-first').value=new Date(first-first.getTimezoneOffset()*60000).toISOString().slice(0,16);
  loadSchedules().catch(e=>notice(e.message))}
});
$('schedule-form').addEventListener('submit',e=>{
 e.preventDefault();if(!currentThread)return;
 const first=$('schedule-first').value?new Date($('schedule-first').value).toISOString():null;
 whileBusy($('schedule-form').querySelector('button[type=submit]'),async()=>{try{await api('threads/'+currentThread+'/schedules',{method:'POST',headers:UI_HEADERS,body:JSON.stringify(
   {prompt:$('schedule-prompt').value.trim(),interval_minutes:Number($('schedule-interval').value),first_run_at:first})});
  $('schedule-prompt').value='';notice('Schedule added: '+INTERVAL_TEXT[$('schedule-interval').value]+'.');await Promise.all([loadSchedules(),loadThreads()])}
 catch(err){notice(err.message)}});
});

// The task drawer must work from any page (e.g. "Open task" on Knowledge), so it lives outside the Tasks section.
$('workspace').append($('detail'));
// Knowledge search: Enter searches, like the fact search does live.
$('kb-search').addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();loadKnowledge().catch(err=>notice(err.message))}});
// Navigation: icon-only buttons on small screens still need names; the current page is announced.
document.querySelectorAll('button[data-view]').forEach(b=>{b.setAttribute('aria-label',b.textContent.replace(/^\W+/,'').trim())});

// HLA_PROJECTS_V1: multi-agent projects: plan, approve, follow tasks, act on gates and failures.
const PROJECT_STATUS={planning:['Planning…','running'],plan_failed:['Plan failed','failed'],awaiting_approval:['Plan ready · review','human_approval_required'],
 running:['Running','running'],paused:['Paused','incomplete'],completed:['Completed','completed'],cancelled:['Cancelled','rejected']};
const PTASK_STATUS={pending:['Waiting','rejected'],running:['Working','running'],needs_you:['Needs you','human_approval_required'],
 waiting_gate:['Needs approval','human_approval_required'],done:['Done','completed'],failed:['Failed','failed'],blocked:['Blocked · needs you','human_approval_required'],skipped:['Skipped','rejected'],cancelled:['Cancelled','rejected']};
let currentProject=null,projectPoll=null,projectsCache=[];
function pill(map,status){const [label,tone]=map[status]||[status,'unknown'];return el('span','pill st-'+tone,label)}
const usd=v=>'$'+Number(v||0).toFixed(Number(v)<1?4:2);
async function loadProjects(){
 const data=await api('projects');projectsCache=data.projects;const list=$('project-list');list.replaceChildren();
 if(!projectsCache.length)list.append(el('p','muted empty-list','No projects yet. Start one with + New.'));
 for(const p of projectsCache){
  const b=el('button','thread-item'+(p.id===currentProject?' active':''));b.type='button';
  const top=el('div','thread-item-top');top.append(el('span','thread-item-title',p.title));
  if(p.attention)top.append(el('span','badge badge-warn',p.attention+' need'+(p.attention>1?'':'s')+' you'));
  else if(p.status==='awaiting_approval')top.append(el('span','badge badge-warn','review plan'));
  else if(p.status==='running')top.append(el('span','badge badge-run','running'));
  b.append(top,el('div','thread-item-meta',(PROJECT_STATUS[p.status]||[p.status])[0]+' · '+p.done_count+'/'+p.task_count+' tasks · '+usd(p.spent)+' · '+when(p.updated_at)));
  b.onclick=()=>openProject(p.id).catch(e=>notice(e.message));list.append(b);
 }
}
// A completed project keeps going: describe the next features; the planner plans them on top of what is done.
const roundDrafts={};  // per project: the round form stays open and keeps its text when the page re-renders
function nextRoundForm(p,d){
 const draft=roundDrafts[p.id]||(roundDrafts[p.id]={open:false,text:'',budget:'2',runs:'40'});
 const box=el('details','project-next');box.open=draft.open;box.addEventListener('toggle',()=>{draft.open=box.open});box.append(el('summary',null,'＋ Add features or changes (round '+(p.phase+1)+')'));
 const form=el('form','project-next-form');
 const text=el('textarea');text.rows=6;text.maxLength=50000;text.placeholder='What should be added or changed? For example: add a contact form that mails the board; show the events on the homepage.';text.setAttribute('aria-label','New request');text.value=draft.text;text.oninput=()=>{draft.text=text.value};
 const lim=el('div','project-next-limits');
 const bud=el('input');bud.type='number';bud.min='0.05';bud.max='100';bud.step='any';bud.value=draft.budget;bud.oninput=()=>{draft.budget=bud.value};bud.setAttribute('aria-label','Budget for this round in USD');
 const rn=el('input');rn.type='number';rn.min='1';rn.max='500';rn.value=draft.runs;rn.oninput=()=>{draft.runs=rn.value};rn.setAttribute('aria-label','Runs for this round');
 const l1=el('label',null,'Budget for this round ($)');l1.append(bud);const l2=el('label',null,'Runs for this round');l2.append(rn);lim.append(l1,l2);
 const go=el('button',null,'Plan this round');go.type='submit';
 form.append(text,lim,el('p','fineprint','Same folder, rules, notes and agents. The planner sees what earlier rounds delivered and plans only the new work; nothing runs until you approve the plan.'),go);
 form.onsubmit=async e=>{e.preventDefault();if(text.value.trim().length<10)return notice('Describe the new work in a sentence or more.');
  go.disabled=true;try{delete roundDrafts[p.id];await projectAction('/continue',{request:text.value.trim(),budget_usd:Number(bud.value)||2,max_runs:Number(rn.value)||40},'Round '+(p.phase+1)+': the planner is writing the tasks.')}catch(err){roundDrafts[p.id]=draft;notice(err.message);go.disabled=false}};
 box.append(form);return box}
// Project knowledge: what this project's agents know (task handoffs, agent notes, yours). They get it in every task.
const kbEditing={};  // note id -> true while it is being edited (survives the page's live re-render)
function projectKnowledgeEl(pid,entries){
 const box=el('details','project-kb');box.dataset.k='kb';
 box.append(el('summary',null,'Project knowledge ('+entries.length+')'));
 box.append(el('p','fineprint','Agents working on this project get these notes in every task and the planner uses them for new rounds. Finished tasks add their handoff here; agents save decisions and paths. Never put passwords here: use the secrets tool.'));
 const list=el('div','kb-list');
 if(!entries.length)list.append(el('p','muted','Nothing yet. It fills up as tasks finish, or add a note below.'));
 const call=(path,method,body)=>api('projects/'+pid+'/knowledge'+path,{method,headers:UI_HEADERS,body:body?JSON.stringify(body):undefined});
 for(const entry of entries){
  const art=el('article','kb-entry');art.dataset.id=entry.id;const head=el('div','kb-entry-head');
  const tw=el('div','kb-title-row');tw.append(el('strong',null,entry.title),originPill(entry));
  const acts=el('div');head.append(tw,acts);art.append(head);
  if(kbEditing[entry.id]){
   const t=el('input');t.value=entry.title;t.maxLength=160;t.dataset.k='kb-t-'+entry.id;t.setAttribute('aria-label','Title');
   const b=el('textarea');b.rows=6;b.maxLength=20000;b.value=entry.body;b.dataset.k='kb-b-'+entry.id;b.setAttribute('aria-label','Note');
   const save=el('button','small','Save');save.type='button';const cancel=el('button','ghost small','Cancel');cancel.type='button';
   save.onclick=()=>whileBusy(save,async()=>{try{await call('/'+entry.id,'PUT',{title:t.value.trim(),body:b.value.trim(),source:entry.source||null});
    delete kbEditing[entry.id];notice('Note updated. Platform updates won\'t overwrite your version.');await openProject(pid)}catch(e){notice(e.message)}});
   cancel.onclick=()=>{delete kbEditing[entry.id];openProject(pid).catch(e=>notice(e.message))};
   const row=el('div','ptask-actions');row.append(save,cancel);art.append(t,b,row);
  }else{
   const edit=el('button','secondary small','Edit');edit.type='button';edit.onclick=()=>{kbEditing[entry.id]=true;openProject(pid).catch(e=>notice(e.message))};
   const del=el('button','ghost small','Delete');del.type='button';
   del.onclick=async()=>{if(!await uiConfirm('Delete “'+entry.title+'”? Agents will no longer see it.',{title:'Delete note',ok:'Delete note',danger:true}))return;whileBusy(del,async()=>{try{await call('/'+entry.id,'DELETE');notice('Note deleted.');await openProject(pid)}catch(e){notice(e.message)}})};
   acts.append(edit,del);
   const body=el('div','md-view rendered-result kb-note-body');renderTaskMarkdown(body,entry.body);art.append(body,el('small',null,kbMeta(entry)));
  }
  list.append(art)}
 box.append(list);
 const form=el('form','project-kb-form');
 const t=el('input');t.placeholder='Title, e.g. Staging URL and branch';t.maxLength=160;t.dataset.k='kb-new-title';t.setAttribute('aria-label','New note title');
 const b=el('textarea');b.rows=3;b.maxLength=20000;b.placeholder='What every task should know (decisions, conventions, hosts, paths)…';b.dataset.k='kb-new-body';b.setAttribute('aria-label','New note');
 const add=el('button','secondary small','Add to project knowledge');add.type='submit';form.append(t,b,add);
 form.onsubmit=e=>{e.preventDefault();if(t.value.trim().length<2||b.value.trim().length<3)return notice('A note needs a title and some text.');
  whileBusy(add,async()=>{try{await call('','POST',{title:t.value.trim(),body:b.value.trim()});t.value='';b.value='';delete t.dataset.dirty;delete b.dataset.dirty;
   notice('Added to project knowledge.');await openProject(pid)}catch(err){notice(err.message)}})};
 box.append(form);return box}
async function projectAction(path,body,msg){
 await api('projects/'+currentProject+path,{method:'POST',headers:UI_HEADERS,body:JSON.stringify(body||{})});
 if(msg)notice(msg);await Promise.all([openProject(currentProject),loadProjects()]);refreshInbox();
}
let ptaskEditing=null;
function ptaskEdit(card,form,t){
 const d=drawer('ptask-drawer',true);ptaskEditing={id:t.id,form};
 if(!form.dataset.wired){form.dataset.wired='1';
  for(const type of ['input','change'])form.addEventListener(type,e=>{if(e.target.dataset&&e.target.dataset.k)e.target.dataset.dirty='1'})}
 d.title.textContent='Task '+t.seq+' · '+(card.querySelector('.ptask-title')?.textContent||t.title);
 d.meta.replaceChildren(el('span',null,'Changes are saved with Save task, or when you approve the plan.'));d.actions.replaceChildren();
 form.hidden=false;d.body.replaceChildren(form);
 d.onclose=()=>{form.hidden=true;if(card.isConnected)card.append(form);ptaskEditing=null};
 d.open();setTimeout(()=>{if(!form.contains(document.activeElement))form.querySelector('input')?.focus()},30)}
function projectTaskEl(t,d,editable,agents){
 const card=el('article','ptask ptask-'+t.status);card.dataset.id=t.id;
 const head=el('div','ptask-head');
 head.append(el('span','ptask-seq',String(t.seq)),el('strong','ptask-title',t.title),pill(PTASK_STATUS,t.status));
 if(t.review)head.append(el('span','badge badge-sched','review'));
 if(t.gate)head.append(el('span','badge badge-gate',t.gate_approved_by?'🔓 approved by '+t.gate_approved_by:'🔒 needs approval to start'));
 head.append(...prBadges(t.prs));
 card.append(head);
 const meta=[t.agent_name||'agent'];if(t.depends_on.length)meta.push('after '+t.depends_on.map(k=>(d.tasks.find(x=>x.key===k)||{}).seq||k).join(', '));
 if(t.attempts>1)meta.push(t.attempts+' attempts');
 card.append(el('div','muted ptask-meta',meta.join(' · ')));
 const access=(d.access||{})[t.agent_id];
 if(Array.isArray(access)&&!access.length&&!['done','skipped','cancelled'].includes(t.status))
  card.append(el('p','ptask-warn',''+(t.agent_name||'This agent')+' has no server access, so it can\'t open files or run checks. Give it a level in Policy, or pick another agent for this task.'));
 if(editable){
  const title=el('input');title.value=t.title;title.maxLength=200;title.setAttribute('aria-label','Task title');
  const desc=el('textarea');desc.rows=3;desc.value=t.description;desc.setAttribute('aria-label','Task description');
  const acc=el('input');acc.value=t.acceptance;acc.placeholder='Done when…';acc.setAttribute('aria-label','Done when');
  const who=el('select');who.setAttribute('aria-label','Agent');for(const a of agents)who.add(new Option(a.name,a.id));who.value=t.agent_id||'';
  const gate=el('label','check');const g=el('input');g.type='checkbox';g.checked=t.gate;gate.append(g,document.createTextNode(' Needs my approval to start'));
  const rev=el('label','check');const rv=el('input');rv.type='checkbox';rv.checked=t.review;rev.append(rv,document.createTextNode(' Reviews the tasks it depends on'));
  const fields={title,desc,acc,who,g,rv};for(const [k,f] of Object.entries(fields))f.dataset.k='t-'+t.id+'-'+k;
  // Visible names: a plan is edited field by field, and "Done when" is not obvious from its value alone.
  const field=(label,input)=>{const l=el('label','ptask-field');l.append(el('span',null,label),input);return l};
  const save=el('button','secondary small','Save task');save.type='button';
  card.saveEdits=async()=>{await api('projects/'+d.project.id+'/tasks/'+t.id,{method:'PATCH',headers:UI_HEADERS,
   body:JSON.stringify({title:title.value,description:desc.value,acceptance:acc.value,agent_id:who.value||null,gate:g.checked,review:rv.checked})});
   Object.values(fields).forEach(f=>delete f.dataset.dirty);card.querySelector('.ptask-title').textContent=title.value;
   // HLA_UI_V4: the compact card shows what was saved.
   card.querySelector('.ptask-desc').textContent=desc.value;card.querySelector('.ptask-acc').textContent=acc.value?'Done when: '+acc.value:'';
   card.querySelector('.ptask-meta').textContent=[who.options[who.selectedIndex]?.text||'agent',...card.querySelector('.ptask-meta').textContent.split(' · ').slice(1)].join(' · ')};
  save.onclick=()=>whileBusy(save,async()=>{try{await card.saveEdits();notice('Task saved: '+title.value+'.')}catch(e){notice(e.message)}});
  const acts=el('div','ptask-actions');acts.append(save);
  if(d.project.status==='awaiting_approval'){const del=el('button','ghost small','Remove');del.type='button';
   del.onclick=async()=>{if(!await uiConfirm('Remove “'+t.title+'” from the plan?',{title:'Remove task',ok:'Remove task',danger:true}))return;try{await api('projects/'+d.project.id+'/tasks/'+t.id,{method:'DELETE',headers:{'x-agent-ui':'1'}});await openProject(d.project.id)}catch(e){notice(e.message)}};acts.append(del)}
  // HLA_UI_V4: a compact card (title, instructions in two lines, done when); Edit opens the fields in a drawer.
  // The fields keep living in the card while the drawer is closed, so unsaved edits are saved on approval.
  const form=el('div','ptask-form');form.hidden=true;
  form.append(field('Title',title),field('Instructions',desc),field('Done when',acc),field('Agent',who),gate,rev,acts);
  card.append(el('p','ptask-desc ptask-clamp',t.description||''),el('p','muted ptask-acc',t.acceptance?'Done when: '+t.acceptance:''));
  const edit=el('button','secondary small ptask-edit-btn','Edit');edit.type='button';edit.setAttribute('aria-label','Edit task '+t.seq+': '+t.title);
  edit.onclick=()=>ptaskEdit(card,form,t);
  const er=el('div','ptask-actions ptask-edit-row');er.append(edit);card.append(er,form);
  if(ptaskEditing&&ptaskEditing.id===t.id&&V4_DRAWERS['ptask-drawer']?.isOpen){
   // The page re-rendered while this task's drawer was open: carry the typed values over to the new fields.
   const old=ptaskEditing.form;
   for(const f of form.querySelectorAll('[data-k]')){const o=old.querySelector('[data-k="'+CSS.escape(f.dataset.k)+'"]');
    if(o&&o.dataset.dirty){if(f.type==='checkbox')f.checked=o.checked;else f.value=o.value;f.dataset.dirty='1'}}
   queueMicrotask(()=>ptaskEdit(card,form,t))}
 }else{
  if(t.description){const p=el('p','ptask-desc',t.description);card.append(p)}
  if(t.acceptance)card.append(el('p','muted ptask-acc','Done when: '+t.acceptance));
 }
 if(t.result){const r=el('details','ptask-result');r.open=['failed','blocked'].includes(t.status);r.dataset.k='res-'+t.id+'-'+t.status;
  r.append(el('summary',null,t.status==='blocked'?'What it needs from you':t.status==='failed'?'Why it stopped':'Handoff'));
  const body=el('div','md-view rendered-result');renderTaskMarkdown(body,t.result);r.append(body);card.append(r)}
 const acts=el('div','ptask-actions');
 if(t.thread_id){const o=el('button','ghost small',t.thread_status==='running'?'Watch live':'Open conversation');o.type='button';
  o.onclick=async()=>{switchView('threads');await openThread(t.thread_id)};acts.append(o)}
 // ask: [question, dialog options] shown before the action runs.
 const act=(label,cls,action,ask)=>{const b=el('button',cls,label);b.type='button';b.onclick=async()=>{
  if(ask&&!await uiConfirm(ask[0],ask[1]))return;b.disabled=true;try{await projectAction('/tasks/'+t.id+'/'+action,{},label+': done.')}catch(e){notice(e.message);b.disabled=false}};return b};
 const retryButton=()=>{const b=el('button','secondary small','Retry…');b.type='button';
  b.onclick=async()=>{const note=await uiPrompt('Retry “'+t.title+'”',{label:'Anything the agent should know or do differently? (optional)',
   multiline:true,rows:6,maxLength:4000,ok:'Retry task',placeholder:'For example: the build failed on Node 18; use Node 20.'});if(note===null)return;
   b.disabled=true;try{await projectAction('/tasks/'+t.id+'/retry',note.trim()?{note:note.trim()}:{},'Retrying.')}catch(e){notice(e.message);b.disabled=false}};return b};
 if(t.status==='waiting_gate')acts.append(act('Approve start','small','approve_gate',['Let "'+t.title+'" start? It may deploy or change real servers.',{title:'Approve start',ok:'Approve start'}]));
 if(['failed','blocked','needs_you','cancelled'].includes(t.status)&&d.project.status!=='cancelled')
  acts.append(t.review&&t.status==='failed'&&t.result?act('Send work back','secondary small','retry',['Send the checked tasks back with this review\'s list of changes, then review again?',{title:'Send work back',ok:'Send work back'}]):retryButton());
 if(t.status==='done'&&!['cancelled','awaiting_approval','planning'].includes(d.project.status)){
  const b=el('button','ghost small','Send back…');b.type='button';
  b.onclick=async()=>{const note=await uiPrompt('Send “'+t.title+'” back',{label:'What should change?',multiline:true,rows:8,maxLength:4000,ok:'Send back',
   placeholder:'For example: see review findings F-1 and F-3'});if(note===null)return;
   b.disabled=true;try{await projectAction('/tasks/'+t.id+'/redo',{note},'Sent back for rework.')}catch(e){notice(e.message);b.disabled=false}};
  acts.append(b)}
 if(['pending','failed','blocked','needs_you','waiting_gate','cancelled'].includes(t.status)&&!['completed','cancelled','awaiting_approval','planning','plan_failed'].includes(d.project.status))
  acts.append(act('Skip','ghost small','skip',['Skip "'+t.title+'"? Tasks after it continue without its result.',{title:'Skip task',ok:'Skip task',danger:true}]));
 if(acts.children.length)card.append(acts);
 return card;
}
async function openProject(id){
 const switching=currentProject!==id;currentProject=id;clearTimeout(projectPoll);
 const [d,kb,files]=await Promise.all([api('projects/'+id),api('projects/'+id+'/knowledge').catch(()=>null),
  api('attachments?project_id='+id).catch(()=>null)]);if(currentProject!==id)return;store.set('project',id);
 const p=d.project,main=$('project-main'),editable=['awaiting_approval','paused'].includes(p.status);
 // Keep what the user is typing (plan editor, note, next round): no re-render while a field has focus, unless the
 // project changed state; otherwise unsaved values, open sections and the caret survive the re-render.
 if(!switching&&main.dataset.project===String(id)&&main.dataset.status===p.status&&main.contains(document.activeElement)&&document.activeElement.matches('input,textarea,select')){
  if(['planning','running'].includes(p.status)||d.tasks.some(t=>t.status==='running'))projectPoll=setTimeout(()=>openProject(id).catch(()=>{}),4000);return}
 const restore=switching||main.dataset.project!==String(id)?null:keepState(main);
 main.dataset.status=p.status;main.dataset.project=id;
 const scroll=main.scrollTop;main.replaceChildren();
 const head=el('div','project-head');const title=el('div');title.append(el('h2',null,p.title),pill(PROJECT_STATUS,p.status));
 const actions=el('div','project-actions');
 const btn=(label,cls,fn)=>{const b=el('button',cls,label);b.type='button';b.onclick=async()=>{b.disabled=true;try{await fn()}catch(e){notice(e.message)}finally{b.disabled=false}};return b};
 if(p.status==='awaiting_approval')actions.append(btn('Approve plan & start','',async()=>{
  // Edits typed into the plan but not saved yet are saved first, so the plan that runs is the one on screen.
  const unsaved=[...main.querySelectorAll('.ptask')].filter(c=>c.saveEdits&&c.querySelector('[data-dirty]'));
  if(!await uiConfirm('Start this project? Agents will work on the tasks in order; gated tasks still wait for you.'+(unsaved.length?'\n\nYour unsaved edits to '+unsaved.length+' task'+(unsaved.length>1?'s':'')+' are saved first.':''),
   {title:'Start project',ok:'Approve & start'}))return;
  for(const c of unsaved)await c.saveEdits();
  return projectAction('/approve',{},'Project started.')}));
 if(['awaiting_approval','plan_failed'].includes(p.status))actions.append(btn('Re-plan…','secondary',async()=>{
  const f=await uiPrompt('Re-plan “'+p.title+'”',{label:'What should the planner change? (optional)',multiline:true,rows:6,maxLength:2000,ok:'Re-plan',
   placeholder:'For example: split the deploy into staging and production, and add a review task.'});if(f===null)return;return projectAction('/replan',f.trim()?{text:f.trim()}:{},'Re-planning…')}));
 if(p.status==='running')actions.append(btn('Pause','secondary',()=>projectAction('/pause',{},'Paused. Running tasks finish their current reply.')));
 if(p.status==='paused')actions.append(btn('Resume','',()=>projectAction('/resume',{},'Resumed.')));
 if(!['completed','cancelled'].includes(p.status))actions.append(btn('Cancel','ghost',async()=>{if(await uiConfirm('Cancel this project? Running tasks are stopped.',{title:'Cancel project',ok:'Cancel project',cancel:'Keep project',danger:true}))return projectAction('/cancel',{},'Project cancelled.')}));
 const back=el('button','ghost small chat-back','← All projects');back.type='button';
 back.onclick=()=>$('projects').querySelector('.projects-layout').classList.remove('has-project');
 head.prepend(back);head.append(title,actions);main.append(head);
 const facts=el('div','project-facts');
 const budget=el('span',d.spent>=Number(p.budget_usd)*0.8?'warn':null,usd(d.spent)+' of '+usd(p.budget_usd));
 facts.append(el('span','muted','Spent '),budget,el('span','muted',' · runs '+d.runs+' of '+p.max_runs+' · '+p.parallel+' at a time · '),el('code',null,p.workdir));
 if(!['completed','cancelled'].includes(p.status)){const lim=btn('Limits…','ghost small',async()=>{
   const v=await uiForm('Project limits',[
    {name:'budget_usd',label:'Budget in USD',type:'number',value:p.budget_usd,min:0.05,max:1000,required:true,hint:'Total for the project; '+usd(d.spent)+' spent so far.'},
    {name:'max_runs',label:'Maximum runs',type:'number',value:p.max_runs,min:1,max:5000,step:1,required:true,hint:d.runs+' used so far.'},
    {name:'parallel',label:'Tasks at a time',type:'select',value:p.parallel,options:[1,2,3,4]}],{ok:'Save limits'});
   if(v===null)return;
   await api('projects/'+id,{method:'PATCH',headers:UI_HEADERS,body:JSON.stringify({budget_usd:v.budget_usd,max_runs:v.max_runs,parallel:Number(v.parallel)})});notice('Limits updated.');await openProject(id)});facts.append(lim)}
 facts.append(mergePolicyEl(p));
 main.append(facts);
 main.append(projectDomainsEl(p));  // HLA_ROUTES_V1
 if(p.status==='paused'&&p.pause_reason)main.append(el('p','project-paused','Paused: '+p.pause_reason));
 if(p.status==='planning'){const w=el('div','project-planning');const dots=el('div','typing');dots.append(el('span'),el('span'),el('span'));w.append(dots,el('span','muted','The planner is writing a task list…'));main.append(w)}
 if(p.error&&p.status==='plan_failed')main.append(el('p','error',p.error));
 if(p.summary)main.append(el('p','project-summary',p.summary));
 if(p.rules){const r=el('details','project-rules');r.dataset.k='rules';r.append(el('summary',null,'Rules from the brief'),el('pre',null,p.rules));main.append(r)}
 const brief=el('details','project-rules');brief.dataset.k='brief';brief.append(el('summary',null,'Goal / brief ('+p.goal.length.toLocaleString()+' characters)'));
 const bb=el('div','md-view rendered-result');renderTaskMarkdown(bb,p.goal);brief.append(bb);main.append(brief);
 if(p.status==='awaiting_approval')main.append(el('p','fineprint','Review the plan: edit titles, instructions, the agent per task and which tasks need your approval to start. Nothing runs until you approve.'));
 // Rounds: tasks grouped per round, the latest first; earlier rounds are finished history and read-only.
 const rounds=d.rounds||[];
 if(rounds.length){
  for(let ph=p.phase;ph>=1;ph--){
   const sec=el('details','project-round');sec.open=ph===p.phase;sec.dataset.k='round-'+ph;const r=rounds.find(x=>x.phase===ph);
   const tasks=d.tasks.filter(t=>(t.phase||1)===ph),done=tasks.filter(t=>['done','skipped'].includes(t.status)).length;
   sec.append(el('summary',null,'Round '+ph+(ph===1?' · original goal':' · '+(r?.request||'').split('\n')[0].slice(0,90))+' — '+done+' of '+tasks.length+' done'));
   if(r){const req=el('div','md-view rendered-result');renderTaskMarkdown(req,r.request);sec.append(req);if(r.summary)sec.append(el('p','project-summary',r.summary))}
   const board=el('div','ptask-board');for(const t of tasks)board.append(projectTaskEl(t,d,editable&&ph===p.phase,d.agents));sec.append(board);main.append(sec)}
 }else{const board=el('div','ptask-board');for(const t of d.tasks)board.append(projectTaskEl(t,d,editable,d.agents));main.append(board)}
 if(p.status==='completed')main.append(nextRoundForm(p,d));
 if(files){  // HLA_ATTACHMENTS_V1: reference files for the planner and every task, plus files agents made
  const sec=el('details','project-rules project-files');sec.dataset.k='files';
  sec.append(el('summary',null,'Files'+(files.attachments.length?' ('+files.attachments.length+')':'')));const inner=el('div');sec.append(inner);
  filesPanel(inner,files.attachments,{upload:{project_id:id},reload:()=>openProject(id),
   hint:'The planner and every task can read these; files agents create appear here too.',
   empty:'No files yet. Upload briefs, screenshots or spreadsheets as reference for the agents.'});
  main.append(sec)}
 if(kb)main.append(projectKnowledgeEl(id,kb.entries));
 const notes=el('div','project-notes');notes.append(el('h3',null,'Notes & log'));
 const form=el('form','note-form');const inp=el('input');inp.placeholder='Add a note for the agents (they read the latest notes)…';inp.maxLength=2000;inp.setAttribute('aria-label','Project note');inp.dataset.k='note';
 const add=el('button','secondary small','Add');form.append(inp,add);
 form.onsubmit=e=>{e.preventDefault();if(!inp.value.trim())return;whileBusy(add,async()=>{try{await api('projects/'+id+'/notes',{method:'POST',headers:UI_HEADERS,body:JSON.stringify({text:inp.value.trim()})});
  inp.value='';delete inp.dataset.dirty;inp.blur();notice('Note added. Agents read it on their next run.');await openProject(id)}catch(err){notice(err.message)}})};
 notes.append(form);const ol=el('ol','project-log');
 for(const e of d.log){const li=el('li');li.append(el('time','muted',when(e.created_at)),el('strong',null,actorName(e.actor)),el('span',null,e.text));ol.append(li)}
 notes.append(ol);main.append(notes);
 if(restore)restore();
 if(!switching)main.scrollTop=scroll;
 $('projects').querySelector('.projects-layout').classList.add('has-project');
 document.querySelectorAll('#project-list .thread-item').forEach((b,i)=>b.classList.toggle('active',projectsCache[i]?.id===id));
 const live=['planning','running'].includes(p.status)||d.tasks.some(t=>t.status==='running');
 if(live&&!$('projects').hidden)projectPoll=setTimeout(()=>Promise.all([openProject(id),loadProjects()]).catch(()=>{}),3000);
}
async function loadProjectPlanners(){
 const data=await api('agents?workspace='+encodeURIComponent($('project-workspace').value));const sel=$('project-planner');
 sel.replaceChildren(new Option('Default model',''));for(const a of (data.agents||data))if(a.enabled)sel.add(new Option(a.name,a.id));
}
$('project-new-toggle').addEventListener('click',()=>{const f=$('project-form'),open=f.hidden;f.hidden=!open;$('project-new-toggle').setAttribute('aria-expanded',String(open));
 if(open){loadProjectPlanners().catch(()=>{});$('project-title').focus()}});
const projectTray=fileTray($('project-attachments'));
$('project-attach').addEventListener('click',()=>$('project-file').click());
$('project-file').addEventListener('change',e=>{projectTray.add([...e.target.files]);e.target.value=''});
$('project-cancel-new').addEventListener('click',()=>{projectTray.clear(true);$('project-form').hidden=true;$('project-new-toggle').setAttribute('aria-expanded','false')});
$('project-form').addEventListener('keydown',e=>{if(e.key==='Escape'){e.preventDefault();$('project-cancel-new').click();$('project-new-toggle').focus()}});
$('project-workspace').addEventListener('change',()=>loadProjectPlanners().catch(()=>{}));
$('project-form').addEventListener('submit',async e=>{
 e.preventDefault();const btn=e.submitter||$('project-form').querySelector('button[type=submit]');btn.disabled=true;
 try{const attachment_ids=await projectTray.ids();
  const r=await api('projects',{method:'POST',headers:UI_HEADERS,body:JSON.stringify({title:$('project-title').value.trim(),goal:$('project-goal').value.trim(),
   workspace:$('project-workspace').value,planner_agent_id:$('project-planner').value||null,budget_usd:Number($('project-budget').value),
   max_runs:Number($('project-runs').value),parallel:Number($('project-parallel').value),merge_policy:mergePolicyField().value,...projectDomains(),attachment_ids})});
  projectTray.clear(false);$('project-form').reset();$('project-form').hidden=true;notice('Project created. The planner is writing a task list.');await loadProjects();await openProject(r.id)}
 catch(err){notice(err.message)}finally{btn.disabled=false}
});
document.querySelector('button[data-view="projects"]').addEventListener('click',()=>{
 loadProjects().then(()=>{const target=currentProject||(window.matchMedia('(min-width: 901px)').matches&&projectsCache[0]?.id);if(target)return openProject(target)}).catch(e=>notice(e.message))});
$('thread-project-back').addEventListener('click',()=>{const id=$('thread-project-back').dataset.project;if(!id)return;switchView('projects');
 loadProjects().then(()=>openProject(id)).catch(e=>notice(e.message))});

// A long brief can be loaded from a file instead of pasted.
function updateGoalCount(){const n=$('project-goal').value.length;$('project-goal-count').textContent=n.toLocaleString()+' / 50,000'}
$('project-goal').addEventListener('input',updateGoalCount);
$('project-goal-load').addEventListener('click',()=>$('project-goal-file').click());
$('project-goal-file').addEventListener('change',async e=>{
 const file=e.target.files[0];e.target.value='';if(!file)return;
 if(file.size>200000)return notice('That file is too large for a project goal (max 50,000 characters).');
 const text=await file.text();
 if(text.length>50000)return notice('That file has '+text.length.toLocaleString()+' characters; the goal can hold 50,000.');
 $('project-goal').value=text;updateGoalCount();
 if(!$('project-title').value.trim()){const h=text.match(/^#\s+(.+)$/m);if(h)$('project-title').value=h[1].replace(/^Project:\s*/i,'').slice(0,120)}
 notice('Loaded '+file.name+' ('+text.length.toLocaleString()+' characters).');
});

// HLA_INBOX_V1: helpers for a calmer UI (remembered place, busy buttons, state that survives re-renders),
// the "Needs you" inbox on Overview, and conversation rename / archive / delete.
var store={get(k){try{return localStorage.getItem('hla.'+k)}catch(e){return null}},
 set(k,v){try{if(v==null)localStorage.removeItem('hla.'+k);else localStorage.setItem('hla.'+k,String(v))}catch(e){}}};
// One request per button: disabled (and announced as busy) until it settles.
async function whileBusy(btn,fn){if(!btn)return fn();if(btn.disabled)return;btn.disabled=true;btn.setAttribute('aria-busy','true');
 try{return await fn()}finally{btn.disabled=false;btn.removeAttribute('aria-busy')}}
// Re-rendering a live page keeps what the reader was doing: unsaved values, opened sections, focus and caret.
// Elements opt in with data-k (a stable key); fields count as unsaved once typed into (data-dirty).
function keepState(root){
 const values={},open={};let focus=null;
 root.querySelectorAll('[data-k]').forEach(e=>{const k=e.dataset.k;
  if(e.tagName==='DETAILS')open[k]=e.open;else if(e.dataset.dirty)values[k]=e.type==='checkbox'?e.checked:e.value});
 const a=document.activeElement;
 if(a&&root.contains(a)&&a.dataset.k)focus={k:a.dataset.k,s:a.selectionStart,e:a.selectionEnd};
 return ()=>{
  root.querySelectorAll('[data-k]').forEach(e=>{const k=e.dataset.k;
   if(e.tagName==='DETAILS'){if(k in open)e.open=open[k]}
   else if(k in values){if(e.type==='checkbox')e.checked=values[k];else e.value=values[k];e.dataset.dirty='1'}});
  if(focus){const f=root.querySelector('[data-k="'+CSS.escape(focus.k)+'"]');
   if(f){f.focus({preventScroll:true});try{if(focus.s!=null)f.setSelectionRange(focus.s,focus.e)}catch(e){}}}
 };
}
for(const id of ['project-main','thread-messages'])for(const type of ['input','change'])
 $(id).addEventListener(type,e=>{if(e.target.dataset&&e.target.dataset.k&&e.target.tagName!=='DETAILS')e.target.dataset.dirty='1'});

function openView(view){const b=document.querySelector('button[data-view="'+view+'"]');if(b)b.click()}
// Opens one item wherever it lives: a project, a conversation or a task (drawer).
async function openItem(kind,id,focusTask){
 if(kind==='task')return openTaskDetail(id);
 if(kind==='project'){switchView('projects');await loadProjects();await openProject(id);
  if(focusTask){const card=document.querySelector('.ptask[data-id="'+focusTask+'"]');
   if(card){card.scrollIntoView({block:'center'});card.classList.add('flash');setTimeout(()=>card.classList.remove('flash'),2200)}}
  return}
 if(kind==='thread'){switchView('threads');await Promise.all([loadThreads(),loadThreadAgents()]);await openThread(id);
  if(window.matchMedia('(min-width: 901px)').matches)$('thread-input').focus({preventScroll:true});return}
}
// After a reload: the page (and conversation or project) that was open last.
function restorePlace(){
 const v=store.get('view');if(!v||!VIEW_TITLES[v]||v==='overview')return;
 const kind=v==='threads'?'thread':v==='projects'?'project':null,id=kind&&store.get(kind);
 if(!id)return openView(v);
 openItem(kind,id).catch(()=>{store.set(kind,null);if(kind==='thread')currentThread=null;else currentProject=null;openView(v)});
}

// "Needs you": everything that waits for a person, refreshed every 15 s, on focus and after each action.
let inboxCount=0,inboxTimer=null,inboxSig='',inboxAll=false;
const INBOX_SHOWN=3;  // more than this folds behind "Show all", so the Overview stays clean on a busy day
const INBOX_KIND={command:['Command','human_approval_required'],approval:['Approval','human_approval_required'],gate:['Approve start','human_approval_required'],
 blocked:['Blocked','human_approval_required'],failed:['Stopped','failed'],plan:['Plan','running'],paused:['Paused','incomplete'],pr:['PR','completed'],
 website:['Website','failed']};
async function loadInbox(){
 const d=await api('inbox');setInboxCount(d.count);
 const sig=JSON.stringify(d.items);if(sig!==inboxSig){inboxSig=sig;renderInbox(d.items)}
 // Conversation badges (working, to approve) stay current while the list is on screen.
 if(!$('threads').hidden)loadThreads().catch(()=>{});
 return d;
}
function refreshInbox(){return loadInbox().catch(()=>{})}
function setInboxCount(n){
 inboxCount=n;const b=$('nav-inbox-badge');b.hidden=!n;b.textContent=n>99?'99+':String(n);
 const nav=b.closest('button');nav.setAttribute('aria-label','Home'+(n?', '+n+' waiting for you':''));
 const c=$('inbox-count');c.hidden=!n;c.textContent=String(n);
 setTitle(titleState);
}
function inboxAction(label,cls,fn){const b=el('button',cls,label);b.type='button';
 b.onclick=()=>whileBusy(b,async()=>{try{await fn()}catch(e){notice(e.message)}});return b}
function renderInbox(items){
 const list=$('inbox-list');list.replaceChildren();
 if(!items.length){const empty=el('div','inbox-empty');empty.append(el('strong',null,'Nothing needs you right now.'),
   el('p','muted','Approvals, plans to review and stopped tasks appear here. Start a conversation or a project to put the agents to work.'));
  list.append(empty);return}
 for(const it of inboxAll?items:items.slice(0,INBOX_SHOWN)){
  const [label,tone]=INBOX_KIND[it.kind]||[it.kind,'unknown'];
  const row=el('article','inbox-item inbox-'+it.kind);
  const main=el('div','inbox-main');
  const head=el('div','inbox-head');head.append(el('span','pill st-'+(it.failed?'failed':tone),label),el('strong',null,it.title));
  if(it.risk!=null)head.append(el('span','muted','risk '+Math.round(Number(it.risk)*100)+'%'));
  main.append(head,el('div','inbox-context',it.context));
  if(it.detail)main.append(el(it.kind==='command'?'code':'div','inbox-detail'+(it.kind==='command'?' inbox-cmd':''),it.detail));
  const t=el('time','muted inbox-when',when(it.since));t.title=new Date(it.since).toLocaleString();main.append(t);
  const acts=el('div','inbox-actions');
  const openProj=(label,cls)=>inboxAction(label,cls,()=>openItem('project',it.project_id,it.project_task_id));
  const openConv=(label,cls)=>inboxAction(label,cls,()=>openItem('thread',it.thread_id));
  if(it.kind==='command')acts.append(openConv('Review command','small'));
  else if(it.kind==='approval'&&it.task_ids)acts.append(inboxAction('Review '+it.count+' tasks','small',()=>{
   $('task-status-filter').value='human_approval_required';$('task-search').value='';$('task-workspace-filter').value='';taskQuery.offset=0;
   switchView('tasks');return loadTasks()}));
  else if(it.kind==='approval')acts.append(it.thread_id?openConv('Review request','small'):inboxAction('Review request','small',()=>openTaskDetail(it.task_id)));
  else if(it.kind==='plan')acts.append(openProj(it.failed?'Open project':'Review plan','small'));
  else if(it.kind==='gate'){
   acts.append(inboxAction('Approve start','small',async()=>{
    if(!await uiConfirm('Let "'+it.detail+'" ('+it.context+') start? It may deploy or change real servers.',{title:'Approve start',ok:'Approve start'}))return;
    await api('projects/'+it.project_id+'/tasks/'+it.project_task_id+'/approve_gate',{method:'POST',headers:UI_HEADERS,body:'{}'});
    notice('Approved: "'+it.detail+'" starts now.');await afterInboxAction(it)}),openProj('Open project','ghost small'));
  }else if(it.kind==='paused'){
   acts.append(inboxAction('Resume','small',async()=>{
    await api('projects/'+it.project_id+'/resume',{method:'POST',headers:UI_HEADERS,body:'{}'});notice(it.context+' resumed.');await afterInboxAction(it)}),
    openProj('Open project','ghost small'));
  }else if(it.kind==='pr')acts.append(prLink(it.url,'Open PR','small'),openProj('Open project','ghost small'));
  else if(it.kind==='website'){acts.append(inboxAction('Open Websites','small',async()=>openWebsite(it.website_id)));
   if(it.url&&/^https?:\/\//.test(it.url))acts.append(prLink(it.url,'Open site','ghost small'))}
  else if(it.kind==='route_request'){  // HLA_ROUTES_V1
   acts.append(inboxAction('Approve','small',()=>rtInboxDecide(it,'approve')),inboxAction('Reject','ghost small',()=>rtInboxDecide(it,'reject')));
   if(it.project_id)acts.append(openProj('Open project','ghost small'))}
  else{acts.append(openProj('Open project','small'));if(it.thread_id)acts.append(openConv('Conversation','ghost small'))}
  row.append(main,acts);list.append(row);
 }
 if(items.length>INBOX_SHOWN){const more=el('button','ghost small inbox-more',inboxAll?'Show fewer':'Show all '+items.length);more.type='button';
  more.onclick=()=>{inboxAll=!inboxAll;renderInbox(items);$('inbox-list').querySelector('.inbox-more')?.focus()};list.append(more)}
}
async function afterInboxAction(it){
 await refreshInbox();
 if(it.project_id&&currentProject===it.project_id&&!$('projects').hidden)await Promise.all([openProject(it.project_id),loadProjects()]);
}
$('inbox-refresh').addEventListener('click',()=>whileBusy($('inbox-refresh'),()=>loadInbox().catch(e=>notice(e.message))));
function startBackground(){clearInterval(inboxTimer);refreshInbox();inboxTimer=setInterval(()=>{if(!document.hidden)refreshInbox()},15000)}
function stopBackground(){clearInterval(inboxTimer);inboxTimer=null;clearTimeout(projectPoll);stopFollowing();setInboxCount(0);inboxSig=''}
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&inboxTimer)refreshInbox()});
document.querySelector('button[data-view="overview"]').addEventListener('click',()=>refreshInbox());

// Conversations: rename (inline), archive / unarchive, delete; a toggle lists the archived ones.
let renaming=false;
function cancelRename(){if(!renaming)return;renaming=false;$('thread-heading').hidden=false;document.querySelector('.rename-input')?.remove()}
function closeThreadMenu(){$('thread-menu').hidden=true;$('thread-menu-toggle').setAttribute('aria-expanded','false')}
$('thread-menu-toggle').addEventListener('click',e=>{e.stopPropagation();const open=$('thread-menu').hidden;$('thread-menu').hidden=!open;
 $('thread-menu-toggle').setAttribute('aria-expanded',String(open));if(open)$('thread-menu').querySelector('button:not(:disabled)')?.focus()});
document.addEventListener('click',e=>{if(!$('thread-menu').hidden&&!e.target.closest('.menu-wrap'))closeThreadMenu()});
$('thread-menu').addEventListener('keydown',e=>{
 const items=[...$('thread-menu').querySelectorAll('button:not(:disabled)')],i=items.indexOf(document.activeElement);
 if(e.key==='Escape'){e.preventDefault();closeThreadMenu();$('thread-menu-toggle').focus()}
 else if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();items[(i+(e.key==='ArrowDown'?1:-1)+items.length)%items.length]?.focus()}});
$('thread-rename').addEventListener('click',()=>{
 closeThreadMenu();if(!currentThreadData||renaming)return;renaming=true;
 const h=$('thread-heading'),input=el('input','rename-input');input.value=currentThreadData.title;input.maxLength=160;
 input.setAttribute('aria-label','Conversation title');h.hidden=true;h.after(input);input.focus();input.select();
 const id=currentThread;let done=false;
 const save=async()=>{if(done)return;done=true;const title=input.value.trim();
  if(!title||title===currentThreadData.title){cancelRename();return}
  input.disabled=true;
  try{await api('threads/'+id,{method:'PATCH',headers:UI_HEADERS,body:JSON.stringify({title})});
   if(currentThreadData&&currentThread===id)currentThreadData.title=title;if(currentThread===id)h.textContent=title;notice('Renamed to “'+title+'”.');
   loadThreads().catch(()=>{})}
  catch(err){notice(err.message)}finally{cancelRename()}};
 input.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();save()}else if(e.key==='Escape'){e.preventDefault();done=true;cancelRename();$('thread-menu-toggle').focus()}});
 input.addEventListener('blur',()=>save());
});
function clearThreadPane(text){
 stopFollowing();currentThread=null;currentThreadData=null;store.set('thread',null);cancelRename();
 $('thread-heading').textContent='Select a conversation';$('thread-sub').textContent='';
 $('thread-menu-toggle').hidden=true;$('schedule-toggle').hidden=true;$('schedule-panel').hidden=true;$('thread-project-back').hidden=true;
 $('thread-messages').replaceChildren(emptyState(text));setComposer(null);$('chat-layout').classList.remove('has-thread');
}
$('thread-archive').addEventListener('click',async()=>{
 closeThreadMenu();const t=currentThreadData;if(!t)return;const archive=!t.archived_at;
 try{await api('threads/'+t.id,{method:'PATCH',headers:UI_HEADERS,body:JSON.stringify({archived:archive})});
  if(archive){clearThreadPane('Archived “'+t.title+'”. It is listed under Archived; nothing was deleted.');notice('Conversation archived.')}
  else{notice('Conversation moved back to your list.');await openThread(t.id)}
  await loadThreads()}
 catch(err){notice(err.message)}
});
$('thread-delete').addEventListener('click',async()=>{
 closeThreadMenu();const t=currentThreadData;if(!t)return;
 if(!await uiConfirm('Delete “'+t.title+'”?\n\nIts messages and schedules are removed for good. The tasks it ran stay in Task history.',
  {title:'Delete conversation',ok:'Delete conversation',danger:true}))return;
 try{await api('threads/'+t.id,{method:'DELETE',headers:{'x-agent-ui':'1'}});
  clearThreadPane('Conversation deleted. Pick another one, or start one with + New.');notice('Deleted “'+t.title+'”.');
  await loadThreads();refreshInbox()}
 catch(err){notice(err.message)}
});
$('thread-archived').addEventListener('click',()=>{
 showArchived=!showArchived;$('thread-archived').setAttribute('aria-pressed',String(showArchived));
 $('thread-archived').classList.toggle('on',showArchived);
 document.querySelector('.chat-sidebar-head h2').textContent=showArchived?'Archived':'Conversations';
 $('thread-list').replaceChildren(el('p','muted empty-list','Loading…'));loadThreads().catch(e=>notice(e.message));
});

// ── HLA_QUEUE_LABELS: queued jobs show a readable status instead of the raw value ──
const QUEUE_LABELS={queued:'Waiting',running:'Running',completed:'Completed',failed:'Failed',cancelled:'Cancelled'};
function queueStatusPill(status){
 return el('span','pill st-'+String(status||'unknown').replace(/[^a-z_]/g,''),QUEUE_LABELS[status]||STATUS_LABELS[status]||String(status||'Unknown').replace(/_/g,' '))}

// ── HLA_FACT_RECHECK_V1: "Re-check now" for stale diagnostic facts (Knowledge page, facts panel) ──
const RECHECK_LABELS={confirmed:'unchanged',changed:'changed'};
let recheckPoll=null;
function recheckSummary(r){return r.checked?r.checked+' checked: '+r.confirmed+' unchanged, '+r.changed+' changed, '+r.failed+' could not be checked':'nothing was due'}
async function loadRecheckStatus(){
 const s=await api('facts/recheck');
 const parts=[s.schedule==='weekly'?'Stale diagnostic facts are re-checked weekly.':'Weekly re-check is off.'];
 parts.push(s.eligible+' stale diagnostic fact'+(s.eligible===1?'':'s')+' can be re-checked (read-only, at most '+s.max_per_run+' per run).');
 if(s.last_run)parts.push('Last run '+when(s.last_run.started_at)+(s.last_run.finished_at?': '+recheckSummary(s.last_run)+(s.last_run.note?' ('+s.last_run.note+')':''):', still running')+'.');
 $('fact-recheck-status').textContent=parts.join(' ');
 $('fact-recheck').disabled=s.running;
 clearTimeout(recheckPoll);
 if(s.running)recheckPoll=setTimeout(async()=>{try{if(!(await loadRecheckStatus()).running)await loadFacts()}catch(e){}},3000);
 return s;
}
$('fact-recheck').addEventListener('click',()=>whileBusy($('fact-recheck'),async()=>{
 try{const r=await api('facts/recheck',{method:'POST',headers:{'x-agent-ui':'1'}});
  notice(r.status==='started'?'Re-check started. Facts update when it finishes.':'Re-check done: '+recheckSummary(r)+'.'+(r.note?' '+r.note:''));
  await Promise.all([loadFacts(),loadRecheckStatus()])}
 catch(e){notice(e.message)}}));
document.querySelector('button[data-view="knowledge"]').addEventListener('click',()=>loadRecheckStatus().catch(()=>{}));

// ── HLA_AUDIT_V1: the SSH gateway's audit log (Audit view) ──
const AUDIT_ACTIONS={agent_diagnostic:'Diagnostic',agent_maintenance:'Maintenance',agent_approved_command:'Approved command',agent_command:'Command',
 agent_free_shell:'Free-shell command',agent_unhinged:'Unhinged command',agent_denied:'Refused: needs approval',agent_write_file:'File write',
 agent_read_file:'File read',chat_approval:'Approval granted',deploy_deployed:'Deployed',deploy_rolled_back:'Deploy rolled back',
 deploy_failed:'Deploy failed',deploy_error:'Deploy error',agent_ha_read:'Home Assistant read',agent_ha_change:'Home Assistant change',
 agent_secret_use:'Secret used',agent_secret_created:'Secret created',agent_secret_replaced:'Secret replaced',diagnostic:'Diagnostic (CLI)',run:'Command (CLI)'};
const AUDIT_OUTCOMES={ok:['OK','st-completed'],failed:['Failed','st-failed'],denied:['Refused','st-rejected'],timed_out:['Timed out','st-incomplete'],
 error:['No result','st-failed'],recorded:['Recorded','st-running']};
const auditAction=a=>AUDIT_ACTIONS[a]||String(a||'').replace(/^agent_/,'').replace(/_/g,' ');
let auditNext=null,auditCount=0,auditSeq=0,auditTimer=null,auditLastFocus=null;
function auditParams(before){
 const p=new URLSearchParams({limit:'50'});
 for(const [k,id] of [['agent','audit-agent'],['server','audit-server'],['action','audit-action'],['q','audit-q']]){const v=$(id).value.trim();if(v)p.set(k,v)}
 // Dates are local days: From counts from its midnight, To includes that whole day.
 const day=(id,add)=>{const v=$(id).value;if(!v)return null;const d=new Date(v+'T00:00:00');d.setDate(d.getDate()+add);return String(Math.floor(d.getTime()/1000))};
 const since=day('audit-since',0),until=day('audit-until',1);if(since)p.set('since',since);if(until)p.set('until',until);
 if(before)p.set('before_id',String(before));
 return p;
}
const auditFiltered=()=>['audit-agent','audit-server','audit-action','audit-q','audit-since','audit-until'].some(id=>$(id).value);
function fillAuditSelect(id,values){
 const sel=$(id),old=sel.value;sel.replaceChildren(...[...sel.options].filter(o=>o.value===''||o.value==='none'));
 for(const [value,text] of values)sel.add(new Option(text,value));
 if([...sel.options].some(o=>o.value===old))sel.value=old;
}
function auditOutcome(r){const [label,cls]=AUDIT_OUTCOMES[r.outcome]||[r.outcome||'—','st-unknown'];
 return el('span','pill audit-outcome '+cls,label+(r.outcome==='failed'&&r.exit_code!=null?' · exit '+r.exit_code:''))}
const auditWho=r=>r.agent_name||(r.agent_id?'Agent '+r.agent_id.slice(0,8):r.actor||'—');
function auditRow(r){
 const tr=el('tr','audit-row');tr.dataset.id=r.id;tr.tabIndex=0;
 const time=el('td','audit-time'),t=el('time',null,when(r.time));t.dateTime=r.time||'';t.title=r.time?new Date(r.time).toLocaleString():'';time.append(t);
 const target=el('td','audit-target');
 if(r.detail){const code=el('code','audit-cmd',r.detail);target.append(code);
  if(r.detail.length>48||r.detail.includes('\n')){const x=el('button','audit-expand','▸');x.type='button';x.title='Show all of it';
   x.setAttribute('aria-label','Show the whole text');x.setAttribute('aria-expanded','false');
   x.onclick=e=>{e.stopPropagation();const on=!code.classList.contains('open');code.classList.toggle('open',on);x.textContent=on?'▾':'▸';x.setAttribute('aria-expanded',String(on))};
   target.append(x)}}
 else target.append(el('span','muted','digest '+r.digest+'…'));
 const out=el('td','audit-out');out.append(auditOutcome(r));
 const approver=r.approved_by||(r.action==='chat_approval'?r.actor:'')||'';
 tr.append(time,el('td','audit-agent',auditWho(r)),el('td','audit-server',r.server||'—'),el('td','audit-action',auditAction(r.action)),target,out);
 if(approver)out.append(el('div','audit-approver',approver));  // HLA_UI_V5: in the outcome cell (no extra column)
 tr.onclick=()=>openAuditDetail(r);tr.onkeydown=e=>{if(e.key==='Enter'&&e.target===tr)openAuditDetail(r)};
 return tr;
}
async function loadAudit(more){
 const seq=++auditSeq,body=$('audit-rows');
 let data;
 try{data=await api('audit?'+auditParams(more?auditNext:null))}
 catch(e){if(seq!==auditSeq)return;if(!more){const tr=el('tr','empty-row'),td=el('td','audit-error',e.message);td.colSpan=6;tr.append(td);body.replaceChildren(tr);
  $('audit-count').textContent='';$('audit-more').hidden=true}throw e}
 if(seq!==auditSeq)return;  // a newer filter change won
 if(data.facets){
  fillAuditSelect('audit-agent',data.facets.agents.map(a=>[a.id,a.name]));
  fillAuditSelect('audit-server',data.facets.servers.map(s=>[s,s]));
  fillAuditSelect('audit-action',data.facets.actions.map(a=>[a,auditAction(a)]).sort((a,b)=>a[1].localeCompare(b[1])));
 }
 if(!more){body.replaceChildren();auditCount=0}
 for(const r of data.rows)body.append(auditRow(r));
 auditCount+=data.rows.length;
 if(!more)renderAuditSummary(data.rows);
 if(!auditCount){const tr=el('tr','empty-row'),td=el('td',null,auditFiltered()?'No entries match these filters.':'Nothing in the audit log yet.');td.colSpan=6;tr.append(td);body.append(tr)}
 auditNext=data.next_before_id;$('audit-more').hidden=!auditNext;
 const active=['audit-agent','audit-server','audit-action','audit-q','audit-since','audit-until'].filter(id=>$(id).value).length;
 $('audit-filter-count').textContent=active?'('+active+' on)':'';
 $('audit-count').textContent=auditCount?auditCount+(auditCount===1?' entry':' entries')+', newest first'+(auditNext?' · more below':''):'';
}
// HLA_UI_V4: a summary of the newest entries above the log (what ran, what was refused or failed, approvals);
// Refused narrows the log to refusals. The full log stays below, every row opens its detail.
function renderAuditSummary(rows){
 const box=$('audit-summary');if(!box)return;box.replaceChildren();
 const filtered=auditFiltered(),n=rows.length;
 const ran=rows.filter(r=>/command|shell|unhinged|maintenance|diagnostic|^run$/.test(r.action)).length;
 const refused=rows.filter(r=>r.outcome==='denied'||r.action==='agent_denied').length;
 const failed=rows.filter(r=>['failed','error','timed_out'].includes(r.outcome)).length;
 const approvals=rows.filter(r=>r.approved_by||r.action==='chat_approval').length;
 const last=rows[0]&&rows[0].time;
 box.append(metric(filtered?'Matching entries':'Latest entries',String(n)+(auditNext?'+':''),last?'newest '+when(last):'nothing yet'),
  metric('Commands and diagnostics',String(ran),'run by agents and people'),
  metric('Refused',String(refused),refused?'needed an approval':'none',{tone:refused?'warn':null,onclick:refused&&[...$('audit-action').options].some(o=>o.value==='agent_denied')?()=>{$('audit-action').value='agent_denied';reloadAudit()}:null}),
  metric('Failed',String(failed),failed?'see the rows marked Failed':'none',{tone:failed?'bad':null}),
  metric('Approvals',String(approvals),'approved by a person'));
}
function openAuditDetail(r){
 $('audit-detail-title').textContent=auditAction(r.action)+(r.server?' · '+r.server:'');
 $('audit-detail-meta').textContent=(r.time?new Date(r.time).toLocaleString():'')+' · entry #'+r.id;
 const dl=$('audit-detail-facts');dl.replaceChildren();
 const add=(k,v)=>{if(v==null||v==='')return;const dd=el('dd');if(v instanceof Node)dd.append(v);else dd.textContent=String(v);dl.append(el('dt',null,k),dd)};
 add('Agent',r.agent_id?(r.agent_name||'Unknown agent')+' ('+r.agent_id.slice(0,8)+')':'none');
 add('Server or service',r.server);add('Action',auditAction(r.action)+' · '+r.action);add('Outcome',auditOutcome(r));
 add('Exit code',r.exit_code);if(r.timed_out)add('Timed out','yes');if(r.truncated)add('Output','truncated');
 add('Approved by',r.approved_by);add(r.action==='chat_approval'?'Approved by':'By',r.actor);add('Task',r.task_id);add('Digest',r.digest+'…');
 $('audit-detail-cmd').textContent=r.detail||'Not recorded: entries from before this gateway version keep only a digest of the command.';
 const links=$('audit-detail-links');links.replaceChildren();
 if(r.thread_id){const b=el('button','small','Open conversation');b.type='button';
  b.onclick=()=>{closeAuditDetail();openItem('thread',r.thread_id).catch(e=>notice(e.message))};links.append(b)}
 if(r.task_id&&r.task_known){const b=el('button','secondary small','Open task');b.type='button';b.onclick=()=>{closeAuditDetail();openTaskDetail(r.task_id)};links.append(b)}
 else if(r.task_id)links.append(el('span','muted','Its task no longer exists.'));
 auditLastFocus=document.activeElement;$('audit-detail').hidden=false;document.body.classList.add('drawer-open');
 for(const n of $('workspace').children)if(n.id!=='audit-detail')n.inert=true;
 $('audit-detail-close').focus();
}
function closeAuditDetail(){
 if($('audit-detail').hidden)return;
 $('audit-detail').hidden=true;document.body.classList.remove('drawer-open');for(const n of $('workspace').children)n.inert=false;
 if(auditLastFocus&&document.contains(auditLastFocus))auditLastFocus.focus();
}
$('workspace').append($('audit-detail'));
$('audit-detail-close').addEventListener('click',closeAuditDetail);
$('audit-detail-backdrop').addEventListener('click',closeAuditDetail);
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!$('audit-detail').hidden)closeAuditDetail()});
const reloadAudit=()=>loadAudit().catch(e=>notice(e.message));
for(const id of ['audit-agent','audit-server','audit-action','audit-since','audit-until'])$(id).addEventListener('change',reloadAudit);
$('audit-q').addEventListener('input',()=>{clearTimeout(auditTimer);auditTimer=setTimeout(reloadAudit,300)});
$('audit-filters').addEventListener('submit',e=>{e.preventDefault();clearTimeout(auditTimer);reloadAudit()});
$('audit-clear').addEventListener('click',()=>{for(const id of ['audit-agent','audit-server','audit-action','audit-q','audit-since','audit-until'])$(id).value='';reloadAudit()});
$('audit-refresh').addEventListener('click',reloadAudit);
$('audit-more').addEventListener('click',()=>whileBusy($('audit-more'),()=>loadAudit(true).catch(e=>notice(e.message))));
document.querySelector('button[data-view="audit"]').addEventListener('click',reloadAudit);
// Phones start with the filters folded away; wider screens always show them.
if(window.matchMedia('(max-width:600px)').matches)$('audit-filter-box').open=false;
window.matchMedia('(max-width:600px)').addEventListener('change',e=>{if(!e.matches)$('audit-filter-box').open=true});

// HLA_COSTS_V1: the Costs page. Spend per day (inline SVG bar chart), per kind of work, model, project and agent,
// the most expensive runs, project budgets, and which model each kind of work uses (models.py).
VIEW_TITLES.costs='Costs';
let costDays=Number(store.get('costDays'))||30,costData=null,costResizeTimer=null,routingData=null;
const money=v=>{const n=Number(v||0);return '$'+(n===0?'0.00':n<0.01?n.toFixed(5):n<1?n.toFixed(4):n.toFixed(2))};
const shortDay=iso=>new Date(iso+'T00:00:00Z').toLocaleDateString(undefined,{month:'short',day:'numeric',timeZone:'UTC'});
const SVG_NS='http://www.w3.org/2000/svg';
function svgEl(tag,attrs,text){const e=document.createElementNS(SVG_NS,tag);for(const [k,v] of Object.entries(attrs||{}))e.setAttribute(k,v);if(text!=null)e.textContent=text;return e}
// A round axis maximum: 1, 2 or 5 times a power of ten, at or above the largest day.
function niceMax(v){if(!(v>0))return 0.01;const p=Math.pow(10,Math.floor(Math.log10(v)));for(const m of [1,2,2.5,5,10])if(m*p>=v)return m*p;return 10*p}
function costChart(daily){
 const box=$('cost-chart');box.replaceChildren();
 const W=Math.max(280,box.clientWidth||600),H=W<500?180:220,left=W<500?44:56,right=8,top=12,bottom=26,n=daily.length;
 const max=niceMax(Math.max(...daily.map(d=>d.cost))),plotW=W-left-right,plotH=H-top-bottom,step=plotW/n,gap=n>45?1:2;
 const total=daily.reduce((s,d)=>s+d.cost,0),peak=daily.reduce((a,d)=>d.cost>a.cost?d:a,daily[0]);
 const svg=svgEl('svg',{viewBox:`0 0 ${W} ${H}`,width:W,height:H,role:'img','aria-labelledby':'cost-chart-title'});
 svg.append(svgEl('title',{id:'cost-chart-title'},`Daily spend over ${n} days: ${money(total)} in total`+(peak&&peak.cost>0?`, highest ${money(peak.cost)} on ${shortDay(peak.day)}.`:'.')));
 for(const f of [0,0.5,1]){const y=top+plotH-f*plotH;
  svg.append(svgEl('line',{x1:left,x2:W-right,y1:y,y2:y,class:f?'cost-grid-line':'cost-axis-line'}),
   svgEl('text',{x:left-6,y:y+4,'text-anchor':'end',class:'cost-axis-text'},money(max*f)))}
 const tip=el('div','cost-tip');tip.hidden=true;
 daily.forEach((d,i)=>{
  const x=left+i*step,bw=Math.max(1,step-gap),h=d.cost>0?Math.max(2,d.cost/max*plotH):0,y=top+plotH-h,r=Math.min(4,bw/2,h);
  const g=svgEl('g',{class:'cost-bar',tabindex:'-1'});
  g.append(svgEl('title',{},`${shortDay(d.day)}: ${money(d.cost)}, ${d.runs} run${d.runs===1?'':'s'}`));
  g.append(svgEl('rect',{x,y:top,width:step,height:plotH,class:'cost-hit'}));
  if(h)g.append(svgEl('path',{class:'cost-mark',d:`M${x+gap/2},${top+plotH}V${y+r}Q${x+gap/2},${y} ${x+gap/2+r},${y}H${x+gap/2+bw-r}Q${x+gap/2+bw},${y} ${x+gap/2+bw},${y+r}V${top+plotH}Z`}));
  const show=()=>{tip.hidden=false;tip.replaceChildren(el('strong',null,shortDay(d.day)),el('span',null,money(d.cost)+' · '+d.runs+' run'+(d.runs===1?'':'s')));
   const px=(x+step/2)/W*box.clientWidth;tip.style.left=Math.min(Math.max(px,60),box.clientWidth-60)+'px';g.classList.add('on')};
  g.addEventListener('mouseenter',show);g.addEventListener('mouseleave',()=>{tip.hidden=true;g.classList.remove('on')});
  g.addEventListener('click',show);
  svg.append(g);
 });
 // Day labels: first, last and a few evenly spaced in between.
 const labels=W<500?3:n<=10?n:6;
 for(let k=0;k<labels;k++){const i=labels===1?0:Math.round(k*(n-1)/(labels-1));
  svg.append(svgEl('text',{x:left+i*step+step/2,y:H-8,'text-anchor':k===0?'start':k===labels-1?'end':'middle',class:'cost-axis-text'},shortDay(daily[i].day)))}
 box.append(svg,tip);
}
function costTable(id,head,rows,cells,onRow){
 const t=$(id);t.replaceChildren();const thead=el('thead'),tr=el('tr');
 head.forEach((h,i)=>{const th=el('th',i?'num':null,h);tr.append(th)});thead.append(tr);
 const body=el('tbody');
 if(!rows.length){const r=el('tr','empty-row'),td=el('td',null,'Nothing in this period.');td.colSpan=head.length;r.append(td);body.append(r)}
 for(const row of rows){const r=el('tr');cells(row).forEach((c,i)=>{const td=el('td',i?'num':null);if(c instanceof Node)td.append(c);else td.textContent=c;
   if(!i&&typeof c==='string')td.title=c;r.append(td)});
  if(onRow){r.classList.add('linked');r.tabIndex=0;r.onclick=()=>onRow(row);r.onkeydown=e=>{if(e.key==='Enter')onRow(row)}}
  body.append(r)}
 t.append(thead,body);
}
function shareBar(part,whole,label){const w=el('div','cost-share');const b=el('span');b.style.width=(whole?Math.max(part>0?1:0,part/whole*100):0)+'%';
 w.append(b);w.setAttribute('aria-hidden','true');if(label)w.title=label;return w}
function renderCosts(d){
 costData=d;const t=d.totals;
 $('cost-range').textContent=`OpenRouter cost per UTC day, ${shortDay(d.since)} – ${shortDay(d.until)}`+(d.workspace?' · '+d.workspace:'')+'. TypeSafe is billed separately (tokens only).';
 const metric=(label,value,foot)=>{const m=el('div','metric');m.append(el('div','metric-label',label),el('div','metric-value',value),el('div','metric-foot',foot));return m};
 $('cost-totals').replaceChildren(metric('Spent',money(t.cost),'last '+d.days+' days'),metric('Runs',fmt(t.runs),money(t.avg_per_run)+' per run'),
  metric('Per day',money(t.avg_per_day),'average'),metric('Tokens',fmt(Number(t.input_tokens)+Number(t.output_tokens)),fmt(t.input_tokens)+' in · '+fmt(t.output_tokens)+' out'));
 costChart(d.daily);
 costTable('cost-daily-table',['Day','Runs','Cost'],[...d.daily].reverse(),r=>[shortDay(r.day),fmt(r.runs),money(r.cost)]);
 const kinds=$('cost-kinds');kinds.replaceChildren();
 if(!d.by_kind.length)kinds.append(el('p','muted','Nothing in this period.'));
 for(const k of d.by_kind){const row=el('div','cost-bar-row');
  row.append(el('span','cost-bar-label',k.label),shareBar(k.cost,t.cost),el('span','cost-bar-value',money(k.cost)),el('span','cost-bar-runs muted',fmt(k.runs)+' run'+(k.runs===1?'':'s')));
  kinds.append(row)}
 costTable('cost-models',['Model','Provider','Runs','Cost'],d.by_model,r=>[r.model,r.provider||'',fmt(r.runs),money(r.cost)]);
 costTable('cost-projects',['Project','Runs','Cost'],d.by_project,r=>[r.title,fmt(r.runs),money(r.cost)],r=>r.id&&openItem('project',r.id));
 costTable('cost-agents',['Agent','Runs','Cost'],d.by_agent,r=>[r.name,fmt(r.runs),money(r.cost)]);
 const kindLabel=k=>({conversation:'Conversation',project_task:'Project task',planner:'Planning',one_shot:'One-shot'})[k]||k;
 costTable('cost-top',['Run','When','Model','Cost'],d.top_runs,r=>{const c=el('div','cost-run');
   c.append(el('span','cost-run-text',r.text||'(no text)'),el('span','muted cost-run-meta',[kindLabel(r.kind),r.project,r.agent].filter(Boolean).join(' · ')));
   c.lastChild.append(el('span','cost-run-when',' · '+when(r.created_at)));
   return [c,when(r.created_at),r.model||'—',money(r.cost)]},r=>openTaskDetail(r.id));
 const budgets=$('cost-budgets');budgets.replaceChildren();
 if(!d.budgets.length)budgets.append(el('p','muted','No projects yet.'));
 for(const b of d.budgets){const level=b.pct>=100?'over':b.pct>=80?'warn':'ok';
  const row=el('button','cost-budget cost-budget-'+level);row.type='button';row.onclick=()=>openItem('project',b.id);
  const head=el('div','cost-budget-head');head.append(el('strong',null,b.title),pill(PROJECT_STATUS,b.status));
  const meter=el('div','cost-meter');meter.setAttribute('role','meter');meter.setAttribute('aria-valuemin','0');meter.setAttribute('aria-valuemax','100');
  meter.setAttribute('aria-valuenow',String(Math.min(100,b.pct)));meter.setAttribute('aria-label',b.title+' budget used');
  const fill=el('span');fill.style.width=Math.min(100,b.pct)+'%';meter.append(fill);
  const text=(level==='over'?'Budget reached · ':level==='warn'?'Over 80% · ':'')+Math.round(b.pct)+'% · '+money(b.spent)+' of '+money(b.budget);
  row.append(head,meter,el('span','cost-budget-text',text));budgets.append(row)}
}
async function loadCosts(){
 document.querySelectorAll('#costs .seg button').forEach(b=>b.setAttribute('aria-pressed',String(Number(b.dataset.days)===costDays)));
 const d=await api('costs?'+new URLSearchParams({days:costDays,workspace:$('cost-workspace').value}));
 renderCosts(d);
}
// Models per kind of work (on the Models page): global settings (default from the environment) and per-project overrides.
function modelSelect(current,options,label){const s=el('select');s.setAttribute('aria-label',label);
 for(const [v,t] of options)s.add(new Option(t,v));
 if(!options.some(([v])=>v===current))s.add(new Option(current+' (not enabled)',current));
 s.value=current;return s}
function renderRouting(){
 const d=routingData,box=$('model-routing');box.replaceChildren();
 for(const k of d.kinds){
  const def=k.env_value?'Default: '+k.env_value+' ('+k.env+')':'Default: agent\'s model';
  const opts=[['__default',def],['',"Agent's model"],...d.allowed.map(m=>[m,m])];
  const current=k.source==='dashboard'?k.model:'__default';
  const s=modelSelect(current,opts,k.label+' model');
  s.onchange=()=>whileBusy(s,async()=>{try{await api('model-routing',{method:'PUT',headers:UI_HEADERS,body:JSON.stringify({kind:k.kind,model:s.value==='__default'?null:s.value})});
   notice(k.label+': '+(s.value==='__default'?'back to the default':s.value||"the agent's model")+'.');await loadRouting()}catch(e){notice(e.message);renderRouting()}});
  const row=el('label','model-row');row.append(el('span',null,k.label),s);
  if(!k.usable)row.append(el('span','error fineprint',k.model+' is not enabled in the model registry; the agent\'s model is used.'));
  box.append(row)}
 const sel=$('model-project'),keep=sel.value||store.get('modelProject')||'';sel.replaceChildren(new Option('Pick a project…',''));
 for(const p of d.projects)sel.add(new Option(p.title+(d.overrides[p.id]?' •':''),p.id));
 sel.value=d.projects.some(p=>p.id===keep)?keep:'';renderProjectRouting();
}
function renderProjectRouting(){
 const d=routingData,pid=$('model-project').value,box=$('model-project-kinds');box.replaceChildren();store.set('modelProject',pid||null);
 if(!pid){box.append(el('p','muted','Overrides for one project: its planning, its tasks and its reviews.'));return}
 const set=d.overrides[pid]||{};
 for(const k of d.project_kinds){
  const s=modelSelect(set[k.kind]||'',[['','No override'],...d.allowed.map(m=>[m,m])],k.label+' model for this project');
  s.onchange=()=>whileBusy(s,async()=>{try{await api('model-routing/projects/'+pid,{method:'PUT',headers:UI_HEADERS,body:JSON.stringify({kind:k.kind,model:s.value||null})});
   notice(k.label+' for this project: '+(s.value||'no override')+'.');await loadRouting()}catch(e){notice(e.message);renderProjectRouting()}});
  const row=el('label','model-row');row.append(el('span',null,k.label),s);box.append(row)}
}
async function loadRouting(){routingData=await api('model-routing');renderRouting()}
$('model-project').addEventListener('change',renderProjectRouting);
document.querySelectorAll('#costs .seg button').forEach(b=>b.addEventListener('click',()=>{costDays=Number(b.dataset.days);store.set('costDays',costDays);
 whileBusy(b,()=>loadCosts().catch(e=>notice(e.message)))}));
$('cost-workspace').addEventListener('change',()=>loadCosts().catch(e=>notice(e.message)));
document.querySelector('button[data-view="costs"]').addEventListener('click',()=>loadCosts().catch(e=>notice(e.message)));
$('refresh').addEventListener('click',()=>{if(!$('costs').hidden)loadCosts().catch(()=>{})});
window.addEventListener('resize',()=>{clearTimeout(costResizeTimer);costResizeTimer=setTimeout(()=>{if(costData&&!$('costs').hidden)costChart(costData.daily)},150)});
// HLA_GITHUB_V1: pull requests instead of merging into main. Merge policy per project, PR badges on task cards,
// "PR ready to merge" in the inbox (prLink), and the allowed GitHub repositories on the Policy page.
// Direct is the default: PR mode needs the GitHub token in the gateway, the repository on the allowlist and a way for the
// sandbox to push branches (its deploy key is read-only). A write credential on the sandbox can push any branch.
const MERGE_POLICY={direct:'Direct: agents merge into the main branch themselves',pr:'Pull requests (needs setup): agents push a branch and open a PR; you merge'};
const PR_SETUP='Pull requests need the GitHub token in the gateway, the repository on the allowlist (Policy → GitHub) and a way for the sandbox to push branches (its deploy key is read-only). A write credential on the sandbox lets agents push any branch, so protect main with GitHub branch protection.';
function mergePolicyField(){
 let sel=$('project-merge');if(sel)return sel;
 sel=el('select');sel.id='project-merge';for(const [v,label] of Object.entries(MERGE_POLICY))sel.add(new Option(label,v));
 sel.value='direct';sel.title=PR_SETUP;
 const hint=el('span','fineprint merge-policy-hint',PR_SETUP);hint.hidden=true;sel.addEventListener('change',()=>{hint.hidden=sel.value!=='pr'});
 const l=el('label','merge-policy-field',null);l.append(document.createTextNode('Code changes'),sel,hint);
 $('project-parallel').closest('label').parentElement.after(l);$('project-form').addEventListener('reset',()=>{hint.hidden=true});return sel}
mergePolicyField();
function mergePolicyEl(p){
 const wrap=el('span','merge-policy');const policy=p.merge_policy||'direct';
 if(['running','completed','cancelled'].includes(p.status)){wrap.append(el('span','muted',' · '+(policy==='pr'?'pull requests':'direct merges')));return wrap}
 const sel=el('select');sel.setAttribute('aria-label','How code changes land');sel.title='How code changes land in the main branch';
 sel.add(new Option('pull requests','pr'));sel.add(new Option('direct merges','direct'));sel.value=policy;sel.title+='. '+PR_SETUP;
 sel.onchange=()=>whileBusy(sel,async()=>{try{await api('projects/'+p.id,{method:'PATCH',headers:UI_HEADERS,body:JSON.stringify({merge_policy:sel.value})});
  notice(sel.value==='pr'?'Tasks now open pull requests; you merge them.':'Tasks now merge into the main branch themselves.');await openProject(p.id)}
  catch(e){notice(e.message);sel.value=policy}});
 wrap.append(el('span','muted',' · '),sel);return wrap}
const PR_TONE={open:'badge-run',merged:'badge-sched',closed:'badge-warn'};
function prLink(url,label,cls){const a=el('a',cls||'badge',label);a.href=url;a.target='_blank';a.rel='noopener noreferrer';return a}
function prBadges(prs){return (prs||[]).map(r=>{
 const a=prLink(r.url,'PR #'+r.number+' · '+r.state+(r.checks==='failure'?' · checks failing':''),'badge '+(PR_TONE[r.state]||'badge-run'));
 a.title=r.repo+(r.title?': '+r.title:'');return a})}
function renderGithubRepos(){
 if(!policyData)return;
 let panel=$('github-panel');
 if(!panel){panel=el('div','panel github-panel');panel.id='github-panel';$('deploy-apps').closest('.panel').before(panel)}
 const canEdit=policyData.editable&&policyData.approver.allowed,repos=policyData.policy.github?.repos||{};
 panel.replaceChildren();
 const head=el('div','panel-heading'),txt=el('div');
 txt.append(el('h2',null,'GitHub repositories'),el('p','muted','Agents can open and read pull requests only in these repositories (service github on the left sets their level). Merging needs level 6 or unhinged mode, unless you allow it for a repository. The token stays in the gateway (Connections page, GITHUB_TOKEN, or GitHub/GITHUB_TOKEN in '+secretStoreName()+').'));
 // HLA_CONNECTIONS_V1: the token, its status and a picker of the repositories it reaches live on the Connections page.
 const toConn=el('a','conn-link','Token, connection test and repository picker: Connections →');toConn.href='#';toConn.id='github-to-connections';
 toConn.onclick=e=>{e.preventDefault();openView('connections')};txt.append(toConn);
 head.append(txt);panel.append(head);
 const list=el('div','deploy-apps');
 if(!Object.keys(repos).length)list.append(el('p','muted','No repositories allowed yet.'));
 for(const [repo,v] of Object.entries(repos)){
  const row=el('div','deploy-app'),info=el('div','deploy-info');
  info.append(el('strong',null,repo),el('div','muted',v.merge?'merges allowed from level 4':'agents open PRs; you merge'));row.append(info);
  if(canEdit){const acts=el('div','deploy-actions');
   const tog=el('button','secondary small',v.merge?'Disallow merges':'Allow merges');tog.type='button';
   tog.onclick=()=>whileBusy(tog,async()=>{if(!v.merge&&!await uiConfirm('Let agents at level 4 merge pull requests in '+repo+'?',{title:'Allow merges',ok:'Allow merges'}))return;
    try{await putPolicy({github:{repos:{[repo]:{merge:!v.merge}}}});notice(repo+': merges '+(v.merge?'disallowed.':'allowed.'))}catch(e){notice(e.message)}});
   const rm=el('button','ghost small','Remove');rm.type='button';
   rm.onclick=()=>whileBusy(rm,async()=>{if(!await uiConfirm('Remove '+repo+'? Agents can no longer reach its pull requests.',{title:'Remove repository',ok:'Remove repository',danger:true}))return;
    try{await putPolicy({github:{repos:{[repo]:null}}});notice(repo+' removed.')}catch(e){notice(e.message)}});
   acts.append(tog,rm);row.append(acts)}
  list.append(row)}
 panel.append(list);
 if(canEdit){const form=el('form','deploy-form github-form');const inp=el('input');inp.placeholder='owner/name, e.g. your-org/site';inp.id='github-repo';
  inp.setAttribute('aria-label','Repository');inp.pattern='[A-Za-z0-9_.\\-]+/[A-Za-z0-9_.\\-]+';inp.required=true;
  const add=el('button','secondary small','Allow repository');add.type='submit';form.append(inp,add);
  form.onsubmit=e=>{e.preventDefault();whileBusy(add,async()=>{try{await putPolicy({github:{repos:{[inp.value.trim()]:{merge:false}}}});notice(inp.value.trim()+' allowed.')}catch(err){notice(err.message)}})};
  panel.append(form)}
}

// ── HLA_CONNECTIONS_V1: the Connections page. Credentials, GitHub and HTTP tools, all held and used by the SSH gateway.
// Values typed here go to the gateway's write-only store once (connections.py) and are never shown again.
VIEW_TITLES.connections='Connections';
SERVICE_LEVELS.http_tools='HTTP tools from the Connections page: an agent gets every tool whose own level it reaches (default 1 for GET, 4 for writes; tools marked "only level 6" need 6 or unhinged mode).';
let connData=null,credData=null,ghStatus=null,ghRepos=null,htData=null,htEditing=null;
const CRED_KINDS={token:'Token',api_key:'API key',password:'Password',header:'Header value',other:'Other'};
const GH_SOURCES={store:'the credential store (this page)',env:'.env.gateway (GITHUB_TOKEN)',infisical:'Infisical (/GitHub)',
 get secret_store(){return (SECRET_BACKEND==='infisical'?'Infisical':'the secret store')+' (GitHub/GITHUB_TOKEN)'}};
function connCanEdit(){return !!(connData&&connData.editable&&connData.approver?.allowed)}
function connWhen(ts){return ts?when(new Date(ts*1000)):'—'}
async function loadConnections(){
 const [c,cr,ht]=await Promise.all([api('connections'),api('credentials'),api('http-tools')]);
 connData=c;credData=cr;htData=ht;noteSecretBackend(c?.secret_store);
 if(!policyData)await loadPolicy().catch(()=>{});
 renderConnNote();renderCredentials();renderHttpTools();renderGithubLevel();
 await loadGithubStatus().catch(e=>{ghStatus={configured:null,ok:false,detail:e.message};renderGithubStatus()});
}
function renderConnNote(){
 const notes=[];
 if(connData.setup)notes.push(connData.setup+'.');
 if(connData.editable&&!connData.approver?.allowed)notes.push(connData.approver.reason);
 $('conn-note').hidden=!notes.length;$('conn-note').textContent=notes.join(' ');
 const can=connCanEdit(),store=can&&credData.configured;
 $('cred-add').hidden=!store;$('gh-token').hidden=!store;$('ht-new').hidden=!can;
}
// Credentials: names and metadata only. "Replace" opens the form with the name filled in.
function renderCredentials(){
 const box=$('cred-list');box.replaceChildren();
 if(credData.setup&&!credData.credentials.length){box.append(el('p','muted',credData.setup+'.'));return}
 if(!credData.credentials.length){box.append(el('p','muted','No credentials stored yet.'));return}
 const used={};for(const t of htData?.tools||[])if(t.auth)(used[t.auth.credential]??=[]).push(t.name);
 for(const c of credData.credentials){
  const row=el('div','deploy-app conn-item'),info=el('div','deploy-info');
  const title=el('div','conn-title');title.append(el('strong',null,c.name),el('span','pill conn-kind',CRED_KINDS[c.kind]||c.kind));
  const uses=[c.name==='GITHUB_TOKEN'?'GitHub':null,...(used[c.name]||[]).map(n=>'tool '+n)].filter(Boolean);
  info.append(title);if(c.description)info.append(el('div','muted',c.description));
  info.append(el('div','muted conn-meta','fingerprint '+c.fingerprint+' · updated '+connWhen(c.updated_at)+(c.updated_by?' by '+c.updated_by:'')+(uses.length?' · used by '+uses.join(', '):'')));
  row.append(info);
  if(connCanEdit()&&credData.configured){
   const acts=el('div','deploy-actions');
   const rep=el('button','secondary small','Replace');rep.type='button';rep.onclick=()=>credForm(c);
   const del=el('button','ghost small','Delete');del.type='button';
   del.onclick=()=>whileBusy(del,async()=>{
    if(!await uiConfirm('Delete '+c.name+'? '+(uses.length?'It is used by '+uses.join(', ')+', which will stop working. ':'')+'This cannot be undone.',{title:'Delete credential',ok:'Delete credential',danger:true}))return;
    try{await api('credentials/'+encodeURIComponent(c.name),{method:'DELETE',headers:UI_HEADERS});notice(c.name+' deleted.');await reloadCredentials()}catch(e){notice(e.message)}});
   acts.append(rep,del);row.append(acts)}
  box.append(row)}
}
function credForm(c){
 const d=$('cred-add');d.open=true;$('cred-name').value=c?c.name:'';$('cred-name').readOnly=!!c;
 $('cred-kind').value=c?c.kind:'token';$('cred-desc').value=c?c.description||'':'';$('cred-value').value='';
 d.querySelector('summary').textContent=c?'Replace '+c.name:'Add a credential';
 d.querySelector('button[type=submit]').textContent=c?'Replace value':'Save credential';
 (c?$('cred-value'):$('cred-name')).focus();
}
async function reloadCredentials(){credData=await api('credentials');renderCredentials();fillCredentialPicker()}
async function storeCredential(name,body){
 return api('credentials/'+encodeURIComponent(name),{method:'PUT',headers:UI_HEADERS,body:JSON.stringify(body)});
}
$('cred-add').addEventListener('toggle',()=>{if(!$('cred-add').open){$('cred-name').readOnly=false;$('cred-add').querySelector('summary').textContent='Add a credential';
 $('cred-add').querySelector('button[type=submit]').textContent='Save credential'}});
$('cred-form').addEventListener('submit',e=>{e.preventDefault();const btn=e.submitter||$('cred-form').querySelector('button[type=submit]');
 whileBusy(btn,async()=>{
  const name=$('cred-name').value.trim(),replacing=credData.credentials.some(c=>c.name===name);
  if(replacing&&!$('cred-name').readOnly&&!await uiConfirm(name+' exists. Replace its value? The old value is gone for good.',{title:'Replace credential',ok:'Replace value',danger:true}))return;
  try{await storeCredential(name,{value:$('cred-value').value,kind:$('cred-kind').value,description:$('cred-desc').value.trim()});
   $('cred-value').value='';$('cred-form').reset();$('cred-add').open=false;notice(name+(replacing?' replaced.':' stored.')+' The value is encrypted in the gateway and will not be shown again.');
   await reloadCredentials();if(name==='GITHUB_TOKEN')loadGithubStatus().catch(()=>{})}
  catch(err){notice(err.message)}})});

// GitHub: where the token comes from and whose it is, the github service level, and the repository allowlist.
async function loadGithubStatus(){ghStatus=await api('github/status');renderGithubStatus();
 if(ghStatus.ok&&!ghRepos)await loadGithubRepos().catch(e=>renderGithubRepoError(e.message));else renderGithubRepoPicker()}
function renderGithubStatus(){
 const box=$('gh-status');box.replaceChildren();const s=ghStatus||{};
 const line=el('div','gh-line'),pill=el('span','pill '+(s.ok?'st-completed':s.configured===false?'st-rejected':'st-failed'),s.ok?'Connected':s.configured===false?'No token':s.configured==null&&s.setup?'Not available':'Problem');
 line.append(pill);
 if(s.ok)line.append(el('span',null,'Signed in as '),el('strong',null,s.login+(s.name&&s.name!==s.login?' ('+s.name+')':'')));
 box.append(line);
 const facts=[];
 if(s.source)facts.push('Token from '+(GH_SOURCES[s.source]||s.source)+'.');
 if(s.ok)facts.push(s.token_type==='classic'?'Classic token, scopes: '+((s.scopes||[]).join(', ')||'none')+'.':s.token_type==='fine-grained'?'Fine-grained token: access is set per repository on GitHub.':'Token type unknown.');
 if(!s.ok&&(s.detail||s.setup))facts.push(s.detail||s.setup);
 if(s.configured===false)facts.push('Add a token below (or GITHUB_TOKEN in .env.gateway / GitHub/GITHUB_TOKEN in '+secretStoreName()+').');
 if(facts.length)box.append(el('p','muted',facts.join(' ')));
 $('gh-token-summary').textContent=s.source==='store'?'Replace the GitHub token':'Set the GitHub token';
}
function renderGithubLevel(){
 const sel=$('gh-level');sel.replaceChildren();const svc=policyData?.policy?.services?.github;
 if(!svc){sel.disabled=true;return}
 for(const [n,label] of [[0,'0 · off'],[1,'1 · read PR status'],[4,'4 · open PRs and comment'],[6,'6 · merge']])sel.add(new Option(label,n));
 if(![0,1,4,6].includes(svc.level))sel.add(new Option(svc.level+' · '+(policyData.policy.levels[svc.level]||''),svc.level));
 sel.value=String(svc.level);sel.disabled=!(policyData.editable&&policyData.approver.allowed);
}
$('gh-level').addEventListener('change',()=>{const sel=$('gh-level'),level=Number(sel.value);
 whileBusy(sel,async()=>{try{await putPolicy({servers:{github:{level}}});notice('GitHub level set to '+level+'.')}catch(e){notice(e.message)}renderGithubLevel()})});
async function loadGithubRepos(){ghRepos=await api('github/repos');renderGithubRepoPicker()}
function renderGithubRepoError(text){const box=$('gh-repos');box.replaceChildren(el('p','muted','Repositories cannot be listed: '+text))}
function renderGithubRepoPicker(){
 const box=$('gh-repos');box.replaceChildren();
 const allow=policyData?.policy?.github?.repos||{},can=policyData&&policyData.editable&&policyData.approver.allowed;
 const listed=new Map((ghRepos?.repos||[]).map(r=>[r.repo,r]));
 // Allowed repositories the token cannot see (renamed, removed, other token) stay visible so they can be removed.
 for(const [repo,v] of Object.entries(allow))if(!listed.has(repo))listed.set(repo,{repo,full_name:repo,missing:true,allowed:true,merge:!!v.merge});
 const q=$('gh-repo-filter').value.trim().toLowerCase();$('gh-repo-filter').hidden=listed.size<8;
 const rows=[...listed.values()].sort((a,b)=>(allow[b.repo]?1:0)-(allow[a.repo]?1:0)||a.repo.localeCompare(b.repo));
 if(!ghRepos&&!rows.length){box.append(el('p','muted',ghStatus?.ok?'Loading repositories…':'Connect a token to choose repositories.'));return}
 if(!rows.length){box.append(el('p','muted','The token reaches no repositories.'));return}
 let shown=0;
 for(const r of rows){
  if(q&&!r.repo.includes(q))continue;shown++;
  const on=!!allow[r.repo],merge=!!allow[r.repo]?.merge;
  const row=el('div','gh-repo'+(on?' on':''));
  const lab=el('label','check gh-repo-name'),cb=el('input');cb.type='checkbox';cb.checked=on;cb.disabled=!can;cb.dataset.repo=r.repo;
  lab.append(cb,el('span',null,r.full_name||r.repo));
  const meta=[r.missing?'not visible to the token':null,r.private?'private':null,r.archived?'archived':null,r.missing?null:r.push?'push':'read only'].filter(Boolean);
  row.append(lab,el('span','muted gh-repo-meta',meta.join(' · ')));
  cb.onchange=()=>whileBusy(cb,async()=>{
   try{await putPolicy({github:{repos:{[r.repo]:cb.checked?{merge:false}:null}}});notice(r.repo+(cb.checked?' allowed.':' removed from the allowlist.'))}
   catch(e){notice(e.message)}renderGithubRepoPicker()});
  if(on){const m=el('label','check gh-merge'),mc=el('input');mc.type='checkbox';mc.checked=merge;mc.disabled=!can;m.append(mc,el('span',null,'allow merges'));
   m.title='Let agents at level 4 merge pull requests in this repository (otherwise level 6 or unhinged mode)';
   mc.onchange=()=>whileBusy(mc,async()=>{
    if(mc.checked&&!await uiConfirm('Let agents at level 4 merge pull requests in '+r.repo+'?',{title:'Allow merges',ok:'Allow merges'})){mc.checked=false;return}
    try{await putPolicy({github:{repos:{[r.repo]:{merge:mc.checked}}}});notice(r.repo+': merges '+(mc.checked?'allowed.':'disallowed.'))}catch(e){notice(e.message)}renderGithubRepoPicker()});
   row.append(m)}
  box.append(row)}
 if(!shown)box.append(el('p','muted','No repository matches.'));
 if(ghRepos?.truncated)box.append(el('p','fineprint','Showing the first 300 repositories the token can reach.'));
}
$('gh-repo-filter').addEventListener('input',renderGithubRepoPicker);
$('gh-test').addEventListener('click',()=>whileBusy($('gh-test'),async()=>{
 ghRepos=null;$('gh-status').replaceChildren(el('p','muted','Checking…'));
 try{await loadGithubStatus();notice(ghStatus.ok?'GitHub answered: signed in as '+ghStatus.login+'.':'GitHub connection: '+(ghStatus.detail||ghStatus.error||'not configured'))}
 catch(e){notice(e.message)}}));
$('gh-token-form').addEventListener('submit',e=>{e.preventDefault();const btn=$('gh-token-form').querySelector('button[type=submit]');
 whileBusy(btn,async()=>{try{await storeCredential('GITHUB_TOKEN',{value:$('gh-token-value').value,kind:'token',description:'GitHub pull requests (Connections page)'});
  $('gh-token-value').value='';$('gh-token').open=false;ghRepos=null;notice('GitHub token stored in the gateway.');await reloadCredentials();await loadGithubStatus()}
  catch(err){notice(err.message)}})});
$('gh-policy-link').addEventListener('click',e=>{e.preventDefault();openView('policy');setTimeout(()=>$('github-panel')?.scrollIntoView({block:'start'}),300)});

// HTTP tools: list, form (parameters as rows), Test with sample parameters.
const HT_TYPES=['string','integer','number','boolean'];
function renderHttpTools(){
 const box=$('ht-list');box.replaceChildren();
 if(htData.setup){box.append(el('p','muted',htData.setup+'.'));return}
 if(!htData.tools.length){box.append(el('p','muted','No HTTP tools yet. A tool is one API request shape, e.g. GET https://api.example.com/v1/cities/{city}/now.'));return}
 const svc=policyData?.policy?.services?.http_tools;
 for(const t of htData.tools){
  const row=el('div','deploy-app conn-item ht-item'),info=el('div','deploy-info');
  const title=el('div','conn-title');title.append(el('strong',null,t.name),el('span','pill conn-kind',t.method),el('span','pill conn-kind','level '+(t.approval_required?'6':t.level)));
  if(!t.enabled)title.append(el('span','pill st-rejected','disabled'));
  if(t.allow_private)title.append(el('span','pill st-failed','private network'));
  info.append(title,el('div','muted',t.description));
  const code=el('code',null,t.base_url+t.path+(t.params.some(p=>p.in==='query')?'?'+t.params.filter(p=>p.in==='query').map(p=>p.name).join('&'):''));info.append(code);
  const facts=[t.auth?'credential '+t.auth.credential+(t.credential_stored?'':' (not stored yet)'):'no credential',
   t.params.length?t.params.length+' parameter'+(t.params.length>1?'s':''):'no parameters','timeout '+t.timeout+' s','max '+Math.round(t.max_response/1024)+' KB'];
  info.append(el('div','muted conn-meta',facts.join(' · ')));
  row.append(info);
  const acts=el('div','deploy-actions');
  if(connCanEdit()){
   const test=el('button','secondary small','Test');test.type='button';test.onclick=()=>toggleHttpTest(row,t);
   const edit=el('button','secondary small','Edit');edit.type='button';edit.onclick=()=>httpForm(t);
   const del=el('button','ghost small','Delete');del.type='button';
   del.onclick=()=>whileBusy(del,async()=>{if(!await uiConfirm('Delete the HTTP tool '+t.name+'? Agents lose it at once.',{title:'Delete HTTP tool',ok:'Delete tool',danger:true}))return;
    try{await api('http-tools/'+t.name,{method:'DELETE',headers:UI_HEADERS});notice(t.name+' deleted.');await reloadHttpTools()}catch(e){notice(e.message)}});
   acts.append(test,edit,del)}
  row.append(acts);box.append(row)}
 if(svc&&svc.level<1)box.append(el('p','fineprint','The http_tools service is at level 0 on the Policy page, so no agent gets these tools.'));
}
async function reloadHttpTools(){htData=await api('http-tools');renderHttpTools();renderCredentials()}
function toggleHttpTest(row,t){
 let box=row.querySelector('.ht-test');if(box){box.remove();return}
 box=el('form','ht-test');const fields=el('div','ht-grid');
 for(const p of t.params){
  const l=el('label',null,p.name+(p.required?' *':'')+' ('+p.in+', '+p.type+')');let input;
  if(p.enum){input=el('select');if(!p.required)input.add(new Option('',''));for(const v of p.enum)input.add(new Option(String(v),String(v)))}
  else if(p.type==='boolean'){input=el('select');for(const v of ['','true','false'])input.add(new Option(v,v))}
  else{input=el('input');input.type=p.type==='string'?'text':'number';if(p.type==='integer')input.step='1';if(p.type==='number')input.step='any';input.placeholder=p.description||''}
  input.dataset.param=p.name;input.dataset.type=p.type;input.required=!!p.required;l.append(input);fields.append(l)}
 if(!t.params.length)fields.append(el('p','muted','This tool takes no parameters.'));
 const out=el('div','ht-result');out.hidden=true;
 const go=el('button','small','Send test request');go.type='submit';const acts=el('div','form-actions');acts.append(el('span','fineprint','Runs once with the gateway admin token (no agent level needed), through the same SSRF checks, and is audited.'),go);
 box.append(fields,acts,out);row.append(box);
 box.onsubmit=e=>{e.preventDefault();whileBusy(go,async()=>{
  const params={};
  for(const i of box.querySelectorAll('[data-param]')){if(i.value==='')continue;const ty=i.dataset.type;
   params[i.dataset.param]=ty==='boolean'?i.value==='true':ty==='integer'?parseInt(i.value,10):ty==='number'?Number(i.value):i.value}
  out.hidden=false;out.replaceChildren(el('p','muted','Sending…'));
  try{const r=await api('http-tools/'+t.name+'/test',{method:'POST',headers:UI_HEADERS,body:JSON.stringify({params})});
   out.replaceChildren();
   if(r.ok===false&&r.error){out.append(el('p','error',r.error+(r.detail?': '+r.detail:'')));return}
   const head=el('div','gh-line');head.append(el('span','pill '+(r.ok?'st-completed':'st-failed'),'HTTP '+r.http_status),el('span','muted',(r.content_type||'')+' · '+r.seconds+' s'+(r.truncated?' · truncated':'')));
   out.append(head);if(r.note)out.append(el('p','fineprint',r.note));
   out.append(el('pre','ht-preview',r.preview+(r.preview_truncated?'\n…':'')))}
  catch(err){out.replaceChildren(el('p','error',err.message))}})};
 box.querySelector('input,select')?.focus();
}
function fillCredentialPicker(){
 const sel=$('ht-cred'),old=sel.value;sel.replaceChildren(new Option('None',''));
 for(const c of credData?.credentials||[])sel.add(new Option(c.name+' ('+(CRED_KINDS[c.kind]||c.kind)+')',c.name));
 if(old&&![...sel.options].some(o=>o.value===old))sel.add(new Option(old+' (not stored)',old));
 sel.value=old;
}
function htLevelOptions(){const sel=$('ht-level');if(sel.options.length)return;
 for(const [n,label] of [[1,'1 · reads (default for GET)'],[2,'2'],[3,'3'],[4,'4 · changes (default for writes)'],[5,'5'],[6,'6 · free shell only']])sel.add(new Option(label,n))}
function paramRow(p){
 p=p||{name:'',in:'query',type:'string',required:false,description:''};
 const row=el('div','ht-param');
 const name=el('input');name.placeholder='name';name.value=p.name;name.pattern='[a-z][a-z0-9_]{0,39}';name.dataset.f='name';name.setAttribute('aria-label','Parameter name');
 const where=el('select');for(const v of ['path','query','body'])where.add(new Option(v,v));where.value=p.in;where.dataset.f='in';where.setAttribute('aria-label','Where it goes');
 const type=el('select');for(const v of HT_TYPES)type.add(new Option(v,v));type.value=p.type;type.dataset.f='type';type.setAttribute('aria-label','Type');
 const req=el('label','check');const rc=el('input');rc.type='checkbox';rc.checked=!!p.required;rc.dataset.f='required';req.append(rc,el('span',null,'required'));
 const desc=el('input');desc.placeholder='description for the model';desc.value=p.description||'';desc.maxLength=200;desc.dataset.f='description';desc.setAttribute('aria-label','Description');
 const en=el('input');en.placeholder='allowed values, comma-separated (optional)';en.value=(p.enum||[]).join(', ');en.dataset.f='enum';en.setAttribute('aria-label','Allowed values');
 const rm=el('button','ghost small','Remove');rm.type='button';rm.onclick=()=>row.remove();
 row.append(name,where,type,req,desc,en,rm);$('ht-params').append(row);
}
function httpForm(t){
 htEditing=t?t.name:null;htLevelOptions();fillCredentialPicker();
 const f=$('ht-form');f.hidden=false;f.reset();$('ht-params').replaceChildren();
 $('ht-form-title').textContent=t?'Edit '+t.name:'New HTTP tool';$('ht-name').readOnly=!!t;
 $('ht-name').value=t?t.name:'';$('ht-method').value=t?t.method:'GET';$('ht-desc').value=t?t.description:'';
 $('ht-base').value=t?t.base_url:'';$('ht-path').value=t?t.path:'/';
 for(const p of t?.params||[])paramRow(p);
 $('ht-cred').value=t?.auth?.credential||'';if(t?.auth&&!$('ht-cred').value){fillCredentialPicker();$('ht-cred').add(new Option(t.auth.credential+' (not stored)',t.auth.credential));$('ht-cred').value=t.auth.credential}
 $('ht-auth-in').value=t?.auth?.in||'header';$('ht-auth-name').value=t?.auth?.name||'';$('ht-auth-prefix').value=t?.auth?t.auth.prefix:'';
 $('ht-headers').value=Object.entries(t?.headers||{}).map(([k,v])=>k+': '+v).join('\n');
 $('ht-level').value=String(t?t.level:1);$('ht-timeout').value=t?t.timeout:15;$('ht-max').value=t?Math.round(t.max_response/1024):64;
 $('ht-approval').checked=!!t?.approval_required;$('ht-private').checked=!!t?.allow_private;$('ht-enabled').checked=t?t.enabled!==false:true;
 htAuthHints();f.scrollIntoView({block:'start',behavior:'smooth'});(t?$('ht-desc'):$('ht-name')).focus({preventScroll:true});
}
function htAuthHints(){const q=$('ht-auth-in').value==='query';$('ht-auth-name').placeholder=q?'api_key':'Authorization';$('ht-auth-prefix').placeholder=q?'':'Bearer '}
$('ht-auth-in').addEventListener('change',htAuthHints);
// New GET tools need level 1, writes 4 (the gateway's defaults), until the owner picks another level.
$('ht-method').addEventListener('change',()=>{if(!htEditing)$('ht-level').value=$('ht-method').value==='GET'?'1':'4'});
$('ht-new').addEventListener('click',()=>httpForm(null));
$('ht-param-add').addEventListener('click',()=>paramRow(null));
$('ht-cancel').addEventListener('click',()=>{$('ht-form').hidden=true;htEditing=null});
function httpDefinition(){
 const params=[...$('ht-params').querySelectorAll('.ht-param')].map(r=>{const g=f=>r.querySelector('[data-f="'+f+'"]');
  const type=g('type').value,p={name:g('name').value.trim(),in:g('in').value,type,required:g('required').checked,description:g('description').value.trim()};
  const vals=g('enum').value.split(',').map(x=>x.trim()).filter(Boolean);
  if(vals.length)p.enum=vals.map(v=>type==='integer'?parseInt(v,10):type==='number'?Number(v):type==='boolean'?v==='true':v);
  return p}).filter(p=>p.name);
 const headers={};
 for(const line of $('ht-headers').value.split('\n')){const i=line.indexOf(':');if(i>0)headers[line.slice(0,i).trim()]=line.slice(i+1).trim()}
 const cred=$('ht-cred').value,where=$('ht-auth-in').value;
 const auth=cred?{credential:cred,in:where,name:$('ht-auth-name').value.trim()||(where==='header'?'Authorization':''),
  prefix:$('ht-auth-prefix').value!==''?$('ht-auth-prefix').value:(where==='header'&&!$('ht-auth-name').value.trim()?'Bearer ':'')}:null;
 return {description:$('ht-desc').value.trim(),method:$('ht-method').value,base_url:$('ht-base').value.trim(),path:$('ht-path').value.trim()||'/',
  params,headers,auth,level:Number($('ht-level').value),timeout:Number($('ht-timeout').value),max_response:Math.round(Number($('ht-max').value)*1024),
  approval_required:$('ht-approval').checked,allow_private:$('ht-private').checked,enabled:$('ht-enabled').checked};
}
$('ht-form').addEventListener('submit',e=>{e.preventDefault();const btn=$('ht-form').querySelector('button[type=submit]');
 whileBusy(btn,async()=>{
  const name=$('ht-name').value.trim(),def=httpDefinition();
  if(!htEditing&&htData.tools.some(t=>t.name===name)&&!await uiConfirm(name+' exists. Replace it?',{title:'Replace HTTP tool',ok:'Replace tool',danger:true}))return;
  if(def.allow_private&&!await uiConfirm('Allow '+name+' to reach private network addresses? Use this only for your own internal APIs.',{title:'Allow private addresses',ok:'Allow private addresses',danger:true}))return;
  try{await api('http-tools/'+encodeURIComponent(name),{method:'PUT',headers:UI_HEADERS,body:JSON.stringify(def)});
   $('ht-form').hidden=true;htEditing=null;notice(name+' saved.'+(def.auth&&!credData.credentials.some(c=>c.name===def.auth.credential)?' Store the credential '+def.auth.credential+' above before it can be used.':''));
   await reloadHttpTools()}
  catch(err){notice(err.message)}})});
document.querySelector('button[data-view="connections"]').addEventListener('click',()=>loadConnections().catch(e=>notice(e.message)));
$('refresh').addEventListener('click',()=>{if(!$('connections').hidden){ghRepos=null;loadConnections().catch(()=>{})}});
// ── HLA_MODEL_REGISTRY_V1: the Models page ──
// The registry (model_registry.py): which models agents may use, the default model, OpenRouter's catalog to add
// from, a tiny test call per model, and the routing panel ("Models per kind of work", models.py) below them.
VIEW_TITLES.models='Models';
let modelsData=null,modelsCatalogTimer=null,modelsCatalogSeq=0,modelsSource='openrouter';  // HLA_XAI_V1: catalog source
const modelProvider=id=>String(id||'').startsWith('xai/')?'xAI':'OpenRouter';
const modelsTests={};  // id -> last test result, kept across re-renders
const modelPath=id=>'models/'+id.split('/').map(encodeURIComponent).join('/');
const modelPrice=v=>v==null?'—':Number(v)===0?'free':'$'+(Number(v)>=0.1?Number(v).toFixed(2):String(parseFloat(Number(v).toFixed(4))));
const modelContext=n=>!n?'—':n>=1e6?(Math.round(n/1e5)/10)+'M':n>=1000?Math.round(n/1000)+'K':String(n);
function modelUsedBy(list){
 const wrap=el('div','model-used');if(!list.length){wrap.append(el('span','muted','—'));return wrap}
 const agents=list.filter(u=>u.type==='agent'),rest=list.filter(u=>u.type!=='agent');
 if(agents.length){const a=el('span','badge badge-run',agents.length+' agent'+(agents.length===1?'':'s'));a.title=agents.map(u=>u.name+(u.enabled?'':' (disabled)')).join(', ');wrap.append(a)}
 for(const u of rest){const b=el('span','badge '+(u.type==='default'?'badge-sched':u.type==='env'?'model-badge-env':'badge-run'),
  u.type==='default'?'default':u.type==='project'?'project: '+u.name:u.type==='env'?u.env:u.text.replace(/^routing: /,''));b.title=u.text;wrap.append(b)}
 return wrap}
function modelTestLine(id){const r=modelsTests[id];if(!r)return null;
 const line=el('div','model-test '+(r.ok?'ok':'bad'));
 line.textContent=r.ok?'OK · '+r.latency_ms+' ms · “'+r.reply+'”'+(r.cost!=null?' · $'+Number(r.cost).toFixed(6):''):'Failed · '+(r.error||'failed')+(r.latency_ms!=null?' ('+r.latency_ms+' ms)':'');
 return line}
function renderModels(){
 const d=modelsData;if(!d)return;
 const ap=$('models-approver');ap.hidden=d.approver.allowed;ap.textContent=d.approver.allowed?'':'Adding, enabling, disabling or removing models and changing the default need an approver: '+(d.approver.reason||'');
 // Default model.
 const sel=$('models-default');sel.replaceChildren();const envModel=d.env.worker_model;
 sel.add(new Option(envModel?envModel+' (environment default)':'First enabled model (no default set)',''));
 for(const m of d.registry.filter(m=>m.enabled))sel.add(new Option((m.label?m.label+' · ':'')+m.id,m.id));
 sel.value=d.default.source==='dashboard'?d.default.model:'';
 $('models-default-hint').textContent='Now: '+(d.default.model||'none')+(d.default.source==='dashboard'?' (set here)':d.default.source==='env'?' (from OPENROUTER_WORKER_MODEL)':d.default.source==='registry'?' (first enabled model)':'')+
  '. Used by runs without an agent or override, and when routing falls back.';
 // Registry table (cards on phones).
 const t=$('models-table');t.replaceChildren();
 const thead=el('thead'),hr=el('tr');for(const h of ['Model','Context','In / 1M','Out / 1M','Tools','Enabled','Used by',''])hr.append(el('th',null,h));thead.append(hr);
 const body=el('tbody');
 if(!d.registry.length){const r=el('tr','empty-row'),td=el('td',null,'The registry is empty: AGENT_ALLOWED_MODELS from the environment applies ('+(d.env.allowed.join(', ')||'none')+').');td.colSpan=8;r.append(td);body.append(r)}
 for(const m of d.registry){
  const tr=el('tr',m.enabled?null:'model-off');tr.dataset.model=m.id;
  const name=el('td','model-name');name.dataset.label='Model';
  name.append(el('strong',null,m.label||m.id),m.label&&m.label!==m.id?el('code',null,m.id):'',el('span','badge model-provider'+(modelProvider(m.id)==='xAI'?' model-provider-xai':''),modelProvider(m.id)));
  if(m.notes)name.append(el('div','fineprint model-notes',m.notes));
  const tl=modelTestLine(m.id);if(tl)name.append(tl);
  const cell=(label,text,cls)=>{const td=el('td',cls||'num',text);td.dataset.label=label;return td};
  const tools=cell('Tools',m.supports_tools==null?'Unknown':m.supports_tools?'Yes':'No','model-tools '+(m.supports_tools===false?'no':m.supports_tools?'yes':''));
  tools.title=m.supports_tools==null?'Unknown: not in the catalog data':m.supports_tools?'Supports tool calls':'No tool calls: agents with server tools need them';
  const en=el('td','model-enabled');en.dataset.label='Enabled';
  const box=el('input');box.type='checkbox';box.checked=m.enabled;box.setAttribute('aria-label','Enabled: '+(m.label||m.id));
  box.onchange=()=>whileBusy(box,async()=>{
   const users=m.used_by.filter(u=>u.type!=='default').map(u=>u.text);
   if(!box.checked&&users.length&&!await uiConfirm('Disable '+m.id+'? Still used by '+users.join(', ')+'. Agents on it cannot run until it is enabled again.',{title:'Disable model',ok:'Disable model',danger:true})){box.checked=true;return}
   try{const r=await api(modelPath(m.id),{method:'PATCH',headers:UI_HEADERS,body:JSON.stringify({enabled:box.checked})});
    notice((m.label||m.id)+(box.checked?' enabled.':' disabled.')+(r.warning?' '+r.warning:''));await reloadModelLists()}
   catch(e){box.checked=!box.checked;notice(e.message)}});
  en.append(box);
  const used=el('td','model-used-cell');used.dataset.label='Used by';used.append(modelUsedBy(m.used_by));
  const acts=el('td','model-actions');
  const test=el('button','secondary small','Test');test.type='button';test.title='One tiny request (≤16 tokens), a fraction of a cent; not recorded as a task';
  test.onclick=()=>whileBusy(test,async()=>{try{const r=await api(modelPath(m.id)+'/test',{method:'POST',headers:UI_HEADERS});modelsTests[m.id]=r;
    notice(r.ok?(m.label||m.id)+' answered in '+r.latency_ms+' ms.':(m.label||m.id)+': '+r.error);renderModels()}catch(e){notice(e.message)}});
  const edit=el('button','ghost small','Edit');edit.type='button';edit.onclick=()=>editModel(tr,m);
  const rm=el('button','ghost small','Remove');rm.type='button';
  rm.onclick=()=>whileBusy(rm,async()=>{if(!await uiConfirm('Remove '+m.id+' from the registry? Agents can no longer be given it.',{title:'Remove model',ok:'Remove model',danger:true}))return;
   try{await api(modelPath(m.id),{method:'DELETE',headers:UI_HEADERS});delete modelsTests[m.id];notice(m.id+' removed.');await reloadModelLists()}catch(e){notice(e.message)}});
  acts.append(test,edit,rm);
  tr.append(name,cell('Context',modelContext(m.context_length)),cell('In / 1M',modelPrice(m.prompt_price)),cell('Out / 1M',modelPrice(m.completion_price)),tools,en,used,acts);
  body.append(tr)}
 t.append(thead,body);
}
function editModel(tr,m){
 const name=tr.querySelector('.model-name');name.replaceChildren();
 const f=el('form','model-edit');const label=el('input');label.value=m.label;label.maxLength=120;label.setAttribute('aria-label','Label');label.placeholder='Label';
 const notes=el('input');notes.value=m.notes;notes.maxLength=2000;notes.setAttribute('aria-label','Notes');notes.placeholder='Notes (what it is good for)';
 const save=el('button','small','Save');save.type='submit';const cancel=el('button','ghost small','Cancel');cancel.type='button';cancel.onclick=renderModels;
 const row=el('div','model-edit-actions');row.append(save,cancel);
 f.append(el('code',null,m.id),label,notes,row);
 f.onsubmit=e=>{e.preventDefault();whileBusy(save,async()=>{try{await api(modelPath(m.id),{method:'PATCH',headers:UI_HEADERS,body:JSON.stringify({label:label.value,notes:notes.value})});
  notice(m.id+' saved.');await reloadModelLists()}catch(err){notice(err.message)}})};
 name.append(f);label.focus()}
async function loadModels(){modelsData=await api('models');renderModels()}
// After a change: the registry, the routing panel and every model picker (agents, conversations, tasks).
async function reloadModelLists(){await Promise.all([loadModels(),loadRouting().catch(()=>{}),loadRoutingModels(),loadAgents().catch(()=>{})]);
 if($('models-catalog').childElementCount)searchCatalog(true)}
function catalogCard(m){
 const card=el('div','model-card'+(m.supports_tools?'':' no-tools'));
 const head=el('div','model-card-head');head.append(el('strong',null,m.name),el('code',null,m.id));
 const facts=el('div','model-card-facts');
 facts.append(el('span',null,modelContext(m.context_length)+' context'),el('span',null,modelPrice(m.prompt_price)+' in · '+modelPrice(m.completion_price)+' out per 1M'));
 facts.append(m.supports_tools?el('span','badge badge-run','tool calls'):el('span','badge badge-warn','no tool calls'));
 const add=el('button',m.in_registry?'ghost small':'secondary small',m.in_registry?'Added':'Add');add.type='button';add.disabled=m.in_registry;
 add.setAttribute('aria-label',(m.in_registry?'Already added: ':'Add ')+m.id);
 add.onclick=()=>whileBusy(add,async()=>{
  if(!m.supports_tools&&!await uiConfirm(m.name+' does not support tool calls. Agents with server tools need them; it suits plain chat or summaries. Add anyway?',{title:'Add model without tool calls',ok:'Add anyway'}))return;
  try{const r=await api('models',{method:'POST',headers:UI_HEADERS,body:JSON.stringify({id:m.id})});notice(m.id+' added.'+(r.warning?' '+r.warning:''));
   m.in_registry=true;add.textContent='Added';add.className='ghost small';await reloadModelLists()}catch(e){notice(e.message)}});
 card.append(head,facts,add);
 if(m.description){card.title=m.description}
 return card}
async function searchCatalog(quiet){
 const q=$('models-catalog-q').value.trim(),seq=++modelsCatalogSeq,box=$('models-catalog'),status=$('models-catalog-status');
 if(!quiet)status.textContent='Searching…';
 const src=modelsSource,name=src==='xai'?'xAI':'OpenRouter';
 try{const d=await api('models/catalog?'+new URLSearchParams({q,limit:'40',source:src}));if(seq!==modelsCatalogSeq)return;
  box.replaceChildren(...d.models.map(catalogCard));
  if(d.error){box.append(el('p','error',d.error+(d.error.includes('not set')?': add XAI_API_KEY to the router .env (back it up first) and recreate agent-router. You can still add xai/<model> by hand.':'')));status.textContent='';return}
  if(!d.models.length)box.append(el('p','muted','No model matches “'+q+'”. Enter the id by hand below if you know it.'));
  status.textContent=(d.total>d.models.length?'Showing '+d.models.length+' of '+d.total:d.total+' match'+(d.total===1?'':'es'))+' · '+d.catalog_size+' models on '+name+(d.stale?' (cached copy: '+name+' did not answer)':'')}
 catch(e){if(seq!==modelsCatalogSeq)return;status.textContent='';box.replaceChildren(el('p','error',e.message))}}
$('models-catalog-q').addEventListener('input',()=>{clearTimeout(modelsCatalogTimer);modelsCatalogTimer=setTimeout(()=>searchCatalog(),250)});
$('models-catalog-q').addEventListener('focus',()=>{if(!$('models-catalog').childElementCount)searchCatalog()});
for(const src of ['openrouter','xai'])$('models-src-'+src).addEventListener('click',()=>{
 modelsSource=src;for(const o of ['openrouter','xai']){const b=$('models-src-'+o);b.setAttribute('aria-pressed',String(o===src));b.className=o===src?'small':'ghost small'}
 $('models-catalog-q').placeholder=src==='xai'?'Search xAI models, e.g. grok-4, grok-3-mini':'Search by name or id, e.g. claude, gpt, qwen';
 const p=(modelsData&&modelsData.providers||{}).xai;
 $('models-src-hint').textContent=src==='xai'?(p&&!p.configured?'XAI_API_KEY is not set on the server.':'Billed to your xAI credits, ids xai/<model>.'):'';
 searchCatalog()});
$('models-default').addEventListener('change',e=>{const s=e.target;whileBusy(s,async()=>{
 try{const r=await api('models/default',{method:'PUT',headers:UI_HEADERS,body:JSON.stringify({model:s.value||null})});
  notice('Default model: '+r.model+(r.source==='dashboard'?'.':' (back to '+(r.source==='env'?'OPENROUTER_WORKER_MODEL':'the first enabled model')+').'));await reloadModelLists()}
 catch(err){notice(err.message);renderModels()}})});
$('models-manual').addEventListener('submit',e=>{e.preventDefault();const b=$('models-manual-add');whileBusy(b,async()=>{
 try{const id=$('models-manual-id').value.trim();const r=await api('models',{method:'POST',headers:UI_HEADERS,body:JSON.stringify({id,label:$('models-manual-label').value})});
  notice(id+' added.'+(r.warning?' '+r.warning:''));$('models-manual').reset();await reloadModelLists()}catch(err){notice(err.message)}})});
$('models-sync').addEventListener('click',e=>{const b=e.currentTarget;whileBusy(b,async()=>{
 try{const r=await api('models/sync',{method:'POST',headers:UI_HEADERS});notice('Prices refreshed for '+r.updated+' model'+(r.updated===1?'':'s')+'.'+
  (r.missing.length?' Not in the OpenRouter or xAI model list: '+r.missing.join(', ')+'.':'')+(r.stale?' (a provider did not answer; cached data used.)':''));await loadModels()}catch(err){notice(err.message)}})});
document.querySelector('button[data-view="models"]').addEventListener('click',()=>Promise.all([loadModels(),loadRouting()]).catch(e=>notice(e.message)));
$('cost-open-models').addEventListener('click',()=>openView('models'));
$('refresh').addEventListener('click',()=>{if(!$('models').hidden)Promise.all([loadModels(),loadRouting()]).catch(()=>{})});
// ── HLA_MCP_V1: the MCP page. Remote MCP servers registered in the SSH gateway: settings (auth by credential name,
// never a value), status, "Refresh tools", each tool with its hints and the level it needs (overridable), and the server's
// own service level mcp:<name> (also on the Policy page). The nav item and the section are created here, so this view
// stays one self-contained block (mcp_admin.py serves /ui/api/mcp).
VIEW_TITLES.mcp='MCP';
let mcpData=null,mcpEditing=null;
const MCP_HINTS={readOnlyHint:['read-only','badge-sched'],destructiveHint:['destructive','badge-warn'],idempotentHint:['idempotent',''],openWorldHint:['open world','']};
const MCP_AUTH={none:'No auth',bearer:'Bearer token',header:'Custom header'};
(function(){
 const nav=el('button','nav');nav.type='button';nav.dataset.view='mcp';nav.append('⧉ ',el('span',null,'MCP'));nav.setAttribute('aria-label','MCP');
 document.querySelector('aside button.nav[data-view="policy"]').after(nav);
 nav.addEventListener('click',()=>{switchView('mcp');loadMcp().catch(e=>notice(e.message))});
 const sec=el('section');sec.id='mcp';sec.hidden=true;
 sec.innerHTML=`<div class="panel"><div class="panel-heading"><div><h2>MCP servers</h2><p class="muted">Remote MCP servers the SSH gateway calls for agents. Each server is a service <code>mcp:&lt;name&gt;</code> with its own level (new servers start at 0 = off). A tool needs level 4, or 6 when the server marks it destructive; set read-only tools lower per tool yourself (the server's read-only hint is not trusted). Tools a server adds later stay off until you review them. Tokens stay in the gateway; tool results reach agents as untrusted data.</p></div><button type="button" id="mcp-add">+ Add server</button></div>
<p id="mcp-note" class="policy-note" hidden></p><div id="mcp-servers" class="mcp-servers"><p class="muted">Loading…</p></div></div>
<div class="panel mcp-form-panel" id="mcp-form-panel" hidden><div class="panel-heading"><div><h2 id="mcp-form-title">Add an MCP server</h2><p class="muted">Streamable HTTP endpoints only (the URL ends in something like <code>/mcp</code>). Pick the token from the credential store; it is entered on the Connections page, never here.</p></div></div>
<form id="mcp-form" class="deploy-form mcp-form" novalidate><label>Name<input id="mcp-name" maxlength="32" pattern="[a-z0-9][a-z0-9_\\-]{0,31}" required placeholder="notion" autocomplete="off"></label>
<label id="mcp-url-label">URL<input id="mcp-url" maxlength="500" required placeholder="https://mcp.example.com/mcp" autocomplete="off" inputmode="url"></label>
<label id="mcp-url-cred-label" hidden>URL credential<select id="mcp-url-cred"></select><span class="fineprint">The whole URL is kept in the credential store (for URLs with a token in them).</span></label>
<label class="check mcp-wide"><input type="checkbox" id="mcp-url-secret"> URL is stored as a credential (it contains a token)</label>
<label>Auth<select id="mcp-auth"><option value="none">No auth</option><option value="bearer">Bearer token (Authorization header)</option><option value="header">Custom header</option></select></label>
<label id="mcp-header-label" hidden>Header name<input id="mcp-header" maxlength="64" placeholder="X-API-Key" autocomplete="off"></label>
<label id="mcp-cred-label" hidden>Credential<select id="mcp-cred"></select><span class="fineprint" id="mcp-cred-hint"></span></label>
<label>Timeout per call (seconds)<input id="mcp-timeout" type="number" min="5" max="120" value="60"></label>
<label class="mcp-wide">Description (optional)<input id="mcp-desc" maxlength="300" placeholder="What agents use it for"></label>
<label class="check"><input type="checkbox" id="mcp-private"> Allow private network addresses (LAN servers: 10.x, 172.16-31.x, 192.168.x)</label>
<label class="check"><input type="checkbox" id="mcp-insecure"> Allow plain http:// (only to a private address)</label>
<label class="check"><input type="checkbox" id="mcp-enabled" checked> Enabled</label>
<p class="fineprint mcp-wide">Loopback, link-local and cloud metadata addresses are always refused, and redirects are not followed.</p>
<div class="form-actions"><button type="button" class="secondary" id="mcp-cancel">Cancel</button><button type="submit" id="mcp-save">Save and load tools</button></div></form></div>
<div class="panel"><details class="mcp-help"><summary>Local (stdio) MCP servers</summary><p class="muted">The gateway does not start programs. Run a stdio server on a host behind a small Streamable HTTP proxy and register that URL (tick “Allow private network addresses”), for example:</p><pre><code>npx -y supergateway --stdio "npx -y @modelcontextprotocol/server-filesystem /srv/share" --outputTransport streamableHttp --port 8000
# or: mcp-proxy --transport streamablehttp --port 8000 -- your-mcp-server --args</code></pre><p class="muted">Then add <code>http://&lt;host&gt;:8000/mcp</code> with plain http allowed, or put it behind HTTPS. Protect the proxy (a token header or a firewall rule that only lets the gateway in).</p></details></div>`;
 $('policy').after(sec);
 $('mcp-add').addEventListener('click',()=>mcpOpenForm(null));
 $('mcp-cancel').addEventListener('click',()=>{$('mcp-form-panel').hidden=true;mcpEditing=null});
 $('mcp-auth').addEventListener('change',mcpAuthFields);
 $('mcp-url-secret').addEventListener('change',mcpAuthFields);
 $('mcp-insecure').addEventListener('change',()=>{if($('mcp-insecure').checked)$('mcp-private').checked=true});
 $('mcp-form').addEventListener('submit',e=>{e.preventDefault();mcpSave()});
 $('refresh').addEventListener('click',()=>{if(!$('mcp').hidden)loadMcp().catch(()=>{})});
})();
async function loadMcp(){mcpData=await api('mcp');renderMcp()}
function mcpCanEdit(){return !!(mcpData&&mcpData.available&&mcpData.editable)}
// What a service level means for an MCP server (with the default tool levels; overrides can use any level).
const MCP_LEVELS={0:'off',1:'tools you set to 1',2:'tools you set to 1-2',3:'tools you set to 1-3',4:'+ tools at their default',5:'+ tools at their default',6:'all tools, also destructive'};
function mcpLevelText(n){return n+' · '+MCP_LEVELS[n]}
function renderMcp(){
 const d=mcpData,box=$('mcp-servers'),notes=[];box.replaceChildren();
 if(!d.available)notes.push(d.note);
 else if(!d.editable)notes.push(d.approver?.reason||'Read-only.');
 if(d.available&&d.credentials_configured===false)notes.push('The gateway credential store is off (HLA_SECRETS_KEY or GATEWAY_CREDENTIAL_KEY): only servers without auth work.');
 $('mcp-note').hidden=!notes.length;$('mcp-note').textContent=notes.join(' ');
 $('mcp-add').disabled=!mcpCanEdit();
 if(d.available&&!d.servers.length)box.append(el('p','muted','No MCP servers yet. Add one with its URL; agents get its tools once you raise its level.'));
 for(const s of d.servers)box.append(mcpServerCard(s));
}
function mcpStatus(s){
 if(!s.enabled)return el('span','pill st-rejected','Off');
 if(s.last_error)return el('span','pill st-failed','Error');
 if(!s.refreshed)return el('span','pill st-rejected','Not loaded');
 return el('span','pill st-completed',s.tool_count+' tool'+(s.tool_count===1?'':'s'));
}
function mcpServerCard(s){
 const card=el('article','mcp-server');card.dataset.server=s.name;const can=mcpCanEdit();
 const head=el('div','mcp-server-head'),title=el('div','mcp-server-title');
 title.append(el('strong',null,s.name),mcpStatus(s));
 if(s.allow_private)title.append(el('span','badge badge-run','LAN allowed'));
 if(s.allow_insecure)title.append(el('span','badge badge-warn','plain http'));
 const lvl=el('label','mcp-level');lvl.append(document.createTextNode('Level of '+s.service));
 const sel=el('select');sel.dataset.mcpLevel=s.name;for(let n=0;n<=6;n++)sel.add(new Option(mcpLevelText(n),String(n)));sel.value=String(s.level);sel.disabled=!can;
 sel.onchange=()=>whileBusy(sel,async()=>{const v=Number(sel.value);
  if(v>=4&&!await uiConfirm('Level '+v+' on '+s.service+' lets agents (at that level) call tools that can change data'+(v>=6?', including destructive ones':'')+'. Continue?',
   {title:'Raise '+s.service+' to level '+v,ok:'Set level '+v,danger:v>=6})){sel.value=String(s.level);return}
  try{await api('mcp/servers/'+s.name+'/level',{method:'PUT',headers:UI_HEADERS,body:JSON.stringify({level:v})});notice(s.service+' is now at level '+v+'.');await loadMcp()}
  catch(e){notice(e.message);sel.value=String(s.level)}});
 lvl.append(sel);head.append(title,lvl);card.append(head);
 const meta=el('div','mcp-meta');
 meta.append(s.url_credential?el('span','mcp-url muted','URL from credential '+s.url_credential):el('code','mcp-url',s.url));
 if(s.url_credential_stored===false)meta.append(el('span','badge badge-warn',s.url_credential+' is not stored'));
 const auth=s.auth.type==='none'?'No auth':MCP_AUTH[s.auth.type]+(s.auth.header?' '+s.auth.header:'')+' · '+s.auth.credential;
 const a=el('span','muted',auth);meta.append(a);
 if(s.credential_stored===false)meta.append(el('span','badge badge-warn',s.auth.credential+' is not stored'));
 meta.append(el('span','muted',s.refreshed_at?'Tools loaded '+when(new Date(s.refreshed_at*1000).toISOString()):'Tools not loaded yet'));
 if(s.server_info&&s.server_info.name)meta.append(el('span','muted',s.server_info.name+(s.server_info.version?' '+s.server_info.version:'')));
 card.append(meta);
 if(s.description)card.append(el('p','muted mcp-desc',s.description));
 if(s.last_error)card.append(el('p','error mcp-error','Last refresh failed: '+s.last_error));
 const acts=el('div','mcp-actions');
 const ref=withIcon(el('button','secondary small'),'refresh','Refresh tools');ref.type='button';ref.disabled=!can;ref.onclick=()=>whileBusy(ref,()=>mcpRefresh(s.name));
 const ed=el('button','ghost small','Edit');ed.type='button';ed.disabled=!can;ed.onclick=()=>mcpOpenForm(s);
 const rm=el('button','ghost small','Remove');rm.type='button';rm.disabled=!can;
 rm.onclick=()=>whileBusy(rm,async()=>{if(!await uiConfirm('Remove the MCP server '+s.name+'? Agents lose its tools; its level and tool overrides are deleted.',{title:'Remove MCP server',ok:'Remove server',danger:true}))return;
  try{await api('mcp/servers/'+s.name,{method:'DELETE',headers:UI_HEADERS});notice(s.name+' removed.');if(mcpEditing===s.name)$('mcp-form-panel').hidden=true;await loadMcp()}catch(e){notice(e.message)}});
 acts.append(ref,ed,rm);card.append(acts);
 if(s.tools.length)card.append(mcpToolList(s,can));
 else if(s.refreshed)card.append(el('p','muted','The server offers no tools.'));
 if((s.stale_overrides||[]).length)card.append(el('p','fineprint','Overrides for tools the server no longer lists: '+s.stale_overrides.join(', ')));
 return card;
}
function mcpToolList(s,can){
 const box=el('details','mcp-tools');box.open=s.tools.length<=12;
 const reachable=s.tools.filter(t=>t.required_level!=null&&t.required_level<=s.level).length;
 box.append(el('summary',null,'Tools ('+s.tools.length+') · '+(s.level?reachable+' within level '+s.level:'server is off (level 0)')+(s.review_count?' · '+s.review_count+' new to review':'')));
 if(s.review_count)box.open=true;
 const list=el('div','mcp-tool-list');
 for(const t of s.tools){
  const row=el('div','mcp-tool'+(t.required_level==null?' mcp-tool-off':''));row.dataset.tool=t.name;
  const info=el('div','mcp-tool-info'),name=el('div','mcp-tool-name');
  name.append(el('code',null,t.name));
  for(const [k,[label,tone]] of Object.entries(MCP_HINTS))if(t.annotations&&t.annotations[k]===true)name.append(el('span','badge '+(tone||'mcp-hint'),label));
  if(t.review)name.append(el('span','badge badge-warn mcp-new','new — review'));
  if((t.changed||[]).length)name.append(el('span','badge badge-warn mcp-changed','changed: '+t.changed.join(', ')));
  info.append(name);
  if(t.description){const p=el('p','muted mcp-tool-desc',t.description.length>240?t.description.slice(0,240)+'…':t.description);if(t.description.length>240)p.title=t.description;info.append(p)}
  const ctl=el('label','mcp-tool-level');ctl.append(el('span','fineprint','Needs'));
  const sel=el('select');sel.dataset.mcpTool=t.name;sel.setAttribute('aria-label','Level '+t.name+' needs');
  sel.add(new Option('Default · level '+t.default_level,''));for(let n=1;n<=6;n++)sel.add(new Option('Level '+n,String(n)));sel.add(new Option('Disabled','off'));
  sel.value=t.override?.disabled?'off':t.override?.level?String(t.override.level):'';sel.disabled=!can;
  sel.onchange=()=>whileBusy(sel,async()=>{const v=sel.value,override=v===''?null:v==='off'?{disabled:true}:{level:Number(v)};
   try{await api('mcp/servers/'+s.name+'/tools',{method:'PUT',headers:UI_HEADERS,body:JSON.stringify({tool:t.name,override})});
    notice(t.name+': '+(v===''?'back to its default (level '+t.default_level+')':v==='off'?'disabled':'needs level '+v)+'.');await loadMcp()}
   catch(e){notice(e.message);await loadMcp().catch(()=>{})}});
  ctl.append(sel);
  if(t.review){
   // A tool the server added after its first refresh: off until the owner looks at it and enables it (an override).
   const en=el('button','secondary small mcp-enable','Enable');en.type='button';en.disabled=!can;
   en.onclick=()=>whileBusy(en,async()=>{
    try{await api('mcp/servers/'+s.name+'/tools',{method:'PUT',headers:UI_HEADERS,body:JSON.stringify({tool:t.name,override:{level:t.default_level}})});
     notice(t.name+' enabled at level '+t.default_level+'.');await loadMcp()}
    catch(e){notice(e.message);await loadMcp().catch(()=>{})}});
   ctl.append(en);
  }
  const eff=t.review?'new: off until you review it':t.required_level==null?'agents cannot call it':t.required_level>s.level?'above the server level ('+s.level+')':'agents at level '+t.required_level+'+';
  ctl.append(el('span','fineprint mcp-tool-eff',eff));
  row.append(info,ctl);list.append(row);
 }
 box.append(list);return box;
}
async function mcpRefresh(name){
 try{const s=await api('mcp/servers/'+name+'/refresh',{method:'POST',headers:UI_HEADERS});notice(name+': '+s.tool_count+' tool'+(s.tool_count===1?'':'s')+' loaded.')}
 catch(e){notice(e.message)}
 await loadMcp().catch(()=>{});
}
function mcpAuthFields(){
 const t=$('mcp-auth').value;$('mcp-header-label').hidden=t!=='header';$('mcp-cred-label').hidden=t==='none';
 const secret=$('mcp-url-secret').checked;$('mcp-url-label').hidden=secret;$('mcp-url-cred-label').hidden=!secret;
}
function mcpOpenForm(s){
 mcpEditing=s?s.name:null;const panel=$('mcp-form-panel');panel.hidden=false;
 $('mcp-form-title').textContent=s?'Edit '+s.name:'Add an MCP server';
 $('mcp-name').value=s?s.name:'';$('mcp-name').disabled=!!s;
 $('mcp-url').value=s&&!s.url_credential?s.url:'';$('mcp-url-secret').checked=!!s?.url_credential;$('mcp-desc').value=s?.description||'';$('mcp-timeout').value=s?s.timeout:60;
 $('mcp-auth').value=s?s.auth.type:'none';$('mcp-header').value=s?.auth.header||'';
 $('mcp-private').checked=!!s?.allow_private;$('mcp-insecure').checked=!!s?.allow_insecure;$('mcp-enabled').checked=s?s.enabled:true;
 const cred=$('mcp-cred');cred.replaceChildren();const names=[...(mcpData?.credentials||[])];
 if(s?.auth.credential&&!names.includes(s.auth.credential))names.push(s.auth.credential);
 cred.add(new Option(names.length?'Pick a credential…':'No credentials stored yet',''));for(const n of names)cred.add(new Option(n,n));
 cred.value=s?.auth.credential||'';
 const ucred=$('mcp-url-cred');ucred.replaceChildren();const unames=[...(mcpData?.credentials||[])];
 if(s?.url_credential&&!unames.includes(s.url_credential))unames.push(s.url_credential);
 ucred.add(new Option(unames.length?'Pick a credential…':'No credentials stored yet',''));for(const n of unames)ucred.add(new Option(n,n));
 ucred.value=s?.url_credential||'';
 $('mcp-cred-hint').textContent=names.length?'Only the name is stored with the server; the gateway fills in the value.':'Store the token on the Connections page first (credentials are write-only).';
 mcpAuthFields();panel.scrollIntoView({behavior:'smooth',block:'start'});(s?$('mcp-url'):$('mcp-name')).focus({preventScroll:true});
}
async function mcpSave(){
 const name=(mcpEditing||$('mcp-name').value.trim()),btn=$('mcp-save');
 if(!/^[a-z0-9][a-z0-9_-]{0,31}$/.test(name))return notice('Names are 1-32 lowercase letters, digits, - and _ (e.g. notion).');
 const type=$('mcp-auth').value;
 if(type!=='none'&&!$('mcp-cred').value)return notice('Pick the credential that holds the token.');
 const secretUrl=$('mcp-url-secret').checked;
 if(secretUrl&&!$('mcp-url-cred').value)return notice('Pick the credential that holds the server URL.');
 const body={...(secretUrl?{url_credential:$('mcp-url-cred').value}:{url:$('mcp-url').value.trim(),url_credential:''}),description:$('mcp-desc').value.trim(),timeout:Number($('mcp-timeout').value)||60,
  allow_private:$('mcp-private').checked,allow_insecure:$('mcp-insecure').checked,enabled:$('mcp-enabled').checked,
  auth:{type,credential:type==='none'?'':$('mcp-cred').value,header:type==='header'?$('mcp-header').value.trim():''}};
 const before=mcpData?.servers.find(x=>x.name===name);
 await whileBusy(btn,async()=>{
  try{await api('mcp/servers/'+name,{method:'PUT',headers:UI_HEADERS,body:JSON.stringify(body)})}
  catch(e){notice(e.message);return}
  $('mcp-form-panel').hidden=true;mcpEditing=null;
  const moved=!before||(secretUrl?before.url_credential!==body.url_credential:before.url!==body.url||!!before.url_credential);
  if(body.enabled&&(moved||!before.refreshed||before.last_error))await mcpRefresh(name);
  else{notice(name+' saved.');await loadMcp().catch(()=>{})}
 });
}

// ── HLA_WEBSITES_V1: the Websites view. Every website on the website VM (public-website) from the SSH gateway's inventory:
// public URLs from Traefik with their HTTP check, environment (label > your override > guess), status, containers, and a
// details panel per site. Overrides (environment, display name, hidden, notes) are stored by websites.py. The nav item, the
// section and the Overview tile are created here, so this view stays one self-contained block (/ui/api/websites).
VIEW_TITLES.websites='Websites';
let wsData=null,wsFilter='all',wsQuery='',wsShowHidden=false,wsPendingOpen=null,wsDrawerId=null,wsEditing=null,wsLoading=false,wsTileAt=0;
const WS_STATUS={up:['Up','st-completed'],protected:['Up · protected','st-running'],degraded:['Degraded','st-incomplete'],down:['Down','st-failed'],unknown:['Unknown','st-unknown']};
let wsShowTab=()=>{};
const WS_ENVS=[['production','Production'],['staging','Staging'],['test','Test'],['unknown','Unknown']];
const WS_SOURCE={api:'Routes from the Traefik API',files:'Routes from the Traefik config files',labels:'Routes from Docker labels',none:'No routes could be read'};
(function(){
 const nav=el('button','nav');nav.type='button';nav.dataset.view='websites';nav.append(el('span',null,'Websites'));nav.setAttribute('aria-label','Websites');
 document.querySelector('aside button.nav[data-view="infrastructure"]').before(nav);
 nav.addEventListener('click',()=>{switchView('websites');loadWebsites().catch(e=>notice(e.message))});
 const sec=el('section');sec.id='websites';sec.hidden=true;
 sec.innerHTML=`<div class="page-intro"><p class="muted">Every website on public-website with its public address from Traefik, checked through Traefik itself. Read-only: the SSH gateway reads Docker and Traefik and never passes on Traefik's config or any password.</p><div class="intro-actions"><button type="button" class="secondary small" id="ws-refresh" title="Read the containers and routes again and check every URL now">Refresh</button></div></div>
<div class="ws-summary" id="ws-summary"><div class="metric-strip"><div class="skeleton-row"></div></div></div>
<div class="page-toolbar"><div class="tabs" role="tablist" id="ws-tabs" aria-label="Websites"><button type="button" role="tab" data-tab="sites" aria-selected="true">Sites</button><button type="button" role="tab" data-tab="routes" aria-selected="false">Domains &amp; routes <span class="tab-count" id="ws-routes-count" hidden></span></button></div></div>
<div data-pane="sites" class="ws-pane"><div class="toolbar ws-toolbar"><div class="seg ws-filter" role="group" aria-label="Environment" id="ws-filter"></div><input id="ws-search" type="search" placeholder="Search name, URL, container…" aria-label="Search websites" maxlength="100"><label class="check ws-hidden-toggle"><input type="checkbox" id="ws-show-hidden"> Show hidden</label></div>
<div class="panel ws-panel"><div id="ws-list" class="ws-list"></div></div><div class="ws-source" id="ws-source"></div></div>
<div data-pane="routes" class="ws-pane" id="ws-routes-pane" hidden></div>`;
 $('audit').after(sec);
 wsShowTab=paneTabs($('ws-tabs'),sec);  // HLA_UI_V4: Sites | Domains & routes
 const filter=$('ws-filter');
 for(const [v,label] of [['all','All'],...WS_ENVS]){const b=el('button','secondary small',label);b.type='button';b.dataset.env=v;b.setAttribute('aria-pressed',String(v==='all'));
  b.onclick=()=>{wsFilter=v;filter.querySelectorAll('button').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));renderWebsites()};filter.append(b)}
 $('ws-search').addEventListener('input',()=>{wsQuery=$('ws-search').value.trim().toLowerCase();renderWebsites()});
 $('ws-show-hidden').addEventListener('change',()=>{wsShowHidden=$('ws-show-hidden').checked;renderWebsites()});
 $('ws-refresh').addEventListener('click',()=>whileBusy($('ws-refresh'),refreshWebsites));
 $('refresh').addEventListener('click',()=>{if(!$('websites').hidden)loadWebsites().catch(()=>{});loadWebsiteTile(true)});
 // Overview: one light line from the last check (no gateway call), linking to the view.
 const tile=el('div','panel ws-tile');tile.id='ws-tile';
 const text=el('div','ws-tile-text');text.id='ws-tile-text';text.append(el('strong',null,'Websites'),el('span','muted',' not checked yet'));
 const open=el('button','ghost small','Open Websites →');open.type='button';open.id='ws-tile-open';open.onclick=()=>openWebsite();
 tile.append(text,open);$('inbox-panel').after(tile);
 document.querySelector('button[data-view="overview"]').addEventListener('click',()=>loadWebsiteTile(true));
})();
// The tile follows the inbox's refresh rhythm, at most once a minute.
const wsInboxLoad=loadInbox;
loadInbox=async function(){const d=await wsInboxLoad();loadWebsiteTile(false);return d};
async function loadWebsiteTile(force){
 if(!force&&Date.now()-wsTileAt<60000)return;wsTileAt=Date.now();
 let s;try{s=await api('websites/summary')}catch(e){return}
 const box=$('ws-tile-text');box.replaceChildren(el('strong',null,'Websites'));
 const c=s.counts||{},up=(c.up||0)+(c.protected||0);
 if(!s.total){box.append(el('span','muted',s.error?' — couldn\'t check: '+s.error:' — not checked yet'));return}
 box.append(el('span','ws-tile-count ws-ok',' '+up+' up'));
 if(c.degraded)box.append(el('span','ws-tile-count ws-warn',' · '+c.degraded+' degraded'));
 box.append(el('span','ws-tile-count '+(c.down?'ws-bad':'muted'),' · '+(c.down||0)+' down'));
 box.append(el('span','muted ws-tile-when',s.error?' · couldn\'t check: '+s.error:' · checked '+when(s.last_checked)));
 $('ws-tile').classList.toggle('ws-tile-alert',!!c.down);
}
function openWebsite(id){
 wsPendingOpen=id||null;
 if(id){const site=(wsData?.sites||[]).find(x=>x.id===id);if(site&&wsFilter!=='all'&&wsEnvOf(site)!==wsFilter)wsFilter='all'}
 openView('websites');wsShowTab('sites');
 if(id&&wsData)wsOpenPending();
}
function wsOpenPending(){
 if(!wsPendingOpen||!wsData)return;const site=(wsData.sites||[]).find(x=>x.id===wsPendingOpen);wsPendingOpen=null;
 if(site)wsOpenDrawer(site)}
async function loadWebsites(){
 if(wsLoading)return;wsLoading=true;
 try{wsData=await api('websites');renderWebsites();loadWebsiteTile(true)}
 finally{wsLoading=false}
}
async function refreshWebsites(){
 try{wsData=await api('websites/refresh',{method:'POST',headers:UI_HEADERS,body:'{}'});renderWebsites();loadWebsiteTile(true);
  notice(wsData.note?wsData.note+'.':'Websites checked: '+wsData.sites.length+' site'+(wsData.sites.length===1?'':'s')+'.')}
 catch(e){notice(e.message)}
}
function wsEnvOf(s){return ['production','staging','test'].includes(s.environment)?s.environment:'unknown'}
function wsStatusPill(status,extra){const [label,cls]=WS_STATUS[status]||WS_STATUS.unknown;const p=el('span','pill ws-status '+cls,label);if(extra)p.title=extra;return p}
function wsEnvPill(s){
 const env=wsEnvOf(s),p=el('span','pill ws-env ws-env-'+env,(WS_ENVS.find(x=>x[0]===env)||[0,'Unknown'])[1]);
 if(s.environment_source==='guess'){p.append(el('span','ws-guess',' · guessed'));p.title='Guessed from the project, container and host names. Edit to set it.'}
 else if(s.environment_source==='label'){p.title='Set by the hla.environment label on the container'}
 else p.title='Set by you';
 return p}
function wsLink(url,label){const a=el('a','ws-url',label||url.replace(/^https?:\/\//,'').replace(/\/$/,''));a.href=url;a.target='_blank';a.rel='noopener noreferrer';return a}
function wsDur(iso){if(!iso)return '—';const s=(Date.now()-new Date(iso))/1000;if(!(s>=0))return '—';
 if(s<3600)return Math.max(1,Math.floor(s/60))+' min';if(s<86400)return Math.floor(s/3600)+' h';return Math.floor(s/86400)+' d'}
function wsUrlLine(u){
 const line=el('div','ws-url-line');
 if(u.url)line.append(wsLink(u.url));else line.append(el('span','muted',u.router+': '+u.reason));
 const facts=[];
 if(u.http_status!=null)facts.push(u.http_status===0?'no answer':'HTTP '+u.http_status);
 if(u.seconds!=null&&u.http_status)facts.push(u.seconds<1?Math.round(u.seconds*1000)+' ms':u.seconds.toFixed(1)+' s');
 if(facts.length)line.append(el('span','ws-url-facts '+(u.status==='down'?'ws-bad':u.status==='degraded'?'ws-warn':'muted'),facts.join(' · ')));
 if(u.protected)line.append(el('span','badge badge-run','login / allow-list'));
 if(u.url&&u.http_status==null&&u.error)line.append(el('span','fineprint ws-url-err',u.error));
 return line}
function wsCert(s){
 const days=s.urls.map(u=>u.cert_days).filter(d=>d!=null);
 if(!days.length)return el('span','muted',s.urls.some(u=>u.tls&&u.url)?'—':'no TLS');
 const d=Math.min(...days);return el('span',d<0?'ws-bad':d<14?'ws-warn':'ws-ok',d<0?'expired':d+' days')}
// HLA_UI_V4: Sites = a summary strip, then one compact row per site (Website | Environment | Status | Response |
// Checked), grouped by environment. A row opens the site's drawer: addresses, certificate, containers, Traefik routes,
// notes and Edit. Where the data comes from and anything that could not be read sits quietly under the list.
function wsPrimaryUrl(s){return s.urls.find(u=>u.url&&u.http_status)||s.urls.find(u=>u.url)||null}
function wsResponse(u){
 if(!u||u.http_status==null)return null;
 const parts=[u.http_status===0?'no answer':'HTTP '+u.http_status];
 if(u.seconds!=null&&u.http_status)parts.push(u.seconds<1?Math.round(u.seconds*1000)+' ms':u.seconds.toFixed(1)+' s');
 return parts.join(' · ')}
function renderWebsites(){
 const d=wsData;if(!d)return;
 const list=$('ws-list'),summary=$('ws-summary'),source=$('ws-source');list.replaceChildren();summary.replaceChildren();source.replaceChildren();
 $('ws-refresh').disabled=d.available&&!d.refresh_allowed;
 $('ws-refresh').title=d.refresh_allowed===false?'Refreshing needs POLICY_ADMIN_TOKEN; the list refreshes itself every minute':'Read the containers and routes again and check every URL now';
 if(!d.available){
  summary.append(el('p','error ws-error',"Couldn't check the websites: "+d.error));
  if((d.stored||[]).length){
   list.append(el('p','muted','Last known status:'));
   const ul=el('ul','ws-stored plain-list');for(const r of d.stored){const li=el('li');li.append(wsStatusPill(r.status),el('strong',null,' '+r.name+' '),el('span','muted',(r.url||'')+' · checked '+when(r.last_checked)));ul.append(li)}
   list.append(ul)}
  return}
 // Summary: one strip per status, then the environments as quick filters.
 const totals=el('div','metric-strip ws-totals');totals.setAttribute('role','list');
 const tones={up:null,protected:null,degraded:'warn',down:'bad'};
 for(const k of ['up','protected','degraded','down']){const n=d.counts[k]||0;
  const t=metric(WS_STATUS[k][0],String(n),k==='protected'?'login or allow-list':k==='down'&&n?'needs attention':null,{tone:n?tones[k]:null});
  t.classList.add('ws-total','ws-total-'+k);t.querySelector('.metric-value').classList.add('ws-total-n');t.querySelector('.metric-label').classList.add('ws-total-l');totals.append(t)}
 summary.append(totals);
 const envs=el('div','ws-envs');
 for(const [env,label] of WS_ENVS){const c=d.by_environment?.[env];if(!c)continue;
  const parts=['up','protected','degraded','down'].filter(k=>c[k]).map(k=>c[k]+' '+WS_STATUS[k][0].toLowerCase());
  const e=el('button','chip ws-env-sum');e.type='button';e.dataset.env=env;e.append(el('strong',null,label+' '),el('span','muted',parts.join(' · ')||'—'));
  e.onclick=()=>$('ws-filter').querySelector('button[data-env="'+env+'"]').click();envs.append(e)}
 if(envs.childNodes.length)summary.append(envs);
 // Where the data comes from, and anything that could not be read (under the list).
 const src=el('p','fineprint ws-src');
 src.append(el('strong',null,WS_SOURCE[d.routes_source]||'Routes'),
  document.createTextNode(d.routes_source==='api'&&d.traefik_api?' ('+d.traefik_api+')':d.routes_source==='files'&&d.dynamic_dir?' in '+d.dynamic_dir:''),
  document.createTextNode(' · checked '+(d.checked_at?when(new Date(d.checked_at*1000).toISOString()):'—')+(d.cached?' (cached)':'')+
   (d.monitor_minutes?' · checked automatically every '+d.monitor_minutes+' min':' · automatic checks are off')));
 source.append(src);
 const warn=(text)=>source.append(el('p','ws-warnline',text));
 if(d.routes_error)warn(d.routes_error);
 for(const f of d.routes_file_errors||[])warn('Could not read '+f.file+': '+f.error);
 if(d.containers_error)warn(d.containers_error);
 if(d.checks_error)warn(d.checks_error);
 if(d.checks_truncated)warn('More than 50 addresses: only the first 50 were checked.');
 if(d.other_routes)source.append(el('p','fineprint',d.other_routes+' other Traefik route'+(d.other_routes===1?' points':'s point')+' to other machines (not listed).'));
 // The sites, grouped by environment.
 const shown=d.sites.filter(s=>(wsShowHidden||!s.hidden)&&(wsFilter==='all'||wsEnvOf(s)===wsFilter)&&(!wsQuery||
  [s.name,s.gateway_name,s.project||'',...s.urls.map(u=>u.url||''),...s.lan_urls,...s.containers.map(c=>c.name+' '+c.image)].join(' ').toLowerCase().includes(wsQuery)));
 const hiddenN=d.sites.filter(s=>s.hidden).length;
 $('ws-show-hidden').closest('label').hidden=!hiddenN;$('ws-show-hidden').closest('label').lastChild.textContent=' Show hidden ('+hiddenN+')';
 for(const b of $('ws-filter').querySelectorAll('button'))b.setAttribute('aria-pressed',String(b.dataset.env===wsFilter));
 if(!d.sites.length){list.append(el('div','empty-state','No containers found on public-website and no Traefik routes to it.'));wsOpenPending();return}
 if(!shown.length){list.append(el('div','empty-state','No websites match this filter.'));wsOpenPending();return}
 const t=el('table','ws-table'),head=el('thead'),hr=el('tr');
 for(const h of ['Website','Environment','Status','Response','Checked'])hr.append(el('th',null,h));head.append(hr);
 const body=el('tbody');
 for(const [env,label] of WS_ENVS){
  const group=shown.filter(s=>wsEnvOf(s)===env);if(!group.length)continue;
  const g=el('tr','ws-group');g.dataset.env=env;const gc=el('th',null);gc.colSpan=5;gc.append(label+' ',el('span','muted','('+group.length+')'));g.append(gc);body.append(g);
  for(const s of group)body.append(wsRow(s));
 }
 t.append(head,body);list.append(t);
 if(wsDrawerId&&V4_DRAWERS['ws-drawer']?.isOpen){const s=d.sites.find(x=>x.id===wsDrawerId);if(s)wsOpenDrawer(s);else V4_DRAWERS['ws-drawer'].close()}
 wsOpenPending();
}
function wsRow(s){
 const row=el('tr','ws-row ws-site'+(s.hidden?' ws-hidden':'')+' ws-st-'+s.status);row.dataset.id=s.id;row.tabIndex=0;
 const name=el('td','ws-cell ws-name');name.dataset.label='Website';
 const title=el('div','ws-title');title.append(el('strong',null,s.name));if(s.hidden)title.append(el('span','badge','hidden'));
 if(s.unmatched)title.append(el('span','badge badge-warn','route without a running container'));
 if(!s.public)title.append(el('span','badge','no public URL'));
 name.append(title);
 const sub=el('div','ws-sub'),u=wsPrimaryUrl(s);
 if(u)sub.append(wsLink(u.url));else if(s.lan_urls.length)sub.append(wsLink(s.lan_urls[0]),el('span','muted',' LAN'));else sub.append(el('span','muted',s.project&&s.project!==s.name?'project '+s.project:'—'));
 if(s.urls.length>1)sub.append(el('span','muted',' +'+(s.urls.length-1)));
 if(s.urls.some(x=>x.protected))sub.append(el('span','badge badge-run','login / allow-list'));
 for(const a of sub.querySelectorAll('a'))a.addEventListener('click',e=>e.stopPropagation());
 name.append(sub);
 const env=el('td','ws-cell ws-envcell');env.dataset.label='Environment';env.append(wsEnvPill(s));
 const st=el('td','ws-cell ws-stat');st.dataset.label='Status';st.append(wsStatusPill(s.status));
 if(s.status_reason&&s.status!=='up'&&s.status!=='protected')st.append(el('div','fineprint ws-reason',s.status_reason));
 const resp=el('td','ws-cell ws-resp');resp.dataset.label='Response';const r=wsResponse(u);
 resp.append(r?el('span','ws-url-facts '+(u.status==='down'?'ws-bad':u.status==='degraded'?'ws-warn':'muted'),r):el('span','muted','—'));
 const chk=el('td','ws-cell ws-checked');chk.dataset.label='Checked';chk.append(el('span','muted',when(s.last_checked)));
 row.append(name,env,st,resp,chk);
 const open=()=>wsOpenDrawer(s);row.onclick=open;row.onkeydown=e=>{if(e.target===row&&(e.key==='Enter'||e.key===' ')){e.preventDefault();open()}};
 return row}
function wsOpenDrawer(s){
 wsDrawerId=s.id;
 const d=drawer('ws-drawer');d.onclose=()=>{wsDrawerId=null;wsEditing=null};
 d.title.textContent=s.name;d.meta.replaceChildren(wsStatusPill(s.status),wsEnvPill(s));
 if(s.status_since&&s.status!=='up')d.meta.append(el('span','muted','since '+when(s.status_since)));
 d.actions.replaceChildren();
 const u=wsPrimaryUrl(s);
 if(u){const a=wsLink(u.url,'Open site');a.className='button-link secondary small';d.actions.append(a)}
 const ed=el('button','secondary small ws-edit-btn',wsEditing===s.id?'Close editor':'Edit');ed.type='button';
 ed.onclick=()=>{wsEditing=wsEditing===s.id?null:s.id;wsOpenDrawer(s);if(wsEditing)d.body.querySelector('.ws-edit select')?.focus()};
 d.actions.append(ed);
 d.body.replaceChildren(wsDetails(s));d.open();
}
function wsDetails(s){
 const box=el('div','ws-details');box.dataset.id=s.id;
 if(wsEditing===s.id)box.append(wsEditForm(s));
 if(s.status_reason)box.append(el('p','ws-reason-line'+(s.status==='down'?' ws-bad':s.status==='degraded'?' ws-warn':' muted'),s.status_reason));
 box.append(el('h3','drawer-section','Addresses'));
 const addr=el('div','ws-addr');for(const x of s.urls)addr.append(wsUrlLine(x));
 if(!s.urls.length){for(const x of s.lan_urls){const l=el('div','ws-url-line');l.append(wsLink(x),el('span','muted ws-url-facts','LAN'));addr.append(l)}
  if(!s.lan_urls.length)addr.append(el('span','muted','No address.'))}
 box.append(addr);
 const facts=el('dl','ws-facts facts kv');
 const fact=(k,v)=>{if(v==null||v==='')return;facts.append(el('dt',null,k));const dd=el('dd');if(v instanceof Node)dd.append(v);else dd.textContent=v;facts.append(dd)};
 fact('Environment',(WS_ENVS.find(x=>x[0]===wsEnvOf(s))||[0,'?'])[1]+' · '+({guess:'guessed from the names',label:'from the hla.environment label',override:'set by you'}[s.environment_source]||'')
  +(s.environment_source==='label'&&s.environment_override?' (your override '+s.environment_override+' is ignored: the label wins)':''));
 fact('Certificate',wsCert(s));
 const web=s.containers.find(c=>c.state==='running');fact('Up for',web?wsDur(web.started):null);
 fact('Last checked',s.last_checked?when(s.last_checked):null);
 if(s.gateway_name!==s.name)fact('Name on the server',s.gateway_name);
 if(s.project&&s.project!==s.name)fact('Compose project',s.project);
 fact('Compose folder',s.working_dir);
 if(s.label_url)fact('URL label',s.label_url);
 fact('Status since',s.status_since?new Date(s.status_since).toLocaleString():null);
 if(s.notes)fact('Notes',s.notes);
 box.append(el('h3','drawer-section','Details'),facts);
 if(s.containers.length){
  box.append(el('h3','drawer-section','Containers'));
  const t=el('div','ws-cont-table');
  for(const c of s.containers){const r=el('div','ws-cont-row');
   const name=el('div','ws-cont-main');name.append(el('span','ws-dot '+(c.state!=='running'?'ws-dot-bad':c.health==='unhealthy'||c.health==='starting'?'ws-dot-warn':'ws-dot-ok')),el('code',null,c.name));
   r.append(name,el('span','muted',c.status||c.state+(c.health?' · '+c.health:'')),el('span','fineprint ws-image',c.image),el('span','fineprint',(c.ports||[]).join(', ')||'no published ports'));t.append(r)}
  box.append(t)}
 if(s.routes.length){
  const det=el('details','ws-routes-box');det.open=true;det.append(el('summary',null,'Traefik routes ('+s.routes.length+')'));
  for(const r of s.routes){const card=el('div','ws-route');
   const h=el('div','ws-route-head');h.append(el('code',null,r.name+(r.provider?'@'+r.provider:'')),el('span','badge',r.tls?'TLS'+(r.cert_resolver?' · '+r.cert_resolver:''):'no TLS'));
   for(const e of r.entrypoints||[])h.append(el('span','badge badge-sched',e));
   card.append(h,el('code','ws-rule',r.rule||'(no rule)'));
   const mw=el('div','ws-mws');
   if(r.middlewares.length){mw.append(el('span','fineprint','Middlewares: '));for(const m of r.middlewares)mw.append(el('span','badge'+(/auth|allow|white/.test(m.type)?' badge-run':''),m.name+' ('+m.type+')'))}
   else mw.append(el('span','fineprint','No middlewares'));
   card.append(mw);
   card.append(el('div','fineprint ws-backend','Service '+(r.service||'—')+(r.backends.length?' → '+r.backends.join(', '):'')));
   det.append(card)}
  box.append(det)}
 return box}
function wsEditForm(s){
 const f=el('form','ws-edit');f.dataset.id=s.id;
 const env=el('label',null,'Environment');const sel=el('select');sel.name='environment';
 sel.add(new Option('Guess ('+(s.base_environment||'production')+')',''));for(const [v,l] of WS_ENVS.slice(0,3))sel.add(new Option(l,v));
 sel.value=s.environment_override||'';env.append(sel);
 if(s.environment_source==='label')env.append(el('span','fineprint',' The container\'s hla.environment label wins over this.'));
 const name=el('label',null,'Display name');const ni=el('input');ni.name='display_name';ni.maxLength=120;ni.placeholder=s.gateway_name;ni.value=s.name!==s.gateway_name?s.name:'';name.append(ni);
 const hid=el('label','check');const hi=el('input');hi.type='checkbox';hi.name='hidden';hi.checked=!!s.hidden;hid.append(hi,document.createTextNode(' Hide this site (not counted, no alerts)'));
 const notes=el('label','ws-wide',"Notes");const ta=el('textarea');ta.name='notes';ta.rows=2;ta.maxLength=2000;ta.value=s.notes||'';notes.append(ta);
 const acts=el('div','form-actions ws-wide');const cancel=el('button','secondary small','Cancel');cancel.type='button';cancel.onclick=()=>{wsEditing=null;wsOpenDrawer(s)};
 const save=el('button','small','Save');save.type='submit';acts.append(cancel,save);
 f.append(env,name,hid,notes,acts);
 f.onsubmit=e=>{e.preventDefault();whileBusy(save,async()=>{
  try{await api('websites/'+encodeURIComponent(s.id),{method:'PUT',headers:UI_HEADERS,body:JSON.stringify({environment:sel.value||null,display_name:ni.value,hidden:hi.checked,notes:ta.value})});
   wsEditing=null;notice((ni.value.trim()||s.gateway_name)+' saved.');await loadWebsites()}
  catch(err){notice(err.message)}})};
 return f}

// ── HLA_ROUTES_V1: "Domains & routes" in the Websites view, route requests in "Needs you", public addresses on projects.
// The SSH gateway owns the routes (one file on the traefik host, checked through the Traefik API, rolled back on failure);
// this block only shows and edits them through /ui/api/routes. Hand-written Traefik routes are listed read-only.
let rtData=null,rtForm=null,rtDomains=null,rtDnsTimer=null,rtDnsSeq=0;
const RT_PROTECTION={'public':'Public (anyone)','lan_only':'LAN only','lan_only+basic_auth':'LAN only + password','basic_auth':'Password (from anywhere)'};
const RT_PROTECTION_HINT={'public':'Anyone on the internet can open it.','lan_only':'Only 192.168.0.0/16 and 10.0.0.0/8 (never Docker\'s 172.16.0.0/12).',
 'lan_only+basic_auth':'LAN addresses only, and a password on top.','basic_auth':'From anywhere, with a password.'};
const RT_DEFAULT={production:'public',staging:'lan_only+basic_auth'};
const RT_DNS={ok:['DNS ok','ws-ok'],lan:['DNS: LAN override','ws-ok'],elsewhere:['DNS points elsewhere','ws-bad'],missing:['No DNS record','ws-warn'],error:['DNS lookup failed','ws-warn']};
const RT_TRAEFIK={enabled:['Live','st-completed'],warning:['Warning','st-incomplete'],disabled:['Off','st-unknown'],missing:['Not loaded','st-failed'],unknown:['Unknown','st-unknown']};
const RT_LABELS=/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$/;
const rtName=h=>h.toLowerCase().replace(/[^a-z0-9-]+/g,'-').replace(/^-+|-+$/g,'').slice(0,40).replace(/-+$/,'')||'route';
(function(){
 const panel=el('div','panel rt-panel');panel.id='rt-panel';
 panel.innerHTML=`<div class="panel-heading rt-heading"><div><h2>Domains &amp; routes</h2><p class="muted">Public addresses the platform manages for the sites on public-website. The SSH gateway writes them to one file on the traefik host (<code>hla-managed.yml</code>), checks them through the Traefik API and puts the previous file back if Traefik refuses. Hand-written Traefik files are never touched: they are listed read-only below.</p></div><div class="row-actions"><button type="button" class="secondary small" id="rt-domains-toggle" aria-expanded="false">Domains…</button><button type="button" class="small" id="rt-new" aria-expanded="false">+ New route</button></div></div>
<div id="rt-status"></div><div id="rt-domains-box" class="rt-box" hidden></div><div id="rt-form-box" class="rt-box" hidden></div><div id="rt-requests"></div>
<div id="rt-list" class="rt-list"><p class="muted">Loading…</p></div>
<details class="rt-others" id="rt-others"><summary>Hand-written routes (read-only)</summary><p class="fineprint">Routes from your own Traefik files and Docker labels. The platform never edits them; a new route may not reuse their host names or router names.</p><div id="rt-others-list"></div></details>`;
 $('ws-routes-pane').append(panel);
 $('rt-new').onclick=()=>rtOpenForm(rtForm&&rtForm.mode==='new'?null:{mode:'new'});
 $('rt-domains-toggle').onclick=()=>rtToggleDomains();
})();
const rtWsLoad=loadWebsites;
loadWebsites=async function(){
 const routes=loadRoutes().catch(e=>{rtData=null;$('rt-status').replaceChildren(el('p','error ws-error',"Couldn't read the routes: "+e.message))});
 try{await rtWsLoad()}finally{await routes;renderRoutes();rtRefillBackends()}
};
async function loadRoutes(){rtData=await api('routes');rtDomains=rtData.domains||rtDomains;renderRoutes()}
function rtSiteOfPort(port){
 for(const s of wsData?.sites||[])for(const c of s.containers)for(const p of c.ports||[]){const m=/:(\d+)->/.exec(p);if(m&&Number(m[1])===port)return {site:s,container:c}}
 return null}
function rtBackends(){
 const out=[],seen=new Set();
 for(const s of wsData?.sites||[])for(const c of s.containers)for(const p of c.ports||[]){
  const m=/^(?:([0-9.]+|::|):)?(\d+)->(\d+)\/tcp$/.exec(p);if(!m||seen.has(m[2])||(m[1]&&!['0.0.0.0','::','',rtData?.website_ip].includes(m[1])))continue;
  seen.add(m[2]);out.push({port:Number(m[2]),label:c.name+' · port '+m[2]+(c.state!=='running'?' ('+c.state+')':''),site:s.name})}
 return out.sort((a,b)=>a.port-b.port)}
function rtCert(host){
 for(const s of wsData?.sites||[])for(const u of s.urls)if(u.url&&new URL(u.url).hostname===host&&u.cert_days!=null)return u.cert_days;
 return null}
function rtDnsEl(d,long){
 const box=el('div','rt-dns');if(!d){box.append(el('span','muted','—'));return box}
 const [label,cls]=RT_DNS[d.status]||RT_DNS.error;const s=el('span','rt-dns-state '+cls,label);s.title=d.message||'';box.append(s);
 if(long&&d.message)box.append(el('div','fineprint',d.message));
 if(['missing','elsewhere'].includes(d.status)&&d.record){const r=el('div','fineprint rt-record');r.append('Add: ',el('code',null,d.record));box.append(r)}
 return box}
function renderRoutes(){
 const d=rtData,status=$('rt-status');if(!status)return;status.replaceChildren();
 const list=$('rt-list'),reqs=$('rt-requests'),others=$('rt-others-list');
 if(!d){list.replaceChildren();return}
 if(!d.available){status.append(el('p','ws-warnline',d.note));list.replaceChildren();reqs.replaceChildren();$('rt-new').disabled=$('rt-domains-toggle').disabled=true;return}
 const canEdit=!!d.editable;$('rt-new').disabled=$('rt-domains-toggle').disabled=!canEdit;
 if(!canEdit)status.append(el('p','ws-warnline',(d.approver?.reason||'Only approvers can change routes.')));
 if(!d.traefik_ok)status.append(el('p','ws-warnline','Traefik API: '+(d.traefik_error||'not reachable')+'. Routes are only changed while the API can confirm them.'));
 if(d.settings_error)status.append(el('p','ws-warnline',d.settings_error));
 const src=el('p','fineprint rt-src');
 src.append('Domains: ',d.domains.length?d.domains.join(', '):el('em',null,'none yet: add them with Domains…'),' · certificates from '+d.cert_resolver+(d.cert_resolver_source==='routers'?' (the resolver your routes use)':d.cert_resolver_source==='setting'?' (TRAEFIK_CERT_RESOLVER)':'')
  +' · public IP '+d.public_ips.join(', '));
 status.append(src);
 // Requests from agents (also in "Needs you").
 reqs.replaceChildren();
 const pending=(d.requests||[]).filter(r=>r.status==='pending');
 const cnt=$('ws-routes-count');if(cnt){cnt.textContent=String(pending.length);cnt.hidden=!pending.length}  // HLA_UI_V4: on the tab
 if(pending.length){reqs.append(el('h3','rt-group',null));reqs.lastChild.append('Requested by agents ',el('span','muted','('+pending.length+')'));
  for(const r of pending){const row=el('div','rt-request');row.dataset.id=r.id;
   const t=el('div');t.append(el('strong',null,r.host),el('span','muted',' · '+r.environment+' · '+RT_PROTECTION[r.protection]+' · port '+r.backend_port));
   if(r.reason)t.append(el('div','fineprint',r.reason));
   const acts=el('div','row-actions');
   const ok=el('button','small','Approve');ok.type='button';ok.disabled=!canEdit;ok.onclick=()=>whileBusy(ok,()=>rtDecide(r,'approve'));
   const no=el('button','ghost small','Reject');no.type='button';no.disabled=!canEdit;no.onclick=()=>whileBusy(no,()=>rtDecide(r,'reject'));
   acts.append(ok,no);row.append(t,acts);reqs.append(row)}}
 // The managed routes.
 list.replaceChildren();
 if(!d.routes.length)list.append(el('p','muted rt-empty',d.domains.length?'No managed routes yet. + New route gives a site a public address.':'Add your domains first (Domains…), then + New route.'));
 else{const head=el('div','rt-row rt-head');for(const h of ['Address','Protection','Backend','DNS','Traefik','Cert.',''])head.append(el('div',null,h));list.append(head)}
 for(const r of d.routes)list.append(rtRow(r,canEdit));
 // Hand-written routers: read-only.
 others.replaceChildren();
 const o=d.other_routers||[];$('rt-others').querySelector('summary').textContent='Hand-written routes (read-only) · '+o.length;
 for(const r of o){const line=el('div','rt-other');line.append(el('code',null,r.name+'@'+r.provider),el('span',null,' '+(r.hosts.join(', ')||r.rule)),el('span','badge','read-only'));others.append(line)}
}
function rtRow(r,canEdit){
 const row=el('article','rt-row rt-route');row.dataset.name=r.name;
 const a=el('div','rt-cell rt-addr');a.dataset.label='Address';a.append(wsLink(r.url,r.host+(r.path_prefix||'')));
 const tags=el('div','ws-tags');tags.append(el('span','pill ws-env ws-env-'+r.environment,r.environment==='production'?'Production':'Staging'));
 if(r.status!=='active')tags.append(el('span','badge','switched off'));a.append(tags);
 const p=el('div','rt-cell');p.dataset.label='Protection';p.append(el('span','badge'+(r.protection==='public'?'':' badge-run'),RT_PROTECTION[r.protection]||r.protection));
 if(r.basic_user)p.append(el('div','fineprint rt-cred','user '+r.basic_user+' · password in '+generatedStoreName()+': '+r.basic_secret));
 const b=el('div','rt-cell');b.dataset.label='Backend';b.append(el('code',null,r.backend));
 const owner=rtSiteOfPort(r.backend_port);b.append(el('div','fineprint',owner?owner.container.name+' ('+owner.container.state+')':'no container publishes this port'));
 const dn=el('div','rt-cell');dn.dataset.label='DNS';dn.append(rtDnsEl(r.dns));
 const tr=el('div','rt-cell');tr.dataset.label='Traefik';const [tl,tc]=RT_TRAEFIK[r.traefik?.status]||RT_TRAEFIK.unknown;const tp=el('span','pill '+tc,tl);tr.append(tp);
 if(r.traefik?.error)tr.append(el('div','fineprint ws-bad',r.traefik.error));
 const ce=el('div','rt-cell');ce.dataset.label='Certificate';const days=rtCert(r.host);
 ce.append(days==null?el('span','muted',r.dns&&['missing','elsewhere'].includes(r.dns.status)?'needs DNS':'—'):el('span',days<0?'ws-bad':days<14?'ws-warn':'ws-ok',days<0?'expired':days+' days'));
 const acts=el('div','rt-cell ws-acts');
 const ed=el('button','ghost small rt-edit','Edit');ed.type='button';ed.disabled=!canEdit;ed.onclick=()=>rtOpenForm({mode:'edit',route:r});
 const del=el('button','ghost small rt-delete','Delete');del.type='button';del.disabled=!canEdit;
 del.onclick=()=>whileBusy(del,async()=>{if(!await uiConfirm('Delete the route for '+r.host+'? Traefik stops serving this address at once.',{title:'Delete route',ok:'Delete route',danger:true}))return;
  try{await api('routes/'+encodeURIComponent(r.name),{method:'DELETE',headers:UI_HEADERS});notice('Route for '+r.host+' deleted.');if(rtForm?.route?.name===r.name)rtOpenForm(null);await loadRoutes()}
  catch(e){notice(e.message)}});
 acts.append(ed,del);row.append(a,p,b,dn,tr,ce,acts);return row}
async function rtDecide(r,action){
 if(action==='approve'){if(!await uiConfirm('Create the route https://'+r.host+'/ → port '+r.backend_port+' ('+RT_PROTECTION[r.protection]+')? The gateway writes it to Traefik and checks it.',{title:'Create route',ok:'Create route'}))return;
  try{await api('routes/requests/'+r.id+'/approve',{method:'POST',headers:UI_HEADERS,body:'{}'});notice('Route created: https://'+r.host+'/');}catch(e){notice(e.message);return}}
 else{const note=await uiPrompt('Reject the route for '+r.host,{label:'Why not? (optional)',hint:'The agent can see this in the audit log.',
   multiline:true,rows:6,maxLength:300,ok:'Reject request',danger:true});if(note===null)return;
  try{await api('routes/requests/'+r.id+'/reject',{method:'POST',headers:UI_HEADERS,body:JSON.stringify({note})});notice('Request for '+r.host+' rejected.')}catch(e){notice(e.message);return}}
 await Promise.all([refreshInbox(),$('websites').hidden?null:loadRoutes()]);
}
function rtToggleDomains(force){
 const box=$('rt-domains-box'),open=force!=null?force:box.hidden;box.hidden=!open;$('rt-domains-toggle').setAttribute('aria-expanded',String(open));box.replaceChildren();if(!open)return;
 const f=el('form','rt-domains');const cur=[...(rtData?.domains||[])];
 const chips=el('div','rt-chips'),inp=el('input');inp.id='rt-domain-add';inp.placeholder='e.g. example.com';inp.maxLength=253;inp.setAttribute('aria-label','Add a domain');
 const draw=()=>{chips.replaceChildren();for(const dname of cur){const c=el('span','rt-chip',dname+' ');const x=el('button','ghost small','×');x.type='button';x.title='Remove '+dname;x.setAttribute('aria-label','Remove '+dname);
   x.onclick=()=>{cur.splice(cur.indexOf(dname),1);draw()};c.append(x);chips.append(c)}if(!cur.length)chips.append(el('span','muted','No domains yet.'))};
 const add=v=>{v=String(v||'').trim().toLowerCase().replace(/\.$/,'');if(!v)return;if(!RT_LABELS.test(v)||!v.includes('.')){notice(v+' is not a domain name like example.com');return}if(!cur.includes(v))cur.push(v);draw()};
 const addBtn=el('button','secondary small','Add');addBtn.type='button';addBtn.onclick=()=>{add(inp.value);inp.value='';inp.focus()};
 inp.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();addBtn.click()}});
 const row=el('div','rt-add');row.append(inp,addBtn);
 f.append(el('h3',null,'Your domains'),el('p','fineprint','New routes are subdomains of these (or the domain itself). Their DNS must point at '+(rtData?.public_ips||[]).join(', ')+' for the certificates.'),chips,row);
 const sugg=(rtData?.domain_suggestions||[]).filter(s=>!cur.includes(s));
 if(sugg.length){const s=el('div','rt-sugg');s.append(el('span','fineprint','Seen in your Traefik routes: '));for(const x of sugg){const b=el('button','ghost small rt-suggest','+ '+x);b.type='button';b.onclick=()=>{add(x);b.remove()};s.append(b)}f.append(s)}
 const acts=el('div','form-actions');const cancel=el('button','secondary small','Cancel');cancel.type='button';cancel.onclick=()=>rtToggleDomains(false);
 const save=el('button','small','Save domains');save.type='submit';acts.append(cancel,save);f.append(acts);draw();
 f.onsubmit=e=>{e.preventDefault();if(inp.value.trim()){add(inp.value);inp.value=''}whileBusy(save,async()=>{
  try{const r=await api('routes/domains',{method:'PUT',headers:UI_HEADERS,body:JSON.stringify({domains:cur})});rtDomains=r.domains;notice('Domains saved: '+(r.domains.join(', ')||'none')+'.');rtToggleDomains(false);await loadRoutes()}
  catch(err){notice(err.message)}})};
 box.append(f);inp.focus();
}
function rtSplitHost(host,domains){
 const d=[...domains].sort((a,b)=>b.length-a.length).find(x=>host===x||host.endsWith('.'+x));
 return d?{domain:d,sub:host===d?'':host.slice(0,-d.length-1)}:{domain:domains[0]||'',sub:host||''}}
function rtFillBackends(sel,keep){
 const want=keep!=null?String(keep):sel.value;sel.replaceChildren(new Option('Pick a container / port…',''));
 for(const b of rtBackends())sel.add(new Option(b.label,String(b.port)));
 sel.add(new Option('Other port…','other'));
 if(want&&![...sel.options].some(o=>o.value===want)){sel.value='other';const p=$('rt-port');if(p&&want!=='other')p.value=want}else sel.value=want||'';
 const p=$('rt-port');if(p)p.closest('label').hidden=sel.value!=='other'}
function rtRefillBackends(){const sel=$('rt-backend');if(sel&&!sel.dataset.touched)rtFillBackends(sel)}
function rtOpenForm(spec){
 rtForm=spec;const box=$('rt-form-box');box.replaceChildren();box.hidden=!spec;$('rt-new').setAttribute('aria-expanded',String(!!spec&&spec.mode==='new'));
 if(!spec)return;
 const r=spec.route||{},pre=spec.prefill||{},domains=rtData?.domains||rtDomains||[];
 const f=el('form','rt-form');f.noValidate=true;
 f.append(el('h3',null,spec.mode==='edit'?'Edit route '+r.host:'New route'));
 if(!domains.length){f.append(el('p','ws-warnline','Add your domains first (Domains…).'))}
 const split=rtSplitHost(r.host||pre.host||'',domains);
 const hostRow=el('div','rt-hostrow');
 const subL=el('label',null,'Subdomain');const sub=el('input');sub.id='rt-sub';sub.maxLength=200;sub.placeholder='e.g. shop-staging';sub.value=split.sub;sub.autocomplete='off';subL.append(sub);
 const domL=el('label',null,'Domain');const dom=el('select');dom.id='rt-domain';for(const x of domains)dom.add(new Option(x,x));if(split.domain)dom.value=split.domain;domL.append(dom);
 hostRow.append(subL,el('span','rt-dot','.'),domL);
 const preview=el('div','rt-preview');preview.id='rt-preview';
 const envL=el('label',null,'Environment');const env=el('select');env.id='rt-env';env.add(new Option('Production','production'));env.add(new Option('Staging','staging'));
 env.value=r.environment||pre.environment||'production';envL.append(env);
 const protL=el('label',null,'Protection');const prot=el('select');prot.id='rt-protection';for(const [v,l] of Object.entries(RT_PROTECTION))prot.add(new Option(l,v));
 prot.value=r.protection||pre.protection||RT_DEFAULT[env.value];protL.append(prot);const protHint=el('span','fineprint');protL.append(protHint);
 const userL=el('label',null,'Login user');const user=el('input');user.id='rt-user';user.maxLength=32;user.value=r.basic_user||'hla';userL.append(user,el('span','fineprint','The password is generated in '+generatedStoreName()+' as TRAEFIK_BASIC_<ROUTE>; '+(SECRET_BACKEND==='infisical'?'read it there.':'export it with the gateway CLI: secrets export TRAEFIK_BASIC_<ROUTE> --generated --out FILE.')));
 const beL=el('label',null,'Backend on public-website');const be=el('select');be.id='rt-backend';beL.append(be);
 const portL=el('label',null,'Port');const port=el('input');port.id='rt-port';port.type='number';port.min=1;port.max=65535;port.placeholder='e.g. 8085';portL.append(port);
 const pathL=el('label',null,'Path prefix (optional)');const path=el('input');path.id='rt-path';path.maxLength=100;path.placeholder='/';path.value=r.path_prefix||'';pathL.append(path);
 const dns=el('div','rt-dnsline');dns.id='rt-dns';
 const err=el('p','error rt-form-error');err.id='rt-form-error';err.hidden=true;
 const grid=el('div','rt-grid');grid.append(envL,protL,userL,beL,portL,pathL);
 const acts=el('div','form-actions');const cancel=el('button','secondary small','Cancel');cancel.type='button';cancel.onclick=()=>rtOpenForm(null);
 const save=el('button','small','Save route');save.type='submit';save.id='rt-save';acts.append(cancel,save);
 f.append(hostRow,preview,dns,grid,el('p','fineprint','HTTPS on websecure with a certificate from '+(rtData?.cert_resolver||'letsencrypt')+'; plain HTTP is redirected by Traefik. Saving writes the managed file and waits up to ~10 s for Traefik to confirm; on any error the previous file is restored.'),err,acts);
 box.append(f);
 rtFillBackends(be,r.backend_port||pre.backend_port||'');be.onchange=()=>{be.dataset.touched='1';portL.hidden=be.value!=='other';if(be.value==='other')port.focus()};
 let protTouched=spec.mode==='edit';
 const host=()=>{const s=sub.value.trim().toLowerCase().replace(/\.$/,'');return (s?s+'.':'')+dom.value};
 const sync=()=>{
  userL.hidden=!prot.value.includes('basic_auth');protHint.textContent=RT_PROTECTION_HINT[prot.value]||'';
  const s=sub.value.trim().toLowerCase(),h=host();let problem='';
  if(!dom.value)problem='Add a domain first.';
  else if(s&&!RT_LABELS.test(s))problem='Use letters, digits and - (dots between parts), not starting or ending with -.';
  else if(h.length>253)problem='The full name is too long.';
  preview.replaceChildren();if(dom.value){preview.append(el('span','muted','Address: '),el('strong',null,'https://'+h+(path.value.trim()&&path.value.trim()!=='/'?path.value.trim():'/')))}
  preview.classList.toggle('rt-bad',!!problem);if(problem)preview.append(el('span','ws-bad',' — '+problem));
  save.disabled=!!problem||!dom.value;return problem};
 const checkDns=()=>{clearTimeout(rtDnsTimer);const h=host();if(sync()||!dom.value){dns.replaceChildren();return}
  dns.replaceChildren(el('span','muted','Checking DNS for '+h+'…'));const seq=++rtDnsSeq;
  rtDnsTimer=setTimeout(async()=>{try{const d=await api('routes/check-dns',{method:'POST',headers:UI_HEADERS,body:JSON.stringify({host:h,protection:prot.value})});
   if(seq!==rtDnsSeq)return;dns.replaceChildren(rtDnsEl(d,true));
   if(['missing','elsewhere'].includes(d.status))dns.append(el('p','ws-warnline rt-dns-warn','You can save the route now, but Traefik cannot get a certificate until the DNS record exists.'))}
   catch(e){if(seq===rtDnsSeq)dns.replaceChildren(el('span','muted','DNS not checked: '+e.message))}},450)};
 sub.addEventListener('input',checkDns);dom.addEventListener('change',checkDns);path.addEventListener('input',sync);
 env.addEventListener('change',()=>{if(!protTouched)prot.value=RT_DEFAULT[env.value];sync()});
 prot.addEventListener('change',()=>{protTouched=true;sync()});
 f.onsubmit=e=>{e.preventDefault();const problem=sync();if(problem){err.hidden=false;err.textContent=problem;return}
  const portVal=be.value==='other'?Number(port.value):Number(be.value);
  if(!portVal||portVal<1||portVal>65535){err.hidden=false;err.textContent='Pick the container or enter the port it publishes on public-website.';return}
  err.hidden=true;const h=host(),name=spec.mode==='edit'?r.name:rtName(h);
  const body={host:h,environment:env.value,protection:prot.value,backend_port:portVal,path_prefix:path.value.trim(),create:spec.mode!=='edit'};
  if(prot.value.includes('basic_auth'))body.basic_user=user.value.trim();
  if(pre.project_id||r.project_id)body.project_id=pre.project_id||r.project_id;
  whileBusy(save,async()=>{save.textContent='Saving… (waiting for Traefik)';
   try{const out=await api('routes/'+encodeURIComponent(name),{method:'PUT',headers:UI_HEADERS,body:JSON.stringify(body)});
    notice('Route saved: https://'+h+'/ is live on Traefik.'+(out.route?.basic_user?' Password: '+out.route.basic_secret+' in '+secretStoreName()+'.':''));rtOpenForm(null);await loadRoutes();rtWsLoad().then(renderRoutes).catch(()=>{})}
   catch(ex){err.hidden=false;err.textContent=ex.message}finally{save.textContent='Save route'}})};
 sync();if(sub.value||dom.value)checkDns();
 box.scrollIntoView({block:'nearest'});(spec.mode==='edit'?prot:sub).focus();
}
// Inbox: route requests from agents (kind route_request).
INBOX_KIND.route_request=['Route','human_approval_required'];
async function rtInboxDecide(it,action){await rtDecide({id:it.request_id,host:it.host,backend_port:it.backend_port,protection:it.protection},action)}
// Projects: optional public addresses (staging and production) on the new-project form and the project page.
async function rtLoadDomains(){if(rtDomains)return rtDomains;try{const d=await api('routes/domains');rtDomains=d.domains||[]}catch(e){rtDomains=[]}return rtDomains}
function rtHostPicker(id,label,domains,value){
 const wrap=el('div','rt-picker');wrap.dataset.picker=id;wrap.append(el('span','rt-picker-label',label));
 const row=el('div','rt-hostrow');const sub=el('input');sub.id=id+'-sub';sub.maxLength=200;sub.autocomplete='off';
 sub.setAttribute('aria-label',label+' subdomain');
 const prev=el('span','fineprint rt-picker-prev');
 if(!domains.length){sub.placeholder='full host name, e.g. shop.example.com';sub.value=value||'';row.append(sub);
  wrap.append(row,el('span','fineprint','No domains configured yet (Websites → Domains & routes).'));wrap.value=()=>sub.value.trim().toLowerCase();return wrap}
 const split=rtSplitHost(value||'',domains);sub.placeholder=id.includes('staging')?'e.g. shop-staging':'e.g. shop';sub.value=value?split.sub:'';
 const dom=el('select');dom.id=id+'-domain';dom.setAttribute('aria-label',label+' domain');for(const x of domains)dom.add(new Option(x,x));dom.value=split.domain;
 row.append(sub,el('span','rt-dot','.'),dom);wrap.append(row,prev);
 wrap.value=()=>{const s=sub.value.trim().toLowerCase();return s?s+'.'+dom.value:''};
 const show=()=>{const s=sub.value.trim().toLowerCase();prev.textContent=s?(RT_LABELS.test(s)?'https://'+s+'.'+dom.value+'/':'Use letters, digits and -'):'';prev.classList.toggle('ws-bad',!!s&&!RT_LABELS.test(s))};
 sub.addEventListener('input',show);dom.addEventListener('change',show);show();return wrap}
async function rtProjectFormFields(){
 const old=$('project-domains');const domains=await rtLoadDomains();
 const box=el('div','project-domains');box.id='project-domains';
 box.append(rtHostPicker('project-staging','Staging address (optional)',domains,''),rtHostPicker('project-production','Production address (optional)',domains,''));
 box.append(el('p','fineprint ws-wide','Agents are told to serve the site there and to request the routes with the request_route tool; you approve each route.'));
 if(old)old.replaceWith(box);else document.querySelector('#project-form .merge-policy-field').after(box);
}
function projectDomains(){
 const v=id=>{const w=document.querySelector('#project-domains [data-picker="'+id+'"]');return w&&w.value?w.value():''};
 const out={};const s=v('project-staging'),p=v('project-production');if(s)out.staging_domain=s;if(p)out.production_domain=p;return out}
$('project-new-toggle').addEventListener('click',()=>{if(!$('project-form').hidden)rtProjectFormFields().catch(()=>{})});
$('project-form').addEventListener('reset',()=>setTimeout(()=>rtProjectFormFields().catch(()=>{}),0));
function projectDomainsEl(p){
 const line=el('div','project-domains-line');
 if(!p.staging_domain&&!p.production_domain){line.append(el('span','muted','No public addresses set. '))}
 for(const [env,host] of [['staging',p.staging_domain],['production',p.production_domain]]){if(!host)continue;
  const item=el('span','project-domain');item.append(el('span','muted',(env==='staging'?'Staging ':'Production ')),wsLink('https://'+host+'/',host));
  const managed=(rtData?.routes||[]).find(r=>r.host===host);
  if(managed)item.append(el('span','badge badge-sched','route live'));
  else{const b=el('button','ghost small project-create-route','Create route');b.type='button';
   b.onclick=()=>whileBusy(b,async()=>{openView('websites');wsShowTab('routes');try{await loadRoutes()}catch(e){notice(e.message);return}
    rtOpenForm({mode:'new',prefill:{host,environment:env,project_id:p.id}})});item.append(b)}
  line.append(item)}
 const ed=el('button','ghost small','Addresses…');ed.type='button';
 ed.onclick=()=>whileBusy(ed,async()=>{
  const host={type:'text',maxLength:253,pattern:'^[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+$',
   patternMessage:'Use a full host name such as app.example.com, without https:// or a path.'};
  const v=await uiForm('Public addresses',[
   {...host,name:'staging',label:'Staging address',value:p.staging_domain||'',placeholder:'staging.example.com',hint:'Full host name; leave empty for none.'},
   {...host,name:'production',label:'Production address',value:p.production_domain||'',placeholder:'www.example.com',hint:'Full host name; leave empty for none.'}],
   {ok:'Save addresses',intro:'Agents on this project get these addresses in their next task.'});
  if(v===null)return;const s=v.staging,pr=v.production;
  try{await api('projects/'+p.id,{method:'PATCH',headers:UI_HEADERS,body:JSON.stringify({staging_domain:s.trim().toLowerCase(),production_domain:pr.trim().toLowerCase()})});
   notice('Public addresses saved; agents get them in their next task.');await openProject(p.id)}catch(e){notice(e.message)}});
 line.append(ed);return line}

// ── HLA_VOICE_V1: the Chief's car mode ─────────────────────────────────────────────────────────────────────────────
// A full-screen voice view (/ui/#chief, also a home-screen shortcut): tap to talk, or hands-free (listen → send after a
// pause → speak the reply → listen again, until a minute of silence). Speech recognition and speech come from the browser
// (Web Speech API / speechSynthesis) or, when picked in Settings and configured, from the server (voice.py: any
// OpenAI-compatible speech service). A turn is an ordinary message in the Chief's conversation (/ui/api/chief), sent with
// voice:true; the reply streams like any run and is spoken sentence by sentence while it arrives. Nothing is approved by
// voice: replies that need the owner show a link to "Needs you".
VIEW_TITLES.chief='Chief';
const CAR={chief:null,config:{server_stt:false,server_tts:false,tts_voices:[]},state:'idle',handsFree:false,exchanges:[],
 recCancel:null,stream:null,source:null,speakQueue:[],speaking:false,audio:null,unlocked:false,wake:null,turnSeq:0,
 replyDone:true,keepAlive:null};
const CAR_SR=window.SpeechRecognition||window.webkitSpeechRecognition||null;
const CAR_SILENCE_MS=1500,CAR_IDLE_MS=60000,CAR_MAX_RECORD_MS=45000;
const carPref=(k,d)=>store.get('chief.'+k)??d;
// The car mode's language: this browser's choice, else the server default (HLA_VOICE_LANGUAGE, en-US unless set).
function carLang(){return carPref('lang',CAR.config&&CAR.config.language||'en-US')}
function carSttEngine(){const want=carPref('stt','browser');return want==='server'&&CAR.config.server_stt?'server':CAR_SR?'browser':CAR.config.server_stt?'server':'none'}
function carTtsEngine(){const want=carPref('tts','browser');return want==='server'&&CAR.config.server_tts?'server':'speechSynthesis' in window?'browser':CAR.config.server_tts?'server':'none'}
(function(){
 const nav=el('button','nav');nav.type='button';nav.dataset.view='chief';nav.append('🎙 ',el('span',null,'Chief'));nav.setAttribute('aria-label','Chief');
 document.querySelector('aside button.nav[data-view="overview"]').after(nav);
 nav.addEventListener('click',()=>{switchView('chief');carOpen()});
 const sec=el('section','car-mode');sec.id='chief';sec.hidden=true;sec.setAttribute('aria-label','Chief car mode');
 sec.innerHTML=`<div class="car-top"><button type="button" class="car-exit secondary" id="car-exit" aria-label="Leave car mode">✕</button>
<div class="car-title"><strong>Chief</strong><span id="car-status" class="car-status" role="status" aria-live="polite">Ready</span></div>
<button type="button" class="car-gear secondary" id="car-settings-toggle" aria-expanded="false" aria-controls="car-settings" aria-label="Voice settings">⚙</button></div>
<div class="car-transcript" id="car-transcript" aria-live="polite"></div>
<p class="car-interim" id="car-interim" aria-hidden="true"></p>
<a href="#inbox" class="car-needs" id="car-needs" hidden>⚑ Open Needs you</a>
<button type="button" class="car-talk" id="car-talk" aria-label="Talk to the Chief"><span class="car-talk-icon" aria-hidden="true">🎙</span><span id="car-talk-label">Tap to talk</span></button>
<div class="car-row"><button type="button" class="car-btn" id="car-handsfree" aria-pressed="false">∞ Hands-free</button>
<button type="button" class="car-btn" id="car-briefing">☰ Briefing</button>
<button type="button" class="car-btn" id="car-stop">■ Stop</button></div>
<div class="car-settings" id="car-settings" hidden><h2>Voice settings</h2>
<label>Chief's model<select id="car-model"></select></label><p class="fineprint">For quick answers pick a fast model without reasoning; the Task detail's Timings show where a turn's time went.</p>
<label>Language<select id="car-lang"><option value="en-US">English (US)</option><option value="en-GB">English (UK)</option><option value="nl-BE">Nederlands (België)</option><option value="nl-NL">Nederlands (Nederland)</option></select></label>
<label>Listen with<select id="car-stt"><option value="browser">This browser</option><option value="server">Server (speech service)</option></select></label>
<label>Speak with<select id="car-tts"><option value="browser">This browser</option><option value="server">Server (speech service)</option></select></label>
<label>Voice<select id="car-voice"></select></label>
<label>Speed <span id="car-rate-value"></span><input type="range" id="car-rate" min="0.7" max="1.6" step="0.1"></label>
<p class="fineprint" id="car-engine-note"></p>
<p class="fineprint">Headset or steering-wheel play/pause toggles listening while this screen is open, when the phone passes that button to the browser (often only while audio plays). Browser speech recognition in Chrome sends the audio to Google; pick “Server” to keep it on your own speech service.</p>
<button type="button" class="secondary" id="car-settings-close">Done</button></div>`;
 $('workspace').querySelector('main').append(sec);
 $('car-exit').onclick=()=>{carStopAll();openView('overview')};
 $('car-talk').onclick=()=>carTalkTap();
 $('car-handsfree').onclick=()=>{carUnlockAudio();carSetHandsFree(!CAR.handsFree);if(CAR.handsFree&&CAR.state==='idle')carListenAndSend()};
 $('car-briefing').onclick=()=>carBriefing();
 $('car-stop').onclick=()=>{carSetHandsFree(false);carCancelListen();carStopSpeaking();if(CAR.state!=='thinking'&&CAR.state!=='offline')carStatus('idle')};
 $('car-needs').onclick=()=>carStopAll();
 $('car-settings-toggle').onclick=()=>carSettings($('car-settings').hidden);
 $('car-settings-close').onclick=()=>carSettings(false);
 $('car-model').onchange=async e=>{try{const c=await api('chief',{method:'PATCH',headers:UI_HEADERS,body:JSON.stringify({model:e.target.value})});if(c&&c.thread_id)CAR.chief=c;notice('The Chief now uses '+e.target.value+'.')}catch(err){notice(err.message)}};
 $('car-lang').onchange=e=>{store.set('chief.lang',e.target.value);carFillVoices()};
 $('car-stt').onchange=e=>{store.set('chief.stt',e.target.value);carEngineNote()};
 $('car-tts').onchange=e=>{store.set('chief.tts',e.target.value);carFillVoices();carEngineNote()};
 $('car-voice').onchange=e=>store.set(carTtsEngine()==='server'?'chief.servervoice':'chief.voice',e.target.value);
 $('car-rate').oninput=e=>{store.set('chief.rate',e.target.value);$('car-rate-value').textContent='×'+Number(e.target.value).toFixed(1)};
 if('speechSynthesis' in window&&speechSynthesis.addEventListener)speechSynthesis.addEventListener('voiceschanged',()=>{if(!$('chief').hidden)carFillVoices()});
 // Leaving the view (another tab, a link) ends listening and speech; the screen may sleep again.
 new MutationObserver(()=>{if(document.body.dataset.view!=='chief')carStopAll()}).observe(document.body,{attributes:true,attributeFilter:['data-view']});
 document.addEventListener('visibilitychange',()=>{if(!document.hidden&&document.body.dataset.view==='chief')carWakeLock()});
 window.addEventListener('online',()=>{if(CAR.state==='offline'&&document.body.dataset.view==='chief')carOpen()});
 window.addEventListener('offline',()=>{if(document.body.dataset.view==='chief')carStatus('offline')});
})();
function carSettings(open){$('car-settings').hidden=!open;$('car-settings-toggle').setAttribute('aria-expanded',open?'true':'false');if(open){carFillVoices();carEngineNote()}}
const CAR_LABELS={idle:'Ready',listening:'Listening…',thinking:'Thinking…',speaking:'Speaking…',offline:'Offline'};
function carStatus(state,text){CAR.state=state;$('car-status').textContent=text||CAR_LABELS[state]||state;$('chief').dataset.state=state;
 $('car-talk-label').textContent=state==='listening'?'Tap to send':state==='speaking'?'Tap to interrupt':state==='thinking'?'Thinking…':'Tap to talk'}
async function carOpen(){
 carWakeLock();carMediaSession();
 $('car-lang').value=carLang();$('car-stt').value=carPref('stt','browser');$('car-tts').value=carPref('tts','browser');
 const rate=carPref('rate','1.1');$('car-rate').value=rate;$('car-rate-value').textContent='×'+Number(rate).toFixed(1);
 if(navigator.onLine===false)return carStatus('offline');
 try{
  const [chief,config,models]=await Promise.all([api('chief'),api('voice/config').catch(()=>CAR.config),api('models').catch(()=>null)]);
  CAR.chief=chief;CAR.config=config;$('car-lang').value=carLang();
  const sel=$('car-model');sel.replaceChildren();
  const list=models&&models.models||[];if(chief.model&&!list.includes(chief.model))sel.add(new Option(chief.model,chief.model));
  for(const v of list)sel.add(new Option(modelOptionText(models,v),v));if(chief.model)sel.value=chief.model;if(typeof subsDecorate==='function')subsDecorate(sel);  // HLA_SUBSCRIPTIONS_V1
  $('car-stt').querySelector('option[value="server"]').disabled=!config.server_stt;$('car-tts').querySelector('option[value="server"]').disabled=!config.server_tts;
  carEngineNote();
  if(CAR.state==='offline'||CAR.state==='idle')carStatus('idle',carSttEngine()==='none'?'No speech recognition here':null);
  await carLoadHistory();
 }catch(e){carStatus('offline','Chief unavailable');carLine('chief','I can’t be reached right now: '+e.message)}
}
function carEngineNote(){
 const stt=carSttEngine(),tts=carTtsEngine();
 $('car-engine-note').textContent='Listening: '+(stt==='browser'?'this browser':stt==='server'?'server speech service':'not available in this browser (no speech recognition and no server service configured)')
  +' · Speaking: '+(tts==='browser'?'this browser':tts==='server'?'server speech service':'not available');
}
function carFillVoices(){
 const sel=$('car-voice');sel.replaceChildren();
 if(carTtsEngine()==='server'){for(const v of CAR.config.tts_voices||[])sel.add(new Option(v,v));const keep=carPref('servervoice','');if(keep)sel.value=keep;return}
 if(!('speechSynthesis' in window))return;
 const norm=l=>String(l||'').toLowerCase().replace('_','-'),lang=norm(carLang()),base=lang.split('-')[0];
 const voices=speechSynthesis.getVoices().filter(v=>norm(v.lang).startsWith(base));
 voices.sort((a,b)=>(norm(b.lang)===lang)-(norm(a.lang)===lang));
 sel.add(new Option('Default for '+carLang(),''));
 for(const v of voices)sel.add(new Option(v.name+' ('+v.lang+')',v.name));
 sel.value=carPref('voice','');if(sel.selectedIndex<0)sel.value='';
}
async function carLoadHistory(){
 if(!CAR.chief)return;
 const d=await api('threads/'+CAR.chief.thread_id+'?limit=40');
 CAR.exchanges=d.messages.filter(m=>m.content&&((m.role==='user'&&m.kind!=='auto_continue')||m.role==='assistant'))
  .slice(-24).map(m=>({role:m.role==='user'?'you':'chief',text:m.role==='user'?m.content:carPlain(m.content)}));
 carRender();
 if(d.thread.status==='running'&&d.thread.current_task_id&&CAR.replyDone)carFollow(d.thread.current_task_id,++CAR.turnSeq);
}
function carLine(role,text){const x={role,text};CAR.exchanges.push(x);CAR.exchanges=CAR.exchanges.slice(-30);carRender();return x}
function carRender(){
 const box=$('car-transcript');box.replaceChildren();
 if(!CAR.exchanges.length)box.append(el('p','car-empty','Ask the Chief anything: “How are the projects doing?”, “What needs me?”, “Plan a project to …”.'));
 if(CAR.exchanges.length&&CAR.chief){const h=el('button','link-button car-history','Earlier messages are in Conversations');h.type='button';
  h.onclick=()=>openItem('thread',CAR.chief.thread_id).catch(e=>notice(e.message));box.append(h)}
 const last=CAR.exchanges.length-1;
 CAR.exchanges.forEach((x,i)=>{const p=el('p','car-line car-'+x.role);p.append(el('span','car-who',x.role==='you'?'You':'Chief'),el('span','car-text',x.text||'…'));
  // Older long replies fold to a few lines; the latest one is always shown in full.
  if(x.role==='chief'&&!x.open&&(x.text||'').length>(i<last?320:900)&&(i<last||CAR.replyDone)){p.classList.add('car-collapsed');const more=el('button','link-button car-more','Show more');more.type='button';
   more.onclick=()=>{x.open=true;carRender()};p.append(more)}
  box.append(p)});
 box.scrollTop=box.scrollHeight;
}
// Spoken form of a reply: no Markdown, code, tables or bare links.
function carPlain(t){
 return String(t||'').replace(/```[\s\S]*?(```|$)/g,' ').replace(/`([^`]*)`/g,'$1').replace(/!\[[^\]]*\]\([^)]*\)/g,' ')
  .replace(/\[([^\]]+)\]\([^)]*\)/g,'$1').replace(/https?:\/\/\S+/g,'link').replace(/^\s*\|?[-:| ]+\|[-:| ]*$/gm,' ')
  .replace(/\|/g,', ').replace(/^\s{0,3}(#{1,6}|>|[-*+•]|\d+[.)])\s+/gm,'').replace(/(\*\*|__|\*|_|~~)(?=\S)([^*_~]+?)\1/g,'$2')
  .replace(/[ \t]+/g,' ').replace(/\n{2,}/g,'\n').trim();
}
function carWakeLock(){
 if(!('wakeLock' in navigator)||document.hidden||document.body.dataset.view!=='chief')return;
 if(CAR.wake&&!CAR.wake.released)return;
 navigator.wakeLock.request('screen').then(l=>{CAR.wake=l}).catch(()=>{});
}
function carMediaSession(){
 if(!('mediaSession' in navigator))return;
 try{navigator.mediaSession.metadata=new MediaMetadata({title:'Chief',artist:'Homelab Agents'});
  for(const a of ['play','pause'])navigator.mediaSession.setActionHandler(a,()=>{if(document.body.dataset.view==='chief')carTalkTap()})}catch(e){}
}
function carStopAll(){
 carSetHandsFree(false);carCancelListen();carStopSpeaking();
 if(CAR.source){CAR.source.close();CAR.source=null}
 CAR.turnSeq++;CAR.replyDone=true;
 if(CAR.stream){CAR.stream.getTracks().forEach(t=>t.stop());CAR.stream=null}
 if(CAR.wake){CAR.wake.release().catch(()=>{});CAR.wake=null}
 if('mediaSession' in navigator)try{for(const a of ['play','pause'])navigator.mediaSession.setActionHandler(a,null)}catch(e){}
 if(CAR.state!=='offline')carStatus('idle');
}
function carSetHandsFree(on){CAR.handsFree=on;const b=$('car-handsfree');b.setAttribute('aria-pressed',on?'true':'false');b.classList.toggle('on',on)}
// iOS and some Android browsers only play sound that a tap started: play a silent sound once, from the first tap.
function carUnlockAudio(){
 orbAudioContext();if(CAR.unlocked)return;CAR.unlocked=true;
 try{if('speechSynthesis' in window){const u=new SpeechSynthesisUtterance(' ');u.volume=0;speechSynthesis.speak(u)}}catch(e){}
 try{CAR.audio=CAR.audio||new Audio();CAR.audio.src='data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=';CAR.audio.play().catch(()=>{})}catch(e){}
}
async function carTalkTap(){
 carUnlockAudio();carWakeLock();
 if(CAR.state==='listening')return carFinishListen();      // tap again: send what was heard
 if(CAR.state==='speaking'){carStopSpeaking();return carListenAndSend()}  // barge in
 if(CAR.state==='thinking')return notice('The Chief is still answering. Tap Stop to silence him.');
 return carListenAndSend();
}
async function carBriefing(){
 carUnlockAudio();carWakeLock();if(!CAR.chief)await carOpen();if(!CAR.chief)return;
 carCancelListen();carStopSpeaking();
 const since=CAR.chief.since?new Date(CAR.chief.since):null;
 const when=since&&!isNaN(since)?since.toLocaleString(carLang(),{weekday:'long',hour:'2-digit',minute:'2-digit'}):null;
 const text=carLang().startsWith('nl')?'Geef me mijn briefing: wat is er veranderd'+(when?' sinds '+when:'')+', wat heeft mij nodig, en wat loopt er nu?'
  :'Give me my briefing: what changed'+(when?' since '+when:'')+', what needs me, and what\'s running?';
 return carSend(text);
}
async function carListenAndSend(){
 const engine=carSttEngine();
 if(engine==='none'){carStatus('idle','No speech recognition here');notice('This browser has no speech recognition. Configure a server speech service (VOICE_STT_BASE_URL) and pick “Server” in voice settings.');return}
 carStatus('listening');$('car-interim').textContent='';
 let text='';
 try{text=await (engine==='server'?carRecordServer():carRecognize())}
 catch(e){carStatus('idle');$('car-interim').textContent='';notice('Listening: '+e.message);carSetHandsFree(false);return}
 $('car-interim').textContent='';
 if(CAR.state!=='listening')return;  // stopped meanwhile
 if(!text){carStatus('idle',CAR.handsFree?'No speech for a minute: hands-free off':null);carSetHandsFree(false);return}
 return carSend(text);
}
function carCancelListen(){if(CAR.recCancel){const c=CAR.recCancel;CAR.recCancel=null;c(true)}$('car-interim').textContent='';if(CAR.state==='listening')carStatus('idle')}
function carFinishListen(){if(CAR.recCancel){const c=CAR.recCancel;CAR.recCancel=null;c(false)}}
// Chrome on Android repeats the whole phrase so far in every new result ("kan", "kan je", "kan je het", ...); desktop
// browsers give separate pieces. A piece that starts with the text so far replaces it; one already at its end is skipped.
function carJoinResults(results){
 let text='';
 for(let i=0;i<results.length;i++){
  const t=String(results[i][0]?.transcript||'').replace(/\s+/g,' ').trim();if(!t)continue;
  const low=text.toLowerCase(),tl=t.toLowerCase();
  if(!text||tl.startsWith(low))text=text&&tl===low?text:t;
  else if(!low.endsWith(tl))text+=' '+t;
 }
 return text;
}
// Browser recognition: keeps listening across the browser's own short timeouts until speech, then sends after a pause.
function carRecognize(){
 return new Promise((resolve,reject)=>{
  const started=Date.now();let heard='',current='',done=false,rec=null,pause=null;
  const end=cancelled=>{if(done)return;done=true;clearTimeout(pause);CAR.recCancel=null;try{rec&&rec.abort()}catch(e){}
   resolve(cancelled?'':(heard+' '+current).trim())};
  const fail=err=>{if(done)return;done=true;clearTimeout(pause);CAR.recCancel=null;reject(err)};
  CAR.recCancel=end;
  const once=()=>{
   if(done)return;
   if(!heard&&Date.now()-started>CAR_IDLE_MS)return end(false);
   rec=new CAR_SR();rec.lang=carLang();rec.continuous=true;rec.interimResults=true;rec.maxAlternatives=1;current='';
   rec.onresult=e=>{
    current=carJoinResults(e.results);$('car-interim').textContent=(heard+' '+current).trim();
    clearTimeout(pause);pause=setTimeout(()=>end(false),CAR_SILENCE_MS);
   };
   rec.onerror=e=>{
    if(e.error==='not-allowed'||e.error==='service-not-allowed')fail(new Error('the microphone is blocked for this site. Allow it in the browser or phone settings.'));
    else if(e.error==='network')fail(new Error('speech recognition needs a network connection (or pick “Server” in voice settings).'))};
   rec.onend=()=>{if(done)return;heard=(heard+' '+current).trim();current='';if(heard)return end(false);setTimeout(once,150)};  // nothing heard yet: listen again
   try{rec.start()}catch(e){fail(e)}
  };
  once();
 });
}
// Server recognition: record until a pause after speech (or a minute without speech), then transcribe on the server.
async function carRecordServer(){
 if(!navigator.mediaDevices?.getUserMedia||!window.MediaRecorder)throw new Error('this browser cannot record audio.');
 if(!CAR.stream||CAR.stream.getTracks().every(t=>t.readyState==='ended'))
  CAR.stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true}});
 const blob=await new Promise((resolve,reject)=>{
  const chunks=[],rec=new MediaRecorder(CAR.stream);let spoke=false,quietSince=Date.now(),done=false,cancelled=false;
  const AC=window.AudioContext||window.webkitAudioContext,ctx=AC?new AC():null,an=ctx&&ctx.createAnalyser(),buf=new Float32Array(2048);
  if(ctx){an.fftSize=2048;ctx.createMediaStreamSource(CAR.stream).connect(an)}
  const started=Date.now();
  const stop=c=>{if(done)return;done=true;cancelled=c;clearInterval(timer);CAR.recCancel=null;try{rec.stop()}catch(e){}};
  const timer=setInterval(()=>{
   let rms=0.05;if(an){an.getFloatTimeDomainData(buf);let s=0;for(const v of buf)s+=v*v;rms=Math.sqrt(s/buf.length)}
   const now=Date.now();if(rms>0.02){spoke=true;quietSince=now;$('car-interim').textContent='…'}
   if((spoke&&now-quietSince>CAR_SILENCE_MS)||(!spoke&&now-started>CAR_IDLE_MS)||now-started>CAR_MAX_RECORD_MS)stop(false);
  },100);
  CAR.recCancel=c=>{spoke=spoke||!c;stop(c)};
  rec.ondataavailable=e=>{if(e.data.size)chunks.push(e.data)};
  rec.onstop=()=>{if(ctx)ctx.close().catch(()=>{});resolve(cancelled||!spoke?null:new Blob(chunks,{type:rec.mimeType||'audio/webm'}))};
  rec.onerror=()=>{clearInterval(timer);reject(new Error('recording failed'))};
  rec.start(250);
 });
 if(!blob)return '';
 $('car-interim').textContent='Transcribing…';
 const r=await fetch('/ui/api/voice/transcribe?'+new URLSearchParams({language:carLang()}),{method:'POST',credentials:'same-origin',
  headers:{'x-agent-ui':'1','Content-Type':blob.type||'application/octet-stream'},body:blob});
 const data=await r.json().catch(()=>({}));if(!r.ok)throw new Error(errorText(r.status,data));
 return (data.text||'').trim();
}
// One turn: the text goes to the Chief's conversation; the reply is shown and spoken as it streams in.
async function carSend(text,typed){
 if(!CAR.chief)return;
 CAR.typed=!!typed;  // HLA_UI_V3: typed in the Chief workspace: answered on screen, not read aloud
 carLine('you',text);$('car-needs').hidden=true;carStatus('thinking');
 const seq=++CAR.turnSeq;
 try{
  const r=await api('threads/'+CAR.chief.thread_id+'/messages',{method:'POST',headers:UI_HEADERS,body:JSON.stringify({content:text,voice:!CAR.typed})});
  if(r.queued)carLine('chief','(Queued: I answer this after the reply I am still giving.)');
  if(!CAR.typed)carAckLater(seq);
  carFollow(r.task_id,seq);
 }catch(e){carStatus(navigator.onLine===false?'offline':'idle');carLine('chief','That did not go through: '+e.message);
  carSpeak(carLang().startsWith('nl')?'Dat is niet gelukt.':'That did not go through.')}
}
// HLA_CHIEF_LATENCY_V1: a short spoken acknowledgement when no reply text has arrived 1.2 s after sending, so the
// silence while the Chief thinks is not dead air. Never spoken once the reply started, and never over it.
const CAR_ACK_MS=1200;
function carAckLater(seq){
 clearTimeout(CAR.ackTimer);CAR.gotText=false;
 CAR.ackTimer=setTimeout(()=>{if(seq!==CAR.turnSeq||CAR.gotText||CAR.speaking||CAR.state==='listening')return;
  const ack=carLang().startsWith('nl')?'Momentje.':'One moment.';
  // Server speech: its own clip right away (not held back to be merged with the reply).
  if(carTtsEngine()==='server'){(CAR.clips=CAR.clips||[]).push({text:ack});carServerPump()}else carSpeak(ack)},CAR_ACK_MS);
}
function carFollow(taskId,seq){
 if(CAR.source)CAR.source.close();
 const line=carLine('chief','');let raw='',spoken=0;CAR.replyDone=false;
 if(CAR.state!=='speaking')carStatus('thinking');
 const src=new EventSource('/ui/api/tasks/'+encodeURIComponent(taskId)+'/events');CAR.source=src;
 const take=final=>{
  // Speak every finished sentence once; at the end, the rest.
  if(raw.length<spoken)spoken=0;
  let cut=-1;
  if(final)cut=raw.length;
  else{
   // Sentence ends, and (to start talking sooner) a clause end once there are at least 40 characters to say.
   const tail=raw.slice(spoken),re=/[.!?…:](?=\s)|\n/g;let m;while((m=re.exec(tail)))cut=spoken+m.index+m[0].length;
   if(cut<=spoken){const cl=/[,;](?=\s)/g;while((m=cl.exec(tail)))if(m.index+1>=40)cut=spoken+m.index+1}
  }
  if(cut<=spoken)return;
  const piece=raw.slice(spoken,cut);
  if(!final&&(piece.match(/```/g)||[]).length%2)return;  // inside a code block: wait for its end
  spoken=cut;const say=carPlain(piece);if(say)carSpeak(say);
 };
 src.addEventListener('content',e=>{if(seq!==CAR.turnSeq)return;raw=JSON.parse(e.data).text||'';if(raw)CAR.gotText=true;line.text=carPlain(raw);carRender();take(false)});
 src.addEventListener('done',async e=>{
  src.close();if(CAR.source===src)CAR.source=null;if(seq!==CAR.turnSeq)return;
  const done=JSON.parse(e.data);
  try{
   const d=await api('threads/'+CAR.chief.thread_id+'?limit=4');
   if(seq!==CAR.turnSeq)return;
   const last=[...d.messages].reverse().find(m=>m.role==='assistant'&&m.task_id===taskId);
   const final=last?last.content||'':raw;
   if(!spoken||final.startsWith(raw.slice(0,spoken))){raw=final;take(true)}else raw=final;
   line.text=carPlain(final)||(done.error||'No answer.');carRender();
   const approval=(last&&last.kind==='approval_request')||done.status==='human_approval_required';
   $('car-needs').hidden=!(approval||/needs you|approv|goedkeur/i.test(final));
   if(approval)carSpeak(carLang().startsWith('nl')?'Dit wacht op jouw goedkeuring. Open Needs you op het scherm.':'This waits for your approval. Open Needs you on the screen.');
   // An automatic continuation (or a queued follow-up) runs next in the same conversation: follow it too.
   if(d.thread.status==='running'&&d.thread.current_task_id&&d.thread.current_task_id!==taskId){carFollow(d.thread.current_task_id,seq);return}
  }catch(err){take(true)}
  CAR.replyDone=true;carAfterReply();
 });
 src.addEventListener('error',()=>{
  if(src.readyState!==EventSource.CLOSED||seq!==CAR.turnSeq)return;
  if(CAR.source===src)CAR.source=null;CAR.replyDone=true;
  if(navigator.onLine===false){carStatus('offline');return}
  // The run continues on the server: pick the reply up from the conversation shortly.
  setTimeout(()=>{if(seq===CAR.turnSeq)carLoadHistory().then(carAfterReply).catch(()=>carStatus('idle'))},3000)});
}
function carAfterReply(){
 carRender();  // HLA_UI_V3: the finished reply may fold now
 clearTimeout(CAR.ackTimer);
 // The reply is complete: a short server-speech remainder held back for merging goes out now.
 if(CAR.pendingText){clearTimeout(CAR.pendingTimer);(CAR.clips=CAR.clips||[]).push({text:CAR.pendingText});CAR.pendingText='';carServerPump();return}
 if(!CAR.speaking&&!(CAR.clips||[]).length)carSpoke()}
// Nothing is left to say: ready, or listen again in hands-free mode.
function carSpoke(){
 if(CAR.state==='listening'||CAR.state==='offline')return;
 if(!CAR.replyDone){carStatus('thinking');return}
 carStatus('idle');
 if(CAR.handsFree&&document.body.dataset.view==='chief')setTimeout(()=>{if(CAR.handsFree&&CAR.state==='idle')carListenAndSend()},350);
}
// Speech output. Browser voices: every piece is handed to speechSynthesis at once (it queues them itself), so there is
// no wait between utterances. Server voices (e.g. Kokoro): short pieces are merged (fewer round trips), up to
// CAR_TTS_PARALLEL requests run ahead of playback, and clips play strictly in order the moment the previous one ends.
const CAR_TTS_PARALLEL=3,CAR_TTS_MERGE=60;
function carSpeak(text){
 if(carTtsEngine()==='none'||CAR.typed)return;
 // Long sentences are split at commas: Chrome cuts off utterances that run longer than about 15 seconds.
 const parts=[];for(const part of text.match(/[^,;]{1,220}(?:[,;]|$)|[\s\S]{1,220}/g)||[text]){const p=part.trim();if(p)parts.push(p)}
 if(!parts.length)return;
 if(carTtsEngine()==='server')return carServerQueue(parts);
 CAR.speaking=true;if(CAR.state!=='listening')carStatus('speaking');
 CAR.utterances=CAR.utterances||0;
 for(const p of parts){
  const u=new SpeechSynthesisUtterance(p);u.lang=carLang();u.rate=Number(carPref('rate','1.1'));
  const want=carPref('voice','');if(want){const v=speechSynthesis.getVoices().find(x=>x.name===want);if(v)u.voice=v}
  CAR.utterances++;let ended=false;
  u.onend=u.onerror=()=>{if(ended)return;ended=true;CAR.utterances=Math.max(0,CAR.utterances-1);
   if(!CAR.utterances&&CAR.speaking){CAR.speaking=false;clearInterval(CAR.keepAlive);CAR.keepAlive=null;carSpoke()}};
  speechSynthesis.speak(u);
 }
 // Desktop Chrome stops speaking after ~15 s in one go; a pause/resume keeps it going (Android treats pause as stop).
 if(!CAR.keepAlive&&!/Android/i.test(navigator.userAgent))CAR.keepAlive=setInterval(()=>{if(speechSynthesis.speaking){speechSynthesis.pause();speechSynthesis.resume()}},10000);
}
// Server speech: CAR.clips is the ordered queue of {text, ready (Promise of a blob URL), abort}; the first one plays.
function carServerQueue(parts){
 CAR.clips=CAR.clips||[];CAR.ttsGen=CAR.ttsGen||0;
 // Merge with the last queued clip when that one has not been requested yet and is still short.
 let pending=CAR.pendingText||'';
 for(const p of parts){
  pending=pending?pending+' '+p:p;
  if(pending.length>=CAR_TTS_MERGE){CAR.clips.push({text:pending});pending=''}
 }
 CAR.pendingText=pending;
 // A short remainder is held briefly for the next piece, then sent on its own.
 clearTimeout(CAR.pendingTimer);
 if(pending)CAR.pendingTimer=setTimeout(()=>{if(CAR.pendingText){CAR.clips.push({text:CAR.pendingText});CAR.pendingText='';carServerPump()}},CAR.replyDone?0:350);
 carServerPump();
}
function carServerFetch(clip){
 const ctl=new AbortController(),gen=CAR.ttsGen;clip.abort=()=>ctl.abort();
 clip.ready=fetch('/ui/api/voice/speak',{method:'POST',credentials:'same-origin',headers:UI_HEADERS,signal:ctl.signal,
   body:JSON.stringify({text:clip.text,voice:carPref('servervoice','')||null})})
  .then(r=>r.ok?r.blob():r.json().catch(()=>({})).then(d=>{throw new Error(errorText(r.status,d))}))
  .then(b=>{if(gen!==CAR.ttsGen)return null;const url=URL.createObjectURL(b);clip.url=url;
   // Preload: an Audio element per clip, decoded while the previous one still plays.
   const a=new Audio();a.preload='auto';a.src=url;a.playbackRate=Number(carPref('rate','1.1'));clip.audio=a;try{a.load()}catch(e){}return a})
  .catch(e=>{if(e.name!=='AbortError'&&gen===CAR.ttsGen)notice('Speech: '+e.message);return null})
  .then(a=>{clip.fetched=true;if(gen===CAR.ttsGen)setTimeout(carServerPump,0);return a});
}
function carServerPump(){
 const clips=CAR.clips||[];
 // Up to CAR_TTS_PARALLEL requests in flight, in queue order, however far ahead of playback they are.
 let inFlight=clips.filter(c=>c.ready&&!c.fetched).length;
 for(const c of clips){if(inFlight>=CAR_TTS_PARALLEL)break;if(!c.ready){carServerFetch(c);inFlight++}}
 if(CAR.playing||!clips.length){if(!clips.length&&!CAR.playing&&!CAR.pendingText&&CAR.speaking){CAR.speaking=false;carSpoke()}return}
 const clip=clips[0];CAR.playing=true;CAR.speaking=true;if(CAR.state!=='listening')carStatus('speaking');
 const gen=CAR.ttsGen;
 clip.ready.then(a=>{
  if(gen!==CAR.ttsGen)return;
  const next=()=>{if(gen!==CAR.ttsGen)return;if(clip.url)URL.revokeObjectURL(clip.url);CAR.clips.shift();CAR.playing=false;carServerPump()};
  if(!a)return next();
  CAR.audio=a;a.onended=next;a.onerror=next;orbAudio(a);
  const p=a.play();if(p&&p.catch)p.catch(()=>next());
 });
}
function carSpeakNext(){carServerPump()}
function carStopSpeaking(){
 const was=CAR.speaking;CAR.speaking=false;clearInterval(CAR.keepAlive);CAR.keepAlive=null;CAR.utterances=0;
 // Barge-in or stop: abort every request in flight, forget the queue, stop the clip that plays.
 CAR.ttsGen=(CAR.ttsGen||0)+1;clearTimeout(CAR.pendingTimer);CAR.pendingText='';
 for(const c of CAR.clips||[]){try{c.abort&&c.abort()}catch(e){}if(c.url)URL.revokeObjectURL(c.url)}
 CAR.clips=[];CAR.playing=false;CAR.speakQueue=[];clearTimeout(CAR.ackTimer);
 try{if('speechSynthesis' in window)speechSynthesis.cancel()}catch(e){}
 if(CAR.audio){try{CAR.audio.pause()}catch(e){}}
 if(was&&CAR.state==='speaking')carStatus(CAR.replyDone?'idle':'thinking');
}

// ── HLA_UI_V3: the shell. Five sections (Home, Chief, Work, Infrastructure, Settings) with contextual tabs, the
// Home command center, the Chief workspace (voice + text, animated orb), Work (filters, concise titles, project tabs),
// the command palette (Ctrl/Cmd+K), the notification center and the calm unhinged indicator. See ui/DESIGN.md. ──────
// A view belongs to one section; a view a later block adds without an entry here goes to Settings.
const SECTIONS=[
 {key:'home',label:'Home',views:['overview']},
 {key:'chief',label:'Chief',views:['chief','threads']},
 {key:'work',label:'Work',views:['tasks','projects','queue']},
 {key:'infrastructure',label:'Infrastructure',views:['infrastructure','websites']},
 // HLA_UI_V4: Settings grouped by what you configure (the page title is the group's name).
 {key:'settings',label:'Settings',views:['agents','models','knowledge','mcp','connections','policy','costs','audit']},
];
VIEW_TITLES.overview='Home';
// Tabs carry the short name; the page title names the whole group.
const TAB_LABELS={chief:'Assistant',threads:'Conversations',infrastructure:'Servers',agents:'Agents',models:'Models',knowledge:'Knowledge',mcp:'Tools & MCP',
 connections:'Connections',policy:'Security',costs:'Costs',audit:'Audit'};
VIEW_TITLES.chief='Chief';
Object.assign(VIEW_TITLES,{agents:'AI & Agents',models:'Models & Providers',knowledge:'Knowledge & Memory',mcp:'Tools & MCP',
 connections:'Connections & Credentials',policy:'Security & Permissions',costs:'Costs & Usage',audit:'Audit & Activity'});
const sectionOf=v=>(SECTIONS.find(s=>s.views.includes(v))||SECTIONS[4]).key;
const sectionDef=k=>SECTIONS.find(s=>s.key===k);
function tabButton(v){return $('subnav').querySelector('button[data-view="'+v+'"]')}

(function(){
 // Every per-view button (also the ones other blocks added) becomes a tab of its section, in the order above.
 const subnav=$('subnav');
 const buttons=[...document.querySelectorAll('aside button.nav[data-view]:not(.nav-section)')];
 for(const sec of SECTIONS){const g=el('div','subnav-group');g.dataset.section=sec.key;g.setAttribute('role','tablist');g.setAttribute('aria-label',sec.label);g.hidden=true;subnav.append(g)}
 for(const b of buttons){const v=b.dataset.view;if(!SECTIONS.some(s=>s.views.includes(v)))SECTIONS[4].views.push(v)}
 for(const sec of SECTIONS)for(const v of sec.views){
  const b=buttons.find(x=>x.dataset.view===v);if(!b)continue;
  b.className='tab';b.setAttribute('role','tab');b.removeAttribute('aria-label');b.replaceChildren(TAB_LABELS[v]||VIEW_TITLES[v]||v);
  subnav.querySelector('[data-section="'+sec.key+'"]').append(b);
 }
 document.querySelector('aside .legacy-nav')?.remove();
 // Section buttons open the page of the section that was open last (or its first page).
 for(const b of document.querySelectorAll('aside .nav-section')){
  if(b.dataset.section==='home')continue;
  b.addEventListener('click',()=>{const sec=sectionDef(b.dataset.section);const last=store.get('sec.'+sec.key);
   const v=sec.views.includes(last)&&tabButton(last)?last:sec.views.find(x=>tabButton(x));if(v)tabButton(v).click()});
 }
 // The task drawer lives at the top level so it opens over any page (Home, Work, the Chief).
 $('workspace').append($('detail'));
 // Home: the website monitor's tile joins the infrastructure summary.
 if($('ws-tile'))$('infra-panel').append($('ws-tile'));
})();
// After every page switch: mark the section, show its tabs, name it in the breadcrumb, remember the page.
const v3Switch=switchView;
switchView=function(view){v3Switch(view);markSection(view);store.set('sec.'+sectionOf(view),view);if(sectionOf(view)!=='home')closeLaunch()};
function markSection(view){
 const key=sectionOf(view),sec=sectionDef(key);document.body.dataset.section=key;
 for(const b of document.querySelectorAll('aside .nav-section')){const on=b.dataset.section===key;b.classList.toggle('active',on);
  if(on)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current')}
 const groups=[...$('subnav').querySelectorAll('.subnav-group')];let tabs=0;
 for(const g of groups){g.hidden=g.dataset.section!==key;if(!g.hidden)tabs=g.querySelectorAll('button').length}
 $('subnav').hidden=tabs<2;
 for(const t of $('subnav').querySelectorAll('button[data-view]'))t.setAttribute('aria-selected',t.dataset.view===view?'true':'false');
 $('page-crumb').textContent=key==='home'||sec.label===VIEW_TITLES[view]?'':sec.label;
 if(view==='overview')$('page-title').textContent=greeting();
 sizeChief();
}
// Deep links: #/<section>[/<page>] and #<page> open that page; the older links (#chief, #websites, #project=…) still work.
const v3Link=openFromLink;
openFromLink=function(hash){
 const m=/^#\/?([a-z]+)(?:\/([a-z]+))?$/.exec(hash||'');
 if(m&&!['chief','websites','inbox'].includes(m[1])||m&&m[2]){
  const [,a,b]=m,view=b&&tabButton(b)?b:sectionDef(a)?null:(tabButton(a)||a==='overview'?a:null);
  if(view||sectionDef(a)){history.replaceState(null,'',location.pathname);
   if(view)openView(view);else document.querySelector('aside .nav-section[data-section="'+a+'"]').click();return true}
 }
 return v3Link(hash);
};
openView=function(view){const b=document.querySelector('button[data-view="'+view+'"]');if(b)b.click()};

// Sidebar: collapses to an icon rail (remembered; automatic between phone and laptop widths).
function sideCollapsed(){const pref=store.get('side');const mid=window.matchMedia('(min-width:721px) and (max-width:1100px)').matches;
 const on=pref==='rail'||(pref!=='open'&&mid);document.body.classList.toggle('side-collapsed',on);
 $('side-collapse').setAttribute('aria-expanded',on?'false':'true');$('side-collapse').setAttribute('aria-label',on?'Expand the sidebar':'Collapse the sidebar')}
$('side-collapse').onclick=()=>{store.set('side',document.body.classList.contains('side-collapsed')?'open':'rail');sideCollapsed()};
window.matchMedia('(max-width:1100px)').addEventListener('change',sideCollapsed);sideCollapsed();
for(const b of document.querySelectorAll('aside .nav-section'))b.addEventListener('mouseenter',()=>{if(document.body.classList.contains('side-collapsed'))b.dataset.tip=b.textContent.trim()});
// Profile menu (notifications on this device, palette, shortcuts, log out).
function menuToggle(btn,menu,open){open=open??menu.hidden;menu.hidden=!open;btn.setAttribute('aria-expanded',String(open));if(open)menu.querySelector('button:not([hidden])')?.focus()}
$('profile-btn').onclick=e=>{e.stopPropagation();menuToggle($('profile-btn'),$('profile-menu'))};
$('profile-menu').addEventListener('click',e=>{if(e.target.closest('.menu-item'))menuToggle($('profile-btn'),$('profile-menu'),false)});
document.addEventListener('click',e=>{if(!$('profile-menu').hidden&&!e.target.closest('.profile-wrap'))menuToggle($('profile-btn'),$('profile-menu'),false);
 if(!$('notif-panel')?.hidden&&!e.target.closest('#notif-panel,#notif-btn'))notifToggle(false)});
$('menu-palette').onclick=()=>paletteOpen();
$('menu-shortcuts').onclick=()=>shortcutsHelp();

// Platform status (sidebar, Home), from the last summary call and the browser's network state.
const HUD={chief:null,source:null,seq:0,busy:false,lastLoad:0,running:0,needs:0,px:null,pxAt:0,ws:null,issues:0};
function hudSystem(state,text){
 for(const id of ['sys-status','side-status']){const c=$(id);if(c)c.dataset.state=state}
 $('sys-status-text').textContent=text;$('side-status-text').textContent=state==='ok'?'All systems normal':text;
 if(!$('side-status').querySelector('.dot'))$('side-status').prepend(el('i','dot'));
}
window.addEventListener('offline',()=>hudSystem('bad','Offline'));
window.addEventListener('online',()=>hudSystem('wait','Reconnecting'));
$('side-status').onclick=()=>openView('infrastructure');
const hudSetInboxCount=setInboxCount;
setInboxCount=function(n){
 hudSetInboxCount(n);HUD.needs=n||0;
 $('k-needs').textContent=String(n||0);$('kpi-needs').classList.toggle('on',!!n);
 $('k-needs-foot').textContent=n?'approvals and decisions':'nothing waiting';
 hudCore();notifBadge();
};
function hudCore(){
 const state=HUD.busy?'thinking':HUD.needs?'attention':HUD.running?'thinking':'idle';
 const c=$('core');if(c)c.dataset.state=state;
}
document.addEventListener('visibilitychange',()=>document.body.classList.toggle('anim-paused',document.hidden));

// ── Home ───────────────────────────────────────────────────────────────────────────────────────────────────────────
const hudMoney=v=>{const n=Number(v||0);return n>=100?'$'+n.toFixed(0):n>=1?'$'+n.toFixed(2):'$'+n.toFixed(n>=0.01?2:3)};
function greeting(){const h=new Date().getHours();return h<5?'Good night':h<12?'Good morning':h<18?'Good afternoon':'Good evening'}
function homeGreeting(){if(document.body.dataset.view==='overview')$('page-title').textContent=greeting()}
async function loadHud(force){
 if(!force&&Date.now()-HUD.lastLoad<14000)return;HUD.lastLoad=Date.now();homeGreeting();
 const [summary,tasks,projects,today,month,ws]=await Promise.all([api('summary').catch(()=>null),api('tasks?limit=12').catch(()=>null),
  api('projects').catch(()=>null),api('costs?days=1').catch(()=>null),api('costs?days=30').catch(()=>null),api('websites/summary').catch(()=>null)]);
 if(summary){hudSystem('ok','Online');HUD.running=Number(summary.running||0);$('k-running').textContent=fmt(summary.running);
  $('k-running-foot').textContent=fmt(summary.completed)+' done · '+fmt(summary.failed)+' failed'}
 else hudSystem(navigator.onLine===false?'bad':'warn',navigator.onLine===false?'Offline':'Degraded');
 if(today&&today.totals){$('k-spend').textContent=hudMoney(today.totals.cost);
  $('k-spend-foot').textContent='this month '+hudMoney(month&&month.totals?month.totals.cost:0)}
 if(tasks){HUD.tasks=tasks.tasks||[];renderFeed(HUD.tasks)}
 if(projects)renderOvProjects(projects.projects||[]);
 HUD.ws=ws;homeInfra();homeStatus();hudCore();notifRender();
 // Proxmox inventory goes through the gateway (slow): at most every two minutes, in the background.
 if(Date.now()-HUD.pxAt>120000){HUD.pxAt=Date.now();api('proxmox').then(d=>{HUD.px=d;homeInfra();homeStatus();notifRender()}).catch(()=>{})}
}
function infraFacts(){
 const f={text:[],state:'ok',warnings:[]};const px=HUD.px;
 if(px&&px.available&&px.nodes){const nodes=px.nodes,bad=nodes.filter(n=>!n.ok);
  const vms=nodes.flatMap(n=>n.vms||[]),running=vms.filter(v=>v.status==='running').length;
  f.text.push(nodes.length+' Proxmox node'+(nodes.length===1?'':'s')+(bad.length?' · '+bad.length+' unreachable':' healthy'),running+' VM'+(running===1?'':'s')+' running');
  if(bad.length){f.state='warn';for(const n of bad)f.warnings.push({title:'Proxmox node '+(n.label||n.node)+' unreachable',sub:n.error||'',go:()=>openView('infrastructure')})}}
 const ws=HUD.ws;
 if(ws&&ws.total){const c=ws.counts||{},attn=(c.down||0)+(c.degraded||0);
  f.text.push(attn?attn+' website'+(attn===1?' needs':'s need')+' attention':ws.total+' website'+(ws.total===1?'':'s')+' up');
  if(c.down){f.state='bad';f.warnings.push({title:c.down+' website'+(c.down===1?' is':'s are')+' down',sub:'Infrastructure · Websites',go:()=>openView('websites')})}
  else if(attn){if(f.state==='ok')f.state='warn';f.warnings.push({title:attn+' website'+(attn===1?' is':'s are')+' degraded',sub:'Infrastructure · Websites',go:()=>openView('websites')})}}
 return f;
}
function homeInfra(){
 const f=infraFacts(),b=$('home-infra');b.dataset.state=f.text.length?f.state:'wait';
 const servers=f.text.filter(t=>!/website/.test(t));
 $('home-infra-text').textContent=servers.length?servers.join(' · '):'No server data yet';
 b.dataset.state=servers.length?(f.warnings.some(w=>!/website/.test(w.title))?'warn':'ok'):'wait';
 HUD.issues=f.warnings.length;
}
function homeStatus(){
 const f=infraFacts(),parts=[];
 if(HUD.running)parts.push(HUD.running+' run'+(HUD.running===1?'':'s')+' in progress');
 if(HUD.needs)parts.push(HUD.needs+' waiting for you');
 const off=$('sys-status').dataset.state!=='ok',issue=f.state!=='ok'&&f.warnings.length;
 const head=off?'The platform is not fully reachable':issue?f.warnings[0].title:'All systems operational';
 $('home-status-text').textContent=[head,...parts].join(' · ');
 $('home-status').dataset.state=off?'bad':issue?f.state:'ok';
 const dot=$('home-status-dot');dot.className='dot '+(off?'bad':issue?f.state:'ok');
}
$('infra-open').onclick=()=>openView('infrastructure');
$('home-infra').onclick=()=>{const f=infraFacts();openView(f.warnings.some(w=>/website/.test(w.title))?'websites':'infrastructure')};
function renderFeed(rows){
 const list=$('feed-list');list.replaceChildren();
 if(!rows.length){list.append(el('li','muted','No activity yet.'));return}
 for(const t of rows.slice(0,8)){
  const li=el('li','feed-item');const b=el('button','feed-row');b.type='button';const st=t.status||'unknown';
  const text=el('span','feed-text',t.display_title||t.summary||'Run');
  b.append(el('i','feed-dot st-dot-'+st),text,el('span','feed-meta',(t.agent_name?t.agent_name+' · ':'')+when(t.created_at)));
  b.title=(STATUS_LABELS[st]||st)+' — '+(t.display_title||'');b.setAttribute('aria-label',(t.display_title||'Run')+', '+(STATUS_LABELS[st]||st));
  b.onclick=()=>openTaskDetail(t.id);li.append(b);list.append(li);
 }
}
const projectStatusLabel=s=>(PROJECT_STATUS[s]||[String(s||'').replace(/_/g,' ')])[0];
const PROJECT_TONE={running:'running',awaiting_approval:'human_approval_required',paused:'incomplete',planning:'running',completed:'completed',cancelled:'cancelled',plan_failed:'failed'};
function renderOvProjects(all){
 const box=$('ov-projects');box.replaceChildren();
 const active=all.filter(p=>['running','awaiting_approval','paused','planning'].includes(p.status));
 const done=active.reduce((a,p)=>a+Number(p.done_count||0),0),total=active.reduce((a,p)=>a+Number(p.task_count||0),0);
 $('k-projects').textContent=String(active.length);
 $('k-projects-bar').style.width=(total?Math.round(done/total*100):0)+'%';
 $('k-projects-foot').textContent=total?done+' of '+total+' tasks done':'none running';
 HUD.projects=all;
 const shown=(active.length?active:all).slice(0,5);
 if(!shown.length){box.append(el('p','muted','No projects yet. Ask the Chief to plan one.'));return}
 for(const p of shown){
  const pct=p.task_count?Math.round(Number(p.done_count||0)/Number(p.task_count)*100):0;
  const b=el('button','ovp');b.type='button';
  const head=el('span','ovp-head');head.append(el('span','ovp-title',p.title),el('span','pill st-'+(PROJECT_TONE[p.status]||'unknown'),projectStatusLabel(p.status)));
  const bar=el('span','kpi-bar');const fill=el('i');fill.style.width=pct+'%';bar.append(fill);
  b.append(head,bar,el('span','ovp-foot',(p.done_count||0)+' of '+(p.task_count||0)+' tasks'+(p.attention?' · '+p.attention+' need attention':'')));
  b.onclick=()=>openItem('project',p.id).catch(e=>notice(e.message));box.append(b);
 }
}
// "Needs attention" is only shown when there is something to act on; otherwise one calm line.
const v3RenderInbox=renderInbox;
renderInbox=function(items){v3RenderInbox(items);HUD.inbox=items;const empty=!items.length;$('inbox-panel').hidden=empty;$('home-clear').hidden=!empty;notifRender()};
const hudInboxLoad=loadInbox;
loadInbox=async function(){const d=await hudInboxLoad();if(!$('overview').hidden)loadHud(false).catch(()=>{});return d};
document.querySelector('aside .nav-section[data-section="home"]').addEventListener('click',()=>loadHud(true).catch(()=>{}));
$('refresh').addEventListener('click',()=>loadHud(true).catch(()=>{}));
for(const k of document.querySelectorAll('.kpi[data-go]'))k.addEventListener('click',()=>{
 const go=k.dataset.go;if(go==='inbox'){(HUD.needs?$('inbox-panel'):$('home-clear')).scrollIntoView({behavior:'smooth',block:'start'});return}openView(go)});
$('feed-all').onclick=()=>openView('tasks');
$('ov-projects-all').onclick=()=>openView('projects');

// "New task": the launch form in a sheet (side panel; bottom sheet on phones).
function launchSheet(open){
 const s=$('launch-sheet');if(open===!s.hidden)return;s.hidden=!open;document.body.classList.toggle('sheet-open',open);
 if(open)setTimeout(()=>$('task-text').focus({preventScroll:true}),30);else $('ov-new-task').focus({preventScroll:true});
}
function closeLaunch(){if(!$('launch-sheet').hidden){$('launch-sheet').hidden=true;document.body.classList.remove('sheet-open')}}
$('ov-new-task').onclick=()=>launchSheet(true);
$('launch-close').onclick=()=>launchSheet(false);
$('launch-backdrop').onclick=()=>launchSheet(false);
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!$('launch-sheet').hidden&&!document.querySelector('dialog[open]')){e.preventDefault();launchSheet(false)}});

// Ask Chief from Home: the message goes to the Chief's conversation; the reply streams in under the input.
const CMD_TEXT={briefing:'Give me a short briefing: what changed since my last check, what needs me and what is running.',
 needs:'What needs me right now?',costs:'What did the platform spend today, and on what?'};
async function cmdSend(text){
 text=String(text||'').trim();if(!text||HUD.busy)return;
 const reply=$('cmd-reply'),body=$('cmd-reply-body'),state=$('cmd-reply-state');
 reply.hidden=false;body.replaceChildren(el('p','muted',text));state.textContent='Sending…';
 HUD.busy=true;hudCore();$('cmd-send').disabled=true;const seq=++HUD.seq;
 try{
  if(!HUD.chief)HUD.chief=await api('chief');
  const r=await api('threads/'+HUD.chief.thread_id+'/messages',{method:'POST',headers:UI_HEADERS,body:JSON.stringify({content:text})});
  $('cmd-input').value='';
  state.textContent=r.queued?'Queued behind the reply in progress':'Thinking…';
  if(r.task_id)cmdFollow(r.task_id,seq);else cmdDone(seq);
 }catch(e){state.textContent='';body.replaceChildren(el('p','error','That did not go through: '+e.message));cmdDone(seq)}
}
function cmdDone(seq){if(seq!==HUD.seq)return;HUD.busy=false;hudCore();$('cmd-send').disabled=false}
function cmdFollow(taskId,seq){
 if(HUD.source)HUD.source.close();
 const body=$('cmd-reply-body'),state=$('cmd-reply-state');let raw='',timer=null;
 const draw=()=>{timer=null;window.renderTaskMarkdown(body,raw)};
 const src=new EventSource('/ui/api/tasks/'+encodeURIComponent(taskId)+'/events');HUD.source=src;
 src.addEventListener('content',e=>{if(seq!==HUD.seq)return;raw=JSON.parse(e.data).text||'';state.textContent='Answering…';if(!timer)timer=setTimeout(draw,90)});
 src.addEventListener('done',async e=>{
  src.close();if(HUD.source===src)HUD.source=null;if(seq!==HUD.seq)return;
  const done=JSON.parse(e.data);
  try{const d=await api('threads/'+HUD.chief.thread_id+'?limit=4');
   const last=[...d.messages].reverse().find(m=>m.role==='assistant'&&m.task_id===taskId);if(last&&last.content)raw=last.content}catch(err){}
  clearTimeout(timer);window.renderTaskMarkdown(body,raw||done.error||'No answer.');
  state.textContent=done.status==='completed'||!done.status?'':String(done.status).replace(/_/g,' ');
  cmdDone(seq);refreshInbox();
 });
 src.onerror=()=>{if(src.readyState===2){if(HUD.source===src)HUD.source=null;state.textContent='Connection lost: open the conversation';cmdDone(seq)}};
}
$('cmd-form').addEventListener('submit',e=>{e.preventDefault();cmdSend($('cmd-input').value)});
for(const c of document.querySelectorAll('.cmd-quick [data-cmd]'))c.addEventListener('click',()=>cmdSend(CMD_TEXT[c.dataset.cmd]));
$('cmd-mic').onclick=()=>{carUnlockAudio();openView('chief')};
$('cmd-open').onclick=()=>openView('chief');
if($('workspace').hidden===false)loadHud(true).catch(()=>{});
new MutationObserver(()=>{if(!$('workspace').hidden)loadHud(false).catch(()=>{})}).observe($('workspace'),{attributes:true,attributeFilter:['hidden']});
setInterval(()=>{if(!document.hidden&&!$('workspace').hidden&&!$('overview').hidden)loadHud(false).catch(()=>{})},30000);

// ── Chief workspace: the car-mode controls in a calmer layout, a text input, and the orb ────────────────────────────
(function(){
 const sec=$('chief');sec.classList.remove('car-mode');sec.classList.add('chief-ws');sec.setAttribute('aria-label','Chief');
 const stage=el('div','chief-stage');
 const orb=el('div','orb');orb.id='chief-orb';orb.dataset.state='idle';orb.setAttribute('aria-hidden','true');
 orb.innerHTML='<i class="orb-glow"></i><i class="orb-ring"></i><i class="orb-core"></i>';
 const text=el('div','chief-stage-text');const name=el('strong',null,'Chief');
 text.append(name,$('car-status'),$('car-interim'));
 const acts=el('div','chief-stage-actions');
 const focus=el('button','ghost small','Car mode');focus.type='button';focus.id='car-focus';focus.setAttribute('aria-pressed','false');
 focus.title='Bigger controls for driving';
 focus.onclick=()=>{const on=!document.body.classList.contains('car-focus');document.body.classList.toggle('car-focus',on);focus.setAttribute('aria-pressed',String(on));store.set('chief.focus',on?'1':null)};
 if(store.get('chief.focus')==='1'){document.body.classList.add('car-focus');focus.setAttribute('aria-pressed','true')}
 const gear=$('car-settings-toggle');gear.className='icon-btn';gear.textContent='';gear.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/></svg>';
 const exit=$('car-exit');exit.className='icon-btn';exit.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg>';exit.setAttribute('aria-label','Leave the Chief');
 acts.append(focus,gear,exit);stage.append(orb,text,acts);
 sec.querySelector('.car-top').replaceWith(stage);
 const dock=el('div','chief-dock');
 const form=el('form','chief-form');form.id='chief-form';form.autocomplete='off';
 const input=el('input');input.id='chief-input';input.maxLength=12000;input.placeholder='Type to the Chief…';input.setAttribute('aria-label','Message to the Chief');input.enterKeyHint='send';
 const send=el('button','ask-send');send.type='submit';send.setAttribute('aria-label','Send');send.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h13M13 6l6 6-6 6"/></svg>';
 form.append(input,send);
 form.onsubmit=async e=>{e.preventDefault();const t=input.value.trim();if(!t)return;input.value='';carStopSpeaking();carCancelListen();if(await chiefReady())carSend(t,true)};
 const controls=el('div','chief-controls');
 const talk=$('car-talk');talk.querySelector('.car-talk-icon').innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg>';
 controls.append($('car-handsfree'),talk,$('car-briefing'),$('car-stop'));
 sec.querySelector('.car-row')?.remove();
 dock.append($('car-needs'),form,controls);
 sec.append(dock,$('car-settings'));
})();
const v3CarStatus=carStatus;
carStatus=function(state,text){v3CarStatus(state,text);const o=$('chief-orb');if(o){o.dataset.state=state==='idle'&&HUD.needs?'idle':state;
 o.classList.toggle('orb-sim',state==='speaking'&&carTtsEngine()!=='server');if(state!=='speaking')o.style.setProperty('--lvl','0')}};
// Server speech: the orb follows the actual audio level (Web Audio analyser). Audio is only routed through the context
// while it runs, so a suspended context can never silence the Chief.
function orbAudioContext(){try{const AC=window.AudioContext||window.webkitAudioContext;if(!AC)return;if(!CAR.actx)CAR.actx=new AC();if(CAR.actx.state!=='running')CAR.actx.resume().catch(()=>{})}catch(e){}}
function orbAudio(a){
 try{const ctx=CAR.actx;if(!ctx||ctx.state!=='running'||matchMedia('(prefers-reduced-motion: reduce)').matches)return;
  if(!a._orb){const src=ctx.createMediaElementSource(a),an=ctx.createAnalyser();an.fftSize=512;src.connect(an);an.connect(ctx.destination);a._orb=an}
  const an=a._orb,buf=new Uint8Array(an.fftSize),o=$('chief-orb');let lvl=0;
  const tick=()=>{if(CAR.audio!==a||a.paused||a.ended){o.style.setProperty('--lvl','0');return}
   an.getByteTimeDomainData(buf);let sum=0;for(const v of buf){const d=(v-128)/128;sum+=d*d}
   lvl=lvl*.7+Math.min(1,Math.sqrt(sum/buf.length)*4)*.3;o.style.setProperty('--lvl',lvl.toFixed(3));requestAnimationFrame(tick)};
  requestAnimationFrame(tick);
 }catch(e){}
}
function sizeChief(){const c=$('chief');if(!c||c.hidden)return;const top=c.getBoundingClientRect().top+window.scrollY;
 const bar=window.matchMedia('(max-width:720px)').matches?(document.querySelector('aside.sidebar').getBoundingClientRect().height+12):20;
 c.style.setProperty('--chief-h',Math.max(380,window.innerHeight-top-bar)+'px')}
window.addEventListener('resize',()=>sizeChief());
// Ask the Chief from anywhere (palette): open the workspace and send the text as a typed message.
// Opening the Chief loads its history (which replaces the transcript): a typed message waits for that first.
const v3CarOpen=carOpen;
carOpen=function(){CAR.opening=v3CarOpen().finally(()=>{CAR.opening=null});return CAR.opening};
async function chiefReady(){if(CAR.opening)await CAR.opening.catch(()=>{});if(!CAR.chief)await carOpen().catch(()=>{});return !!CAR.chief}
async function chiefAsk(text){openView('chief');if(await chiefReady())carSend(text,true)}

// ── Work: status filters for tasks, project detail in tabs ────────────────────────────────────────────────────────
(function(){
 const sel=$('task-status-filter');
 const FILTERS=[['','All'],['running','Active'],['human_approval_required','Waiting for approval'],['completed','Completed'],['failed,incomplete','Failed']];
 for(const [v] of FILTERS)if(v&&![...sel.options].some(o=>o.value===v))sel.add(new Option(v==='failed,incomplete'?'Failed or incomplete':v,v));
 const bar=el('div','work-filters seg');bar.setAttribute('role','group');bar.setAttribute('aria-label','Show tasks');
 const mark=()=>{for(const b of bar.children)b.setAttribute('aria-pressed',String(b.dataset.status===sel.value))};
 for(const [v,label] of FILTERS){const b=el('button',null,label);b.type='button';b.dataset.status=v;
  b.onclick=()=>{sel.value=v;sel.dispatchEvent(new Event('change'));mark()};bar.append(b)}
 sel.addEventListener('change',mark);mark();
 const panel=$('tasks').querySelector('.panel');panel.querySelector('.toolbar').before(bar);
 const h=panel.querySelector('.panel-heading h2');if(h)h.textContent='Tasks';
})();
// Project detail: Overview · Plan · Log · Files, instead of one long page. The open tab is kept per project.
const projectTab=new Map();
function projectTabs(id){
 const main=$('project-main');if(main.dataset.project!==String(id)||main.querySelector('.ptabs'))return;
 const status=main.dataset.status,head=main.querySelector('.project-head');if(!head)return;
 const kids=[...main.children].filter(x=>x!==head);
 const panels={overview:el('div','ptab-panel'),plan:el('div','ptab-panel'),log:el('div','ptab-panel'),files:el('div','ptab-panel')},meta=[];
 for(const k of kids){
  const c=k.classList;
  if(c.contains('project-facts')||c.contains('project-domains-line'))meta.push(k);  // compact project metadata stays above the tabs
  else if(c.contains('project-notes'))panels.log.append(k);
  else if(c.contains('project-files')||c.contains('project-kb')){if(k.tagName==='DETAILS')k.open=true;panels.files.append(k)}
  else if(c.contains('project-round')||c.contains('ptask-board')||c.contains('project-next')||(c.contains('fineprint')&&/Review the plan/.test(k.textContent)))panels.plan.append(k);
  else panels.overview.append(k);
 }
 // Overview gets the progress and a compact task list; the full editable plan is one tab further.
 const cards=[...panels.plan.querySelectorAll('.ptask')];
 if(cards.length){
  const done=cards.filter(c=>/\b(done|skipped)\b/.test(c.querySelector('.pill')?.className||'')).length;
  const prog=el('div','project-progress');const bar=el('span','kpi-bar');const fill=el('i');fill.style.width=Math.round(done/cards.length*100)+'%';bar.append(fill);
  prog.append(bar,el('span',null,done+' of '+cards.length+' tasks done'));panels.overview.prepend(prog);
  const list=el('div','ptask-minis');
  for(const c of cards.slice(0,12)){const row=el('button','ptask-mini');row.type='button';row.style.all='';
   const title=c.querySelector('.ptask-title,h3,strong')?.textContent||'Task';const pill=c.querySelector('.pill');
   row.append(el('span',null,title));if(pill)row.append(pill.cloneNode(true));
   row.onclick=()=>{select('plan');c.scrollIntoView({block:'center'});c.classList.add('flash');setTimeout(()=>c.classList.remove('flash'),2000)};list.append(row)}
  panels.overview.append(list);
 }
 const nav=el('div','ptabs');nav.setAttribute('role','tablist');nav.setAttribute('aria-label','Project');
 const count=(n)=>n?el('span','count',' '+n):'';
 const tabs=[['overview','Overview'],['plan','Plan',cards.length],['log','Log'],['files','Files',panels.files.querySelectorAll('.att-item,.kb-entry,.project-kb li').length]];
 const btns={};
 function select(k){projectTab.set(String(id),k);for(const [key] of tabs){const on=key===k;btns[key].setAttribute('aria-selected',String(on));btns[key].tabIndex=on?0:-1;panels[key].hidden=!on}}
 for(const [k,label,n] of tabs){const b=el('button',null,label);b.type='button';b.setAttribute('role','tab');b.dataset.ptab=k;if(n)b.append(count(n));b.onclick=()=>select(k);btns[k]=b;nav.append(b)}
 nav.addEventListener('keydown',e=>{if(!['ArrowLeft','ArrowRight'].includes(e.key))return;const keys=tabs.map(t=>t[0]),i=keys.indexOf(projectTab.get(String(id)));
  const k=keys[(i+(e.key==='ArrowRight'?1:keys.length-1))%keys.length];select(k);btns[k].focus()});
 head.after(...meta,nav,...Object.values(panels));
 select(projectTab.get(String(id))||(['awaiting_approval','plan_failed'].includes(status)?'plan':'overview'));
}
const v3OpenProject=openProject;
openProject=async function(id){await v3OpenProject(id);projectTabs(id)};
const v3OpenItem=openItem;
openItem=async function(kind,id,focusTask){if(kind==='project'&&focusTask)projectTab.set(String(id),'plan');return v3OpenItem(kind,id,focusTask)};

// ── Command palette (Ctrl/Cmd+K): pages, actions, recent work, agents, servers, and "Ask Chief" ───────────────────
const PALETTE={items:[],sel:0,box:null,data:null,at:0};
function paletteOpen(prefill){
 if(PALETTE.box){PALETTE.box.querySelector('input').focus();return}
 const wrap=el('div','palette');wrap.id='palette';wrap.setAttribute('role','dialog');wrap.setAttribute('aria-modal','true');wrap.setAttribute('aria-label','Command palette');
 const box=el('div','palette-box');const row=el('div','palette-input');
 row.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2"/></svg>';
 const input=el('input');input.id='palette-input';input.placeholder='Search pages, projects, tasks… or ask the Chief';input.setAttribute('role','combobox');
 input.setAttribute('aria-expanded','true');input.setAttribute('aria-controls','palette-list');input.setAttribute('aria-autocomplete','list');input.autocomplete='off';
 row.append(input,el('kbd',null,'Esc'));
 const list=el('ul','palette-list');list.id='palette-list';list.setAttribute('role','listbox');
 const foot=el('div','palette-foot');foot.append(el('span',null,'↑↓ to move'),el('span',null,'Enter to open'),el('span',null,'Esc to close'));
 box.append(row,list,foot);wrap.append(box);document.body.append(wrap);PALETTE.box=wrap;PALETTE.prev=document.activeElement;
 wrap.addEventListener('mousedown',e=>{if(e.target===wrap)paletteClose()});
 input.addEventListener('input',()=>{PALETTE.sel=0;paletteRender()});
 input.addEventListener('keydown',e=>{
  if(e.key==='Escape'){e.preventDefault();paletteClose()}
  else if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();const n=PALETTE.items.length;if(n){PALETTE.sel=(PALETTE.sel+(e.key==='ArrowDown'?1:n-1))%n;paletteRender(true)}}
  else if(e.key==='Enter'){e.preventDefault();const it=PALETTE.items[PALETTE.sel];if(it)paletteRun(it)}
 });
 if(prefill)input.value=prefill;
 paletteRender();input.focus();
 if(Date.now()-PALETTE.at>30000){PALETTE.at=Date.now();
  Promise.all([api('projects').catch(()=>null),api('tasks?limit=8').catch(()=>null),api('threads').catch(()=>null),api('agents').catch(()=>null)])
   .then(([p,t,th,a])=>{PALETTE.data={projects:p?.projects||[],tasks:t?.tasks||[],threads:th?.threads||[],agents:a?.agents||[]};if(PALETTE.box)paletteRender()})}
}
function paletteClose(){if(!PALETTE.box)return;PALETTE.box.remove();PALETTE.box=null;try{PALETTE.prev?.focus({preventScroll:true})}catch(e){}}
function paletteRun(it){paletteClose();try{const r=it.run();if(r&&r.catch)r.catch(e=>notice(e.message))}catch(e){notice(e.message)}}
function paletteEntries(q){
 const out=[],d=PALETTE.data||{},add=(group,icon,label,sub,run)=>out.push({group,icon,label,sub,run});
 if(q)add('Chief','orb','Ask Chief: '+q,'Opens the Chief',()=>chiefAsk(q));
 for(const sec of SECTIONS)for(const v of sec.views){if(!tabButton(v)&&v!=='overview')continue;
  add('Go to','arrow',v==='overview'?'Home':(TAB_LABELS[v]||VIEW_TITLES[v]||v),sec.label,()=>openView(v))}
 add('Actions','plus','New task','Home',()=>{openView('overview');launchSheet(true)});
 add('Actions','plus','New project','Work',()=>{openView('projects');setTimeout(()=>{if($('project-form').hidden)$('project-new-toggle').click()},200)});
 add('Actions','plus','New conversation','Chief',()=>{openView('threads');setTimeout(()=>$('thread-new-toggle')?.click(),200)});
 add('Actions','mic','Talk to the Chief','Voice',()=>{carUnlockAudio();openView('chief')});
 add('Actions','alert','Show failed tasks','Work',()=>{openView('tasks');const s=$('task-status-filter');s.value='failed,incomplete';s.dispatchEvent(new Event('change'))});
 add('Actions','sidebar','Toggle the sidebar','View',()=>$('side-collapse').click());
 if(document.body.classList.contains('unhinged'))add('Actions','power','Turn off unhinged mode','Security',()=>$('unhinged-banner-off').click());
 for(const p of (d.projects||[]).slice(0,8))add('Projects','project',p.title,projectStatusLabel(p.status),()=>openItem('project',p.id));
 for(const t of (d.threads||[]).slice(0,8))add('Conversations','chat',t.title||'Conversation',t.agent_name||'',()=>openItem('thread',t.id));
 for(const t of (d.tasks||[]).slice(0,8))add('Recent tasks','task',t.display_title||t.summary||'Run',STATUS_LABELS[t.status]||t.status,()=>openTaskDetail(t.id));
 for(const a of (d.agents||[]).slice(0,10))add('Agents','agent',a.name,a.role||'Agent',()=>openView('agents'));
 for(const n of (HUD.px?.nodes||[]))for(const vm of (n.vms||[]).slice(0,20))add('Servers','server',vm.name||('VM '+vm.vmid),(n.label||n.node)+' · '+(vm.status||''),()=>openView('infrastructure'));
 if(!q)return out.filter(x=>x.group!=='Servers'&&x.group!=='Agents');
 const words=q.toLowerCase().split(/\s+/).filter(Boolean);
 const hit=x=>{const h=(x.label+' '+x.sub+' '+x.group).toLowerCase();return words.every(w=>h.includes(w))};
 return out.filter(x=>x.group==='Chief'||hit(x)).slice(0,40);
}
function paletteRender(keepScroll){
 if(!PALETTE.box)return;const q=PALETTE.box.querySelector('input').value.trim();
 const items=PALETTE.items=paletteEntries(q);const list=PALETTE.box.querySelector('.palette-list');list.replaceChildren();
 if(PALETTE.sel>=items.length)PALETTE.sel=0;let group=null;
 items.forEach((it,i)=>{
  if(it.group!==group){group=it.group;const g=el('li','palette-group',group);g.setAttribute('role','presentation');list.append(g)}
  const li=el('li','palette-item');li.setAttribute('role','option');li.id='pi-'+i;li.setAttribute('aria-selected',String(i===PALETTE.sel));
  const pic=el('span','pi-icon');pic.append(ico(it.icon));li.append(pic,el('span','pi-label',it.label),el('span','pi-sub',it.sub||''));
  li.onmousemove=()=>{if(PALETTE.sel!==i){PALETTE.sel=i;paletteRender(true)}};li.onclick=()=>paletteRun(it);list.append(li);
 });
 if(!items.length)list.append(el('li','notif-empty','No matches.'));
 PALETTE.box.querySelector('input').setAttribute('aria-activedescendant','pi-'+PALETTE.sel);
 const cur=list.querySelector('[aria-selected=true]');if(cur&&keepScroll)cur.scrollIntoView({block:'nearest'});
}
$('palette-open').onclick=()=>paletteOpen();
// Keyboard: Ctrl/Cmd+K palette, "/" focuses Ask Chief on Home, g then h/c/w/i/s goes to a section, ? lists shortcuts.
let gPending=0;
document.addEventListener('keydown',e=>{
 if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'&&!$('workspace').hidden){e.preventDefault();PALETTE.box?paletteClose():paletteOpen();return}
 const typing=e.target.closest&&e.target.closest('input,textarea,select,[contenteditable=true]');
 if(typing||e.ctrlKey||e.metaKey||e.altKey||$('workspace').hidden||PALETTE.box||document.querySelector('dialog[open]'))return;
 if(e.key==='/'&&!$('overview').hidden){e.preventDefault();$('cmd-input').focus();return}
 if(e.key==='?'){e.preventDefault();shortcutsHelp();return}
 if(e.key==='g'){gPending=Date.now();return}
 if(Date.now()-gPending<1200){const map={h:'home',c:'chief',w:'work',i:'infrastructure',s:'settings'};const k=map[e.key];gPending=0;
  if(k){e.preventDefault();document.querySelector('aside .nav-section[data-section="'+k+'"]').click()}}
});
function shortcutsHelp(){uiConfirm('Ctrl/⌘ K — search, go anywhere, ask the Chief\n/ — Ask Chief (on Home)\ng then h, c, w, i or s — Home, Chief, Work, Infrastructure, Settings\nEsc — close a panel or dialog\n? — this list',{title:'Keyboard shortcuts',ok:'Got it',cancel:'Close'}).catch(()=>{})}

// ── Notification center: approvals, failed runs, infrastructure warnings, finished runs ───────────────────────────
(function(){const p=el('div','notif-panel');p.id='notif-panel';p.hidden=true;p.setAttribute('role','dialog');p.setAttribute('aria-label','Notifications');
 const head=el('div','notif-head');head.append(el('h2',null,'Notifications'));
 const push=$('push-toggle');push.className='ghost small';head.append(push);  // push to this device (shown where supported)
 const body=el('div','notif-body');body.id='notif-body';p.append(head,body);$('workspace').append(p)})();
function notifItems(){
 const items=[];
 for(const it of (HUD.inbox||[]))items.push({tone:'warn',title:it.title,sub:it.context||'Needs you',go:()=>{openView('overview');setTimeout(()=>$('inbox-panel').scrollIntoView({behavior:'smooth'}),60)}});
 for(const w of infraFacts().warnings)items.push({tone:'bad',title:w.title,sub:w.sub,go:w.go});
 const seen=Number(store.get('notifSeen')||0),day=Date.now()-86400000;
 for(const t of (HUD.tasks||[])){const at=new Date(t.finished_at||t.created_at).getTime();if(at<day)continue;
  if(['failed','incomplete'].includes(t.status))items.push({tone:'bad',title:'Failed: '+(t.display_title||'run'),sub:(t.agent_name||'')+' · '+when(t.created_at),go:()=>openTaskDetail(t.id),at,fresh:at>seen});
  else if(t.status==='completed')items.push({tone:'ok',title:'Done: '+(t.display_title||'run'),sub:(t.agent_name||'')+' · '+when(t.created_at),go:()=>openTaskDetail(t.id),at,fresh:at>seen,quiet:true})}
 return items;
}
function notifBadge(){const items=notifItems().filter(x=>!x.quiet&&(x.fresh!==false));const n=items.length,c=$('notif-count');c.hidden=!n;c.textContent=n>99?'99+':String(n);
 $('notif-btn').setAttribute('aria-label','Notifications'+(n?', '+n+' new':''))}
function notifRender(){notifBadge();const p=$('notif-panel'),body=$('notif-body');if(!p||p.hidden||!body)return;
 body.replaceChildren();
 const items=notifItems().slice(0,20);
 if(!items.length){body.append(el('p','notif-empty','You are all caught up.'));return}
 for(const it of items){const b=el('button','notif-item');b.type='button';const txt=el('span');txt.append(el('strong',null,it.title),el('small',null,it.sub||''));
  b.append(el('i','dot '+(it.tone||'')),txt);b.onclick=()=>{notifToggle(false);it.go&&it.go()};body.append(b)}
}
function notifToggle(open){const p=$('notif-panel');open=open??p.hidden;p.hidden=!open;$('notif-btn').setAttribute('aria-expanded',String(open));
 if(open){store.set('notifSeen',Date.now());notifRender();(p.querySelector('.notif-item')||p.querySelector('button:not([hidden])'))?.focus()}else notifBadge()}
$('notif-btn').onclick=e=>{e.stopPropagation();notifToggle()};
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!$('notif-panel').hidden){notifToggle(false);$('notif-btn').focus()}
 if(e.key==='Escape'&&!$('profile-menu').hidden){menuToggle($('profile-btn'),$('profile-menu'),false);$('profile-btn').focus()}});
// Mark the section of the page that is open now (not stored: the remembered page is restored after sign-in).
markSection(document.body.dataset.view||'overview');

// ── HLA_UI_V5: review fixes (one icon set, one status source, one primary per view, calmer details) ────────────
// Icons: the static buttons get their SVG once (dynamic ones use ico()/withIcon() where they are built).
(function(){
 const set=(id,name,label)=>{const b=$(id);if(b)withIcon(b,name,label)};
 set('push-toggle','bell','Notify');set('inbox-refresh','refresh','Check now');set('task-attach','clip','Attach files');
 set('project-attach','clip','Reference files…');set('thread-list-toggle','sidebar','List');set('thread-files-toggle','clip','Files');
 set('thread-menu-toggle','more');set('thread-attach','clip');set('thread-stop','stop','Stop');set('detail-stop','stop','Stop');
 for(const id of ['launch-close','close-detail','audit-detail-close']){const b=$(id);if(b){b.className='icon-btn';withIcon(b,'x')}}
 set('car-handsfree','repeat','Hands-free');set('car-briefing','list','Briefing');set('car-stop','stop','Stop');
 const needs=$('car-needs');if(needs)needs.replaceChildren(ico('flag'),document.createTextNode(' Open what needs you'));
 for(const id of ['project-new-toggle','thread-new-toggle'])set(id,'plus','New');
 set('ov-new-task','plus','New task');set('mcp-add','plus','Add server');
 // Conversations: the page title and tab already say it; the list keeps its name for screen readers only.
 document.querySelector('.chat-sidebar-head h2')?.classList.add('sr-only');
})();

// One status for the whole shell. The sidebar, Home's status line and the bell all read platformStatus(): unhinged
// mode first (it changes what agents may do), then reachability, then infrastructure warnings.
function unhingedState(){return policyData?.policy?.unhinged||{active:false}}
function unhingedUntil(u){if(!u.until)return 'until you turn it off';const d=new Date(u.until*1000),now=new Date();
 const t=d.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});return 'until '+(d.toDateString()===now.toDateString()?t:d.toLocaleDateString([],{day:'numeric',month:'short'})+' '+t)}
function platformStatus(){
 const off=$('sys-status').dataset.state, f=infraFacts();
 if(off==='bad')return {state:'bad',text:'Offline'};
 if(off==='warn')return {state:'warn',text:'Platform partly unreachable'};
 if(f.state!=='ok'&&f.warnings.length)return {state:f.state,text:f.warnings[0].title};
 return {state:off==='wait'?'wait':'ok',text:off==='wait'?'Connecting…':'All systems operational'};
}
function shellStatus(){
 const u=unhingedState(),s=platformStatus(),side=$('side-status');
 if(u.active){side.dataset.state='warn';side.classList.add('unhinged-on');$('side-status-text').textContent='Unhinged on · '+unhingedUntil(u);
  side.title='Agents at level 5 run any command without approval, '+unhingedUntil(u)+'. Open Security to turn it off.'}
 else{side.dataset.state=s.state;side.classList.remove('unhinged-on');$('side-status-text').textContent=s.text;side.title='Platform status'}
}
const v5HudSystem=hudSystem;
hudSystem=function(state,text){v5HudSystem(state,text);shellStatus()};
const v5HomeStatus=homeStatus;
homeStatus=function(){
 v5HomeStatus();const s=platformStatus(),parts=[];
 if(HUD.running)parts.push(HUD.running+' run'+(HUD.running===1?'':'s')+' in progress');
 if(HUD.needs)parts.push(HUD.needs+' waiting for you');
 $('home-status-text').textContent=[s.state==='wait'?'Connecting…':s.text,...parts].join(' · ');
 $('home-status').dataset.state=s.state;$('home-status-dot').className='dot '+(s.state==='wait'?'':s.state);
 shellStatus();
};
const v5RenderUnhinged=renderUnhinged;
renderUnhinged=function(){
 v5RenderUnhinged();const u=unhingedState();
 if(u.active){$('unhinged-banner-text').textContent=unhingedUntil(u);
  $('unhinged-banner').title='Unhinged mode: agents at level 5 run any command without approval, '+unhingedUntil(u)+'. Everything is still logged.'}
 shellStatus();
};
$('side-status').onclick=()=>openView(unhingedState().active?'policy':'infrastructure');

// Notification center: grouped (Needs you · Alerts · Runs), with times, read state and Clear. The read state lives in
// this browser only (localStorage, guarded): it is a convenience, the inbox on Home stays the source of truth.
const NOTIF={read:null,cleared:null};
function notifLoad(){if(NOTIF.read)return;
 try{NOTIF.read=new Set(JSON.parse(store.get('notifRead')||'[]'))}catch(e){NOTIF.read=new Set()}
 try{NOTIF.cleared=new Set(JSON.parse(store.get('notifCleared')||'[]'))}catch(e){NOTIF.cleared=new Set()}}
function notifSave(){const cap=s=>JSON.stringify([...s].slice(-400));store.set('notifRead',cap(NOTIF.read));store.set('notifCleared',cap(NOTIF.cleared))}
notifItems=function(){
 notifLoad();const items=[],now=Date.now(),day=now-86400000;
 for(const it of (HUD.inbox||[])){const key='in:'+it.kind+':'+(it.task_id||it.project_task_id||it.project_id||it.thread_id||it.website_id||it.title);
  items.push({key,group:'Needs you',tone:'warn',title:it.title,sub:it.context||'',at:it.since?new Date(it.since).getTime():null,
   go:()=>{openView('overview');setTimeout(()=>$('inbox-panel').scrollIntoView({behavior:'smooth',block:'start'}),60)}})}
 for(const w of infraFacts().warnings)items.push({key:'al:'+w.title,group:'Alerts',tone:'bad',title:w.title,sub:w.sub,at:HUD.lastLoad||null,go:w.go});
 for(const t of (HUD.tasks||[])){const at=new Date(t.finished_at||t.created_at).getTime();if(at<day)continue;
  if(['failed','incomplete'].includes(t.status))items.push({key:'t:'+t.id,group:'Runs',tone:'bad',title:'Failed: '+(t.display_title||'run'),sub:t.agent_name||'',at,go:()=>openTaskDetail(t.id)});
  else if(t.status==='completed')items.push({key:'t:'+t.id,group:'Runs',tone:'ok',title:'Done: '+(t.display_title||'run'),sub:t.agent_name||'',at,go:()=>openTaskDetail(t.id),quiet:true})}
 return items.filter(x=>!NOTIF.cleared.has(x.key)).map(x=>({...x,unread:!NOTIF.read.has(x.key)}));
};
notifBadge=function(){
 const items=notifItems().filter(x=>x.unread&&!x.quiet),n=items.length,c=$('notif-count');c.hidden=!n;c.textContent=n>99?'99+':String(n);
 const needs=items.filter(x=>x.group==='Needs you').length,alerts=items.filter(x=>x.group==='Alerts').length,fails=n-needs-alerts;
 const parts=[needs&&needs+' need you',alerts&&alerts+' alert'+(alerts>1?'s':''),fails&&fails+' failed run'+(fails>1?'s':'')].filter(Boolean);
 $('notif-btn').setAttribute('aria-label','Notifications'+(n?', '+n+' unread: '+parts.join(', '):''));$('notif-btn').title=n?parts.join(' · '):'Notifications';
};
notifRender=function(){
 notifBadge();const p=$('notif-panel'),body=$('notif-body');if(!p||p.hidden||!body)return;body.replaceChildren();
 const items=notifItems();
 if(!items.length){body.append(el('p','notif-empty','You are all caught up.'));return}
 for(const g of ['Needs you','Alerts','Runs']){const rows=items.filter(x=>x.group===g).sort((a,b)=>(b.at||0)-(a.at||0)).slice(0,g==='Runs'?8:12);if(!rows.length)continue;
  const h=el('h3','notif-group');h.append(document.createTextNode(g),el('span','tab-n',String(rows.length)));body.append(h);
  for(const it of rows){const b=el('button','notif-item'+(it.unread?' unread':''));b.type='button';const txt=el('span');
   txt.append(el('strong',null,it.title),el('small',null,[it.sub,it.at?when(new Date(it.at).toISOString()):''].filter(Boolean).join(' · ')));
   b.append(el('i','dot '+(it.tone||'')),txt);b.onclick=()=>{NOTIF.read.add(it.key);notifSave();notifToggle(false);it.go&&it.go()};body.append(b)}}
};
(function(){
 const head=document.querySelector('#notif-panel .notif-head'),acts=el('div','notif-actions');
 const read=el('button','ghost small','Mark all read');read.type='button';read.id='notif-read';
 read.onclick=()=>{for(const x of notifItems())NOTIF.read.add(x.key);notifSave();notifRender()};
 const clear=el('button','ghost small','Clear');clear.type='button';clear.id='notif-clear';clear.title='Hide these until something new happens (Needs you stays on Home)';
 clear.onclick=()=>{for(const x of notifItems()){NOTIF.read.add(x.key);NOTIF.cleared.add(x.key)}notifSave();notifRender();$('notif-btn').focus()};
 acts.append(read,clear,$('push-toggle'));head.append(acts);
})();

// ── Work › Queue: compact rows (title, status, time) → details in a drawer; the form behind "Queue task". ──────────
function firstLine(text,n){const t=String(text||'').split('\n').map(x=>x.replace(/^[#>*\-\s]+/,'').trim()).find(Boolean)||'Queued task';
 return t.length>(n||90)?t.slice(0,(n||90)-1).trimEnd()+'…':t}
(function(){
 const sec=$('queue'),form=$('queue-form'),formPanel=form.closest('.panel'),jobsPanel=$('queue-jobs').closest('.panel');
 const intro=el('div','page-intro');const txt=el('p','muted','Jobs the worker runs one at a time: text only, no server commands.');
 const add=el('button',null);add.type='button';add.id='queue-new';withIcon(add,'plus','Queue task');
 const refresh=el('button','ghost small');refresh.type='button';withIcon(refresh,'refresh','Refresh');refresh.onclick=()=>loadQueue().catch(e=>notice(e.message));
 const side=el('div','intro-actions');side.append(refresh,add);intro.append(txt,side);sec.prepend(intro);
 jobsPanel.removeAttribute('style');jobsPanel.classList.add('queue-panel');const h=jobsPanel.querySelector('h2');if(h)h.remove();
 $('queue-refresh').hidden=true;formPanel.hidden=true;
 const d=drawer('queue-drawer');
 add.onclick=()=>{d.title.textContent='Queue a task';d.meta.textContent='The worker processes jobs one at a time.';d.body.replaceChildren(form);d.actions.replaceChildren();d.open();
  setTimeout(()=>$('queue-text').focus(),30)};
 form.addEventListener('submit',()=>setTimeout(()=>{if(!$('queue-text').value&&d.isOpen)d.close()},400));
 d.onclose=()=>{if(form.parentNode!==formPanel)formPanel.append(form)};
 window.queueShow=job=>{d.title.textContent=firstLine(job.task_text,80);d.meta.replaceChildren(queueStatusPill(job.status),
   el('span',null,' queued '+when(job.created_at)+(job.finished_at?' · finished '+when(job.finished_at):'')));
  const acts=[];if(job.api_task_id){const o=el('button','secondary small','Open task');o.type='button';o.onclick=()=>{d.close();openTaskDetail(job.api_task_id)};acts.push(o)}
  d.actions.replaceChildren(...acts);
  const req=el('details','drawer-section');req.append(el('summary',null,'Request'),el('pre',null,job.task_text||''));
  const res=el('div','drawer-section');res.append(el('h3',null,job.error_message&&!job.result_text?'Error':'Result'));
  const body=el('div','rendered-result md-view');window.renderTaskMarkdown(body,job.result_text||job.error_message||'No result yet.');res.append(body);
  d.body.replaceChildren(req,res);d.open()};
})();
loadQueue=async function(){
 const d=await api('queue?limit=75'),list=$('queue-jobs');list.replaceChildren();list.className='row-list queue-list';
 if(!d.jobs.length){list.append(el('div','empty-state',null));list.lastChild.append(el('strong',null,'Nothing queued'),el('span',null,'Queue a text task and the worker picks it up.'));return}
 for(const job of d.jobs){
  const row=el('div','row-item queue-row');row.tabIndex=0;row.setAttribute('role','button');
  const head=el('div','queue-head');head.append(queueStatusPill(job.status),el('strong','row-title',firstLine(job.task_text)),
   el('span','row-side',job.finished_at?(job.status==='cancelled'?'cancelled ':'finished ')+when(job.finished_at):job.started_at?'started '+when(job.started_at):'queued '+when(job.created_at)));
  if(job.api_task_id){const open=el('button','link-button','Open task');open.type='button';open.onclick=e=>{e.stopPropagation();openTaskDetail(job.api_task_id)};head.append(open)}
  if(job.status==='queued'){const cancel=el('button','ghost small','Cancel');cancel.type='button';
   cancel.onclick=async e=>{e.stopPropagation();try{await api('queue/'+job.id+'/cancel',{method:'POST',headers:{'x-agent-ui':'1'}});notice('Queued task cancelled.');await loadQueue()}catch(err){notice(err.message)}};head.append(cancel)}
  row.append(head);
  if(job.error_message&&!job.result_text)row.append(el('p','row-sub error',firstLine(job.error_message,140)));
  const show=()=>queueShow(job);row.onclick=show;row.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();show()}};
  list.append(row);
 }
};

// ── Websites: one environment filter (the segmented control). No environment chips, no Environment column (the
// groups already say it); a guessed environment is a quiet note on the row. ──────────────────────────────────────
const v5RenderWebsites=renderWebsites;
renderWebsites=function(){
 v5RenderWebsites();
 $('ws-summary')?.querySelector('.ws-envs')?.remove();
 const t=document.querySelector('#ws-list .ws-table');if(!t)return;
 for(const th of t.querySelectorAll('thead th'))if(th.textContent==='Environment')th.remove();
 for(const g of t.querySelectorAll('tr.ws-group th'))g.colSpan=4;
 for(const td of t.querySelectorAll('td.ws-envcell')){
  const pill=td.querySelector('.ws-env'),guess=pill&&pill.querySelector('.ws-guess');
  if(guess){const b=el('span','badge ws-env ws-env-guessed','environment guessed');b.title=pill.title;td.closest('tr').querySelector('.ws-title').append(b)}
  td.remove();
 }
};

// ── Work › Tasks: the status chips are the only status filter (the select stays as their hidden state). ────────────
(function(){
 const sel=$('task-status-filter'),bar=document.querySelector('#tasks .work-filters');if(!sel||!bar)return;
 const v='cancelled,rejected,expired';if(![...sel.options].some(o=>o.value===v))sel.add(new Option('Stopped or rejected',v));
 const b=el('button',null,'Stopped');b.type='button';b.dataset.status=v;b.setAttribute('aria-pressed','false');
 b.onclick=()=>{sel.value=v;sel.dispatchEvent(new Event('change'))};bar.append(b);
 sel.hidden=true;sel.tabIndex=-1;sel.setAttribute('aria-hidden','true');
 const ws=$('task-workspace-filter');if(ws)ws.options[0].text='All workspaces';
})();
const v5LoadTasks=loadTasks;
loadTasks=async function(...a){const v=$('task-status-filter').value;
 for(const b of document.querySelectorAll('#tasks .work-filters button'))b.setAttribute('aria-pressed',String(b.dataset.status===v));
 const r=await v5LoadTasks(...a);groupTaskRows();return r};

// Repeated runs: consecutive runs with the same title, status and agent show once with "×N" (expandable).
const runKey=t=>[t.display_title||t.summary||'',t.status,t.agent_name||''].join('\u0001');
function groupRuns(rows){const out=[];for(const t of rows){const last=out[out.length-1];if(last&&runKey(last.t)===runKey(t))last.n++;else out.push({t,n:1})}return out}
function groupTaskRows(){
 const rows=[...document.querySelectorAll('#task-rows tr[data-id]')];let lead=null,key=null;
 for(const tr of rows){
  const k=[tr.querySelector('.req-text')?.textContent,tr.querySelector('.status-cell')?.textContent,tr.querySelector('.who div')?.textContent].join('\u0001');
  if(lead&&k===key){tr.hidden=true;tr.classList.add('grp-more');lead._more.push(tr);continue}
  lead=tr;key=k;tr._more=[];
 }
 for(const tr of rows){if(!tr._more||!tr._more.length)continue;
  const b=el('button','grp-count','×'+(tr._more.length+1));b.type='button';b.title='Show all '+(tr._more.length+1)+' runs';b.setAttribute('aria-expanded','false');
  b.onclick=e=>{e.stopPropagation();const open=b.getAttribute('aria-expanded')!=='true';b.setAttribute('aria-expanded',String(open));for(const x of tr._more)x.hidden=!open};
  const rt=tr.querySelector('.req-text');rt.classList.add('has-count');rt.replaceChildren(el('span','req-label',rt.textContent),b)}
}
renderFeed=function(rows){
 const list=$('feed-list');list.replaceChildren();
 if(!rows.length){list.append(el('li','muted','No activity yet.'));return}
 for(const {t,n} of groupRuns(rows).slice(0,8)){
  const li=el('li','feed-item'),b=el('button','feed-row');b.type='button';const st=t.status||'unknown';
  const text=el('span','feed-text',t.display_title||t.summary||'Run');if(n>1)text.append(el('span','grp-count',' ×'+n));
  const who=[t.display_context,t.agent_name].filter(Boolean).join(' · '),meta=el('span','feed-meta');
  if(who)meta.append(el('span','feed-who',who+' · '));meta.append(document.createTextNode(when(t.created_at)));
  b.append(el('i','feed-dot st-dot-'+st),text,meta);
  b.title=(STATUS_LABELS[st]||st)+' — '+(t.display_title||'')+(n>1?' ('+n+' runs)':'');b.setAttribute('aria-label',(t.display_title||'Run')+(n>1?', '+n+' runs':'')+', '+(STATUS_LABELS[st]||st));
  b.onclick=()=>openTaskDetail(t.id);li.append(b);list.append(li);
 }
};

// ── Chief: a centred orb with its status (no second "Chief" label), tools in the corner, Stop only while he
// listens or speaks (or hands-free is on), voice settings in the shared drawer, and Car mode that hides the shell. ──
(function(){
 const sec=$('chief'),stage=sec.querySelector('.chief-stage');if(!stage)return;
 stage.querySelector('.chief-stage-text>strong')?.remove();
 stage.classList.add('chief-hero');
 const tools=stage.querySelector('.chief-stage-actions');tools.classList.add('chief-tools');
 const focus=$('car-focus');withIcon(focus,'car','Car mode');
 const exitFocus=el('button','secondary car-focus-exit');exitFocus.type='button';exitFocus.id='car-focus-exit';withIcon(exitFocus,'x','Exit car mode');
 exitFocus.onclick=()=>focus.click();sec.prepend(exitFocus);
 focus.addEventListener('click',()=>{setTimeout(()=>{sizeChief();$('car-transcript').scrollTop=1e9},30)});
 const settings=$('car-settings');settings.classList.remove('car-settings');settings.classList.add('voice-settings');settings.querySelector('h2')?.remove();
 $('chief-form').querySelector('.ask-send')?.classList.add('chief-send');
})();
carSettings=function(open){
 const d=drawer('voice-drawer'),s=$('car-settings');
 if(open){d.title.textContent='Voice settings';d.meta.textContent='Model, language and voices for the Chief.';if(s.parentNode!==d.body)d.body.replaceChildren(s);
  s.hidden=false;d.onclose=()=>{s.hidden=true;$('car-settings-toggle').setAttribute('aria-expanded','false')};d.open();
  $('car-settings-toggle').setAttribute('aria-expanded','true');carFillVoices();carEngineNote()}
 else d.close();
};
const v5CarStatus=carStatus;
carStatus=function(state,text){v5CarStatus(state,text);chiefStopShown()};
function chiefStopShown(){const s=$('car-stop');if(s)s.hidden=!(['listening','speaking'].includes(CAR.state)||CAR.handsFree)}
const v5SetHandsFree=carSetHandsFree;
carSetHandsFree=function(on){const r=v5SetHandsFree(on);chiefStopShown();return r};
chiefStopShown();

// ── Settings › Tools & MCP: one row per server (name, state, warnings, level); the server opens in a drawer with
// its level, connection, actions and tools. The add/edit form stays on the page. ─────────────────────────────────
let mcpOpenName=null;
renderMcp=function(){
 const d=mcpData,box=$('mcp-servers'),notes=[];box.replaceChildren();box.className='row-list mcp-rows';
 if(!d.available)notes.push(d.note);else if(!d.editable)notes.push(d.approver?.reason||'Read-only.');
 if(d.available&&d.credentials_configured===false)notes.push('The gateway credential store is off (HLA_SECRETS_KEY or GATEWAY_CREDENTIAL_KEY): only servers without auth work.');
 $('mcp-note').hidden=!notes.length;$('mcp-note').textContent=notes.join(' ');$('mcp-add').disabled=!mcpCanEdit();
 if(d.available&&!d.servers.length){const e=el('div','empty-state');e.append(el('strong',null,'No MCP servers yet'),el('span',null,'Add one with its URL; agents get its tools once you raise its level.'));box.append(e)}
 for(const s of d.servers){
  const row=el('div','row-item mcp-row');row.dataset.server=s.name;row.tabIndex=0;row.setAttribute('role','button');row.setAttribute('aria-label',s.name+', level '+s.level);
  const title=el('div','row-title');title.append(el('strong',null,s.name),mcpStatus(s));
  if(s.allow_private)title.append(el('span','badge','LAN allowed'));
  if(s.allow_insecure)title.append(el('span','badge badge-warn','plain http'));
  if(s.credential_stored===false||s.url_credential_stored===false)title.append(el('span','badge badge-warn','credential missing'));
  if(s.review_count)title.append(el('span','badge badge-warn',s.review_count+' new to review'));
  const sub=el('div','row-sub',s.description||(s.url_credential?'URL from credential '+s.url_credential:s.url));
  const side=el('div','row-side');side.append(el('span',null,'Level '+s.level),ico('arrow'));
  row.append(title,sub,side);
  const open=()=>mcpShow(s.name);row.onclick=open;row.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();open()}};
  box.append(row);
 }
 const dr=V4_DRAWERS['mcp-drawer'];
 if(dr&&dr.isOpen&&mcpOpenName){if(d.servers.some(x=>x.name===mcpOpenName))mcpShow(mcpOpenName,true);else dr.close()}
};
function mcpShow(name,refresh){
 const s=(mcpData?.servers||[]).find(x=>x.name===name);if(!s)return;mcpOpenName=name;
 const d=drawer('mcp-drawer',true);d.onclose=()=>{mcpOpenName=null};
 d.title.textContent=s.name;d.meta.replaceChildren(mcpStatus(s),el('span',null,' '+s.service));d.actions.replaceChildren();
 const keep=refresh?d.body.scrollTop:0;const card=mcpServerCard(s);card.classList.add('in-drawer');
 // Edit opens the form on the page: close the drawer first (the page behind it is inert).
 for(const b of card.querySelectorAll('.mcp-actions button'))if(b.textContent.trim()==='Edit')b.addEventListener('click',()=>{d.close();setTimeout(()=>$('mcp-form-panel').scrollIntoView({block:'start'}),30)},true);
 d.body.replaceChildren(card);d.open();if(refresh)d.body.scrollTop=keep;
}

// ── GitHub repositories live in one place: Connections › GitHub (with the token). Security keeps one line linking there.
const v5RenderGithubRepos=renderGithubRepos;
renderGithubRepos=function(){
 v5RenderGithubRepos();const panel=$('github-panel'),home=$('gh-panel');if(!panel||!home)return;
 panel.classList.remove('panel');panel.classList.add('gh-allowed');
 const h=panel.querySelector('.panel-heading h2');if(h){const h3=el('h3',null,'Allowed repositories');h.replaceWith(h3)}
 panel.querySelector('#github-to-connections')?.remove();
 if(panel.parentNode!==home)home.append(panel);
 let line=$('github-policy-line');
 if(!line){line=el('p','fineprint policy-gh-line');line.id='github-policy-line';const a=el('a',null,'Connections › GitHub');a.href='#';
  a.onclick=e=>{e.preventDefault();openView('connections')};line.append('GitHub repositories agents may reach, and merges per repository, are set on ',a,'.');
  $('deploy-apps').closest('.panel').before(line)}
};

// ── Long explanations on Settings and Infrastructure pages show one line with "More" (the full text on request). ──
function clampIntros(view){
 const sec=$(view);if(!sec||!['settings','infrastructure','work'].includes(sectionOf(view)))return;
 for(const p of sec.querySelectorAll('.panel-heading .muted, .page-intro > p, .pane-intro')){
  if(p.dataset.clamp||p.textContent.length<150||p.closest('.drawer'))continue;p.dataset.clamp='1';p.classList.add('clamp');
  const more=el('button','link-button clamp-more','More');more.type='button';more.setAttribute('aria-expanded','false');
  more.onclick=()=>{const open=p.classList.toggle('clamp-open');more.textContent=open?'Less':'More';more.setAttribute('aria-expanded',String(open))};
  p.after(more);
 }
}
const v5SwitchClamp=switchView;
switchView=function(view){v5SwitchClamp(view);setTimeout(()=>{clampIntros(view);subnavReveal()},0)};
clampIntros(document.body.dataset.view||'overview');

// ── Phones: a short "Infra" label in the tab bar; page tabs that do not fit fade at the edge and the open one scrolls
// into view; Security's Save sits in a bar that stays above the tab bar. ─────────────────────────────────────────
(function(){
 const infra=document.querySelector('aside .nav-section[data-section="infrastructure"]');
 if(infra){infra.dataset.short='Infra';infra.querySelector('span')?.classList.add('label-long')}
 const sub=$('subnav');
 const fade=()=>{const max=sub.scrollWidth-sub.clientWidth;sub.classList.toggle('fade-r',max>2&&sub.scrollLeft<max-2);sub.classList.toggle('fade-l',sub.scrollLeft>2)};
 sub.addEventListener('scroll',fade,{passive:true});window.addEventListener('resize',fade);
 window.subnavReveal=()=>{const t=sub.querySelector('.subnav-group:not([hidden]) [aria-selected="true"]');
  if(t&&sub.scrollWidth>sub.clientWidth){const r=t.getBoundingClientRect(),s=sub.getBoundingClientRect();
   if(r.left<s.left+8||r.right>s.right-8)sub.scrollLeft+=r.left-s.left-(s.width-r.width)/2}fade()};
 const save=$('policy-save')?.closest('.launch-actions');if(save)save.classList.add('save-bar');
})();
subnavReveal();

// ── Chief transcript: replies render as Markdown; a folded older reply shows a clean plain preview (no table pipes,
// code or headings), "Show more" opens the full rendered reply. ──────────────────────────────────────────────────
function carPreview(t){
 return String(t||'').replace(/```[\s\S]*?(```|$)/g,' ').split('\n').filter(l=>!/\|/.test(l)&&!/^\s*[-:|]+\s*$/.test(l))
  .map(l=>l.replace(/^\s{0,3}(#{1,6}|>|[-*+•]|\d+[.)])\s+/,'').replace(/`([^`]*)`/g,'$1').replace(/\[([^\]]+)\]\([^)]*\)/g,'$1')
   .replace(/(\*\*|__|\*|_|~~)(?=\S)([^*_~]+?)\1/g,'$2').trim()).filter(Boolean).join(' · ');
}
carRender=function(){
 const box=$('car-transcript');box.replaceChildren();
 if(!CAR.exchanges.length)box.append(el('p','car-empty','Ask the Chief anything: “How are the projects doing?”, “What needs me?”, “Plan a project to …”.'));
 if(CAR.exchanges.length&&CAR.chief){const h=el('button','link-button car-history','Earlier messages are in Conversations');h.type='button';
  h.onclick=()=>openItem('thread',CAR.chief.thread_id).catch(e=>notice(e.message));box.append(h)}
 const last=CAR.exchanges.length-1;
 CAR.exchanges.forEach((x,i)=>{
  const p=el('div','car-line car-'+x.role);const text=el('div','car-text');p.append(el('span','car-who',x.role==='you'?'You':'Chief'),text);
  const fold=x.role==='chief'&&!x.open&&(x.text||'').length>(i<last?320:900)&&(i<last||CAR.replyDone);
  if(x.role==='chief'&&x.text&&!fold){text.classList.add('md-view');window.renderTaskMarkdown(text,x.text)}
  else text.textContent=fold?carPreview(x.text):(x.text||'…');
  if(fold){p.classList.add('car-collapsed');const more=el('button','link-button car-more','Show more');more.type='button';more.onclick=()=>{x.open=true;carRender()};p.append(more)}
  box.append(p)});
 box.scrollTop=box.scrollHeight;
};
// ── Project header: one fact line (spent · runs · at a time, Limits…, a labelled "Code changes" choice); the working
// folder on its own quiet line. ──────────────────────────────────────────────────────────────────────────────────
const v5OpenProject=openProject;
openProject=async function(id){
 await v5OpenProject(id);const main=$('project-main'),f=main.querySelector('.project-facts');if(!f||f.dataset.v5)return;f.dataset.v5='1';
 for(const n of [...f.childNodes])if(n.nodeType===1&&n.tagName==='SPAN'&&n.classList.contains('muted')&&/· $/.test(n.textContent))n.textContent=n.textContent.replace(/ · $/,'');
 const code=f.querySelector(':scope>code');
 if(code){const line=el('div','project-path');line.append(el('span','muted','Folder '),code);code.title=code.textContent;f.after(line)}
 const mp=f.querySelector('.merge-policy');
 if(mp){const sep=mp.querySelector(':scope>span.muted');if(sep&&sep.textContent.trim()==='·')sep.replaceWith(el('span','muted mp-label','Code changes'));
  else if(sep)sep.textContent=sep.textContent.replace(/^ · /,'Code changes: ')}
 const facts=el('span','project-facts-text');while(f.firstChild&&!(f.firstChild.nodeType===1&&f.firstChild.tagName==='BUTTON')&&!(f.firstChild.classList&&f.firstChild.classList.contains('merge-policy')))facts.append(f.firstChild);
 f.prepend(facts);
};
// ── Smaller things from the review ─────────────────────────────────────────────────────────────────────────────
// Websites: the Sites summary and Refresh belong to Sites, not to Domains & routes; a site that is down or degraded
// offers "Ask Chief to investigate".
(function(){
 const sec=$('websites');if(!sec)return;
 const mark=()=>{const on=sec.querySelector('#ws-tabs [aria-selected="true"]')?.dataset.tab==='routes';sec.classList.toggle('on-routes',on)};
 sec.querySelector('#ws-tabs')?.addEventListener('click',()=>setTimeout(mark,0));
 const show=wsShowTab;wsShowTab=k=>{const r=show(k);mark();return r};mark();
})();
const v5WsOpenDrawer=wsOpenDrawer;
wsOpenDrawer=function(s){
 v5WsOpenDrawer(s);const d=V4_DRAWERS['ws-drawer'];if(!d||!['down','degraded'].includes(s.status))return;
 if(d.actions.querySelector('.ws-ask'))return;
 const b=el('button','secondary small ws-ask');b.type='button';withIcon(b,'orb','Ask Chief to investigate');
 b.onclick=()=>{d.close();chiefAsk('The website '+s.name+' is '+s.status+(s.status_reason?' ('+s.status_reason+')':'')+'. Find out why and tell me what to do.')};
 d.actions.prepend(b);
};
// Home: when nothing runs, "Running" says what ran last.
const v5RenderFeedIdle=renderFeed;
renderFeed=function(rows){v5RenderFeedIdle(rows);
 if(!HUD.running&&rows&&rows.length){const t=rows.find(r=>r.status!=='running');if(t)$('k-running-foot').textContent='Idle · last: '+(t.display_title||'run').slice(0,48)+' · '+when(t.finished_at||t.created_at)}};
// Agents: "1 agent", not "1 agents".
// Write only when the text changes: Firefox fires the observer again even for an identical write, which looped forever.
const v5LoadAgentsSummary=()=>{const s=$('agent-summary');if(!s)return;const t=s.textContent,n=t.replace(/^1 agents configured/,'1 agent configured');if(n!==t)s.textContent=n};
new MutationObserver(v5LoadAgentsSummary).observe($('agent-summary')||document.createElement('i'),{childList:true,characterData:true,subtree:true});
// Knowledge: the search is live, so no Search button; the workspace choice says what it is.
(function(){const b=$('kb-refresh');if(b)b.hidden=true;const w=$('kb-workspace');if(w)for(const o of w.options)o.text=o.value==='work'?'Work workspace':'Home workspace'})();
// Task drawer: Timings shows the total; the breakdown is one click away.
const v5OpenTaskDetail=openTaskDetail;
openTaskDetail=async function(id){
 const r=await v5OpenTaskDetail(id);
 for(const dt of document.querySelectorAll('#detail-facts dt'))if(dt.textContent==='Timings'){const dd=dt.nextElementSibling;if(!dd||dd.dataset.v5)continue;dd.dataset.v5='1';
  const full=dd.textContent,m=/total ([\d.]+ s)/.exec(full),first=/first words ([\d.]+ s)/.exec(full);
  if(m){dd.replaceChildren(el('span',null,(first?'first words '+first[1]+' · ':'')+'total '+m[1]+' '));const more=el('button','link-button','Details');more.type='button';
   more.onclick=()=>{dd.textContent=full};dd.append(more)}}
 return r};
// Toasts: an old message does not follow you to the next page, and a toast never sits on an open sheet.
let noticeAt=0;const v5Notice=notice;notice=function(text){if(text)noticeAt=Date.now();return v5Notice(text)};
const v5SwitchToast=switchView;switchView=function(view){v5SwitchToast(view);if($('notice').textContent&&Date.now()-noticeAt>2500)v5Notice('')};
// ── Keyboard: "Skip to content" is the first Tab stop (past the sidebar and header); the palette gives focus back. ──
(function(){
 const a=el('a','skip-link','Skip to content');a.href='#main';a.id='skip-link';document.body.prepend(a);
 const sync=()=>{a.hidden=$('workspace').hidden};sync();
 new MutationObserver(sync).observe($('workspace'),{attributes:true,attributeFilter:['hidden']});
 a.onclick=e=>{e.preventDefault();const sec=document.querySelector('main>section:not([hidden])');
  const t=sec&&sec.querySelector('input:not([type=hidden]):not([disabled]),textarea,button:not([disabled]),select,a[href],[tabindex="0"]');
  const target=document.body.dataset.view==='chief'?$('chief-input'):t||$('page-title');
  if(target===$('page-title'))$('page-title').tabIndex=-1;target.focus()};
})();
//@V5-END

// ── HLA_SUBSCRIPTIONS_V1: Claude (Pro/Max) and ChatGPT (Plus/Pro) subscriptions as model engines ───────────────────
// The router runs the vendors' own CLIs (Claude Code, Codex) signed in to the owner's plan (/ui/api/subscriptions).
// Settings › Models has a "Subscriptions" panel (one row per engine, sign-in in a drawer). Their models (ids
// claude-sub/<m>, chatgpt-sub/<m>, or billing:"subscription") sit in a "Subscriptions" group in every model picker,
// disabled with "(sign in first)" while the engine is signed out, and runs on them read "Subscription", not $0.00.
ICONS.ext='<path d="M14 4.5h5.5V10"/><path d="m19.5 4.5-8 8"/><path d="M18 14v4.5a1 1 0 0 1-1 1H5.5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1H10"/>';
ICONS.copy='<rect x="8.5" y="8.5" width="11" height="11" rx="2"/><path d="M15.5 8.5V6a1.5 1.5 0 0 0-1.5-1.5H6A1.5 1.5 0 0 0 4.5 6v8A1.5 1.5 0 0 0 6 15.5h2.5"/>';
function isSubModel(id,billing){return billing==='subscription'||/-sub\//.test(String(id||''))}
function subCostText(model,usd,billing){return isSubModel(model,billing)?'Subscription':cost(usd)}
const SUBS={data:null,loading:null,tests:{},pickers:new Set(),openId:null,timer:null,seq:0,lastKey:''};
const SUBS_HINT={claude:'Claude Pro or Max · runs Claude Code',chatgpt:'ChatGPT Plus or Pro · runs Codex'};
const subsName=e=>String(e.label||e.id).replace(/\s*\(subscription\)\s*$/i,'');
// Account · plan as the CLI reports them; a lowercase plan name ("max") reads as a name ("Max").
const subsWho=e=>[e.account,e.plan?String(e.plan).charAt(0).toUpperCase()+String(e.plan).slice(1):''].filter(Boolean).join(' · ');
const subsEngines=()=>(SUBS.data&&SUBS.data.engines)||[];
function subsEngineOf(id){const m=/^([a-z0-9_.-]+?)-sub\//i.exec(String(id||''));return m?subsEngines().find(e=>e.id===m[1])||null:null}
const subsBusy=e=>!!e&&e.installed&&!e.signed_in&&['starting','waiting'].includes(e.login&&e.login.state);
function loadSubs(){
 if(SUBS.loading)return SUBS.loading;
 SUBS.loading=api('subscriptions').then(d=>{SUBS.data={engines:Array.isArray(d.engines)?d.engines:[]}},e=>{SUBS.data={engines:[],error:e.message}})
  .finally(()=>{SUBS.loading=null;subsRender();subsDecorateAll()});
 return SUBS.loading}
// Writes answer with the login object (or the engine): fold it into the cached engine.
function subsMerge(id,patch,isEngine){const e=subsEngines().find(x=>x.id===id);if(!e||!patch)return e;
 if(isEngine)Object.assign(e,patch);else e.login=Object.assign({},e.login||{},patch);return e}
function subsPost(id,path,body){return api('subscriptions/'+encodeURIComponent(id)+'/'+path,{method:'POST',headers:UI_HEADERS,body:JSON.stringify(body||{})})}

// The panel (first on Settings › Models: subscriptions are a way to run models, next to the registry they feed).
function subsPanel(){
 let p=$('subs-panel');if(p)return p;
 p=el('div','panel subs-panel');p.id='subs-panel';
 const head=el('div','panel-heading'),txt=el('div');
 txt.append(el('h2',null,'Subscriptions'),el('p','muted','Run agents on your own Claude or ChatGPT plan instead of an API key: it uses the plan\'s limits, with no per-token cost. Tools still go through the gateway, and agents never see the sign-in.'));
 head.append(txt);
 const note=el('p','fineprint error');note.id='subs-approver';note.hidden=true;
 const list=el('div','subs-list');list.id='subs-list';list.append(el('div','skeleton-row'));
 p.append(head,note,list);
 const sec=$('models');sec.insertBefore(p,sec.firstElementChild);return p}
function subsStatus(e){
 if(!e.installed)return el('span','pill st-unknown','Not installed');
 if(e.signed_in)return el('span','pill st-completed','Connected');
 if(subsBusy(e))return el('span','pill st-running','Signing in…');
 return el('span','pill st-pending','Signed out')}
function subsTestLine(id){const r=SUBS.tests[id];if(!r)return null;
 return el('div','model-test '+(r.ok?'ok':'bad'),r.ok?'OK · '+r.ms+' ms'+(r.reply?' · “'+String(r.reply).slice(0,120)+'”':''):'Failed · '+(r.error||'no answer')+(r.ms!=null?' ('+r.ms+' ms)':''))}
function subsRender(){
 if(!$('models'))return;subsPanel();const list=$('subs-list');list.replaceChildren();
 const ap=modelsData&&modelsData.approver,note=$('subs-approver');
 note.hidden=!ap||ap.allowed;note.textContent=ap&&!ap.allowed?'Signing in and out needs an approver: '+(ap.reason||''):'';
 if(!SUBS.data){list.append(el('div','skeleton-row'));return}
 if(SUBS.data.error&&!subsEngines().length){
  list.append(el('p','muted subs-empty',/not found/i.test(SUBS.data.error)?'Subscriptions are not available on this server yet.':'Subscriptions could not be loaded: '+SUBS.data.error));return}
 // One cyan primary per view: only the first "Sign in" is primary (the rest of the Models page has none).
 let primary=false;
 for(const e of subsEngines()){
  const row=el('div','subs-row');row.dataset.engine=e.id;
  const main=el('div','subs-main'),title=el('div','row-title');
  title.append(el('strong',null,subsName(e)),subsStatus(e));
  if(e.signed_in){const who=subsWho(e);if(who)title.append(el('span','subs-account',who))}
  const sub=el('div','row-sub subs-sub');
  sub.textContent=[SUBS_HINT[e.id]||'',!e.installed?'not installed on the server':e.version?'version '+e.version:''].filter(Boolean).join(' · ');
  main.append(title,sub);
  if(e.last_error)main.append(el('div','subs-error',e.last_error));
  const tl=subsTestLine(e.id);if(tl)main.append(tl);
  const acts=el('div','subs-actions');
  if(e.installed&&!e.signed_in){
   const b=el('button',primary?'secondary small':'small primary',subsBusy(e)?'Continue sign-in':'Sign in');b.type='button';primary=true;
   b.setAttribute('aria-label',(subsBusy(e)?'Continue signing in to ':'Sign in to ')+subsName(e));b.onclick=()=>subsSignIn(e.id,b);acts.append(b)}
  if(e.signed_in){const t=el('button','secondary small','Test');t.type='button';t.setAttribute('aria-label','Test '+subsName(e));
   t.title='One short request on your plan; not recorded as a task';t.onclick=()=>subsTest(e.id,t);acts.append(t)}
  if(e.installed)acts.append(actionMenu('More',[
   {label:'Cancel sign-in',disabled:!subsBusy(e),run:()=>subsCancel(e.id)},
   {label:'Sign out',danger:true,disabled:!e.signed_in,title:e.signed_in?'':'Not signed in',run:()=>subsSignOut(e.id)}],'subs-more'));
  row.append(main,acts);list.append(row)}
 if(!subsEngines().length)list.append(el('p','muted subs-empty','No subscription engines on this server.'));
}
async function subsTest(id,btn){await whileBusy(btn,async()=>{
 try{const r=await subsPost(id,'test');SUBS.tests[id]=r;const e=subsEngines().find(x=>x.id===id);
  notice(r.ok?subsName(e||{id})+' answered in '+r.ms+' ms.':subsName(e||{id})+': '+(r.error||'no answer'));subsRender()}catch(err){notice(err.message)}})}
async function subsSignOut(id){
 const e=subsEngines().find(x=>x.id===id);if(!e)return;
 if(!await uiConfirm('Sign out of '+subsName(e)+'? Agents and conversations on its models stop until you sign in again.',{title:'Sign out',ok:'Sign out',danger:true}))return;
 try{const r=await subsPost(id,'logout');subsMerge(id,r&&r.id?r:{signed_in:false},true);delete SUBS.tests[id];notice('Signed out of '+subsName(e)+'.')}
 catch(err){notice(err.message)}
 await loadSubs();reloadModelLists().catch(()=>{})}
async function subsCancel(id){
 try{const r=await subsPost(id,'login/cancel');subsMerge(id,r)}catch(err){notice(err.message)}
 subsStopPoll();const d=V4_DRAWERS['subs-drawer'];if(d&&d.isOpen&&SUBS.openId===id)d.close();await loadSubs()}

// Sign-in drawer: open the vendor page, enter the code it shows (device flow) or paste the code it gives back.
async function subsSignIn(id,btn){
 const e=subsEngines().find(x=>x.id===id);if(!e)return;
 if(subsBusy(e)){subsShow(id);subsPoll();return}
 await whileBusy(btn,async()=>{
  try{const r=await subsPost(id,'login');subsMerge(id,r);subsShow(id);subsPoll();subsRender()}catch(err){notice(err.message)}})}
function subsStopPoll(){clearTimeout(SUBS.timer);SUBS.timer=null;SUBS.seq++}
function subsPoll(){
 clearTimeout(SUBS.timer);const seq=++SUBS.seq;
 SUBS.timer=setTimeout(async()=>{
  if(seq!==SUBS.seq||!SUBS.openId)return;
  await loadSubs();if(seq!==SUBS.seq||!SUBS.openId)return;
  const e=subsEngines().find(x=>x.id===SUBS.openId);if(!e)return;
  subsShow(e.id,true);
  const st=e.login&&e.login.state;
  if(['starting','waiting'].includes(st)&&!e.signed_in)subsPoll();else if(st==='done'||e.signed_in)subsDone()},2000)}
function subsDone(){reloadModelLists().catch(()=>{})}
function subsDots(){const t=el('span','typing');t.setAttribute('aria-hidden','true');t.append(el('span'),el('span'),el('span'));return t}
function subsSafeUrl(u){try{const x=new URL(String(u||''));return x.protocol==='https:'?x.href:null}catch(e){return null}}
function subsShow(id,fromPoll){
 const e=subsEngines().find(x=>x.id===id);if(!e)return;
 const lg=e.login||{state:'idle'},state=e.signed_in&&lg.state!=='error'?'done':lg.state||'idle';
 const d=drawer('subs-drawer');
 // Re-render only when something changed (a poll every 2 s must not disturb a code being typed).
 const key=JSON.stringify([id,state,lg.url,lg.user_code,lg.needs_code,lg.message,e.account,e.plan]);
 if(fromPoll&&d.isOpen&&key===SUBS.lastKey)return;
 SUBS.lastKey=key;SUBS.openId=id;
 d.onclose=()=>{SUBS.openId=null;SUBS.lastKey='';subsStopPoll();subsRender()};
 d.title.textContent='Sign in to '+subsName(e);
 d.meta.replaceChildren(state==='done'?el('span','pill st-completed','Connected'):state==='error'?el('span','pill st-failed','Sign-in failed'):
  ['starting','waiting'].includes(state)?el('span','pill st-running','Signing in…'):subsStatus(e));
 d.actions.replaceChildren();
 const typed=d.body.querySelector('#subs-code-input'),keepCode=typed?typed.value:'',hadFocus=!!typed&&document.activeElement===typed;
 const opened=!!d.body.querySelector('#subs-open.visited');
 const body=el('div','subs-flow');
 body.append(el('p','muted subs-intro','Use the account with your '+(e.id==='chatgpt'?'ChatGPT Plus or Pro':e.id==='claude'?'Claude Pro or Max':subsName(e))+' plan. The sign-in stays on this server: agents only get the answers, never the login.'));
 if(state==='starting'||state==='idle'){
  const w=el('p','subs-state');w.append(subsDots(),document.createTextNode(' Starting the sign-in…'));body.append(w)}
 if(state==='waiting'){
  const steps=el('ol','subs-steps');
  const url=subsSafeUrl(lg.url),s1=el('li');
  s1.append(el('span','subs-step-text','Open the sign-in page and approve access.'));
  if(url){const a=el('a','button-link subs-open'+(opened?' visited':''));a.href=url;a.target='_blank';a.rel='noopener noreferrer';withIcon(a,'ext','Open sign-in page');a.id='subs-open';
   a.addEventListener('click',()=>{a.classList.add('visited');const s=$('subs-code-submit');if(s)s.classList.remove('secondary')});s1.append(a)}
  else s1.append(el('span','muted','Waiting for the sign-in address…'));
  steps.append(s1);
  if(lg.user_code){const s2=el('li');s2.append(el('span','subs-step-text','Enter this code on that page:'));
   const row=el('div','subs-code-row'),code=el('code','subs-code',lg.user_code);code.id='subs-user-code';
   const copy=el('button','secondary small');copy.type='button';withIcon(copy,'copy','Copy');copy.setAttribute('aria-label','Copy the code');
   copy.onclick=async()=>{try{await navigator.clipboard.writeText(lg.user_code);notice('Code copied.')}catch(err){notice('Select the code and copy it.')}};
   row.append(code,copy);s2.append(row);steps.append(s2)}
  if(lg.needs_code){const s3=el('li');s3.append(el('span','subs-step-text','After you sign in, the page shows a code. Paste it here.'));
   const f=el('form','subs-code-form');f.autocomplete='off';
   const lab=el('label',null,'Paste the code');const inp=el('input');inp.id='subs-code-input';inp.required=true;inp.maxLength=2000;inp.spellcheck=false;inp.autocomplete='off';
   inp.setAttribute('autocapitalize','off');inp.value=keepCode;lab.append(inp);
   const sub=el('button',opened?null:'secondary','Submit');sub.type='submit';sub.id='subs-code-submit';
   f.append(lab,sub);f.onsubmit=ev=>{ev.preventDefault();whileBusy(sub,async()=>{
    try{const r=await subsPost(id,'login/code',{code:inp.value.trim()});subsMerge(id,r);
     if(r&&r.state==='done'){subsMerge(id,{signed_in:true},true);subsShow(id);await loadSubs();subsShow(id,true);subsDone()}else{subsShow(id);subsPoll()}}
    catch(err){notice(err.message)}})};
   s3.append(f);steps.append(s3)}
  body.append(steps);
  const w=el('p','subs-state muted');w.append(subsDots(),document.createTextNode(lg.needs_code?' Waiting for the code…':' Waiting for you to finish on the sign-in page…'));body.append(w)}
 if(state==='done'){const who=subsWho(e);const ok=el('div','subs-result ok');
  ok.append(ico('check'),el('span',null,'Connected'+(who?' as '+who:'')+'. Its models can now be picked for agents and conversations.'));body.append(ok)}
 if(state==='error'){const bad=el('div','subs-result bad');bad.append(ico('alert'),el('span',null,lg.message||e.last_error||'The sign-in did not finish.'));body.append(bad)}
 if(lg.message&&!['error','done'].includes(state))body.append(el('p','fineprint subs-msg',lg.message));
 const foot=el('div','subs-foot');
 if(state==='done'){const c=el('button','secondary','Done');c.type='button';c.id='subs-done';c.onclick=()=>d.close();foot.append(c)}
 else if(state==='error'){const t=el('button',null,'Try again');t.type='button';t.onclick=()=>whileBusy(t,async()=>{try{const r=await subsPost(id,'login');subsMerge(id,r);subsShow(id);subsPoll()}catch(err){notice(err.message)}});
  const c=el('button','ghost','Close');c.type='button';c.onclick=()=>d.close();foot.append(t,c)}
 else{const c=el('button','ghost','Cancel');c.type='button';c.id='subs-cancel';c.onclick=()=>whileBusy(c,()=>subsCancel(id));foot.append(c)}
 body.append(foot);
 d.body.replaceChildren(body);d.open();
 if(hadFocus){const i=$('subs-code-input');if(i)i.focus()}
}

// Model pickers: subscription models in their own "Subscriptions" group; disabled while their engine is signed out.
// Models the picker list does not carry are added (disabled) for a signed-out engine, so it shows what sign-in unlocks.
function subsDecorate(sel){
 if(!sel||sel.tagName!=='SELECT')return;SUBS.pickers.add(sel);
 if(!SUBS.data&&!SUBS.loading)loadSubs();
 const cur=sel.value,have=new Map();
 for(const o of [...sel.querySelectorAll('option')])if(isSubModel(o.value)){have.set(o.value,o.dataset.base||o.textContent);o.remove()}
 const old=sel.querySelector('optgroup.subs-group');if(old)old.remove();
 for(const e of subsEngines())if(e.installed!==false&&!e.signed_in)for(const m of e.models||[])if(m&&m.id&&!have.has(m.id))have.set(m.id,m.label||m.id);
 if(have.size){
  const g=document.createElement('optgroup');g.label='Subscriptions';g.className='subs-group';
  for(const [id,text] of have){const eng=subsEngineOf(id),off=!!eng&&!eng.signed_in;
   const o=new Option(text+(off?' (sign in first)':''),id);o.dataset.base=text;o.disabled=off;g.append(o)}
  sel.append(g);if([...sel.options].some(o=>o.value===cur))sel.value=cur}
 subsPlanPill(sel)}
// A small "Plan" pill next to a picker while a subscription model is chosen.
function subsPlanPill(sel){
 if(!sel.dataset.subsPill){sel.dataset.subsPill='1';sel.addEventListener('change',()=>subsPlanPill(sel))}
 let p=sel.nextElementSibling;
 if(!(p&&p.classList.contains('plan-pill'))){if(!isSubModel(sel.value))return;p=el('span','pill no-dot plan-pill','Plan');p.title='Runs on your subscription plan: no per-token cost';sel.after(p)}
 const hide=!isSubModel(sel.value);if(p.hidden!==hide)p.hidden=hide}
// Pickers with an id are kept even while detached (the Chief's settings move into a drawer); rebuilt ones are dropped.
function subsDecorateAll(){for(const s of [...SUBS.pickers]){if(!s.isConnected&&!s.id){SUBS.pickers.delete(s);continue}subsDecorate(s)}}
const subsAfter=(fn,ids)=>async function(...a){const r=await fn.apply(this,a);for(const id of ids())subsDecorate($(id));return r};
loadAgents=subsAfter(loadAgents,()=>['agent-model']);
loadRoutingModels=subsAfter(loadRoutingModels,()=>['task-model-override','thread-model-override']);
const subsRenderRouting=renderRouting;
renderRouting=function(){subsRenderRouting();for(const s of document.querySelectorAll('#model-routing select'))subsDecorate(s)};
const subsRenderProjectRouting=renderProjectRouting;
renderProjectRouting=function(){subsRenderProjectRouting();for(const s of document.querySelectorAll('#model-project-kinds select'))subsDecorate(s)};
// Models page: the registry's subscription rows read "plan" instead of a price, provider "Subscription".
const subsRenderModels=renderModels;
renderModels=function(){
 subsRenderModels();
 // The default may be a subscription model offered without a registry row (signed-in engine): offer those too.
 const dsel=$('models-default'),d=modelsData;
 if(d&&dsel){for(const id of d.models||[])if(isSubModel(id)&&![...dsel.options].some(o=>o.value===id))dsel.add(new Option(modelOptionText(d,id),id));
  if(d.default&&d.default.source==='dashboard')dsel.value=d.default.model}
 subsDecorate(dsel);
 for(const tr of document.querySelectorAll('#models-table tbody tr[data-model]')){
  const m=(modelsData&&modelsData.registry||[]).find(x=>x.id===tr.dataset.model);if(!m||!isSubModel(m.id,m.billing))continue;
  tr.classList.add('model-sub');const b=tr.querySelector('.model-provider');if(b){b.textContent='Subscription';b.classList.add('model-provider-sub')}
  for(const td of tr.querySelectorAll('td[data-label="In / 1M"],td[data-label="Out / 1M"]'))td.textContent='plan';
  const eng=subsEngineOf(m.id);if(eng&&!eng.signed_in){const n=tr.querySelector('.model-name');if(n&&!n.querySelector('.subs-off'))n.append(el('div','fineprint subs-off','Sign in to '+subsName(eng)+' first (Subscriptions, above).'))}}
 subsRender()};
const subsCatalogCard=catalogCard;
catalogCard=function(m){const c=subsCatalogCard(m);
 if(isSubModel(m.id,m.billing)){const f=c.querySelector('.model-card-facts span:nth-child(2)');if(f)f.textContent='your plan · no per-token cost'}
 return c};
// Costs: subscription runs are not billed per token.
const subsRenderCosts=renderCosts;
renderCosts=function(d){
 subsRenderCosts(d);
 const subRow=r=>isSubModel(r.model,r.billing);
 if(!(d.by_model||[]).some(subRow))return;
 costTable('cost-models',['Model','Provider','Runs','Cost'],d.by_model,r=>subRow(r)?[r.model,'Subscription',fmt(r.runs),'Subscription']:[r.model,r.provider||'',fmt(r.runs),money(r.cost)]);
 const n=d.by_model.filter(subRow).reduce((s,r)=>s+Number(r.runs||0),0);
 $('cost-range').textContent+=' '+fmt(n)+' run'+(n===1?'':'s')+' on subscriptions (your plan, no per-token cost).'};
document.querySelector('button[data-view="models"]').addEventListener('click',()=>{subsRender();loadSubs()});
$('refresh').addEventListener('click',()=>{if(!$('models').hidden)loadSubs()});
//@SUBS-END
