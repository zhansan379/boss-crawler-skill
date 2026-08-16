#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""scripts/stage_timer.py 的回归测试。

埋点是全流程共用的横切件（parse/deep/materials/render/apply/pipeline 都在写
run_timings.jsonl），所以它有两条硬约束：合并同名阶段、以及**失败不带崩业务** ——
异常要照原样往上抛、写盘失败只许抱怨一句。这两条都在下面被盯着。

原先这些用例挂在 test_deep_shards.py 里（分片方案已删），拆出来独立成文件。

跑法: python tests/test_stage_timer.py
"""

import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
sys.path.insert(0, HERE)

for _stream in (sys.stdout, sys.stderr):      # Windows 控制台是 GBK，✅ 会抛
    _stream.reconfigure(encoding='utf-8', errors='replace')

FAILURES = []


def check(label, cond, detail=''):
    if cond:
        print('  ✅ %s' % label)
    else:
        print('  ❌ %s  %s' % (label, detail))
        FAILURES.append(label)


def run(*args):
    """跨进程跑 CLI：子命令的参数解析和退出码只有这样才测得到。"""
    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    proc = subprocess.run(
        [sys.executable] + list(args),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        env=env, cwd=HERE,
    )
    return proc.returncode, proc.stdout.decode('utf-8', 'replace')


def test_api(tmp):
    print('\n[1] Python API')
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
    return run_dir


def test_cli(run_dir):
    print('\n[2] 命令行 mark / report')
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


def test_never_crashes(tmp):
    print('\n[3] 埋点失败不带崩业务')
    import stage_timer

    # 传一个不可能创建的路径：应该只在 stderr 抱怨，不抛异常
    stage_timer.span(os.path.join('\x00bad'), 'x', 1)
    check('非法路径不抛异常', True)

    code, out = run(os.path.join(HERE, 'stage_timer.py'), 'report',
                    os.path.join(tmp, 'no_such_run'))
    check('没有记录时 report 不报错', code == 0, out)


def main():
    tmp = tempfile.mkdtemp(prefix='stage_timer_test_')
    try:
        run_dir = test_api(tmp)
        test_cli(run_dir)
        test_never_crashes(tmp)
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
