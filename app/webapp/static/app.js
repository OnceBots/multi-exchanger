const tg = window.Telegram?.WebApp;
const params = new URLSearchParams(location.search);
const botId = Number(params.get('bot_id'));
const initData = tg?.initData || '';
if (tg) tg.expand();

async function api(path, options={}) {
  const headers = {...(options.headers||{}), 'X-Telegram-Init-Data': initData, 'Content-Type':'application/json'};
  const r = await fetch(path, {...options, headers});
  if(!r.ok) throw new Error(await r.text());
  return r.json();
}
async function load(){
  try{
    const data=await api(`/api/${botId}/rooms`);
    document.querySelector('#status').textContent=`${data.rooms.length} salas públicas`;
    const all=[...data.rooms];
    document.querySelector('#rooms').innerHTML=all.map(r=>`<article class="card room"><div><b>${escapeHtml(r.name)}</b><div>${r.current_members} miembros · ${r.status}</div></div><button onclick="joinRoom('${r.room_id}')">UNIRME</button></article>`).join('') || '<div class="card">No hay salas activas.</div>';
  }catch(e){document.querySelector('#status').textContent='No se pudo autenticar'; console.error(e);}
}
async function joinRoom(id){await api(`/api/${botId}/rooms/${encodeURIComponent(id)}/join`,{method:'POST'}); await load();}
async function createRoom(){
  const name=document.querySelector('#name').value.trim(); const description=document.querySelector('#description').value.trim(); const visibility=document.querySelector('#visibility').value; const max=Number(document.querySelector('#max').value||0);
  await api(`/api/${botId}/rooms`,{method:'POST',body:JSON.stringify({name,description,visibility,max_members:max})}); await load();
}
function escapeHtml(s){return String(s).replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));}
load();
