from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    app_name: str = 'ASOC PI Readiness & Manager Sign-Off'
    app_secret: str = 'development-secret-change-me'
    app_admin_username: str = 'admin'
    app_admin_password: str = 'ChangeMe123!'
    app_manager_name: str = 'Manager'
    app_manager_email: str = ''
    data_dir: str = './data'

    mock_mode: bool = True
    jira_base_url: str = ''
    jira_api_version: int = 3
    jira_auth_mode: str = 'basic'
    jira_username: str = ''
    jira_api_token: str = ''
    jira_verify_ssl: bool = True
    jira_timeout_seconds: int = 30
    jira_scan_max_results: int = 2000
    jira_scan_batch_size: int = 50
    scan_cache_seconds: int = 180

    jira_project: str = 'NMGOS'
    default_pi_value: str = 'PI26'
    default_scrum_master_id: str = '70121:c296bec5-b136-48b7-9345-a1e16f9f38dc'
    default_scrum_master_name: str = ''
    default_priority: str = 'Critical'
    manager_signoff_deadline: str = '2026-07-07T16:00:00+02:00'

    enable_jira_writeback: bool = False
    signoff_label_prefix: str = 'manager-signoff'

    @property
    def data_path(self) -> Path:
        path = Path(self.data_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path


settings = Settings()
