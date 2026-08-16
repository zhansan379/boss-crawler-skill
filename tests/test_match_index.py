#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""match_index.py 的契约测试：四个文件合成一张表。

这张表存在的理由是「分数 + 判定 + 公司 + 职位」哪个单文件都拿不全，
所以测的是合并本身，而不是输出格式：

1. **精细度叠加的方向不能反**。deep_results 的模型分必须盖住 scored_jobs 的规则分，
   反过来就是拿粗的盖细的 —— 这种错不报异常，只会让人按错的分数投递。
2. **rank 是 deep_results 唯一的对齐键**。deep_results 自己不含 link，
   rank 错位不会报任何错，只会把一个岗位的分析安到另一个岗位上，所以要锁住。
3. **序号 = qualified_jobs.json 的 1-based 原序**，且这张表不排序 ——
   一排序，index 和 `--only` / `materials_*_N` / `apply --max` 的对应关系就断了。
4. **缺产物不崩**。只跑了 crawl 的目录、坏 JSON、少文件都要出表，
   没匹配上的岗位由 `matched < total` 报出来，而不是留一列空白让人以为分数就是空的。
5. **不能把 JD 正文带出来**。这个视图是给主上下文读的，泄一段 JD 就失去意义。

跑法: python tests/test_match_index.py
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

import match_index as M

FAILURES = []
_TMP = None


def check(label, cond, detail=''):
    if cond:
        print('  ✅ %s' % label)
    else:
        print('  ❌ %s  %s' % (label, detail))
        FAILURES.append(label)


def write(run_dir, name, obj=None, raw=None):
    path = os.path.join(run_dir, name)
    with open(path, 'w', encoding='utf-8') as f:
        if raw is not None:
            f.write(raw)
        else:
            json.dump(obj, f, ensure_ascii=False)


JD_MARKER = '这段JD正文不许出现在瘦视图里' * 40


def full_fixture():
    """四个来源都齐的 run 目录。L4 故意没跑过匹配。"""
    run_dir = tempfile.mkdtemp(prefix='mi_', dir=_TMP)
    write(run_dir, 'qualified_jobs.json', [
        {'link': 'L1', '职位': 'AI应用开发', '公司': '甲公司', '城市': '太原',
         '薪资': '10-15K', '岗位要求和职责': JD_MARKER, '公司信息': JD_MARKER},
        {'link': 'L2', '职位': 'Python工程师', '公司': '乙公司', '城市': '西安', '薪资': '12-18K'},
        {'link': 'L3', '职位': '算法实习', '公司': '丙公司', '城市': '太原', '薪资': '200/天'},
        {'link': 'L4', '职位': '没跑过匹配', '公司': '丁公司', '城市': '太原', '薪资': '面议'},
    ])
    write(run_dir, 'scored_jobs.json', {
        'tier3': [{'link': 'L1', 'match_score': 70,
                   'application_category': 'need_optimization',
                   'missing_skills': ['K8s']}],
        'tier2': [{'link': 'L2', 'match_score': 55,
                   'application_category': 'need_optimization'}],
    })
    write(run_dir, 'job_classification.json', {'classification': {
        # L2 这条故意不给 application_category，测桶名兜底
        'qualified': [{'link': 'L2', 'match_score': 62,
                       'classification_reason': '技能匹配', 'highlight': 'JD 要的都会'}],
        'cannot_apply': [{'link': 'L3', 'match_score': 30,
                          'application_category': 'cannot_apply',
                          'classification_reason': '学历不符'}],
    }})
    write(run_dir, 'deep_candidates.json', {'candidates': [
        {'rank': 1, 'job': {'link': 'L1'}},
        {'rank': 2, 'job': {'link': 'L3'}},
    ]})
    write(run_dir, 'deep_results.json', {'results': [
        {'rank': 1, 'score': 88, 'category': 'qualified', 'reason': '非常匹配',
         'missing_items': ['无'], 'highlight': '项目经历直接对口'},
        {'rank': 2, 'score': 35, 'category': 'cannot_apply', 'reason': '经验差 4 年'},
    ]})
    return run_dir


# ==================== 1. 叠加方向 ====================

def test_precedence():
    print('\n[1] 精细度叠加：deep 盖 classification 盖 scored')
    run_dir = full_fixture()
    index = M.build_match_index(run_dir)

    check('L1 取深度分 88（不是规则分 70）', index['L1']['match_score'] == 88,
          index['L1'].get('match_score'))
    check('L1 判定取深度的 qualified（不是规则的 need_optimization）',
          index['L1']['category'] == 'qualified', index['L1'].get('category'))
    check('L2 取规则视图 62（不是 scored 的 55；它没进深度）',
          index['L2']['match_score'] == 62, index['L2'].get('match_score'))
    check('L3 取深度分 35（不是视图的 30）', index['L3']['match_score'] == 35,
          index['L3'].get('match_score'))
    check('L4 完全不在索引里', 'L4' not in index, list(index))


# ==================== 2. category 补齐 ====================

def test_category_filled():
    """P1 的附带修正：put() 原先不收 category，判定列整列是空的。"""
    print('\n[2] 判定列不为空，桶名可兜底')
    run_dir = full_fixture()
    index = M.build_match_index(run_dir)

    check('三个匹配过的岗位都有 category',
          all(index[k].get('category') for k in ('L1', 'L2', 'L3')),
          {k: index[k].get('category') for k in ('L1', 'L2', 'L3')})
    # L2 的视图里没有 application_category，只能从桶名来
    check('L2 的判定由桶名兜底成 qualified', index['L2']['category'] == 'qualified',
          index['L2'].get('category'))


# ==================== 3. rank 对齐 ====================

def test_rank_alignment():
    print('\n[3] rank 是 deep_results 唯一的对齐键')
    run_dir = full_fixture()

    # 把 deep_candidates 的 rank 对调，link 不动：分析结果必须跟着换岗位
    write(run_dir, 'deep_candidates.json', {'candidates': [
        {'rank': 2, 'job': {'link': 'L1'}},
        {'rank': 1, 'job': {'link': 'L3'}},
    ]})
    index = M.build_match_index(run_dir)
    check('rank 对调后 L1 拿到 35 分', index['L1']['match_score'] == 35,
          index['L1'].get('match_score'))
    check('rank 对调后 L3 拿到 88 分', index['L3']['match_score'] == 88,
          index['L3'].get('match_score'))

    # rank 对不上的候选：跳过，不能崩，也不能张冠李戴
    write(run_dir, 'deep_candidates.json', {'candidates': [{'rank': 99, 'job': {'link': 'L1'}}]})
    index = M.build_match_index(run_dir)
    check('rank 无对应结果时退回规则分 70', index['L1']['match_score'] == 70,
          index['L1'].get('match_score'))


# ==================== 4. ranked 视图 ====================

def test_ranked_view():
    print('\n[4] ranked 视图：序号、原序、matched 计数')
    run_dir = full_fixture()
    view = M.build_ranked(run_dir)

    check('total = 4', view['total'] == 4, view.get('total'))
    check('matched = 3（L4 没匹配上）', view['matched'] == 3, view.get('matched'))
    check('序号是 1-based 连续', [j['index'] for j in view['jobs']] == [1, 2, 3, 4],
          [j['index'] for j in view['jobs']])
    check('顺序就是 qualified_jobs.json 的原序（没按分数排）',
          [j['link'] for j in view['jobs']] == ['L1', 'L2', 'L3', 'L4'],
          [j['link'] for j in view['jobs']])
    check('L4 的分数是 None 而不是 0', view['jobs'][3]['match_score'] is None,
          view['jobs'][3].get('match_score'))
    check('公司职位取到了（deep_results 里本来没有这两列）',
          view['jobs'][0]['company'] == '甲公司' and view['jobs'][0]['position'] == 'AI应用开发',
          view['jobs'][0])

    limited = M.build_ranked(run_dir, limit=2)
    check('--limit 2 只出 2 条', limited['total'] == 2, limited.get('total'))
    check('--limit 不改序号', [j['index'] for j in limited['jobs']] == [1, 2],
          [j['index'] for j in limited['jobs']])


# ==================== 5. 不泄 JD ====================

def test_no_jd_leak():
    print('\n[5] 瘦视图不带 JD / 公司信息正文')
    run_dir = full_fixture()
    blob = json.dumps(M.build_ranked(run_dir), ensure_ascii=False)

    check('输出里没有 JD 正文', JD_MARKER[:20] not in blob)
    check('输出规模没爆（4 个岗位 < 2KB）', len(blob) < 2048, len(blob))


# ==================== 6. 缺产物 / 坏 JSON ====================

def test_degrades():
    print('\n[6] 只跑了 crawl、坏 JSON、少文件都不崩')

    bare = tempfile.mkdtemp(prefix='bare_', dir=_TMP)
    write(bare, 'qualified_jobs.json', [{'link': 'L1', '职位': 'X', '公司': 'Y'}])
    view = M.build_ranked(bare)
    check('只有 qualified_jobs 也能出表', view['total'] == 1, view)
    check('matched = 0（没有任何匹配产物）', view['matched'] == 0, view.get('matched'))
    check('索引为空 dict 而不是异常', M.build_match_index(bare) == {})

    broken = tempfile.mkdtemp(prefix='broken_', dir=_TMP)
    write(broken, 'qualified_jobs.json', [{'link': 'L1', '职位': 'X', '公司': 'Y'}])
    write(broken, 'scored_jobs.json', raw='{"tier3": [,,,}')
    write(broken, 'job_classification.json', raw='not json at all')
    view = M.build_ranked(broken)
    check('坏掉的匹配产物被跳过，表照出', view['total'] == 1, view)

    # 被包一层的 qualified_jobs.json
    wrapped = tempfile.mkdtemp(prefix='wrapped_', dir=_TMP)
    write(wrapped, 'qualified_jobs.json', {'jobs': [{'link': 'L1', '职位': 'X', '公司': 'Y'}]})
    check('{"jobs": [...]} 也认', M.build_ranked(wrapped)['total'] == 1)

    # deep_candidates 在、deep_results 不在：不能因为半套就崩
    half = tempfile.mkdtemp(prefix='half_', dir=_TMP)
    write(half, 'qualified_jobs.json', [{'link': 'L1', '职位': 'X', '公司': 'Y'}])
    write(half, 'deep_candidates.json', {'candidates': [{'rank': 1, 'job': {'link': 'L1'}}]})
    check('只有 deep_candidates 没有 deep_results 时不崩',
          M.build_ranked(half)['total'] == 1)


# ==================== 7. CLI ====================

def test_cli():
    print('\n[7] read_thin.py --kind ranked 的 CLI 契约')
    run_dir = full_fixture()
    env = dict(os.environ, PYTHONIOENCODING='utf-8')

    def run(*args):
        return subprocess.run([sys.executable, os.path.join(HERE, 'read_thin.py')] + list(args),
                              capture_output=True, text=True, encoding='utf-8', env=env)

    ok = run(run_dir, '--kind', 'ranked')
    check('退出 0', ok.returncode == 0, ok.stderr or ok.stdout[:200])
    try:
        payload = json.loads(ok.stdout)
        check('stdout 是合法 JSON', True)
        check('CLI 与函数结果一致', payload == M.build_ranked(run_dir))
    except ValueError as e:
        check('stdout 是合法 JSON', False, repr(e))

    # ranked 要目录；给文件应当明确报错而不是当成 JSON 去解析
    bad = run(os.path.join(run_dir, 'qualified_jobs.json'), '--kind', 'ranked')
    check('给文件而非目录 → 退出 1', bad.returncode == 1, bad.returncode)
    check('错误信息说清要目录', 'run directory' in bad.stdout, bad.stdout[:120])

    # 未知 kind 属用法错误 → 2（仓库统一约定）
    unknown = run(run_dir, '--kind', 'nope')
    check('未知 kind → 退出 2', unknown.returncode == 2, unknown.returncode)
    check('未知 kind 的提示里列出了 ranked', 'ranked' in unknown.stdout, unknown.stdout[:120])

    check('--limit 非整数 → 退出 2',
          run(run_dir, '--kind', 'ranked', '--limit', 'x').returncode == 2)

    # 老三个 kind 不能被这次改动带坏
    jobs = run(os.path.join(run_dir, 'qualified_jobs.json'), '--kind', 'jobs')
    check('--kind jobs 仍然可用', jobs.returncode == 0, jobs.stdout[:120])
    check('--kind jobs 仍然不带 JD', JD_MARKER[:20] not in jobs.stdout)
    check('缺文件 → 退出 1',
          run(os.path.join(run_dir, 'nope.json'), '--kind', 'jobs').returncode == 1)
    check('不带参数 → 退出 1 并打用法', run().returncode == 1)


# ==================== 8. gen_materials 侧没被改坏 ====================

def test_gen_materials_reexport():
    print('\n[8] gen_materials 仍能拿到同一个 build_match_index')
    import gen_materials

    check('gen_materials.build_match_index 就是 match_index 里那个',
          gen_materials.build_match_index is M.build_match_index)
    run_dir = full_fixture()
    check('从 gen_materials 调用结果一致',
          gen_materials.build_match_index(run_dir) == M.build_match_index(run_dir))


def main():
    global _TMP
    print('=' * 60)
    print('match_index.py 契约测试')
    print('=' * 60)
    _TMP = tempfile.mkdtemp(prefix='mi_suite_')
    try:
        test_precedence()
        test_category_filled()
        test_rank_alignment()
        test_ranked_view()
        test_no_jd_leak()
        test_degrades()
        test_cli()
        test_gen_materials_reexport()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)

    print('\n' + '=' * 60)
    if FAILURES:
        print('❌ %d 项失败: %s' % (len(FAILURES), '; '.join(FAILURES)))
        return 1
    print('✅ 全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
