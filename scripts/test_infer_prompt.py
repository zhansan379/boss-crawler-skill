# -*- coding: utf-8 -*-
"""爬取参数提示词的装配测试（prompts/crawl_params.st ↔ infer_params.py）。

提示词从 Python 里抽到 .st 之后多了一道缝：模板里的占位符和 Python 传的关键字必须
一一对上。对不上时 str.format 抛 KeyError，而这异常只在真的调模型那一刻才发生 ——
流水线跑到 infer 阶段才崩，前面几步的 token 已经花完了。所以这里在不发请求的前提下
把整段提示词装配一遍。

另一半盯的是「在校 → 实习 / 已毕业 → 全职」这条判定：它现在依赖传进去的今天的日期。
日期没进提示词的话，模型只能看简历措辞猜 —— 往届和在校的简历措辞是一样的，这种错
表现成「爬回来一批全职岗」，看不出是提示词的问题。

跑法：python scripts/test_infer_prompt.py
"""

import io
import os
import sys
import json
import inspect
import shutil
import tempfile
import contextlib
from string import Formatter
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

for _stream in (sys.stdout, sys.stderr):
    _stream.reconfigure(encoding='utf-8', errors='replace')

import infer_params as I
from resume_matcher.prompts import (
    load_prompt, get_crawl_params_prompt,
    get_resume_parse_prompt,
    get_match_analysis_prompt, get_greeting_prompt, get_optimize_prompt,
)

# 模板占位符的**唯一**期望集合。这一行就是那道缝的契约：模板里加了新占位符而
# 忘了在 prompts.py 的包装函数里传，或者反过来，都会在这里对不上。
EXPECTED_FIELDS = {'resume', 'kw_budget', 'salary', 'experience',
                   'degree', 'job_type', 'today'}

STUDENT = {
    'basic_info': {'name': '张三', 'expected_city': '西安',
                   'expected_position': 'AI应用开发实习生',
                   'availability': {'duration': '6个月'}},
    'education': {'school': '某大学', 'degree': '本科',
                  'start_year': '2023', 'graduation_year': '2027'},
    'experience': {'total_years': 0, 'companies': []},
    'skills': {'programming': ['Python']},
    # 几万字的简历原文：进了提示词就是白烧 token，profile_digest 应该把它丢掉
    'raw_text': '这是简历原文' * 5000,
}


def build_run_dir(profile=None):
    run_dir = tempfile.mkdtemp(prefix='infer_prompt_')
    json.dump(profile or STUDENT,
              open(os.path.join(run_dir, 'profile.json'), 'w', encoding='utf-8'),
              ensure_ascii=False)
    return run_dir


def run_infer(argv):
    """跑 infer_params.main()，返回 (退出码, 打印出来的全文)。永不发请求。"""
    old_argv, old_chat, old_resolve = sys.argv, I.chat_json, I.resolve
    sys.argv = ['infer_params.py'] + argv
    sent = []
    I.chat_json = lambda prompt, **k: sent.append(prompt) or {}
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            code = I.main()
    finally:
        sys.argv, I.chat_json, I.resolve = old_argv, old_chat, old_resolve
    return code, buf.getvalue()


def main():
    failures = []

    def check(label, cond, detail=''):
        print('%s %s%s' % ('  ✅' if cond else '  ❌', label,
                           '' if cond else '  ← ' + detail))
        if not cond:
            failures.append(label)

    print('=== 1. 模板与包装函数的占位符必须一一对上 ===')
    src = load_prompt('crawl_params')
    # 用 string.Formatter 解析而不是自己写正则：它就是 str.format 用的那个解析器，
    # {{ }} 的转义规则跟运行时完全一致，正则很容易把 JSON 示例里的括号也算进来。
    fields = {name for _, name, _, _ in Formatter().parse(src) if name}
    check('模板占位符 == 期望集合', fields == EXPECTED_FIELDS,
          '模板里是 %s' % sorted(fields))
    params = set(inspect.signature(get_crawl_params_prompt).parameters)
    check('包装函数的参数 == 模板占位符', params == fields,
          '函数参数 %s / 模板 %s' % (sorted(params), sorted(fields)))

    print('\n=== 2. 装配一遍：占位符全部替换掉，JSON 示例的括号还原成单层 ===')
    text = I.build_prompt(STUDENT, ['西安'], '2026-08-15')
    # 不能拿 Formatter 再解析装配结果：渲染完的 JSON 示例是真的单层括号，解析器会把
    # `{\n  "keywords"` 当成字段名报出来。要查的是那几个具体占位符还在不在。
    leftover = ['{%s}' % f for f in EXPECTED_FIELDS if '{%s}' % f in text]
    check('装配后没有残留占位符', not leftover, '还剩 %s' % leftover)
    check('JSON 示例是单层括号（{{ 还原成 {）',
          '{\n  "keywords"' in text and '{{' not in text,
          text[text.find('请只输出'):][:120])
    check('丢掉了 raw_text（几万字原文不进提示词）',
          '这是简历原文' not in text)
    check('保留了 education（判在校/已毕业的依据）', 'graduation_year' in text)

    print('\n=== 3. 今天的日期进了提示词，且判定规则真的引用它 ===')
    check('日期出现在提示词里', '2026-08-15' in text)
    check('日期出现两次以上（开头声明 + 约束里点名用它）',
          text.count('2026-08-15') >= 2,
          '只出现 %d 次' % text.count('2026-08-15'))
    rule = text[text.find('6. '):text.find('7. ')]
    check('约束 6 是拿日期跟毕业年份比，不是看简历措辞',
          'graduation_year' in rule and '2026-08-15' in rule, rule)
    for word in ('在校生', '应届生', '实习', '全职'):
        check('约束 6 覆盖了 %s' % word, word in rule)
    check('写明了毕业季按 6 月算（毕业年份 == 今年时唯一的判据）',
          '6 月' in rule, rule)
    check('说明了 job_type 不能填 null', 'null' in rule, rule)

    print('\n=== 4. 枚举值从 FILTER_LABELS 注入，不是写死在模板里 ===')
    for field in ('salary', 'experience', 'degree', 'job_type'):
        values = I.valid_values(field)
        missing = [v for v in values if v not in text]
        check('%s 的 %d 个合法值都印全了' % (field, len(values)),
              not missing, '缺 %s' % missing)
    # 约束 3 那四行必须是占位符而不是抄一份枚举：爬虫的 FILTER_LABELS 改了值，抄的那
    # 份不会报错，只会让模型继续给旧值，然后爬虫安静地少筛一批。
    for field in ('salary', 'experience', 'degree', 'job_type'):
        check('模板里 %s 是占位符注入' % field, '{%s}' % field in src,
              '模板里没有 {%s}' % field)

    print('\n=== 5. 关键词预算跟着城市冷热变 ===')
    hot = I.build_prompt(STUDENT, ['西安'], '2026-08-15')
    small = I.build_prompt(STUDENT, ['三门峡'], '2026-08-15')
    check('热门城市 %d 个' % I.HOT_CITY_KEYWORDS,
          '%d 个以内' % I.HOT_CITY_KEYWORDS in hot)
    check('小城市 %d 个' % I.SMALL_CITY_KEYWORDS,
          '%d 个以内' % I.SMALL_CITY_KEYWORDS in small)

    print('\n=== 6. --today 的取值与校验 ===')
    run_dir = build_run_dir()
    try:
        code, out = run_infer([run_dir, '--dry-run', '--today', '2027-03-01'])
        check('合法日期：退出码 0', code == 0, out)
        check('用的是给的日期而不是系统日期', '2027-03-01' in out, out)

        code, out = run_infer([run_dir, '--dry-run', '--today', '2026/8/15'])
        check('格式写错：退出码 1（不能就这么拼进提示词）', code == 1, out)
        check('报错里给了正确格式', 'YYYY-MM-DD' in out, out)
        check('报错里回显了收到的值', '2026/8/15' in out, out)

        code, out = run_infer([run_dir, '--dry-run', '--today', '明天'])
        check('中文日期也拦住', code == 1, out)

        code, out = run_infer([run_dir, '--dry-run'])
        check('不给 --today：默认取系统日期', code == 0 and
              date.today().isoformat() in out, out)
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)

    print('\n=== 7. 真的调模型时，日期已在提示词里（--dry-run 之外的路径） ===')
    run_dir = build_run_dir()
    old_resolve = I.resolve
    sent = []
    I.resolve = lambda **kw: type('C', (), {'model': 'm-test'})()
    old_chat = I.chat_json
    I.chat_json = lambda prompt, **k: (sent.append(prompt),
                                       {'keywords': ['AI应用开发'], 'cities': ['西安'],
                                        'job_type': ['实习'], 'experience': ['在校生']})[1]
    try:
        buf = io.StringIO()
        old_argv, sys.argv = sys.argv, ['infer_params.py', run_dir]
        try:
            with contextlib.redirect_stdout(buf):
                code = I.main()
        finally:
            sys.argv = old_argv
        out = buf.getvalue()
        check('退出码 0', code == 0, out)
        check('发出去的提示词带着今天的日期',
              bool(sent) and date.today().isoformat() in sent[0],
              '没发出提示词' if not sent else sent[0][:200])
        check('日志里印了按哪天判断（人能核对机器时钟对不对）',
              '判断在校/已毕业' in out and date.today().isoformat() in out, out)
    finally:
        I.resolve, I.chat_json = old_resolve, old_chat
        shutil.rmtree(run_dir, ignore_errors=True)

    print('\n=== 8. 另外 4 个模板没被动到 ===')
    for name in ('resume_parse', 'match_analysis',
                 'greeting', 'resume_optimize'):
        try:
            check('%s.st 还在且能加载' % name, bool(load_prompt(name)))
        except Exception as exc:                       # noqa: BLE001
            check('%s.st 还在且能加载' % name, False, str(exc))
    job = {'公司': 'A', '职位': 'B', '岗位要求和职责': 'C'}
    for label, fn in (('resume_parse', lambda: get_resume_parse_prompt('x')),
                      ('match_analysis', lambda: get_match_analysis_prompt('a', 'b')),
                      ('greeting', lambda: get_greeting_prompt(job, name='张三')),
                      ('resume_optimize', lambda: get_optimize_prompt(
                          'x', 'A', 'B', '10-20K', 'req', 80, '无', '无'))):
        try:
            check('%s 的包装函数仍可调用' % label, bool(fn()))
        except Exception as exc:                       # noqa: BLE001
            check('%s 的包装函数仍可调用' % label, False, repr(exc))

    print('\n' + '=' * 60)
    if failures:
        print('❌ %d 项未通过：' % len(failures))
        for label in failures:
            print('   - %s' % label)
        return 1
    print('✅ 全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
