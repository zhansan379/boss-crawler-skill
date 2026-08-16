#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""where_am_i.py 的契约测试：产物 → 下一步。

这个脚本的价值全在「说的下一步真能跑」，所以测的是三件事而不是文案：

1. **阶段名必须是 pipeline.STAGES 里的**。它打印的是可以直接粘去执行的命令，
   阶段名拼错时脚本自己不会报错，只有粘过去的人才会看到 `--from` 报用法错误。
2. **推进方向单调**。每补齐一批产物，下一步必须往后走，不能倒退也不能卡住 ——
   卡住意味着补了产物却仍被要求重跑同一步，那是无限循环。
3. **退出码恒为 0，且任何输入都不崩**。它是压缩后重建状态的第一条命令，
   自己崩掉等于把人推回去整篇读文档。空目录、坏 JSON、不存在的路径都要接住。

另外盯两条安全线：投递命令不许自带 `--yes`（不可撤销），目检必须走
verify_image.py 而不是 Read 图片。

跑法: python tests/test_where_am_i.py
"""

import os
import sys
import json
import shutil
import subprocess
import tempfile
import time

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
sys.path.insert(0, HERE)

for _stream in (sys.stdout, sys.stderr):      # Windows 控制台是 GBK
    _stream.reconfigure(encoding='utf-8', errors='replace')

import pipeline
import where_am_i as W

FAILURES = []


def check(label, cond, detail=''):
    if cond:
        print('  ✅ %s' % label)
    else:
        print('  ❌ %s  %s' % (label, detail))
        FAILURES.append(label)


def write(run_dir, name, obj=None, raw=None):
    path = os.path.join(run_dir, name)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(raw if raw is not None else json.dumps(obj, ensure_ascii=False))


def next_of(run_dir):
    """(下一步标题, 命令列表, 备注列表)。"""
    _done, (title, cmds), notes = W.survey(run_dir)
    return title, cmds, notes


def stage_in(cmds):
    """从 `pipeline.py ... --from X` 里取出 X；没有 pipeline 命令则 None。"""
    for cmd in cmds:
        if 'pipeline.py' in cmd and '--from' in cmd:
            parts = cmd.split()
            return parts[parts.index('--from') + 1]
    return None


def make_run(tmp, name):
    run_dir = os.path.join(tmp, name)
    os.makedirs(run_dir)
    return run_dir


def parsed(run_dir):
    write(run_dir, 'profile.json', {'name': '张三'})
    write(run_dir, 'resume_text.txt', raw='简历正文')
    write(run_dir, 'profile_validation.json', {'ok': True})


def crawled(run_dir, mode):
    write(run_dir, 'crawl_params.json', {'match_mode': mode, 'city': '太原'})
    write(run_dir, 'crawl_summary.json', {'written': 42, 'skipped': 3})


def merged(run_dir, n=2):
    jobs = [{'公司': '公司%d' % i, '职位': '岗位%d' % i} for i in range(1, n + 1)]
    write(run_dir, 'qualified_jobs.json', jobs)
    write(run_dir, 'matching_report.html', raw='<html></html>')
    return jobs


def materialized(run_dir, n=2):
    for i in range(1, n + 1):
        write(run_dir, 'generated/greeting_%d_公司%d.txt' % (i, i), raw='你好')
        write(run_dir, 'generated/resume_%d_公司%d.json' % (i, i), {'name': '张三'})


def verified(run_dir, n=2, clean=True):
    """verify 跑过的痕迹。报告里存的 mtime 必须和材料实际的一致 ——
    where_am_i 判的是「查过之后有没有改」，随便填个数会被当成过期。"""
    gen = os.path.join(run_dir, 'generated')
    checked = {}
    for i in range(1, n + 1):
        for name in ('greeting_%d_公司%d.txt' % (i, i), 'resume_%d_公司%d.json' % (i, i)):
            checked[name] = os.path.getmtime(os.path.join(gen, name))
    write(run_dir, 'verify_report.json', {
        'baseline_sources': ['resume_text.txt', 'profile.json'],
        'allow': [], 'checked': checked, 'unreadable': [],
        'findings': [] if clean else [{'index': 1, 'file': 'resume_1_公司1.json',
                                       'terms': ['PyTorch']}],
        'clean': clean,
    })


def rendered(run_dir, n=2):
    for i in range(1, n + 1):
        d = os.path.join(run_dir, 'applications', '公司%d-岗位%d' % (i, i))
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, '张三-岗位%d.png' % i), 'wb') as f:
            f.write(b'\x89PNG')


def test_deep_sequence(tmp):
    print('\n[1] 深度模式：产物逐步补齐，下一步必须逐步推进')
    run_dir = make_run(tmp, 'deep')

    seen = []

    def step(label, expect_stage):
        title, cmds, _notes = next_of(run_dir)
        stage = stage_in(cmds)
        check('%s → 阶段 %s' % (label, expect_stage), stage == expect_stage,
              '实际 %r（标题：%s）' % (stage, title))
        seen.append(stage)

    step('空目录', 'parse')
    parsed(run_dir)
    step('有 profile', 'infer')
    write(run_dir, 'crawl_params.json', {'match_mode': 'deep'})
    step('有 crawl_params', 'crawl')
    write(run_dir, 'crawl_summary.json', {'written': 42})
    step('爬完', 'match')
    write(run_dir, 'deep_candidates.json', [{'rank': 1}, {'rank': 2}])
    step('预筛出候选', 'deep')
    write(run_dir, 'deep_results.json', {'results': [{'rank': 1}]})
    step('深度分析完', 'merge')
    merged(run_dir)
    step('合并出报告', 'materials')
    materialized(run_dir)
    step('素材齐了', 'verify')
    verified(run_dir)
    step('核查通过', 'render')

    order = [pipeline.STAGES.index(s) for s in seen]
    check('推进方向单调不回头', order == sorted(order), str(seen))
    check('每个阶段名都在 pipeline.STAGES 里',
          all(s in pipeline.STAGES for s in seen), str(seen))
    return run_dir


def test_quick_skips_deep(tmp):
    print('\n[2] 快速模式：不该要求 deep / merge')
    run_dir = make_run(tmp, 'quick')
    parsed(run_dir)
    crawled(run_dir, 'quick')

    title, cmds, _ = next_of(run_dir)
    check('爬完之后是 match', stage_in(cmds) == 'match', title)

    merged(run_dir)
    title, cmds, _ = next_of(run_dir)
    check('有报告之后直接到 materials', stage_in(cmds) == 'materials', title)

    # 快速模式下即使没有 deep_results.json，也不该被要求跑 deep
    seen = []
    for _ in range(3):
        _t, cmds, _n = next_of(run_dir)
        seen.append(stage_in(cmds))
        materialized(run_dir)
    check('全程没提 deep/merge', 'deep' not in seen and 'merge' not in seen, str(seen))


def test_tail_steps(tmp):
    print('\n[3] 流水线之外的两步：写材料 md、投递')
    run_dir = make_run(tmp, 'tail')
    parsed(run_dir)
    crawled(run_dir, 'quick')
    merged(run_dir)
    materialized(run_dir)
    verified(run_dir)
    rendered(run_dir)

    title, cmds, _ = next_of(run_dir)
    check('渲染完 → write_application_md',
          any('write_application_md.py' in c for c in cmds), title)
    check('这一步不是 pipeline 阶段', stage_in(cmds) is None, str(cmds))

    for i in (1, 2):
        d = os.path.join(run_dir, 'applications', '公司%d-岗位%d' % (i, i))
        with open(os.path.join(d, '岗位信息+招呼语.md'), 'w', encoding='utf-8') as f:
            f.write('# md')

    title, cmds, notes = next_of(run_dir)
    joined = '\n'.join(cmds)
    check('材料齐了 → 投递', 'apply.py' in joined, title)
    check('投递命令不自带 --yes（不可撤销的一步）', '--yes' not in joined, joined)
    check('先让空跑一次', 'apply.py' in joined and '--yes' not in joined, joined)
    check('目检走 verify_image.py 而不是 Read 图片',
          'verify_image.py' in joined, joined)
    check('备注里点明 --yes 要用户同意',
          any('--yes' in n for n in notes), str(notes))

    write(run_dir, 'apply_log.json', [{'公司': '公司1', 'sent': True}])
    title, cmds, _ = next_of(run_dir)
    check('投完 → 走完了', '走完' in title, title)


def test_llm_hint(tmp):
    print('\n[4] LLM 阶段缺 api_key 时给出 llm_check')
    run_dir = make_run(tmp, 'nokey')
    old = dict(os.environ)
    for key in ('LLM_API_KEY', 'OPENAI_API_KEY'):
        os.environ.pop(key, None)
    try:
        # resolve() 会读 assets/llm_config.json；本机真配了 key 时这条提示不该出现，
        # 所以两种结果都合法 —— 要盯的是「有提示时提示的是 llm_check」。
        _t, _c, notes = next_of(run_dir)
        joined = '\n'.join(notes)
        if 'api_key' in joined:
            check('提示指向 llm_check.py', 'llm_check.py' in joined, joined)
        else:
            check('本机已配 key，未误报', True)

        os.environ['LLM_API_KEY'] = 'sk-test'
        os.environ['LLM_BASE_URL'] = 'https://example.com/v1'
        _t, _c, notes = next_of(run_dir)
        check('配了 key 之后不再提示',
              not any('llm_check.py' in n for n in notes), str(notes))
    finally:
        os.environ.clear()
        os.environ.update(old)


def test_verify_state(tmp):
    print('\n[5] verify 的状态是「有没有过期」，不是「有没有跑过」')
    run_dir = make_run(tmp, 'verify')
    parsed(run_dir)
    crawled(run_dir, 'quick')
    merged(run_dir)
    materialized(run_dir)

    title, cmds, notes = next_of(run_dir)
    check('没查过 → 阶段 verify', stage_in(cmds) == 'verify', title)
    check('说明是还没查过', any('还没查过' in n for n in notes), str(notes))

    verified(run_dir)
    _t, cmds, _ = next_of(run_dir)
    check('查过且干净 → 推进到 render', stage_in(cmds) == 'render', str(cmds))

    # 关键一条：材料重新生成之后，旧报告的「通过」对新材料不成立
    time.sleep(1.1)             # mtime 精度到秒，容差也是 1 秒
    write(run_dir, 'generated/resume_1_公司1.json', {'name': '张三', 'v': 2})
    _t, cmds, notes = next_of(run_dir)
    check('材料改过 → 退回 verify', stage_in(cmds) == 'verify', str(cmds))
    check('说清是哪份材料改了',
          any('resume_1_公司1.json' in n and '又改了' in n for n in notes), str(notes))

    # 新增一份材料同样算没查过
    verified(run_dir)
    write(run_dir, 'generated/greeting_3_公司3.txt', raw='你好')
    _t, cmds, notes = next_of(run_dir)
    check('新增材料 → 退回 verify', stage_in(cmds) == 'verify', str(cmds))
    check('说清是新材料',
          any('greeting_3_公司3.txt' in n and '新的' in n for n in notes), str(notes))

    # 上次查出编造 → 不能推进，且要带上是哪几个岗位
    os.remove(os.path.join(run_dir, 'generated', 'greeting_3_公司3.txt'))
    verified(run_dir, clean=False)
    _t, cmds, notes = next_of(run_dir)
    joined = '\n'.join(notes)
    check('上次查出编造 → 停在 verify', stage_in(cmds) == 'verify', str(cmds))
    check('带上出问题的岗位序号', '1' in joined and '查出' in joined, joined)
    check('两条路都给了，且提醒要先问用户',
          '--force' in joined and '--allow' in joined and '问用户' in joined, joined)

    # 坏掉的报告当成没查过，不能当通过
    write(run_dir, 'verify_report.json', raw='{ 坏 JSON')
    _t, cmds, _ = next_of(run_dir)
    check('坏报告当没查过（不是当通过）', stage_in(cmds) == 'verify', str(cmds))
    write(run_dir, 'verify_report.json', raw='[]')
    _t, cmds, _ = next_of(run_dir)
    check('报告是数组也不当通过', stage_in(cmds) == 'verify', str(cmds))


def test_robustness(tmp):
    print('\n[6] 坏输入不崩、退出码恒为 0')
    run_dir = make_run(tmp, 'broken')

    # 坏 JSON：每一个读 JSON 的地方都得接住
    parsed(run_dir)
    write(run_dir, 'crawl_params.json', raw='{ 这不是 JSON')
    try:
        title, cmds, _ = next_of(run_dir)
        check('crawl_params 坏了仍能给出下一步', bool(title) and bool(cmds), title)
    except Exception as exc:                       # noqa: BLE001
        check('crawl_params 坏了仍能给出下一步', False, repr(exc))

    write(run_dir, 'crawl_summary.json', raw='not json either')
    write(run_dir, 'qualified_jobs.json', raw='[[[')
    try:
        title, _cmds, _ = next_of(run_dir)
        check('多个坏文件叠加也不崩', bool(title), title)
    except Exception as exc:                       # noqa: BLE001
        check('多个坏文件叠加也不崩', False, repr(exc))

    # 空的投递池要单独说，而不是继续往下要素材
    write(run_dir, 'crawl_params.json', {'match_mode': 'quick'})
    write(run_dir, 'crawl_summary.json', {'written': 1})
    write(run_dir, 'qualified_jobs.json', [])
    write(run_dir, 'matching_report.html', raw='<html>')
    title, _cmds, _ = next_of(run_dir)
    check('空投递池会明说，而不是要求跑 materials',
          'qualified_jobs.json' in title and '空' in title, title)


def run_cli(*args):
    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    proc = subprocess.run(
        [sys.executable, os.path.join(HERE, 'where_am_i.py')] + list(args),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, cwd=HERE,
    )
    return proc.returncode, proc.stdout.decode('utf-8', 'replace')


def test_cli(tmp, deep_run):
    print('\n[7] 命令行：退出码恒为 0')
    code, out = run_cli(deep_run)
    check('正常目录退出码 0', code == 0, out[-300:])
    check('印了 run_dir', 'run_dir:' in out, out[:200])
    check('印了已完成清单', '已完成' in out, out[:200])
    check('印了下一步', '下一步' in out, out[:400])
    check('收尾指向 references/cli.md', 'references/cli.md' in out, out[-200:])

    code, out = run_cli(os.path.join(tmp, '不存在的目录'))
    check('不存在的目录退出码仍是 0', code == 0, out[-300:])
    check('并给出从头跑的命令', 'pipeline.py' in out, out)

    empty = make_run(tmp, 'cli_empty')
    code, out = run_cli(empty)
    check('空目录退出码 0', code == 0, out[-300:])
    check('空目录提示 parse', '--from parse' in out, out[-400:])


def main():
    tmp = tempfile.mkdtemp(prefix='where_am_i_test_')
    try:
        deep_run = test_deep_sequence(tmp)
        test_quick_skips_deep(tmp)
        test_tail_steps(tmp)
        test_llm_hint(tmp)
        test_verify_state(tmp)
        test_robustness(tmp)
        test_cli(tmp, deep_run)
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
    sys.exit(main())
