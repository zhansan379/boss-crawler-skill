#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""offline 确定性 stub：评估的「不花钱、不触网」路径。

三件事：
1. `FAKE_CFG` —— 假 LLM 配置，供 --generate --offline 时把 resolve() 骗过去。
2. `install_offline(module)` —— 把某个模块里的 chat/chat_json/resolve 替换成
   一定抛 `BrokenNetwork` 的函数。offline **评估**（--offline，不生成）本就不调网络，
   这层是「防御性证明」：评估跑完没炸，说明评估路径确实一根毛都没碰网络。
3. `StubRun` —— 确定性产物生成器 + registry（可落盘、可回放）。--generate --offline
   用它产出**干净对照**：greeting 用保守文本（无术语、非客套），resume 只允许加
   「JD 里出现过、原文没有」的驱动词，绝不新增 JD 与原文都没依据的词 → 干净岗的
   hallucination 应≈0，作为真跑结果的参照系。

刻意不 import llm。要装的只是下游 gen_materials 拿到的那个模块对象。
"""

import os
import json

# 与 metrics.py 同一套 sys.path 小抄：verify_no_fabrication 在 scripts/verify/，
# 依赖 scripts/ 里的 resume_matcher 包。
import sys
# scripts/eval/materials/ 上溯两级 → scripts/ 根
_SCRIPTS = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
for _p in (_SCRIPTS, os.path.join(_SCRIPTS, 'verify')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from llm import LLMConfig
except Exception:                       # noqa: BLE001  (纯 import stub，缺 llm 也能单独用 stubs)
    LLMConfig = None
from verify_no_fabrication import _baseline_keys


class BrokenNetwork(RuntimeError):
    """Offline 路径里任何对模型的调用都会抛出它。命中说明路线被改了。"""


def _raise_broken(*args, **kwargs):
    raise BrokenNetwork(
        'offline 模式下禁止调用模型。若这条出现在评估中途，说明评估路径偷偷触网了')


# config 三层解析在 gen_materials 里是模块级 `resolve = llm.resolve`。
# offline 时我们把那个引用换掉即可，不用动 llm 包本身。
_FAKE_API = 'sk-fake-offline'           # 假 key，绝不真实请求


def make_fake_cfg():
    """一份永远不会真的打到网络的 LLMConfig。"""
    if LLMConfig is None:
        # llm 包缺席（语法/依赖问题）也能给出一个壳，保证 --offline 在残缺环境下可走
        class _Cfg:
            concurrency = 2
            model = 'fake'
        return _Cfg()
    return LLMConfig(
        protocol='openai', base_url='https://example.invalid/v1',
        api_key=_FAKE_API, model='offline-fake',
    )


FAKE_CFG = make_fake_cfg()


def install_offline(module):
    """把 module（通常是 gen_materials）的 chat/chat_json/resolve 换成抛错函数。

    返回原引用，需要时用 `install_offline(gm)` 之后 `gm.chat = saved.chat` 还原。
    用 exitstack 更方便：`with offline_guard(gm): ...`。
    """
    saved = {'chat': module.chat, 'chat_json': module.chat_json, 'resolve': module.resolve}
    module.chat = _raise_broken
    module.chat_json = _raise_broken
    module.resolve = _raise_broken
    return saved


def offline_guard(module):
    """上下文管理器：with 块内对 module 的模型调用一律抛 BrokenNetwork。"""
    import contextlib

    @contextlib.contextmanager
    def _cm():
        saved = install_offline(module)
        try:
            yield
        finally:
            module.chat = saved['chat']
            module.chat_json = saved['chat_json']
            module.resolve = saved['resolve']
    return _cm()


# ==================== 确定性产物生成（干净对照） ====================
# 纯规则，无随机：同一份 job + resume_text 永远产出同一份产物，registry 回放才一致。

_GREETING_TEMPLATE = (
    '{company}正在招{position}？我是这个方向的候选人，{extra}很乐意聊聊这个岗位。')


def clean_greeting(job, availability_text=''):
    """保守招呼语：前 15 字给硬事实、非客套，正文不含任何技术术语 → 无据词=0。

    到岗承诺只在简历写过时才带（availability_text 非空），否则不编 -> fabricated_commitment=False。
    保证 has_wasted_preview=False（不以「您好/你好/我是/我对…」开头）。
    """
    company = (job.get('公司') or '').strip() or '贵司'
    position = (job.get('职位') or '').strip() or '岗位'
    extra = availability_text + '，' if availability_text else ''
    text = _GREETING_TEMPLATE.format(company=company, position=position, extra=extra)
    return text.strip()


def clean_resume(resume_text, jd_text):
    """干净优化简历：保留原文全部章节与正文，仅允许向「专业技能」追加一行
    「JD 里出现过、原文没有」的驱动词。绝不新增 JD 与原文都没有依据的词。

    返回 (markdown, 新增的驱动词列表)。jd_text 为空则原样返回。
    """
    if not (jd_text or '').strip():
        return (resume_text or ''), []
    base = (resume_text or '').rstrip()
    baseline = _baseline_keys(base)
    driven = [t for t in sorted(_baseline_keys(jd_text)) if t not in baseline][:4]
    if not driven:
        return base, []
    line = '- 岗位驱动技术要点：%s' % '、'.join(driven)
    # 在原「专业技能」章节头部插入一行；没有该章节就补一个。均不碰其他正文。
    for marker in ('## 专业技能', '专业技能'):
        if marker in base:
            body = base.replace(marker, '%s\n%s' % (marker, line), 1)
            return body, driven
    return '%s\n\n## 专业技能\n%s' % (base, line), driven


class StubRun:
    """一批岗位的确定性产物 + 可回放 registry。

    用法：
        run = StubRun()
        for i, job in enumerate(jobs, 1):
            greeting = run.register_greeting(i, clean_greeting(job, avail))
            resume_md, driven = clean_resume(base, jd)
            run.register_resume(i, resume_md, driven)
        run.save(path)          # 落盘 JSON
        StubRun.load(path)      # 回放，再 register 会覆盖
    """

    def __init__(self):
        self.greetings = {}      # {index(int): str}
        self.resumes = {}        # {index(int): {'markdown': str, 'driven': [str]}}

    def register_greeting(self, index, text):
        self.greetings[int(index)] = text
        return text

    def register_resume(self, index, markdown, driven=None):
        self.resumes[int(index)] = {'markdown': markdown, 'driven': driven or []}
        return self.resumes[int(index)]

    def save(self, path):
        payload = {
            '_run': 'stub-registry',
            'greetings': {str(k): v for k, v in self.greetings.items()},
            'resumes': {str(k): v for k, v in self.resumes.items()},
        }
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)      # 与 write_eval_json/save_cache 同款
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return path

    @classmethod
    def load(cls, path):
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        run = cls()
        run.greetings = {int(k): v for k, v in data.get('greetings', {}).items()}
        run.resumes = {int(k): v for k, v in data.get('resumes', {}).items()}
        return run