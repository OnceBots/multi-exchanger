from __future__ import annotations

from datetime import datetime
from html import escape


BOTFATHER_URL = "https://t.me/BotFather"


def botfather_instructions(username: str | None, *, reason: str, expires_at: datetime | None = None) -> tuple[str, InlineKeyboardMarkup]:
    handle = f"@{username.lstrip('@')}" if username else "tu bot"
    if reason == "telegram_deleted":
        title = "🗑️ BOT ELIMINADO EN TELEGRAM"
    elif reason == "timer":
        title = "⏱ TIEMPO AGOTADO"
    else:
        title = "🗑 ELIMINACIÓN DEL BOT"
    if reason == "telegram_deleted":
        intro = (
            f"Telegram ya no acepta el token de <b>{escape(handle)}</b>. "
            "Esto normalmente ocurre cuando el bot fue eliminado desde <b>@BotFather</b>. "
            "La plataforma lo detectó automáticamente y lo pasó a estado <b>ARCHIVED</b>."
        )
    elif reason == "timer":
        intro = f"El tiempo configurado para <b>{escape(handle)}</b> ha terminado. El bot fue detenido en esta plataforma para evitar que siga operando."
        if expires_at:
            intro += f"\n\n⏰ Vencimiento: <code>{escape(expires_at.isoformat())}</code>"
    else:
        intro = f"Para eliminar definitivamente <b>{escape(handle)}</b> de Telegram debes hacerlo desde <b>@BotFather</b>."
    details = (
        (
            "<b>Qué se conserva:</b>\n"
            "✅ La configuración registrada en nuestra plataforma.\n"
            "✅ Las salas, miembros y registros de actividad.\n"
            "✅ El historial que ya existe en Telegram.\n\n"
            "⚠️ Un bot eliminado en Telegram no puede volver a funcionar con ese mismo token."
        )
        if reason == "telegram_deleted"
        else (
            "<b>Cómo eliminarlo definitivamente:</b>\n"
            "1️⃣ Abre <b>@BotFather</b>.\n"
            "2️⃣ Envía <code>/mybots</code>.\n"
            f"3️⃣ Selecciona <b>{escape(handle)}</b>.\n"
            "4️⃣ Entra en <b>Delete Bot</b> / <b>Eliminar bot</b>.\n"
            "5️⃣ Confirma la eliminación.\n\n"
            "⚠️ Telegram indica que <b>/deletebot</b> es irreversible. El historial que ya existe en Telegram no es borrado por nuestra plataforma."
        )
    )
    text = f"<b>{title}</b>\n\n{intro}\n\n{details}"
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🤖 Abrir @BotFather", url=BOTFATHER_URL)],
    ])
    return text, markup


def schedule_label(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes} min"
    hours, rem = divmod(minutes, 60)
    if rem:
        return f"{hours} h {rem} min"
    return f"{hours} h"
