#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""verify_no_fabrication.py 的契约测试：查「简历原文里没有的技术词」。

这个脚本的价值全在**假阴性代价 ≫ 假阳性代价**：漏一个编造的技能，
人就带着它去面试；多报一个，人看一眼上下文就放行了。所以测的重点是：

1. **真编造必须报出来**。原文没有的技术词一个都不能漏。
2. **原文有的不许报**。误报本身不致命，但误报多了人就开始无脑跳过提示，
   那时候真的编造也会被一起跳过 —— 这才是误报真正的代价。
   斜杠尤其要盯：「MySQL/Redis」曾被整体当成一个词，把两个真技能报成编造。
3. **optimization_suggestions 不在检查范围内**。那栏本职工作就是提没有的技术词。
4. **中文不参与**。取词从拉丁字母开头，「多智能体面试系统」不该被当技术词。
5. **没基底 ≠ 干净**。基底缺失时「零发现」是假阴性，必须非零退出。
6. **提示要能直接粘出去跑**。序号去重、放行名单按词截断。
7. **退出码**：0 干净 / 1 有发现或缺前置 / 2 用法错 / 3 部分材料读不动。

跑法: python tests/test_verify_no_fabrication.py
"""

import os
import sys
import json
import shutil
import subprocess
import tempfile

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
sys.path.insert(0, HERE)

for _stream in (sys.stdout, sys.stderr):      # Windows 控制台是 GBK
    _stream.reconfigure(encoding='utf-8', errors='replace')

import verify_no_fabrication as V

FAILURES = []
_TMP = None
SCRIPT = os.path.join(HERE, 'verify_no_fabrication.py')


def check(label, cond, detail=''):
    if cond:
        print('  ✅ %s' % label)
    else:
        print('  ❌ %s  %s' % (label, detail))
        FAILURES.append(label)


# 基底：一份普通的国内应届简历，斜杠分隔的技能栏是常态写法
RESUME_TEXT = """张三　2026 届本科　计算机科学与技术
技能：Python、FastAPI、MySQL/Redis、Docker、Git、Linux
项目：中国软件杯《AI 模拟面试系统》，用 LangChain 做多智能体编排，
      向量检索用 Milvus，前端 Vue3 + TypeScript。
论文：多模态智能体在教育场景的应用（已录用）
熟悉 scikit-learn、Node.js 与 k8s 基础概念。
"""

PROFILE = {
    'name': '张三',
    'skills': ['Pandas', 'NumPy'],          # 只在 profile 里，测并集
    'projects': [{'name': '校园二手平台', 'stack': ['Flask', 'SQLite']}],
    'education': None,                      # 显式 null，_walk_strings 不该崩
}


def make_run(materials, resume_text=RESUME_TEXT, profile=PROFILE):
    """建一个 run 目录。materials: {文件名: str 或 dict}。"""
    run_dir = tempfile.mkdtemp(prefix='vnf_', dir=_TMP)
    if resume_text is not None:
        with open(os.path.join(run_dir, 'resume_text.txt'), 'w', encoding='utf-8') as f:
            f.write(resume_text)
    if profile is not None:
        with open(os.path.join(run_dir, 'profile.json'), 'w', encoding='utf-8') as f:
            json.dump(profile, f, ensure_ascii=False)

    # qualified_jobs.json 决定 --only 的合法范围
    with open(os.path.join(run_dir, 'qualified_jobs.json'), 'w', encoding='utf-8') as f:
        json.dump([{'link': 'L%d' % i, '职位': '岗位%d' % i, '公司': '公司%d' % i}
                   for i in (1, 2, 3)], f, ensure_ascii=False)

    gen = os.path.join(run_dir, 'generated')
    os.makedirs(gen)
    for name, body in materials.items():
        with open(os.path.join(gen, name), 'w', encoding='utf-8') as f:
            if isinstance(body, str):
                f.write(body)
            else:
                json.dump(body, f, ensure_ascii=False)
    return run_dir


def flat(findings):
    """findings → {原文写法} 的扁平集合，方便断言。"""
    return {raw for _, _, hits in findings for raw, _ in hits}


def run_verify(run_dir, **kw):
    baseline, sources = V.load_baseline(run_dir, kw.pop('override', None))
    findings, targets, broken = V.verify(run_dir, baseline, **kw)
    return findings, targets, broken, sources


# ==================== 1. 真编造要报出来 ====================

def test_catches_fabrication():
    print('\n[1] 原文没有的技术词必须报出来')
    run_dir = make_run({
        'resume_1_公司1.json': {
            'optimized_resume': '熟练使用 PyTorch 训练模型，用 nginx 做反向代理，'
                                '基于 AutoGen 搭建多智能体，检索层用 LlamaIndex。',
        },
        # Terraform 而不是 Kubernetes —— 基底里有 k8s，别名会把 Kubernetes 认成原文，
        # 拿它当「编造」等于把测试写成永远失败（第一版就踩了这个）。
        'greeting_1_公司1.txt': '您好，我用过 Terraform 管理基础设施。',
    })
    findings, targets, broken, _ = run_verify(run_dir)
    got = flat(findings)

    for term in ('PyTorch', 'nginx', 'AutoGen', 'LlamaIndex'):
        check('报出 %s' % term, term in got, '实际: %s' % sorted(got))
    check('招呼语里的 Terraform 也报', 'Terraform' in got, '实际: %s' % sorted(got))
    check('简历和招呼语各算一条 finding', len(findings) == 2, '实际 %d' % len(findings))
    check('两份材料都查了', len(targets) == 2, '实际 %d' % len(targets))
    check('没有读不动的', broken == [], '实际: %s' % broken)
    check('每条都带上下文', all(ctx for _, _, hits in findings for _, ctx in hits))


# ==================== 2. 原文有的不许报 ====================

def test_no_false_positive():
    print('\n[2] 原文里有的词不许报（误报会让人开始无脑跳过提示）')
    run_dir = make_run({
        'resume_1_公司1.json': {
            'optimized_resume': '主要技术栈 Python、FastAPI、MySQL/Redis、Docker；'
                                '做过 LangChain 多智能体面试系统，前端 Vue3 + TypeScript，'
                                '用过 scikit-learn 和 Node.js。',
        },
    })
    findings, _, _, sources = run_verify(run_dir)
    got = flat(findings)

    check('MySQL/Redis 不报（斜杠是分隔符，不是词的一部分）',
          not ({'MySQL', 'Redis', 'MySQL/Redis'} & got), '实际: %s' % sorted(got))
    check('scikit-learn 不报（连字符属于词内）', 'scikit-learn' not in got)
    check('Node.js 不报（点属于词内）', not any(g.lower().startswith('node') for g in got))
    check('Vue3 不报（词后带数字）', 'Vue3' not in got)
    check('整份材料干净', findings == [], '实际: %s' % sorted(got))
    check('基底两个来源都认', sources == ['resume_text.txt', 'profile.json'],
          '实际: %s' % sources)


def test_profile_only_skill():
    print('\n[2b] 只写在 profile.json 里的技能也算原文（并集，不是择一）')
    run_dir = make_run({
        'resume_1_公司1.json': {'optimized_resume': '用 Pandas 和 NumPy 做数据清洗，Flask 写接口。'},
        # FastAPI 只在 resume_text.txt 里、不在 profile.json 里，用来证明
        # --resume-text 换掉 resume_text.txt 之后它确实失去了原文依据
        'resume_2_公司2.json': {'optimized_resume': '用 FastAPI 写接口。'},
    })
    findings, _, _, _ = run_verify(run_dir)
    check('Pandas/NumPy/Flask 都不报（只在 profile 里）',
          findings == [], '实际: %s' % sorted(flat(findings)))

    # 反过来：override 掉 resume_text 后，profile 仍然生效
    other = os.path.join(_TMP, 'other_resume.txt')
    with open(other, 'w', encoding='utf-8') as f:
        f.write('只有一句话，没有任何技能。')
    findings2, _, _, sources2 = run_verify(run_dir, override=other)
    got2 = flat(findings2)
    check('--resume-text 覆盖 resume_text.txt（择一）',
          sources2 == ['other_resume.txt', 'profile.json'], '实际: %s' % sources2)
    check('覆盖后 profile 里的 Pandas 仍不报', 'Pandas' not in got2, '实际: %s' % sorted(got2))
    check('覆盖后 resume_text 独有的 FastAPI 反而要报',
          'FastAPI' in got2, '实际: %s' % sorted(got2))


def test_alias():
    print('\n[2c] 确定同义的写法互认')
    run_dir = make_run({
        'resume_1_公司1.json': {'optimized_resume': '熟悉 Kubernetes 与 nodejs。'},
    })
    findings, _, _, _ = run_verify(run_dir)
    got = flat(findings)
    check('原文写 k8s，材料写 Kubernetes → 不报', 'Kubernetes' not in got,
          '实际: %s' % sorted(got))
    check('原文写 Node.js，材料写 nodejs → 不报', 'nodejs' not in got,
          '实际: %s' % sorted(got))


# ==================== 3. 检查范围 ====================

def test_scope():
    print('\n[3] 只查会发出去的内容')
    run_dir = make_run({
        'resume_1_公司1.json': {
            'optimized_resume': 'Python、FastAPI、Docker。',                 # 干净
            'optimization_suggestions': ['建议补充 Kubernetes', '可以学 Kafka 和 Spark'],
            'match_analysis': 'JD 要求 Elasticsearch，你没有',
        },
    })
    findings, targets, _, _ = run_verify(run_dir)
    got = flat(findings)
    check('optimization_suggestions 里的 Kubernetes 不报',
          'Kubernetes' not in got, '实际: %s' % sorted(got))
    check('Kafka / Spark 不报', not ({'Kafka', 'Spark'} & got), '实际: %s' % sorted(got))
    check('match_analysis 里的 Elasticsearch 不报',
          'Elasticsearch' not in got, '实际: %s' % sorted(got))
    check('只有 optimized_resume 进了待查', len(targets) == 1, '实际 %d' % len(targets))

    # optimized_resume 缺失或为空 → 这份材料不算待查，而不是当空文本查过
    run2 = make_run({'resume_2_公司2.json': {'optimization_suggestions': ['补 Rust']}})
    _, targets2, _, _ = run_verify(run2)
    check('没有 optimized_resume 的材料不进待查', targets2 == [], '实际: %s' % targets2)


def test_chinese_not_a_token():
    print('\n[4] 中文不参与取词')
    run_dir = make_run({
        'greeting_1_公司1.txt': '您好，我做过多智能体面试系统，也搞过知识图谱问答和数字孪生平台。',
    })
    findings, _, _, _ = run_verify(run_dir)
    check('中文短语一个都不报', findings == [],
          '实际: %s' % sorted(flat(findings)))


# ==================== 5. 没基底 ≠ 干净 ====================

def test_no_baseline():
    print('\n[5] 缺基底时不能报「干净」')
    run_dir = make_run(
        {'resume_1_公司1.json': {'optimized_resume': '精通 PyTorch 和 Kubernetes。'}},
        resume_text=None, profile=None)

    baseline, sources = V.load_baseline(run_dir, None)
    check('sources 为空能被调用方看见', sources == [], '实际: %s' % sources)
    check('基底词集合为空', baseline == set(), '实际: %s' % baseline)

    code, out = cli(run_dir)
    check('CLI 退 1 而不是 0', code == 1, '实际 %d' % code)
    check('说清是缺基底', '缺基底' in out, out[:200])
    check('没有打出「没查出」的假干净结论', '没查出' not in out, out[:200])

    # 坏掉的 profile.json 不能算基底来源
    run2 = make_run({'resume_1_公司1.json': {'optimized_resume': 'PyTorch'}},
                    resume_text=None, profile=None)
    with open(os.path.join(run2, 'profile.json'), 'w', encoding='utf-8') as f:
        f.write('{ 这不是 JSON')
    _, sources2 = V.load_baseline(run2, None)
    check('坏 profile.json 不进 sources', sources2 == [], '实际: %s' % sources2)


# ==================== 6. 读不动的材料 ====================

def test_broken_material():
    print('\n[6] 读不动的材料要报出来，不能算查过')
    run_dir = make_run({
        'resume_1_公司1.json': '{ 坏 JSON',
        'resume_2_公司2.json': {'optimized_resume': 'Python、Docker。'},
    })
    findings, targets, broken, _ = run_verify(run_dir)
    check('坏 JSON 进 broken', len(broken) == 1, '实际: %s' % broken)
    check('好的那份仍然查了', len(targets) == 1, '实际 %d' % len(targets))
    check('好的那份是干净的', findings == [], '实际: %s' % sorted(flat(findings)))

    code, out = cli(run_dir)
    check('有 broken 且无发现 → 退 3（部分成功）', code == 3, '实际 %d：%s' % (code, out[:200]))
    check('明说那几份没查过', '没查过' in out or '读不动' in out, out[:300])


# ==================== 7. 提示可直接粘贴 ====================

def test_hints():
    print('\n[7] 两条下一步提示要能直接粘出去跑')
    run_dir = make_run({
        # 同一个岗位的两份材料都有编造 → 序号会重复
        'resume_1_公司1.json': {'optimized_resume': '用 PyTorch 训练。'},
        'greeting_1_公司1.txt': '我熟悉 nginx。',
        'resume_2_公司2.json': {'optimized_resume': '用 Rust 写服务。'},
    })
    code, out = cli(run_dir)
    check('有发现 → 退 1', code == 1, '实际 %d' % code)

    only_line = [l for l in out.splitlines() if '--only' in l]
    check('打出了 --only 提示', len(only_line) == 1, '实际: %s' % only_line)
    if only_line:
        spec = only_line[0].split('--only')[1].split()[0]
        check('序号去重且升序（不是 1,1,2）', spec == '1,2', '实际: %s' % spec)

    allow_line = [l for l in out.splitlines() if '--allow' in l]
    check('打出了 --allow 提示', len(allow_line) == 1, '实际: %s' % allow_line)
    if allow_line:
        terms = allow_line[0].split('--allow')[1].strip().split(',')
        check('放行名单里每个词都完整（没被截断）',
              all(t.strip() in ('PyTorch', 'nginx', 'Rust') for t in terms),
              '实际: %s' % terms)

    check('提醒了查不出夸大', '夸大' in out, out[-300:])


def test_allow():
    print('\n[7b] --allow 放行')
    run_dir = make_run({
        'resume_1_公司1.json': {'optimized_resume': '用 PyTorch 和 nginx。'},
    })
    code, _ = cli(run_dir)
    check('放行前退 1', code == 1, '实际 %d' % code)

    code, out = cli(run_dir, '--allow', 'PyTorch,nginx')
    check('逗号分隔放行 → 退 0', code == 0, '实际 %d：%s' % (code, out[:200]))

    code, _ = cli(run_dir, '--allow', 'PyTorch', '--allow', 'nginx')
    check('--allow 可重复', code == 0, '实际 %d' % code)

    code, _ = cli(run_dir, '--allow', 'PyTorch/nginx')
    check('斜杠写法也能放行（与取词同规则）', code == 0, '实际 %d' % code)

    code, _ = cli(run_dir, '--allow', 'pytorch,NGINX')
    check('放行不分大小写', code == 0, '实际 %d' % code)

    code, _ = cli(run_dir, '--allow', 'PyTorch')
    check('只放行一个，另一个仍报', code == 1, '实际 %d' % code)


# ==================== 8. CLI 契约 ====================

def cli(run_dir, *extra):
    proc = subprocess.run(
        [sys.executable, SCRIPT, run_dir] + list(extra),
        capture_output=True, text=True, encoding='utf-8', errors='replace')
    return proc.returncode, (proc.stdout or '') + (proc.stderr or '')


def test_cli():
    print('\n[8] 退出码与 --only 范围')
    clean = make_run({'resume_1_公司1.json': {'optimized_resume': 'Python、Docker、Git。'}})
    dirty = make_run({
        'resume_1_公司1.json': {'optimized_resume': '用 PyTorch。'},
        'resume_2_公司2.json': {'optimized_resume': 'Python、Docker。'},
    })

    code, out = cli(clean)
    check('干净 → 0', code == 0, '实际 %d：%s' % (code, out[:200]))
    code, _ = cli(dirty)
    check('有发现 → 1', code == 1, '实际 %d' % code)

    code, _ = cli(dirty, '--only', '2')
    check('--only 2 只查干净那份 → 0', code == 0, '实际 %d' % code)
    code, _ = cli(dirty, '--only', '1')
    check('--only 1 查脏那份 → 1', code == 1, '实际 %d' % code)

    code, out = cli(dirty, '--only', '99')
    check('--only 越界 → 2（不是静默忽略）', code == 2, '实际 %d：%s' % (code, out[:200]))
    code, out = cli(dirty, '--only', 'abc')
    check('--only 写法错 → 2', code == 2, '实际 %d' % code)

    code, out = cli(os.path.join(clean, 'nope'))
    check('目录不存在 → 1（前置未满足，非用法错）', code == 1, '实际 %d：%s' % (code, out[:200]))

    code, out = cli(dirty, '--quiet')
    check('--quiet 仍退 1', code == 1, '实际 %d' % code)
    check('--quiet 不打上下文', 'PyTorch' in out and '训练' not in out, out[:300])

    # 只跑过 crawl（没有 generated/）时，「零发现」不等于干净 —— 什么都没查过。
    # 口径与缺基底一致：退 1 阻断，别让 render 接着跑。
    bare = make_run({})
    code, out = cli(bare)
    check('没有任何材料 → 1（没查过 ≠ 干净）', code == 1, '实际 %d：%s' % (code, out[:200]))
    check('说清是没材料而不是干净', '无材料' in out and '没查出' not in out, out[:200])


def main():
    global _TMP
    _TMP = tempfile.mkdtemp(prefix='vnf_suite_')
    print('=' * 60)
    print('verify_no_fabrication.py 契约测试')
    print('=' * 60)
    try:
        test_catches_fabrication()
        test_no_false_positive()
        test_profile_only_skill()
        test_alias()
        test_scope()
        test_chinese_not_a_token()
        test_no_baseline()
        test_broken_material()
        test_hints()
        test_allow()
        test_cli()
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
