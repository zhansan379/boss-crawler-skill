#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""检查生成材料里有没有简历原文没有的技术词（流水线的 verify 阶段）。

为什么要有这个脚本：
「不得编造」这条约束原先**只写在 prompt 里**（`prompts/resume_optimize.st`、
`prompts/greeting.st`），代码层零强制。2026-08-16 复盘的那次实跑里，模型往优化后的
简历里塞进了 PyTorch / nginx / react / AutoGen / CrewAI / LlamaIndex 六个简历原文
根本没有的技术词，全靠当场警觉才发现，用 16 条一行流 + 手写正则清理，其中一次替换
还没命中、留了残留。发出去的是简历长图和招呼语，这类错误一旦投递就收不回来。

范围（刻意收窄）：
  查   `materials/resume_{i}_*.json` 的 **optimized_resume** 字段 —— render 只渲染这一个
  查   `materials/greeting_{i}_*.txt` 全文
  不查 **optimization_suggestions** —— 那是给人看的改进建议，本来就该出现简历里
       还没有的技术词。复盘时的误判正是从这里来的。
  不查 中文表述。「多智能体面试系统」那次误判说明中文语义判断不可靠：那个词在简历
       第 28 行（中国软件杯《AI模拟面试系统》）和第 34 行（期刊论文）都有依据。
       本脚本只认**拉丁字母技术词**，那也正是六个真阳性的形态。

判定方式是**白名单式**：基底里出现过的词一律放过，没出现过的一律报出来。
反过来（维护一份「危险词库」）永远追不上模型的想象力。代价是会有误报，
所以留了 `--allow` —— 有据可依的词一次性放行，而不是让人去改脚本。

这个脚本**不替代 gate:send**。它只挡住「原文没有」这一类，语气、夸大、
把「了解」写成「精通」都查不出来，人仍然要在发送前逐条过一遍。

退出码：
  0  干净（或没有可查的材料且未要求严格）
  1  查出可疑词，或基底/材料缺失 —— 两种都该阻断 render
  2  用法错误（序号越界、参数写错）
  3  部分：查过的都干净，但有岗位没有材料可查

用法:
  python scripts/verify/verify_no_fabrication.py <run_dir>
  python scripts/verify/verify_no_fabrication.py <run_dir> --only 1,3,5-7
  python scripts/verify/verify_no_fabrication.py <run_dir> --allow PyTorch,nginx
  python scripts/verify/verify_no_fabrication.py <run_dir> --resume-text path/to/base.txt
"""

import argparse
import glob
import json
import os
import re
import subprocess
import sys

_SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _SCRIPTS)

import check_artifacts
from check_artifacts import parse_only          # 与 materials/核对侧共用同一份严格解析
from resume_matcher import (resume_text_path, profile_path as _profile_path,
                            materials_dir, verify_report_path, deliver_dir)


def reconfigure_stdout():
    for stream in (sys.stdout, sys.stderr):     # Windows 控制台是 GBK
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError, OSError):
            pass


def open_in_file_manager(path):
    """跨平台在文件管理器里打开 path（Windows start / macOS open / Linux xdg-open）。

    打开失败只提示、绝不改变 verify 的结论或退出码 —— 路径照旧打印在 stdout。
    """
    try:
        if sys.platform.startswith('win'):
            os.startfile(path)
        elif sys.platform == 'darwin':
            subprocess.run(['open', path], check=False)
        else:
            subprocess.run(['xdg-open', path], check=False)
        return True
    except Exception as exc:                    # noqa: BLE001 —— 打开失败不该放倒 verify
        print('  （自动打开失败：%s，可手动打开上面的路径）' % exc)
        return False


# ==================== 取词 ====================

# 拉丁字母开头的连续技术词形：Node.js / C++ / C# / scikit-learn / PyTorch / K8s
# 都能整体取到。中文之间的英文词也能取到，因为中文字符不在字符集里。
#
# `/` **不在**字符集里，它在简历里是分隔符不是词的一部分 ——「MySQL/Redis 存储」
# 若整体成词就会归一成 mysqlredis，两个明明在原文里的技能被报成编造（实测踩到过）。
_TOKEN = re.compile(r'[A-Za-z][A-Za-z0-9+#._-]*')

# 与技术能力无关的通用词。宁可短也不要长 —— 每加一个词就少一次提醒的机会，
# 而误报有 --allow 兜着，漏报没有任何兜底。
_GENERIC = frozenset(x.lower() for x in (
    'a', 'an', 'the', 'and', 'or', 'of', 'to', 'in', 'on', 'at', 'for', 'with', 'by',
    'is', 'are', 'be', 'as', 'it', 'no', 'not', 'all', 'any', 'my', 'me', 'i',
    'ok', 'hr', 'jd', 'qq', 'id', 'url', 'http', 'https', 'www', 'com', 'cn', 'net',
    'e', 'g', 'etc', 'vs', 'via', 'per', 'top', 'n', 'x', 'y', 'z',
))

# 同一个东西的不同写法。左边出现在基底里就等于右边也出现过。
# 只放**确定同义**的，「会 Java 所以会 Kotlin」这种推断不属于这里。
_ALIASES = {
    'k8s': ('kubernetes',),
    'kubernetes': ('k8s',),
    'js': ('javascript',),
    'javascript': ('js',),
    'ts': ('typescript',),
    'typescript': ('ts',),
    'nodejs': ('node',),
    'node': ('nodejs',),
    'postgres': ('postgresql',),
    'postgresql': ('postgres',),
    'py': ('python',),
    'golang': ('go',),
    'go': ('golang',),
    'tf': ('tensorflow',),
    'tensorflow': ('tf',),
    'ml': ('machinelearning',),
    'dl': ('deeplearning',),
    'llm': ('llms',),
    'llms': ('llm',),
}


def _norm(token):
    """比较用的归一形。大小写、点、连字符、下划线都不算区别。

    Node.js / nodejs / Node-JS 归一到同一个 key；C++ 的 + 和 C# 的 # 保留，
    因为 c / c++ / c# 是**三种不同**的技能，抹掉就分不出来了。
    斜杠不在这里，它已经在 _TOKEN 那层当分隔符切开了。
    """
    return re.sub(r'[._\-]', '', token).strip().lower()


def _tokens(text):
    """一段文本里的技术词候选（归一形 → 原文首次出现的写法）。"""
    found = {}
    for raw in _TOKEN.findall(text or ''):
        key = _norm(raw)
        if len(key) < 2 or key in _GENERIC:
            continue
        if key.isdigit():
            continue
        found.setdefault(key, raw)
    return found


def _baseline_keys(text):
    """基底文本 → 归一后的词集合（含别名展开）。"""
    keys = set(_tokens(text))
    for key in list(keys):
        keys.update(_ALIASES.get(key, ()))
    return keys


# ==================== 基底 ====================

def _walk_strings(node):
    """任意嵌套结构里的所有字符串。profile.json 的技能/项目/经历都在里面。"""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            for s in _walk_strings(value):
                yield s
    elif isinstance(node, (list, tuple)):
        for item in node:
            for s in _walk_strings(item):
                yield s


def load_baseline(run_dir, override=None):
    """(基底词集合, 来源说明列表)。

    优先级与 gen_materials.load_resume_text 保持一致（--resume-text >
    resume_text.txt），但这里是**并集而不是择一**：profile.json 里的技能列表、
    项目、获奖也都是有据可依的原文，漏掉它们会把真技能报成编造。
    """
    parts, sources = [], []

    for path in (override, resume_text_path(run_dir)):
        if path and os.path.isfile(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    text = f.read()
            except OSError:
                continue
            if text.strip():
                parts.append(text)
                sources.append(os.path.basename(path))
                break                       # 与 gen_materials 一致：择一，不叠加

    profile_path = _profile_path(run_dir)
    if os.path.isfile(profile_path):
        try:
            with open(profile_path, 'r', encoding='utf-8') as f:
                profile = json.load(f)
            parts.extend(_walk_strings(profile))
            sources.append('profile.json')
        except (ValueError, OSError):
            pass

    return _baseline_keys('\n'.join(parts)), sources


# ==================== 待查材料 ====================

def _index_of(path, kind):
    m = re.match(r'%s_(\d+)_' % kind, os.path.basename(path))
    return int(m.group(1)) if m else None


def collect_targets(run_dir, only=None):
    """[(序号, 标签, 文本)]，按序号排序。只取会真正发出去的内容。"""
    gen = materials_dir(run_dir)
    targets, broken = [], []

    for path in sorted(glob.glob(os.path.join(gen, 'resume_*_*.json'))):
        index = _index_of(path, 'resume')
        if index is None or (only is not None and index not in only):
            continue
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (ValueError, OSError) as e:
            broken.append((os.path.basename(path), str(e)))
            continue
        # 只看 optimized_resume。optimization_suggestions 是给人看的建议，
        # 出现新技术词是它的本职工作，查它就是制造误报。
        body = (data or {}).get('optimized_resume')
        if isinstance(body, str) and body.strip():
            targets.append((index, os.path.basename(path), body))

    for path in sorted(glob.glob(os.path.join(gen, 'greeting_*_*.txt'))):
        index = _index_of(path, 'greeting')
        if index is None or (only is not None and index not in only):
            continue
        try:
            with open(path, 'r', encoding='utf-8') as f:
                text = f.read()
        except OSError as e:
            broken.append((os.path.basename(path), str(e)))
            continue
        if text.strip():
            targets.append((index, os.path.basename(path), text))

    targets.sort(key=lambda t: (t[0], t[1]))
    return targets, broken


def _context(text, raw_token, width=36):
    """可疑词出现的上下文，给人判断用 —— 光给一个词看不出是不是真编造。"""
    pos = text.find(raw_token)
    if pos < 0:
        return ''
    start = max(0, pos - width)
    end = min(len(text), pos + len(raw_token) + width)
    snippet = text[start:end].replace('\n', ' ').replace('\r', ' ')
    return ('…' if start else '') + re.sub(r'\s+', ' ', snippet) + ('…' if end < len(text) else '')


# ==================== 检查 ====================

def verify(run_dir, baseline, only=None, allow=()):
    """返回 (findings, targets, broken)。

    findings: [(序号, 标签, [(原文写法, 上下文), ...])]
    `baseline` 由 load_baseline() 给出 —— 传进来而不是在这里算，是为了让调用方
    先判断「有没有基底」：没有基底时一个词都查不出，那是假阴性而不是干净。
    """
    allowed = set()
    for term in allow:
        # 走 _tokens 而不是 _norm：放行名单和检出用同一套取词规则，
        # 才不会出现「--allow MySQL/Redis 写了却没生效」这种对不上的情况。
        for key in _tokens(term):
            allowed.add(key)
            allowed.update(_ALIASES.get(key, ()))

    targets, broken = collect_targets(run_dir, only)

    findings = []
    for index, label, text in targets:
        hits = []
        for key, raw in sorted(_tokens(text).items()):
            if key in baseline or key in allowed:
                continue
            hits.append((raw, _context(text, raw)))
        if hits:
            findings.append((index, label, hits))
    return findings, targets, broken


# ==================== 留痕 ====================

def write_report(run_dir, sources, targets, findings, broken, allow):
    """落一份 verify_report.json。写失败返回原因，成功返回 None。

    这一步本身不产材料，不留痕迹的话 where_am_i.py 就看不见它跑过没有 ——
    而看不见的检查会在上下文丢失后被跳过，那正是最需要它的时候。

    报告里存**被查文件的 mtime**：材料重新生成过就说明这份报告过期了。
    只记「查过」而不记查的是哪一版，等于给人一个永远显示通过的旧结论。
    """
    gen = materials_dir(run_dir)
    checked = {}
    for _, label, _ in targets:
        try:
            checked[label] = os.path.getmtime(os.path.join(gen, label))
        except OSError:
            checked[label] = None

    report = {
        'baseline_sources': list(sources),
        'allow': list(allow),
        'checked': checked,                       # 文件名 → mtime
        'unreadable': [name for name, _ in broken],
        'findings': [
            {'index': index, 'file': label, 'terms': [raw for raw, _ in hits]}
            for index, label, hits in findings
        ],
        'clean': not findings,
    }
    try:
        vpath = verify_report_path(run_dir)
        os.makedirs(os.path.dirname(vpath), exist_ok=True)
        with open(vpath, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    except OSError as e:
        return str(e)
    return None


def report_is_stale(run_dir):
    """(该重跑的原因, 报告 dict)；不用重跑时原因是 None。

    给 where_am_i.py 用。材料比报告新 = 重新生成过 = 那一版没查过。
    """
    try:
        with open(verify_report_path(run_dir),
                  'r', encoding='utf-8') as f:
            report = json.load(f)
    except (ValueError, OSError):
        return '还没查过', None
    if not isinstance(report, dict):
        return '报告格式不对，重查一遍', None

    gen = materials_dir(run_dir)
    checked = report.get('checked') or {}
    for label, was in checked.items():
        try:
            now = os.path.getmtime(os.path.join(gen, label))
        except OSError:
            return '%s 已经不在了' % label, report
        # 1 秒容差：FAT / 网络盘的 mtime 精度只到秒
        if was is None or now > was + 1:
            return '%s 查过之后又改了' % label, report

    # 报告里没记的材料就是新生成的，同样没查过
    for pattern in ('resume_*_*.json', 'greeting_*_*.txt'):
        for path in glob.glob(os.path.join(gen, pattern)):
            if os.path.basename(path) not in checked:
                return '%s 是新的，没查过' % os.path.basename(path), report

    if not report.get('clean'):
        return '上次查出了编造，还没处理完', report
    return None, report


def main(argv=None):
    ap = argparse.ArgumentParser(
        description='检查生成材料里有没有简历原文没有的技术词（verify 阶段）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='只查 optimized_resume 和 greeting_*.txt；不查 optimization_suggestions，'
               '也不判断中文表述。详见脚本头部注释。')
    ap.add_argument('run_dir')
    ap.add_argument('--only', help='只查这些序号（1,3,5-7），与 materials/render 同一套序号')
    ap.add_argument('--allow', action='append', default=[],
                    help='放行这些词（逗号分隔，可重复）—— 有据可依但不在基底文本里时用')
    ap.add_argument('--resume-text', help='基底文件，默认 <run_dir>/resume_text.txt')
    ap.add_argument('--quiet', action='store_true', help='只打结论，不打上下文')
    args = ap.parse_args(argv)

    if not os.path.isdir(args.run_dir):
        # 1 不是 2：命令本身没写错，是前置条件（目录还没跑出来）不满足。
        # 口径跟 gen_materials.py:400、read_thin.py:215 保持一致。
        print('[错误] 运行目录不存在: %s' % args.run_dir)
        return 1

    allow = []
    for chunk in args.allow:
        allow.extend(p.strip() for p in str(chunk).split(',') if p.strip())

    # 序号范围拿 qualified_jobs.json 的条数来卡，跟 materials 核对侧同一份严格解析：
    # 越界是调用方写错了，静默忽略会让人以为查过了。
    picked = None
    if args.only:
        try:
            total = len(check_artifacts._load_jobs(args.run_dir))
        except (ValueError, OSError) as e:
            print('[错误] --only 需要 qualified_jobs.json 来判断序号范围，但读不动: %s' % e)
            return 2
        try:
            picked = parse_only(args.only, total)
        except ValueError as e:
            print('[错误] %s' % e)
            return 2

    baseline, sources = load_baseline(args.run_dir, args.resume_text)

    if not sources:
        # 没有基底就没有「原文」可比，此时「一个可疑词都没查出」是假阴性，
        # 比报错危险得多 —— 所以这里退 1 阻断，而不是当干净放过。
        print('[缺基底] 既没有 resume_text.txt 也没有 profile.json，无法判断哪些词是原文里的')
        print('  给基底：--resume-text <文件>')
        return 1

    findings, targets, broken = verify(
        args.run_dir, baseline, only=picked, allow=allow)

    print('基底: %s（%d 个词）' % ('、'.join(sources), len(baseline)))

    for name, err in broken:
        print('  ⚠ 读不动，已跳过: %s（%s）' % (name, err))

    if not targets:
        print('[无材料] materials/ 下没有可查的 optimized_resume 或 greeting_*.txt')
        return 1

    print('待查: %d 份材料' % len(targets))

    # 报告在判定之前写：查出编造时也要留痕，否则重跑一次就「看起来没查出问题」。
    # --only 跑的是子集，写全量报告会把没查的那几份也标成查过 —— 所以不写。
    if picked is None:
        err = write_report(args.run_dir, sources, targets, findings, broken, allow)
        if err:
            print('  ⚠ verify_report.json 没写成（%s）—— 结论仍然有效，只是没留痕' % err)
    else:
        print('  （--only 子集，不写 verify_report.json）')

    if not findings:
        print('\n✅ 没查出简历原文之外的技术词')
        if broken:
            print('  （但有 %d 份材料读不动，那几份没查过）' % len(broken))
            return 3
        return 0

    total = sum(len(h) for _, _, h in findings)
    print('\n❌ %d 份材料里查出 %d 处简历原文没有的技术词：' % (len(findings), total))
    for index, label, hits in findings:
        print('\n  #%d  %s' % (index, label))
        for raw, ctx in hits:
            print('    • %s' % raw)
            if ctx and not args.quiet:
                print('      %s' % ctx)

    # 提示用户投递材料在哪、并自动打开该目录，方便逐份核对被标记的内容。
    dlv_dir = os.path.normpath(os.path.abspath(deliver_dir(args.run_dir)))
    print('\n📂 投递可读材料/报告: %s' % dlv_dir)
    print('   已为你打开: %s' % dlv_dir)
    open_in_file_manager(dlv_dir)

    # 两个提示都要能直接粘出去跑，所以：序号去重（同一个岗位的简历和招呼语
    # 各算一条 finding，不去重会打出 --only 1,1,2）；放行名单按**词**截断而不是
    # 按字符截断 —— 截在词中间会得到一条看着能用、其实放行了别的词的命令。
    indices = sorted({i for i, _, _ in findings})
    terms = sorted({raw for _, _, hits in findings for raw, _ in hits}, key=str.lower)
    shown, extra = terms[:20], len(terms) - 20

    print('\n下一步（二者选一，别直接跑 render）：')
    print('  有据可依 → 放行后重跑本条：--allow %s%s'
          % (','.join(shown), ('   （另有 %d 个词未列出）' % extra) if extra > 0 else ''))
    print('  确属编造 → 重生成这几个岗位：python scripts/stages/gen_materials.py "%s" --only %s --force'
          % (args.run_dir, ','.join(str(i) for i in indices)))
    print('\n注意：本脚本只查「原文没有的技术词」。夸大、把「了解」写成「精通」、'
          '语气不当都查不出来 —— 发送前仍要逐条过一遍。')
    return 1


if __name__ == '__main__':
    reconfigure_stdout()
    sys.exit(main())
