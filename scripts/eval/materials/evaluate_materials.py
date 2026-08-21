#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""质量评估工具的唯一 CLI 入口 —— 编排「准备 state → 造/取岗位 → 生成材料 → 六维评估 → 报告」。

一次调用分两段（都可被 --generate / --offline 组合控制）：

1. **准备**：搭好一个工作 run_dir 的 state/（resume_text.txt + profile.json + qualified_jobs.json）。
   - 简历：`--resume FILE`（md/txt 直接读）写 resume_text.txt；否则复用 run_dir 既有 state。
   - 岗位：`--jobs-ai N`（AI 造多样岗位，默认要触网）/ `--jobs-csv PATH`（本地 CSV，离线可）/ `--jobs-existing`（复用 run_dir 既有 qualified_jobs.json）。
2. **评估**（永远离线：只读产物 + 纯函数，不触网）：
   - `--generate`：先按 skill 原逻辑（gen_materials.gen_greeting / gen_resume）生成回头语与优化简历，
     再评估。`--offline` 下生成改为确定性 stub（干净对照，不花钱不触网）。
   - 默认（不带 --generate）：直接评估 run_dir 已落盘的 materials/ 产物。

产物：`{run_dir}/eval/eval.json` + `{run_dir}/eval/report.html`。退出码沿用仓库 0/1/3。
"""

import os
import sys
import glob
import json
import argparse
from concurrent.futures import ThreadPoolExecutor

# 本文件位于 scripts/eval/materials/，上溯两级拿到 scripts/ 根，才能
# `import eval.materials.*`（需 scripts/eval/__init__.py 在 path 内）并
# import llm / resume_matcher / verify 等 scripts/ 下的包。
_SCRIPTS = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
for _p in (_SCRIPTS,
           os.path.join(_SCRIPTS, 'stages'),
           os.path.join(_SCRIPTS, 'verify'),
           os.path.join(_SCRIPTS, 'resume_matcher')):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def reconfigure_stdout():
    for stream in (sys.stdout, sys.stderr):       # Windows 控制台是 GBK
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass


# 延迟 import：gen_materials 顶层会拉 llm，只有真需要生成/读产物时才 import（避免 offline 评估
# 也被 llm 配置挡住）。metrics 等纯函数子模块随时可 import。
def _gm():
    from gen_materials import (load_profile, build_resume_summary, format_availability,
                               gen_greeting, gen_resume, safe_name,
                               _write_atomic)
    return (load_profile, build_resume_summary, format_availability,
            gen_greeting, gen_resume, safe_name, _write_atomic)


# ---- 产物/路径 helper（与仓库 paths.py 一致，离线评估也要能定位材料）----
def _state_path(run_dir, name):
    return os.path.join(run_dir, 'state', name)


def _materials(run_dir):
    return os.path.join(run_dir, 'materials')


def _find(run_dir, kind, index):
    """找 materials/greeting_{i}_*.txt 或 resume_{i}_*.json。返回路径或 None。"""
    if not os.path.isdir(_materials(run_dir)):
        return None
    hits = sorted(glob.glob(os.path.join(_materials(run_dir), '%s_%d_*' % (kind, index))))
    for h in hits:
        if os.path.getsize(h) > 0:
            return h
    return None


# ==================== 1) 准备 state ====================

def _minimal_profile():
    """空 profile 兜底：gen_materials 的读 profile 函数都允许空结构（summary/availability 空）。"""
    return {'basic_info': {}, 'education': {}, 'skills': {}, 'projects': [],
            'experience': {}, 'awards': [], 'publications': []}


def _read_resume_file(path):
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    with open(path, 'r', encoding='utf-8') as f:
        text = f.read().strip()
    if len(text) < 50:
        raise ValueError('简历文本太短（<50 字），无法作为优化基底')
    return text


def prepare_state(run_dir, resume_file=None):
    """保证 state/resume_text.txt 与 profile.json 存在。返回 (profile, base_text)。"""
    os.makedirs(os.path.join(run_dir, 'state'), exist_ok=True)
    rt_path, pf_path = _state_path(run_dir, 'resume_text.txt'), _state_path(run_dir, 'profile.json')

    if resume_file:
        text = _read_resume_file(resume_file)
        with open(rt_path, 'w', encoding='utf-8') as f:
            f.write(text)
    base_text = None
    if os.path.exists(rt_path):
        with open(rt_path, 'r', encoding='utf-8') as f:
            base_text = f.read().strip()

    profile = None
    if os.path.exists(pf_path):
        with open(pf_path, 'r', encoding='utf-8') as f:
            profile = json.load(f)
    if profile is None:
        profile = _minimal_profile()
        if not os.path.exists(pf_path):
            with open(pf_path, 'w', encoding='utf-8') as f:
                json.dump(profile, f, ensure_ascii=False, indent=2)
    return profile, base_text


# ==================== 2) 岗位来源 ====================

def resolve_jobs(run_dir, profile, args, offline):
    """按 args 决定岗位列表，写 state/qualified_jobs.json。返回 job dict 数组。"""
    qj = _state_path(run_dir, 'qualified_jobs.json')
    jobs = None

    if args.jobs_csv:
        from eval.materials.gen_test_jobs import load_jobs_csv
        jobs = load_jobs_csv(args.jobs_csv)
    elif args.jobs_ai:
        if offline:
            print('⚠ --offline 下 --jobs-ai 会触网，已降级：改用 --jobs-csv 或 --jobs-existing。')
            jobs = _load_existing_jobs(run_dir)
            if not jobs:
                raise ValueError('offline 且无既有岗位：请传 --jobs-csv 或 --jobs-existing')
        else:
            from eval.materials.gen_test_jobs import build_jobs_ai
            _, build_resume_summary, _, *_ = _gm()
            summary = build_resume_summary(profile)
            from llm import resolve, ConfigError
            try:
                cfg = resolve(stage='gen_test_jobs')
            except ConfigError as exc:
                print('❌ 配置错误：\n%s' % exc)
                raise
            jobs = build_jobs_ai(summary, args.jobs_ai, cfg, run_dir, args.jobs_ai_spec)
    else:                 # --jobs-existing 或默认：复用 run_dir 既有 job 池
        jobs = _load_existing_jobs(run_dir)

    if not jobs:
        raise ValueError('没有可用的岗位来源：--jobs-ai / --jobs-csv / --jobs-existing 三选一，'
                         '且 run_dir 无既有 qualified_jobs.json')

    with open(qj, 'w', encoding='utf-8') as f:
        json.dump(jobs, f, ensure_ascii=False, indent=2)
    print('✅ 岗位 %d 个 → %s' % (len(jobs), qj))
    return jobs


def _load_existing_jobs(run_dir):
    from eval.materials.gen_test_jobs import load_jobs_existing
    try:
        return load_jobs_existing(run_dir)
    except FileNotFoundError:
        return None


# ==================== 3) 生成材料 ====================

def _make_availability(profile):
    _, _, format_availability, *_ = _gm()
    return format_availability(profile)


def generate_materials(run_dir, jobs, profile, args, offline):
    """真调（非 offline）或 stub（offline）生成。返回 {index: {'greeting':…, 'resume_md':…}}。"""
    (load_profile, build_resume_summary, format_availability,
     gen_greeting, gen_resume, safe_name, _write_atomic) = _gm()
    rt_path = _state_path(run_dir, 'resume_text.txt')
    with open(rt_path, 'r', encoding='utf-8') as f:
        base_resume = f.read().strip()
    os.makedirs(_materials(run_dir), exist_ok=True)

    if offline:
        from eval.materials.stubs import clean_greeting, clean_resume
        records = {}
        for i, job in enumerate(jobs, 1):
            jd = '、'.join(str(v) for v in
                           (job.get('技能标签'), job.get('岗位要求和职责'), job.get('职位'),
                            job.get('公司')) if v)
            greet = clean_greeting(job, _make_availability(profile))
            md, driven = clean_resume(base_resume, jd)
            records[i] = {'greeting': greet, 'resume_md': md}
            path = os.path.join(_materials(run_dir),
                                'greeting_%d_%s.txt' % (i, safe_name(job.get('公司'))))
            _write_atomic(path, greet)
            data = {'optimized_resume': md,
                    'optimization_suggestions': {},
                    'key_changes': [], 'stub_driven': driven}
            rpath = os.path.join(_materials(run_dir),
                                 'resume_%d_%s.json' % (i, safe_name(job.get('公司'))))
            _write_atomic(rpath, json.dumps(data, ensure_ascii=False, indent=2))
        print('✅ --offline stub 生成 %d 组材料（确定性，未触网）' % len(jobs))
        return records

    # ── 真调：复用 skill 原逻辑 ──
    from llm import resolve, ConfigError

    try:
        cfg_g = resolve(stage='greeting')
        cfg_r = resolve(stage='resume')
    except ConfigError as exc:
        print('❌ 配置错误：\n%s' % exc)
        return None
    def work(i):
        job = jobs[i - 1]
        match = {}
        g_row = r_row = None
        try:
            g_text, _ = gen_greeting(job, profile, match, cfg_g, run_dir, 'ai')
        except Exception as exc:                                   # noqa: BLE001
            g_text = None
            print('  ⚠ 岗位 #%d 招呼语失败: %s' % (i, exc))
        try:
            data = gen_resume(job, base_resume, match, cfg_r, run_dir)
        except Exception as exc:                                   # noqa: BLE001
            data = None
            print('  ⚠ 岗位 #%d 简历失败: %s' % (i, exc))
        if g_text:
            path = os.path.join(_materials(run_dir),
                                'greeting_%d_%s.txt' % (i, safe_name(job.get('公司'))))
            _write_atomic(path, g_text); g_row = path
        if data:
            rpath = os.path.join(_materials(run_dir),
                                 'resume_%d_%s.json' % (i, safe_name(job.get('公司'))))
            _write_atomic(rpath, json.dumps(data, ensure_ascii=False, indent=2)); r_row = rpath
        return i, g_row, r_row, data

    workers = args.workers or 4
    print('真调生成 %d 组材料（并发 %d）……' % (len(jobs), workers))
    records = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, g_row, r_row, data in ex.map(work, range(1, len(jobs) + 1)):
            greet = ''
            if g_row:
                with open(g_row, 'r', encoding='utf-8') as f:
                    greet = f.read()
            records[i] = {'greeting': greet,
                          'resume_md': (data or {}).get('optimized_resume', '') or ''}
    return records


# ==================== 4) 六维评估 ====================

def evaluate_run(run_dir, jobs, profile, base_text, args, classified=None):
    """逐岗评估：读产物（materials/ 或 generate 内存 records 已落盘）→ metrics。

    classified: {岗号: terms dict}（LLM 术语分类结果，缓存优先）。有则该岗走
    terms_source='llm'，否则回退规则。同时把该岗的原/优化简历文本塞进 jobs_view，
    供 report_html 渲染「优化前 vs 优化后」对照。
    """
    from eval.materials.metrics import evaluate_job
    from verify_no_fabrication import load_baseline, _baseline_keys
    from gen_materials import format_availability

    baseline, _ = load_baseline(run_dir)
    availability = format_availability(profile)
    classified = classified or {}
    jobs_view, missing = [], []

    for i, job in enumerate(jobs, 1):
        greet_path, resume_path = _find(run_dir, 'greeting', i), _find(run_dir, 'resume', i)
        greeting = ''
        if greet_path:
            with open(greet_path, 'r', encoding='utf-8') as f:
                greeting = f.read().strip()
        opt_md = ''
        if resume_path:
            with open(resume_path, 'r', encoding='utf-8') as f:
                opt_md = (json.load(f) or {}).get('optimized_resume') or ''
        missing_now = not (greet_path and resume_path)
        if missing_now:
            missing.append(i)

        if missing_now:
            # 无产物：不是「删光原文」，是压根没生成 → 六维不做判定，KPI 聚合自动略过空 metrics
            jobs_view.append({
                'index': i, 'company': job.get('公司', ''),
                'position': job.get('职位', ''), 'link': job.get('link', ''),
                'status': 'missing', 'greeting_preview': '', 'greeting_full': '',
                'base_text': '', 'optimized_resume': '',
                'metrics': {},
            })
            continue

        jd = '、'.join(str(v) for v in
                       (job.get('技能标签'), job.get('岗位要求和职责'), job.get('职位'),
                        job.get('公司')) if v)
        jd_keys = _baseline_keys(jd)

        # 该岗是否有 LLM 分类结果：有则走 llm，无则回退规则
        use_llm = i in classified and classified[i].get('mode') == 'llm'
        try:
            metrics = evaluate_job(
                base_text=base_text or '',
                baseline=baseline,
                jd_keys=jd_keys,
                greeting_text=greeting,
                optimized_resume=opt_md,
                availability_text=availability,
                subjective=not args.no_subjective,
                terms_source='llm' if use_llm else 'rule',
                classified_terms=classified.get(i) if use_llm else None,
            )
        except Exception as exc:                                   # noqa: BLE001
            metrics = {'error': str(exc)}
            print('  ⚠ 岗位 #%d 评估失败: %s' % (i, exc))

        from eval.materials.metrics import preview as _pv
        jobs_view.append({
            'index': i,
            'company': job.get('公司', ''),
            'position': job.get('职位', ''),
            'link': job.get('link', ''),
            'status': 'missing' if missing_now else 'ok',
            'greeting_preview': _pv(greeting),
            'greeting_full': greeting,
            'base_text': (base_text or ''),
            'optimized_resume': opt_md,
            'terms_mode': 'llm' if use_llm else 'rule',
            'metrics': metrics,
        })
    return jobs_view, missing


# ==================== 5) 报告 ====================

def write_report(run_dir, recommend_result, jobs_view, meta, out_dir=None):
    from eval.materials.report_html import render_html, write_eval_json
    out = out_dir or os.path.join(run_dir, 'eval')
    os.makedirs(out, exist_ok=True)
    html_path = os.path.join(out, 'report.html')
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(render_html(recommend_result, jobs_view, meta))
    json_path = write_eval_json(os.path.join(out, 'eval.json'), recommend_result,
                                jobs_view, meta)
    return html_path, json_path


def main():
    reconfigure_stdout()
    ap = argparse.ArgumentParser(
        description='批量生成并多角度评估 招呼语 + 优化简历 质量（幻觉/删除/新增/章节/招呼语/客观性）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='两种模式：\n'
               '  离线评估既有产物（默认不生成，不触网）:\n'
               '    python scripts/eval/materials/evaluate_materials.py <run_dir> --jobs-existing\n'
               '  真调生成+评估（花钱）:\n'
               '    python scripts/eval/materials/evaluate_materials.py <工作run> --resume 简历.md --jobs-ai 8 --generate\n')
    ap.add_argument('run_dir', help='工作运行目录（缺 state 会自动搭 state/）')
    ap.add_argument('--resume', help='简历文件（md/txt，写为 state/resume_text.txt；缺省复用既有 state）')
    ap.add_argument('--jobs-ai', type=int, metavar='N', help='用 AI 造 N 个多样岗位（需联网/配 key）')
    ap.add_argument('--jobs-csv', help='读岗位 CSV（中文列，离线可用）')
    ap.add_argument('--jobs-ai-spec', default='', help='AI 造岗的多样性补充说明')
    ap.add_argument('--jobs-existing', action='store_true',
                    help='复用工作 run_dir 已有 state/qualified_jobs.json')
    ap.add_argument('--generate', action='store_true', help='先生成材料再评估（默认评估既有产物）')
    ap.add_argument('--offline', action='store_true', help='全程不触网：生成降级为确定性 stub，AI 造岗自动降级')
    ap.add_argument('--stub-registry', help='stub 生成的产物 registry 落盘路径（可回放）')
    ap.add_argument('--llm-recommend', action='store_true', help='额外调模型做综合点评（需联网，避开 offline）')
    ap.add_argument('--terms-llm', action='store_true',
                    help='术语三分类收 LLM 语义判断（缓存优先；需联网/配 key，倒回规则兜底）')
    ap.add_argument('--force-llm-terms', action='store_true',
                    help='忽略术语分类缓存，强制重新调 LLM 分类')
    ap.add_argument('--no-subjective', action='store_true', help='关闭客观性(夸大)启发维度')
    ap.add_argument('--out-dir', help='报告输出目录（默认 <run_dir>/eval/）')
    ap.add_argument('--workers', '-w', type=int, help='生成并发数（默认 4）')
    ap.add_argument('--model'); ap.add_argument('--base-url'); ap.add_argument('--api-key')
    args = ap.parse_args()

    if args.offline and args.llm_recommend:
        print('❌ --offline 与 --llm-recommend 冲突：点评要调模型。', file=sys.stderr)
        return 2

    run_dir = os.path.abspath(args.run_dir)
    if not os.path.isdir(run_dir):
        os.makedirs(run_dir, exist_ok=True)

    # 1) state
    try:
        profile, base_text = prepare_state(run_dir, args.resume)
    except (FileNotFoundError, ValueError) as exc:
        print('❌ %s' % exc)
        return 1
    if not base_text:
        print('❌ 没有可用的简历文本（state/resume_text.txt 缺失，请 --resume 指定）')
        return 1

    # 2) 岗位
    try:
        jobs = resolve_jobs(run_dir, profile, args, args.offline)
    except (ValueError, FileNotFoundError) as exc:
        print('❌ %s' % exc)
        return 1
    if not jobs:
        print('❌ 岗位列表为空')
        return 1

    # 3) 生成（可选）
    if args.generate:
        generated = generate_materials(run_dir, jobs, profile, args, args.offline)
        if generated is None:
            return 1
        if args.stub_registry and args.offline:
            from eval.materials.stubs import StubRun
            run = StubRun()
            for i, rec in generated.items():
                run.register_greeting(i, rec['greeting'])
                run.register_resume(i, rec['resume_md'], [])
            run.save(args.stub_registry)
            print('✅ stub registry → %s' % args.stub_registry)

    # 4) 术语分类（LLM，缓存优先）
    #    - --terms-llm 或 --force-llm-terms：调分类器（联网），写缓存；force 忽略缓存。
    #    - --offline：只读已有缓存，不回退联网；没缓存那几岗走规则兜底（报里标 rule）。
    classified = None
    if args.terms_llm or args.force_llm_terms or args.offline:
        from eval.materials.terms_llm import classify_all
        from gen_materials import build_resume_summary
        opt_by_index = {}
        for i in range(1, len(jobs) + 1):
            rp = _find(run_dir, 'resume', i)
            if rp:
                with open(rp, 'r', encoding='utf-8') as _f:
                    opt_by_index[i] = ((json.load(_f) or {})
                                       .get('optimized_resume') or '')
        if args.terms_llm or args.force_llm_terms:
            from llm import resolve, ConfigError
            try:
                cfg = resolve(stage='eval_terms')
            except ConfigError as exc:
                print('❌ 术语分类配置错误：\n%s' % exc)
                return 1
            classified = classify_all(jobs, base_text or '', opt_by_index,
                                      cfg=cfg, run_dir=run_dir,
                                      offline=args.offline, force=args.force_llm_terms)
        else:   # 仅 --offline：读缓存即可
            classified = classify_all(jobs, base_text or '', opt_by_index,
                                      run_dir=run_dir, offline=True)
        n_llm = sum(1 for d in (classified or {}).values()
                    if (d or {}).get('mode') == 'llm')
        if n_llm:
            print('✅ 术语分类：%d/%d 岗走 LLM（缓存优先）' % (n_llm, len(jobs)))

    # 5) 评估
    jobs_view, missing = evaluate_run(run_dir, jobs, profile, base_text, args,
                                      classified=classified)
    if not jobs_view:
        print('❌ 没有任何岗位可供评估')
        return 1

    # 5) 聚合 + 建议 (+LLM 点评)
    from eval.materials.recommend import recommend, recommend_llm
    llm_comment = ''
    if args.llm_recommend and not args.offline:
        from llm import resolve, ConfigError
        try:
            cfg = resolve(stage='eval_recommend')
            llm_comment = recommend_llm(recommend([j['metrics'] for j in jobs_view]), cfg, run_dir)
        except (ConfigError, Exception) as exc:                    # noqa: BLE001
            print('⚠ LLM 点评跳过: %s' % exc)
    rec = recommend([j['metrics'] for j in jobs_view], llm_comment=llm_comment)

    meta = 'run_dir: %s · 岗位 %d · %s · 生成=%s offline=%s' % (
        run_dir, len(jobs), ('简历 ' + args.resume) if args.resume else '复用既有 state',
        '是' if args.generate else '否', '是' if args.offline else '否')

    try:
        html_path, json_path = write_report(run_dir, rec, jobs_view, meta, args.out_dir)
    except Exception as exc:                                        # noqa: BLE001
        print('❌ 报告写出失败: %s' % exc)
        return 1

    agg = rec.get('aggregate', {})
    print('\n📊 评估完成：%d 岗' % len(jobs_view))
    print('   平均无据术语 %.1f%% · 岗位驱动 %.1f%% · 原文保留 %.1f%%'
          % (agg.get('terms', {}).get('avg_hallucination_pct', 0) * 100,
             agg.get('terms', {}).get('avg_jd_driven_pct', 0) * 100,
             agg.get('char_diff', {}).get('avg_coverage_pct', 1) * 100))
    print('   章节缺失 %d 岗 · 前15字客套 %d 条 · 编造到岗承诺 %d 条'
          % (agg.get('chapters', {}).get('n_jobs_missing', 0),
             agg.get('greeting', {}).get('n_wasted_preview', 0),
             agg.get('greeting', {}).get('n_fabricated_commitment', 0)))
    print('   建议 %d 条' % len(rec.get('suggestions', [])))
    print('   📄 %s' % html_path)
    if missing:
        print('⚠ 缺产物岗位: %s' % '、'.join(map(str, missing)))
        return 3
    return 0


if __name__ == '__main__':
    sys.exit(main())