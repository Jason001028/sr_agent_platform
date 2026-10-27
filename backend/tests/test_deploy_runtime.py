"""Deployment preflight and isolated update archive contract checks."""
from pathlib import Path
import shutil
import subprocess
import tarfile
from unittest.mock import patch

import pytest

from backend import check_runtime

REPO = Path(__file__).resolve().parents[2]


def test_missing_jsonschema_reports_correct_interpreter_and_install_command(capsys):
    with patch.object(check_runtime.importlib.util, 'find_spec',
                      side_effect=lambda module: None if module == 'jsonschema' else object()):
        assert check_runtime.main() == 1
    output = capsys.readouterr()
    assert 'jsonschema' in output.err
    assert check_runtime.sys.executable in output.err
    assert '-m pip install -r' in output.err
    assert 'requirements-api.txt' in output.err
    assert 'Traceback' not in output.err


def test_installed_modules_pass_preflight(capsys):
    with patch.object(check_runtime.importlib.util, 'find_spec', return_value=object()):
        assert check_runtime.main() == 0
    assert 'modules found' in capsys.readouterr().out


def bash_executable():
    bash = shutil.which('bash')
    if bash:
        return bash
    git = shutil.which('git')
    if git:
        candidate = Path(git).resolve().parents[1] / 'bin' / 'bash.exe'
        if candidate.is_file():
            return str(candidate)
    pytest.skip('Git Bash / Bash unavailable for archive integration test')


@pytest.mark.parametrize('with_manifest', [True, False])
def test_update_archive_carries_dependencies_without_machine_templates(tmp_path, with_manifest):
    # Execute the real packaging script only in a new isolated tree. Its release
    # cleanup can therefore never touch the developer's release archives.
    fixture = tmp_path / 'package-fixture'
    for directory in ('frontend/scripts', 'frontend/dist', 'backend/api', 'deploy', 'SR_code/variants'):
        (fixture / directory).mkdir(parents=True, exist_ok=True)
    script = fixture / 'frontend/scripts/package-offline.sh'
    shutil.copyfile(REPO / 'frontend/scripts/package-offline.sh', script)
    (fixture / 'frontend/dist/index.html').write_text('test', encoding='utf-8')
    (fixture / 'backend/api/app.py').write_text('# test', encoding='utf-8')
    for name in ('repair_sr.py', 'verify_sr_run.py'):
        (fixture / 'SR_code/variants' / name).write_text('# test', encoding='utf-8')
    manifest = (REPO / 'deploy/requirements-api.txt').read_bytes()
    if with_manifest:
        (fixture / 'deploy/requirements-api.txt').write_bytes(manifest)
    (fixture / 'deploy/nginx.conf').write_text('machine template', encoding='utf-8')
    (fixture / 'deploy/sr-api.service').write_text('machine template', encoding='utf-8')
    result = subprocess.run([bash_executable(), script.as_posix()], cwd=fixture,
                            capture_output=True, encoding='utf-8', errors='replace', timeout=45)
    if not with_manifest:
        assert result.returncode != 0
        assert not list(fixture.glob('release/*.tar.gz'))
        return
    assert result.returncode == 0, result.stdout + result.stderr
    packages = list((fixture / 'release').glob('backend-*.tar.gz'))
    assert len(packages) == 1
    with tarfile.open(packages[0]) as archive:
        assert archive.extractfile('backend/requirements-api.txt').read() == manifest
        assert 'jsonschema>=4.17,<4.18' in manifest.decode('utf-8')
        assert 'PyYAML' in manifest.decode('utf-8')
        names = archive.getnames()
        assert 'backend/api/app.py' in names
        assert not any(name.endswith(('nginx.conf', 'sr-api.service')) for name in names)
