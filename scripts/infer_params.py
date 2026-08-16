#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""profile.json → 爬取参数（流水线的 infer 阶段）。

主代理在这一步停下来问用户（核心参数 / 列表筛选 / 最低岗位数），把确认下来的值当 flag
传进来；没给的字段才交给模型推断。脚本本身不投递、不花钱，最坏结果是爬错一轮，而爬取
命令还需要用户手动执行。

**规模（--scale）不推断**：references/resume-parsing.md 的映射表明确写了「Do NOT
auto-infer; let user decide」—— 简历里没有任何字段能推出「想去多大的公司」。要筛就
用 --scale 显式给。

用法：
    python scripts/infer_params.py <run_dir>                    # 推断并写 crawl_params.json
    python scripts/infer_params.py <run_dir> --save             # 同时存进 assets/preferences.json
    python scripts/infer_params.py --profile path/to/profile.json
    python scripts/infer_params.py <run_dir> --city 杭州 --keywords "Python,后端开发"
    python scripts/infer_params.py <run_dir> --scale 100-499人 --min-count 15
    python scripts/infer_params.py <run_dir> --dry-run          # 只打印提示词
    python scripts/infer_params.py <run_dir> --today 2027-03-01 # 换个「今天」再推一次

参数全部由命令行给出时不调模型（一次都不请求）。

提示词在 scripts/prompts/crawl_params.st。其中「在校 → 实习 / 已毕业 → 全职」这条靠
传进去的今天的日期跟简历里的毕业年份对比得出 —— 不传日期时模型只能看简历措辞猜，
往届和在校的简历措辞是一样的。

退出码：0 = 成功，1 = 输入缺失/调用失败。
"""

import os
import sys
import json
import argparse
import unicodedata
from datetime import date

import preferences
from boss_crawler.config import FILTER_LABELS
from resume_matcher.prompts import get_crawl_params_prompt
from llm import (
    ConfigError, LLMError, chat_json, resolve, format_usage, reconfigure_stdout,
)

# 一线/新一线的兜底名单：assets/weizhi.json 不在仓库里（assets/ 被 .gitignore 挡了），
# 首次运行还没爬过数据时读不到 hotCitySites，但关键词预算不该因此失效。
# 「全国」「不限」也算在内 —— 它们是 BOSS 的全国代码（100010000），岗位池比任何单个
# 城市都大，按小城市的 3 个关键词卡它没道理。weizhi.json 的 hot 列表里本来就有「全国」，
# 所以这两条只是在首次运行（文件还没落盘）时把行为对齐，不是新规则。
_FALLBACK_HOT_CITIES = (
    '全国', '不限',
    '北京', '上海', '广州', '深圳', '杭州', '成都', '重庆', '武汉', '苏州', '西安',
    '天津', '南京', '郑州', '长沙', '东莞', '佛山', '宁波', '青岛', '合肥',
)

HOT_CITY_KEYWORDS = 5          # 一线/新一线：关键词最多 5 个
SMALL_CITY_KEYWORDS = 3        # 其余城市：2-3 个，多了只换来重复岗位

VALID_MODES = ('list', 'custom')
VALID_MATCH_MODES = ('quick', 'deep')

# 只在这里做「模型输出 → 爬虫可接受值」的校验。爬虫拿到一个不认识的筛选值不会报错，
# 它会安静地筛不出东西 —— 那种失败要等一轮爬取结束才看得见。
_ENUM_FIELDS = {
    'salary': 'salary',
    'experience': 'experience',
    'degree': 'degree',
    'job_type': 'jobType',
    'scale': 'scale',
}


def valid_values(field):
    """某个筛选字段的合法中文值（从爬虫的 FILTER_LABELS 取，避免两处枚举漂移）。"""
    return tuple(FILTER_LABELS[_ENUM_FIELDS[field]].values())


# 提示词在 prompts/crawl_params.st。那个目录原本只放 skill 路径的共享模板，这一个是
# 例外：只有本脚本读它（skill 路径这一步是三轮 AskUserQuestion，不需要提示词）。抽出去
# 是为了改判定口径时不用碰 Python —— 尤其是约束 6 那段在校/已毕业的日期规则。
def build_prompt(profile, cities_hint, today):
    return get_crawl_params_prompt(
        resume=profile_digest(profile),
        kw_budget=keyword_budget(cities_hint),
        salary='、'.join(valid_values('salary')),
        experience='、'.join(valid_values('experience')),
        degree='、'.join(valid_values('degree')),
        job_type='、'.join(valid_values('job_type')),
        today=today,
    )


def load_profile(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def profile_digest(profile, limit=2500):
    """给模型看的简历摘要。丢掉 raw_text —— 它能有几万字，且这里要的是结论不是原文。"""
    keep = {}
    for key in ('basic_info', 'education', 'experience', 'skills',
                'salary_expectation', 'keywords'):
        value = profile.get(key)
        if value:
            keep[key] = value
    projects = profile.get('projects') or []
    if projects:
        keep['projects'] = [
            {k: v for k, v in (p or {}).items() if k in ('name', 'role', 'tech_stack')}
            for p in projects[:8] if isinstance(p, dict)
        ]
    text = json.dumps(keep, ensure_ascii=False, indent=2)
    return text[:limit]


def is_hot_city(cities):
    """目标城市里有没有一线/新一线。"""
    names = [str(c).strip() for c in (cities or []) if str(c).strip()]
    if not names:
        return False
    hot = set(_FALLBACK_HOT_CITIES)
    # 只在 weizhi.json 已经落盘时才读它：load_city_data() 发现文件缺失会**联网重新抓一次**
    # 并写进 assets/。为了决定「关键词给 3 个还是 5 个」去发网络请求不值得 —— 离线时那是
    # 一次挂起（try/except 拦不住超时），而兜底名单本来就够用。
    try:
        from boss_crawler.config import ASSETS_DIR
        if os.path.exists(os.path.join(ASSETS_DIR, 'weizhi.json')):
            from boss_crawler.data_loader import load_city_data
            data = load_city_data() or {}
            hot |= {str(c.get('name', '')).strip() for c in (data.get('hot') or [])}
    except Exception:                       # noqa: BLE001 — 数据缺失时用兜底名单即可
        pass
    return any(name in hot for name in names)


def keyword_budget(cities):
    return HOT_CITY_KEYWORDS if is_hot_city(cities) else SMALL_CITY_KEYWORDS


def _as_list(value):
    if isinstance(value, list):
        return [str(v).strip() for v in value if v is not None and str(v).strip()]
    if value and isinstance(value, str):
        return [p.strip() for p in value.split(',') if p.strip()]
    return []


def _as_int(value):
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def normalize(raw, warnings):
    """模型输出 → 白名单内、枚举合法的参数 dict。非法值丢掉并记一条告警。"""
    if not isinstance(raw, dict):
        raise LLMError('返回的不是 JSON 对象')

    params = {}

    for field in ('keywords', 'cities'):
        values = _as_list(raw.get(field))
        # 去重但保序：模型偶尔会把同一个词写两遍
        seen, unique = set(), []
        for value in values:
            if value not in seen:
                seen.add(value)
                unique.append(value)
        if unique:
            params[field] = unique

    for field in _ENUM_FIELDS:
        if field == 'scale':
            continue                        # 规模不推断，见模块 docstring
        allowed = valid_values(field)
        good, bad = [], []
        for value in _as_list(raw.get(field)):
            (good if value in allowed else bad).append(value)
        if bad:
            warnings.append('%s 里有 %d 个不在枚举内的值已丢弃：%s（合法值：%s）'
                            % (field, len(bad), '、'.join(bad), '、'.join(allowed)))
        if good:
            params[field] = good

    mode = str(raw.get('mode') or '').strip().lower()
    params['mode'] = mode if mode in VALID_MODES else 'custom'
    if mode and mode not in VALID_MODES:
        warnings.append('mode=%s 不合法，改用 custom' % mode)

    match_mode = str(raw.get('match_mode') or '').strip().lower()
    if match_mode in VALID_MATCH_MODES:
        params['match_mode'] = match_mode
    elif match_mode:
        warnings.append('match_mode=%s 不合法，留空' % match_mode)

    for field in ('count', 'top_n', 'min_count'):
        number = _as_int(raw.get(field))
        if number:
            params[field] = number

    return params


def apply_overrides(params, args, warnings):
    """命令行覆盖模型推断。命令行给的值不做枚举校验之外的加工。"""
    pairs = (
        ('keywords', args.keywords), ('cities', args.city),
        ('degree', args.degree), ('experience', args.experience),
        ('salary', args.salary), ('job_type', args.job_type), ('scale', args.scale),
    )
    for field, value in pairs:
        if value is None:
            continue
        values = _as_list(value)
        if field in _ENUM_FIELDS:
            allowed = valid_values(field)
            bad = [v for v in values if v not in allowed]
            if bad:
                # 命令行的非法值直接报错而不是静默丢弃：用户是有意打的，
                # 悄悄丢掉会让他以为筛选生效了
                raise ValueError('--%s 的值不在枚举内：%s\n  合法值：%s'
                                 % (field.replace('_', '-'), '、'.join(bad), '、'.join(allowed)))
        if values:
            params[field] = values
        else:
            params.pop(field, None)         # 显式传空串 = 不筛这一项

    if args.mode:
        params['mode'] = args.mode
    if args.match_mode:
        params['match_mode'] = args.match_mode
    for field, value in (('count', args.count), ('top_n', args.top_n),
                         ('min_count', args.min_count)):
        if value is not None:
            if value > 0:
                params[field] = value
            else:
                params.pop(field, None)     # 0 = 关掉这一项（min_count=0 即不做数量门槛）
    return params


def enforce_budget(params, warnings, explicit=False):
    """按城市市场规模砍关键词。

    爬取时间与关键词数量成正比，小市场里多给的关键词只会换来重复岗位（太原 5 个关键词
    117 行只有 53 个唯一岗位）。模型经常忽略这条，所以在代码里硬砍一刀。

    explicit=True（关键词是命令行给的）时只告警不截断：用户是有意打这些词的，可能就是
    在一个窄赛道里搜，替他删掉和悄悄丢掉非法枚举值是同一类错误。
    """
    keywords = params.get('keywords') or []
    budget = keyword_budget(params.get('cities'))
    if len(keywords) > budget:
        where = '、'.join(params.get('cities') or ['未指定城市'])
        if explicit:
            warnings.append('关键词 %d 个超出建议预算（%s → 建议 %d 个以内），'
                            '因为是命令行显式给的所以保留；小城市里多给关键词大多只换来重复岗位'
                            % (len(keywords), where, budget))
        else:
            warnings.append('关键词 %d 个超出预算（%s → 上限 %d），已截断：丢弃 %s'
                            % (len(keywords), where, budget, '、'.join(keywords[budget:])))
            params['keywords'] = keywords[:budget]
    return params


# 缺城市/关键词时的示例值。示例给两个是为了顺手示范逗号分隔的写法 ——
# 只写一个的话，多城市怎么传还得去翻 --help。
_MISSING_EXAMPLE = {
    'cities': '--city "西安,北京"',
    'keywords': '--keywords "AI应用开发,全栈开发"',
}


def print_missing_hint(field, run_dir, profile_path, args):
    """缺核心参数时印出能直接粘回终端的续跑命令。

    只说「无法爬取」的话，人得自己想明白三件事：补哪个参数、补到哪条命令上、
    以及补完为什么还要再等一次模型。这里一次说完 —— 上一次真实使用里，这三件
    事各卡了一下。
    """
    example = _MISSING_EXAMPLE[field]

    # 由 pipeline.py 启动时要给 pipeline 的形式：补完参数只重跑这一步的话，
    # 后面的爬取/匹配/生成还得自己一个个敲。--all 是必须的 —— 不给终点
    # pipeline 只跑 infer 这一个阶段，人以为续上了整轮，实际又停在原地。
    if os.environ.get('BOSS_PIPELINE_STAGE') and run_dir:
        cmd = ('python scripts/pipeline.py --run-dir "%s" --from infer --all %s'
               % (run_dir, example))
    elif run_dir:
        cmd = 'python scripts/infer_params.py "%s" %s' % (run_dir, example)
    else:
        cmd = ('python scripts/infer_params.py --profile "%s" %s'
               % (profile_path, example))

    print('  补上就能接着跑：\n    %s' % cmd)

    print_field_help(field)
    print_common_params()

    # 关键词和城市必须**同时**由命令行给出才会跳过推断（manual_core）。只补一个
    # 就得再等一轮模型，而模型这轮的学历/经验/工作类型判断也会跟着重来一遍。
    other = '--keywords' if field == 'cities' else '--city'
    print('  （这样重跑会再调一次模型；连 %s 一起给就完全跳过推断，'
          '但学历/经验/工作类型也得自己填）' % other)


def print_field_help(field):
    """缺的那个字段本身怎么填。"""
    if field == 'cities':
        # 「不给城市」和「全国搜」是两件事，而前者的报错很容易被读成后者。
        # 全国是一个**城市值**（BOSS 的 100010000），不是「省略即全国」。
        print('  城市怎么填（不给城市 ≠ 全国搜，全国得显式写出来）：')
        rows = [('--city 全国', '全国一次搜完（也可写「不限」）'),
                ('--city "西安,北京,杭州"', '多个城市，逐个爬')]
        width = max(_cjk_width(flag) for flag, _ in rows)
        for flag, note in rows:
            print('    %s  %s' % (_cjk_pad(flag, width), note))
        print('    全部城市名：python scripts/boss_post_interactive.py --list-cities')
    else:
        print('  关键词怎么填：')
        print('    --keywords "AI应用开发,全栈开发"   逗号分隔，一个词一个方向')
        print('    热门城市最多 %d 个、其余城市 %d 个，超了会截断 —— '
              '小城市里多给关键词大多只换来重复岗位'
              % (HOT_CITY_KEYWORDS, SMALL_CITY_KEYWORDS))
        print('    岗位名参考：python scripts/boss_post_interactive.py --list-positions')


def _cjk_width(text):
    """字符串在终端里占几列。"""
    return sum(2 if unicodedata.east_asian_width(c) in 'WF' else 1 for c in text)


def _cjk_pad(text, width):
    """按**显示宽度**右侧补空格。

    中文在终端里占两列而 len() 只算一个字符，所以 '%-*s' 会把带中文的那几行
    推歪 —— 而这张表的意义就是让人扫一眼看清哪个 flag 配哪些值。
    """
    return text + ' ' * max(0, width - _cjk_width(text))


def print_common_params():
    """顺手把常见参数摆出来。

    缺一个参数时人往往想把其他几个也一次定下来，而这些字段全是枚举：值写错了
    爬虫只印一行「未匹配」警告然后少爬一批，不会失败 —— 于是错的那次看起来
    跟成功一样。枚举一律从 valid_values() 取，跟爬虫的 FILTER_LABELS 同源。
    """
    print('  其余参数不给就由模型推断，也可以直接指定（传空串 "" 表示不筛该项）：')
    rows = [
        ('--count 30', '每个关键词每个城市爬多少条（0=不限）'),
        ('--degree 本科', ' / '.join(valid_values('degree'))),
        ('--experience 在校生', ' / '.join(valid_values('experience'))),
        ('--job-type 实习', ' / '.join(valid_values('job_type'))),
        ('--salary 10-20K', ' / '.join(valid_values('salary'))),
        ('--scale 100-499人', ' / '.join(valid_values('scale')) + '（不做推断）'),
        ('--match-mode deep', 'deep=逐岗位调模型（配 --top-n 15）；quick=纯规则零 token'),
    ]
    width = max(_cjk_width(flag) for flag, _ in rows)
    for flag, values in rows:
        print('    %s  %s' % (_cjk_pad(flag, width), values))


def main():
    reconfigure_stdout()

    ap = argparse.ArgumentParser(
        description='从 profile.json 推断爬取参数，写 crawl_params.json',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='注意：公司规模（--scale）不做推断，简历里没有能推出它的信息。\n'
               '参数全部由命令行给出时不会调用模型。\n')
    ap.add_argument('run_dir', nargs='?', help='运行目录（含 profile.json）')
    ap.add_argument('--profile', help='直接指定 profile.json 路径')
    ap.add_argument('--save', action='store_true',
                    help='同时整份写入 assets/preferences.json（覆盖已有预设，'
                         '供预设重放路径复用）')
    ap.add_argument('--dry-run', action='store_true', help='只打印提示词，不请求')
    ap.add_argument('--today', metavar='YYYY-MM-DD',
                    help='当作今天的日期传给模型（默认取系统日期）。模型靠它跟简历里的'
                         '毕业年份对比，判断该筛实习还是全职')

    group = ap.add_argument_group('覆盖推断结果（可逗号分隔；传空串表示不筛该项）')
    group.add_argument('--city', help='城市，如 "杭州,上海"')
    group.add_argument('--keywords', help='搜索关键词')
    group.add_argument('--mode', choices=VALID_MODES)
    group.add_argument('--match-mode', choices=VALID_MATCH_MODES, dest='match_mode')
    group.add_argument('--count', type=int, help='每关键词每城市条数，0=不限')
    group.add_argument('--top-n', type=int, dest='top_n')
    group.add_argument('--min-count', type=int, dest='min_count', help='0=关掉数量门槛')
    group.add_argument('--degree')
    group.add_argument('--experience')
    group.add_argument('--salary')
    group.add_argument('--job-type', dest='job_type')
    group.add_argument('--scale', help='公司规模（只能手动给，不推断）')

    ap.add_argument('--model')
    ap.add_argument('--base-url')
    ap.add_argument('--api-key')
    args = ap.parse_args()

    run_dir = os.path.abspath(args.run_dir) if args.run_dir else None
    if args.today:
        # 格式写错了不能放过去：这个值是直接拼进提示词的，"2026/8/16" 或 "明天" 都不会
        # 报错，只会让模型算错在校/已毕业，最后表现成「筛错了工作类型」——离这里很远。
        try:
            date.fromisoformat(args.today)
        except ValueError:
            print('❌ --today 要写成 YYYY-MM-DD（如 %s），收到的是 %r'
                  % (date.today().isoformat(), args.today))
            return 1
    profile_path = args.profile or (os.path.join(run_dir, 'profile.json') if run_dir else None)
    if not profile_path:
        print('❌ 需要 <run_dir> 或 --profile 其中之一')
        return 1
    if not os.path.exists(profile_path):
        print('❌ 找不到 %s' % profile_path)
        print('  先跑：python scripts/parse_resume.py <简历文件>%s'
              % (' --output-dir "%s"' % run_dir if run_dir else ''))
        return 1

    try:
        profile = load_profile(profile_path)
    except ValueError as exc:
        print('❌ profile.json 解析失败: %s' % exc)
        return 1

    # 全部核心参数都由命令行给出时没必要调模型
    manual_core = bool(args.keywords and args.city)
    warnings = []

    if manual_core:
        print('📋 关键词与城市都已由命令行给出，跳过模型推断')
        params = {'mode': args.mode or 'custom'}
    else:
        cities_hint = _as_list(args.city) or _as_list(
            (profile.get('basic_info') or {}).get('expected_city')
            or (profile.get('basic_info') or {}).get('city'))
        today = args.today or date.today().isoformat()
        prompt = build_prompt(profile, cities_hint, today)

        if args.dry_run:
            print('--dry-run：提示词 %d 字符\n%s\n%s\n%s'
                  % (len(prompt), '-' * 60, prompt, '-' * 60))
            print('（未发送请求）')
            return 0

        overrides = {k: v for k, v in
                     (('model', args.model), ('base_url', args.base_url),
                      ('api_key', args.api_key)) if v}
        try:
            # 阶段名用 'infer' —— llm_config.example.json 里写死的五个阶段名之一，
            # 换个名字 stages.infer 就盖不上来了
            cfg = resolve(stage='infer', **overrides)
        except ConfigError as exc:
            print('❌ 配置错误：\n%s' % exc)
            return 1

        print('🤔 推断爬取参数（模型 %s，按 %s 判断在校/已毕业）…' % (cfg.model, today))
        try:
            raw = chat_json(prompt, stage='infer', run_dir=run_dir, cfg=cfg)
            params = normalize(raw, warnings)
        except LLMError as exc:
            print('❌ 推断失败：%s' % exc)
            print('  可以直接手动给参数：--city 杭州 --keywords "Python,后端开发"')
            return 1
        reasoning = str((raw or {}).get('reasoning') or '').strip()
        if reasoning:
            print('  理由：%s' % reasoning)

    try:
        params = apply_overrides(params, args, warnings)
    except ValueError as exc:
        print('❌ %s' % exc)
        return 1
    params = enforce_budget(params, warnings, explicit=bool(args.keywords))

    if not params.get('keywords'):
        print('❌ 没有推断出任何关键词，也没有 --keywords。无法爬取。')
        print_missing_hint('keywords', run_dir, profile_path, args)
        return 1
    if not params.get('cities'):
        print('❌ 没有推断出城市，也没有 --city。无法爬取。')
        print('  简历里没写期望城市，模型不会替你编一个。')
        print_missing_hint('cities', run_dir, profile_path, args)
        return 1

    # ── 输出 ──
    print('\n%s' % ('=' * 60))
    labels = preferences.LABELS
    for key in ('cities', 'keywords', 'mode', 'count', 'match_mode', 'top_n',
                'min_count', 'degree', 'experience', 'salary', 'job_type', 'scale'):
        if key not in params:
            continue
        value = params[key]
        print('  %-12s %s' % (labels.get(key, key),
                              '、'.join(value) if isinstance(value, list) else value))
    if 'scale' not in params:
        print('  %-12s （未筛选，需要就加 --scale）' % labels['scale'])

    for warning in warnings:
        print('  ⚠ %s' % warning)

    if run_dir:
        os.makedirs(run_dir, exist_ok=True)
        out_path = os.path.join(run_dir, 'crawl_params.json')
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(params, f, ensure_ascii=False, indent=2)
        print('\n  已写出: %s' % out_path)

    if args.save:
        # _replace=True：这里刚把整份参数打给用户确认过，落盘必须和打印出来的一致。
        # 合并会把上一轮的旧筛选偷偷带进来 —— 文件比屏幕多几个字段，最难查。
        # 单字段补写走 `preferences.py save`（那边默认合并）。
        saved = preferences.save(_replace=True, **params)
        print('  已存入预设: %s（%d 个字段）' % (preferences.prefs_path(), len(saved) - 2))

    command = preferences.crawl_command(params)
    print('\n下一步（确认参数后手动执行）：')
    print('  %s%s' % (command, ' --run-dir "%s"' % run_dir if run_dir else ''))
    if params.get('match_mode') == 'deep':
        print('\n爬完之后（deep 模式）：')
        print('  python scripts/run_matcher.py --mode deep --profile "%s" --top %d --output-dir "%s"'
              % (profile_path, params.get('top_n', 15), run_dir or '<run_dir>'))
        print('  python scripts/deep_analyze.py "%s"' % (run_dir or '<run_dir>'))
        print('  python scripts/run_matcher.py --mode deep --merge --output-dir "%s"'
              % (run_dir or '<run_dir>'))
    else:
        print('\n爬完之后：')
        print('  python scripts/run_matcher.py --mode quick --profile "%s" --output-dir "%s"'
              % (profile_path, run_dir or '<run_dir>'))

    if run_dir and not manual_core:
        print('\n' + format_usage(run_dir))
    return 0


if __name__ == '__main__':
    sys.exit(main())
