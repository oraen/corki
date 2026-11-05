"""Bundled defaults for the two supported Codex model generations.

Metadata only: no account configuration, credentials or model requests.
Existing provider settings are merged by the manager, never replaced here.
"""

_MODELS = (
    ("gpt-6.1-sol", 1050000, False,
     "兼顾能力、速度与成本，适合复杂编码、专业分析和多步骤任务。"),
    ("gpt-6-astra", 872000, True,
     "面向高难度任务的旗舰模型，适合复杂推理、代码开发与多步骤项目。"),
    ("gpt-6-sol", 872000, True,
     "通用主力模型，适合日常开发、问题分析与持续的多步骤工作。"),
    ("gpt-6-luna", 872000, False,
     "轻量快速模型，适合明确的小任务、日常问答与批量处理。"),
)


def model_catalog():
    return {
        name: {
            "description": description,
            "max_context_window": maximum,
            "context_window": min(512 * 1024, maximum),
            "reasoning_effort": "medium",
            "supported_reasoning_levels": [
                {"effort": effort}
                for effort in ["low", "medium", "high", "xhigh", "max"]
                + (["ultra"] if ultra else [])
            ],
        }
        for name, maximum, ultra, description in _MODELS
    }
