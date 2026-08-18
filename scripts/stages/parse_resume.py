#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""简历文件 → resume_text.txt + profile.json（走 OpenAI 兼容接口）。

流水线的 parse 阶段：抽文本 + 一次 `chat_json` 结构化，再跑一遍字段校验
（profile_validation.json）。

用法：
    python scripts/stages/parse_resume.py 简历.pdf
    python scripts/stages/parse_resume.py 简历.pdf --output-dir assets/2026-08-15_10-00-00
    python scripts/stages/parse_resume.py 简历.md --cross-check       # 校验有缺口时再让模型复核一遍
    python scripts/stages/parse_resume.py 简历.pdf --model gpt-4o     # 单次覆盖模型

支持 PDF / Word / md / markdown / txt（由 resume_matcher.parse_resume_file 决定）。

退出码：0 = 成功（校验可能有提示），1 = 失败，2 = 用法错误。
"""

import os
import sys
import json
import argparse

_SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _SCRIPTS)

import stage_timer
from resume_matcher import parse_resume_file, create_run_dir
from resume_matcher.paths import (profile_path as _profile_path,
                                  resume_text_path as _text_path,
                                  profile_validation_path as _validation_path)
from resume_matcher.prompts import get_resume_parse_prompt
from validate_profile import run_validation
from llm import ConfigError, LLMError, chat_json, format_usage, reconfigure_stdout

# profile.json 的顶层字段与各自的空值。run_matcher.load_profile 用 `data.get(k, 默认)`
# 读它们，而 **模型经常把没有的字段写成显式 null** —— 那时 get 的默认值不会生效，
# 下游拿到 None 就炸。所以在写盘前统一归一化，这个坑在本仓库踩过两次。
_SCHEMA = {
    'basic_info': dict,
    'education': dict,
    'experience': dict,
    'skills': dict,
    'projects': list,
    'awards': list,
    'publications': list,
    'social_links': dict,
    'salary_expectation': dict,
    'keywords': list,
    'raw_text': str,
}


def normalize_profile(data):
    """补齐缺失字段、把显式 null 换成对应类型的空值、丢弃 schema 外的键。

    丢弃未知键是为了让 profile.json 的形状可预测 —— 模型偶尔会自作主张多加
    `summary` / `analysis` 之类的字段，留着它们只会让下游读的人猜。
    """
    if not isinstance(data, dict):
        raise LLMError('模型返回的不是 JSON 对象，而是 %s' % type(data).__name__)

    out = {}
    dropped = []
    for key, kind in _SCHEMA.items():
        value = data.get(key)
        if value is None or not isinstance(value, kind):
            out[key] = kind()
        else:
            out[key] = value
    for key in data:
        if key not in _SCHEMA:
            dropped.append(key)
    return out, dropped


def _profile_stats(profile):
    skills = profile.get('skills') or {}
    skill_count = 0
    for value in skills.values():
        if isinstance(value, list):
            skill_count += len(value)
        elif value:
            skill_count += 1
    exp = profile.get('experience') or {}
    exp_items = exp.get('companies') if isinstance(exp, dict) else None
    return {
        'skills': skill_count,
        'projects': len(profile.get('projects') or []),
        'companies': len(exp_items or []),
        'keywords': len(profile.get('keywords') or []),
    }


def cross_check(resume_text, profile, report, run_dir, cfg_overrides):
    """让模型带着「校验发现的缺口」再看一遍简历，返回修正后的 profile（或 None）。

    只在 run_validation 报了缺口/提示时才值得跑：它是第二次全文推理，成本与第一次
    解析同级。校验脚本的技能检查是词典查表（KNOWN_TECH_TERMS），独立于产出 profile
    的那个模型 —— 这正是它有价值的地方，所以先跑脚本、再按脚本的结论决定要不要复核。
    """
    missing = report.get('missing_skills') or []
    hints = []
    if report.get('hint_projects'):
        hints.append('简历里出现但 profile 未收录的项目名：%s' % '、'.join(report['hint_projects']))
    if report.get('hint_companies'):
        hints.append('简历里出现但 profile 未收录的公司名：%s' % '、'.join(report['hint_companies']))
    for warning in report.get('hint_warnings') or []:
        hints.append(warning)

    prompt = (
        '你在复核一份简历的结构化解析结果。下面给出简历原文、当前解析结果，以及一个\n'
        '独立校验脚本发现的疑点。请修正解析结果。\n\n'
        '严格要求：\n'
        '1. 只能补充和纠正简历原文里**确实存在**的信息，绝对不许编造技能、经历或项目。\n'
        '2. 校验脚本用的是词典查表和正则，误报很常见。疑点仅供参考，简历里没有的就不要加。\n'
        '3. 保持字段结构与当前解析结果一致，输出完整的 JSON（不是补丁）。\n'
        '4. 只输出 JSON 本身，不要解释、不要代码块围栏。\n\n'
        '## 校验脚本认为可能漏掉的技能词\n%s\n\n'
        '## 其他疑点\n%s\n\n'
        '## 当前解析结果\n%s\n\n'
        '## 简历原文\n%s\n'
        % ('、'.join(missing) if missing else '（无）',
           '\n'.join('- ' + h for h in hints) if hints else '（无）',
           json.dumps(profile, ensure_ascii=False, indent=2),
           resume_text)
    )
    revised = chat_json(prompt, stage='parse', run_dir=run_dir, **cfg_overrides)
    normalized, _ = normalize_profile(revised)
    return normalized


def main():
    reconfigure_stdout()

    ap = argparse.ArgumentParser(
        description='解析简历文件为 profile.json（OpenAI 兼容接口）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='示例:\n'
               '  python scripts/stages/parse_resume.py 简历.pdf\n'
               '  python scripts/stages/parse_resume.py 简历.pdf --output-dir assets/2026-08-15_10-00-00 --cross-check\n')
    ap.add_argument('resume_file', help='简历文件路径（PDF / Word / md / txt）')
    ap.add_argument('--output-dir', '-o', help='运行目录，默认在 assets/ 下新建时间戳目录')
    ap.add_argument('--cross-check', action='store_true',
                    help='校验发现缺口时，再让模型带着缺口复核一遍（多一次全文推理）')
    ap.add_argument('--force', action='store_true', help='覆盖运行目录里已有的 profile.json')
    ap.add_argument('--model', help='本次使用的模型，覆盖配置')
    ap.add_argument('--base-url')
    ap.add_argument('--api-key')
    args = ap.parse_args()

    if not os.path.isfile(args.resume_file):
        print('❌ 简历文件不存在: %s' % args.resume_file)
        return 2

    cfg_overrides = {k: v for k, v in
                     (('model', args.model), ('base_url', args.base_url), ('api_key', args.api_key))
                     if v}

    # ── 运行目录 ──
    if args.output_dir:
        run_dir = os.path.abspath(args.output_dir)
        os.makedirs(run_dir, exist_ok=True)
    else:
        run_dir = create_run_dir()
    profile_path = _profile_path(run_dir)

    if os.path.exists(profile_path) and not args.force:
        print('⚠ 已存在 %s' % profile_path)
        print('  加 --force 覆盖，或换一个 --output-dir。')
        return 1

    print('📂 运行目录: %s' % run_dir)

    # ── 读简历 ──
    try:
        resume_text = parse_resume_file(args.resume_file)
    except (ImportError, ValueError, OSError) as exc:
        print('❌ 简历读取失败: %s' % exc)
        return 1

    resume_text = (resume_text or '').strip()
    if len(resume_text) < 50:
        print('❌ 只读出 %d 个字符，几乎肯定是解析失败（扫描版 PDF？加密？）' % len(resume_text))
        print('  可以先另存为 .md 或 .txt 再跑。')
        return 1

    text_path = _text_path(run_dir)
    os.makedirs(os.path.dirname(text_path), exist_ok=True)
    with open(text_path, 'w', encoding='utf-8') as f:
        f.write(resume_text)
    print('📄 简历正文: %s（%d 字符）' % (text_path, len(resume_text)))

    # ── 解析 ──
    print('\n🧠 调用模型解析简历 …')
    try:
        with stage_timer.stage(run_dir, 'parse_resume'):
            raw = chat_json(get_resume_parse_prompt(resume_text),
                            stage='parse', run_dir=run_dir, **cfg_overrides)
            profile, dropped = normalize_profile(raw)
    except ConfigError as exc:
        print('❌ 配置错误：\n%s' % exc)
        return 1
    except LLMError as exc:
        print('❌ 解析失败: %s' % exc)
        return 1

    os.makedirs(os.path.dirname(profile_path), exist_ok=True)
    with open(profile_path, 'w', encoding='utf-8') as f:
        json.dump(profile, f, ensure_ascii=False, indent=2)

    stats = _profile_stats(profile)
    print('✅ profile.json 已写入: %s' % profile_path)
    print('   技能 %d 项 / 项目 %d 个 / 公司 %d 家 / 关键词 %d 个'
          % (stats['skills'], stats['projects'], stats['companies'], stats['keywords']))
    if dropped:
        print('   （忽略了 schema 外的字段: %s）' % '、'.join(dropped))

    # ── 交叉校验（词典查表，独立于产出 profile 的模型）──
    print('\n🔍 交叉校验 …')
    report = run_validation(resume_text, profile)
    _print_validation(report)

    if args.cross_check and (report['has_gaps'] or report['has_hints']):
        print('\n🧠 --cross-check：带着疑点让模型复核一遍 …')
        try:
            revised = cross_check(resume_text, profile, report, run_dir, cfg_overrides)
        except LLMError as exc:
            print('⚠ 复核失败，保留原解析结果: %s' % exc)
        else:
            before, after = _profile_stats(profile), _profile_stats(revised)
            with open(profile_path, 'w', encoding='utf-8') as f:
                json.dump(revised, f, ensure_ascii=False, indent=2)
            profile = revised
            print('✅ 已更新 profile.json：技能 %d→%d / 项目 %d→%d / 公司 %d→%d'
                  % (before['skills'], after['skills'], before['projects'],
                     after['projects'], before['companies'], after['companies']))
            report = run_validation(resume_text, profile)
            _print_validation(report)
    elif report['has_gaps']:
        print('\n  提示：加 --cross-check 可让模型带着这些缺口复核一遍。')

    # profile_validation.json 是 where_am_i.py 判定 3.5b 已完成的依据。
    # 这里自己写，而不是调 validate_profile.print_report —— 那个函数用
    # os.path.dirname(sys.argv[2]) 决定写哪里，import 进来会写错位置或直接崩。
    vpath = _validation_path(run_dir)
    os.makedirs(os.path.dirname(vpath), exist_ok=True)
    with open(vpath, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print('\n' + format_usage(run_dir))
    print('\n下一步：')
    print('  python scripts/stages/infer_params.py "%s" --save     # 从简历推断爬取参数' % profile_path)
    print('  python scripts/stages/run_matcher.py --mode deep --profile "%s" --top 15 --output-dir "%s"'
          % (profile_path, run_dir))
    return 0


def _print_validation(report):
    """精简版校验输出。

    退出码语义照 validate_profile.py：**只有缺技能算问题**，hints 来自松散正则和
    集合差，简历与 profile 的用词不同就会触发，读一读就好，不要当成 gate。

    这里故意不打 report['total_profile_skills']：它是 `skills ∪ keywords` 的集合
    大小（去重、含 keywords），而上面 _profile_stats 打的技能数是各分类 list 长度
    累加（不去重、不含 keywords）。两个口径在同一屏里差几个数，只会让人以为解析
    出了问题。技能计数统一由 _profile_stats 一处负责，这行只打它独有的
    total_extracted。字段本身仍写进 profile_validation.json，不受影响。
    """
    print('  简历提取术语 %d 个（词典查表 + 驼峰识别）' % report['total_extracted'])
    if report['missing_skills']:
        print('  ⚠ profile 里缺这些技能词（词典查表，值得看）：')
        print('     %s' % '、'.join(report['missing_skills']))
    else:
        print('  ✅ 没有技能缺口')
    for key, label in (('hint_projects', '项目名未对上'), ('hint_companies', '公司名未对上')):
        if report.get(key):
            print('  · hint（启发式，误报常见）%s: %s' % (label, '、'.join(report[key])))
    for warning in report.get('hint_warnings') or []:
        print('  · hint: %s' % warning)


if __name__ == '__main__':
    sys.exit(main())
