#!/usr/bin/env python3
"""Deprecated patch installer.

The local Bot API path mapping is integrated in app/bot/api_client.py and the
Vultr Compose override. Running the historical installer could overwrite those
fixes, so this file intentionally makes no changes.
"""
from __future__ import annotations


def main() -> int:
    print(
        "Este instalador antiguo quedó deshabilitado para evitar sobrescribir "
        "la corrección actual. No ejecuta cambios. Consulte "
        "PATCH_MANIFEST_LOCAL_API_PATHS_V4.md."
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
