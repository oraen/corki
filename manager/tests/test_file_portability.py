"""Real handles and directory links at manager filesystem boundaries."""
import importlib.util
import io
import os
from pathlib import Path
import sqlite3
import subprocess
import zipfile

import pytest

spec = importlib.util.spec_from_file_location('manager_portability', Path(__file__).parents[1] / 'server.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_readonly_query_closes_connection_on_success_and_error(tmp_path, monkeypatch):
    path = tmp_path / '数据库.db'
    connection = sqlite3.connect(path)
    connection.execute('CREATE TABLE example (value TEXT)')
    connection.close()
    connections = []
    connect = sqlite3.connect

    def tracked(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connections.append(connection)
        return connection

    store = object.__new__(module.Store)
    store.database = path
    monkeypatch.setattr(module.sqlite3, 'connect', tracked)
    assert store.query('SELECT * FROM example') == []
    with pytest.raises(sqlite3.OperationalError):
        store.query('SELECT * FROM missing')
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match='closed'):
            connection.execute('SELECT 1')
    path.unlink()


def test_skill_package_link_is_never_browsed_or_exported(tmp_path):
    home = tmp_path / 'home'
    root = home / 'skills' / 'example'
    root.mkdir(parents=True)
    (root / 'SKILL.md').write_text('---\nname: example\ndescription: fixture\n---\n', encoding='utf-8')
    outside = tmp_path / '外部 files'
    outside.mkdir()
    (outside / 'external.txt').write_text('outside fixture', encoding='utf-8')
    link = root / 'linked'
    if os.name == 'nt':
        subprocess.run(f'cmd.exe /d /c mklink /J "{link}" "{outside}"',
                       check=True, capture_output=True, timeout=60)
    else:
        link.symlink_to(outside, target_is_directory=True)
    try:
        store = module.Store(home, tmp_path / 'workspace', tmp_path / 'compat')
        item = next(row for row in store.files() if row['kind'] == 'skills')
        package = store.skill_package(item['id'])
        assert next(row for row in package['entries'] if row['path'] == 'linked')['type'] == 'link'
        assert not any(row['path'].startswith('linked/') for row in package['entries'])
        with pytest.raises(ValueError):
            store.read_skill_resource(item['id'], 'linked/external.txt')
        data, _ = store.export_skill(item['id'])
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            assert archive.namelist() == ['example/SKILL.md']
        store.delete_skill({'id': item['id'], 'revision': item['revision']})
        assert not root.exists()
        assert (outside / 'external.txt').read_text(encoding='utf-8') == 'outside fixture'
    finally:
        if os.path.lexists(link):
            link.rmdir() if os.name == 'nt' else link.unlink()


def test_web_asset_types_ignore_host_file_associations(tmp_path, monkeypatch):
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer

    monkeypatch.setattr(module, 'STATIC', tmp_path)
    monkeypatch.setattr(module.mimetypes, 'guess_type', lambda path: ('text/plain', None))
    fixtures = {'index.html': 'text/html', 'app.js': 'text/javascript',
                'style.css': 'text/css', 'icon.svg': 'image/svg+xml', 'data.json': 'application/json'}
    for name in fixtures:
        (tmp_path / name).write_bytes(b'fixture')
    server = ThreadingHTTPServer(('127.0.0.1', 0), module.Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        for name, expected in fixtures.items():
            with urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}/{name}', timeout=10) as response:
                assert response.headers.get_content_type() == expected
                assert response.headers['X-Content-Type-Options'] == 'nosniff'
                assert response.read() == b'fixture'
    finally:
        server.shutdown()
        server.server_close()
        worker.join(10)
        assert not worker.is_alive()


def test_atomic_config_and_backup_preserve_bytes_and_private_permissions(tmp_path):
    path = tmp_path / '配置.json'
    original = '{"description":"原始"}\r\n'
    module.atomic(path, original)
    module.atomic(path, '{"description":"更新"}\n')
    backups = list((tmp_path / '.manager-backups').iterdir())
    assert len(backups) == 1
    assert backups[0].read_bytes() == original.encode('utf-8')
    assert path.read_bytes() == '{"description":"更新"}\n'.encode('utf-8')
    assert {p.name for p in tmp_path.iterdir()} == {'配置.json', '.manager-backups'}
    for file in [path, *backups]:
        if os.name == 'nt':
            from corki.platforms.windows_acl import WindowsPrivateACL
            from corki.platforms.windows_file_handles import WindowsFiles
            files = WindowsFiles()
            handle = files.open(file, access=0x20000)
            try:
                WindowsPrivateACL().verify(handle)
            finally:
                files.close(handle)
        else:
            assert file.stat().st_mode & 0o777 == 0o600
