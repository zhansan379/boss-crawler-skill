#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""LLM 配置诊断：打印解析到的配置（key 打码），可选发一条最小请求验连通。

配置错误是这套 CLI 最高频的失败方式，而且报错点往往在流水线中段（跑完爬取才发现
key 没配）。所以给它一个一秒钟的独立入口，跑长流程之前先跑这个。

用法：
    python scripts/utils/llm_check.py                 # 打印配置 + 发一条最小请求
    python scripts/utils/llm_check.py --no-call       # 只打印配置，不花钱
    python scripts/utils/llm_check.py --stage deep    # 看某阶段的实际生效配置
    python scripts/utils/llm_check.py --json          # 也验一遍 JSON 模式能否工作

协议回退：请求打 404（多半是协议猜错，base_url 指向 Claude Code 网关却按 openai 算）
时，自动用另一协议再试一次；哪个通就提示应配 protocol=xxx。自查代价是那次 404
多一次请求。

退出码：0 = 可用，1 = 配置缺失/非法，2 = 配置齐全但请求打不通。
"""

import os
import sys
import argparse

_SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _SCRIPTS)

from llm import (
    ConfigError, LLMError, resolve, describe, chat, chat_json,
    reconfigure_stdout, CONFIG_PATH,
)

# 各阶段名与用到它们的脚本，打印出来方便按阶段配模型
STAGES = [
    ('parse', 'parse_resume.py  简历解析'),
    ('infer', 'infer_params.py  爬取参数推断'),
    ('deep', 'deep_analyze.py  岗位深度匹配'),
    ('greeting', 'gen_materials.py 招呼语'),
    ('resume', 'gen_materials.py 简历改写'),
]


def main() -> int:
    reconfigure_stdout()

    ap = argparse.ArgumentParser(description='检查 LLM 配置与连通性')
    ap.add_argument('--stage', help='只看某个阶段的生效配置（parse/infer/deep/greeting/resume）')
    ap.add_argument('--no-call', action='store_true', help='只打印配置，不发请求')
    ap.add_argument('--json', dest='check_json', action='store_true',
                    help='额外验一遍 JSON 模式（chat_json）能否拿到合法 JSON')
    ap.add_argument('--base-url')
    ap.add_argument('--api-key')
    ap.add_argument('--model')
    ap.add_argument('--protocol', choices=['openai', 'anthropic'],
                    help='协议：openai（/chat/completions）或 anthropic（/v1/messages，Claude Code 的协议）。'
                         '不传则按 base_url 自动判断')
    args = ap.parse_args()

    overrides = {'base_url': args.base_url, 'api_key': args.api_key,
                 'model': args.model, 'protocol': args.protocol}
    warnings: list = []

    try:
        cfg = resolve(stage=args.stage, warnings_out=warnings, **overrides)
    except ConfigError as exc:
        print('❌ 配置错误：\n%s' % exc)
        return 1

    print('配置文件：%s%s' % (CONFIG_PATH, '' if _exists(CONFIG_PATH) else '  （不存在，仅用环境变量/默认值）'))
    print()
    print(describe(cfg))

    for warning in warnings:
        print('  ⚠ %s' % warning)

    # 各阶段实际生效的模型 —— 「我配了 stages.deep 但没生效」靠这张表自查
    if not args.stage:
        print('\n各阶段生效模型：')
        for name, desc in STAGES:
            try:
                s = resolve(stage=name, **overrides)
                mark = '' if s.model == cfg.model else '  ← stages.%s 覆盖' % name
                print('  %-9s %-28s %s%s' % (name, desc, s.model, mark))
            except ConfigError:
                print('  %-9s %-28s (配置缺失)' % (name, desc))

    if not cfg.api_key:
        print('\n❌ 没有 api_key，无法调用。')
        print(_hint())
        return 1

    if args.no_call:
        print('\n（--no-call：未发送请求）')
        return 0

    print('\n发送最小请求验证连通 …')
    try:
        working, reply = _text_probe(cfg, args.stage)
    except LLMError as exc:
        print('❌ 请求失败：%s' % exc)
        print(_hint())
        return 2
    print('✅ 文本调用正常，模型回复：%s' % reply.strip()[:80])
    if working.protocol != cfg.protocol:
        print('  ⚠ 你配的 protocol=%s 打 404，改用 %s 才通 —— 建议把配置改成 protocol=%s'
              % (cfg.protocol, working.protocol, working.protocol))

    if args.check_json:
        print('\n验证 JSON 模式 …')
        try:
            data = chat_json(
                '只输出这个 JSON，不要任何其他内容：{"ok": true, "n": 1}',
                stage=args.stage, cfg=working, max_tokens=64)
        except LLMError as exc:
            print('⚠ JSON 模式失败：%s' % exc)
            print('  可在配置里设 "json_mode": false —— 部分兼容端点不支持 response_format，')
            print('  关掉之后仍会靠输出解析兜住（剥围栏 + 括号平衡扫描）。')
            return 2
        print('✅ JSON 模式正常：%r' % (data,))

    return 0


def _exists(path: str) -> bool:
    import os
    return os.path.exists(path)


def _text_probe(cfg, stage: str):
    """发一条最小文本请求验证连通；404 时自动用另一协议回退探测一次。

    404 多半是「协议猜错」：base_url 指向 Claude Code 网关（host 不含
    anthropic、也不以 /messages 结尾）时，自动判断会误判成 openai，于是打
    /chat/completions 打出 404。用另一协议回退一次，哪个通就提示应配
    protocol=xxx。自查的代价是那次 404 多一次请求。

    Returns:
        (working_cfg, reply_text) —— working_cfg 是探测成功的配置（可能已换协议）。
    Raises:
        LLMError —— 探测彻底失败（非 404，或回退同样失败；后者消息含原 404）。
    """
    try:
        reply = chat('回复两个字：可用', stage=stage, cfg=cfg, max_tokens=32)
        return cfg, reply
    except LLMError as first:
        if first.status_code != 404:
            raise
        other = 'anthropic' if cfg.protocol == 'openai' else 'openai'
        print('  HTTP 404 —— 当前 protocol=%s 打不通，自动用 %s 回退探测一次 …'
              % (cfg.protocol, other))
        working = cfg.merged(protocol=other)
        try:
            reply = chat('回复两个字：可用', stage=stage, cfg=working, max_tokens=32)
        except LLMError as second:
            raise LLMError('protocol=%s 打 404，改用 %s 回退也失败：\n'
                           '  回退错误：%s\n  原错误：%s'
                           % (cfg.protocol, other, second, first)) from second
        return working, reply


def _hint() -> str:
    return ('\n排查顺序：\n'
            '  1. base_url 是否带了版本段（DeepSeek 用 /v1，火山方舟用 /api/v3）\n'
            '  2. protocol 是否对上：404 既不重试也不回退时，多半是协议或模型名错。\n'
            '     Claude Code 的协议是 anthropic（/v1/messages）；base_url 指向\n'
            '     api.anthropic.com 或以 /messages 结尾会自动识别，否则需显式配\n'
            '     --protocol anthropic。本脚本已在 404 时自动用另一协议回退探测过一轮\n'
            '  3. model 名称是否是该服务商的合法模型 ID（401 是 key 错）\n'
            '  4. 是否需要代理（本模块直连，不读 HTTP_PROXY 之外的设置）')


if __name__ == '__main__':
    sys.exit(main())
