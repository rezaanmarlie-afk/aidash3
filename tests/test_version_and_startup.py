from pathlib import Path

from app.main import APP_VERSION, ROOT_DIR, health


def test_running_build_is_visible_and_health_reports_source():
    payload = health()
    assert APP_VERSION == '4.3.0'
    assert payload['version'] == '4.3.0'
    assert Path(payload['application_source']).resolve() == ROOT_DIR.resolve()
    assert Path(payload['main_file']).resolve() == (ROOT_DIR / 'app' / 'main.py').resolve()


def test_windows_start_script_always_uses_its_own_folder():
    script = (ROOT_DIR / 'start.ps1').read_text(encoding='utf-8')
    assert 'Set-Location -LiteralPath $PSScriptRoot' in script
    assert '.venv\\Scripts\\python.exe' in script
    assert 'Get-NetTCPConnection -LocalPort 8000' in script
