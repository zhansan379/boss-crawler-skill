#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成每个岗位的 `投递.md` 与 `优化建议.md`：把该岗位采到的**全部**字段和招呼语
写进 投递.md，另在同一目录写一份只含优化建议的 优化建议.md。

为什么要有这个脚本，而不是让主 agent 按模板手写：

1. **完整性靠不住。** 20 个爬取字段手写一遍，漏一个不会报错，只是文件里少一行。
   2026-08-13 那次真实运行的产物写了 11 个字段，缺了 商圈/领域/性质/规模/
   技能标签/福利标签/位置/公司信息/JD/HR 三项 —— 每次生成的字段集都不一样。
2. **数据源必须绕开 `build_job_view()`。** 那是唯一的字段映射处
   （scoring.py:725），但它整字段丢弃 `区域` `商圈` `领域` `性质` `位置`，
   并把 `公司信息` 截到 500、`岗位要求和职责` 截到 1000（HTML 路径 300/500）。
   `领域`（行业）和 `性质`（融资阶段）恰恰是「公司信息」的核心。
   所以这里按 link 回读原始 CSV —— 详情是回填进同一个 CSV 的
   （crawler.py:368-399），那里的值全量且未截断。

用法：
    python scripts/deliver/write_application_md.py <run_dir> --all
    python scripts/deliver/write_application_md.py <run_dir> --index 1
    python scripts/deliver/write_application_md.py <run_dir> --index 1 --greeting-file P
    python scripts/deliver/write_application_md.py <run_dir> --index 1 --greeting "文本"

退出码：0 = 全部写出且字段完整，1 = 有岗位的原始 CSV 行没找到（字段可能不全，
详情见 stdout 的警告）。
"""

import argparse
import csv
import glob
import json
import os
import re
import sys
import time

_SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _SCRIPTS)

from resume_matcher import (qualified_jobs_path, match_analysis_path,
                            greeting_pattern, resume_pattern, deliver_dir)

# CSV 是 utf-8-sig（boss_crawler/config.py:25）。'utf-8-sig' 同时能读无 BOM 的文件。
ENCODING = 'utf-8-sig'

_SKILL_ROOT = os.path.dirname(_SCRIPTS)
POST_DATA_DIR = os.path.join(_SKILL_ROOT, 'assets', 'post_data')

# 爬取字段的分组与展示顺序。必须覆盖 boss_crawler/config.py 的 CSV_FIELDS 全部字段，
# 否则 render 时会走到「其他采集字段」兜底（那一节的存在就是为了让漏项可见而非消失）。
JOB_ROWS = ['职位', '薪资', '经验', '学历', '技能标签', '福利标签',
            '城市', '区域', '商圈', '地址', '位置', '已失效', 'link']
COMPANY_ROWS = ['公司', '领域', '性质', '规模']
HR_ROWS = ['HR姓名', 'HR职位', 'HR公司', '代招', 'HR活跃度', 'HR在线']
LONG_FIELDS = ['公司信息', '岗位要求和职责']

KNOWN_FIELDS = set(JOB_ROWS) | set(COMPANY_ROWS) | set(HR_ROWS) | set(LONG_FIELDS)

LABELS = {'link': '链接'}

# 空格分隔的多值字段，展示时改成顿号分隔（crawler 用 ' '.join 拼的）
TAG_FIELDS = {'技能标签', '福利标签'}

NOT_COLLECTED = '未采集'
NOT_ADJUDICATED = '未裁定'


# ==================== 基础工具 ====================

def sanitize(name):
    """清洗成合法目录名/文件名。与 auto-apply.md 的规则一致。"""
    cleaned = re.sub(r'[/\\:*?"<>|]', '_', (name or '').strip())
    return cleaned.strip(' .') or '未命名'


def cell(value, placeholder=NOT_COLLECTED):
    """表格单元格：转义 | 与换行，空值显式写成占位符而不是留白。"""
    text = ('' if value is None else str(value)).strip()
    if not text:
        return placeholder
    return text.replace('|', r'\|').replace('\r\n', ' ').replace('\n', '<br>')


def tags(value):
    """多值字段 → 顿号分隔。crawler 用空格拼，但数据里也见过逗号分隔。"""
    parts = [p for p in re.split(r'[\s,，、]+', (value or '').strip()) if p]
    return '、'.join(parts) if parts else ''


def gps(value):
    """`位置` 存的是 str(dict)（crawler.py:51），拆成人读的经纬度。

    解析不了就原样返回 —— 宁可难看，也不要把采到的值弄丢。
    """
    text = (value or '').strip()
    lon = re.search(r"'longitude':\s*([-\d.]+)", text)
    lat = re.search(r"'latitude':\s*([-\d.]+)", text)
    if lon and lat:
        return '经度 %s，纬度 %s' % (lon.group(1), lat.group(1))
    return text


def table(row, keys, placeholder=NOT_COLLECTED):
    """渲染「字段 | 值」两列表。"""
    lines = ['| 字段 | 值 |', '|---|---|']
    for key in keys:
        raw = row.get(key, '')
        if key in TAG_FIELDS:
            raw = tags(raw)
        elif key == '位置':
            raw = gps(raw)
        lines.append('| %s | %s |' % (LABELS.get(key, key), cell(raw, placeholder)))
    return '\n'.join(lines)


def bullets(items):
    """列表 → markdown 无序列表；空则返回 None（调用方决定是否整节省略）。"""
    if not items:
        return None
    if isinstance(items, str):
        items = [items]
    out = []
    for item in items:
        text = str(item).strip()
        if text:
            out.append('- %s' % text.replace('\n', ' '))
    return '\n'.join(out) if out else None


# ==================== 数据加载 ====================

def load_jobs(run_dir):
    """读 qualified_jobs.json。宽容处理被包一层的情况（同 check_artifacts.py）。"""
    path = qualified_jobs_path(run_dir)
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = data.get('jobs') or data.get('data') or []
    return data


def load_match_analysis(run_dir):
    """读 state/match_analysis.json（merge 时按 link 落盘的逐岗裁定结论）。

    qualified_jobs.json 按设计只含**原始爬取字段**，匹配结论不在这；write_application_md
    早期只读它，导致 匹配分析 四列永远「未裁定」、下方各小节整块缺失。真正的匹配数据由
    merge_deep_results 写进这个文件（keyed by link），这里按 link 连回来。文件缺失
    说明还没走到 merge 或用的不是深度模式 —— 返回空 dict 让 render 走原来的占位符，
    不抛错。
    """
    path = match_analysis_path(run_dir)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _csv_candidates(job, explicit_csv):
    """按「最可能命中」排序的候选 CSV 路径。"""
    paths = []
    if explicit_csv:
        paths.append(explicit_csv)

    source = (job.get('source_file') or '').strip()
    if source:
        # source_file 可能是绝对路径、相对 cwd 的路径，或只是个裸文件名
        paths.append(source)
        paths.append(os.path.join(_SKILL_ROOT, source))
        base = os.path.basename(source)
        paths.extend(glob.glob(os.path.join(POST_DATA_DIR, '**', base), recursive=True))

    # 兜底：扫全部爬取结果。_details 后缀的不是主表（见 data_loader.py:49）
    everything = sorted(glob.glob(os.path.join(POST_DATA_DIR, '**', '*.csv'), recursive=True))
    paths.extend(p for p in everything if '_details' not in os.path.basename(p))

    seen, unique = set(), []
    for path in paths:
        key = os.path.normcase(os.path.abspath(path))
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def resolve_csv_row(job, explicit_csv=None):
    """按 link 在爬取 CSV 里找回这一行的全量字段。

    Returns: (row_or_None, csv_path_or_None)
    """
    link = (job.get('link') or '').strip()
    if not link:
        return None, None

    for path in _csv_candidates(job, explicit_csv):
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding=ENCODING, newline='') as f:
                for row in csv.DictReader(f):
                    if (row.get('link') or '').strip() == link:
                        return row, path
        except (OSError, csv.Error, UnicodeDecodeError):
            continue
    return None, None


def merge(job, csv_row):
    """CSV 行（全量未截断）为准，job dict 补上分析字段。

    job dict 里的爬取字段可能来自 build_job_view —— 已被截断或缺失，所以
    CSV 拿到的值一律覆盖它。CSV 没找到时才退回 job dict 自身。
    """
    merged = dict(job)
    if csv_row:
        for key, value in csv_row.items():
            if key and (value or '').strip():
                merged[key] = value
    # ASCII 键兜底：job 若走过 build_job_view，中文键可能整个不在
    ascii_fallback = {
        '职位': 'position', '公司': 'company', '城市': 'city', '薪资': 'salary',
        '经验': 'experience', '学历': 'degree', '规模': 'scale',
        '技能标签': 'skill_tags', '福利标签': 'welfare_tags',
        '岗位要求和职责': 'jd', '公司信息': 'company_info',
        'HR活跃度': 'hr_active_desc', 'HR在线': 'hr_online', 'HR职位': 'hr_title',
    }
    for zh, ascii_key in ascii_fallback.items():
        if not (merged.get(zh) or '').strip():
            alt = job.get(ascii_key)
            if alt and str(alt).strip():
                merged[zh] = alt
    return merged


# ==================== 交付目录与撞名 ====================

def job_dir_stem(index, company, position):
    """`deliver/` 下单份岗位的目录名：`#N-<公司>-<岗位>`。

    序号前缀让同一批里「公司+岗位」撞名的两个岗位也各有独立目录。材料层
    （greeting_N、resume_N）从 gen_materials 起就以序号定位，交付层一度拿自然键
    `<公司>-<岗位>` 当身份 —— 一撞名，两份材料的 PNG/投递.md 就写进同一目录
    互相覆盖（#5/#6 的病灶）。这里把自然键延续成「序号 + 自然键」，附件文件名
    仍是干净的 `<姓名>-<岗位>.png`，序号只进目录名。
    """
    return '#%d-%s-%s' % (index, sanitize(company or '未知公司'),
                          sanitize(position or '未知岗位'))


def find_duplicate_links(run_dir, jobs, indexes):
    """返回 {link: [序号...]}，同一 link 在选中的一批里出现多次 = 撞名。

    同一 link = 同一条 BOSS 岗位 = 同一个 HR。一批里重复出现，要么是上游去重漏了、
    要么是用户手选重复：重复投同一个 link 既浪费一次珍贵的沟通机会，又会像 #5/#6
    那样在版本上打架。这在渲染/写出前挡下，不静默放行 —— 目录已按序号唯一，
    但「同一个岗位投两次」不该靠那层兜着。
    """
    groups = {}
    for index in indexes:
        job = jobs[index - 1]
        link = str(job.get('link') or '').strip()
        if link:
            groups.setdefault(link, []).append(index)
    return {link: ixs for link, ixs in groups.items() if len(ixs) > 1}


def resolve_greeting(run_dir, index, args):
    """招呼语来源优先级：命令行 > materials/greeting_{i}_*.txt > 空。"""
    if args.greeting:
        return args.greeting.strip(), '命令行 --greeting'
    if args.greeting_file:
        with open(args.greeting_file, encoding='utf-8') as f:
            return f.read().strip(), args.greeting_file
    pattern = greeting_pattern(run_dir, index)
    hits = sorted(glob.glob(pattern))
    if hits:
        with open(hits[0], encoding='utf-8') as f:
            return f.read().strip(), hits[0]
    return '', None


# ==================== 优化建议 ====================

def load_resume_optimization(run_dir, index):
    """读 materials/resume_{index}_*.json，只取优化建议部分。

    resume json 是 resume_optimize.st 的整个返回对象：里面有整份优化后简历
    （optimized_resume，很大，由 images / render 另派生），还有精简的
    optimization_suggestions（must_add / should_adjust / keywords_to_emphasize /
    format_suggestions）和 key_changes。优化建议.md 只落后者，不重复塞整份简历——
    那份内容同批材料已有一份，写两遍只是多地漂移。

    文件缺失（--resume-mode skip、没走到 materials、或该岗位没生成成功）时返回
    None，由 render_optimization 回退到规则侧的 optimization_points。
    """
    hits = sorted(glob.glob(resume_pattern(run_dir, index)))
    if not hits:
        return None
    try:
        with open(hits[0], encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return {
        'suggestions': data.get('optimization_suggestions') or {},
        'key_changes': data.get('key_changes') or [],
        'source': os.path.basename(hits[0]),
    }


def _suggestion_bullets(items):
    """must_add / should_adjust 里的 {section, content|suggestion} → 子弹列表。"""
    out = []
    for entry in items or []:
        if not isinstance(entry, dict):
            continue
        sec = (entry.get('section') or '').strip()
        text = (entry.get('content') or entry.get('suggestion') or '').strip()
        if not text:
            continue
        out.append('- 【%s】%s' % (sec, text) if sec and sec != text else '- %s' % text)
    return out


def _string_bullets(items):
    """str 或 str 列表 → 子弹列表；空值过滤。"""
    if items is None:
        return []
    if isinstance(items, str):
        items = [items]
    return ['- %s' % str(x).strip() for x in items if str(x).strip()]


def render_optimization(company, position, opt, job, index):
    """优化建议.md 正文。

    opt 非空且有数据时用 LLM 出的建议（materials/resume_*.json）；否则回退
    规则匹配侧的关键字级 optimization_points（快速模式 / 没走 materials 时）。
    """
    parts = ['# %s - %s 优化建议' % (company, position), '']
    if opt:
        parts.append('> 生成时间 %s ｜ 岗位序号 #%d ｜ 数据来源 %s'
                     % (time.strftime('%Y-%m-%d %H:%M:%S'), index, opt['source']))
    else:
        parts.append('> 生成时间 %s ｜ 岗位序号 #%d ｜ 数据来源 规则匹配'
                     '（未走到优化简历，以下为规则侧宽松给出的优化点）'
                     % (time.strftime('%Y-%m-%d %H:%M:%S'), index))
    parts.append('')

    if opt and (opt['suggestions'] or opt['key_changes']):
        s = opt['suggestions']
        groups = [
            ('## 需要补充的内容', _suggestion_bullets(s.get('must_add'))),
            ('## 建议调整的部分', _suggestion_bullets(s.get('should_adjust'))),
            ('## 需强调的关键词', _string_bullets(s.get('keywords_to_emphasize'))),
            ('## 格式优化建议', _string_bullets(s.get('format_suggestions'))),
        ]
        for title, lines in groups:
            if lines:
                parts.append(title)
                parts.append('')
                parts.extend(lines)
                parts.append('')
        changes = _string_bullets(opt['key_changes'])
        if changes:
            parts.append('## 主要修改点')
            parts.append('')
            parts.extend(changes)
            parts.append('')
    else:
        lines = _string_bullets(job.get('optimization_points'))
        if not lines:
            lines = _string_bullets(job.get('optimization_suggestions'))
        parts.append('## 优化建议')
        parts.append('')
        parts.extend(lines if lines else ['- （该岗位暂无优化建议）'])
        parts.append('')

    parts.append('> 以上只指向「简历里还缺什么、怎么写更好」，不是要你无中生有 —— ')
    parts.append('> 每一处补充请都用真实经历支撑，HR 一眼能看穿的虚假经历只会反噬投递。')
    parts.append('')
    return '\n'.join(parts)


# ==================== 渲染 ====================

def render(row, job, greeting, csv_path, index, csv_row=None):
    company = (row.get('公司') or '').strip() or '未知公司'
    position = (row.get('职位') or '').strip() or '未知岗位'

    parts = ['# %s - %s' % (company, position), '']

    origin = csv_path if csv_path else '未找到原始 CSV，字段取自 qualified_jobs.json（可能不全）'
    parts.append('> 生成时间 %s ｜ 岗位序号 #%d ｜ 数据来源 %s'
                 % (time.strftime('%Y-%m-%d %H:%M:%S'), index, origin))
    parts.append('')

    # 失效岗位置顶警示 —— 投了也是白投，不能只藏在表格某一行里
    if (row.get('已失效') or '').strip() == '是':
        parts.append('> 🚫 **该岗位已失效**（爬取时 BOSS 返回 invalidStatus=true），投递不会被看到。')
        parts.append('')

    parts.append('## 岗位信息')
    parts.append('')
    parts.append(table(row, JOB_ROWS))
    parts.append('')

    parts.append('## 公司信息')
    parts.append('')
    parts.append(table(row, COMPANY_ROWS))
    parts.append('')
    parts.append('### 公司介绍')
    parts.append('')
    intro = (row.get('公司信息') or '').strip()
    parts.append(intro if intro else NOT_COLLECTED)
    parts.append('')

    parts.append('## 招聘者')
    parts.append('')
    parts.append(table(row, HR_ROWS))
    parts.append('')

    # 代招提示：跟你聊的可能不是用人公司自己的 HR
    hr_company = (row.get('HR公司') or '').strip()
    company_name = (row.get('公司') or '').strip()
    if (row.get('代招') or '').strip() == '是' or (hr_company and company_name
                                                  and hr_company != company_name):
        parts.append('> ⚠️ **代招岗位**：HR 所属「%s」与岗位公司「%s」不一致，'
                     '对接的是猎头/外包而非用人公司自己的 HR。'
                     % (hr_company or '未采集', company_name or '未采集'))
        parts.append('')

    parts.append('> 活跃度是**爬取瞬间**的快照，投递时可能已过期。'
                 '空值是「未采集」（爬取时没带 -d），不等于「不活跃」。')
    parts.append('')

    parts.append('## 岗位要求和职责')
    parts.append('')
    jd = (row.get('岗位要求和职责') or '').strip()
    parts.append(jd if jd else NOT_COLLECTED)
    parts.append('')

    # 匹配分析：分数与结论来自 job dict。这些字段不在 CSV 里，也不在 qualified_jobs
    # （它只含原始爬取字段），由 write_one 在写前置入了 state/match_analysis.json 的
    # 按-link 记录（merge 阶段落盘），所以这里直接 job.get(...) 就能读到。
    parts.append('## 匹配分析')
    parts.append('')
    score = job.get('match_score', '')
    analysis = {
        '匹配度': ('%s%%' % score) if score != '' else '',
        '投递难度': job.get('difficulty', ''),
        '投递结论': job.get('application_category', ''),
        '结论理由': job.get('application_category_reason', ''),
    }
    parts.append(table(analysis, list(analysis), placeholder=NOT_ADJUDICATED))
    parts.append('')

    sections = [
        ('匹配理由', job.get('match_reasons')),
        ('命中技能', job.get('matched_skills')),
        ('缺口（未在简历中虚构）', job.get('missing_skills') or job.get('missing_items')),
        ('亮点', job.get('highlight')),
        ('风险', job.get('risk')),
        ('优化建议', job.get('optimization_points')),
    ]
    for title, value in sections:
        body = bullets(value)
        if body:
            parts.append('### %s' % title)
            parts.append('')
            parts.append(body)
            parts.append('')

    # 兜底：CSV 里出现了本脚本不认识的新列，也要落进文件而不是静默丢掉。
    # 只看 csv_row —— merged 里混着 build_job_view 的 ASCII 别名（position/jd/…），
    # 那些是上面已展示字段的重复，列进来只会让文件里同一份 JD 出现两遍。
    extras = {}
    if csv_row:
        extras = {k: v for k, v in csv_row.items()
                  if k and k not in KNOWN_FIELDS and k != 'source_file'
                  and isinstance(v, str) and v.strip()}
    if extras:
        parts.append('## 其他采集字段')
        parts.append('')
        parts.append(table(extras, sorted(extras)))
        parts.append('')

    parts.append('## 招呼语（%d 字）' % len(greeting))
    parts.append('')
    parts.append(greeting if greeting else '⚠️ 未提供招呼语')
    parts.append('')

    return '\n'.join(parts)


# ==================== 主流程 ====================

def write_one(run_dir, job, index, args):
    """返回 (out_path, missing_fields)。missing_fields 非空表示字段不全。"""
    csv_row, csv_path = resolve_csv_row(job, args.csv)
    row = merge(job, csv_row)

    # 把匹配分析按 link 连进 job dict：qualified_jobs 里没有 match_score/category/…，
    # render() 那些 `job.get(...)` 正是读这几个键。连上后它们才有值，否则回退占位符
    # 「未裁定」。快速模式 / merge 前跑本脚本时数据源缺失，load 会返回空 dict 不报错。
    key = (job.get('link') or '').strip()
    if key:
        match_analysis = load_match_analysis(run_dir)
        if key in match_analysis:
            job = {**job, **(match_analysis[key] or {})}

    greeting, greeting_src = resolve_greeting(run_dir, index, args)

    company = sanitize(row.get('公司') or row.get('company') or '未知公司')
    position = sanitize(row.get('职位') or row.get('position') or '未知岗位')
    out_dir = os.path.join(deliver_dir(run_dir), job_dir_stem(index, company, position))
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, '投递.md')

    existed = os.path.exists(out_path)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(render(row, job, greeting, csv_path, index, csv_row))

    # 单独的 优化建议.md：投递材料交给人看的那份是投递.md，这里是给「改简历」用的，
    # 只含优化建议不重复整份简历。优先 LLM 出的优化 json，缺了就回退规则侧优化点。
    opt = load_resume_optimization(run_dir, index)
    opt_path = os.path.join(out_dir, '优化建议.md')
    with open(opt_path, 'w', encoding='utf-8') as f:
        f.write(render_optimization(company, position, opt, job, index))

    missing = sorted(k for k in KNOWN_FIELDS if not (row.get(k) or '').strip())

    print('%s #%d %s-%s' % ('覆盖' if existed else '新建', index, company, position))
    print('    → %s' % out_path)
    print('    → %s (%s)' % (opt_path, opt['source'] if opt else '规则匹配'))
    if csv_path:
        print('    CSV: %s' % csv_path)
    else:
        print('    ⚠️ 未按 link 找到原始 CSV 行，字段取自 qualified_jobs.json')
    print('    招呼语: %s' % (greeting_src or '⚠️ 无（用 --greeting/--greeting-file 补）'))
    if missing:
        print('    空字段（源头未采到）: %s' % '、'.join(missing))

    return out_path, (missing if not csv_path else [])


def main():
    for _stream in (sys.stdout, sys.stderr):      # Windows 控制台是 GBK
        _stream.reconfigure(encoding='utf-8', errors='replace')

    ap = argparse.ArgumentParser(
        description='生成 deliver/<公司>-<岗位>/投递.md（含全部爬取字段）')
    ap.add_argument('run_dir')
    ap.add_argument('--all', action='store_true', help='处理 qualified_jobs.json 里所有岗位')
    ap.add_argument('--index', type=int, help='只处理第 N 个岗位（1-based，与 greeting_N_ 对齐）')
    ap.add_argument('--greeting', help='招呼语正文（自定义/默认分支用）')
    ap.add_argument('--greeting-file', help='招呼语文件路径')
    ap.add_argument('--csv', help='指定原始爬取 CSV，跳过自动定位')
    args = ap.parse_args()

    if not args.all and args.index is None:
        ap.error('至少给一个：--all 或 --index N')
    if args.all and (args.greeting or args.greeting_file):
        ap.error('--all 时不能给 --greeting/--greeting-file（那是单个岗位的招呼语）')

    jobs = load_jobs(args.run_dir)
    if not jobs:
        print('qualified_jobs.json 里没有岗位')
        return 1

    if args.all:
        targets = list(enumerate(jobs, 1))
    else:
        if not 1 <= args.index <= len(jobs):
            print('--index %d 越界，共 %d 个岗位' % (args.index, len(jobs)))
            return 1
        targets = [(args.index, jobs[args.index - 1])]

    if len(targets) > 1:
        dup = find_duplicate_links(args.run_dir, jobs, [i for i, _ in targets])
        if dup:
            for link, ixs in dup.items():
                print('❌ 同一岗位出现多次（link=%s）：%s'
                      % (link, '、'.join('#%d' % i for i in ixs)))
            print('  同一 link = 同一 HR，重复写出只会产出两套几乎一样的材料，')
            print('  且交付目录虽已按序号分开，不该靠这层兜着同一个岗位投两次。')
            print('  请先在上游去重，或用 --index 只处理其中一个。')
            return 1

    incomplete = 0
    for index, job in targets:
        _, missing = write_one(args.run_dir, job, index, args)
        if missing:
            incomplete += 1

    print('\n共写出 %d 份。' % len(targets))
    if incomplete:
        print('其中 %d 份没找到原始 CSV —— 字段可能被 build_job_view 截断或丢弃，'
              '请确认 source_file 或用 --csv 指定。' % incomplete)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
