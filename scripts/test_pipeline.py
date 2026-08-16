# -*- coding: utf-8 -*-
"""pipeline.py 的编排测试 —— 一个子进程都不真起。

最重要的一条断言在最后：**流水线跑到终点也绝不执行 apply.py**。其余用例覆盖
「阶段区间」「失败就停」「快速模式补投递池」「爬空了不许往下走」这几处编排逻辑，
它们的共同特点是：错了不会报错，只会安静地少投几个岗位或多烧一轮 token。

注意 `--all`：不给终点时 pipeline **只跑一个阶段**，所以要断言「九个阶段全跑」的用例
必须显式给 `--all`。用例 11 专门盯这套区间语义。

做法：换掉 pipeline.run_stage（真正 fork 子进程的地方），记录被调用的阶段与命令。
阶段之后的产物核查照旧真跑（读真文件），因为那正是要测的东西。

跑法：python scripts/test_pipeline.py
"""

import os
import io
import sys
import json
import shutil
import tempfile
import contextlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

for _stream in (sys.stdout, sys.stderr):
    _stream.reconfigure(encoding='utf-8', errors='replace')

import pipeline

RAN = []            # [(阶段名, argv), ...]
CODES = {}          # 阶段名 → 让它返回的退出码


def _fake_run_stage(name, cmd, run_dir, timed=False):
    RAN.append((name, cmd))
    return CODES.get(name, 0)


def build_run_dir(scored=None, qualified=None, profile=True, params=None,
                  crawl_summary=True, materials=()):
    run_dir = tempfile.mkdtemp(prefix='pipeline_')
    if profile:
        json.dump({'basic_info': {'name': '张三'}},
                  open(os.path.join(run_dir, 'profile.json'), 'w', encoding='utf-8'),
                  ensure_ascii=False)
    if params is not None:
        json.dump(params, open(os.path.join(run_dir, 'crawl_params.json'), 'w',
                               encoding='utf-8'), ensure_ascii=False)
    if scored is not None:
        json.dump(scored, open(os.path.join(run_dir, 'scored_jobs.json'), 'w',
                               encoding='utf-8'), ensure_ascii=False)
    if qualified is not None:
        json.dump(qualified, open(os.path.join(run_dir, 'qualified_jobs.json'), 'w',
                                  encoding='utf-8'), ensure_ascii=False)
    if crawl_summary:
        json.dump({'written': 40, 'total': 40, 'skipped': 0, 'run_dups': 0},
                  open(os.path.join(run_dir, 'crawl_summary.json'), 'w',
                       encoding='utf-8'), ensure_ascii=False)
    if materials:
        gen = os.path.join(run_dir, 'generated')
        os.makedirs(gen, exist_ok=True)
        for kind, index, tag in materials:
            with open(os.path.join(gen, '%s_%d_%s.txt' % (kind, index, tag)),
                      'w', encoding='utf-8') as f:
                f.write('内容')
    return run_dir


def run(argv):
    RAN.clear()
    return pipeline.main(argv)


def run_capture(argv):
    """跑一遍并把 stdout 收下来 —— 提示文案本身就是要测的东西。"""
    RAN.clear()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = pipeline.main(argv)
    return code, buf.getvalue()


def stages_ran():
    return [name for name, _ in RAN]


def cmd_of(name):
    for stage, cmd in RAN:
        if stage == name:
            return cmd
    return []


# 一个岗位在 scored_jobs.json 里长什么样：原始 CSV 字段 + 评分字段混在一起
def scored_job(company, position, link, extra=None):
    job = {
        'link': link, '职位': position, '公司': company, '城市': '杭州',
        '薪资': '15-25K', '经验': '1-3年', '学历': '本科',
        '岗位要求和职责': 'JD 正文', 'HR活跃度': '刚刚活跃',
        'match_score': 92, 'application_category': 'qualified',
        'analysis': {'skill_match': 0.9},
    }
    job.update(extra or {})
    return job


def main():
    failures = []

    def check(label, cond, detail=''):
        print('%s %s%s' % ('  ✅' if cond else '  ❌', label,
                           '' if cond else '  ← ' + detail))
        if not cond:
            failures.append(label)

    pipeline.run_stage = _fake_run_stage
    # 岗位池行数：真去扫 assets/post_data 会把开发机上的历史数据算进来，
    # 结果随机器而变。这里固定住，min-jobs 那道闸门才测得准。
    pipeline.job_pool_rows = lambda: 40

    print('=== 1. --dry-run：九个阶段按序打印，一个都不执行 ===')
    resume = tempfile.mktemp(suffix='.md')
    with open(resume, 'w', encoding='utf-8') as f:
        f.write('# 张三\nPython 后端开发，3 年经验。')
    run_dir = build_run_dir(params={'keywords': ['Python'], 'cities': ['杭州'],
                                    'mode': 'custom', 'match_mode': 'deep', 'top_n': 10})
    try:
        CODES.clear()
        code = run([resume, '--run-dir', run_dir, '--all', '--dry-run'])
        check('退出码 0', code == 0, '实际 %s' % code)
        check('一个阶段都没执行', stages_ran() == [], str(stages_ran()))
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)

    # --dry-run 不给 --run-dir 时也不能留下痕迹：真跑一次会 create_run_dir()，
    # 那会把 LATEST.txt 指到新目录 —— 而 LATEST.txt 是 --from 续跑、
    # run_matcher --merge、where_am_i 共用的「当前运行」指针。dry-run 把它指到
    # 一个空目录，等于把上一轮真实运行的续跑入口悄悄换掉。
    latest_file = os.path.join(pipeline.OUTPUT_DIR, 'LATEST.txt')
    before = (open(latest_file, encoding='utf-8').read()
              if os.path.exists(latest_file) else None)
    dirs_before = set(os.listdir(pipeline.OUTPUT_DIR)) \
        if os.path.isdir(pipeline.OUTPUT_DIR) else set()
    CODES.clear()
    code = run([resume, '--all', '--dry-run'])
    after = (open(latest_file, encoding='utf-8').read()
             if os.path.exists(latest_file) else None)
    dirs_after = set(os.listdir(pipeline.OUTPUT_DIR)) \
        if os.path.isdir(pipeline.OUTPUT_DIR) else set()
    check('--dry-run 不新建运行目录', dirs_after == dirs_before,
          str(sorted(dirs_after - dirs_before)))
    check('--dry-run 不改写 LATEST.txt 指针', after == before,
          '%r → %r' % (before, after))
    check('--dry-run 无 --run-dir 也是退出码 0', code == 0, '实际 %s' % code)

    print('\n=== 2. 深度模式 --all：九个阶段全跑，顺序不能乱 ===')
    run_dir = build_run_dir(
        params={'keywords': ['Python'], 'cities': ['杭州'], 'mode': 'custom',
                'match_mode': 'deep', 'top_n': 10, 'min_count': 20},
        qualified=[{'公司': 'XX科技', '职位': 'Java开发', 'link': 'https://x/1'}],
        materials=[('greeting', 1, 'XX科技'), ('resume', 1, 'XX科技')])
    try:
        CODES.clear()
        code = run([resume, '--run-dir', run_dir, '--all'])
        check('退出码 0', code == 0, '实际 %s' % code)
        check('阶段顺序完整',
              stages_ran() == ['parse', 'infer', 'crawl', 'match', 'deep',
                               'merge', 'materials', 'verify', 'render'], str(stages_ran()))
        check('match 走 deep 且带 --top 10',
              '--mode' in cmd_of('match') and 'deep' in cmd_of('match')
              and '--top' in cmd_of('match') and '10' in cmd_of('match'),
              pipeline.show(cmd_of('match')))
        check('merge 带 --merge', '--merge' in cmd_of('merge'), pipeline.show(cmd_of('merge')))
        check('crawl 参数由 crawl_params 拼出（-p/-c/-y 齐）',
              all(f in cmd_of('crawl') for f in ('-p', 'Python', '-c', '杭州', '-y')),
              pipeline.show(cmd_of('crawl')))
        check('crawl 带 --run-dir', '--run-dir' in cmd_of('crawl'))
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)

    print('\n=== 3. 快速模式：跳过 deep/merge，并补出 qualified_jobs.json ===')
    run_dir = build_run_dir(
        params={'keywords': ['Python'], 'cities': ['杭州'], 'match_mode': 'quick'},
        scored={'tier1': [scored_job('XX科技', 'Java开发', 'https://x/1')],
                'tier2': [scored_job('YY公司', '全栈工程师', 'https://x/2')],
                'tier3': [scored_job('ZZ集团', '测试', 'https://x/3')],
                'tier4': [], 'total': 3},
        materials=[('greeting', 1, 'XX科技'), ('resume', 1, 'XX科技'),
                   ('greeting', 2, 'YY公司'), ('resume', 2, 'YY公司')])
    try:
        CODES.clear()
        code = run([resume, '--run-dir', run_dir, '--all'])
        check('退出码 0', code == 0, '实际 %s' % code)
        check('deep/merge 没跑', 'deep' not in stages_ran() and 'merge' not in stages_ran(),
              str(stages_ran()))
        check('match 走 quick', 'quick' in cmd_of('match'), pipeline.show(cmd_of('match')))

        path = os.path.join(run_dir, 'qualified_jobs.json')
        pool = json.load(open(path, encoding='utf-8')) if os.path.exists(path) else None
        check('qualified_jobs.json 被补出来了', isinstance(pool, list))
        if isinstance(pool, list):
            check('只收 tier1 + tier2（tier3 不投）', len(pool) == 2, '实际 %d' % len(pool))
            check('顺序是符合在前', (pool[0].get('公司'), pool[1].get('公司'))
                  == ('XX科技', 'YY公司'), str([j.get('公司') for j in pool]))
            check('只留原始爬取字段（评分字段不带进去）',
                  all('match_score' not in j and 'analysis' not in j for j in pool),
                  str(sorted(pool[0])))
            check('link 在（下游按 link 回查 CSV）', all(j.get('link') for j in pool))
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)

    print('\n=== 4. 已有 qualified_jobs.json 时绝不覆盖（用户可能已手工收窄） ===')
    kept = [{'公司': '只投这一家', '职位': 'Java开发', 'link': 'https://x/9'}]
    run_dir = build_run_dir(
        params={'keywords': ['Python'], 'cities': ['杭州'], 'match_mode': 'quick'},
        scored={'tier1': [scored_job('XX科技', 'Java开发', 'https://x/1')],
                'tier2': [scored_job('YY公司', '全栈', 'https://x/2')]},
        qualified=kept,
        materials=[('greeting', 1, '只投这一家'), ('resume', 1, '只投这一家')])
    try:
        CODES.clear()
        code = run([resume, '--run-dir', run_dir, '--to', 'match'])
        check('退出码 0', code == 0, '实际 %s' % code)
        pool = json.load(open(os.path.join(run_dir, 'qualified_jobs.json'), encoding='utf-8'))
        check('文件原样保留', pool == kept, str(pool))
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)

    print('\n=== 5. 爬完没有 crawl_summary.json（没登录）：停住，不往下跑 ===')
    run_dir = build_run_dir(
        params={'keywords': ['Python'], 'cities': ['杭州'], 'match_mode': 'quick'},
        crawl_summary=False)
    try:
        CODES.clear()
        code = run([resume, '--run-dir', run_dir, '--from', 'crawl'])
        check('退出码 1', code == 1, '实际 %s' % code)
        check('停在 crawl，match 没跑', stages_ran() == ['crawl'], str(stages_ran()))
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)

    print('\n=== 6. 岗位池低于最低岗位数：停在 crawl（deep 按岗位烧钱） ===')
    run_dir = build_run_dir(params={'keywords': ['Python'], 'cities': ['太原'],
                                    'match_mode': 'deep', 'min_count': 100})
    try:
        CODES.clear()
        code = run([resume, '--run-dir', run_dir, '--from', 'crawl'])
        check('退出码 1', code == 1, '实际 %s' % code)
        check('没进 match', stages_ran() == ['crawl'], str(stages_ran()))

        CODES.clear()
        code = run([resume, '--run-dir', run_dir, '--from', 'crawl', '--to', 'match',
                    '--min-jobs', '0'])
        check('--min-jobs 0 是放行开关', 'match' in stages_ran(), str(stages_ran()))
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)

    print('\n=== 7. 某阶段失败：立刻停，后面的阶段一个都不跑 ===')
    run_dir = build_run_dir(params={'keywords': ['Python'], 'cities': ['杭州'],
                                    'match_mode': 'deep', 'top_n': 5})
    try:
        CODES.clear()
        CODES['deep'] = 1
        code = run([resume, '--run-dir', run_dir, '--all'])
        check('退出码 1', code == 1, '实际 %s' % code)
        check('停在 deep', stages_ran() == ['parse', 'infer', 'crawl', 'match', 'deep'],
              str(stages_ran()))
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)

    print('\n=== 8. --no-images：不渲染，且提示的投递命令带 --no-image ===')
    run_dir = build_run_dir(
        params={'keywords': ['Python'], 'cities': ['杭州'], 'match_mode': 'quick'},
        scored={'tier1': [scored_job('XX科技', 'Java开发', 'https://x/1')], 'tier2': []},
        materials=[('greeting', 1, 'XX科技'), ('resume', 1, 'XX科技')])
    try:
        CODES.clear()
        code = run([resume, '--run-dir', run_dir, '--all', '--no-images'])
        check('退出码 0', code == 0, '实际 %s' % code)
        check('render 没跑', 'render' not in stages_ran(), str(stages_ran()))
        # 不要长图 ≠ 不用查材料：招呼语照样会发出去，verify 必须还在计划里
        check('verify 仍然跑了', 'verify' in stages_ran(), str(stages_ran()))
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)

    print('\n=== 9. materials 部分失败（缺产物）：退出码 3，不当成成功 ===')
    run_dir = build_run_dir(
        params={'keywords': ['Python'], 'cities': ['杭州'], 'match_mode': 'quick'},
        scored={'tier1': [scored_job('XX科技', 'Java开发', 'https://x/1')],
                'tier2': [scored_job('YY公司', '全栈', 'https://x/2')]},
        materials=[('greeting', 1, 'XX科技'), ('resume', 1, 'XX科技')])   # #2 的两份都缺
    try:
        CODES.clear()
        code = run([resume, '--run-dir', run_dir, '--all', '--no-images'])
        check('退出码 3（部分成功）', code == 3, '实际 %s' % code)
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)

    print('\n=== 10. 全程没有任何一条命令指向 apply.py ===')
    run_dir = build_run_dir(
        params={'keywords': ['Python'], 'cities': ['杭州'], 'match_mode': 'deep',
                'top_n': 10},
        qualified=[{'公司': 'XX科技', '职位': 'Java开发', 'link': 'https://x/1'}],
        materials=[('greeting', 1, 'XX科技'), ('resume', 1, 'XX科技')])
    try:
        CODES.clear()
        code = run([resume, '--run-dir', run_dir, '--all'])
        check('退出码 0', code == 0, '实际 %s' % code)
        flat = ' '.join(' '.join(cmd) for _, cmd in RAN)
        check('没有 apply.py', 'apply.py' not in flat, flat)
        check('没有 auto_apply', 'auto_apply' not in flat, flat)
        check('没有 --yes 投递开关（-y 是爬虫的确认，不同一回事）',
              '--yes' not in flat, flat)
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)

    # ── 阶段区间语义 ──────────────────────────────────────────────
    # 「不给 --to 就只跑一个阶段」是这套流水线最容易被悄悄改回去的一条：
    # 默认值一变，`pipeline.py 简历.pdf` 就会重新把 materials（按岗位两次模型调用）
    # 顺手跑掉，而退出码仍是 0 —— 没有任何一处会报错。
    print('\n=== 11. 不给 --to 只跑一个阶段 ===')
    run_dir = build_run_dir(params={'keywords': ['Python'], 'cities': ['杭州'],
                                    'match_mode': 'deep', 'top_n': 10})
    try:
        CODES.clear()
        code, out = run_capture([resume, '--run-dir', run_dir])
        check('退出码 0', code == 0, '实际 %s' % code)
        check('只跑了 parse', stages_ran() == ['parse'], str(stages_ran()))
        check('印出下一步命令（--from infer）',
              '--from infer' in out, out[-400:])
        check('印出一次跑完余下的命令（--all）', '--all' in out, out[-400:])

        CODES.clear()
        code = run([resume, '--run-dir', run_dir, '--from', 'infer'])
        check('--from infer 只跑 infer', stages_ran() == ['infer'], str(stages_ran()))

        CODES.clear()
        code = run([resume, '--run-dir', run_dir, '--from', 'materials'])
        check('--from materials 不带上 render',
              stages_ran() == ['materials'], str(stages_ran()))
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)

    print('\n=== 12. --from match 是一个整体：match → deep → merge ===')
    # 深度模式下单跑 match 只写 deep_candidates.json，没有 qualified_jobs.json，
    # 下游一步都走不了 —— 停在那里是个谁都用不了的半成品。
    run_dir = build_run_dir(params={'keywords': ['Python'], 'cities': ['杭州'],
                                    'match_mode': 'deep', 'top_n': 10})
    try:
        CODES.clear()
        code = run([resume, '--run-dir', run_dir, '--from', 'match'])
        check('退出码 0', code == 0, '实际 %s' % code)
        check('三步一起跑', stages_ran() == ['match', 'deep', 'merge'], str(stages_ran()))
        check('materials 没被带上', 'materials' not in stages_ran(), str(stages_ran()))

        CODES.clear()
        code = run([resume, '--run-dir', run_dir, '--from', 'deep'])
        check('--from deep 跑到 merge 为止',
              stages_ran() == ['deep', 'merge'], str(stages_ran()))
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)

    print('\n=== 13. 快速模式 --from match：只跑 match，并补出投递池 ===')
    run_dir = build_run_dir(
        params={'keywords': ['Python'], 'cities': ['杭州'], 'match_mode': 'quick'},
        scored={'tier1': [scored_job('XX科技', 'Java开发', 'https://x/1')], 'tier2': []})
    try:
        CODES.clear()
        code, out = run_capture([resume, '--run-dir', run_dir, '--from', 'match'])
        check('退出码 0', code == 0, '实际 %s' % code)
        check('deep/merge 没跑（快速模式里它们不存在）',
              stages_ran() == ['match'], str(stages_ran()))
        check('qualified_jobs.json 被补出来了',
              os.path.exists(os.path.join(run_dir, 'qualified_jobs.json')))
        # 下一步不能指向 deep/merge —— 快速模式下那条命令只会印「跳过」。
        check('下一步指向 materials', '--from materials' in out, out[-500:])
        check('提醒 materials 会按岗位调两次模型', '两次模型' in out, out[-500:])
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)

    print('\n=== 14. --all 与 --to 同时给：用法错误（退出码 2） ===')
    run_dir = build_run_dir(params={'keywords': ['Python'], 'cities': ['杭州']})
    try:
        CODES.clear()
        code = 0
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                run([resume, '--run-dir', run_dir, '--all', '--to', 'match'])
        except SystemExit as exc:      # argparse 互斥组直接 exit(2)
            code = exc.code
        check('退出码 2', code == 2, '实际 %s' % code)
        check('一个阶段都没跑', stages_ran() == [], str(stages_ran()))
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)

    print('\n=== 15. verify 查出编造：停在 render 之前 ===')
    # verify 的失败和别的阶段不同 —— 它「失败」正说明它干了活。所以要锁两件事：
    # render 一定不跑（图渲出来人就当材料定稿了），提示得说清是查出了东西。
    run_dir = build_run_dir(
        params={'keywords': ['Python'], 'cities': ['杭州'], 'match_mode': 'quick'},
        scored={'tier1': [scored_job('XX科技', 'Java开发', 'https://x/1')], 'tier2': []},
        materials=[('greeting', 1, 'XX科技'), ('resume', 1, 'XX科技')])
    try:
        CODES.clear()
        CODES['verify'] = 1                     # 查出了简历原文没有的技术词
        code, out = run_capture([resume, '--run-dir', run_dir, '--all'])
        check('退出码 1', code == 1, '实际 %s' % code)
        check('render 没跑（图渲出来就等于材料定稿）',
              'render' not in stages_ran(), str(stages_ran()))
        check('提示说的是查出了东西，不是脚本坏了',
              '查出' in out and '修完接着跑' not in out, out[-400:])
        check('给了放行和重生成两条路',
              '--allow' in out and 'gen_materials.py' in out, out[-400:])
        check('也说了怎么彻底跳过', '--skip-verify' in out, out[-400:])

        # verify 退 3 = 有几份材料读不动：那是部分成功，不该拦住 render
        CODES.clear()
        CODES['verify'] = 3
        code = run([resume, '--run-dir', run_dir, '--all'])
        check('verify 退 3 → 整轮 3 且 render 照跑',
              code == 3 and 'render' in stages_ran(), '%s / %s' % (code, stages_ran()))

        # --skip-verify：跳过但要留一句话，别让人以为查过了
        CODES.clear()
        code, out = run_capture([resume, '--run-dir', run_dir, '--all', '--skip-verify'])
        check('--skip-verify 退 0 且 verify 没跑',
              code == 0 and 'verify' not in stages_ran(), '%s / %s' % (code, stages_ran()))
        check('跳过时明确提醒要自己看', '自己逐份看' in out, out[:600])
        check('render 照跑', 'render' in stages_ran(), str(stages_ran()))

        # --allow / --only 要透传下去，否则人在 pipeline 上加了参数却不生效
        CODES.clear()
        run([resume, '--run-dir', run_dir, '--all', '--allow', 'PyTorch,nginx',
             '--only', '1'])
        vcmd = cmd_of('verify')
        check('--allow 透传给 verify', '--allow' in vcmd and 'PyTorch,nginx' in vcmd,
              pipeline.show(vcmd))
        check('--only 透传给 verify', '--only' in vcmd and '1' in vcmd,
              pipeline.show(vcmd))
        check('verify 不带 LLM 参数（它是纯 stdlib 的）',
              '--model' not in vcmd and '--api-key' not in vcmd, pipeline.show(vcmd))
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)
        os.remove(resume)

    print('\n' + '=' * 60)
    if failures:
        print('❌ %d 条断言失败：' % len(failures))
        for item in failures:
            print('  - %s' % item)
        return 1
    print('✅ 流水线编排测试全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
