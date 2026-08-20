#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""匹配评估结果的聚合 + 阈值→文件/代码位建议（镜像 materials/recommend.py）。

`aggregate(variants)`：把若干「预测变体」（rule-only / deep-only / blended）各自完整的
evaluate_matching 结果，聚成逐变体摘要 + 跨变体均值。`prompt_suggestions(agg)` 按阈值分裪映射
到**具体 prompt 文件 / 代码位**——每一档都指向「改哪里、往哪个方向」。

所有数值都是启发阈值，不是法官：超过阈值只是「值得看一眼」的信号。
"""

import json

# ==================== 阈值（启发，非结论） ====================

_ACCURACY_LOW = 0.75            # 分类准确率 < 75%
_CANNOT_APPLY_F1_LOW = 0.5      # cannot_apply 类 F1 < 0.5 → 硬门槛层漏判
_MAE_HIGH = 15.0                # 分数 MAE > 15 分
_BIAS_STRONG = -8.0             # 有符号 bias 强负（系统性打低）
_SKILL_MISSING_PRECISION_LOW = 0.6
_SKILL_MATCHED_RECALL_LOW = 0.5
_SPEARMAN_TIES = 0.2            # |spearman| ≤ 0.2 视为「排序几乎无区分度」


def aggregate(variants):
    """variants: [{name, result:evaluate_matching输出, note}] → 逐变体摘要 + 跨变体均值。"""
    if not variants:
        return {'n_variants': 0, 'variants': {}, 'overall': {}}

    per = {}
    for v in variants:
        name = v.get('name')
        r = v.get('result') or {}
        clf = r.get('classification') or {}
        err = r.get('score_error') or {}
        rnk = r.get('ranking') or {}
        sk = r.get('skills') or {}
        per[name] = {
            'n': r.get('n', 0),
            'accuracy': clf.get('accuracy', 0.0),
            'per_class': clf.get('per_class', {}),
            'classification_gap': clf.get('classification_gap', {}),
            'confusion': clf.get('confusion', {}),
            'mae': err.get('mae', 0.0),
            'rmse': err.get('rmse', 0.0),
            'bias': err.get('bias', 0.0),
            'ndcg': rnk.get('ndcg', 0.0),
            'ndcg_at_k': rnk.get('ndcg_at_k', 0.0),
            'spearman': rnk.get('spearman'),
            'relevance': rnk.get('relevance', ''),
            'matched': (sk.get('matched') or {}),
            'missing': (sk.get('missing') or {}),
            'note': v.get('note', ''),
        }

    def amean(key):
        vals = [p.get(key) for p in per.values() if isinstance(p.get(key), (int, float))]
        return sum(vals) / len(vals) if vals else 0.0

    def cls_f1_mean(cls_key):
        vals = [p.get('per_class', {}).get(cls_key, {}).get('f1', 0.0) for p in per.values()]
        return sum(vals) / len(vals) if vals else 0.0

    # 跨变体均值（Spearman 可能为 None，单独收集非 None 的平均）
    spears = [p.get('spearman') for p in per.values() if p.get('spearman') is not None]
    overall = {
        'n_variants': len(per),
        'avg_accuracy': amean('accuracy'),
        'avg_mae': amean('mae'),
        'avg_rmse': amean('rmse'),
        'avg_bias': amean('bias'),
        'avg_ndcg': amean('ndcg'),
        'avg_spearman': sum(spears) / len(spears) if spears else None,
        'avg_qualified_f1': cls_f1_mean('qualified'),
        'avg_need_optimization_f1': cls_f1_mean('need_optimization'),
        'avg_cannot_apply_f1': cls_f1_mean('cannot_apply'),
        'avg_matched_recall': amean_of_skill('matched', 'recall', per),
        'avg_missing_precision': amean_of_skill('missing', 'precision', per),
    }
    return {'n_variants': len(per), 'variants': per, 'overall': overall}


def amean_of_skill(family, key, per):
    vals = [p.get(family, {}).get(key, 0.0) for p in per.values()
            if isinstance(p.get(family), dict) and isinstance(p.get(family).get(key), (int, float))]
    return sum(vals) / len(vals) if vals else 0.0


def _variant(agg, name):
    return agg.get('variants', {}).get(name) or {}


# ==================== 阈值→建议（映射到具体文件/代码位） ====================

def prompt_suggestions(agg):
    out = []
    rv = _variant(agg, 'rule')
    if not rv:
        return out

    def add(file, level, issue, action, evidence=''):
        out.append({'file': file, 'level': level, 'issue': issue,
                    'action': action, 'evidence': evidence})

    acc = rv.get('accuracy', 0.0)
    can = (rv.get('per_class') or {}).get('cannot_apply', {}) or {}
    if acc < _ACCURACY_LOW or can.get('f1', 0.0) < _CANNOT_APPLY_F1_LOW:
        add('scripts/prompts/match_analysis.st', 'high',
            '规则分类整体准确率偏低，或 cannot_apply 类 F1 过低（硬门槛层漏判）',
            '在匹配/深度提示词里加显式硬门槛逐项自检（学历/经验差/薪资缺口），'
            '要求模型按 cannot_apply 三门槛逐条核对后再给分类',
            'rule accuracy=%.0f%% · cannot_apply F1=%.2f' % (
                acc * 100, can.get('f1', 0.0) if can else 0.0))

    bias = rv.get('bias', 0.0)
    mae = rv.get('mae', 0.0)
    if bias < _BIAS_STRONG or mae > _MAE_HIGH:
        add('scripts/resume_matcher/scoring.py', 'medium',
            '规则分系统性偏%s（bias=%.1f）或误差过大（MAE=%.1f）'
            % ('低' if bias < 0 else '高', bias, mae),
            '复核 _parse_salary / parse_experience_years 的折算口径：薪资「区间重叠 vs 缺口」、'
            '经验「无年限时 None vs 0」的判定是否被 bias 横向拉偏',
            'rule bias=%.1f · MAE=%.1f' % (bias, mae))

    miss = rv.get('missing', {}) or {}
    matched = rv.get('matched', {}) or {}
    if miss.get('precision', 1.0) < _SKILL_MISSING_PRECISION_LOW:
        add('scripts/resume_matcher/scoring.py', 'medium',
            'missing 技能 precision 偏低：规则报了一堆「其实不缺失」的技能',
            '核对技能标签提取与 missing_skills 的口径：避免把无缺失回退的 _baseline_keys 词汇'
            '混进 missing，细化 _COMMON_SKILLS 命中规则',
            'missing precision=%.0f%%' % (miss.get('precision', 0.0) * 100))
    if matched.get('recall', 1.0) < _SKILL_MATCHED_RECALL_LOW:
        add('scripts/resume_matcher/scoring.py', 'medium',
            'matched 技能 recall 偏低：规则漏判了一批实际命中的技能（常为别名/大小写）',
            '给 _SKILL_ALIASES 补遗漏别名（对比 gold 的 matched 词表，看哪些别名没覆盖）',
            'matched recall=%.0f%%' % (matched.get('recall', 0.0) * 100))

    spear = rv.get('spearman')
    if spear is not None and abs(spear) <= _SPEARMAN_TIES and acc >= _ACCURACY_LOW:
        add('scripts/resume_matcher/scoring.py', 'low',
            '分类准但排序相关≈0：类别对但同类内部的先后顺序被薪资/经验子分主导',
            '核对薪资/经验子分的区分度：同档岗位是否有稳定排序信号；必要时调子分权重',
            'spearman=%.2f · accuracy=%.0f%%' % (spear, acc * 100))

    order = {'high': 0, 'medium': 1, 'low': 2}
    out.sort(key=lambda s: order.get(s['level'], 3))
    return out


def recommend(variants, llm_comment=None):
    """聚合 + 规则建议；llm_comment 可由外部算好（LLM 综合点评文本）直接带上。"""
    agg = aggregate(variants)
    suggestions = prompt_suggestions(agg)
    return {'aggregate': agg, 'variants': agg.get('variants', {}),
            'suggestions': suggestions,
            'llm_comment': llm_comment or ''}


def recommend_llm(agg, cfg, run_dir):
    """在规则建议之外再调一次模型做综合点评（--llm-recommend）。失败不致命。"""
    from llm import chat, LLMError
    from eval.matcher import matcher_prompts as _mp
    prompt = _mp.build_recommend_prompt(json.dumps(agg, ensure_ascii=False, indent=2))
    try:
        return chat(prompt, stage='eval_matcher_recommend', run_dir=run_dir, cfg=cfg).strip()
    except (LLMError, Exception) as exc:                     # noqa: BLE001
        return '（LLM 点评失败，仅保留规则建议：%s）' % exc