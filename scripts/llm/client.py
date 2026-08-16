#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""OpenAI 兼容端点的最小客户端：一问一答、拿结构化结果、并发、记账。

用 requests 而不是 openai SDK：DrissionPage 已经依赖 requests，所以这是零新增依赖；
而这里用到的全部能力就是一个 POST /chat/completions，不值得为它让使用者多装一个包。

不做流式、不做 function calling —— 被替换掉的那 5 处推理都是「一问一答出结构化结果」。
"""

import os
import json
import time
import random
import threading
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional, Sequence

from .config import LLMConfig, resolve

try:
    import requests
except ImportError:                                     # pragma: no cover
    requests = None

# 值得重试的 HTTP 状态：限流、网关抖动、上游超时。
# 其余 4xx（401 key 错、404 模型名错、400 参数错）立刻失败 —— 重试只会把同样的错
# 再犯三遍，还让人多等三轮退避。
_RETRY_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})

_MAX_BACKOFF = 30.0
_USAGE_FILE = 'llm_usage.jsonl'

# Anthropic Messages API（Claude Code 的协议）版本头，以及该协议强制要求的 max_tokens
# （OpenAI 端点不传也能跑，anthropic 不传直接 400；调用方没给时用这个保守上限）。
_ANTHROPIC_VERSION = '2023-06-01'
_ANTHROPIC_DEFAULT_MAX_TOKENS = 4096

# 记账写文件和进度打印都要跨线程，用同一把锁：并发下两个线程同时 print 会串行成
# 一行乱码，同时 append 会把两条 JSON 写到同一行。
_LOCK = threading.Lock()


class LLMError(Exception):
    """调用失败（网络、HTTP 状态、响应结构、JSON 无法解析）。"""


# ==================== 环境 ====================

def reconfigure_stdout() -> None:
    """Windows 控制台默认 GBK，中文/emoji 会抛 UnicodeEncodeError 并带崩整个脚本。

    本仓库每个入口脚本都做这件事（run_matcher.py / check_artifacts.py / …），
    这里给新脚本一个共用的实现。非标准 stdout（被重定向成非 TextIO）时静默跳过。
    """
    import sys
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError, OSError):
            pass


# ==================== JSON 提取 ====================

def strip_fence(text: str) -> str:
    """剥掉 ```json … ``` 围栏。

    提示词里已经写了「不要代码块」，但模型照样会加 —— 这是最高频的一种「输出正确
    但格式多包了一层」，靠提示词治不干净，只能在解析侧兜住。

    纯文本产物（招呼语）也需要它：前 15 个字被 ``` 占掉就等于整条预览废了，
    所以这是公开函数，不只给 extract_json 用。
    """
    s = text.strip()
    if not s.startswith('```'):
        return s
    # 去掉第一行的 ``` / ```json，再去掉结尾的 ```
    newline = s.find('\n')
    if newline == -1:
        return s
    body = s[newline + 1:]
    end = body.rfind('```')
    if end != -1:
        body = body[:end]
    return body.strip()


def _first_balanced(text: str) -> Optional[str]:
    """扫出第一段括号平衡的 JSON 值（对象或数组）。

    必须识别字符串字面量：JD 和简历文本里 `{` `}` `[` `]` 都可能出现在字符串内，
    单纯数括号会在第一个中文引号里的花括号上就切错位置。
    """
    start = -1
    opener = ''
    for i, ch in enumerate(text):
        if ch in '{[':
            start = i
            opener = ch
            break
    if start == -1:
        return None

    closer = '}' if opener == '{' else ']'
    depth = 0
    in_string = False
    escaped = False

    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == '\\':
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def extract_json(text: str) -> Any:
    """从模型输出里拿到 JSON。返回 dict 或 list。

    三层，缺一层都会在实测里翻车：
      1. 直接 loads —— 提示词生效时的正常路径。
      2. 剥 ``` 围栏 —— 模型最爱多加的一层。
      3. 括号平衡扫描 —— 前后带「好的，以下是分析结果：」这类废话时。

    三层都失败抛 LLMError，由 chat_json 决定要不要带着原始输出再问一次。
    """
    if text is None:
        raise LLMError('模型没有返回内容')
    candidates = []
    raw = text.strip()
    if raw:
        candidates.append(raw)
    stripped = strip_fence(raw)
    if stripped and stripped != raw:
        candidates.append(stripped)
    for base in (stripped or raw, raw):
        chunk = _first_balanced(base)
        if chunk and chunk not in candidates:
            candidates.append(chunk)

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except ValueError:
            continue
    preview = raw[:300].replace('\n', ' ')
    raise LLMError('输出无法解析为 JSON（前 300 字）：%s' % preview)


# ==================== 记账 ====================

def _log_usage(run_dir: Optional[str], record: Dict[str, Any]) -> None:
    """把一次调用追加进 {run_dir}/llm_usage.jsonl。

    记账失败绝不能影响主流程 —— 用量统计是附带产物，不是业务。
    """
    if not run_dir:
        return
    try:
        os.makedirs(run_dir, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False)
        with _LOCK:
            with open(os.path.join(run_dir, _USAGE_FILE), 'a', encoding='utf-8') as f:
                f.write(line + '\n')
    except (OSError, TypeError, ValueError):
        pass


def usage_summary(run_dir: str) -> Dict[str, Any]:
    """汇总 llm_usage.jsonl。没有文件返回全 0，不抛异常。"""
    path = os.path.join(run_dir, _USAGE_FILE)
    total = {'calls': 0, 'prompt_tokens': 0, 'completion_tokens': 0,
             'total_tokens': 0, 'seconds': 0.0, 'retries': 0, 'failures': 0,
             'by_stage': {}}
    if not os.path.exists(path):
        return total
    try:
        with open(path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except OSError:
        return total

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        stage = rec.get('stage') or '(未标注)'
        bucket = total['by_stage'].setdefault(
            stage, {'calls': 0, 'total_tokens': 0, 'seconds': 0.0, 'failures': 0})
        total['calls'] += 1
        bucket['calls'] += 1
        for key in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
            total[key] += int(rec.get(key) or 0)
        bucket['total_tokens'] += int(rec.get('total_tokens') or 0)
        total['seconds'] += float(rec.get('seconds') or 0)
        bucket['seconds'] += float(rec.get('seconds') or 0)
        total['retries'] += int(rec.get('retries') or 0)
        if not rec.get('ok', True):
            total['failures'] += 1
            bucket['failures'] += 1
    return total


def format_usage(run_dir: str) -> str:
    """用量汇总的人读版本。"""
    s = usage_summary(run_dir)
    if not s['calls']:
        return '（本轮没有 LLM 调用记录）'
    lines = ['LLM 用量：%d 次调用，%d tokens（入 %d / 出 %d），累计 %.1fs，重试 %d 次，失败 %d 次'
             % (s['calls'], s['total_tokens'], s['prompt_tokens'],
                s['completion_tokens'], s['seconds'], s['retries'], s['failures'])]
    for stage, b in sorted(s['by_stage'].items(), key=lambda kv: -kv[1]['total_tokens']):
        lines.append('  %-10s %3d 次  %7d tokens  %6.1fs%s'
                     % (stage, b['calls'], b['total_tokens'], b['seconds'],
                        '  失败 %d' % b['failures'] if b['failures'] else ''))
    return '\n'.join(lines)


# ==================== 调用 ====================

def _sleep_for(attempt: int, retry_after: Optional[str]) -> float:
    """退避时长。优先听服务端的 Retry-After，否则指数退避 + 抖动。

    抖动是必需的：并发 4-6 条一起撞限流时，无抖动的退避会让它们继续同步撞。
    """
    if retry_after:
        try:
            return min(float(retry_after), _MAX_BACKOFF)
        except (TypeError, ValueError):
            pass
    base = min(2.0 ** attempt, _MAX_BACKOFF)
    return base * (0.5 + random.random() * 0.5)


def _build_headers(cfg: LLMConfig) -> Dict[str, str]:
    """按协议组认证头。

    openai 认 `Authorization: Bearer`；anthropic（Claude Code 的协议）认
    `x-api-key` + `anthropic-version`，这两者混用会各打各的炸 —— 所以必须按协议二选一。
    """
    if cfg.protocol == 'anthropic':
        return {
            'x-api-key': cfg.api_key,
            'anthropic-version': _ANTHROPIC_VERSION,
            'Content-Type': 'application/json',
        }
    return {
        'Authorization': 'Bearer %s' % cfg.api_key,
        'Content-Type': 'application/json',
    }


def _post(cfg: LLMConfig, payload: Dict[str, Any]) -> Dict[str, Any]:
    """发一次请求，返回解析后的 JSON body。可重试的失败抛 _Retryable。"""
    if requests is None:
        raise LLMError('requests 未安装：pip install requests')
    headers = _build_headers(cfg)
    try:
        resp = requests.post(cfg.endpoint(), headers=headers, json=payload,
                             timeout=cfg.timeout)
    except requests.exceptions.Timeout as exc:
        raise _Retryable('请求超时（%ss）：%s' % (cfg.timeout, exc), None)
    except requests.exceptions.ConnectionError as exc:
        raise _Retryable('连不上 %s：%s' % (cfg.endpoint(), exc), None)
    except requests.exceptions.RequestException as exc:
        raise LLMError('请求失败：%s' % exc)

    if resp.status_code in _RETRY_STATUS:
        raise _Retryable('HTTP %d：%s' % (resp.status_code, resp.text[:200]),
                         resp.headers.get('Retry-After'))
    if resp.status_code >= 400:
        raise LLMError('HTTP %d（不重试）：%s\n  endpoint=%s model=%s'
                       % (resp.status_code, resp.text[:400], cfg.endpoint(), cfg.model))
    try:
        return resp.json()
    except ValueError:
        raise _Retryable('响应不是 JSON：%s' % resp.text[:200], None)


class _Retryable(Exception):
    def __init__(self, message: str, retry_after: Optional[str]):
        super().__init__(message)
        self.retry_after = retry_after


def _content_of(cfg: LLMConfig, body: Dict[str, Any]) -> str:
    """从响应里取正文。

    openai 从 `choices[0].message.content` 取；anthropic（Claude Code 的协议）把正文
    放在 `content` 块数组的 `{"type":"text"}` 里，逐块拼接。

    空 content 视为可重试：推理模型偶尔只填 reasoning_content 就返回，
    而空正文对调用方来说和失败没区别。
    """
    if cfg.protocol == 'anthropic':
        blocks = body.get('content')
        if not isinstance(blocks, list):
            raise _Retryable('响应里没有 content 块：%s'
                             % json.dumps(body, ensure_ascii=False)[:200], None)
        text = ''.join(
            (b.get('text') or '') for b in blocks
            if isinstance(b, dict) and b.get('type') == 'text'
        )
        if not text.strip():
            raise _Retryable('模型返回了空正文（stop_reason=%s）'
                             % body.get('stop_reason'), None)
        return text

    choices = body.get('choices')
    if not isinstance(choices, list) or not choices:
        raise _Retryable('响应里没有 choices：%s' % json.dumps(body, ensure_ascii=False)[:200], None)
    message = choices[0].get('message') or {}
    content = message.get('content')
    if content is None or str(content).strip() == '':
        raise _Retryable('模型返回了空正文（finish_reason=%s）'
                         % choices[0].get('finish_reason'), None)
    return str(content)


def _build_messages(prompt: str, system: Optional[str],
                    messages: Optional[Sequence[Dict[str, str]]]) -> List[Dict[str, str]]:
    if messages is not None:
        return list(messages)
    out: List[Dict[str, str]] = []
    if system:
        out.append({'role': 'system', 'content': system})
    out.append({'role': 'user', 'content': prompt})
    return out


def _build_payload(cfg: LLMConfig, messages: List[Dict[str, str]], *,
                   want_json: bool, max_tokens: Optional[int]) -> Dict[str, Any]:
    """按协议组请求体。

    anthropic 与 openai 的差异都在这里：system 提到顶层、content 用块数组、
    max_tokens 强制必填、没有 response_format 字段（JSON 靠 extract_json 兜住）。
    """
    if cfg.protocol == 'anthropic':
        system = '\n\n'.join(
            str(m.get('content')) for m in messages if m.get('role') == 'system')
        msgs = [m for m in messages if m.get('role') != 'system']
        payload: Dict[str, Any] = {
            'model': cfg.model,
            'max_tokens': max_tokens or _ANTHROPIC_DEFAULT_MAX_TOKENS,
            'messages': [
                {'role': m['role'],
                 'content': [{'type': 'text', 'text': str(m.get('content'))}]}
                for m in msgs
            ],
        }
        if cfg.temperature is not None:
            payload['temperature'] = cfg.temperature
        if system:
            payload['system'] = system
        return payload

    payload: Dict[str, Any] = {
        'model': cfg.model,
        'messages': messages,
        'temperature': cfg.temperature,
    }
    if max_tokens:
        payload['max_tokens'] = max_tokens
    if want_json and cfg.json_mode:
        # 不是所有兼容端点都支持这个字段；支持的话省事，不支持的话由 extract_json 兜住。
        payload['response_format'] = {'type': 'json_object'}
    return payload


def _usage_of(usage: Dict[str, Any], protocol: str) -> Dict[str, Any]:
    """把不同协议的用量字段统一到 prompt/completion/total。

    anthropic 返回 input_tokens/output_tokens（另有 cache_* 分量，算在 input 里），
    openai 返回 prompt_tokens/completion_tokens/total_tokens。记账格式只有一套，
    所以在这里归一。
    """
    if protocol == 'anthropic':
        inp = usage.get('input_tokens') or 0
        out = usage.get('output_tokens') or 0
        return {'prompt_tokens': inp, 'completion_tokens': out,
                'total_tokens': inp + out}
    return usage


def _call(cfg: LLMConfig, messages: List[Dict[str, str]], *,
          want_json: bool, max_tokens: Optional[int],
          run_dir: Optional[str], stage: str) -> str:
    """带重试的一次逻辑调用，返回正文。用量无论成败都记一条。"""
    cfg.require_key()
    payload = _build_payload(cfg, messages, want_json=want_json, max_tokens=max_tokens)

    started = time.time()
    retries = 0
    last = ''
    for attempt in range(cfg.max_retries + 1):
        try:
            body = _post(cfg, payload)
            content = _content_of(cfg, body)
        except _Retryable as exc:
            last = str(exc)
            if attempt >= cfg.max_retries:
                break
            retries += 1
            time.sleep(_sleep_for(attempt, exc.retry_after))
            continue
        except LLMError as exc:
            _log_usage(run_dir, {'stage': stage, 'model': cfg.model, 'ok': False,
                                 'seconds': round(time.time() - started, 2),
                                 'retries': retries, 'error': str(exc)[:300]})
            raise

        usage = _usage_of(body.get('usage') or {}, cfg.protocol)
        _log_usage(run_dir, {
            'stage': stage, 'model': cfg.model, 'ok': True,
            'prompt_tokens': usage.get('prompt_tokens'),
            'completion_tokens': usage.get('completion_tokens'),
            'total_tokens': usage.get('total_tokens'),
            'seconds': round(time.time() - started, 2),
            'retries': retries,
        })
        return content

    _log_usage(run_dir, {'stage': stage, 'model': cfg.model, 'ok': False,
                         'seconds': round(time.time() - started, 2),
                         'retries': retries, 'error': last[:300]})
    raise LLMError('重试 %d 次后仍失败：%s' % (retries, last))


def chat(prompt: str = '', *, system: Optional[str] = None,
         messages: Optional[Sequence[Dict[str, str]]] = None,
         stage: Optional[str] = None, run_dir: Optional[str] = None,
         cfg: Optional[LLMConfig] = None, max_tokens: Optional[int] = None,
         **overrides: Any) -> str:
    """一问一答，返回纯文本正文。"""
    conf = (cfg or resolve(stage=stage)).merged(**overrides)
    msgs = _build_messages(prompt, system, messages)
    return _call(conf, msgs, want_json=False, max_tokens=max_tokens,
                 run_dir=run_dir, stage=stage or conf.stage or 'chat')


def chat_json(prompt: str = '', *, system: Optional[str] = None,
              messages: Optional[Sequence[Dict[str, str]]] = None,
              stage: Optional[str] = None, run_dir: Optional[str] = None,
              cfg: Optional[LLMConfig] = None, max_tokens: Optional[int] = None,
              **overrides: Any) -> Any:
    """一问一答，返回解析好的 JSON（dict 或 list）。

    第一次解析失败时**带着原始输出**再问一次，而不是原样重发：模型看得见自己
    上一次输出了什么，纠正率远高于把同一个提示词再发一遍。只重问一次 —— 两次都
    解析不出来的多半是提示词或模型能力问题，继续烧钱没意义。
    """
    conf = (cfg or resolve(stage=stage)).merged(**overrides)
    label = stage or conf.stage or 'chat_json'
    msgs = _build_messages(prompt, system, messages)

    raw = _call(conf, msgs, want_json=True, max_tokens=max_tokens,
                run_dir=run_dir, stage=label)
    try:
        return extract_json(raw)
    except LLMError as first:
        retry_msgs = msgs + [
            {'role': 'assistant', 'content': raw[:4000]},
            {'role': 'user', 'content':
                '上面的输出无法被解析为 JSON。请只输出合法的 JSON 本身：'
                '不要任何解释文字，不要 Markdown 代码块围栏，不要在 JSON 前后加内容。'},
        ]
        raw2 = _call(conf, retry_msgs, want_json=True, max_tokens=max_tokens,
                     run_dir=run_dir, stage=label + ':json-retry')
        try:
            return extract_json(raw2)
        except LLMError as second:
            raise LLMError('两次都拿不到合法 JSON。\n  第一次：%s\n  第二次：%s'
                           % (first, second))


# ==================== 并发 ====================

@dataclass
class Outcome:
    """一个并发任务的结果。失败也是一种结果，不是异常。"""
    index: int
    item: Any
    ok: bool
    value: Any = None
    error: str = ''


def map_concurrent(items: Sequence[Any], fn: Callable[[Any], Any], *,
                   workers: int = 4,
                   label: Optional[Callable[[Any, int], str]] = None,
                   quiet: bool = False) -> List[Outcome]:
    """并发跑 fn(item)，返回与输入等长、按原顺序排列的 Outcome 列表。

    **单条失败不带崩全批**：一个岗位分析失败不该让另外 14 个白跑。调用方按
    `ok` 分流 —— 下游（merge_deep_results / check_artifacts）本来就有「这条缺失」
    的兜底路径，能用上的前提是这批别整体炸掉。
    """
    total = len(items)
    results: List[Optional[Outcome]] = [None] * total
    if total == 0:
        return []
    workers = max(1, min(workers, total))
    done = [0]

    def _run(index: int, item: Any) -> Outcome:
        try:
            return Outcome(index=index, item=item, ok=True, value=fn(item))
        except Exception as exc:                     # 包括 LLMError 和任何解析异常
            return Outcome(index=index, item=item, ok=False,
                           error='%s: %s' % (type(exc).__name__, exc))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_run, i, item) for i, item in enumerate(items)]
        for future in as_completed(futures):
            outcome = future.result()
            results[outcome.index] = outcome
            if quiet:
                continue
            with _LOCK:
                done[0] += 1
                name = label(outcome.item, outcome.index) if label else '#%d' % (outcome.index + 1)
                mark = '✅' if outcome.ok else '❌'
                tail = '' if outcome.ok else '  %s' % outcome.error[:160]
                print('  [%d/%d] %s %s%s' % (done[0], total, mark, name, tail), flush=True)

    return [r for r in results if r is not None]
