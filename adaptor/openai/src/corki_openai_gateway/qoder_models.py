"""Static public model names and wire IDs from Qoder IDE's assistant catalog.

Only normal assistant routes are included, not Quest/Experts-specific workflows.
This contains model metadata only, never account IDs, policies or login state.
"""

MODELS = (
    ("auto", "auto", "Qoder 自动选择模型", 200000),
    ("ultimate", "ultimate", "Qoder Ultimate 自动路由", 200000),
    ("fast", "fast", "Qoder Fast 快速路由", 200000),
    ("efficient", "efficient", "Qoder Efficient 路由", 200000),
    ("Sonus", "smodel", "Sonus，通过 Qoder 套餐调用", 180000),
    ("Qwen3.8-Max", "qmodel_38max", "Qwen3.8-Max，通过 Qoder 套餐调用", 180000),
    ("Qwen3.8-Flash", "qfmodel", "Qwen3.8-Flash，通过 Qoder 套餐调用", 180000),
    ("Kimi-K3.1-Pro", "kmodel_latest", "Kimi-K3.1-Pro，通过 Qoder 套餐调用", 180000),
    ("Kimi-K2.8-Preview", "kmodel", "Kimi-K2.8-Preview，通过 Qoder 套餐调用", 200000),
    ("GLM-5.3", "gmodel", "GLM-5.3，通过 Qoder 套餐调用", 180000),
    ("GLM-5.3-Flash", "gfmodel", "GLM-5.3-Flash，通过 Qoder 套餐调用", 200000),
    ("DeepSeek-V4-Pro", "dmodel", "DeepSeek-V4-Pro，通过 Qoder 套餐调用", 200000),
    ("DeepSeek-Flash", "dfmodel", "DeepSeek-Flash，通过 Qoder 套餐调用", 200000),
    ("MiniMax-M3.1-Flash", "mmodel", "MiniMax-M3.1-Flash，通过 Qoder 套餐调用", 180000),
)
_ALIASES = {name.casefold(): identity for name, identity, _, _ in MODELS}
_ALIASES.update({identity.casefold(): identity for _, identity, _, _ in MODELS})


# IDE-only aliases are recognized for explicit requests, but are not offered by
# default until the model-server transport accepts them. Do not silently route
# an unavailable selection to a different model.
AVAILABLE = frozenset({"auto", "ultimate", "kmodel", "gmodel", "dmodel"})


def model_catalog():
    return {
        name: {"description": description, "context_window": window}
        for name, identity, description, window in MODELS
        if identity in AVAILABLE
    }


def model_id(name):
    return _ALIASES.get(name.casefold(), name)
