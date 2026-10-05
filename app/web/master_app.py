from __future__ import annotations

import html as html_lib

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.services.webapp_auth import WebAppAuthError, validate_init_data


def build_master_app_router(platform) -> APIRouter:
    router = APIRouter()

    @router.get("/master-app", response_class=HTMLResponse)
    async def master_app() -> str:
        return MASTER_APP_HTML

    @router.post("/api/master/create-bot")
    async def create_bot(
        request: Request,
        x_telegram_init_data: str | None = Header(default=None),
    ) -> dict:
        if platform.manager is None:
            raise HTTPException(status_code=503, detail="platform_not_ready")
        init_data = (x_telegram_init_data or "").strip()
        try:
            user = validate_init_data(
                init_data,
                platform.settings.master_bot_token,
                platform.settings.webapp_auth_max_age_seconds,
            )
        except WebAppAuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

        user_id = int(user.get("id", 0))
        if not user_id:
            raise HTTPException(status_code=401, detail="telegram_user_missing")

        body = await request.json()
        token = str(body.get("token", "")).strip()
        display_name = str(body.get("name", "")).strip()[:80]
        requested_username = str(body.get("username", "")).strip()[:80]

        if not token or ":" not in token:
            raise HTTPException(status_code=400, detail="token_invalid")
        if not display_name:
            display_name = "Bot hijo"

        info = await platform.manager.register_bot(
            token,
            user_id,
            metadata={
                "display_name": display_name,
                "requested_username": requested_username,
            },
        )
        return {
            "ok": True,
            "bot_id": info.bot_id,
            "username": info.username,
            "url": f"https://t.me/{info.username}" if info.username else None,
            "status": str(info.status),
        }

    return router


MASTER_APP_HTML = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
<title>Crear bot</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
:root{color-scheme:dark;--bg:var(--tg-theme-bg-color,#0d1117);--card:var(--tg-theme-secondary-bg-color,#18212b);--text:var(--tg-theme-text-color,#f5f7fa);--hint:var(--tg-theme-hint-color,#92a0b0);--accent:#49a9ff;--border:rgba(255,255,255,.08)}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-family:Arial,sans-serif;padding:18px 16px 30px}.shell{max-width:460px;margin:auto}.brand{display:flex;justify-content:center;margin:8px 0 16px}.avatar{width:76px;height:76px;border-radius:50%;display:flex;align-items:center;justify-content:center;background:linear-gradient(145deg,#18212b,#0f151d);border:1px solid var(--border);font-size:36px}.title{text-align:center;font-size:23px;font-weight:800}.subtitle{text-align:center;color:var(--hint);font-size:13px;margin:6px 16px 20px}.card{background:var(--card);border:1px solid var(--border);border-radius:18px;padding:16px}.field{margin-bottom:14px}.label{font-size:12px;color:var(--hint);margin-bottom:7px}.input{width:100%;border:1px solid rgba(255,255,255,.1);background:rgba(255,255,255,.03);color:var(--text);padding:13px 12px;border-radius:12px;outline:none;font-size:15px}.input:focus{border-color:rgba(73,169,255,.75)}.link-preview{font-size:12px;color:var(--accent);min-height:18px;margin-top:5px}.hint{font-size:12px;color:var(--hint);line-height:1.45}.secret{margin-top:14px;padding:12px;border-radius:12px;background:rgba(73,169,255,.08);border:1px solid rgba(73,169,255,.16);font-size:12px;line-height:1.45}.actions{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:16px}.btn{border:0;border-radius:12px;padding:13px;font-weight:700;font-size:14px}.btn-primary{background:var(--accent);color:#fff}.btn-secondary{background:rgba(255,255,255,.06);color:var(--text)}.status{margin-top:14px;display:none;padding:12px;border-radius:12px;background:rgba(255,255,255,.04);font-size:13px;line-height:1.45}.success{border:1px solid rgba(34,197,94,.22);background:rgba(34,197,94,.08)}.error{border:1px solid rgba(239,68,68,.22);background:rgba(239,68,68,.08)}.loading .btn-primary{opacity:.55;pointer-events:none}
</style>
</head>
<body>
<div class="shell">
  <div class="brand"><div class="avatar">🤖</div></div>
  <div class="title">Crear bot</div>
  <div class="subtitle">Crea y administra tu propio bot hijo desde el Master.</div>
  <div class="card" id="card">
    <div class="field"><div class="label">Nombre del bot</div><input id="name" class="input" maxlength="80" placeholder="Solicitudes de unión"></div>
    <div class="field"><div class="label">Nombre de usuario del bot o URL</div><input id="username" class="input" maxlength="80" placeholder="@mi_bot o https://t.me/mi_bot"><div id="preview" class="link-preview"></div></div>
    <div class="field"><div class="label">Token de BotFather</div><input id="token" class="input" type="password" autocomplete="off" placeholder="123456789:AA..."></div>
    <div class="secret">🔐 <b>Token privado</b><br>Se valida directamente con Telegram y se almacena cifrado. Nunca se publica ni se muestra a otros usuarios.</div>
    <div class="actions"><button class="btn btn-secondary" onclick="closeApp()">Cancelar</button><button class="btn btn-primary" onclick="createBot()">Crear</button></div>
    <div id="status" class="status"></div>
  </div>
</div>
<script>
const tg=window.Telegram?.WebApp;if(tg){try{tg.expand();tg.ready();}catch(e){}}
const $=id=>document.getElementById(id);
function preview(){let v=$("username").value.trim();if(!v){$("preview").textContent="";return;}if(v.startsWith("@"))v=v.slice(1);v=v.replace(/^https?:\\/\\/(t\\.me\\/)?/i,"").replace(/^t\\.me\\//i,"").split(/[/?#]/)[0];$("preview").textContent=v?`Enlace: https://t.me/${v}`:"";}
$("username").addEventListener("input",preview);
function closeApp(){try{tg?.close();}catch(e){}}
async function createBot(){
  const name=$("name").value.trim();const username=$("username").value.trim();const token=$("token").value.trim();
  if(!name){return show("Escribe el nombre del bot.",false);}if(!token||!token.includes(":")){return show("El token de BotFather no parece válido.",false);}
  document.body.classList.add("loading");show("⏳ Validando el token con Telegram y preparando tu bot...",null);
  try{const res=await fetch('/api/master/create-bot',{method:'POST',headers:{'Content-Type':'application/json','X-Telegram-Init-Data':tg?.initData||''},body:JSON.stringify({name,username,token})});const data=await res.json();if(!res.ok)throw new Error(data.detail||'No se pudo crear el bot');
    show(`✅ <b>Bot creado correctamente.</b><br><br>🤖 @${data.username||data.bot_id}<br>🆔 ${data.bot_id}<br>🟢 ${data.status}`,true);$("token").value="";if(data.url){setTimeout(()=>{try{tg?.openTelegramLink(data.url);}catch(e){window.location.href=data.url;}},900);}
  }catch(e){show(`❌ ${escapeHtml(e.message||String(e))}`,false);}finally{document.body.classList.remove("loading");}
}
function show(msg,success){const s=$("status");s.style.display='block';s.className='status '+(success===true?'success':success===false?'error':'');s.innerHTML=msg;}
function escapeHtml(v){return String(v).replace(/[&<>\"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#039;'}[m]));}
</script>
</body>
</html>"""
