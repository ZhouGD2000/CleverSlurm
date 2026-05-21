def test_feishu_config_accepts_direct_values_in_env_fields(isolated_home):
    from ai_slurm.config import feishu_secret, feishu_webhook_url

    (isolated_home / "config.toml").write_text(
        """
[notification.feishu]
webhook_url_env = "https://open.feishu.cn/open-apis/bot/v2/hook/test"
secret_env = "direct-secret-value"
"""
    )

    assert feishu_webhook_url() == "https://open.feishu.cn/open-apis/bot/v2/hook/test"
    assert feishu_secret() == "direct-secret-value"
