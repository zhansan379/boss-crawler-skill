# -*- coding: utf-8 -*-
"""apply.py 的投递闸门测试。

核心断言只有一条，但它是这条流水线最重要的一条：**不给 --yes 时
auto_apply_jobs 的调用次数必须是 0**。其余用例覆盖「材料缺失一律不投」。

跑法：python tests/test_apply_gate.py
"""

import os
import sys
import io
import json
import shutil
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "deliver"))

for _stream in (sys.stdout, sys.stderr):
    _stream.reconfigure(encoding='utf-8', errors='replace')

CALLS = []


class Capture(io.StringIO):
    """收集 stdout 的缓冲区。

    apply.main() 一进来就 `sys.stdout.reconfigure(encoding='utf-8')`（Windows 控制台
    是 GBK），而 StringIO 没有 reconfigure —— 直接换成 StringIO 会 AttributeError。
    """

    def reconfigure(self, **kwargs):
        pass


def _fake_auto_apply(**kwargs):
    CALLS.append(kwargs)
    return [{'status': 'applied', 'company': 'XX科技', 'greeting_verified': True,
             'attachment_sent': True, 'link': kwargs['greetings'] and
             list(kwargs['greetings'])[0]}]


def build_run_dir(with_greeting=True, with_image=True):
    # 4 桶布局（paths.py 真源）：state/ 放 profile 与 qualified_jobs，materials/
    # 放 greeting_N，deliver/（#N-公司-岗位/）放简历 PNG。旧 test 写的裸
    # profile.json + generated/ + applications/ 是预 4 桶时代的路径，apply 读不到。
    run_dir = tempfile.mkdtemp(prefix='apply_gate_')
    state = os.path.join(run_dir, 'state')
    materials = os.path.join(run_dir, 'materials')
    os.makedirs(state)
    os.makedirs(materials)

    json.dump({'basic_info': {'name': '张三'}, 'skills': None, 'projects': None},
              open(os.path.join(state, 'profile.json'), 'w', encoding='utf-8'),
              ensure_ascii=False)

    jobs = [
        {'company': 'XX科技', 'position': 'Java开发',
         'link': 'https://zhipin.com/job/1', 'match_score': 88},
        {'company': 'YY公司', 'position': '全栈工程师',
         'link': 'https://zhipin.com/job/2', 'match_score': 80},
    ]
    json.dump(jobs, open(os.path.join(state, 'qualified_jobs.json'), 'w', encoding='utf-8'),
              ensure_ascii=False)

    if with_greeting:
        for i, tag in ((1, 'XX科技'), (2, 'YY公司')):
            path = os.path.join(materials, 'greeting_%d_%s.txt' % (i, tag))
            with open(path, 'w', encoding='utf-8') as f:
                f.write('26届本科可实习6个月，做过日均百万请求的服务重构，应聘贵司岗位。')

    if with_image:
        # deliver/ 目录带 #N- 前缀（方案一）。岗位序号 = qualified_jobs 里的 1-based 位次
        for i, (company, position) in enumerate(
                (('XX科技', 'Java开发'), ('YY公司', '全栈工程师')), 1):
            job_dir = os.path.join(run_dir, 'deliver', '#%d-%s-%s' % (i, company, position))
            os.makedirs(job_dir, exist_ok=True)
            with open(os.path.join(job_dir, '张三-%s.png' % position), 'wb') as f:
                f.write(b'\x89PNG\r\n\x1a\n' + b'x' * 5000)
    return run_dir


def run(argv, apply_module):
    CALLS.clear()
    sys.argv = ['apply.py'] + argv
    return apply_module.main()


def main():
    import apply as apply_module
    # 换掉真正的投递入口。测试永远不该碰浏览器。
    apply_module.auto_apply_jobs = _fake_auto_apply
    # 体检要 Pillow，且这里的 PNG 是假的；闸门本身不依赖体检结果
    apply_module.verify_images = lambda paths: (True, '', True)

    failures = []

    def check(label, cond, detail=''):
        print('%s %s%s' % ('  ✅' if cond else '  ❌', label,
                           '' if cond else '  ← ' + detail))
        if not cond:
            failures.append(label)

    print('=== 1. 不给 --yes：一次都不能调 auto_apply_jobs ===')
    run_dir = build_run_dir()
    try:
        code = run([run_dir], apply_module)
        check('退出码 0', code == 0, '实际 %s' % code)
        check('auto_apply_jobs 调用 0 次', len(CALLS) == 0, '实际 %d 次' % len(CALLS))
        check('没写 apply_log.json',
              not os.path.exists(os.path.join(run_dir, 'apply_log.json')))
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)

    print('\n=== 2. 给了 --yes：调 1 次，参数按 link 对齐 ===')
    run_dir = build_run_dir()
    try:
        code = run([run_dir, '--yes'], apply_module)
        check('退出码 0', code == 0, '实际 %s' % code)
        check('auto_apply_jobs 调用 1 次', len(CALLS) == 1, '实际 %d 次' % len(CALLS))
        if CALLS:
            kw = CALLS[0]
            links = {'https://zhipin.com/job/1', 'https://zhipin.com/job/2'}
            check('greetings 按 link 键入', set(kw['greetings']) == links,
                  str(set(kw['greetings'])))
            check('resume_file_path 按 link 键入',
                  isinstance(kw['resume_file_path'], dict)
                  and set(kw['resume_file_path']) == links)
            check('max_applications == 2', kw['max_applications'] == 2)
            check('output_dir 是 run_dir',
                  os.path.normpath(kw['output_dir']) == os.path.normpath(run_dir))
            check('profile 的 null 被洗成空 dict/list',
                  kw['_profile'].skills == {} and kw['_profile'].projects == [])
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)

    print('\n=== 3. 缺招呼语：拒绝，且一个都不投（不做投一半） ===')
    run_dir = build_run_dir(with_greeting=False)
    try:
        code = run([run_dir, '--yes'], apply_module)
        check('退出码 1', code == 1, '实际 %s' % code)
        check('auto_apply_jobs 调用 0 次', len(CALLS) == 0, '实际 %d 次' % len(CALLS))
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)

    print('\n=== 4. 缺简历图：默认拒绝，--no-image 可继续 ===')
    run_dir = build_run_dir(with_image=False)
    try:
        code = run([run_dir, '--yes'], apply_module)
        check('缺图时退出码 1', code == 1, '实际 %s' % code)
        check('缺图时没投', len(CALLS) == 0, '实际 %d 次' % len(CALLS))
        code = run([run_dir, '--yes', '--no-image'], apply_module)
        check('--no-image 后退出码 0', code == 0, '实际 %s' % code)
        check('--no-image 后投了 1 次', len(CALLS) == 1, '实际 %d 次' % len(CALLS))
        if CALLS:
            check('--no-image 时不传附件', CALLS[0]['resume_file_path'] is None)
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)

    print('\n=== 5. 只有一个岗位有招呼语时，--only 才放行 ===')
    run_dir = build_run_dir()
    try:
        os.remove([os.path.join(run_dir, 'materials', f)
                   for f in os.listdir(os.path.join(run_dir, 'materials'))
                   if f.startswith('greeting_2_')][0])
        code = run([run_dir, '--yes'], apply_module)
        check('#2 缺料时整批拒绝', code == 1 and not CALLS, '实际 code=%s calls=%d'
              % (code, len(CALLS)))
        code = run([run_dir, '--yes', '--only', '1'], apply_module)
        check('--only 1 放行', code == 0 and len(CALLS) == 1,
              '实际 code=%s calls=%d' % (code, len(CALLS)))
        if CALLS:
            check('只投了 #1', list(CALLS[0]['greetings']) == ['https://zhipin.com/job/1'])
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)

    print('\n=== 6. 招呼语寒暄开头：告警但不拦 ===')
    run_dir = build_run_dir()
    try:
        path = os.path.join(run_dir, 'materials', 'greeting_1_XX科技.txt')
        with open(path, 'w', encoding='utf-8') as f:
            f.write('您好，我是一名应届生，看到贵司在招后端，很感兴趣，希望有机会交流。')
        code = run([run_dir, '--yes'], apply_module)
        check('仍然投出去了', code == 0 and len(CALLS) == 1,
              '实际 code=%s calls=%d' % (code, len(CALLS)))
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)

    print('\n=== 7. --company：只投指定公司 ===')
    run_dir = build_run_dir()
    try:
        code = run([run_dir, '--yes', '--company', 'YY公司'], apply_module)
        check('全字匹配放行', code == 0 and len(CALLS) == 1,
              '实际 code=%s calls=%d' % (code, len(CALLS)))
        if CALLS:
            check('只投了 YY公司(#2)',
                  list(CALLS[0]['greetings']) == ['https://zhipin.com/job/2'],
                  str(list(CALLS[0]['greetings'])))

        code = run([run_dir, '--yes', '--company', 'YY'], apply_module)
        check('简称（子串）也能匹配', code == 0 and len(CALLS) == 1,
              '实际 code=%s calls=%d' % (code, len(CALLS)))

        code = run([run_dir, '--yes', '--company', 'XX科技,YY公司'], apply_module)
        check('两家都写就都投', code == 0 and len(CALLS) == 1
              and len(CALLS[0]['greetings']) == 2,
              '实际 code=%s calls=%d' % (code, len(CALLS)))
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)

    print('\n=== 8. --company 有一个名字拼错：整条命令失败，一个都不投 ===')
    run_dir = build_run_dir()
    try:
        code = run([run_dir, '--yes', '--company', 'XX科技,不存在的公司'], apply_module)
        check('退出码 1', code == 1, '实际 %s' % code)
        check('已命中的 XX科技 也没投', len(CALLS) == 0, '实际 %d 次' % len(CALLS))

        code = run([run_dir, '--yes', '--company', 'XX科技', '--only', '2'], apply_module)
        check('--company 与 --only 交集为空时拒绝', code == 1 and not CALLS,
              '实际 code=%s calls=%d' % (code, len(CALLS)))
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)

    print('\n=== 9. 演练必须列出可选公司和可选序号 ===')
    run_dir = build_run_dir()
    buf = Capture()
    try:
        stdout, sys.stdout = sys.stdout, buf
        try:
            code = run([run_dir], apply_module)
        finally:
            sys.stdout = stdout
        output = buf.getvalue()
        check('退出码 0 且没投', code == 0 and not CALLS,
              '实际 code=%s calls=%d' % (code, len(CALLS)))
        check('打印了 --company 用法', '--company "XX科技"' in output)
        check('打印了 --only 用法', '--only 1' in output)
        check('两家公司都列了出来',
              'XX科技' in output and 'YY公司' in output)
        check('序号和公司在同一行对得上',
              '#1 Java开发' in output and '#2 全栈工程师' in output)
        # 例子必须是本轮真实存在的序号：2 个岗位时不能出现 #3
        check('没有编造超出范围的序号', '--only 1,3' not in output)
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)

    print('\n=== 10. --greeting / --greeting-file：整批统一覆盖打招呼语 ===')
    run_dir = build_run_dir()
    try:
        TEXT = '统一定稿：26届可实习6个月，做过百万并发服务重构，盼面聊。'
        code = run([run_dir, '--yes', '--greeting', TEXT], apply_module)
        check('退出码 0', code == 0, '实际 %s' % code)
        check('投了 1 次', len(CALLS) == 1, '实际 %d 次' % len(CALLS))
        if CALLS:
            texts = set(CALLS[0]['greetings'].values())
            check('两个岗位都用整批这一条', texts == {TEXT},
                  '实际 %s' % texts)

        # --greeting-file：正文从文件读，同样整批覆盖
        gf = os.path.join(run_dir, 'batch_greeting.txt')
        with open(gf, 'w', encoding='utf-8') as f:
            f.write('文件版统一定稿：盼面聊。')
        code = run([run_dir, '--yes', '--greeting-file', gf], apply_module)
        check('--greeting-file 退出码 0', code == 0, '实际 %s' % code)
        if CALLS:
            texts = set(CALLS[0]['greetings'].values())
            check('--greeting-file 也整批覆盖', texts == {'文件版统一定稿：盼面聊。'},
                  '实际 %s' % texts)

        # 防御：空招呼语必须拦下，不能把空话发出去
        code = run([run_dir, '--yes', '--greeting', '   '], apply_module)
        check('空 --greeting 拒绝', code == 1 and not CALLS,
              '实际 code=%s calls=%d' % (code, len(CALLS)))

        # 防御：两个开关都给就拒绝，不做任何投机取巧
        code = run([run_dir, '--yes', '--greeting', TEXT, '--greeting-file', gf],
                   apply_module)
        check('--greeting 与 --greeting-file 同给拒绝', code == 1 and not CALLS,
              '实际 code=%s calls=%d' % (code, len(CALLS)))
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)

    print('\n' + '=' * 60)
    if failures:
        print('❌ %d 项失败：%s' % (len(failures), '；'.join(failures)))
        return 1
    print('✅ 投递闸门全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
