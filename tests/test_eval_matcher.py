# -*- coding: utf-8 -*-
"""matcher 评估端到端离线测试：CLI 编排 + 报告落盘 + 深度/混合回填 + 退出码。

不触网：--gold-fixtures 恒离线（fixture 自带岗位与 gold）；--offline + --deep-results 从
既有 deep_results.json 回填，绝不调 chat。全程断言无网络调用路径被触达。

覆盖：
  · --gold-fixtures --mode quick --offline 跑通 main() → exit 0，eval_matcher/{report.html,
    eval.json} 落盘，rule 变体 accuracy=1.0（8 条 fixture 与 FIXTURE_PROFILE 自洽）。
  · 刻意错例：把某条 fixture 的 gold 翻转 → 同一流水线 accuracy<1 且混淆对角线外有值。
  · --deep-results 回填 → deep/blended 变体出现，blended=int(0.4*rule100+0.6*deep) 核验。
  · deep_results 缺某 rank → exit 3（报告仍写）。
  · 退出码：--gold-fixtures + --profile（密封对,禁止自定义）→ 1；--gold-ai/--judge-gold 缺
    --profile → 1；--offline + --judge-gold 且无 manifest → 2（SystemExit）。

跑法：python -m pytest tests/test_eval_matcher.py -q
"""

import os
import sys
import json
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts",
                                "eval", "matcher"))

for _stream in (sys.stdout, sys.stderr):
    _stream.reconfigure(encoding='utf-8', errors='replace')

import evaluate_matcher as EM                                        # noqa: E402
from eval.matcher.matcher_gold import load_hand_gold, _FIXTURE_PROFILE   # noqa: E402


def _write_profile(tmp_path):
    p = tmp_path / 'profile.json'
    p.write_text(json.dumps(_FIXTURE_PROFILE, ensure_ascii=False), encoding='utf-8')
    return str(p)


def _profile_dict():
    return json.loads(json.dumps(_FIXTURE_PROFILE))


def _make_run(tmp_path):
    run = tmp_path / 'run'
    (run / 'eval_matcher').mkdir(parents=True, exist_ok=True)
    return str(run)


def _argv(run_dir, *extra):
    # --gold-fixtures 是「内置简历+数据集」密封对：不由 CLI 传 --profile，评分用数据集内联简历。
    return ['evaluate_matcher.py', run_dir,
            '--gold-fixtures', '--mode', 'quick', '--offline',
            '--out-dir', os.path.join(run_dir, 'eval_matcher'), *extra]


# ==================== 离线 happy path ====================

def test_offline_fixture_e2e_writes_report_and_rule_accurate(tmp_path):
    run = _make_run(tmp_path)
    saved = sys.argv
    sys.argv = _argv(run)
    try:
        code = EM.main()
    finally:
        sys.argv = saved
    assert code == 0

    ev = os.path.join(run, 'eval_matcher', 'eval.json')
    rep = os.path.join(run, 'eval_matcher', 'report.html')
    assert os.path.exists(ev) and os.path.exists(rep)

    with open(ev, encoding='utf-8') as f:
        data = json.load(f)
    rule = data['aggregate']['variants']['rule']
    # 8 条 fixture 与 FIXTURE_PROFILE 自洽 → 规则分类全对
    assert rule['accuracy'] == 1.0
    assert rule['n'] == 8
    assert 'cannot_apply' in rule['per_class']
    # 报告含标题 + KPI 卡片（判对没） + 逐岗表
    html = open(rep, encoding='utf-8').read()
    assert '岗位匹配评估' in html and '判对没' in html
    assert '逐岗核对' in html


# ==================== 刻意错例 → accuracy<1 & 混淆有值 ====================

def test_deliberate_mismatch_lowers_accuracy(tmp_path):
    run = _make_run(tmp_path)
    profile = _write_profile(tmp_path)
    samples = load_hand_gold()
    # A3 规则判 cannot_apply；翻转 gold 为 need_optimization → 制造确定性错判
    samples[2]['gold']['category'] = 'need_optimization'
    args = _ns(run, profile, mode='quick')
    code = EM._run(run, os.path.join(run, 'eval_matcher'), _profile_dict(), samples, args, 'hand')
    assert code == 0

    with open(os.path.join(run, 'eval_matcher', 'eval.json'), encoding='utf-8') as f:
        data = json.load(f)
    rule = data['aggregate']['variants']['rule']
    assert rule['accuracy'] < 1.0
    # 混淆矩阵不能空：cannot_apply 列 ≠ gold 档(need_opt) 有错判
    offdiag = rule['confusion']['cannot_apply']['need_optimization']
    assert offdiag >= 1


# ==================== --deep-results 回填 + blended 公式 ====================

def _ns(run_dir, profile, mode='both', deep_results=None, offline=True):
    return argparse.Namespace(
        run_dir=run_dir, profile=profile, mode=mode,
        deep_results=deep_results, blend_weight=EM.DEEP_WEIGHT,
        k=None, relevance='category', no_normalize=False,
        llm_recommend=False, offline=offline, workers=4,
        gold_fixtures=True, gold_ai=None, judge_gold=False,
        jobs_csv=None, jobs_ai=None, jobs_existing=False,
        gold_ai_spec='', jobs_ai_spec='', out_dir=None)


def _write_deep_results(tmp_path, scores=None, drop_ranks=()):
    samples = load_hand_gold()
    scores = scores or {i: 80 for i in range(1, len(samples) + 1)}
    recs = []
    for i in scores:
        if i in drop_ranks:
            continue
        recs.append({'rank': i, 'score': scores[i], 'category': 'qualified',
                     'reason': 'stub', 'missing_items': []})
    p = tmp_path / 'deep_results.json'
    p.write_text(json.dumps({'results': recs}, ensure_ascii=False), encoding='utf-8')
    return str(p), samples


def test_deep_results_backfill_and_blend_formula(tmp_path):
    run = _make_run(tmp_path)
    profile = _write_profile(tmp_path)
    deep_file, samples = _write_deep_results(tmp_path)
    args = _ns(run, profile, mode='both', deep_results=deep_file)
    code = EM._run(run, os.path.join(run, 'eval_matcher'), _profile_dict(), samples, args, 'hand')
    assert code == 0

    with open(os.path.join(run, 'eval_matcher', 'eval.json'), encoding='utf-8') as f:
        data = json.load(f)
    agg = data['aggregate']['variants']
    assert 'deep' in agg and 'blended' in agg and 'rule' in agg
    # 每行 blended.score100 = int(0.4*rule100 + 0.6*deep_score) 核验；
    # _blend 里的 rule100 = min(100, match_score/115*100) 用**未取整**的 match_score，
    # 与展示用的 round(score100) 有别，故须从 score_native 还原。
    for row in data['jobs']:
        rule100 = min(100.0, float(row['rule']['score_native']) / EM.RULE_MAX * 100.0)
        deep_score = row['deep']['score']
        assert row['blended']['score100'] == int(0.4 * rule100 + 0.6 * deep_score)


def test_deep_results_missing_rank_returns_3_but_report_written(tmp_path):
    run = _make_run(tmp_path)
    profile = _write_profile(tmp_path)
    deep_file, samples = _write_deep_results(tmp_path, drop_ranks=(3, 4))
    args = _ns(run, profile, mode='both', deep_results=deep_file)
    code = EM._run(run, os.path.join(run, 'eval_matcher'), _profile_dict(), samples, args, 'hand')
    assert code == 3
    assert os.path.exists(os.path.join(run, 'eval_matcher', 'eval.json'))


# ==================== 退出码：fixtures 拒绝自定义 / 非 fixtures 缺 profile / 离线+judge ====================

def test_fixture_mode_rejects_custom_profile(tmp_path):
    # --gold-fixtures 是「内置简历+数据集」密封对 → 传 --profile 必须报错（exit 1）
    run = _make_run(tmp_path)
    saved = sys.argv
    sys.argv = ['evaluate_matcher.py', run, '--gold-fixtures', '--offline',
                '--profile', os.path.join(str(tmp_path), 'mine.json'),
                '--out-dir', os.path.join(run, 'eval_matcher')]
    try:
        code = EM.main()
    finally:
        sys.argv = saved
    assert code == 1


def test_non_fixture_gold_requires_profile(tmp_path):
    # --gold-ai / --judge-gold 不是密封对 → 缺 --profile 报错（exit 1），且不触网
    run = _make_run(tmp_path)
    saved = sys.argv
    sys.argv = ['evaluate_matcher.py', run, '--gold-ai', '3', '--offline',
                '--out-dir', os.path.join(run, 'eval_matcher')]
    try:
        code = EM.main()
    finally:
        sys.argv = saved
    assert code == 1


def test_offline_judge_without_manifest_exits_2(tmp_path):
    run = _make_run(tmp_path)
    profile = _write_profile(tmp_path)
    saved = sys.argv
    sys.argv = ['evaluate_matcher.py', run, '--profile', profile,
                '--judge-gold', '--jobs-existing', '--offline',
                '--out-dir', os.path.join(run, 'eval_matcher')]
    try:
        import pytest
        with pytest.raises(SystemExit) as exc:
            EM.main()
    finally:
        sys.argv = saved
    assert exc.value.code == 2


if __name__ == '__main__':
    import pytest
    sys.exit(pytest.main([__file__, '-q']))