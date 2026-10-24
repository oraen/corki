"""Exercise bundled installer scripts against disposable repositories and homes."""
import importlib.util
import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / 'skills/skill-installer/scripts'


@pytest.fixture
def installer(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    monkeypatch.setenv('CORKI_HOME', str(tmp_path / 'corki'))
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path / 'user'))
    spec = importlib.util.spec_from_file_location(
        'tested_skill_installer', SCRIPTS / 'install-skill-from-github.py')
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    repo = tmp_path / 'repo'
    repo.mkdir()
    monkeypatch.setattr(module, '_prepare_repo', lambda *_: str(repo))
    return module, repo


def write_skill(root, name='example'):
    root.mkdir(parents=True, exist_ok=True)
    (root / 'SKILL.md').write_text(f'---\nname: {name}\ndescription: Test skill\n---\nInstructions')


def test_preview_then_install_and_collision(installer, tmp_path, capsys):
    m, repo = installer
    write_skill(repo / 'skills/example')
    args = ['--repo', 'fixture/repo', '--path', 'skills/example']
    assert m.main([*args, '--dry-run']) == 0
    assert not (tmp_path / 'corki/skills').exists()
    assert m.main(args) == 0
    assert (tmp_path / 'corki/skills/example/SKILL.md').is_file()
    assert m.main(args) == 1
    assert 'already exists' in capsys.readouterr().err


@pytest.mark.parametrize('scope', ['public', 'builtin', 'corki'])
def test_duplicate_metadata_name_across_roots(installer, tmp_path, scope, capsys):
    m, repo = installer
    roots = {'public': tmp_path / 'user/.agents/skills',
             'builtin': tmp_path / 'corki/skills/.system',
             'corki': tmp_path / 'corki/skills'}
    write_skill(roots[scope] / 'legacy-1.0', 'Example')
    write_skill(repo / 'example')
    assert m.main(['--repo', 'fixture/repo', '--path', 'example', '--scope', 'public']) == 1
    assert 'already installed' in capsys.readouterr().err
    assert not (tmp_path / 'user/.agents/skills/example').exists()


def test_mismatch_and_rename_are_rejected(installer):
    m, repo = installer
    write_skill(repo / 'version-1.0', 'example')
    assert m.main(['--repo', 'fixture/repo', '--path', 'version-1.0']) == 1
    write_skill(repo / 'example')
    assert m.main(['--repo', 'fixture/repo', '--path', 'example', '--name', 'renamed']) == 1


def test_batch_validates_before_writing(installer, tmp_path):
    m, repo = installer
    write_skill(repo / 'first', 'first')
    write_skill(repo / 'second', 'wrong')
    assert m.main(['--repo', 'fixture/repo', '--path', 'first', 'second']) == 1
    assert not (tmp_path / 'corki/skills/first').exists()


def test_root_skill_public_scope_and_no_builtin_installs(installer, tmp_path):
    m, repo = installer
    write_skill(repo)
    args = ['--repo', 'fixture/repo', '--path', '.']
    assert m.main([*args, '--dest', str(tmp_path / 'corki/skills/.system')]) == 1
    assert m.main([*args, '--scope', 'public']) == 0
    assert (tmp_path / 'user/.agents/skills/example/SKILL.md').is_file()


def test_missing_name_and_unsafe_link(installer, tmp_path):
    m, repo = installer
    write_skill(repo / 'example')
    p = repo / 'example/SKILL.md'
    p.write_text('---\ndescription: Test\n---\nText')
    assert m.main(['--repo', 'fixture/repo', '--path', 'example']) == 1
    write_skill(repo / 'example')
    (repo / 'example/outside').symlink_to(tmp_path, target_is_directory=True)
    assert m.main(['--repo', 'fixture/repo', '--path', 'example']) == 1


def test_local_listing_offline_and_legacy_names(installer, tmp_path, monkeypatch, capsys):
    write_skill(tmp_path / 'user/.agents/skills/legacy-1.0', 'example')
    write_skill(tmp_path / 'corki/skills/.system/builtin', 'builtin')
    spec = importlib.util.spec_from_file_location('tested_skill_list', SCRIPTS / 'list-skills.py')
    listing = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(listing)
    monkeypatch.setattr(listing, '_request', lambda *_: pytest.fail(
        'local inventory must not request network'))
    assert listing.main(['--local', '--format', 'json']) == 0
    rows = json.loads(capsys.readouterr().out)
    assert {(row['name'], row['scope']) for row in rows} == {
        ('example', 'public'), ('builtin', 'builtin')}
    assert listing._installed_skills() == {'example', 'builtin'}


@pytest.mark.parametrize('name', ['../escape', r'root\..\escape', 'C:escape'])
def test_archive_paths_validated_before_any_extraction(installer, tmp_path, name):
    module, _ = installer
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        archive.writestr('root/good.txt', 'fixture')
        archive.writestr(name, 'must not be extracted')
    destination = tmp_path / 'output'
    destination.mkdir()
    with zipfile.ZipFile(stream) as archive, pytest.raises(module.InstallError):
        module._safe_extract_zip(archive, str(destination))
    assert list(destination.iterdir()) == []


@pytest.mark.parametrize('path', ['C:relative', r'C:\absolute', r'\rooted'])
def test_repo_selection_rejects_windows_anchors(installer, path):
    module, _ = installer
    with pytest.raises(module.InstallError):
        module._validate_relative_path(path)


def test_git_error_decoding_preserves_utf8_and_tolerates_invalid_bytes(installer):
    module, _ = installer
    script = (
        "import sys; sys.stderr.buffer.write('测试失败'.encode('utf-8') + b'\\xff'); sys.exit(1)"
    )
    with pytest.raises(module.InstallError, match='测试失败'):
        module._run_git([sys.executable, '-c', script])


def test_listing_cli_outputs_utf8_under_ascii_stdio(installer, tmp_path):
    write_skill(tmp_path / '隔离用户/.agents/skills/example')
    bootstrap = (
        "import pathlib,runpy,sys; "
        "home=pathlib.Path(sys.argv[1]); pathlib.Path.home=classmethod(lambda cls:home); "
        "script=sys.argv[2]; sys.path.insert(0,str(pathlib.Path(script).parent)); "
        "sys.argv=[script,'--local','--format','json']; "
        "runpy.run_path(script,run_name='__main__')"
    )
    result = subprocess.run(
        [sys.executable, '-X', 'utf8=0', '-c', bootstrap,
         str(tmp_path / '隔离用户'), str(SCRIPTS / 'list-skills.py')],
        env={**os.environ, 'PYTHONIOENCODING': 'ascii'}, capture_output=True, check=True,
    )
    assert json.loads(result.stdout.decode('utf-8'))[0]['name'] == 'example'


@pytest.mark.skipif(os.name != 'nt', reason='actual NTFS directory junction')
def test_installer_and_listing_do_not_follow_directory_junctions(installer, tmp_path):
    import skill_catalog
    module, repo = installer
    outside = tmp_path / 'outside'
    write_skill(outside, 'outside')
    write_skill(repo / 'example')
    link = repo / 'example' / 'redirect'
    subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(link), str(outside)],
                   check=True, capture_output=True)
    try:
        assert module.main(['--repo', 'fixture/repo', '--path', 'example']) == 1
        names = {row['name'] for row in skill_catalog.local_skills(repo)}
        assert names == {'example'}
        assert (outside / 'SKILL.md').is_file()
    finally:
        link.rmdir()


@pytest.mark.skipif(os.name != 'nt', reason='actual Windows filename mapping')
@pytest.mark.parametrize('names', [('root/NUL.txt',), ('root/file:stream',),
                                 ('root/file.',), ('root/A.txt', 'root/a.txt')])
def test_windows_archive_names_cannot_alias_or_open_devices(installer, tmp_path, names):
    module, _ = installer
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        for name in names:
            archive.writestr(name, 'fixture')
    destination = tmp_path / 'output'
    destination.mkdir()
    with zipfile.ZipFile(stream) as archive, pytest.raises(module.InstallError):
        module._safe_extract_zip(archive, str(destination))
    assert list(destination.iterdir()) == []
