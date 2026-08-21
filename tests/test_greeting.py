#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""招呼语生成规则的回归锁。

盯的是两件容易被改回去的事：

1. **前 15 个字不能是客套话。** BOSS 直聘的消息列表预览框只显示前 15 个字，HR 在
   一屏未读里扫的就是这一截。原来的模板开头是「您好，我是XXX，对贵公司的「YYY」
   岗位非常感兴趣。」—— 预览框里 15 个字全是客套，等于没投。

2. **到岗时间/时长/出勤不许编。** 这三样是对方会照着安排工位和排期的承诺，简历没
   写就不能替用户猜一个。测试里给一份完全没有 availability 的简历，断言输出里不
   出现任何到岗承诺的字样。

用法：python tests/test_greeting.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

from resume_matcher.config import ResumeProfile
from resume_matcher.auto_apply import (
    generate_greeting, preview, has_wasted_preview, _lead,
    _default_greeting, _compact_greeting, PREVIEW_LEN,
)
from resume_matcher.prompts import get_greeting_prompt

FAILURES = []


def check(label, ok, extra=''):
    print(('  [OK] ' if ok else '  [FAIL] ') + label + (('  ' + extra) if extra else ''))
    if not ok:
        FAILURES.append(label)


JOB = {
    '公司': '某某科技',
    '职位': 'Python后端开发工程师',
    '薪资': '15-25K',
    '经验': '1-3年',
    '学历': '本科',
    '技能标签': 'Python,Django,MySQL',
    '岗位要求和职责': '负责后端服务开发，要求熟悉 Python、Django，了解 MySQL 优化。',
}


def _profile(**over):
    base = dict(
        basic_info={'name': '张三'},
        education={'school': '西安交通大学', 'degree': '本科', 'major': '计算机科学',
                   'graduation_year': '2026'},
        experience={'total_years': None, 'companies': []},
        skills={'programming': ['Python'], 'frameworks': ['Django'],
                'tools': ['MySQL'], 'other': []},
        projects=[{'name': '订单中台', 'description': '基于 Django 的订单服务',
                   'tech_stack': ['Python', 'Django']}],
    )
    base.update(over)
    return ResumeProfile(**base)


# ── [1] 前 15 个字 ──────────────────────────────────────────────────
def test_preview_not_wasted():
    print('\n[1] 前 15 个字不是客套话')

    g = generate_greeting(_profile(), JOB)
    head = preview(g)
    check('模板模式的前 15 字不以客套话开头', not has_wasted_preview(g), repr(head))
    check('前 15 字里有实质信息（学校/年限/技能之一）',
          any(k in head for k in ('届', '年', '西安交通大学', 'Python')), repr(head))
    check('preview() 就是 %d 个字' % PREVIEW_LEN, len(head) <= PREVIEW_LEN, repr(head))

    # 有工作年限 → 社招公式
    g2 = generate_greeting(_profile(experience={'total_years': 3, 'companies': []}), JOB)
    check('社招：前 15 字带出年限', '3年' in preview(g2), repr(preview(g2)))
    check('社招前 15 字不浪费', not has_wasted_preview(g2))

    # 兜底路径也不能浪费预览框
    check('_default_greeting 前 15 字不浪费', not has_wasted_preview(_default_greeting(JOB)),
          repr(preview(_default_greeting(JOB))))
    compact = _compact_greeting('x', '张三', 'Python后端开发工程师', ['Python', 'Django'])
    check('_compact_greeting 前 15 字不浪费', not has_wasted_preview(compact),
          repr(preview(compact)))

    check('has_wasted_preview 能抓到反例',
          has_wasted_preview('您好，我是张三，对贵公司的岗位非常感兴趣'))


# ── [2] 到岗信息：给了才用，没给不猜 ────────────────────────────────
def test_availability_never_fabricated():
    print('\n[2] 到岗/时长/出勤：简历没写就不出现')

    g = generate_greeting(_profile(), JOB)          # availability 完全缺失
    banned = ('到岗', '可实习', '每周', '周5天', '随时')
    hit = [w for w in banned if w in g]
    check('无 availability 时不出现任何到岗承诺', not hit, '命中: %s' % hit)

    # 显式 null（profile.json 真实写法：键存在、值是 null）
    g_null = generate_greeting(
        _profile(basic_info={'name': '张三', 'availability': {
            'can_start': None, 'duration': None, 'days_per_week': None}}), JOB)
    check('availability 三项均为 null 时同样不出现', not any(w in g_null for w in banned))

    # availability 不是 dict（手改坏了）也不能崩
    for bad in ('随时到岗', [], 0, True):
        try:
            out = generate_greeting(
                _profile(basic_info={'name': '张三', 'availability': bad}), JOB)
            ok = bool(out)
        except Exception as e:
            ok = False
            out = '%s: %s' % (type(e).__name__, e)
        check('availability=%r 不抛异常' % (bad,), ok, str(out)[:40])


def test_availability_used_when_given():
    print('\n[3] 给了到岗信息就顶到最前面')

    p = _profile(basic_info={'name': '张三', 'availability': {
        'can_start': '下周一', 'duration': None, 'days_per_week': None}})
    g = generate_greeting(p, JOB)
    check('can_start 进了前 15 字', '下周一' in preview(g), repr(preview(g)))
    check('到岗时间排在学校前面', preview(g).index('下周一') < 5, repr(preview(g)))

    p2 = _profile(basic_info={'name': '张三', 'availability': {
        'can_start': None, 'duration': '6个月', 'days_per_week': '5天'}})
    head2 = preview(generate_greeting(p2, JOB))
    check('时长和出勤都进了前 15 字', '6个月' in head2 and '5天' in head2, repr(head2))

    # _lead 单独跑一遍：应届无年限 → 届别 + 学校
    lead = _lead(_profile(), ['Python'])
    check('应届无 availability → 届别公式', '26届' in lead, repr(lead))


# ── [4] 收尾与硬编码 ───────────────────────────────────────────────
def test_closing_and_no_hardcoded_domain():
    print('\n[4] 收尾委婉 + 不带死领域词')

    g = generate_greeting(_profile(), JOB)
    check('收尾是委婉请求发简历', '方便看一下吗' in g, g[-20:])
    check('不再用「期待您的回复」这种放哪都成立的空话', '期待您的回复' not in g)

    compact = _compact_greeting('x', '张三', 'Java开发', ['Java', 'Spring'])
    check('_compact_greeting 不再硬编码「AI应用」', 'AI应用' not in compact, compact)

    check('长度在 BOSS 聊天上限内', len(g) <= 300, '%d 字' % len(g))


# ── [5] 提示词模板 ─────────────────────────────────────────────────
def test_prompt_template():
    print('\n[5] greeting.st 能加载并填满（availability 有值走动态段）')

    prompt = get_greeting_prompt(JOB, resume='张三 男 应届 复旦硕士',
                                 match_reasons='技能命中 3 项',
                                 availability='可到岗：随时、每周出勤：5天')
    check('模板渲染出内容', len(prompt) > 500, '%d 字符' % len(prompt))
    check('岗位信息填进去了', '某某科技' in prompt and 'Python后端开发工程师' in prompt)
    check('没有漏填的占位符', '{' not in prompt and '}' not in prompt)
    check('availability 值进了到岗段', '我实际的到岗安排' in prompt and '随时' in prompt and '5天' in prompt)
    for rule in ('15 个字', '不许编造', '校招'):
        check('模板包含规则「%s」' % rule, rule in prompt)


# ── [6] availability 动态组装 ──────────────────────────────────────
def test_availability_dynamic_assembly():
    print('\n[6] availability 动态组装：只有存在数据才带日期相关内容')

    _RESUME = '张三 男 应届 复旦硕士'
    AVAIL_FILL = '可到岗：2026-09-01、每周出勤：3天'   # 独特值，模板「前15字公式」例子里绝不出现
    p_empty = get_greeting_prompt(JOB, resume=_RESUME, match_reasons='技能命中 3 项',
                                  availability='')
    check('空 availability → 到岗数据段头（权威来源）不出现',
          '我实际的到岗安排（权威来源）' not in p_empty)
    check('空 availability → 到岗数据段措辞不出现', '栏里给了值就照写' not in p_empty)
    check('空 availability → 到岗值不泄露进提示词', '2026-09-01' not in p_empty)
    check('空 availability → 防编造规则仍保留', '不许编造的三件事' in p_empty)

    p_filled = get_greeting_prompt(JOB, resume=_RESUME, match_reasons='技能命中 3 项',
                                   availability=AVAIL_FILL)
    check('有 availability → 到岗段头出现', '我实际的到岗安排（权威来源）' in p_filled)
    check('有 availability → 值进提示词', '2026-09-01' in p_filled and '3天' in p_filled)
    check('两种情况都无残留占位',
          '{' not in p_empty and '}' not in p_empty
          and '{' not in p_filled and '}' not in p_filled)


def main():
    print('=' * 60)
    print('招呼语生成规则测试')
    print('=' * 60)
    test_preview_not_wasted()
    test_availability_never_fabricated()
    test_availability_used_when_given()
    test_closing_and_no_hardcoded_domain()
    test_prompt_template()
    test_availability_dynamic_assembly()

    print('\n' + '=' * 60)
    if FAILURES:
        print('失败 %d 项：' % len(FAILURES))
        for f in FAILURES:
            print('  - ' + f)
        return 1
    print('全部通过')
    return 0


if __name__ == '__main__':
    for _stream in (sys.stdout, sys.stderr):      # Windows 控制台是 GBK
        _stream.reconfigure(encoding='utf-8', errors='replace')
    sys.exit(main())
