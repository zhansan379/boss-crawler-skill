# -*- coding: utf-8 -*-
"""eval/materials/metrics.py + stubs.py 的纯函数单测：不触网、不依赖 llm 配置。

复用 verify_no_fabrication 的判定口径，所以这些断言同时保护「评估用的白名单式判定」
不被改偏 —— 别名（k8s⇄kubernetes）这类容易误报的地方单独拎出来验证。

跑法：python -m pytest tests/test_eval_metrics.py -q   （或直接 python tests/test_eval_metrics.py）
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

for _stream in (sys.stdout, sys.stderr):
    _stream.reconfigure(encoding='utf-8', errors='replace')

from eval.materials.metrics import (                            # noqa: E402
    char_diff, term_stats, greeting_stats,
    chapter_stats, missing_chapters, subjective_stats, evaluate_job,
    preview, has_wasted_preview,
)
from eval.materials.stubs import clean_greeting, clean_resume, StubRun   # noqa: E402
from verify_no_fabrication import _baseline_keys               # noqa: E402


# ==================== 字符级 diff ====================

def test_char_diff_pure_retention():
    r = char_diff("我有 Python 经验", "我有 Python 经验")
    assert r['deleted_pct'] == 0
    assert r['coverage_pct'] == 1.0


def test_char_diff_deletion_ratio():
    base = "冒号删除一段话，能力：精通 Java。"
    r = char_diff(base, "能力：精通 Java。")
    # 分母是原文长度；删除段应大于 0、覆盖小于 1
    assert r['deleted_chars'] > 0
    assert 0 < r['deleted_pct'] < 1
    assert 0 < r['coverage_pct'] < 1
    assert r['added_pct'] >= 0


def test_char_diff_addition():
    r = char_diff("精通 Java", "精通 Java、Spring、Redis")
    assert r['added_chars'] > 0
    assert r['added_pct'] > 0


# ==================== 术语三分类 ====================

def test_term_classify_baseline():
    # baseline 用真实 _baseline_keys 构造（含别名展开），与 load_baseline 同口径
    baseline = _baseline_keys("精通 Python 与 FastAPI，做过 RAG 检索。")
    r = term_stats("精通 Python 与 FastAPI 的 RAG 研发",
                   baseline=baseline, jd_keys=set())
    assert r['n_retained'] >= 3
    assert r['hallucination_pct'] == 0


def test_term_classify_jd_driven():
    # PyTorch 在 JD 里有依据 → 岗位驱动，不算幻觉
    r = term_stats("我会 Python 和 PyTorch",
                   baseline=_baseline_keys("我会 Python"),
                   jd_keys=_baseline_keys("要求会 PyTorch"))
    assert r['n_jd_driven'] == 1
    assert r['hallucination_pct'] == 0


def test_term_unfounded_flagged():
    r = term_stats("精通 Python 与 Kubernetes 与 nginx",
                   baseline=_baseline_keys("精通 Python"),
                   jd_keys=set())
    assert r['n_unfounded'] >= 2
    assert r['hallucination_pct'] > 0
    # 主分母是优化后全文术语总数 N_opt
    assert r['n_opt'] >= 3
    assert any(f['term'] == 'Kubernetes' for f in r['unfounded'])


def test_alias_k8s_kubernetes_not_reported():
    # 原文写 k8s、优化写 Kubernetes：_baseline_keys 展开别名后应同词 → 不算无据
    r = term_stats("负责 Kubernetes 容器编排生产",
                   baseline=_baseline_keys("负责 k8s 容器编排"),
                   jd_keys=set())
    assert r['n_unfounded'] == 0


def test_fabrication_within_new_denominator():
    r = term_stats("精通 Python 与 PyTorch 与 Quarkus",
                   baseline=_baseline_keys("精通 Python"),
                   jd_keys=set())
    # 新增 2 词（PyTorch/Quarkus）都无据 → within_new = 1.0
    assert r['fabrication_within_new'] == 1.0


def test_term_stats_per_bucket_detail_and_terms():
    """规则路也回三桶逐词明细 + 统一 terms list（report 才能逐词着色/列具体词）。"""
    base = _baseline_keys("精通 Python 与 FastAPI")
    jd = _baseline_keys("要求会 PyTorch")
    r = term_stats("精通 Python 与 PyTorch 与 FakeX",
                   baseline=base, jd_keys=jd)
    assert r['n_retained'] == 1 and r['n_jd_driven'] == 1 and r['n_unfounded'] == 1
    # 三桶明细
    assert ['Python'] == [w['term'] for w in r['retained']]
    assert ['PyTorch'] == [w['term'] for w in r['jd_driven']]
    assert ['FakeX'] == [w['term'] for w in r['unfounded']]
    # 统一 terms list：与 rule/llm 两路都同构，含 bucket + context
    buckets = {t['bucket'] for t in r['terms']}
    assert buckets == {'retained', 'jd_driven', 'unfounded'}
    assert {t['term'] for t in r['terms']} == {'Python', 'PyTorch', 'FakeX'}
    # 规则路径无 mode/analysis（仅 LLM 有），向后兼容
    assert r.get('mode') != 'llm' and 'analysis' not in r


# ==================== 招呼语质量 ====================

def test_greeting_wasted_preview():
    g = greeting_stats("您好，我是张三，对贵司的 AI 岗很有兴趣",
                       baseline={"张三"}, jd_keys=set(), availability_text='')
    assert g['has_wasted_preview'] is True


def test_greeting_fabricated_commitment_when_no_availability():
    # 简历没写到岗，招呼语却承诺「可到岗」「每周5天」 ← 编造承诺（verify 抓不到这层）
    g = greeting_stats("可每周 5 天到岗，能稳定实习 6 个月",
                       baseline=set(), jd_keys=set(), availability_text='')
    assert g['fabricated_commitment'] is True


def test_greeting_no_fabrication_when_availability_present():
    g = greeting_stats("可每周 5 天到岗",
                       baseline=set(), jd_keys=set(),
                       availability_text='可到岗: 随时、每周出勤: 5天')
    assert g['fabricated_commitment'] is False


# ==================== 章节保真 ====================

def test_missing_chapters_detected():
    base = "## 个人简介\n\n## 专业技能\n\n## 工作经历\n\n## 项目经历\n\n## 教育背景\n"
    opt = "## 个人简介\n\n## 专业技能\n\n## 项目经历\n"
    assert missing_chapters(base, opt) == ['工作经历', '教育背景']
    assert chapter_stats(base, opt)['n_missing'] == 2


def test_empty_base_skips():
    assert missing_chapters("", "## 专业技能\n") == []


# ==================== 客观性/夸大（启发） ====================

def test_subjective_upgrade_flagged():
    r = subjective_stats("我是了解 Java 的",
                         "我是精通 Java 的")
    assert r['flagged'] is True
    assert r['upgrades']


def test_subjective_disabled():
    assert subjective_stats("了解 Java", "精通 Java", enabled=False)['flagged'] is False


def test_subjective_absolute_added():
    r = subjective_stats("能做后端",
                         "能做后端，技术行业领先、顶尖水准")
    assert r['absolute_added']


# ==================== evaluate_job 聚合 ====================

def test_evaluate_job_shape():
    r = evaluate_job(
        base_text="## 个人简介\n精通 Python 做后端。",
        baseline={"python"},
        jd_keys=set(),
        greeting_text="Python 后端方向，可到岗",
        optimized_resume="## 个人简介\n精通 Python 做后端并精通 Kubernetes。",
        availability_text='',
    )
    for key in ('char_diff', 'terms', 'greeting', 'chapters', 'subjective'):
        assert key in r
    assert r['terms']['n_unfounded'] >= 1          # Kubernetes 无据


def test_evaluate_job_terms_source_llm_uses_classified():
    """terms_source='llm'：不走 term_stats 白名单，直接采用分类器的 terms dict。

    其余五维照常离线纯算数；llm 模式带 mode/analysis，rule 没有（向后兼容）。
    """
    classified = {'n_opt': 3, 'n_retained': 2, 'n_jd_driven': 1, 'n_unfounded': 0,
                  'hallucination_pct': 0.0, 'jd_driven_pct': 1 / 3,
                  'retention_pct': 2 / 3, 'fabrication_within_new': 0.0,
                  'unfounded': [], 'terms': [
                      {'term': 'Python', 'bucket': 'retained', 'reason': '原文有'},
                      {'term': 'PyTorch', 'bucket': 'jd_driven', 'reason': 'JD 要求'},
                  ], 'analysis': '靠岗位，无编造。', 'mode': 'llm'}
    r = evaluate_job(
        base_text="## 个人简介\nPython",
        baseline={"python"},
        jd_keys=_baseline_keys("要求 PyTorch"),
        greeting_text="Python 方向",
        optimized_resume="## 个人简介\nPython 与 PyTorch",
        terms_source='llm', classified_terms=classified,
    )
    assert r['terms']['mode'] == 'llm'
    assert r['terms']['n_jd_driven'] == 1 and r['terms']['n_unfounded'] == 0
    assert r['terms']['analysis'] == '靠岗位，无编造。'
    # 分类器把 PyTorch 判为 jd_driven —— 规则白名单也会判成 jd_driven，但模式标记要生效
    assert r['terms']['terms'][1]['bucket'] == 'jd_driven'
    # 其他五维照常离线算数
    for key in ('char_diff', 'greeting', 'chapters', 'subjective'):
        assert key in r


def test_evaluate_job_terms_source_rule_still_runs():
    """terms_source 缺省/rule：回退原白名单规则，不产生 mode/analysis。"""
    r = evaluate_job(
        base_text="## 个人简介\nPython",
        baseline={"python"},
        jd_keys=set(),
        greeting_text="Python",
        optimized_resume="## 个人简介\nPython 与 Quarkus",
    )
    assert r['terms'].get('mode') != 'llm'
    assert r['terms']['n_unfounded'] >= 1          # Quarkus 规则判无据
    assert 'analysis' not in r['terms']


# ==================== stub 生成（干净对照） ====================

def test_clean_resume_adds_only_jd_driven_words():
    base = "## 个人简介\n\n## 专业技能\n- Python、FastAPI\n"
    md, driven = clean_resume(base, "要求会 Python、FastAPI、PyTorch，并熟悉 nginx 部署")
    assert md.startswith("## 个人简介")                     # 不吞前置内容
    assert "岗位驱动技术要点" in md
    assert "nginx" in md and "pytorch" in md
    assert "Python" in md                                   # 原文词保留
    # 只加 JD 有依据而原文没有的词；别名归一后小写
    assert driven == ["nginx", "pytorch"]


def test_clean_greeting_has_no_wasted_preview():
    g = clean_greeting({'公司': '某某科技', '职位': 'AI工程师'})
    assert has_wasted_preview(g) is False


def test_clean_greeting_no_commitment_when_no_availability():
    g = clean_greeting({'公司': '某某', '职位': '后端'})
    assert '到岗' not in g
    assert '每周' not in g


# ==================== registry 回放 ====================

def test_stub_registry_roundtrip(tmp_path):
    run = StubRun()
    run.register_greeting(1, "你好")
    run.register_resume(1, "## 专业技能\n- PyTorch", driven=['PyTorch'])
    p = tmp_path / 'registry.json'
    run.save(str(p))
    back = StubRun.load(str(p))
    assert back.greetings[1] == "你好"
    assert back.resumes[1]['markdown'].startswith("## 专业技能")
    assert back.resumes[1]['driven'] == ['PyTorch']
    assert back.greetings == run.greetings


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
            kw = {n: _tmp for n in argnames if n in ('tmp_path',)}
            fn(**kw)
            print('PASS', fn.__name__)
        except Exception as e:
            failed += 1
            print('FAIL', fn.__name__)
            traceback.print_exc()
    sys.exit(1 if failed else 0)