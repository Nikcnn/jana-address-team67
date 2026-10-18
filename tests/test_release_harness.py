"""Release harness must never reuse the working public demo's project or port."""
import importlib.util
from pathlib import Path
import subprocess
from urllib.parse import urlparse


def test_benchmark_commands_are_isolated_and_use_current_image(monkeypatch):
    monkeypatch.setattr('sys.argv', ['release_acceptance.py'])
    path = Path(__file__).resolve().parents[1] / 'scripts/release/release_acceptance.py'
    spec = importlib.util.spec_from_file_location('isolated_release_harness', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    commands = []
    def fake_run(command, **kwargs):
        commands.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, '', '')
    monkeypatch.setattr(module.subprocess, 'run', fake_run)
    module.sh(module.COMPOSE + ' up -d --no-build --pull never', check=True)
    assert '-p jana67-benchmark-' in commands[0][0]
    assert 'compose.measure.yaml' not in commands[0][0]
    assert urlparse(module.BASE).port != 8010
    assert module.IMAGE == 'jana-address:team67-rc3-final'
    assert module.OUT.is_relative_to(module.ROOT)
