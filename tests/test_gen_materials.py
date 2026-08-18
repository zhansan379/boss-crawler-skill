# -*- coding: utf-8 -*-
"""gen_materials.py 的测试：产物文件名必须被**真实的**下游认出来。

这套测试盯的是三个接缝，每一个坏掉都不报错、只是静默出错：

  · 文件名契约。`check_artifacts.check` 只认 `{kind}_{i}_` 前缀且文件非空；
    `write_application_md.resolve_greeting` 用 `glob` 回找 `greeting_{i}_*.txt`。
    公司名里带 `[` 时 glob 会把它当字符集，文件永远匹配不上 —— 于是投递材料里
    招呼语一栏是空的，而生成阶段显示「全部成功」。
  · 序号对齐。`{i}` 是岗位在 qualified_jobs.json 里的 1-based 位置，下游全靠它把
    招呼语、简历、岗位三者对上。序号挪一位 = 把 A 公司的招呼语发给 B 公司。
    所以哪怕中间某个岗位生成失败，其余岗位的序号也不能顺移。
  · 非空 + 原子写。0 字节的壳子会通过「文件存在」这种判断，然后被渲染成一张空白
    简历投出去。

所以这里不 mock check_artifacts，也不 mock write_application_md 的回找逻辑 ——
真的调它们。只有 LLM 请求是假的。

跑法：python tests/test_gen_materials.py
"""

import os
import sys
import json
import shutil
import tempfile
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "stages"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "deliver"))

for _stream in (sys.stdout, sys.stderr):
    _stream.reconfigure(encoding='utf-8', errors='replace')

import gen_materials as GM
import write_application_md as WAM
from check_artifacts import check as real_check
from llm.config import LLMConfig
from resume_matcher.auto_apply import has_wasted_preview

FAKE_CFG = LLMConfig(base_url='https://fake.local/v1', api_key='sk-fake',
                     model='m-test', concurrency=3, max_retries=0)

PROFILE = {
    'basic_info': {'name': '张三', 'city': '太原', 'phone': '13800000000',
                   'email': 'zhangsan@example.com', 'status': '离职-随时到岗',
                   'availability': {'can_start': '随时', 'duration': '6个月',
                                    'days_per_week': '5天'}},
    'education': {'school': '某大学', 'degree': '本科', 'major': '计算机科学与技术',
                  'graduation_year': 2026},
    'experience': {'total_years': 1,
                   'companies': [{'name': '某科技', 'position': '后端实习',
                                  'duration': '2025.03-2025.09',
                                  'highlights': ['订单查询 P99 从 800ms 降到 120ms',
                                                 '写了三个内部工具']}]},
    'skills': {'语言': ['Python', 'Java'], '框架': ['FastAPI'], 'AI': ['RAG', 'LangChain']},
    'projects': [{'name': '智能问答系统', 'role': '主力开发', 'tech_stack': 'Python/FAISS',
                  'description': '基于 RAG 的企业知识库问答',
                  'highlights': ['检索召回率提升 30%']}],
    'awards': [], 'publications': [], 'social_links': {},
    'salary_expectation': {'min': 10, 'max': 20, 'unit': 'K'},
    'keywords': ['Python', 'RAG'], 'raw_text': '（原文略）',
}

# 第三个公司名故意带方括号和空格：glob 的字符集陷阱就在这
JOBS = [
    {'link': 'https://www.zhipin.com/job_detail/aaaaaaa1.html', '公司': '甲公司',
     '职位': 'AI应用开发工程师', '城市': '西安', '薪资': '15-25K', '经验': '1-3年',
     '学历': '本科', '技能标签': 'Python,RAG', '岗位要求和职责': '负责 RAG 系统研发。',
     '公司信息': '甲公司简介', 'source_file': 'a.csv'},
    {'link': 'https://www.zhipin.com/job_detail/bbbbbbb2.html', '公司': '乙公司',
     '职位': 'Python开发', '城市': '太原', '薪资': '10-15K', '经验': '经验不限',
     '学历': '本科', '技能标签': 'Python,Django', '岗位要求和职责': '负责后台开发。',
     '公司信息': '', 'source_file': 'a.csv'},
    {'link': 'https://www.zhipin.com/job_detail/ccccccc3.html',
     '公司': '丙[科技] 有限公司/西安分公司', '职位': '算法工程师', '城市': '西安',
     '薪资': '20-30K', '经验': '1-3年', '学历': '硕士', '技能标签': 'PyTorch',
     '岗位要求和职责': '负责算法研发。', '公司信息': '', 'source_file': 'b.csv'},
]

GREETINGS = {
    '甲公司': '随时可到岗，做过 RAG 问答系统，检索召回率提升 30%，想投这个岗位。',
    '乙公司': '一年后端经验，把订单查询 P99 从 800ms 压到 120ms，Python/FastAPI 熟练。',
    '丙[科技]': '2026 届本科，Python 与 RAG 方向，做过企业知识库问答系统。',
}

RESUME_JSON = {
    'optimized_resume': '# 张三\n\n## 教育背景\n某大学 本科 计算机科学与技术\n\n'
                        '## 项目经历\n智能问答系统：基于 RAG 的企业知识库问答，召回率提升 30%。\n'
                        '## 专业技能\nPython、FastAPI、RAG、LangChain\n',
    'changes': ['把 RAG 项目提到最前'],
    'keywords_added': ['RAG', '向量检索'],
    'match_improvement': '+12',
}


def company_of(text):
    for key in GREETINGS:
        if key in text:
            return key
    return None


def make_stubs(fail_for=(), bad_preview_for=(), record=None):
    """返回 (stub_chat, stub_chat_json)。fail_for 里的公司名会抛错。"""

    def stub_chat(prompt=None, messages=None, stage=None, run_dir=None, cfg=None, **kw):
        text = prompt or ''
        if messages:                                  # 前 15 字重写那一轮
            text = '\n'.join(m.get('content', '') for m in messages)
        company = company_of(text)
        if company is None:
            raise AssertionError('招呼语提示词里认不出公司：%r' % text[:200])
        if record is not None:
            record.append(('greeting', company, bool(messages)))
        if company in fail_for:
            raise GM.LLMError('模型返回了空招呼语（测试注入）')
        if company in bad_preview_for and not messages:
            # 第一轮故意把前 15 字浪费掉，逼出重写那一轮
            return '您好，我对贵公司的这个岗位非常感兴趣，希望能有机会交流。'
        return GREETINGS[company]

    def stub_chat_json(prompt=None, stage=None, run_dir=None, cfg=None, **kw):
        company = company_of(prompt or '')
        if company is None:
            raise AssertionError('简历提示词里认不出公司：%r' % (prompt or '')[:200])
        if record is not None:
            record.append(('resume', company, False))
        if company in fail_for:
            raise GM.LLMError('optimized_resume 缺失（测试注入）')
        data = dict(RESUME_JSON)
        data['company'] = company
        return data

    return stub_chat, stub_chat_json


def prepare(run_dir, jobs=None, extra=None):
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, 'qualified_jobs.json'), 'w', encoding='utf-8') as f:
        json.dump(jobs if jobs is not None else JOBS, f, ensure_ascii=False, indent=2)
    with open(os.path.join(run_dir, 'profile.json'), 'w', encoding='utf-8') as f:
        json.dump(PROFILE, f, ensure_ascii=False, indent=2)
    for name, payload in (extra or {}).items():
        with open(os.path.join(run_dir, name), 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False)
    return run_dir


def run_main(argv, stubs=None, record=None):
    stub_chat, stub_chat_json = stubs or make_stubs(record=record)
    saved = (sys.argv, GM.chat, GM.chat_json, GM.resolve)
    sys.argv = ['gen_materials.py'] + argv
    GM.chat, GM.chat_json = stub_chat, stub_chat_json
    GM.resolve = lambda **kw: FAKE_CFG          # 不碰开发机上真实的 llm_config.json
    try:
        return GM.main()
    finally:
        sys.argv, GM.chat, GM.chat_json, GM.resolve = saved


def gen_names(run_dir):
    gen = os.path.join(run_dir, 'generated')
    return sorted(os.listdir(gen)) if os.path.isdir(gen) else []


def read_text(path):
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


def main():
    failures = []

    def check(label, cond, detail=''):
        print('%s %s%s' % ('  ✅' if cond else '  ❌', label,
                           '' if cond else '  ← ' + str(detail)[:400]))
        if not cond:
            failures.append(label)

    tmp = tempfile.mkdtemp(prefix='mat_test_')
    try:
        # ================================================================
        print('=== 1. safe_name：文件名不能炸文件系统，也不能炸 glob ===')
        check('斜杠冒号被替换',
              GM.safe_name('甲/乙:丙') == '甲_乙_丙', GM.safe_name('甲/乙:丙'))
        # [ ] 不是文件系统非法字符，但 write_application_md 用 glob 回找产物
        name = GM.safe_name('丙[科技] 有限公司')
        check('方括号被替换（glob 会把它当字符集）',
              '[' not in name and ']' not in name, name)
        check('空格被去掉', ' ' not in name, name)
        check('按 limit 截断', len(GM.safe_name('公司' * 40)) == 24,
              len(GM.safe_name('公司' * 40)))
        check('空名字给 unknown', GM.safe_name('') == 'unknown')
        check('None 给 unknown', GM.safe_name(None) == 'unknown')
        check('换行制表被替换', '\n' not in GM.safe_name('甲\n乙\t丙'))

        # ================================================================
        print('\n=== 2. parse_only：序号解析 ===')
        check('单个与逗号', GM.parse_only('1,3', 5) == {1, 3})
        check('区间', GM.parse_only('2-4', 5) == {2, 3, 4})
        check('混合 + 空格', GM.parse_only(' 1, 3-5 ', 5) == {1, 3, 4, 5})
        check('None 表示全做', GM.parse_only(None, 5) is None)
        for bad, why in (('9', '超出上界'), ('0', '0 不是 1-based'), ('a', '不是数字')):
            try:
                GM.parse_only(bad, 5)
                check('拒绝非法输入（%s）' % why, False, '没抛')
            except ValueError:
                check('拒绝非法输入（%s）' % why, True)

        # ================================================================
        print('\n=== 3. 招呼语素材：不能把联系方式塞进招呼语 ===')
        summary = GM.build_resume_summary(PROFILE)
        check('带教育与技能', '某大学' in summary and 'FastAPI' in summary, summary[:150])
        check('带经历数字', '120ms' in summary, summary)
        # 招呼语里带电话邮箱既没用（平台内已通）又是泄露
        check('不含电话', '13800000000' not in summary, summary)
        check('不含邮箱', 'example.com' not in summary, summary)
        check('按 limit 截断', len(GM.build_resume_summary(PROFILE, limit=60)) <= 60)

        avail = GM.format_availability(PROFILE)
        check('到岗信息拼成一行', '随时' in avail and '5天' in avail, avail)
        check('没写 availability 就给空串',
              GM.format_availability({'basic_info': {}}) == '')
        check('availability 不是 dict 也不炸',
              GM.format_availability({'basic_info': {'availability': '随时'}}) == '')

        # ================================================================
        print('\n=== 4. 优化基底：resume_text.txt > profile 通用稿 ===')
        base_dir = os.path.join(tmp, 'base')
        os.makedirs(base_dir)
        text, source = GM.load_resume_text(base_dir, PROFILE)
        check('没有 resume_text.txt 时用 profile 生成', 'profile.json' in source, source)
        check('通用稿含项目与技能', '智能问答系统' in text and 'FastAPI' in text, text[:200])

        with open(os.path.join(base_dir, 'resume_text.txt'), 'w', encoding='utf-8') as f:
            f.write('这是一份真实的简历原文，' * 10)
        text, source = GM.load_resume_text(base_dir, PROFILE)
        check('有 resume_text.txt 就优先用它', source == 'resume_text.txt', source)

        with open(os.path.join(base_dir, 'resume_text.txt'), 'w', encoding='utf-8') as f:
            f.write('太短')
        _, source = GM.load_resume_text(base_dir, PROFILE)
        check('resume_text.txt 太短视为没有（免得优化一份空简历）',
              'profile.json' in source, source)
        try:
            GM.load_resume_text(base_dir, {'basic_info': {'name': 'x'}})
            check('profile 也空时拒绝（不能请模型凭空编简历）', False, '没抛')
        except GM.LLMError:
            check('profile 也空时拒绝（不能请模型凭空编简历）', True)

        # ================================================================
        print('\n=== 5. 全流程：产物被真实 check_artifacts 认出 ===')
        run_dir = prepare(os.path.join(tmp, 'run_full'))
        record = []
        code = run_main([run_dir, '--workers', '3'], record=record)
        check('全部成功退出码 0', code == 0, code)
        check('3 岗位 × 2 类 = 6 次调用', len(record) == 6, record)

        names = gen_names(run_dir)
        check('写出 6 个文件', len(names) == 6, names)
        check('没有残留的 .part（原子写）',
              not any(n.endswith('.part') for n in names), names)
        check('招呼语是 .txt、简历是 .json',
              sorted(n.split('_')[0] for n in names) ==
              ['greeting'] * 3 + ['resume'] * 3, names)
        check('文件都非空', all(os.path.getsize(os.path.join(run_dir, 'generated', n)) > 0
                              for n in names))

        # 真实 check_artifacts
        jobs, found, missing = real_check(run_dir, ['greeting', 'resume'])
        check('真实 check_artifacts：一项不缺', not missing, missing)
        check('真实 check_artifacts：认出 6 个', len(found) == 6, found)

        # 序号对齐：每个岗位拿到的必须是自己那份
        for index, key in ((1, '甲公司'), (2, '乙公司'), (3, '丙[科技]')):
            hit = [n for n in names if n.startswith('greeting_%d_' % index)]
            body = read_text(os.path.join(run_dir, 'generated', hit[0])) if hit else ''
            check('greeting_%d 的内容属于 %s' % (index, key),
                  body.strip() == GREETINGS[key], body[:80])
            hit = [n for n in names if n.startswith('resume_%d_' % index)]
            data = json.loads(read_text(os.path.join(run_dir, 'generated', hit[0])))
            check('resume_%d 的内容属于 %s' % (index, key), data.get('company') == key,
                  data.get('company'))
            check('resume_%d 保留了整个 JSON 对象（下游要 changes/keywords_added）' % index,
                  'optimized_resume' in data and 'changes' in data, sorted(data))

        # 真实的 write_application_md 回找逻辑（glob）—— 带方括号的公司名是重点
        fake_args = argparse.Namespace(greeting=None, greeting_file=None)
        for index, key in ((1, '甲公司'), (2, '乙公司'), (3, '丙[科技]')):
            body, path = WAM.resolve_greeting(run_dir, index, fake_args)
            check('真实 resolve_greeting 找得到 #%d（%s）' % (index, key),
                  body.strip() == GREETINGS[key], (path, body[:60]))

        check('耗时进了 run_timings.jsonl',
              'gen_materials' in read_text(os.path.join(run_dir, 'run_timings.jsonl')))

        # ================================================================
        print('\n=== 6. 部分失败：序号绝不顺移 ===')
        run_dir = prepare(os.path.join(tmp, 'run_partial'))
        code = run_main([run_dir], make_stubs(fail_for=('乙公司',)))
        check('部分失败退出码 3', code == 3, code)
        names = gen_names(run_dir)
        check('失败的 2 号一个文件都没有',
              not any(n.startswith('greeting_2_') or n.startswith('resume_2_')
                      for n in names), names)
        check('1 号和 3 号照常写出',
              any(n.startswith('greeting_1_') for n in names)
              and any(n.startswith('greeting_3_') for n in names), names)
        # 关键：3 号没有因为 2 号失败就变成 2 号
        hit = [n for n in names if n.startswith('greeting_3_')]
        check('3 号还是 3 号（序号没顺移）',
              read_text(os.path.join(run_dir, 'generated', hit[0])).strip()
              == GREETINGS['丙[科技]'], hit)
        _, _, missing = real_check(run_dir, ['greeting', 'resume'])
        check('check_artifacts 报出缺的正是 #2 两项', len(missing) == 2
              and all('#2' in m for m in missing), missing)
        check('缺项信息里带公司名（便于人判断要不要补）',
              all('乙公司' in m for m in missing), missing)

        # 重跑补齐：已有的跳过，只补失败的
        record = []
        code = run_main([run_dir], record=record)
        check('重跑退出码 0', code == 0, code)
        check('只补了 2 号的两项', len(record) == 2, record)
        check('补的是乙公司', all(c == '乙公司' for _, c, _ in record), record)
        _, _, missing = real_check(run_dir, ['greeting', 'resume'])
        check('补齐后 check_artifacts 通过', not missing, missing)

        # ================================================================
        print('\n=== 7. 已有产物默认跳过、--force 覆盖 ===')
        run_dir = prepare(os.path.join(tmp, 'run_skip'))
        run_main([run_dir])
        record = []
        code = run_main([run_dir], record=record)
        check('全都有了就不再调模型', code == 0 and not record, (code, record))

        target = os.path.join(run_dir, 'generated',
                              [n for n in gen_names(run_dir)
                               if n.startswith('greeting_1_')][0])
        with open(target, 'w', encoding='utf-8') as f:
            f.write('人工改过的招呼语')
        record = []
        run_main([run_dir], record=record)
        check('人工改过的产物不会被默默覆盖',
              read_text(target) == '人工改过的招呼语' and not record, read_text(target))
        record = []
        run_main([run_dir, '--force'], record=record)
        check('--force 才覆盖', read_text(target).strip() == GREETINGS['甲公司'],
              read_text(target)[:60])
        check('--force 重做全部 6 项', len(record) == 6, len(record))

        # 0 字节的壳子不算落盘：否则空简历会被渲染出去
        with open(target, 'w', encoding='utf-8') as f:
            pass
        record = []
        run_main([run_dir], record=record)
        check('0 字节文件视为缺失并重生成',
              os.path.getsize(target) > 0 and len(record) == 1, (os.path.getsize(target),
                                                                record))

        # ================================================================
        print('\n=== 8. --only / --dry-run / 两个 skip ===')
        run_dir = prepare(os.path.join(tmp, 'run_only'))
        record = []
        code = run_main([run_dir, '--only', '1,3'], record=record)
        check('--only 1,3 只做 4 项', len(record) == 4, record)
        check('--only 没做 2 号',
              not any(n.startswith('greeting_2_') for n in gen_names(run_dir)),
              gen_names(run_dir))
        check('--only 之下退出码 0（没选的不算失败）', code == 0, code)

        run_dir = prepare(os.path.join(tmp, 'run_dry'))

        def boom(*a, **k):
            raise AssertionError('--dry-run 竟然发起了请求')

        code = run_main([run_dir, '--dry-run'], (boom, boom))
        check('--dry-run 退出码 0 且不请求', code == 0, code)
        check('--dry-run 一个文件都不写', gen_names(run_dir) == [], gen_names(run_dir))

        code = run_main([run_dir, '--greeting-mode', 'skip', '--resume-mode', 'skip'],
                        (boom, boom))
        check('两个都 skip 时报错退出 1（没有要做的事）', code == 1, code)

        run_dir = prepare(os.path.join(tmp, 'run_greet_only'))
        record = []
        code = run_main([run_dir, '--resume-mode', 'skip'], record=record)
        check('--resume-mode skip 只出招呼语', len(record) == 3
              and all(kind == 'greeting' for kind, _, _ in record), record)
        check('只出招呼语时也算齐全（退出码 0）', code == 0, code)
        _, _, missing = real_check(run_dir, ['greeting'])
        check('按 greeting 一种口径校验通过', not missing, missing)

        # --greeting-mode default 走离线规则模板，一次都不该调模型
        run_dir = prepare(os.path.join(tmp, 'run_default'))
        calls = []

        def count_chat(*a, **k):
            calls.append(1)
            raise AssertionError('default 模式不该调模型出招呼语')

        code = run_main([run_dir, '--greeting-mode', 'default', '--resume-mode', 'skip'],
                        (count_chat, count_chat))
        check('--greeting-mode default 不调模型', code == 0 and not calls, (code, calls))
        names = gen_names(run_dir)
        check('模板模式照样写出 3 个招呼语', len(names) == 3, names)
        body = read_text(os.path.join(run_dir, 'generated', names[0]))
        check('模板招呼语非空', len(body.strip()) > 20, body[:60])

        # ================================================================
        print('\n=== 9. 前 15 字被客套话占掉时重写一次 ===')
        run_dir = prepare(os.path.join(tmp, 'run_preview'))
        record = []
        code = run_main([run_dir, '--resume-mode', 'skip'],
                        make_stubs(bad_preview_for=('甲公司',), record=record))
        check('重写路径下退出码 0', code == 0, code)
        check('甲公司问了两轮（原轮 + 重写轮）',
              [r for r in record if r[1] == '甲公司'] ==
              [('greeting', '甲公司', False), ('greeting', '甲公司', True)],
              [r for r in record if r[1] == '甲公司'])
        check('其他两个只问一轮', len(record) == 4, record)
        hit = [n for n in gen_names(run_dir) if n.startswith('greeting_1_')][0]
        body = read_text(os.path.join(run_dir, 'generated', hit))
        check('落盘的是重写后的版本', body.strip() == GREETINGS['甲公司'], body[:60])
        check('落盘的前 15 字不再是客套话（用真实判定函数）',
              not has_wasted_preview(body), body[:20])

        # ================================================================
        print('\n=== 10. gen_resume 的兜底：空简历不能落盘 ===')
        run_dir = prepare(os.path.join(tmp, 'run_badresume'))

        def short_resume(prompt=None, **kw):
            return {'optimized_resume': '太短了', 'changes': []}

        code = run_main([run_dir, '--greeting-mode', 'skip'],
                        (None, short_resume))
        check('optimized_resume 过短 → 全失败退出码 3', code == 3, code)
        check('过短的简历一个都没落盘', gen_names(run_dir) == [], gen_names(run_dir))

        def no_body(prompt=None, **kw):
            return {'changes': ['改了很多']}

        run_dir = prepare(os.path.join(tmp, 'run_nobody'))
        code = run_main([run_dir, '--greeting-mode', 'skip'], (None, no_body))
        check('缺 optimized_resume → 退出码 3', code == 3, code)
        check('缺字段时不落盘', gen_names(run_dir) == [], gen_names(run_dir))

        def fenced(prompt=None, **kw):
            data = dict(RESUME_JSON)
            data['optimized_resume'] = '```markdown\n%s\n```' % RESUME_JSON['optimized_resume']
            return data

        run_dir = prepare(os.path.join(tmp, 'run_fenced'))
        run_main([run_dir, '--greeting-mode', 'skip'], (None, fenced))
        hit = [n for n in gen_names(run_dir) if n.startswith('resume_1_')][0]
        data = json.loads(read_text(os.path.join(run_dir, 'generated', hit)))
        check('简历正文外面的 ``` 被剥掉（否则渲染出一堆反引号）',
              not data['optimized_resume'].startswith('```'),
              data['optimized_resume'][:30])

        # ================================================================
        print('\n=== 11. build_match_index：三个来源叠加，deep 最权威 ===')
        link = JOBS[0]['link']
        run_dir = prepare(os.path.join(tmp, 'run_index'), extra={
            'scored_jobs.json': {'tier1': [{'link': link, 'match_score': 70,
                                            'match_reasons': ['规则理由'],
                                            'missing_skills': ['规则缺失']}]},
        })
        index = GM.build_match_index(run_dir)
        check('只有 scored_jobs 时用规则分', index[link]['match_score'] == 70, index)

        run_dir = prepare(os.path.join(tmp, 'run_index2'), extra={
            'scored_jobs.json': {'tier1': [{'link': link, 'match_score': 70,
                                            'match_reasons': ['规则理由']}]},
            'deep_candidates.json': {'candidates': [{'rank': 1, 'job': {'link': link}}]},
            'deep_results.json': {'results': [{'rank': 1, 'score': 95,
                                               'reason': '模型理由',
                                               'missing_items': ['模型缺失'],
                                               'highlight': '亮点'}]},
        })
        index = GM.build_match_index(run_dir)
        check('deep 的分盖住规则分', index[link]['match_score'] == 95, index)
        check('deep 的理由盖住规则理由', index[link]['match_reasons'] == ['模型理由'], index)
        check('deep 的 highlight 带上来', index[link].get('highlight') == '亮点', index)
        check('一个来源都没有也不炸',
              GM.build_match_index(os.path.join(tmp, 'base')) == {})
        bad_dir = prepare(os.path.join(tmp, 'run_index_bad'))
        with open(os.path.join(bad_dir, 'scored_jobs.json'), 'w', encoding='utf-8') as f:
            f.write('{ 这不是 JSON')
        check('来源文件坏了也不炸（降级而非拒绝执行）',
              GM.build_match_index(bad_dir) == {})

        # ================================================================
        print('\n=== 12. 前置条件缺失时的报错 ===')
        empty = os.path.join(tmp, 'run_empty')
        os.makedirs(empty)
        check('没有 qualified_jobs.json 退出码 1', run_main([empty]) == 1)
        check('运行目录不存在退出码 1',
              run_main([os.path.join(tmp, '不存在的目录')]) == 1)

        no_profile = os.path.join(tmp, 'run_noprofile')
        os.makedirs(no_profile)
        with open(os.path.join(no_profile, 'qualified_jobs.json'), 'w', encoding='utf-8') as f:
            json.dump(JOBS, f, ensure_ascii=False)
        check('没有 profile.json 退出码 1', run_main([no_profile]) == 1)

        empty_pool = prepare(os.path.join(tmp, 'run_emptypool'), jobs=[])
        check('qualified_jobs.json 是空数组退出码 1', run_main([empty_pool]) == 1)

        wrapped = prepare(os.path.join(tmp, 'run_wrapped'), jobs=None)
        with open(os.path.join(wrapped, 'qualified_jobs.json'), 'w', encoding='utf-8') as f:
            json.dump({'jobs': JOBS[:1]}, f, ensure_ascii=False)
        code = run_main([wrapped, '--resume-mode', 'skip'])
        check('被包一层的 {"jobs": [...]} 也能读（与 check_artifacts 同口径）',
              code == 0 and len(gen_names(wrapped)) == 1, (code, gen_names(wrapped)))

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print('\n' + '=' * 60)
    if failures:
        print('❌ %d 条断言失败：' % len(failures))
        for item in failures:
            print('  - %s' % item)
        return 1
    print('✅ gen_materials 测试全部通过（含真实 check_artifacts / resolve_greeting 联测）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
