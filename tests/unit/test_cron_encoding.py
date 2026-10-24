import json

from corki.cron.diagnostics import safe_log
from corki.cron.models import validate_model
from corki.cron.tools import CronTool


def test_cron_reads_utf8_provider_names_and_redacts_configured_values(tmp_path):
    credential = "仅用于测试的凭据值"
    settings = {
        "default": {"provider": "中文供应商", "model": "模型示例"},
        "providers": {
            "中文供应商": {
                "api_key": credential,
                "base_url": "https://example.invalid",
                "models": {"模型示例": {}},
            }
        },
    }
    (tmp_path / "settings.json").write_text(
        json.dumps(settings, ensure_ascii=False), encoding="utf-8"
    )
    assert validate_model(tmp_path, "中文供应商", "模型示例") == ("中文供应商", "模型示例")
    log = safe_log(tmp_path, "Example failure: " + credential)
    assert credential not in log and "[已隐藏]" in log
    tool = CronTool(tmp_path)
    result = tool.invoke(
        {
            "action": "create",
            "values": {
                "name": "中文任务",
                "prompt": "测试",
                "schedule": "every 2h",
                "timezone": "UTC",
            },
        },
        tmp_path,
    )
    task = next(job for job in tool.store.list()["jobs"] if job["id"] == result["id"])
    assert task["provider"] == "中文供应商" and task["model"] == "模型示例"
