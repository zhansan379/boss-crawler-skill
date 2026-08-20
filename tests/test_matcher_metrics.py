# -*- coding: utf-8 -*-
"""matcher_metrics.py 纯函数单测：分类 / 分数误差 / 排序 / 技能 四族指标 + 组合入口。

不触网、不依赖 llm。断言覆盖：
  · 分类：全对 / 混淆 / 空支持 0.0（非 NaN）/ classification_gap 暴露过欠预测。
  · 分数：MAE/RMSE/bias 符号 / native 刻度。
  · 排序：nDCG 最优=1 / 对调下降 / ndcg_at_k / Spearman 单调≈1、n<2→None、恒定→None、
    relevance 两源差异。
  · 技能：别名（k8s⇄Kubernetes、es⇄elasticsearch）normalize=True 命中、False 不命中 + 空集约定。
  · evaluate_matching 六键齐全 + 坏样本进 errors。

跑法：python -m pytest tests/test_matcher_metrics.py -q   （或直接 python tests/test_matcher_metrics.py）
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts",
                                "eval", "matcher"))

from matcher_metrics import (                       # noqa: E402
    classification_eval, score_error_eval, ranking_eval, skills_eval,
    evaluate_matching, _to_100,
)


def test_to_100_normalizes_rule_scale():
    r = _to_100(115)
    assert r['score100'] == 100.0
    r2 = _to_100(57.5)
    assert abs(r2['score100'] - 50.0) < 1e-9


# ==================== 1) 分类 ====================

def test_classification_all_correct():
    gold = ['qualified', 'need_optimization', 'cannot_apply']
    pred = gold[:]
    r = classification_eval(gold, pred)
    assert r['accuracy'] == 1.0
    assert r['n'] == 3
    assert all(pc['f1'] == 1.0 for pc in r['per_class'].values())


def test_classification_confusion_and_gap():
    gold = ['qualified', 'need_optimization', 'cannot_apply']
    pred = ['qualified', 'cannot_apply', 'cannot_apply']   # 中间档误判成不可投
    r = classification_eval(gold, pred)
    # 3 样中 index0/2 命中、index1 错 → 2/3
    assert r['accuracy'] == 2 / 3
    # cannot_apply 被过度预测：gold 1 → pred 2，gap delta = +1
    assert r['classification_gap']['cannot_apply'] == [1, 2, 1]
    assert r['confusion']['cannot_apply']['need_optimization'] == 1


def test_classification_zero_support_is_zero_not_nan():
    r = classification_eval([], [])
    assert r['n'] == 0 and r['accuracy'] == 0.0
    for pc in r['per_class'].values():
        assert pc['precision'] == 0.0 and pc['recall'] == 0.0 and pc['f1'] == 0.0


def test_classification_prefilled_keys():
    r = classification_eval(['qualified'], ['qualified'])
    assert set(r['per_class']) == {'qualified', 'need_optimization', 'cannot_apply'}


# ==================== 2) 分数误差 ====================

def test_score_missing_pair_skipped():
    r = score_error_eval([60.0, None], [50.0, 1.0])
    assert r['n'] == 1                       # None gold 被跳过
    assert abs(r['mae'] - 10.0) < 1e-9


def test_score_bias_sign():
    # pred 恒比 gold 低 10 → bias 强负（系统性打低）
    r = score_error_eval([80, 60, 90], [70, 50, 80])
    assert abs(r['bias'] + 10.0) < 1e-9
    assert abs(r['mae'] - 10.0) < 1e-9


def test_score_native_scale():
    # copy_native 时把 pred<115 的原生刻度暴露出来（供看归一后缩掉的误差差距）
    r = score_error_eval([50.0, 50.0], [115, 0], copy_native=True)
    assert r['native_scale_mae'] is not None


# ==================== 3) 排序质量 ====================

def test_ndcg_optimal_is_one():
    samples = [{'index': 1, 'gold_cat': 'qualified', 'pred_cat': 'qualified',
                'gold_score': 90, 'pred_score': 90, 'link': 'a'},
               {'index': 2, 'gold_cat': 'need_optimization', 'pred_cat': 'need_optimization',
                'gold_score': 66, 'pred_score': 66, 'link': 'b'},
               {'index': 3, 'gold_cat': 'cannot_apply', 'pred_cat': 'cannot_apply',
                'gold_score': 30, 'pred_score': 30, 'link': 'c'}]
    r = ranking_eval(samples, k=3, relevance='category')
    assert r['ndcg'] == 1.0
    assert r['ndcg_at_k'] == 1.0


def test_ndcg_drops_when_order_toggled():
    # 三档 gold 序数(2/1/0)必须有差异，nDCG 才能被「pred 序」重排影响：
    # 全 equal grade 时任何 pred 序得到的 by_pred 都相同，nDCG 恒 1.0。
    def mk(pred_cats):
        gcats = ['qualified', 'need_optimization', 'cannot_apply']
        return [{'index': i, 'gold_cat': g, 'pred_cat': p, 'gold_score': s,
                 'pred_score': s, 'link': '%s%s' % (g, i)}
                for i, (g, p, s) in enumerate(zip(gcats, pred_cats, [90, 60, 30]))]
    best = mk(['qualified', 'need_optimization', 'cannot_apply'])   # pred 序与 gold 一致
    r_best = ranking_eval(best, relevance='category')['ndcg']
    assert r_best == 1.0
    # 最差：pred 把不可投的排最前 → by_pred(按 pred 序) 是 [0,1,2]，nDCG 必降
    worst = mk(['cannot_apply', 'need_optimization', 'qualified'])
    r_worst = ranking_eval(worst, relevance='category')['ndcg']
    assert r_worst < r_best


def test_spearman_monotonic_and_edge_cases():
    # 严格单调 → ≈1
    mk = lambda by: [{'index': i, 'gold_cat': 'qualified', 'gold_score': s,
                      'pred_score': s, 'pred_cat': 'qualified', 'link': str(i)}
                     for i, s in enumerate(sorted(by))]
    full = mk([80, 70, 60, 50])
    assert ranking_eval(full, relevance='score')['spearman'] == 1.0
    # n<2 → None，永不 NaN/报错
    single = mk([70])
    assert ranking_eval(single, relevance='score')['spearman'] is None
    # 恒定（零方差）→ None
    flat = mk([70, 70, 70])
    assert ranking_eval(flat, relevance='score')['spearman'] is None


def test_relevance_sources_differ():
    # 'category' 用序数（稳健），'score' 用连续分（更敏感）：两者 Spearman 可不同
    samples = [{'index': 1, 'gold_cat': 'qualified', 'pred_cat': 'qualified',
                'gold_score': 95, 'pred_score': 60, 'link': 'a'},
               {'index': 2, 'gold_cat': 'need_optimization', 'pred_cat': 'cannot_apply',
                'gold_score': 66, 'pred_score': 70, 'link': 'b'}]
    r_cat = ranking_eval(samples, relevance='category')
    r_score = ranking_eval(samples, relevance='score')
    assert r_cat['relevance'] == 'category' and r_score['relevance'] == 'score'
    assert r_cat['ndcg'] >= 0 and r_score['ndcg'] >= 0


# ==================== 4) 技能命中 / 缺失 ====================

def _skill_sample(gold_matched, pred_matched, gold_missing, pred_missing,
                  normalize=True):
    return skills_eval([{'gold_matched': gold_matched, 'pred_matched': pred_matched,
                         'gold_missing': gold_missing, 'pred_missing': pred_missing}],
                       normalize=normalize)


def test_skills_alias_folds_when_normalize():
    # 别名：k8s⇄Kubernetes、es⇄elasticsearch
    r = _skill_sample(['Kubernetes', 'Elasticsearch'], ['k8s', 'es'],
                      ['Go'], ['Go'])
    assert r['matched']['recall'] == 1.0
    assert r['matched']['precision'] == 1.0
    assert r['matched']['f1'] == 1.0


def test_skills_alias_not_folded_when_no_normalize():
    r = _skill_sample(['Kubernetes'], ['k8s'], [], [], normalize=False)
    # 不折叠 → 'k8s' != 'kubernetes' → 全 miss（recall 0、precision 0）
    assert r['matched']['recall'] == 0.0
    assert r['matched']['precision'] == 0.0


def test_skills_empty_set_conventions():
    # gold空+pred空 → P=R=1
    assert _skill_sample([], [], [], [])['matched']['f1'] == 1.0
    # gold空+pred非空 → P=0,R=1
    r = _skill_sample([], ['Python'], [], [])
    assert r['matched']['precision'] == 0.0 and r['matched']['recall'] == 1.0
    # pred空+gold非空 → P=1,R=0
    r = _skill_sample(['Python'], [], [], [])
    assert r['matched']['precision'] == 1.0 and r['matched']['recall'] == 0.0


def test_skills_missing_only_when_both_defined():
    # missing 侧 pred 未定义（None）→ 不进 missing 比较池，matched 单独算
    r = skills_eval([{'gold_matched': ['Python'], 'pred_matched': ['Python'],
                      'gold_missing': ['Go'], 'pred_missing': None}])
    assert r['samples_with_gold_missing'] == 0
    assert r['matched']['recall'] == 1.0
    assert r['missing']['recall'] == 0.0          # 无样本 → 全零默认


# ==================== 组合入口 ====================

def _sample(i, gcat, pcat, gm, pm, gx, px, gscore=60, pscore=50):
    return {'index': i, 'link': str(i), 'category': pcat,
            'score100': pscore, 'matched_skills': pm, 'missing_skills': px,
            'gold': {'category': gcat, 'score': gscore,
                     'matched_skills': gm, 'missing_skills': gx}}


def test_evaluate_matching_six_keys_and_error_isolation():
    good = _sample(1, 'qualified', 'qualified', ['Python'], ['Python'], ['Go'], ['Go'])
    bad = {'index': 2, 'category': 'qualified', 'score100': 50}       # 缺 gold → 进 errors
    r = evaluate_matching([], [good, bad])
    assert set(r.keys()) == {'classification', 'score_error', 'ranking',
                             'skills', 'n', 'errors'}
    assert r['n'] == 1                       # 坏样本被跳过
    assert len(r['errors']) == 1
    assert r['classification']['accuracy'] == 1.0
    assert r['skills']['matched']['recall'] == 1.0


if __name__ == '__main__':
    import pytest
    sys.exit(pytest.main([__file__, '-q']))