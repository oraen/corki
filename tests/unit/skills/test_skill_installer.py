"""Exercise bundled installer scripts against disposable repositories and homes."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / 'skills/skill-installer/scripts'


@pytest.fixture
def installer(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    monkeypatch.setenv('CORKI_HOME', str(tmp_path / 'corki'))
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path / 'user'))
    spec = importlib.util.spec_from_file_location('tested_skill_installer', SCRIPTS / 'install-skill-from-github.py')
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
    roots = {'public': tmp_path / 'user/.agents/skills', 'builtin': tmp_path / 'corki/skills/.system',
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
    (repo / 'example/outside').symlink_to(tmp_path)
    assert m.main(['--repo', 'fixture/repo', '--path', 'example']) == 1


def test_local_listing_offline_and_legacy_names(installer, tmp_path, monkeypatch, capsys):
    import skill_catalog
    write_skill(tmp_path / 'user/.agents/skills/legacy-1.0', 'example')
    write_skill(tmp_path / 'corki/skills/.system/builtin', 'builtin')
    spec = importlib.util.spec_from_file_location('tested_skill_list', SCRIPTS / 'list-skills.py')
    listing = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(listing)
    monkeypatch.setattr(listing, '_request', lambda *_: pytest.fail('local inventory must not request network'))
    assert listing.main(['--local', '--format', 'json']) == 0
    rows = json.loads(capsys.readouterr().out)
    assert {(row['name'], row['scope']) for row in rows} == {('example', 'public'), ('builtin', 'builtin')}
    assert listing._installed_skills() == {'example', 'builtin'}
