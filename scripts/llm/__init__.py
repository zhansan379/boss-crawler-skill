#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""OpenAI 兼容接口的模型调用模块。

为什么存在：本仓库原本把 5 处推理（简历解析、参数推断、深度匹配、招呼语、简历改写）
交给 Claude Code 的 subagent 做，因此只能在 skill 里跑。这个模块把那 5 处换成直连
任意 OpenAI 兼容端点，于是每个阶段都能单独用命令行执行。

skill 路径（SKILL.md）和 CLI 路径并存，互不依赖：前者的推理走 Claude Code 订阅，
后者走你自己的 api_key。
"""

from .config import (
    LLMConfig,
    ConfigError,
    resolve,
    chat_endpoint,
    mask_key,
    describe,
    CONFIG_PATH,
    EXAMPLE_PATH,
)
from .client import (
    LLMError,
    chat,
    chat_json,
    extract_json,
    strip_fence,
    map_concurrent,
    Outcome,
    usage_summary,
    format_usage,
    reconfigure_stdout,
)

__all__ = [
    'LLMConfig', 'ConfigError', 'resolve', 'chat_endpoint', 'mask_key', 'describe',
    'CONFIG_PATH', 'EXAMPLE_PATH',
    'LLMError', 'chat', 'chat_json', 'extract_json', 'strip_fence',
    'map_concurrent', 'Outcome', 'usage_summary', 'format_usage', 'reconfigure_stdout',
]
