"""Verification script for OpenMontage integration."""
import sys
import os

sys.path.insert(0, '.')

print('='*60)
print('VERIFICACION DE INTEGRACION OPENMONTAGE')
print('='*60)

# 1. Verificar estructura de archivos
paths = [
    'tools/base_tool.py',
    'tools/tool_registry.py', 
    'tools/subtitle/subtitle_gen.py',
    'tools/video/remotion_caption_burn.py',
    'tools/analysis/transcriber.py',
    'styles/playbook_loader.py',
    'styles/anime-ghibli.yaml',
    'schemas/styles/playbook.schema.json',
    'integrations/openmontage_bridge.py'
]

print('\n[1] ESTRUCTURA DE ARCHIVOS:')
all_exist = True
for p in paths:
    exists = os.path.exists(p)
    status = 'OK' if exists else 'FALTA'
    print(f'  [{status}] {p}')
    if not exists:
        all_exist = False

print(f'\n  Resultado: {"✓ Todos los archivos existen" if all_exist else "✗ Faltan archivos"}')

# 2. Verificar imports
print('\n[2] IMPORTS DE PYTHON:')
try:
    from tools.base_tool import BaseTool, ToolResult
    print('  ✓ tools.base_tool')
except Exception as e:
    print(f'  ✗ tools.base_tool: {e}')

try:
    from tools.tool_registry import registry, ToolRegistry
    print('  ✓ tools.tool_registry')
except Exception as e:
    print(f'  ✗ tools.tool_registry: {e}')

try:
    from tools.subtitle.subtitle_gen import SubtitleGen
    print('  ✓ tools.subtitle.subtitle_gen')
except Exception as e:
    print(f'  ✗ tools.subtitle.subtitle_gen: {e}')

try:
    from tools.video.remotion_caption_burn import RemotionCaptionBurn
    print('  ✓ tools.video.remotion_caption_burn')
except Exception as e:
    print(f'  ✗ tools.video.remotion_caption_burn: {e}')

try:
    from styles.playbook_loader import load_playbook, list_playbooks
    print('  ✓ styles.playbook_loader')
except Exception as e:
    print(f'  ✗ styles.playbook_loader: {e}')

try:
    from integrations.openmontage_bridge import OpenMontageBridge, bridge
    print('  ✓ integrations.openmontage_bridge')
except Exception as e:
    print(f'  ✗ integrations.openmontage_bridge: {e}')

# 3. Verificar funcionalidad basica
print('\n[3] FUNCIONALIDAD BASICA:')
try:
    from integrations import bridge
    tools = bridge.get_available_tools()
    print(f'  ✓ Bridge: {len(tools)} tools disponibles')
    for t in tools:
        print(f'     - {t["name"]} ({t["status"]})')
except Exception as e:
    print(f'  ✗ Bridge error: {e}')

try:
    styles = bridge.list_available_styles()
    print(f'  ✓ Styles: {len(styles)} estilos')
    for s in styles:
        print(f'     - {s}')
except Exception as e:
    print(f'  ✗ Styles error: {e}')

# 4. Verificar schema
print('\n[4] SCHEMAS:')
try:
    import json
    with open('schemas/styles/playbook.schema.json') as f:
        schema = json.load(f)
    print(f'  ✓ Schema valido: {schema.get("title", "N/A")}')
except Exception as e:
    print(f'  ✗ Schema error: {e}')

print('\n' + '='*60)
print('VERIFICACION COMPLETA')
print('='*60)
