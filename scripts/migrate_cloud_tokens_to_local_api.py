"""One-time migration of stored bot tokens from Telegram's hosted Bot API.

Dry-run by default. Run only during a planned cutover after the local Bot API
server is reachable and the old Render worker has been stopped.

Usage:
    python scripts/migrate_cloud_tokens_to_local_api.py
    python scripts/migrate_cloud_tokens_to_local_api.py --execute

The script never prints or persists plaintext tokens. Disabled/archived bots
that are no longer valid are reported as warnings rather than blocking active
bot migration.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv
from pymongo import AsyncMongoClient


@dataclass(frozen=True)
class Candidate:
    label: str
    token: str
    active: bool


def _secret_box_key() -> bytes:
    raw = os.getenv("BOT_TOKEN_ENCRYPTION_KEY", "").strip()
    if not raw:
        raise RuntimeError("Falta BOT_TOKEN_ENCRYPTION_KEY en .env")
    try:
        Fernet(raw.encode("utf-8"))
    except Exception as exc:
        raise RuntimeError("BOT_TOKEN_ENCRYPTION_KEY no es una clave Fernet válida") from exc
    return raw.encode("utf-8")


def _candidate_active(doc: dict[str, Any]) -> bool:
    status = str(doc.get("status", "")).upper()
    return doc.get("enabled") is not False and status not in {
        "ARCHIVED", "AUTH_ERROR", "INVALID_TOKEN", "DELETED", "STOPPED_INVALID_TOKEN"
    }


async def _telegram_ok(client: httpx.AsyncClient, base_url: str, token: str, method: str) -> tuple[bool, str]:
    try:
        response = await client.post(f"{base_url.rstrip('/')}/bot{token}/{method}")
        payload = response.json()
        return bool(payload.get("ok")), str(payload.get("description", ""))
    except (httpx.HTTPError, ValueError) as exc:
        # Do not stringify HTTP exceptions: their URL may contain the token.
        return False, type(exc).__name__


async def run(execute: bool) -> int:
    load_dotenv(Path(".env"))
    mongo_uri = os.getenv("MONGO_URI", "").strip()
    db_name = os.getenv("DB_NAME", "telegram_multibot").strip()
    master_token = (os.getenv("MASTER_BOT_TOKEN") or os.getenv("MASTER_TOKEN") or "").strip()
    api_base = os.getenv("TELEGRAM_API_BASE_URL", "http://127.0.0.1:8081").strip().rstrip("/")
    encryption_key = _secret_box_key()
    if not mongo_uri or not master_token:
        raise RuntimeError("Faltan MONGO_URI o MASTER_BOT_TOKEN en .env")

    mongo = AsyncMongoClient(mongo_uri, serverSelectionTimeoutMS=10000, connectTimeoutMS=10000)
    try:
        await mongo.admin.command("ping")
        docs = await mongo[db_name]["bots"].find(
            {}, {"bot_id": 1, "username": 1, "token_encrypted": 1, "encrypted_token": 1, "enabled": 1, "status": 1}
        ).to_list(length=None)

        fernet = Fernet(encryption_key)
        candidates: list[Candidate] = [Candidate("master", master_token, True)]
        seen = {master_token}
        for doc in docs:
            encrypted = doc.get("token_encrypted") or doc.get("encrypted_token")
            if not encrypted:
                continue
            try:
                token = fernet.decrypt(str(encrypted).encode("utf-8")).decode("utf-8").strip()
            except (InvalidToken, ValueError, TypeError):
                label = f"child:{doc.get('bot_id', 'unknown')}"
                print(f"ERROR {label}: no se pudo descifrar el token; verifica que BOT_TOKEN_ENCRYPTION_KEY sea la misma que en Render")
                if _candidate_active(doc):
                    return 2
                continue
            if not token or token in seen:
                continue
            seen.add(token)
            label = f"child:{doc.get('bot_id', 'unknown')}"
            candidates.append(Candidate(label, token, _candidate_active(doc)))

        print(f"Tokens encontrados: {len(candidates)} (master + registros de bots con token cifrado)")
        print(f"Bot API local configurada: {api_base}")
        if not execute:
            print("DRY-RUN: no se llamó a Telegram. Ejecuta con --execute durante la ventana de corte.")
            for item in candidates:
                print(f" - {item.label} ({'activo' if item.active else 'inactivo/archivado'})")
            return 0

        # Network preflight before changing any cloud session. HTTP 404 at the
        # root is fine; this check only verifies that a server answers at all.
        try:
            async with httpx.AsyncClient(timeout=10.0) as probe:
                await probe.get(api_base + "/")
        except httpx.HTTPError as exc:
            print(f"ERROR: Bot API local no responde en {api_base} ({type(exc).__name__}); no se migró ningún token.")
            return 3

        failures = 0
        async with httpx.AsyncClient(timeout=30.0) as telegram:
            for item in candidates:
                local_ok, _ = await _telegram_ok(telegram, api_base, item.token, "getMe")
                if local_ok:
                    print(f"OK {item.label}: ya funciona en el Bot API local")
                    continue

                _, cloud_description = await _telegram_ok(telegram, "https://api.telegram.org", item.token, "logOut")
                local_ok = False
                local_description = ""
                for attempt in range(5):
                    await asyncio.sleep(1 if attempt else 0.25)
                    local_ok, local_description = await _telegram_ok(telegram, api_base, item.token, "getMe")
                    if local_ok:
                        break

                if local_ok:
                    print(f"OK {item.label}: sesión local verificada")
                elif not item.active:
                    print(f"WARN {item.label}: no se pudo migrar y el bot está inactivo/archivado; revisar solo si se va a restaurar ({local_description or cloud_description or 'sin detalle'})")
                else:
                    failures += 1
                    safe_reason = local_description or cloud_description or "respuesta no confirmada"
                    print(f"ERROR {item.label}: no se pudo confirmar sesión en Bot API local ({safe_reason})")

        if failures:
            print(f"Migración incompleta: {failures} bot(s) activo(s) no quedaron verificados localmente.")
            return 4
        print("Migración de tokens activos finalizada. Los registros inactivos advertidos no se borraron ni modificaron en MongoDB.")
        return 0
    finally:
        await mongo.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="ejecutar logOut en la API oficial y comprobar la API local")
    args = parser.parse_args()
    try:
        exit_code = asyncio.run(run(args.execute))
    except KeyboardInterrupt:
        exit_code = 130
    except Exception as exc:
        print(f"ERROR: {exc}")
        exit_code = 1
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
