#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""并行分片 + 计时埋点的回归测试。

覆盖 2026-08-13 改造引入的三个新部件：
  scripts/shard_deep_candidates.py     切片
  deep_analysis.collect_shard_results  收拢
  scripts/stage_timer.py               埋点

跑法: python scripts/test_deep_shards.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

FAILURES = []


def check(label, cond, detail=''):
    if cond:
        print('  ✅ %s' % label)
    else:
        print('  ❌ %s  %s' % (label, detail))
        FAILURES.append(label)


def make_candidates(run_dir, count=9):
    """造一份 deep_candidates.json，字段跟 save_deep_candidates 的产出对齐。"""
    candidates = []
    for rank in range(1, count + 1):
        candidates.append({
            'rank': rank,
            'rule_score': 110 - rank,
            'rule_difficulty': 'Medium',
            'job': {
                'link': 'https://example.com/job/%d.html' % rank,
                '职位': 'Python后端%d' % rank,
                '公司': '测试公司%d' % rank,
                '城市': '杭州',
                '区域': '滨江区',
                '薪资': '20-30K',
                '经验': '3-5年',
                '学历': '本科',
                '规模': '100-499人',
                '技能标签': 'Python,Django',
                # 故意超长，验证 --max-jd 截断
                '岗位要求和职责': 'JD正文%d ' % rank + ('要求熟悉分布式系统。' * 400),
            },
            'rule_analysis': {
                'match_reasons': ['技能匹配'],
                'missing_skills': ['K8s'],
                'optimization_points': ['补充K8s'],
            },
            'rule_application_category': 'need_optimization',
            'rule_application_category_reason': '规则兜底理由',
        })

    data = {
        'version': '1.0',
        'mode': 'deep',
        'profile': {
            'basic_info': {'name': '张三', 'city': '杭州', 'target_position': '后端开发'},
            'education': {'school': '某大学', 'degree': '本科',
                          'major': '计算机', 'graduation_year': '2023'},
            # null 而非缺键：复现 scoring.py:676 那类坑
            'experience': {'total_years': None},
            'skills': {'programming': ['Python'], 'frameworks': ['Django'],
                       'tools': None, 'other': []},
            'projects': [{'name': '项目A', 'description': '做了个系统'}],
            'salary_expectation': {'min': None, 'max': None},
            'keywords': ['Python', '后端'],
            # 简历原文：必须不出现在分片里
            'raw_text': 'RAWTEXT_SENTINEL ' * 500,
        },
        'candidates': candidates,
        'total': len(candidates),
    }

    with open(os.path.join(run_dir, 'deep_candidates.json'), 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)
    return data


def run(*args):
    proc = subprocess.run(
        [sys.executable] + list(args),
        cwd=os.path.dirname(HERE), capture_output=True, text=True,
        encoding='utf-8', errors='replace',
    )
    return proc.returncode, (proc.stdout or '') + (proc.stderr or '')


def test_shard(tmp):
    print('\n[1] 切片')
    run_dir = os.path.join(tmp, 'run1')
    os.makedirs(run_dir)
    make_candidates(run_dir, count=9)

    code, out = run(os.path.join(HERE, 'shard_deep_candidates.py'), run_dir,
                    '--per-shard', '4', '--max-jd', '500')
    check('退出码 0', code == 0, out[-500:])

    shard_dir = os.path.join(run_dir, 'deep_shards')
    shards = sorted(n for n in os.listdir(shard_dir) if n.startswith('shard_'))
    # 9 个候选 / 每片 4 → 3 片（4+4+1）
    check('9 候选切成 3 片', shards == ['shard_01.md', 'shard_02.md', 'shard_03.md'],
          str(shards))

    texts = {}
    for name in shards:
        with open(os.path.join(shard_dir, name), encoding='utf-8') as f:
            texts[name] = f.read()

    all_text = ''.join(texts.values())
    check('简历原文 raw_text 没被塞进分片', 'RAWTEXT_SENTINEL' not in all_text)
    check('每片都含判定口径（自包含）',
          all('判定口径' in t for t in texts.values()))
    check('每片都含简历摘要（自包含）',
          all('简历摘要' in t for t in texts.values()))
    check('每片都指定了自己的结果路径',
          all('result_%02d.json' % i in texts['shard_%02d.md' % i] for i in (1, 2, 3)))

    # rank 覆盖：1..9 每个恰好出现在一片里
    ranks_seen = []
    for i, name in enumerate(shards, 1):
        for rank in range(1, 10):
            if 'rank=%d' % rank in texts[name]:
                ranks_seen.append(rank)
    check('rank 1-9 全覆盖且不重复', sorted(ranks_seen) == list(range(1, 10)),
          str(sorted(ranks_seen)))

    check('JD 已按 --max-jd 截断', '已截断至 500 字' in all_text)
    check('null 的 total_years 没崩、显示为应届/实习',
          '应届/实习' in texts['shard_01.md'])
    check('null 的期望薪资降级为 ?-?K', '?-?K' in texts['shard_01.md'])
    check('输出契约要求写文件而非复述', '不要把 JSON 内容复述到回复里' in all_text)
    check('打印了屏障命令', 'check_artifacts.py' in out and 'deep_shards' in out)
    check('埋点已写入', os.path.exists(os.path.join(run_dir, 'run_timings.jsonl')))

    # 单片体积：三片各自都应远小于「全部 JD 进主上下文」的总量
    biggest = max(len(t) for t in texts.values())
    check('单片体积受控（<6000 字符）', biggest < 6000, '最大 %d' % biggest)

    print('\n[2] 重跑切片会清掉上一轮残留')
    stale = os.path.join(shard_dir, 'result_99.json')
    with open(stale, 'w', encoding='utf-8') as f:
        f.write('{}')
    code, out = run(os.path.join(HERE, 'shard_deep_candidates.py'), run_dir,
                    '--per-shard', '4', '--max-jd', '500')
    check('残留 result_99.json 被清除', not os.path.exists(stale))
    check('清理有提示', '清理上一轮残留分片' in out)

    return run_dir


def test_collect(tmp, run_dir):
    print('\n[3] 收拢分片结果')
    from resume_matcher.deep_analysis import collect_shard_results

    shard_dir = os.path.join(run_dir, 'deep_shards')

    def write_result(index, results):
        with open(os.path.join(shard_dir, 'result_%02d.json' % index),
                  'w', encoding='utf-8') as f:
            json.dump({'results': results}, f, ensure_ascii=False)

    write_result(1, [{'rank': r, 'score': 80 + r, 'category': 'qualified'}
                     for r in (1, 2, 3, 4)])
    # 裸数组写法也要吃得下
    with open(os.path.join(shard_dir, 'result_02.json'), 'w', encoding='utf-8') as f:
        json.dump([{'rank': r, 'score': 70, 'category': 'need_optimization'}
                   for r in (5, 6, 7, 8)], f)
    write_result(3, [{'rank': 9, 'score': 60, 'category': 'cannot_apply'}])

    path = collect_shard_results(run_dir)
    check('返回了 deep_results.json 路径',
          path and path.endswith('deep_results.json'), str(path))
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    ranks = [r['rank'] for r in data['results']]
    check('9 条结果按 rank 升序', ranks == list(range(1, 10)), str(ranks))
    check('裸数组分片也被吃下', any(r['rank'] == 5 for r in data['results']))

    print('\n[4] 坏分片不带崩整批')
    with open(os.path.join(shard_dir, 'result_04.json'), 'w', encoding='utf-8') as f:
        f.write('{"results": [{"rank": 10, ')      # 半截 JSON
    write_result(5, [{'rank': 1, 'score': 1}])     # rank 与 shard_01 冲突
    write_result(6, [{'score': 99}])               # 缺 rank

    path = collect_shard_results(run_dir)
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    ranks = [r['rank'] for r in data['results']]
    check('坏分片被跳过，其余照常合并', ranks == list(range(1, 10)), str(ranks))
    first = [r for r in data['results'] if r['rank'] == 1][0]
    check('rank 冲突时保留先到的', first['score'] == 81, str(first))

    print('\n[5] 没有分片目录时返回 None（回落旧流程）')
    empty = os.path.join(tmp, 'run_empty')
    os.makedirs(empty)
    check('无 deep_shards → None', collect_shard_results(empty) is None)


def test_barrier(tmp, run_dir):
    print('\n[6] 屏障识别 deep_shards')
    shard_dir = os.path.join(run_dir, 'deep_shards')
    for name in list(os.listdir(shard_dir)):
        if name.startswith('result_'):
            os.remove(os.path.join(shard_dir, name))

    code, out = run(os.path.join(HERE, 'check_artifacts.py'), run_dir,
                    '--kinds', 'deep_shards', '--wait', '0')
    check('全缺时退出码 1', code == 1, out[-300:])
    check('报出缺失的 result 文件', 'result_01.json' in out, out[-300:])

    with open(os.path.join(shard_dir, 'result_01.json'), 'w', encoding='utf-8') as f:
        json.dump({'results': [{'rank': 1}]}, f)
    with open(os.path.join(shard_dir, 'result_02.json'), 'w', encoding='utf-8') as f:
        f.write('{"results": [{"rank": 5},')       # 半截：应判为未就绪
    with open(os.path.join(shard_dir, 'result_03.json'), 'w', encoding='utf-8') as f:
        json.dump({'results': []}, f)              # 空 results：应判为未就绪

    code, out = run(os.path.join(HERE, 'check_artifacts.py'), run_dir,
                    '--kinds', 'deep_shards', '--wait', '0')
    check('半截 JSON 判为未就绪', 'result_02.json' in out and '缺失' in out)
    check('空 results 判为未就绪', 'results 为空' in out, out[-400:])
    check('已就绪的分片报 ✅', 'result_01.json（1 条）' in out, out[-400:])

    for index, ranks in ((2, [5]), (3, [9])):
        with open(os.path.join(shard_dir, 'result_%02d.json' % index),
                  'w', encoding='utf-8') as f:
            json.dump({'results': [{'rank': r} for r in ranks]}, f)
    code, out = run(os.path.join(HERE, 'check_artifacts.py'), run_dir,
                    '--kinds', 'deep_shards', '--wait', '0')
    check('齐全时退出码 0', code == 0, out[-300:])
    check('齐全时提示合并命令', '--merge' in out)

    code, out = run(os.path.join(HERE, 'check_artifacts.py'), run_dir,
                    '--kinds', 'deep_shards', 'greeting', '--wait', '0')
    check('deep_shards 与 greeting 混用被拒', code != 0)


def test_timer(tmp):
    print('\n[7] 计时埋点')
    import stage_timer

    run_dir = os.path.join(tmp, 'run_timer')
    os.makedirs(run_dir)

    stage_timer.span(run_dir, 'fast_stage', 1.5)
    stage_timer.span(run_dir, 'slow_stage', 200)
    stage_timer.span(run_dir, 'slow_stage', 100)       # 同名多次，应合并
    try:
        with stage_timer.stage(run_dir, 'boom'):
            raise RuntimeError('炸了')
    except RuntimeError:
        pass

    records = stage_timer.load(run_dir)
    check('4 条 span 落盘', len([r for r in records if r['kind'] == 'span']) == 4,
          str(len(records)))
    boom = [r for r in records if r['stage'] == 'boom'][0]
    check('异常阶段记为 error', boom['status'] == 'error', str(boom))
    check('异常照常向上抛', True)   # 上面的 except 接住了，没接住测试就崩了

    code, out = run(os.path.join(HERE, 'stage_timer.py'), 'mark', run_dir, 'stage_a')
    check('CLI mark 成功', code == 0, out)
    code, out = run(os.path.join(HERE, 'stage_timer.py'), 'mark', run_dir, 'stage_b')
    check('CLI mark 第二次成功', code == 0, out)

    code, out = run(os.path.join(HERE, 'stage_timer.py'), 'report', run_dir)
    check('report 退出码 0', code == 0, out[-300:])
    check('慢阶段排在前面且合并了次数',
          out.index('slow_stage') < out.index('fast_stage') and '×2' in out, out)
    check('慢阶段被标记值得优化', '值得优化' in out)
    check('error 阶段有标记', '❌1' in out, out)
    check('mark 间隔被计算', 'stage_a' in out and 'stage_b' in out)
    check('5m00s 格式化正确', '5m00s' in out, out)

    print('\n[8] 埋点失败不带崩业务')
    # 传一个不可能创建的路径：应该只在 stderr 抱怨，不抛异常
    stage_timer.span(os.path.join('\x00bad'), 'x', 1)
    check('非法路径不抛异常', True)

    code, out = run(os.path.join(HERE, 'stage_timer.py'), 'report',
                    os.path.join(tmp, 'no_such_run'))
    check('没有记录时 report 不报错', code == 0, out)


def main():
    tmp = tempfile.mkdtemp(prefix='deep_shards_test_')
    try:
        run_dir = test_shard(tmp)
        test_collect(tmp, run_dir)
        test_barrier(tmp, run_dir)
        test_timer(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAILURES:
        print('❌ %d 项失败:' % len(FAILURES))
        for name in FAILURES:
            print('   - %s' % name)
        return 1
    print('✅ ALL PASSED')
    return 0


if __name__ == '__main__':
    for _stream in (sys.stdout, sys.stderr):
        _stream.reconfigure(encoding='utf-8', errors='replace')
    sys.exit(main())
