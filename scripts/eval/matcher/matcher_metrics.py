#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""匹配评估的**纯函数层**：四族指标。输入 gold/pred 样本，输出结构化数字与明细。

刻意不 import llm、不触网 —— 评估只有「读过 gold 与预测 → 算数」两件事。

同一套样本契约贯穿所有函数：
  sample = {
      'link': str,                          # 对齐键（可选，仅用于回显）
      'gold':  {'category': str, 'score': float, 'matched_skills': [str],
                'missing_skills': [str]},   # oracle，独立于两条预测路径
      'rule':  {'category': str, 'match_score': float,   # 规则分 0-115 原生
                'matched_skills': [str], 'missing_skills': [str]},
      'deep':  {'deep_score': float or None, 'category': str or None,  # 深度 0-100
                'missing_skills': [str] or None},
      'blended': {'category': str, 'blended_score': float},             # 0-100
  }
  rule/deep/blended 三变体各自作为一套 `{category, score, matched_skills, missing_skills}`
  预测喂给下面的族函数；deep 变体没有 matched_skills（deep/LLM 只产 missing），
  这是**固有不对称**，报告须写明而非静默填零。

刻度契约：gold.score 恒为 0-100；rule.match_score 恒为 0-115 原生，比较前先经
`_to_100` 归一（同 merge_deep_results 的 `min(100, s/115*100)`）。deep/blended 已是 0-100。
"""

import math

# ==================== 刻度 ====================

_LABEL_ORDER = ('qualified', 'need_optimization', 'cannot_apply')
_GRADE = {'qualified': 2, 'need_optimization': 1, 'cannot_apply': 0}

RULE_MAX = 115.0


def _to_100(score, copy_native=False):
    """规则分归一：0-115 → 0-100（同 merge_deep_results）。copy_native 另附原生 0-115 刻度。"""
    out = {'score100': min(100.0, float(score) / RULE_MAX * 100.0) if score is not None else None}
    if copy_native and score is not None:
        out['score_native'] = float(score)
    return out


# ==================== 1) 分类 ====================

def classification_eval(gold_cats, pred_cats):
    """分类准确率 + 混淆 + 逐类 P/R/F1 + 系统性过/欠预测（classification_gap）。

    - label 预填三类键防止 KeyError；某类支持数 0 时 P/R/F1 给 0.0（非 NaN）。
    - classification_gap：每类 (gold数, pred数, delta=pred-gold)。delta>0 系统性多投、
      <0 系统性漏投 —— 直接暴露「过度乐观/悲观」的裁定倾向，是单看 accuracy 看不到的。
    """
    n = len(gold_cats)
    if n == 0:
        return {'accuracy': 0.0,
                'confusion': {p: {g: 0 for g in _LABEL_ORDER} for p in _LABEL_ORDER},
                'per_class': {c: {'precision': 0.0, 'recall': 0.0, 'f1': 0.0,
                                  'support': 0, 'predicted': 0} for c in _LABEL_ORDER},
                'classification_gap': {c: [0, 0, 0] for c in _LABEL_ORDER},
                'n': 0, 'label_order': list(_LABEL_ORDER)}

    confusion = {p: {g: 0 for g in _LABEL_ORDER} for p in _LABEL_ORDER}
    gold_count = {c: 0 for c in _LABEL_ORDER}
    pred_count = {c: 0 for c in _LABEL_ORDER}
    correct = 0
    for g, p in zip(gold_cats, pred_cats):
        confusion[p][g] += 1
        gold_count[g] += 1
        pred_count[p] += 1
        if p == g:
            correct += 1

    per_class = {}
    for c in _LABEL_ORDER:
        tp = confusion[c].get(c, 0)
        fp = pred_count[c] - tp
        fn = gold_count[c] - tp
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        per_class[c] = {'precision': precision, 'recall': recall, 'f1': f1,
                        'support': gold_count[c], 'predicted': pred_count[c]}

    gap = {c: [gold_count[c], pred_count[c], pred_count[c] - gold_count[c]]
           for c in _LABEL_ORDER}
    return {'accuracy': correct / n, 'confusion': confusion,
            'per_class': per_class, 'classification_gap': gap,
            'n': n, 'label_order': list(_LABEL_ORDER)}


# ==================== 2) 分数误差 ====================

def _mae(pairs):
    return sum(abs(a - b) for a, b in pairs) / len(pairs) if pairs else 0.0


def _rmse(pairs):
    if not pairs:
        return 0.0
    return math.sqrt(sum((a - b) ** 2 for a, b in pairs) / len(pairs))


def score_error_eval(gold_scores, pred_scores, copy_native=False):
    """分数误差：MAE / RMSE / 有符号 bias（mean(pred - gold)）+ 逐样本明细。

    bias<0 = 系统性打低，bias>0 = 系统性打高。copy_native=True 时在逐样本内附
    规则原生 0-115 刻度（native_scale_mae），供看「归一到 100 后缩掉的误差差距」。
    """
    pairs = [(float(g), float(p)) for g, p in zip(gold_scores, pred_scores)
             if g is not None and p is not None]
    biases = [p - g for g, p in pairs]
    per_sample = []
    for idx, (g, p) in enumerate(pairs):
        row = {'index': idx, 'gold': g, 'pred': p, 'abs_err': abs(p - g)}
        if copy_native and idx < len(pred_scores):
            native = pred_scores[idx]           # 原始 0-115（_to_100 的输入）
            if isinstance(native, (int, float)) and native > 100:
                row['pred_native'] = native
        per_sample.append(row)
    native_mae = None
    if copy_native:
        natives = [p for p in pred_scores if isinstance(p, (int, float))]
        if natives:
            native_mae = _mae([(float(g), float(p)) for g, p in zip(gold_scores, natives)
                               if g is not None and p is not None])
    return {'mae': _mae(pairs), 'rmse': _rmse(pairs),
            'bias': (sum(biases) / len(biases)) if biases else 0.0,
            'n': len(pairs), 'per_sample': per_sample,
            'native_scale_mae': native_mae}


# ==================== 3) 排序质量 ====================

def _dcg(grades):
    """DCG = Σ grade_i / log2(i+2)（i 从 0 计）。"""
    return sum(g / math.log2(i + 2) for i, g in enumerate(grades))


def _spearman(a, b):
    """纯 Python 秩相关（不假设 scipy）。

    n<2 或任一侧是恒定值（方差 / 秩差为 0，相关无定义）→ None（从不 NaN/报错）。
    恒定判定走「值集合大小 <2」而不是先 rank——rank 会把恒定序列摊平成 {0..n-1}，
    反而造出伪秩差，让恒定输入误报成 1.0。
    """
    n = len(a)
    if n < 2:
        return None
    if len({float(x) for x in a}) < 2 or len({float(x) for x in b}) < 2:
        return None
    def ranks(xs):
        order = sorted(range(n), key=lambda i: xs[i])
        r = [0.0] * n
        for rank, i in enumerate(order):
            r[i] = rank
        return r
    ra, rb = ranks(a), ranks(b)
    sa, sb = sum(ra), sum(rb)
    sas, sbs = sum(x * x for x in ra), sum(x * x for x in rb)
    sab = sum(x * y for x, y in zip(ra, rb))
    denom = math.sqrt((sas - sa * sa / n) * (sbs - sb * sb / n))
    if denom == 0:
        return None
    return (sab - sa * sb / n) / denom


def ranking_eval(samples, k=None, relevance='category'):
    """排序：nDCG@k + 全程 nDCG + Spearman。relevance 决定「怎样的序才算好」。

    - relevance='category'（稳健默认）：gold 序数 grade=qualified2/need_opt1/cannot0，
      衡量「排在前面的岗位是否真是更该投的」。gold 与 pred 都用同序数。
    - relevance='score'（更灵敏）：用连续 gold.score 与 pred.score 的相关。
    nDCG：DCG(按 pred 序排的 gold 分) / DCG(按 gold 序排的 gold 分，即理想序)。
    """
    n = len(samples)
    if n == 0:
        return {'ndcg': 0.0, 'ndcg_at_k': 0.0, 'spearman': None, 'n': 0,
                'top_order': [], 'relevance': relevance}
    if relevance == 'category':
        gold_g = [float(_GRADE.get(s.get('gold_cat') or '', 0)) for s in samples]
        pred_grade = [float(_GRADE.get(s.get('pred_cat') or '', 0)) for s in samples]
        gold_spear, pred_spear = gold_g, pred_grade
    else:                                   # 'score'
        gold_g = [float(s.get('gold_score') or 0) for s in samples]
        pred_spear = [float(s.get('pred_score') or 0) for s in samples]
        gold_spear = gold_g
    ideal = sorted(gold_g, reverse=True)
    by_pred = [g for _, g in sorted(zip(pred_spear, gold_g), key=lambda x: -x[0])]
    dcg = _dcg(by_pred)
    idcg = _dcg(ideal)
    ndcg = dcg / idcg if idcg else 0.0
    ndcg_at_k = _dcg(by_pred[:k]) / (_dcg(ideal[:k]) if k else 0.0) if k else ndcg
    if k and len(ideal) >= k and _dcg(ideal[:k]):
        ndcg_at_k = _dcg(by_pred[:k]) / _dcg(ideal[:k])
    return {'ndcg': ndcg, 'ndcg_at_k': ndcg_at_k if k else ndcg,
            'spearman': _spearman(gold_spear, pred_spear),
            'n': n,
            'top_order': [s.get('link', s.get('index', i))
                          for i, s in enumerate(samples)],
            'relevance': relevance}


# ==================== 4) 技能命中 / 缺失 ====================

def _fold(skills, normalize):
    """谓词侧技能集折叠。normalize=True 时走 resume_matcher 的别名归一（k8s⇄kubernetes、
    es⇄elasticsearch，不算错）；False 只做去空，保留原文（精确匹配）。

    lazy import：skills_eval 在纯函数层，不承诺能导入 resume_matcher（离线单测也只需
    自身闭环），故只有当 normalize 真正需要别名表时才 import。
    """
    if not normalize:
        return set(skills or [])
    from resume_matcher.scoring import _normalize_skill
    return set(_normalize_skill(s) for s in (skills or []))


def _set_f1(gold_set, pred_set):
    """truth=gold_set、prediction=pred_set 的统一 P/R/F1。空集约定见 docstring。"""
    tp = len(gold_set & pred_set)
    fp = len(pred_set - gold_set)
    fn = len(gold_set - pred_set)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    # 空集约定：gold空+pred空 → P=R=1；gold空+pred非空 → P=0,R=1；pred空+gold非空 → P=1,R=0
    if not gold_set and not pred_set:
        precision = recall = f1 = 1.0
    elif not gold_set:
        precision, recall, f1 = 0.0, 1.0, 0.0
    elif not pred_set:
        precision, recall, f1 = 1.0, 0.0, 0.0
    return {'precision': precision, 'recall': recall, 'f1': f1,
            'tp': tp, 'fp': fp, 'fn': fn}


def skills_eval(samples, normalize=True):
    """matched / missing 各自的 P/R/F1。两侧按 set（normalize 折别名）比较。

    missing 只在**两侧都定义了**的样本里算（`samples_with_gold_missing`）——deep 变体
    只产 missing、matched 只有规则侧有，所以 matched/missing 各自的比较池可能不同。
    """
    matched_pairs, missing_pairs = [], []
    for s in samples:
        gold_m = s.get('gold_matched'); gold_x = s.get('gold_missing')
        pred_m = s.get('pred_matched'); pred_x = s.get('pred_missing')
        if gold_m is not None and pred_m is not None:
            matched_pairs.append((_fold(gold_m, normalize), _fold(pred_m, normalize)))
        if gold_x is not None and pred_x is not None:
            missing_pairs.append((_fold(gold_x, normalize), _fold(pred_x, normalize)))
    out = {'n': len(samples),
           'samples_with_gold_missing': len(missing_pairs)}
    # gold 为真、pred 为系统预测；集合比较取交集的集合为 pred∩gold
    if matched_pairs:
        acc = {'precision': 0.0, 'recall': 0.0, 'f1': 0.0, 'tp': 0, 'fp': 0, 'fn': 0}
        for gold, pred in matched_pairs:
            r = _set_f1(gold, pred)
            for k in acc:
                acc[k] += r[k]
        denom = len(matched_pairs)
        out['matched'] = {k: (v / denom if isinstance(v, (int, float)) else v)
                          for k, v in acc.items()}
    else:
        out['matched'] = {'precision': 0.0, 'recall': 0.0, 'f1': 0.0,
                          'tp': 0, 'fp': 0, 'fn': 0}
    if missing_pairs:
        acc = {'precision': 0.0, 'recall': 0.0, 'f1': 0.0, 'tp': 0, 'fp': 0, 'fn': 0}
        for gold, pred in missing_pairs:
            r = _set_f1(gold, pred)
            for k in acc:
                acc[k] += r[k]
        denom = len(missing_pairs)
        out['missing'] = {k: (v / denom if isinstance(v, (int, float)) else v)
                          for k, v in acc.items()}
    else:
        out['missing'] = {'precision': 0.0, 'recall': 0.0, 'f1': 0.0,
                          'tp': 0, 'fp': 0, 'fn': 0}
    return out


# ==================== 组合入口 ====================

def evaluate_matching(gold_jobs, preds, k=None, relevance='category', normalize=True):
    """一个「预测变体」的完整四族评估。gold_jobs 与 preds 按 index 对齐。

    preds 是逐岗预测（同一套变体），且使用统一形状字段：每项含
      {'index', 'category', 'score' | ('score100' 归一后), 'matched_skills', 'missing_skills',
       'gold_category', 'gold_score', 'gold_matched', 'gold_missing'}
    由调用方（evaluate_matcher）从 gold + 单变体预测拼好。坏样本记进 errors 并跳过
    （镜像 materials 逐岗 except 的保护），不整批崩。

    返回六键：classification / score_error / ranking / skills / n / errors。
    """
    ok = []
    errors = []
    for p in preds:
        g = p.get('gold')
        try:
            sample = {
                'link': p.get('link'),
                'index': p.get('index'),
                'gold_cat': g['category'],
                'pred_cat': p['category'],
                'gold_score': g['score'],
                'pred_score': p.get('score100', p.get('score')),
                'gold_matched': g.get('matched_skills'),
                'pred_matched': p.get('matched_skills'),
                'gold_missing': g.get('missing_skills'),
                'pred_missing': p.get('missing_skills'),
            }
            ok.append(sample)
        except Exception as exc:                                   # noqa: BLE001
            errors.append({'index': p.get('index'), 'error': '%s: %s' % (type(exc).__name__, exc)})

    ranking_samples = [{'link': s['link'], 'index': s['index'],
                        'gold_cat': s['gold_cat'], 'pred_cat': s['pred_cat'],
                        'gold_score': s['gold_score'], 'pred_score': s['pred_score']}
                       for s in ok]
    return {
        'classification': classification_eval(
            [s['gold_cat'] for s in ok], [s['pred_cat'] for s in ok]),
        'score_error': score_error_eval(
            [s['gold_score'] for s in ok], [s['pred_score'] for s in ok]),
        'ranking': ranking_eval(ranking_samples, k=k, relevance=relevance),
        'skills': skills_eval(ok, normalize=normalize),
        'n': len(ok),
        'errors': errors,
    }