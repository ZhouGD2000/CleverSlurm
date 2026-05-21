def test_feishu_config_accepts_direct_values_in_env_fields(isolated_home):
    from cslurm.config import feishu_secret, feishu_webhook_url

    (isolated_home / "config.toml").write_text(
        """
[notification.feishu]
webhook_url_env = "https://open.feishu.cn/open-apis/bot/v2/hook/test"
secret_env = "direct-secret-value"
"""
    )

    assert feishu_webhook_url() == "https://open.feishu.cn/open-apis/bot/v2/hook/test"
    assert feishu_secret() == "direct-secret-value"


def test_root_dir_ignores_legacy_ai_slurm_root(tmp_path, monkeypatch):
    from cslurm.config import root_dir

    home = tmp_path / "home"
    home.mkdir()
    (home / ".ai-slurm").mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CSLURM_ROOT", raising=False)
    monkeypatch.setenv("AI_SLURM_ROOT", str(tmp_path / "legacy-root"))

    assert root_dir() == home / ".cslurm"


def test_ai_format_ignores_legacy_provider_key(isolated_home, monkeypatch):
    from cslurm.config import ai_format

    monkeypatch.delenv("CSLURM_AI_FORMAT", raising=False)
    (isolated_home / "config.toml").write_text(
        """
[ai]
provider = "anthropic"
"""
    )

    assert ai_format() == "openai"
