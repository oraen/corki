"""Standalone plugin scripts must work with Unicode paths and Windows pipes."""
import json
import os
from pathlib import Path
import subprocess
import sys


def test_plugin_creator_unicode_roundtrip(tmp_path):
    scripts = Path(__file__).resolve().parents[2] / 'skills/plugin-creator/scripts'
    destination = tmp_path / '插件 files'
    marketplace = tmp_path / '市场.json'
    marketplace.write_text(json.dumps({
        'name': 'personal', 'interface': {'displayName': '我的插件'}, 'plugins': []
    }, ensure_ascii=False), encoding='utf-8-sig')
    env = {**os.environ, 'CORKI_HOME': str(tmp_path / 'home'), 'PYTHONIOENCODING': 'ascii'}

    def run(name, *args, success=True):
        result = subprocess.run(
            [sys.executable, '-X', 'utf8=0', str(scripts / name), *map(str, args)],
            env=env, capture_output=True, timeout=120,
        )
        output = result.stdout.decode('utf-8') + result.stderr.decode('utf-8')
        assert (result.returncode == 0) == success, output
        return output

    output = run('create_basic_plugin.py', 'example', '--path', destination,
                 '--with-skills', '--with-mcp', '--with-apps', '--with-marketplace',
                 '--marketplace-path', marketplace, '--category', '示例')
    assert '插件 files' in output
    plugin = destination / 'example'
    manifest = plugin / '.codex-plugin/plugin.json'
    data = json.loads(manifest.read_text(encoding='utf-8'))
    data['description'] = '中文插件'
    manifest.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8-sig')
    assert 'passed' in run('validate_plugin.py', plugin)
    run('update_plugin_cachebuster.py', plugin, '--cachebuster', 'fixture')
    assert json.loads(manifest.read_text(encoding='utf-8'))['description'] == '中文插件'
    assert run('read_marketplace_name.py', '--marketplace-path', marketplace).strip() == 'personal'
    saved = json.loads(marketplace.read_text(encoding='utf-8'))
    assert saved['interface']['displayName'] == '我的插件'
    assert saved['plugins'][0]['category'] == '示例'
    assert 'reserved by Windows' in run('create_basic_plugin.py', 'CON', '--path', destination, success=False)
    assert not (destination / 'con').exists()
