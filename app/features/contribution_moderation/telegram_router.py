from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message


def _menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 Mi reputación", callback_data="contrib:profile"), InlineKeyboardButton(text="🏆 Ranking", callback_data="contrib:ranking")],
        [InlineKeyboardButton(text="📖 Cómo funciona", callback_data="contrib:rules")],
    ])


def _mod_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Aportes pendientes", callback_data="mod:pending")],
        [InlineKeyboardButton(text="🚩 Reportes", callback_data="mod:reports")],
        [InlineKeyboardButton(text="📊 Estadísticas", callback_data="mod:stats")],
    ])


def build_contribution_router(ctx) -> Router:
    router = Router(name=f"contribution:{ctx.bot_id}")
    service = ctx.services.contribution

    async def profile_text(uid: int) -> str:
        profile = service.decorate_profile(await service.profile(ctx.bot_id, uid))
        return (
            "<b>👤 PERFIL DE CONTRIBUCIÓN</b>\n\n"
            f"⭐ Reputación: <b>{int(profile.get('reputation', 0))}</b>\n"
            f"🏷 Nivel: <b>{html.escape(str(profile.get('level')))}</b>\n"
            f"💰 Créditos: <b>{int(profile.get('credits', 0))}</b>\n"
            f"📤 Aportes: <b>{int(profile.get('approved_contributions', 0))}</b> aprobados / {int(profile.get('contributions', 0))} enviados\n"
            f"📥 Descargas: <b>{int(profile.get('downloads', 0))}</b>\n"
            f"⚖️ Ratio: <b>{profile.get('ratio', 0)}</b>\n\n"
            "Los aportes válidos aumentan tus créditos y reputación."
        )

    @router.message(Command("aporte", "aportaciones", "reputacion"))
    async def contribution_profile(message: Message) -> None:
        if not message.from_user:
            return
        await message.answer(await profile_text(int(message.from_user.id)), reply_markup=_menu())

    @router.callback_query(F.data == "contrib:profile")
    async def contribution_profile_cb(callback: CallbackQuery) -> None:
        try:
            await callback.answer()
        except Exception:
            pass
        await callback.message.edit_text(await profile_text(int(callback.from_user.id)), reply_markup=_menu())

    @router.message(Command("ranking"))
    async def ranking_cmd(message: Message) -> None:
        rows = await service.leaderboard(ctx.bot_id, 10)
        text = "<b>🏆 TOP COLABORADORES</b>\n\n"
        if not rows:
            text += "Todavía no hay actividad suficiente."
        else:
            medals = ["🥇", "🥈", "🥉"]
            for index, row in enumerate(rows, start=1):
                label = medals[index - 1] if index <= 3 else f"<b>{index}.</b>"
                name = html.escape(str(row.get("username") or row.get("first_name") or row.get("user_id")))
                text += f"{label} {name} · ⭐ {int(row.get('reputation', 0))} · 📤 {int(row.get('approved_contributions', 0))}\n"
        await message.answer(text)

    @router.callback_query(F.data == "contrib:ranking")
    async def ranking_cb(callback: CallbackQuery) -> None:
        try:
            await callback.answer()
        except Exception:
            pass
        rows = await service.leaderboard(ctx.bot_id, 10)
        text = "<b>🏆 TOP COLABORADORES</b>\n\n"
        for index, row in enumerate(rows, start=1):
            name = html.escape(str(row.get("username") or row.get("first_name") or row.get("user_id")))
            text += f"<b>{index}.</b> {name} · ⭐ {int(row.get('reputation', 0))} · 📤 {int(row.get('approved_contributions', 0))}\n"
        if not rows:
            text += "Todavía no hay actividad suficiente."
        await callback.message.edit_text(text, reply_markup=_menu())

    @router.message(Command("aporte_info", "reglas_aportes"))
    async def rules_cmd(message: Message) -> None:
        await message.answer(
            "<b>📖 SISTEMA DE CONTRIBUCIÓN</b>\n\n"
            "📤 Envía contenido válido a una sala.\n"
            "🛡️ El aporte puede quedar pendiente de moderación.\n"
            "✅ Un aporte aprobado otorga créditos y reputación.\n"
            "📥 Las descargas consumen créditos.\n"
            "🚫 El spam, duplicado o abuso puede reducir tu reputación o provocar restricciones.\n\n"
            "La finalidad es mantener una comunidad donde el consumo y la colaboración estén equilibrados.",
            reply_markup=_menu(),
        )

    @router.callback_query(F.data == "contrib:rules")
    async def rules_cb(callback: CallbackQuery) -> None:
        try:
            await callback.answer()
        except Exception:
            pass
        await callback.message.edit_text(
            "<b>📖 SISTEMA DE CONTRIBUCIÓN</b>\n\n"
            "📤 Aporta contenido válido.\n✅ Los aportes aprobados generan créditos y reputación.\n📥 Las descargas consumen créditos.\n🚫 El abuso puede generar restricciones.",
            reply_markup=_menu(),
        )

    def is_moderator(uid: int) -> bool:
        return uid == ctx.owner_id or uid in set(ctx.settings.admin_ids or [])

    @router.message(Command("moderacion", "mod"))
    async def moderation_cmd(message: Message) -> None:
        if not message.from_user or not is_moderator(int(message.from_user.id)):
            await message.answer("⛔ No autorizado.")
            return
        await message.answer("<b>🛡 PANEL DE MODERACIÓN</b>", reply_markup=_mod_menu())

    @router.callback_query(F.data == "mod:pending")
    async def pending_cb(callback: CallbackQuery) -> None:
        if not is_moderator(int(callback.from_user.id)):
            await callback.answer("No autorizado", show_alert=True)
            return
        await callback.answer()
        rows = await ctx.services.contribution.repo.list_pending_content(ctx.bot_id, 10)
        if not rows:
            await callback.message.edit_text("✅ No hay aportes pendientes.", reply_markup=_mod_menu())
            return
        text = "<b>📥 APORTES PENDIENTES</b>\n\n"
        buttons = []
        for item in rows:
            cid = str(item["contribution_id"])
            text += f"<code>{cid[:8]}</code> · {html.escape(str(item.get('media_type')))} · usuario <code>{item.get('uploader_id')}</code>\n"
            buttons.append([InlineKeyboardButton(text=f"✅ {cid[:8]}", callback_data=f"mod:approve:{cid}"), InlineKeyboardButton(text=f"❌ {cid[:8]}", callback_data=f"mod:reject:{cid}")])
        buttons.append([InlineKeyboardButton(text="↩️ Panel", callback_data="mod:panel")])
        await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

    @router.callback_query(F.data.startswith("mod:approve:"))
    async def approve_cb(callback: CallbackQuery) -> None:
        if not is_moderator(int(callback.from_user.id)):
            await callback.answer("No autorizado", show_alert=True)
            return
        cid = callback.data.split(":", 2)[2]
        result = await service.approve_contribution(ctx.bot_id, cid, int(callback.from_user.id))
        await callback.answer("✅ Aprobado" if result else "Ya procesado", show_alert=not bool(result))
        await callback.message.edit_text("✅ <b>Aporte aprobado</b>\n\nEl usuario recibió su recompensa.", reply_markup=_mod_menu())

    @router.callback_query(F.data.startswith("mod:reject:"))
    async def reject_cb(callback: CallbackQuery) -> None:
        if not is_moderator(int(callback.from_user.id)):
            await callback.answer("No autorizado", show_alert=True)
            return
        cid = callback.data.split(":", 2)[2]
        result = await service.reject_contribution(ctx.bot_id, cid, int(callback.from_user.id), reason="Rechazado por moderación")
        await callback.answer("❌ Rechazado" if result else "Ya procesado", show_alert=not bool(result))
        await callback.message.edit_text("❌ <b>Aporte rechazado</b>\n\nSe aplicó la política de moderación.", reply_markup=_mod_menu())

    @router.callback_query(F.data == "mod:reports")
    async def reports_cb(callback: CallbackQuery) -> None:
        if not is_moderator(int(callback.from_user.id)):
            await callback.answer("No autorizado", show_alert=True)
            return
        await callback.answer()
        rows = await ctx.services.contribution.repo.list_open_reports(ctx.bot_id, 10)
        text = "<b>🚩 REPORTES ABIERTOS</b>\n\n"
        if not rows:
            text += "No hay reportes pendientes."
        for row in rows:
            text += f"<code>{row['report_id'][:8]}</code> · {html.escape(str(row.get('reason')))} · contenido <code>{str(row.get('contribution_id'))[:8]}</code>\n"
        await callback.message.edit_text(text, reply_markup=_mod_menu())

    @router.callback_query(F.data == "mod:stats")
    async def stats_cb(callback: CallbackQuery) -> None:
        if not is_moderator(int(callback.from_user.id)):
            await callback.answer("No autorizado", show_alert=True)
            return
        await callback.answer()
        stats = await ctx.services.contribution.repo.stats(ctx.bot_id)
        await callback.message.edit_text(
            "<b>📊 MODERACIÓN</b>\n\n"
            f"👥 Usuarios: <b>{stats['users']}</b>\n"
            f"📥 Pendientes: <b>{stats['pending_contributions']}</b>\n"
            f"🚩 Reportes: <b>{stats['open_reports']}</b>\n"
            f"✅ Aprobados: <b>{stats['approved_contributions']}</b>\n"
            f"❌ Rechazados: <b>{stats['rejected_contributions']}</b>",
            reply_markup=_mod_menu(),
        )

    @router.callback_query(F.data == "mod:panel")
    async def mod_panel_cb(callback: CallbackQuery) -> None:
        if not is_moderator(int(callback.from_user.id)):
            await callback.answer("No autorizado", show_alert=True)
            return
        await callback.answer()
        await callback.message.edit_text("<b>🛡 PANEL DE MODERACIÓN</b>", reply_markup=_mod_menu())

    return router
