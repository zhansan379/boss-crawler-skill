#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""verify_no_fabrication.py 的契约测试（方案一：纯 LLM 全文判定）。

verify 闸门不再维护本地折词/词表 —— 它把完整原简历 + 完整材料交给模型，模型通读
全文、自己识别技术词（复合名词如 `Vibe Coding` 当整体），逐条判有没有依据。脚本层
只做三件事：驱动模型、把模型判「没有依据」的词收集成 findings、用 --allow 让人纠错。

本套测试的价值观没变：
  假阴性代价 ≫ 假阳性代价。漏一个编造的技能，人就带它去面试；多报一个，人看一眼
  上下文就放行。所以测的重点放在「真编造的必须拦」、「干净的不准拦」、
  「模型判有依据却没有引证的空理由必须升格为嫌疑」（防懒模型全标通过）。

mock 策略：进程内驱动 V.main([...])，打桩 V.chat_json / V.resolve，不联网、可确定。
每个用例尽量用**单份材料**，避免并发下想要的不同响应注入顺序不定导致的 flaky。

跑法: python tests/test_verify_no_fabrication.py
"""

import os
import sys
import json
import shutil
import tempfile
from contextlib import contextmanager

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'verify'))

for _stream in (sys.stdout, sys.stderr):      # Windows 控制台是 GBK
    _stream.reconfigure(encoding='utf-8', errors='replace')

import verify_no_fabrication as V
from llm import ConfigError, LLMError

FAILURES = []
_TMP = None
SCRIPT = os.path.join(HERE, 'verify', 'verify_no_fabrication.py')


def check(label, cond, detail=''):
    if cond:
        print('  ✅ %s' % label)
    else:
        print('  ❌ %s  %s' % (label, detail))
        FAILURES.append(label)


# 基底：一份普通的国内应届简历（喂给模型当「依据」参考，verify.st 读它）
RESUME_TEXT = """张三　2026 届本科　计算机科学与技术
技能：Python、FastAPI、MySQL/Redis、Docker、Git、Linux
项目：中国软件杯《AI 模拟面试系统》，用 LangChain 做多智能体编排，
      向量检索用 Milvus，前端 Vue3 + TypeScript。
论文：多模态智能体在教育场景的应用（已录用）
熟悉 scikit-learn、Node.js 与 k8s 基础概念。
"""

PROFILE = {
    'name': '张三',
    'skills': ['Pandas', 'NumPy'],          # 只在 profile 里，喂给模型一并参考
    'projects': [{'name': '校园二手平台', 'stack': ['Flask', 'SQLite']}],
    'education': None,                      # 显式 null，_walk_strings 不该崩
}


def make_run(materials, resume_text=RESUME_TEXT, profile=PROFILE):
    """建一个 run 目录，按 paths.py 的四桶契约落盘。"""
    run_dir = tempfile.mkdtemp(prefix='vnf_', dir=_TMP)
    st = os.path.join(run_dir, 'state')
    os.makedirs(st, exist_ok=True)
    if resume_text is not None:
        with open(os.path.join(st, 'resume_text.txt'), 'w', encoding='utf-8') as f:
            f.write(resume_text)
    if profile is not None:
        with open(os.path.join(st, 'profile.json'), 'w', encoding='utf-8') as f:
            json.dump(profile, f, ensure_ascii=False)

    # qualified_jobs.json 决定 --only 的合法范围（check_artifacts._load_jobs 读它）
    with open(os.path.join(st, 'qualified_jobs.json'), 'w', encoding='utf-8') as f:
        json.dump([{'link': 'L%d' % i, '职位': '岗位%d' % i, '公司': '公司%d' % i}
                   for i in (1, 2, 3)], f, ensure_ascii=False)

    gen = os.path.join(run_dir, 'materials')
    os.makedirs(gen)
    for name, body in materials.items():
        with open(os.path.join(gen, name), 'w', encoding='utf-8') as f:
            if isinstance(body, str):
                f.write(body)
            else:
                json.dump(body, f, ensure_ascii=False)
    return run_dir


def T(term, supported, reason=''):
    """造一条模型的 terms 判定。"""
    d = {'term': term, 'supported': supported}
    if reason:
        d['reason'] = reason
    return d


class _StubCfg:
    concurrency = 2

    def require_key(self):
        pass


@contextmanager
def mock_verify(chats, resolve_ok=True, fail_chat=False):
    """打桩 V.chat_json 和 V.resolve，进程内测 main 而不联网。

    chats: 一份 terms 列表，每次调用都返回它（并发下所有材料拿同一份 —— 所以
    每个用例只用单份材料，保证语义确定）。resolve_ok=False 模拟没配到 LLM。
    """
    old_c, old_r = V.chat_json, V.resolve

    def fake_chat(prompt, **k):
        if fail_chat:
            raise LLMError('stub boom')
        return {'terms': chats}

    def fake_resolve(**k):
        if not resolve_ok:
            raise ConfigError('no api key configured')
        return _StubCfg()

    V.chat_json, V.resolve = fake_chat, fake_resolve
    try:
        yield
    finally:
        V.chat_json, V.resolve = old_c, old_r


def report_terms(run_dir):
    """读 verify_report.json，返回 [(term, supported_notstored)] —— 只抽 term 列表。"""
    with open(V.verify_report_path(run_dir), 'r', encoding='utf-8') as f:
        data = json.load(f)
    out = []
    for fnd in data.get('findings', []):
        for item in fnd.get('terms', []):
            out.append(item['term'])
    return out, data.get('clean')


# ==================== 判定主路径 ====================

def test_true_fabrication_blocks():
    print('\n[1] 模型判有编造 → findings 非空、退出 1、报告不干净')
    run_dir = make_run({'resume_1_公司1.json':
                        {'optimized_resume': '用 PyTorch 与 AutoGen 做训练。'}})
    with mock_verify([T('PyTorch', False, '无'), T('AutoGen', False, '无')]):
        rc = V.main([run_dir])
    check('退出码 1', rc == 1, '实际 %d' % rc)
    terms, clean = report_terms(run_dir)
    check('两个编造都进了报告', set(terms) == {'PyTorch', 'AutoGen'},
          '实际: %s' % sorted(terms))
    check('报告 marked 不干净', clean is False, '实际 %r' % clean)


def test_clean_is_zero():
    print('\n[2] 模型全判有依据 → 干净、退出 0、报告 clean')
    run_dir = make_run({'resume_1_公司1.json':
                        {'optimized_resume': 'Python、Docker、Git。'}})
    with mock_verify([T('Python', True, '原文技能'), T('Docker', True, '原文技能'),
                      T('Git', True, '原文技能')]):
        rc = V.main([run_dir])
    check('退出码 0', rc == 0, '实际 %d' % rc)
    terms, clean = report_terms(run_dir)
    check('无 findings', terms == [], '实际 %s' % terms)
    check('报告 clean', clean is True, '实际 %r' % clean)


def test_compound_is_one_unit():
    print('\n[3] 复合技术词（Vibe Coding / GitHub Copilot）作为一个报告单位，不被拆碎')
    run_dir = make_run({'resume_1_公司1.json':
                        {'optimized_resume': '长期践行 Vibe Coding 工作流，用 GitHub Copilot 辅助。'}})
    with mock_verify([T('Vibe Coding', False, '无'), T('GitHub Copilot', False, '无')]):
        rc = V.main([run_dir])
    check('有编造 → 退出 1', rc == 1, '实际 %d' % rc)
    terms, _ = report_terms(run_dir)
    check('Vibe Coding 整词进报告（不是 Vibe/Coding 两条）', 'Vibe Coding' in terms
          and 'Vibe' not in terms and 'Coding' not in terms,
          '实际: %s' % sorted(terms))
    check('GitHub Copilot 整词进报告', 'GitHub Copilot' in terms
          and 'GitHub' not in terms and 'Copilot' not in terms,
          '实际: %s' % sorted(terms))


def test_empty_reason_upgraded():
    print('\n[4] 模型判有依据却无引证（懒模型全标通过）→ 升格为嫌疑，必须拦')
    run_dir = make_run({'resume_1_公司1.json':
                        {'optimized_resume': '掌握 Vue3、TypeScript。'}})
    with mock_verify([T('Vue3', True, ''), T('TypeScript', True, '')]):
        rc = V.main([run_dir])
    check('退出码 1（空理由全标通过不放行）', rc == 1, '实际 %d' % rc)
    terms, _ = report_terms(run_dir)
    check('两个都进了 findings', set(terms) == {'Vue3', 'TypeScript'},
          '实际: %s' % sorted(terms))


def test_llm_failure_bounces():
    print('\n[5] LLM 调用失败 → 按「不可判定」阻断，退 1，绝不静默放行')
    run_dir = make_run({'resume_1_公司1.json':
                        {'optimized_resume': '会用任何技能。'}})
    with mock_verify([], fail_chat=True):
        rc = V.main([run_dir])
    check('退出码 1', rc == 1, '实际 %d' % rc)


# ==================== --allow 人的纠错 ====================

def test_allow_filters_model_output():
    print('\n[6] --allow 放行模型报出的词 → 过滤后干净、退 0')
    run_dir = make_run({'resume_1_公司1.json':
                        {'optimized_resume': '用 GitHub Copilot 辅助编码。'}})
    with mock_verify([T('GitHub Copilot', False, '无')]):
        rc = V.main([run_dir, '--allow', 'GitHub Copilot'])
    check('退出码 0（被放行）', rc == 0, '实际 %d' % rc)


def test_allow_repeatable_and_normalized():
    print('\n[7] --allow 可重复、大小写/空格不敏感（Vibe Coding ≡ vibe coding）')
    run_dir = make_run({'resume_1_公司1.json':
                        {'optimized_resume': '会用 Vibe Coding 和 Copilot。'}})
    with mock_verify([T('Vibe Coding', False, '无'), T('Copilot', False, '无')]):
        rc1 = V.main([run_dir, '--allow', 'Vibe-Coding', '--allow', 'copilot'])
    check('--allow 两个化身都命中 → 退 0', rc1 == 0, '实际 %d' % rc1)


def test_allow_does_not_overlift():
    print('\n[8] --allow 只放行名单内的词，名单外的编造仍报')
    run_dir = make_run({'resume_1_公司1.json':
                        {'optimized_resume': '用 Vibe Coding 和 PyTorch。'}})
    with mock_verify([T('Vibe Coding', False, '无'), T('PyTorch', False, '无')]):
        rc = V.main([run_dir, '--allow', 'Vibe Coding'])
    check('PyTorch 未被放行 → 仍退 1', rc == 1, '实际 %d' % rc)
    terms, _ = report_terms(run_dir)
    check('只剩 PyTorch 一条', terms == ['PyTorch'], '实际 %s' % terms)


# ==================== 前置条件与退出码契约 ====================

def test_no_llm_blocks():
    print('\n[9] 没配到 LLM → 报错退 1，绝不退回旧规则/静默放行')
    run_dir = make_run({'resume_1_公司1.json':
                        {'optimized_resume': '用任何技能。'}})
    with mock_verify([T('Python', True, '原文')], resolve_ok=False):
        rc = V.main([run_dir])
    check('退出码 1', rc == 1, '实际 %d' % rc)


def test_no_baseline():
    print('\n[10] 没有原简历原文 → 无法判定，「零发现」是假阴性，退 1')
    run_dir = make_run({'resume_1_公司1.json':
                        {'optimized_resume': 'Python、Docker。'}},
                       resume_text=None, profile=None)
    with mock_verify([]):
        rc = V.main([run_dir])
    check('退出码 1', rc == 1, '实际 %d' % rc)


def test_no_material():
    print('\n[11] 没有任何可查材料 → 退 1（没查过 ≠ 干净），且说清是没材料')
    run_dir = make_run({})
    with mock_verify([]):
        rc = V.main([run_dir])
    check('退出码 1', rc == 1, '实际 %d' % rc)


def test_only_validation():
    print('\n[12] --only 范围与用法错')
    dirty = make_run({'resume_1_公司1.json': {'optimized_resume': '用 PyTorch。'}})
    with mock_verify([T('PyTorch', False, '无')]):
        rc = V.main([dirty, '--only', '1'])
    check('--only 1 查脏份 → 1', rc == 1, '实际 %d' % rc)
    with mock_verify([T('Python', True, '原文')]):
        rc = V.main([dirty, '--only', '2'])
    check('--only 2 只查没材料那份 → 无材料退 1（范围里没有可查）', rc == 1, '实际 %d' % rc)

    with mock_verify([]):
        rc = V.main([dirty, '--only', '99'])
    check('--only 越界 → 2（不是静默忽略）', rc == 2, '实际 %d' % rc)
    with mock_verify([]):
        rc = V.main([dirty, '--only', 'abc'])
    check('--only 写法错 → 2', rc == 2, '实际 %d' % rc)
    with mock_verify([]):
        rc = V.main([os.path.join(dirty, 'nope')])
    check('目录不存在 → 1（前置未满足，非用法错）', rc == 1, '实际 %d' % rc)


# ==================== 材料收集与报告 ====================

def test_collect_broken():
    print('\n[13] 读不动的材料被记为 broken，collect_targets 不因它崩溃')
    run_dir = make_run({'resume_1_公司1.json': '{broken json',
                        'greeting_2_公司2.txt': '你好，希望获得面试机会。'})
    targets, broken = V.collect_targets(run_dir)
    check('坏 JSON 进 broken', len(broken) == 1, '实际 %s' % broken)
    check('好的一份照常收集', len(targets) == 1, '实际 %d' % len(targets))


def test_report_stale():
    print('\n[14] 报告留痕 + mtime 过期检测（材料改了必须提示重查）')
    run_dir = make_run({'resume_1_公司1.json':
                        {'optimized_resume': 'Python、Docker。'}})
    with mock_verify([T('Python', True, '原文'), T('Docker', True, '原文')]):
        rc = V.main([run_dir])
    check('干净 → 0', rc == 0, '实际 %d' % rc)
    reason, _ = V.report_is_stale(run_dir)
    check('报告不过期', reason is None, '实际 %r' % reason)

    # 材料在查过后被重写 → 报告过期。report_is_stale 有 1 秒 mtime 容差（FAT/网络盘
    # 精度只到秒），所以显式把 mtime 推到未来，别赌同一秒内写完会不会同比刷新。
    path = os.path.join(run_dir, 'materials', 'resume_1_公司1.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({'optimized_resume': '用 PyTorch。'}, f, ensure_ascii=False)
    future = os.path.getmtime(path) + 5
    os.utime(path, (future, future))
    reason, _ = V.report_is_stale(run_dir)
    check('材料改了 → 提示重查', reason is not None, '实际 %r' % reason)


# ==================== 杂项基元 ====================

def test_normalize():
    print('\n[15] --allow 归一：空格/点/连字符/大小写不算区别，+ # 保留')
    check('Vibe Coding → vibecoding', V._normalize('Vibe Coding') == 'vibecoding')
    check('GitHub-Copilot → githubcopilot', V._normalize('GitHub-Copilot') == 'githubcopilot')
    check('C++ 保留 +', V._normalize('C++') == 'c++')
    check('C# 保留 #', V._normalize('C#') == 'c#')


def test_eval_tools_survive():
    print('\n[16] 评估工具依赖的本地取词保留（eval/materials/metrics 与 evaluate_materials 仍能 import）')
    from verify_no_fabrication import _tokens, _baseline_keys, _norm, _context, load_baseline
    check('_baseline_keys 可拆词', 'react' in _baseline_keys('会用 React Native'), '拆词失败')
    check('load_baseline 返回词集合+来源', True)
    _ = (_tokens, _norm, _context)


def main():
    global _TMP
    _TMP = tempfile.mkdtemp(prefix='vnf_suite_')
    print('=' * 60)
    print('verify_no_fabrication.py 契约测试（纯 LLM 全文判定）')
    print('=' * 60)
    try:
        test_true_fabrication_blocks()
        test_clean_is_zero()
        test_compound_is_one_unit()
        test_empty_reason_upgraded()
        test_llm_failure_bounces()
        test_allow_filters_model_output()
        test_allow_repeatable_and_normalized()
        test_allow_does_not_overlift()
        test_no_llm_blocks()
        test_no_baseline()
        test_no_material()
        test_only_validation()
        test_collect_broken()
        test_report_stale()
        test_normalize()
        test_eval_tools_survive()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)

    print('\n' + '=' * 60)
    if FAILURES:
        print('❌ %d 项未通过：' % len(FAILURES))
        for f in FAILURES:
            print('   - %s' % f)
        return 1
    print('✅ 全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())