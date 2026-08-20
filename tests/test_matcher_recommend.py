# -*- coding: utf-8 -*-
"""matcher_recommend 纯函数单测：聚合 + 阈值→具体文件/代码位建议。

不触网、不依赖 llm。断言覆盖：
  · aggregate([]) 空输入 → zero-shape（n_variants 0 / variants {} / overall {}）。
  · aggregate 单变体 → 摘要字段齐全、Spearman None 不进均值、跨变体均值。
  · prompt_suggestions 阈值命中 → 期望的 {file,level,issue,action,evidence}，且 high 优先排序。
  · 全优输入 → 无建议。

跑法：python -m pytest tests/test_matcher_recommend.py -q
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts",
                                "eval", "matcher"))

from matcher_recommend import aggregate, prompt_suggestions, recommend   # noqa: E402


def _variant_result(accuracy=0.9, cannot_f1=0.8, bias=0.0, mae=5.0,
                    miss_p=0.9, match_r=0.9, spearman=None):
    """构造一条 evaluate_matching 结果的最小形状（对齐 aggregate 读取的键）。"""
    def cls(f1, gap_delta=0):
        return {'qualified': {'f1': f1}, 'need_optimization': {'f1': f1},
                'cannot_apply': {'f1': cannot_f1}, 'support': 3, 'predicted': 3}
    return {
        'n': 8,
        'classification': {'accuracy': accuracy, 'per_class': cls(cannot_f1),
                           'confusion': {}, 'classification_gap': {'qualified': [1, 1, 0],
                                                                    'need_optimization': [1, 1, 0],
                                                                    'cannot_apply': [1, 1, 0]}},
        'score_error': {'mae': mae, 'rmse': mae, 'bias': bias},
        'ranking': {'ndcg': 0.9, 'ndcg_at_k': 0.9, 'spearman': spearman, 'relevance': 'category'},
        'skills': {'matched': {'recall': match_r}, 'missing': {'precision': miss_p}},
    }


def _payload(accuracy=0.9, **kw):
    return [{'name': 'rule', 'result': _variant_result(accuracy=accuracy, **kw), 'note': ''}]


# ==================== 空输入 / 单变体 ====================

def test_aggregate_empty_zero_shape():
    agg = aggregate([])
    assert agg['n_variants'] == 0
    assert agg['variants'] == {}
    assert agg['overall'] == {}


def test_aggregate_single_variant_fields():
    agg = aggregate(_payload(accuracy=1.0, spearman=None))
    rv = agg['variants']['rule']
    assert rv['accuracy'] == 1.0
    assert rv['mae'] == 5.0 and rv['bias'] == 0.0
    # Spearman None → overall.avg_spearman 为 None（不取平均，也非 NaN）
    assert agg['overall']['avg_accuracy'] == 1.0
    assert agg['overall']['avg_spearman'] is None


# ==================== 阈值 → 建议 ====================

def test_suggestions_low_accuracy_high_priority():
    agg = aggregate(_payload(accuracy=0.5))
    sugs = prompt_suggestions(agg)
    hit = [s for s in sugs if s['file'] == 'scripts/prompts/match_analysis.st']
    assert hit and hit[0]['level'] == 'high'
    assert 'cannot_apply' in hit[0]['issue']
    # high 排最前
    assert sugs[0]['level'] == 'high'


def test_suggestions_negative_bias_flags_scoring():
    agg = aggregate(_payload(accuracy=0.9, bias=-12.0, mae=16.0))
    sugs = prompt_suggestions(agg)
    hit = [s for s in sugs if s['file'] == 'scripts/resume_matcher/scoring.py'
           and 'bias' in s['issue']]
    assert hit and hit[0]['level'] == 'medium'
    assert '_-12' in hit[0]['evidence'] or '-12' in hit[0]['evidence']


def test_suggestions_skill_alias_recall():
    agg = aggregate(_payload(accuracy=0.9, match_r=0.3))
    sugs = prompt_suggestions(agg)
    hit = [s for s in sugs if '_SKILL_ALIASES' in s['action']]
    assert hit and 'matched' in hit[0]['issue']


def test_suggestions_all_good_none():
    agg = aggregate(_payload(accuracy=0.9, cannot_f1=0.9, bias=0.0, mae=5.0,
                             miss_p=0.9, match_r=0.9, spearman=0.7))
    assert prompt_suggestions(agg) == []


def test_recommend_high_first_and_llm_comment_carried():
    rec = recommend(_payload(accuracy=0.5), llm_comment='综合点评')
    assert rec['llm_comment'] == '综合点评'
    assert rec['suggestions'][0]['level'] == 'high'
    assert rec['aggregate']['n_variants'] == 1


if __name__ == '__main__':
    import pytest
    sys.exit(pytest.main([__file__, '-q']))