# -*- coding: utf-8 -*-
"""eval 端到端流程测试：offline 评估（断言不触网）+ stub 生成 + 对照岗幻觉 + 报告落盘。

产物目录走真实 prod 契约（state/ resume_text.txt+profile.json+qualified_jobs.json、
materials/ greeting_#_*.txt + resume_#_*.json），不复用 tests/ 里过时的 run_dir/generated/ 约定。

关键断言：
  · offline 评估全程不触发模型调用（monkeypatch 成必炸，跑通即证明没触网）。
  · 臆造岗（往优化稿里注入 PyTorch/nginx/CrewAI）hallucination_pct > 0，干净岗 == 0。
  · --generate --offline 用 stub 落盘 materials/，registry 可回放一致。
  · report.html 含 KPI / 术语 / 建议三大区块。

跑法：python -m pytest tests/test_eval_flow.py -q
"""

import os
import sys
import json
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "stages"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "eval", "materials"))

for _stream in (sys.stdout, sys.stderr):
    _stream.reconfigure(encoding='utf-8', errors='replace')

import gen_materials as GM
import evaluate_materials as EM


RESUME = """## 个人简介
张三，2 年 Python 后端经验，做事务实。

## 专业技能
- Python、FastAPI、Redis

## 工作经历
某科技 · 后端工程师 · 2024-2025
- 订单查询 P99 从 800ms 降到 120ms

## 项目经历
智能问答系统
- 基于 RAG 的企业知识库问答
"""

PROFILE = {
    'basic_info': {'name': '张三', 'status': '离职-随时到岗',
                   'availability': {'can_start': '随时', 'days_per_week': '5天'}},
    'education': {'school': '某大学', 'degree': '本科', 'major': '计算机'},
    'experience': {'total_years': 2, 'companies': [
        {'name': '某科技', 'position': '后端', 'highlights': ['P99 降 120ms']}]},
    'skills': {'语言': ['Python'], '框架': ['FastAPI'], 'AI': ['RAG']},
    'projects': [{'name': '智能问答系统', 'tech_stack': 'Python/FAISS',
                  'highlights': ['召回率升 30%']}],
    'awards': [], 'publications': [],
}

# 岗位：一个方向基本契合（驱动词不超简历）、一个故意偏（JD 含简历没有的技术栈）
JOBS = [
    {'link': 'https://jobs.eval.local/1', '公司': '甲公司', '职位': '后端开发',
     '薪资': '15-25K', '经验': '1-3年', '学历': '本科',
     '技能标签': 'Python,FastAPI,Redis',
     '岗位要求和职责': '负责 Python 后端与 FastAPI 服务开发。'},
    {'link': 'https://jobs.eval.local/2', '公司': '乙科技', '职位': 'AI平台工程师',
     '薪资': '20-35K', '经验': '3-5年', '学历': '硕士',
     '技能标签': 'Kubernetes,nginx,CrewAI,机器学习',
     '岗位要求和职责': '用 Kubernetes 部署 nginx 网关，基于 CrewAI 与机器学习做智能体平台。'},
]


def make_run(tmp_path):
    """在 tmp_path 下建一个真实契约的 run_dir：state/ 三件套 + 空 materials/。"""
    run = tmp_path / 'run'
    (run / 'state').mkdir(parents=True, exist_ok=True)
    (run / 'materials').mkdir(parents=True, exist_ok=True)
    (run / 'state' / 'resume_text.txt').write_text(RESUME, encoding='utf-8')
    (run / 'state' / 'profile.json').write_text(
        json.dumps(PROFILE, ensure_ascii=False), encoding='utf-8')
    (run / 'state' / 'qualified_jobs.json').write_text(
        json.dumps(JOBS, ensure_ascii=False), encoding='utf-8')
    return str(run)


def read_metrics(run_dir, index):
    """从 eval.json 里取某岗位的 metrics。"""
    with open(os.path.join(run_dir, 'eval', 'eval.json'), encoding='utf-8') as f:
        data = json.load(f)
    for j in data['jobs']:
        if j['index'] == index:
            return j['metrics']
    return None


# ==================== offline 评估：不触网 + 对照岗幻觉 ====================

def test_offline_eval_no_network_and_hallucination_contrast(tmp_path):
    run = make_run(tmp_path)

    # 用 stub 产物：岗1 干净（驱动词生成），岗2 注入一批「简历和 JD 都没有」的词。
    # 岗2 的 JD 本身含 nginx/CrewAI —— 那些是岗位驱动、不该算无据；控制组用 True2 新造。
    from eval.materials.stubs import clean_greeting, clean_resume
    for i, job in enumerate(JOBS, 1):
        jd = '、'.join(str(job.get(k)) for k in
                       ('技能标签', '岗位要求和职责', '职位', '公司') if job.get(k))
        greet = clean_greeting(job, '可到岗: 随时、每周出勤: 5天')
        md, _ = clean_resume(RESUME, jd)
        if i == 2:                                    # 臆造：注入 JD 也没有的词
            md = md + "\n- 精通 PyTorch 与 TensorFlow，用 True2 做过分布式训练\n"
        GM._write_atomic(os.path.join(run, 'materials', 'greeting_%d_%s.txt' % (i, job['公司'])), greet)
        GM._write_atomic(os.path.join(run, 'materials', 'resume_%d_%s.json' % (i, job['公司'])),
                         json.dumps({'optimized_resume': md}, ensure_ascii=False))

    # 证明确实没触网：把模型调用全换成必炸
    saved = GM.chat, GM.chat_json, GM.resolve
    GM.chat = GM.chat_json = GM.resolve = lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError('offline 评估不应触网'))

    args = argparse.Namespace(no_subjective=False)
    try:
        jobs_view, missing = EM.evaluate_run(run, JOBS, PROFILE, RESUME, args)
    finally:
        GM.chat, GM.chat_json, GM.resolve = saved

    assert missing == []
    assert len(jobs_view) == 2

    # 对照：岗1 干净 hallucination≈0，岗2 臆造 > 0
    alias = {j['index']: j['metrics'] for j in jobs_view}
    assert alias[1]['terms']['hallucination_pct'] == 0.0
    assert alias[2]['terms']['hallucination_pct'] > 0.0
    unfounded_words = {f['term'] for f in alias[2]['terms']['unfounded']}
    # 真无据：PyTorch/TensorFlow（简历、JD 都没有）
    assert 'PyTorch' in unfounded_words and 'TensorFlow' in unfounded_words
    # 岗位驱动：JD 里写了 nginx/CrewAI，正确被分到 jd_driven，不算无据
    assert 'CrewAI' not in unfounded_words
    assert alias[2]['terms']['n_jd_driven'] > 0


def test_offline_writes_eval_json(tmp_path):
    run = make_run(tmp_path)
    from eval.materials.stubs import clean_greeting, clean_resume
    for i, job in enumerate(JOBS, 1):
        jd = job['技能标签']
        GM._write_atomic(os.path.join(run, 'materials', 'greeting_%d_%s.txt' % (i, job['公司'])),
                         clean_greeting(job, ''))
        GM._write_atomic(os.path.join(run, 'materials', 'resume_%d_%s.json' % (i, job['公司'])),
                         json.dumps({'optimized_resume': clean_resume(RESUME, jd)[0]},
                                    ensure_ascii=False))
    args = argparse.Namespace(no_subjective=False)
    jobs_view, _ = EM.evaluate_run(run, JOBS, PROFILE, RESUME, args)

    from eval.materials.recommend import recommend
    from eval.materials.report_html import write_eval_json
    rec = recommend([j['metrics'] for j in jobs_view])
    write_eval_json(os.path.join(run, 'eval', 'eval.json'), rec, jobs_view, 'test')

    assert os.path.exists(os.path.join(run, 'eval', 'eval.json'))
    m = read_metrics(run, 1)
    assert 'char_diff' in m and 'terms' in m and 'greeting' in m and 'chapters' in m


# ==================== --generate --offline：stub 落盘 + registry 回放 ====================

def test_generate_offline_stub_and_registry(tmp_path):
    run = make_run(tmp_path)
    args = argparse.Namespace(offline=True, workers=2)
    records = EM.generate_materials(run, JOBS, PROFILE, args, offline=True)
    assert set(records) == {1, 2}
    assert os.path.exists(os.path.join(run, 'materials', 'greeting_1_甲公司.txt'))
    assert os.path.exists(os.path.join(run, 'materials', 'resume_1_甲公司.json'))

    # registry 回放一致
    from eval.materials.stubs import StubRun
    reg_path = os.path.join(run, 'eval', 'stub_registry.json')
    os.makedirs(os.path.dirname(reg_path), exist_ok=True)
    stub = StubRun()
    for i, rec in records.items():
        stub.register_greeting(i, rec['greeting'])
        stub.register_resume(i, rec['resume_md'], [])
    stub.save(reg_path)
    back = StubRun.load(reg_path)
    assert back.greetings == stub.greetings
    assert back.resumes == stub.resumes


# ==================== report.html 区块 ====================

def test_report_html_blocks(tmp_path):
    run = make_run(tmp_path)
    from eval.materials.stubs import clean_greeting, clean_resume
    for i, job in enumerate(JOBS, 1):
        jd = job['技能标签']
        GM._write_atomic(os.path.join(run, 'materials', 'greeting_%d_%s.txt' % (i, job['公司'])),
                         clean_greeting(job, ''))
        md = clean_resume(RESUME, jd)[0]
        if i == 2:
            md += "- PyTorch\n"
        GM._write_atomic(os.path.join(run, 'materials', 'resume_%d_%s.json' % (i, job['公司'])),
                         json.dumps({'optimized_resume': md}, ensure_ascii=False))
    args = argparse.Namespace(no_subjective=False)
    jobs_view, _ = EM.evaluate_run(run, JOBS, PROFILE, RESUME, args)
    from eval.materials.recommend import recommend
    from eval.materials.report_html import render_html
    html = render_html(recommend([j['metrics'] for j in jobs_view]), jobs_view, '测试')
    assert '术语三分类' in html
    assert '无据术语逐条' in html
    assert '提示词优化建议' in html
    assert 'PyTorch' in html
    # 规则路径也逐词着色（对照面板） + 堆叠条给具体术语标签，不只比例
    assert 'class="tterm' in html
    assert 'class="chip' in html
    assert '规则分类' in html
    assert '&amp;amp;lt;span' not in html


# ==================== --terms-llm：分类器接入 + 对照面板 ====================

def test_terms_llm_feeds_classifier_and_compare_panel(tmp_path):
    """--terms-llm 走 LLM 分类（monkeypatch classify_all，不触网），报告渲染对照面板。

    断言：job2（岗位驱动 PyTorch / 无据 FabricatedStack）terms.mode=llm 且带 analysis；
    report.html 出现「优化前 vs 优化后」、悬停理由、三类着色；job1（无缓存）回退规则。
    """
    run = make_run(tmp_path)
    from eval.materials.stubs import clean_greeting, clean_resume
    for i, job in enumerate(JOBS, 1):
        GM._write_atomic(os.path.join(run, 'materials', 'greeting_%d_%s.txt' % (i, job['公司'])),
                         clean_greeting(job, ''))
        md = clean_resume(RESUME, job['技能标签'])[0]
        if i == 2:
            md += "\n- 熟悉 PyTorch 与 FabricatedStack\n"
        GM._write_atomic(os.path.join(run, 'materials', 'resume_%d_%s.json' % (i, job['公司'])),
                         json.dumps({'optimized_resume': md}, ensure_ascii=False))

    # 只给 2 岗分类（1 岗无缓存 → 应回退规则），monkeypatch 成必炸无法区分 bucket 语义，
    # 直接喂确定性三桶。完全由我们给定，不触网。
    from eval.materials import terms_llm
    fake_classified = {
        2: {'n_opt': 2, 'n_retained': 1, 'n_jd_driven': 1, 'n_unfounded': 0,
            'hallucination_pct': 0.0, 'jd_driven_pct': 0.5, 'retention_pct': 0.5,
            'fabrication_within_new': 0.0, 'unfounded': [],
            'terms': [
                {'term': 'PyTorch', 'bucket': 'jd_driven', 'reason': '乙公司 JD 明确要求'},
                {'term': 'FabricatedStack', 'bucket': 'unfounded', 'reason': '两边都没有，编的'},
            ],
            'analysis': '靠岗位但有一条编造需清。', 'mode': 'llm'},
    }
    called = {}
    def _fake_classify_all(jobs, base_text, opt_by_index, **kw):
        called['n'] = len(jobs)
        return fake_classified
    saved = terms_llm.classify_all
    terms_llm.classify_all = _fake_classify_all
    args = argparse.Namespace(no_subjective=False)
    try:
        classified = terms_llm.classify_all(JOBS, RESUME, {}, run_dir=run, offline=True)
        assert called['n'] == 2
        # 逐岗喂给 evaluate_run
        jobs_view, _ = EM.evaluate_run(run, JOBS, PROFILE, RESUME, args,
                                       classified=classified)
    finally:
        terms_llm.classify_all = saved

    alias = {j['index']: j['metrics'] for j in jobs_view}
    assert alias[2]['terms']['mode'] == 'llm'
    assert alias[2]['terms']['analysis'] == '靠岗位但有一条编造需清。'
    assert alias[1]['terms'].get('mode') != 'llm'          # 1 岗无分类 → 规则兜底

    from eval.materials.recommend import recommend
    from eval.materials.report_html import render_html
    html = render_html(recommend([j['metrics'] for j in jobs_view]), jobs_view, '测试')
    assert '优化前 vs 优化后' in html
    assert 'LLM 语义分类' in html
    assert '规则分类' in html
    assert 'FabricatedStack' in html and 'PyTorch' in html
    assert 'title="[岗位驱动] 乙公司 JD 明确要求' in html        # 悬停理由
    assert '&amp;amp;lt;span' not in html                      # 无双倍转义


def test_terms_llm_offline_reads_cache_without_network(tmp_path):
    """--offline 且有缓存：分类直接读缓存键，不触网。无缓存岗给 {'mode':'rule'} 兜底。"""
    from eval.materials import terms_llm
    # 先造一份缓存：给岗1 写入一个确定性 terms dict，岗1 脚本用真实 resume/opt 一致输入
    from eval.materials.stubs import clean_resume
    opt1 = clean_resume(RESUME, JOBS[0]['技能标签'])[0]
    opt2 = clean_resume(RESUME, JOBS[1]['技能标签'])[0]
    opt_by = {1: opt1, 2: opt2}
    cache = {}
    # 岗1 的键：base_text + opt1 + jd1
    jd1 = '、'.join(str(JOBS[0].get(k)) for k in
                    ('技能标签', '岗位要求和职责', '职位', '公司') if JOBS[0].get(k))
    key1 = terms_llm._job_key(RESUME, opt1, jd1)
    cache[key1] = {'n_opt': 3, 'n_retained': 3, 'n_jd_driven': 0, 'n_unfounded': 0,
                   'hallucination_pct': 0.0, 'terms': [], 'analysis': '缓存命中的点评。',
                   'mode': 'llm'}
    terms_llm.save_cache(str(tmp_path), cache)

    # 把真触网分类器换成必炸：cache 命中路径不该走到它
    def _boom(*a, **k):
        raise RuntimeError('offline 不应触网')
    saved = terms_llm.classify_job
    terms_llm.classify_job = _boom
    try:
        out = terms_llm.classify_all(JOBS, RESUME, opt_by, run_dir=str(tmp_path), offline=True)
    finally:
        terms_llm.classify_job = saved
    assert out[1]['mode'] == 'llm'
    assert out[1]['analysis'] == '缓存命中的点评。'
    assert out[2] == {'mode': 'rule'}               # 岗2 无缓存 → 规则兜底标记


if __name__ == '__main__':
    import tempfile
    import pathlib
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    _tmp = pathlib.Path(tempfile.mkdtemp())
    failed = 0
    for fn in fns:
        try:
            argnames = fn.__code__.co_varnames[:fn.__code__.co_argcount]
            kw = {n: _tmp for n in argnames if n == 'tmp_path'}
            fn(**kw)
            print('PASS', fn.__name__)
        except Exception:
            failed += 1
            print('FAIL', fn.__name__)
            traceback.print_exc()
    sys.exit(1 if failed else 0)