#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""OpenAI 兼容接口的模型调用模块。

为什么存在：本仓库有 5 处推理（简历解析、参数推断、深度匹配、招呼语、简历改写）。
它们统一走这个模块直连任意 OpenAI 兼容端点，于是每个阶段都能单独用命令行执行，
不依赖任何特定的 AI 客户端。

三层配置优先级见 config.py：命令行 > 环境变量 > assets/llm_config.json > 内置默认。
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
