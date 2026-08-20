# -*- coding: utf-8 -*-
"""matcher gold 源测试：手工 fixture + manifest 回放 + gold 清洗/缺失回退 + judge 映射。

不触网：`judge_gold_jobs` 的 chat_json 用 stub 换成固定应答；`_clean_gold` 与回退全纯。

覆盖：
  · load_hand_gold：8 条、source='hand'、category 落三枚举、score∈[0,100]、matched/missing 为 list。
  · write_manifest / load_manifest roundtrip（offline 确定性重放不重生）。
  · _clean_gold：category 经 _normalize_category 落枚举，score 截断到 0-100，matched 清洗。
  · gold.missing 缺失回退：优先技能标签分割；标签为空才回退 _baseline_keys(jd 文本)。
  · judge 映射：stub chat_json → gold.category/score/missing 来自应答，matched 用「简历技能 ∩
    岗位标签」推导，category 经 _normalize_category。

跑法：python -m pytest tests/test_gold_sources.py -q
"""

import os
import sys
import json

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts",
                                "eval", "matcher"))

from eval.matcher.matcher_gold import (                        # noqa: E402
    load_hand_gold, _clean_gold, write_manifest, load_manifest,
    _FIXTURE_PROFILE, _jobs_tags, normalize_skill,
)

_CATS = {'qualified', 'need_optimization', 'cannot_apply'}


def _job(tags=None, jd=''):
    return {'link': 'https://eval.local/j', '公司': '某司', '职位': '后端',
            '薪资': '20-30K', '经验': '3-5年', '学历': '本科',
            '技能标签': tags or '', '岗位要求和职责': jd}


# ==================== A 来源：手工 fixture ====================

def test_hand_fixtures_shape():
    samples = load_hand_gold()
    assert len(samples) == 8
    for s in samples:
        g = s['gold']
        assert g['source'] == 'hand'
        assert g['category'] in _CATS
        assert 0 <= g['score'] <= 100
        assert isinstance(g['matched_skills'], list)
        assert isinstance(g['missing_skills'], list)
        assert s['link'].startswith('https://')
        assert 'job' in s and 'profile' in s     # 每样本内联岗位与简历（回归自洽）


def test_hand_fixtures_cover_hard_gates_and_alias():
    samples = load_hand_gold()
    by_link = {s['link']: s for s in samples}
    # A1 qualified / A3 cannot_apply(薪资差)/ A6 学历硬门槛 / A5 别名
    cats = {s['gold']['category'] for s in samples}
    assert cats == _CATS                          # 三档都有覆盖
    # 技能别名（k8s ⇄ kubernetes、es ⇄ elasticsearch）在两套归一下折叠一致
    assert normalize_skill('k8s') == normalize_skill('kubernetes') == 'kubernetes'
    assert normalize_skill('es') == normalize_skill('elasticsearch') == 'elasticsearch'


# ==================== B 来源：manifest 持久化 ====================

def test_manifest_roundtrip(tmp_path):
    samples = load_hand_gold()
    p = os.path.join(str(tmp_path), 'eval_matcher', 'gold_manifest.json')
    write_manifest(p, samples)
    back = load_manifest(p)
    assert len(back) == len(samples)
    for a, b in zip(samples, back):
        assert a['link'] == b['link']
        assert a['gold'] == b['gold']             # 完整 gold 往返无损（offline 可确定性重放）


# ==================== gold 清洗与缺失回退 ====================

def test_clean_gold_clamps_score_and_normalizes_category():
    raw = {'category': '无法投递', 'score': 150,
           'matched_skills': ['Py', '', 'Redis'], 'missing_skills': []}
    job = _job(tags='Go,Redis')
    gold = _clean_gold(raw, job, 'ai')
    # score 截断到 100；matched 清掉空串；category 落三枚举（中文简称归一或回退默认）
    assert gold['score'] == 100
    assert gold['matched_skills'] == ['Py', 'Redis']
    assert gold['source'] == 'ai'
    assert gold['category'] in _CATS


def test_missing_falls_back_to_tags_split_first():
    # 有技能标签 → gold.missing 优先取标签分割（技能-only 字段），不回退 jd 启发式
    raw = {'category': 'qualified', 'score': 60}
    gold = _clean_gold(raw, _job(tags='Go,Redis,Python'), 'ai')
    assert set(gold['missing_skills']) == {'Go', 'Redis', 'Python'}


def test_missing_falls_back_to_baseline_keys_when_no_tags():
    # 技能标签为空 → 回退 _baseline_keys(jd 文本)（启发式，报告标注）
    raw = {'category': 'qualified', 'score': 60}
    gold = _clean_gold(raw, _job(tags='', jd='负责 Go 与 Rust 服务开发，快learn 上手'), 'ai')
    assert gold['missing_skills']                       # 非空（启发式抽取到词）
    lower = {str(s).lower() for s in gold['missing_skills']}
    assert 'go' in lower


# ==================== C 来源：LLM judge ====================

def _patch_chat(monkeypatch, fn):
    import llm as _llm                       # judge 走 `from llm import chat_json`（函数内 lazy）
    monkeypatch.setattr(_llm, 'chat_json', fn)


def test_judge_maps_chat_json_to_gold(tmp_path, monkeypatch):
    from eval.matcher import matcher_gold as MG
    _patch_chat(monkeypatch, lambda *a, **k: {
        'category': 'qualified', 'score': 78,
        'missing_items': ['Go'], 'reason': 'stub judge'})
    job = _job(tags='Python,Redis')
    samples, errors = MG.judge_gold_jobs(_FIXTURE_PROFILE, [job], 'dummy-cfg', str(tmp_path))
    assert errors == []
    assert len(samples) == 1
    gold = samples[0]['gold']
    assert gold['source'] == 'judge'
    assert gold['category'] == 'qualified'
    assert gold['score'] == 78
    assert gold['missing_skills'] == ['Go']
    # matched 用「简历技能 ∩ 岗位标签」推导（深度侧不产 matched 的固有不对称）；
    # 与 _profile_skills/_jobs_tags 一致，产出的 matched 是归一后的规范形（小写）
    assert 'python' in gold['matched_skills']
    assert 'redis' in gold['matched_skills']


def test_judge_isolates_bad_jobs_in_errors(tmp_path, monkeypatch):
    from eval.matcher import matcher_gold as MG
    def _boom(*a, **k):
        raise RuntimeError('judge 失败')
    _patch_chat(monkeypatch, _boom)
    samples, errors = MG.judge_gold_jobs(_FIXTURE_PROFILE, [_job(tags='A'), _job(tags='B')],
                                         'dummy-cfg', str(tmp_path))
    assert samples == []                    # 全败 → 无样本
    assert len(errors) == 2                 # 逐岗失败进 errors，不整批崩


if __name__ == '__main__':
    import pytest
    sys.exit(pytest.main([__file__, '-q']))