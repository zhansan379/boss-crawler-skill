# -*- coding: utf-8 -*-
"""简历优化同意闸门（计划→征询→应用）的离线测试。

盯的是三个接缝（用当前 state/ 四桶布局写 run_dir，不走上一版根目录老路径）：

  · gen_resume 拆成 gen_plan + apply_plan 后，**合并路径（--resume-mode ai）仍是
    每岗位两次调用、仍产出 3 键契约** —— 这是 eval harness 与既有调用的断点。
  · --resume-mode plan / --resume-mode apply 两半：plan 只写 plan_{i}_*.json；
    apply 只对「已批准」岗位出 resume_{i}_*.json（被拒的没有）。
  · set_plan_decisions.py 落盘 decision_{i}.json，apply 读它 —— 过滤后的
    suggestions（含用户亲手补的 must_add 真内容）确实到达 apply，被放弃的不出现。

LLM 请求全部打桩（GM.chat_json 按 stage 切换 plan/apply 返回），不碰真实模型。

跑法：python tests/test_plan_decisions.py
"""

import os
import sys
import json
import importlib

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "stages"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "deliver"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "utils"))

for _stream in (sys.stdout, sys.stderr):
    _stream.reconfigure(encoding='utf-8', errors='replace')

import gen_materials as GM
import set_plan_decisions as SPD
import read_thin as RT
from llm.config import LLMConfig

FAKE_CFG = LLMConfig(base_url='https://fake.local/v1', api_key='sk-fake',
                     model='m-test', concurrency=2, max_retries=0)

JOBS = [
    {'link': 'https://www.zhipin.com/job_detail/a1.html', '公司': '甲公司',
     '职位': 'AI应用开发', '技能标签': 'Python,RAG', '岗位要求和职责': '负责 RAG。'},
    {'link': 'https://www.zhipin.com/job_detail/b2.html', '公司': '乙公司',
     '职位': 'Python开发', '技能标签': 'Python', '岗位要求和职责': '后台开发。'},
    {'link': 'https://www.zhipin.com/job_detail/c3.html', '公司': '丙公司',
     '职位': '算法工程师', '技能标签': 'PyTorch', '岗位要求和职责': '召回优化。'},
]

# 脚本用 state/{qualified_jobs,profile}.json + state/resume_text.txt（四桶布局）。
RESUME_TEXT = (
    '# 张三\n'
    '## 个人简介\n负责过 RAG 问答，会 Python。\n'
    '## 专业技能\nPython、RAG、PyTorch\n'
    '## 项目经历\n### 智能问答\n- 基于 RAG 的企业问答，召回率提升\n'
    '## 教育背景\n某大学 本科\n'
)

PLAN_CH = ['个人简介', '专业技能', '项目经历', '教育背景']
SUG = {
    'must_add': [{'section': '专业技能', 'content': '多智能体项目经验'}],
    'should_adjust': [{'section': '项目经历', 'suggestion': '量化成果前置'}],
    'keywords_to_emphasize': ['RAG'],
    'format_suggestions': ['指标加粗'],
}
FILTERED_SUG = {
    'must_add': [{'section': '专业技能', 'content': '我真实做过的多智能体系统'}],
    'should_adjust': [],
    'keywords_to_emphasize': ['RAG'],
    'format_suggestions': [],
}
FAKE_RESUME = '\n'.join('## %s\n- 内容\n' % c for c in PLAN_CH)


def make_chat_json(record):
    def stub(prompt=None, stage=None, run_dir=None, cfg=None, **kw):
        record.append(stage)
        if stage == 'resume:plan':
            return {'chapter_plan': [{'chapter': c, 'order': i, 'keep': True}
                                     for i, c in enumerate(PLAN_CH, 1)],
                    'optimization_suggestions': SUG}
        # stage 'resume'（应用）
        return {'optimized_resume': FAKE_RESUME, 'key_changes': ['润色']}
    return stub


def prepare(run):
    os.makedirs(os.path.join(run, 'state'), exist_ok=True)
    with open(os.path.join(run, 'state', 'qualified_jobs.json'), 'w', encoding='utf-8') as f:
        json.dump(JOBS, f, ensure_ascii=False, indent=2)
    with open(os.path.join(run, 'state', 'profile.json'), 'w', encoding='utf-8') as f:
        json.dump({'basic_info': {'name': '张三'}, 'education': {}, 'skills': {},
                   'experience': {}, 'projects': [], 'awards': [], 'publications': [],
                   'social_links': {}}, f, ensure_ascii=False)
    with open(os.path.join(run, 'state', 'resume_text.txt'), 'w', encoding='utf-8') as f:
        f.write(RESUME_TEXT)
    try:
        import apply as AP
        AP_AVAILABLE = True
    except Exception as exc:                                    # noqa: BLE001
        AP = None
        AP_AVAILABLE = False
    return run


def cli_gm(argv, record):
    saved = (sys.argv, GM.chat, GM.chat_json, GM.resolve)
    sys.argv = ['gen_materials.py'] + argv
    GM.chat = lambda *a, **k: ''
    GM.chat_json = make_chat_json(record)
    GM.resolve = lambda **kw: FAKE_CFG
    try:
        return GM.main()
    finally:
        sys.argv, GM.chat, GM.chat_json, GM.resolve = saved


def cli_spd(argv):
    saved = sys.argv
    sys.argv = ['set_plan_decisions.py'] + argv
    try:
        return SPD.main()
    finally:
        sys.argv = saved


def main():
    import tempfile
    import shutil
    failures = []

    def check(label, cond, detail=''):
        print('%s %s%s' % ('  ✅' if cond else '  ❌', label,
                           '' if cond else '  ← ' + str(detail)[:400]))
        if not cond:
            failures.append(label)

    tmp = tempfile.mkdtemp(prefix='plan_gate_')
    try:
        # ============ 1. 合并路径（--resume-mode ai）拆分后行为不变 ============
        run = os.path.join(tmp, 'r_combined')
        prepare(run)
        rec = []
        code = cli_gm([run, '--greeting-mode', 'skip'], rec)
        check('合并路径退出码 0', code == 0, code)
        # 每岗位：plan 一轮 + apply 一轮 = 6 次 chat_json
        plans = [s for s in rec if s == 'resume:plan']
        applies = [s for s in rec if s == 'resume']
        check('合并路径每岗位两次调用（3 plan + 3 apply）',
              len(plans) == 3 and len(applies) == 3, (len(plans), len(applies)))
        names = sorted(os.listdir(os.path.join(run, 'materials')))
        resumes = [n for n in names if n.startswith('resume_')]
        check('合并路径写出 3 份 resume', len(resumes) == 3, names)
        if resumes:
            with open(os.path.join(run, 'materials', resumes[0]), encoding='utf-8') as f:
                d = json.load(f)
            check('resume 保持 3 键契约',
                  set(d) == {'optimization_suggestions', 'optimized_resume', 'key_changes'},
                  sorted(d))
            check('合并路径 optimization_suggestions = 计划全量',
                  d['optimization_suggestions'] == SUG, d.get('optimization_suggestions'))

        # ============ 2. --resume-mode plan 只出计划 ============
        run = os.path.join(tmp, 'r_plan')
        prepare(run)
        rec = []
        code = cli_gm([run, '--greeting-mode', 'skip', '--resume-mode', 'plan'], rec)
        check('plan 阶段退出码 0', code == 0, code)
        check('plan 阶段只调 plan 一轮/岗位', rec == ['resume:plan'] * 3, rec)
        names = sorted(os.listdir(os.path.join(run, 'materials')))
        check('plan 阶段写 3 份 plan_*.json', len([n for n in names if n.startswith('plan_')]) == 3, names)
        check('plan 阶段不写 resume', not any(n.startswith('resume_') for n in names), names)
        if names:
            with open(os.path.join(run, 'materials', names[0]), encoding='utf-8') as f:
                p = json.load(f)
            check('plan 文件含 optimization_suggestions 与 chapter_plan',
                  'optimization_suggestions' in p and 'chapter_plan' in p, sorted(p))

        # ============ 3. set_plan_decisions 落盘 + apply 只出已批准 ============
        run = os.path.join(tmp, 'r_gate')
        prepare(run)
        cli_gm([run, '--greeting-mode', 'skip', '--resume-mode', 'plan'], [])
        check('--approved 写决策 0', cli_spd([run, '--index', '1', '--approved']) == 0)
        check('--reject 写决策 0', cli_spd([run, '--index', '2', '--reject']) == 0)
        check('--suggestions 写决策 0',
              cli_spd([run, '--index', '3',
                       '--suggestions', json.dumps(FILTERED_SUG, ensure_ascii=False)]) == 0)
        check('坏 JSON 拒绝（退出 1）',
              cli_spd([run, '--index', '3', '--suggestions', '{不是 json']) == 1)

        dec = GM.load_decision(run, 1)
        check('#1 决策 approved', bool(dec and dec['approved']), dec)
        dec2 = GM.load_decision(run, 2)
        check('#2 决策未批准', bool(dec2 and not dec2['approved']), dec2)
        dec3 = GM.load_decision(run, 3)
        check('#3 决策带过滤 suggestions', dec3 and dec3['suggestions'] == FILTERED_SUG, dec3)

        rec = []
        code = cli_gm([run, '--greeting-mode', 'skip', '--resume-mode', 'apply'], rec)
        check('apply 阶段退出码 0（被拒不算失败）', code == 0, code)
        names = sorted(os.listdir(os.path.join(run, 'materials')))
        made = [n for n in names if n.startswith('resume_')]
        check('只出已批准岗位（#1/#3），被拒 #2 没有',
              len(made) == 2 and any(n.startswith('resume_1_') for n in made)
              and any(n.startswith('resume_3_') for n in made)
              and not any(n.startswith('resume_2_') for n in made), made)
        with open(os.path.join(run, 'materials',
                               [n for n in made if n.startswith('resume_1_')][0]),
                  encoding='utf-8') as f:
            d1 = json.load(f)
        check('#1（approved）suggestions = 计划全量', d1['optimization_suggestions'] == SUG,
              d1.get('optimization_suggestions'))
        with open(os.path.join(run, 'materials',
                               [n for n in made if n.startswith('resume_3_')][0]),
                  encoding='utf-8') as f:
            d3 = json.load(f)
        check('#3（逐条）suggestions = 过滤版（含用户真内容）',
              d3['optimization_suggestions'] == FILTERED_SUG,
              d3.get('optimization_suggestions'))

        # ============ 4. --resume-mode apply 无决策/未批准不生成（幂等） ============
        run = os.path.join(tmp, 'r_noapply')
        prepare(run)
        cli_gm([run, '--greeting-mode', 'skip', '--resume-mode', 'plan'], [])
        rec = []
        code = cli_gm([run, '--greeting-mode', 'skip', '--resume-mode', 'apply'], rec)
        check('未批准一个岗位就不出简历、退出 0', code == 0 and rec == [], (code, rec))
        check('未批准时 materials/ 只有 plan 文件',
              not any(n.startswith('resume_') for n in os.listdir(os.path.join(run, 'materials'))),
              os.listdir(os.path.join(run, 'materials')))

        # ============ 5. read_thin --kind plans 紧凑摘要 ============
        run = os.path.join(tmp, 'r_thin')
        prepare(run)
        cli_gm([run, '--greeting-mode', 'skip', '--resume-mode', 'plan'], [])
        plans = RT.thin_plans(run)
        check('thin_plans 返回 3 个计划的摘要', len(plans) == 3, plans)
        check('摘要带 index/公司/职位/must_add', bool(plans)
              and plans[0]['index'] == 1 and plans[0]['公司'] == '甲公司'
              and 'must_add' in plans[0] and 'should_adjust' in plans[0], plans[0] if plans else None)
        flat = json.dumps(plans, ensure_ascii=False)
        check('摘要里出现 must_add 的 section', '多智能体' in flat or '专业技能' in flat, flat[:200])

        # ============ 6. apply.py 回退图路由（决策里 fallback_image） ============
        try:
            import apply as AP
        except Exception as exc:                                # noqa: BLE001
            AP = None
        if AP is not None:
            run = os.path.join(tmp, 'r_fallback')
            prepare(run)
            img = os.path.join(tmp, 'my_custom.png')
            with open(img, 'wb') as f:
                f.write(b'xx')
            cli_gm([run, '--greeting-mode', 'skip', '--resume-mode', 'plan'], [])
            cli_spd([run, '--index', '2', '--reject', '--fallback-image', img])
            got = AP.decision_fallback_image(run, 2)
            check('被拒岗位取到回退图绝对路径',
                  got == os.path.abspath(img), got)
            check('已批准岗位/无决策时无回退图',
                  AP.decision_fallback_image(run, 1) is None,
                  AP.decision_fallback_image(run, 1))

        # ============ 7. --clear 移除决策 ============
        run = os.path.join(tmp, 'r_clear')
        prepare(run)
        cli_spd([run, '--index', '1', '--approved'])
        check('#1 决策存在', GM.load_decision(run, 1) and GM.load_decision(run, 1)['approved'])
        cli_spd([run, '--index', '1', '--clear'])
        check('--clear 后决策消失', GM.load_decision(run, 1) is None)

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print('\n' + '=' * 60)
    if failures:
        print('❌ %d 条断言失败：' % len(failures))
        for item in failures:
            print('  - %s' % item)
        return 1
    print('✅ 同意闸门测试全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())