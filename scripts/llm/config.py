#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""LLM 配置解析：CLI 参数 > 环境变量 > assets/llm_config.json > 内置默认。

三层而不是一层，是因为这三层各解决一个不同的问题：
  - 配置文件：一次写好，长期复用，还能给不同阶段配不同模型（便宜模型跑解析，
    强模型跑深度分析）。
  - 环境变量：CI / 临时切换 key，且不落盘。
  - CLI 参数：单次实验（`--model xxx` 试一个新模型），不污染前两层。

每个字段的最终来源都记在 `LLMConfig.source` 里，`llm_check.py` 会打印出来 ——
「我明明配了却没生效」是这类模块最常见的困惑，让来源可见比写文档有用。
"""

import os
import json
from dataclasses import dataclass, field, replace
from typing import Any, Dict, Optional
from urllib.parse import urlparse

_SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_PATH = os.path.join(_SKILL_ROOT, 'assets', 'llm_config.json')
EXAMPLE_PATH = os.path.join(_SKILL_ROOT, 'assets', 'llm_config.example.json')

# 环境变量：先认 LLM_* ，再回落到 OPENAI_* 与 ANTHROPIC_* 。
# 认 OPENAI_*/ANTHROPIC_* 是因为使用者大概率已经为别的工具设过它们（Claude Code 就用
# ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN / ANTHROPIC_MODEL），不该逼人再设一遍。
_ENV_ALIASES = {
    'base_url':    ('LLM_BASE_URL', 'OPENAI_BASE_URL', 'OPENAI_API_BASE', 'ANTHROPIC_BASE_URL'),
    'api_key':     ('LLM_API_KEY', 'OPENAI_API_KEY', 'ANTHROPIC_AUTH_TOKEN'),
    'model':       ('LLM_MODEL', 'OPENAI_MODEL', 'ANTHROPIC_MODEL'),
    'protocol':    ('LLM_PROTOCOL',),
    'timeout':     ('LLM_TIMEOUT',),
    'max_retries': ('LLM_MAX_RETRIES',),
    'concurrency': ('LLM_CONCURRENCY',),
    'temperature': ('LLM_TEMPERATURE',),
    'json_mode':   ('LLM_JSON_MODE',),
}

# 支持的协议。空串或非法值会让 resolve 回落到按 base_url 自动判断。
_PROTOCOLS = ('openai', 'anthropic')

_INT_FIELDS = ('timeout', 'max_retries', 'concurrency')
_FLOAT_FIELDS = ('temperature',)
_BOOL_FIELDS = ('json_mode',)

# 允许出现在配置文件里的键。白名单之外的一律忽略并告警 —— 与 preferences.py 同样的
# 理由：这是用户能手改的文件，静默透传未知键等于给自己开一个没人审过的开关。
_ALLOWED = set(_ENV_ALIASES) | {'stages'}


class ConfigError(Exception):
    """配置缺失或非法。消息里带上怎么修，不要只说「缺 api_key」。"""


@dataclass
class LLMConfig:
    protocol: str = 'openai'
    base_url: str = 'https://api.openai.com/v1'
    api_key: str = ''
    model: str = 'gpt-4o-mini'
    timeout: int = 120
    max_retries: int = 3
    concurrency: int = 4
    temperature: float = 0.3
    json_mode: bool = True
    stage: str = ''
    source: Dict[str, str] = field(default_factory=dict)

    def require_key(self) -> 'LLMConfig':
        """没有 api_key 就抛错，并把三种配置方式打印出来。"""
        if not self.api_key:
            raise ConfigError(_missing_key_message())
        return self

    def endpoint(self) -> str:
        return chat_endpoint(self.base_url, self.protocol)

    def merged(self, **overrides: Any) -> 'LLMConfig':
        """派生一份带覆盖的副本（None 值视为「没传」，不覆盖）。"""
        clean = {k: v for k, v in overrides.items()
                 if v is not None and k in _ENV_ALIASES}
        if not clean:
            return self
        src = dict(self.source)
        for k in clean:
            src[k] = 'call'
        return replace(self, source=src, **clean)


def mask_key(key: str) -> str:
    """打码 api_key。全长度都不暴露：只留前 3 后 4，中间固定省略号。"""
    if not key:
        return '(未设置)'
    if len(key) <= 8:
        return key[:2] + '***'
    return '%s...%s' % (key[:3], key[-4:])


def _detect_protocol(base_url: str) -> str:
    """按 base_url 猜协议：host 含 anthropic 或路径以 /messages 结尾 → anthropic。

    只在用户没显式配 protocol 时调用。Claude Code 的协议就是 Anthropic Messages API
    （POST /v1/messages），所以 base_url 指向 api.anthropic.com 或某个 /messages 网关时，
    自动切到 anthropic 能省掉一次显式配置。
    """
    low = ((base_url or '').strip().rstrip('/')).lower()
    if not low:
        return 'openai'
    if low.endswith('/messages') or 'anthropic' in low:
        return 'anthropic'
    return 'openai'


def chat_endpoint(base_url: str, protocol: str = 'openai') -> str:
    """base_url → 协议对应的完整地址。

    openai   → …/chat/completions
    anthropic → …/v1/messages （Claude Code 的协议）

    不无脑追加 `/v1`：火山方舟是 `/api/v3`、Azure 是 `/openai/deployments/...`，
    追加会直接打不通。只在 base_url 是**裸域名**（完全没有路径）时补 `/v1`，
    因为那种写法（`https://api.deepseek.com`）几乎总是漏了版本段。
    """
    b = (base_url or '').strip().rstrip('/')
    if not b:
        raise ConfigError('base_url 为空。' + _missing_key_message())
    if protocol == 'anthropic':
        if b.endswith('/messages'):
            return b
        if not urlparse(b).path.strip('/'):
            b += '/v1'
        if b.endswith('/v1'):
            return b + '/messages'
        return b + '/v1/messages'
    if b.endswith('/chat/completions'):
        return b
    if not urlparse(b).path.strip('/'):
        b += '/v1'
    return b + '/chat/completions'


def _coerce(key: str, value: Any) -> Any:
    """把字符串来源（环境变量）的值转成字段该有的类型。"""
    if value is None:
        return None
    if key in _INT_FIELDS:
        return int(str(value).strip())
    if key in _FLOAT_FIELDS:
        return float(str(value).strip())
    if key in _BOOL_FIELDS:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ('1', 'true', 'yes', 'on')
    return str(value).strip()


def _load_file(path: str) -> Dict[str, Any]:
    """读配置文件。不存在返回 {}；语法坏掉抛 ConfigError（静默忽略会让人查半天）。"""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except ValueError as exc:
        raise ConfigError('配置文件 JSON 语法错误：%s\n  %s' % (path, exc))
    except OSError as exc:
        raise ConfigError('配置文件读不出来：%s\n  %s' % (path, exc))
    if not isinstance(data, dict):
        raise ConfigError('配置文件顶层必须是 JSON 对象：%s' % path)
    return data


def _apply(cfg_kwargs: Dict[str, Any], source: Dict[str, str],
           data: Dict[str, Any], origin: str, warn: list) -> None:
    """把一层配置盖到累积结果上，并记下来源。"""
    for key, value in data.items():
        if key == 'stages':
            continue
        if key not in _ENV_ALIASES:
            if key in _ALLOWED:
                continue
            warn.append('%s 中的未知键 `%s` 已忽略' % (origin, key))
            continue
        try:
            coerced = _coerce(key, value)
        except (TypeError, ValueError):
            warn.append('%s 中 `%s` 的值 %r 类型不对，已忽略' % (origin, key, value))
            continue
        if coerced is None or coerced == '':
            continue
        cfg_kwargs[key] = coerced
        source[key] = origin


def resolve(stage: Optional[str] = None, config_path: Optional[str] = None,
            env: Optional[Dict[str, str]] = None,
            warnings_out: Optional[list] = None,
            **overrides: Any) -> LLMConfig:
    """解析出最终配置。

    Args:
        stage: 阶段名（`parse` / `infer` / `deep` / `greeting` / `resume`）。
               配置文件里 `stages.<stage>` 的字段会盖在顶层字段之上。
        config_path: 配置文件路径，默认 assets/llm_config.json。测试用它顶掉真实配置。
        env: 环境变量字典，默认 os.environ。测试用它避免污染进程环境。
        warnings_out: 传一个 list 进来接收告警（未知键、类型不对）。
        **overrides: CLI 层覆盖，值为 None 的键视为「没传」。

    优先级（后者盖前者）：内置默认 → 文件顶层 → 文件 stages.<stage> → 环境变量 → overrides。
    """
    if env is None:
        env = os.environ
    if config_path is None:
        config_path = CONFIG_PATH
    warn = warnings_out if warnings_out is not None else []

    kwargs: Dict[str, Any] = {}
    source: Dict[str, str] = {k: 'default' for k in _ENV_ALIASES}

    # ── 第 1 层：配置文件顶层 ──
    data = _load_file(config_path)
    _apply(kwargs, source, data, 'file', warn)

    # ── 第 2 层：配置文件的 stages.<stage> ──
    stages = data.get('stages') or {}
    if stage and isinstance(stages, dict):
        stage_data = stages.get(stage)
        if isinstance(stage_data, dict):
            _apply(kwargs, source, stage_data, 'file:stages.%s' % stage, warn)
        elif stage_data is not None:
            warn.append('配置文件 stages.%s 不是对象，已忽略' % stage)

    # ── 第 3 层：环境变量 ──
    for key, names in _ENV_ALIASES.items():
        for name in names:
            raw = env.get(name)
            if raw is None or str(raw).strip() == '':
                continue
            try:
                kwargs[key] = _coerce(key, raw)
            except (TypeError, ValueError):
                warn.append('环境变量 %s 的值 %r 类型不对，已忽略' % (name, raw))
                break
            source[key] = 'env:%s' % name
            break

    # ── 第 4 层：CLI 覆盖 ──
    for key, value in overrides.items():
        if value is None or key not in _ENV_ALIASES:
            continue
        try:
            kwargs[key] = _coerce(key, value)
        except (TypeError, ValueError):
            warn.append('命令行 --%s 的值 %r 类型不对，已忽略' % (key.replace('_', '-'), value))
            continue
        source[key] = 'cli'

    # ── 协议：显式给了且合法就用；非法或没给，按 base_url 自动判断 ──
    proto = kwargs.get('protocol')
    if proto is not None and proto not in _PROTOCOLS:
        warn.append('协议 `%s` 不合法（可选 %s），改为按 base_url 自动判断'
                    % (proto, ' / '.join(_PROTOCOLS)))
        proto = None
    if proto is None:
        base = kwargs.get('base_url') or LLMConfig.base_url
        kwargs['protocol'] = _detect_protocol(base)
        source['protocol'] = 'auto'

    cfg = LLMConfig(stage=stage or '', source=source, **kwargs)
    if cfg.concurrency < 1:
        cfg = replace(cfg, concurrency=1)
    if cfg.max_retries < 0:
        cfg = replace(cfg, max_retries=0)
    return cfg


def describe(cfg: LLMConfig) -> str:
    """人读的配置摘要。api_key 只出现打码后的形式。"""
    rows = [
        ('protocol', cfg.protocol),
        ('base_url', cfg.base_url),
        ('endpoint', chat_endpoint(cfg.base_url, cfg.protocol) if cfg.base_url else '(无)'),
        ('model', cfg.model),
        ('api_key', mask_key(cfg.api_key)),
        ('timeout', '%ss' % cfg.timeout),
        ('max_retries', cfg.max_retries),
        ('concurrency', cfg.concurrency),
        ('temperature', cfg.temperature),
        ('json_mode', cfg.json_mode),
    ]
    width = max(len(k) for k, _ in rows)
    lines = []
    for key, value in rows:
        origin = cfg.source.get(key, '')
        suffix = '   ← %s' % origin if origin and origin != 'default' else ''
        lines.append('  %-*s : %s%s' % (width, key, value, suffix))
    head = '阶段: %s' % (cfg.stage or '(默认)')
    return head + '\n' + '\n'.join(lines)


def _missing_key_message() -> str:
    return (
        '缺少 api_key（或 base_url）。三种配法任选一种：\n'
        '  1) 配置文件（推荐，可长期复用）：\n'
        '       复制 %s\n'
        '       为        %s\n'
        '       填入 base_url / api_key / model\n'
        '  2) 环境变量：\n'
        '       PowerShell: $env:LLM_API_KEY="sk-..."; $env:LLM_BASE_URL="https://..."\n'
        '       Bash:       export LLM_API_KEY=sk-... LLM_BASE_URL=https://...\n'
        '       （也认 OPENAI_API_KEY / OPENAI_BASE_URL）\n'
        '       用 Claude Code 的协议时认 ANTHROPIC_AUTH_TOKEN / ANTHROPIC_BASE_URL /\n'
        '       ANTHROPIC_MODEL，base_url 指向 api.anthropic.com 会自动识别协议\n'
        '  3) 命令行：--api-key sk-... --base-url https://... --model xxx --protocol openai\n'
        '配好后用 `python scripts/utils/llm_check.py` 验证连通。'
        % (os.path.relpath(EXAMPLE_PATH, _SKILL_ROOT),
           os.path.relpath(CONFIG_PATH, _SKILL_ROOT))
    )
