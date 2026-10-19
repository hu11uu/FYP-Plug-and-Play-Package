let state, busy = false;
const $ = id => document.getElementById(id);
const textNode = (tag, text, cls) => {const el=document.createElement(tag);el.textContent=text;if(cls)el.className=cls;return el;};
async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Request failed');
  return data;
}
function lock(value) {
  busy=value;
  document.querySelectorAll('button').forEach(el=>el.disabled=value);
  $('question').disabled=value || !!state?.survey_questions.length;
  $('send').disabled=$('question').disabled;
}
async function action(fn) {
  if(busy)return;
  lock(true);$('status').textContent='Working…';
  try{state=await fn();render();}catch(error){$('status').textContent=error.message;}finally{lock(false);}
}
function applyUi(){
  const ui=state.ui||{display_name:'this topic',brand:'TA',sample_questions:[],placeholder_hint:'Ask a question about this topic...',deployment:'custom'};
  if(document.title!==`${ui.brand} / Study & feedback lab`)document.title=`${ui.brand} / Study & feedback lab`;
  $('brand-text').textContent=ui.brand;
  $('heading').textContent=`Make sense of ${ui.display_name}.`;
  $('course-eyebrow').textContent=`${ui.brand}-DEMO / COURSE PACK`;
  $('chat-panel').setAttribute('aria-label',`${ui.display_name} chatbot`);
  $('question').placeholder=ui.placeholder_hint;
  return ui;
}
function render(){
  const ui=applyUi();
  $('mode').textContent=state.mode==='offline'?'OFFLINE TEST MODE · Answers use the sample pack. Model judgments are scripted fixtures, not AI evaluation.':'LIVE AZURE MODEL · The original prompts are sent to your configured service.';
  $('turns').textContent=`${state.msg_count} student messages`;
  $('completed').textContent=`${state.surveys_shown_count} / 2`;
  if(state.messages.length){$('messages').replaceChildren(...state.messages.map(m=>{const el=textNode('div','',`message ${m.role}`);el.append(textNode('span',m.role==='human'?'YOU':'STUDY ASSISTANT','role'),textNode('span',m.content));return el;}));}
  else $('messages').replaceChildren(textNode('div',`Try a question below to explore the ${ui.display_name} course pack.`,'welcome'));
  $('messages').scrollTop=$('messages').scrollHeight;
  $('suggestions').replaceChildren();
  if(!state.survey_questions.length&&!state.pending_scenarios.length){
    const suggestions=state.follow_up_suggestions.length?state.follow_up_suggestions:(ui.sample_questions.length?ui.sample_questions:['Ask a starter question']);
    suggestions.forEach(q=>{const b=textNode('button',q);b.onclick=()=>send(q);$('suggestions').append(b);});
  }
  $('scenarios').replaceChildren();
  if(state.pending_scenarios.length){$('scenarios').append(textNode('h3','Which situation applies?'));state.pending_scenarios.forEach(s=>{const b=textNode('button',s);b.onclick=()=>action(()=>api('/api/chat',{query:s,scenario:s}));$('scenarios').append(b);});}
  $('survey').hidden=!state.survey_questions.length;
  $('survey-items').replaceChildren();
  state.survey_questions.forEach((q,i)=>{const f=document.createElement('fieldset');f.append(textNode('legend',`${i+1}. ${q}`));for(let v=1;v<=5;v++){const l=document.createElement('label'),r=document.createElement('input');r.type='radio';r.name=`rating-${i}`;r.value=v;r.required=true;r.checked=state.survey_responses[i]===v;l.append(r,document.createTextNode(v));f.append(l);}$('survey-items').append(f);});
  $('status').textContent=state.last_error || (state.saved?'Feedback saved locally. You can continue the chat.':'');
  $('trace').textContent=state.events.map(e=>`Turn ${e.turn} · ${e.action}\n${e.decisions.join('\n')||'No survey decision logged.'}`).join('\n\n')||'No decisions yet.';
  $('course').replaceChildren(...state.course.map((doc,i)=>{const d=document.createElement('details');d.className='course-item';d.append(textNode('summary',`${String(i+1).padStart(2,'0')}  ${doc.title}`),textNode('p',doc.content));return d;}));
}
async function send(query){await action(async()=>{const result=await api('/api/chat',{query});$('question').value='';return result;});}
$('chat-form').onsubmit=e=>{e.preventDefault();send($('question').value);};
$('survey').onsubmit=e=>{e.preventDefault();const ratings=state.survey_questions.map((_,i)=>Number(document.querySelector(`input[name="rating-${i}"]:checked`).value));action(()=>api('/api/survey',{ratings}));};
$('new-session').onclick=()=>action(()=>api('/api/session',{}));
$('export').onclick=()=>{const blob=new Blob([JSON.stringify({exported_at:new Date().toISOString(),...state},null,2)],{type:'application/json'});const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=`${(state.ui&&state.ui.deployment)||'chatbot'}-test-conversation.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
action(async()=>{try{return await api('/api/state');}catch{return await api('/api/session',{});}});
