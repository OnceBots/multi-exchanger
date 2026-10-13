# Corrección de pruebas portables para Windows/Linux

Este parche actualiza únicamente los tests del mapeo de archivos del Bot API local.

- Usa nombres de directorio de prueba sin `:` para que Windows pueda crear las carpetas temporales. Los tokens reales de Telegram siguen siendo usados sin transformar por la aplicación en Linux.
- Asegura que el test del wrapper cree el archivo de host esperado antes de solicitar el mapeo, respetando que el mapeador real rechaza archivos que no existen.
- No modifica `.env`, tokens, claves, configuración ni código de producción.

Verificación recomendada en el repositorio: `python -m pytest -q tests/test_local_api_file_path_mapping.py tests/test_vultr_api_client.py`. En Windows, usa la versión de pytest compatible con el Python instalado.
