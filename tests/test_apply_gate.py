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
    run_dir = tempfile.mkdtemp(prefix='apply_gate_')
    os.makedirs(os.path.join(run_dir, 'generated'))

    json.dump({'basic_info': {'name': '张三'}, 'skills': None, 'projects': None},
              open(os.path.join(run_dir, 'profile.json'), 'w', encoding='utf-8'),
              ensure_ascii=False)
    json.dump(
        [{'company': 'XX科技', 'position': 'Java开发', 'link': 'https://zhipin.com/job/1',
          'match_score': 88},
         {'company': 'YY公司', 'position': '全栈工程师', 'link': 'https://zhipin.com/job/2',
          'match_score': 80}],
        open(os.path.join(run_dir, 'qualified_jobs.json'), 'w', encoding='utf-8'),
        ensure_ascii=False)

    if with_greeting:
        for i, tag in ((1, 'XX科技'), (2, 'YY公司')):
            path = os.path.join(run_dir, 'generated', 'greeting_%d_%s.txt' % (i, tag))
            with open(path, 'w', encoding='utf-8') as f:
                f.write('26届本科可实习6个月，做过日均百万请求的服务重构，应聘贵司岗位。')

    if with_image:
        for company, position in (('XX科技', 'Java开发'), ('YY公司', '全栈工程师')):
            job_dir = os.path.join(run_dir, 'applications', '%s-%s' % (company, position))
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
        os.remove([os.path.join(run_dir, 'generated', f)
                   for f in os.listdir(os.path.join(run_dir, 'generated'))
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
        path = os.path.join(run_dir, 'generated', 'greeting_1_XX科技.txt')
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

    print('\n' + '=' * 60)
    if failures:
        print('❌ %d 项失败：%s' % (len(failures), '；'.join(failures)))
        return 1
    print('✅ 投递闸门全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
