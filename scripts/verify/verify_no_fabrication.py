#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""检查生成材料里有没有简历原文没有的技术词（流水线的 verify 阶段）。

为什么要有这个脚本：
「不得编造」这条约束原先**只写在 prompt 里**（`prompts/resume_optimize.st`、
`prompts/greeting.st`），代码层零强制。2026-08-16 复盘的那次实跑里，模型往优化后的
简历里塞进了 PyTorch / nginx / react / AutoGen / CrewAI / LlamaIndex 六个简历原文
根本没有的技术词，全靠当场警觉才发现，用 16 条一行流 + 手写正则清理，其中一次替换
还没命中、留了残留。发出去的是简历长图和招呼语，这类错误一旦投递就收不回来。

判定方式是**纯 LLM 全文判定**：把完整原简历 + 完整待查材料交给模型，让它通读全文、
自己识别材料里**所有**技术名词（多词复合名如 `Vibe Coding`、`GitHub Copilot` 当作
一个整体），逐个对照原简历判「有没有依据」——处理缩写 / 同义词 / 中英等价这些字符串
比对做不到的语义问题。脚本不再维护任何本地词表/正则折词器；复合词被拆碎成 `Vibe`+`Coding`
这类误报，正是这套方案要消灭的。人仍可用 `--allow` 放行模型误判的词，作为最终纠错。

为什么要让模型**列全技术词**而不是只报编造：纯 LLM 判定最大的隐患是模型偷懒一口回
「全干净」却一个词没真查。强制它把识别出的**全部**技术词连同判断一起输出，你看到
「没报词」时也能看清它到底审了多少词，防静默放水。已配不到 LLM 时（无 key / 离线）
本脚本**直接报错退 1**，绝不静默放行，也不退回旧规则。脚本不替代 gate:send —— 它只
挡「原文没有」这一类；语气、夸大、把「了解」写成「精通」仍要人发送前逐条过一遍。

范围（刻意收窄）：
  查   `materials/resume_{i}_*.json` 的 **optimized_resume** 字段 —— render 只渲染这一个
  查   `materials/greeting_{i}_*.txt` 全文
  不查 **optimization_suggestions** —— 那是给人看的改进建议，本来就该出现简历里
       还没有的技术词。
  不查 中文表述的判断 —— 由模型语义判定处理，脚本层不猜语义。

退出码：
  0  干净（模型确认没有编造的技术词）
  1  模型查出编造 / LLM 配置缺失 / 部分材料不可判定 —— 阻断 render
  3  部分：查过的都干净，但有岗位没有材料可查（模型没查全的岗位）

用法:
  python scripts/verify/verify_no_fabrication.py <run_dir>
  python scripts/verify/verify_no_fabrication.py <run_dir> --only 1,3,5-7
  python scripts/verify/verify_no_fabrication.py <run_dir> --allow Vibe Coding,PyTorch
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
from resume_matcher.prompts import get_verify_prompt
from llm import (ConfigError, LLMError, chat_json, map_concurrent, resolve)
import threading


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


# ==================== 原简历来源 ====================

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


def _load_sources(run_dir, override=None):
    """(原文片段列表, 来源说明列表, 拼接的全文)。

    原简历全文 = resume_text.txt（或 --resume-text 覆盖）+ profile.json 的全部字符串。
    这就是喂给 LLM 做判定依据的原文；两个来源取**并集**，漏掉 profile 里的技能/项目
    会把真技能当成编造报出来。
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
                break                       # 择一：override 或 resume_text.txt
    profile_path = _profile_path(run_dir)
    if os.path.isfile(profile_path):
        try:
            with open(profile_path, 'r', encoding='utf-8') as f:
                profile = json.load(f)
            parts.extend(_walk_strings(profile))
            sources.append('profile.json')
        except (ValueError, OSError):
            pass
    return parts, sources, '\n'.join(parts)


def load_context_text(run_dir, override=None):
    """给 LLM 判定用的原简历全文。"""
    _, _, text = _load_sources(run_dir, override)
    return text


def load_sources(run_dir, override=None):
    """(原简历全文, 来源说明列表)。"""
    parts, sources, _ = _load_sources(run_dir, override)
    return '\n'.join(parts), sources


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
        # 只查 optimized_resume。optimization_suggestions 是给人看的建议，
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


def _context(material, term, width=36):
    """term 在材料里的上下文，给人核对用 —— 光给一个词看不出是不是真编造。"""
    pos = material.find(term)
    if pos < 0:
        return ''
    start = max(0, pos - width)
    end = min(len(material), pos + len(term) + width)
    snippet = material[start:end].replace('\n', ' ').replace('\r', ' ')
    return ('…' if start else '') + re.sub(r'\s+', ' ', snippet) + ('…' if end < len(material) else '')


# ==================== --allow 归一 ====================

def _normalize(term):
    """归一形：去空格/点/连字符/下划线，转小写，保留 `+#`（C++/C# 各不相同）。

    用于把模型报出的词跟 `--allow` 名单对上。复合名词 `Vibe Coding` 归一成
    `vibecoding`，放行时两个等价的下划线/连字符写法也能对上。
    """
    return re.sub(r'[\s._\-]', '', str(term or '')).strip().lower()


# ==================== LLM 判定 ====================

# verify.st 让模型把**全部**技术词连同 reason 引证一起列出，输出量随材料规模线性
# 上涨 —— 这是方案一（列全词防偷懒）的固有代价。默认 max_tokens 只有 4096，塞不
# 下会以 stop_reason=max_tokens 截断、返回空正文。所以 verify 单独抬高预算。
_VERIFY_MAX_TOKENS = 16384


def judge_target(index, label, material, resume_ctx, cfg, run_dir, kind):
    """对一份材料做模型全文判定。

    返回 (label, kept)：
      kept = [(term, reason), ...] —— 模型判「没有依据」的技术词（含空 reason 兜底）。
    模型调用失败（LLMError/ConfigError）时返回 kept=[FALLBACK]，把整份材料当作
    「不可判定」阻断 —— 无法确认就比假干净安全，绝不静默放过。
    """
    try:
        prompt = get_verify_prompt(resume_text=resume_ctx or '（原文未提供）',
                                   material=material or '（材料未提供）',
                                   kind=kind)
        data = chat_json(prompt, stage='verify', run_dir=run_dir, cfg=cfg,
                         max_tokens=_VERIFY_MAX_TOKENS)
    except (LLMError, ConfigError) as exc:
        print('  ⚠ %s 判定失败，按「不可判定」阻断：%s' % (label, str(exc)[:200]))
        return label, [('（%s 模型评审失败，需人工核查）' % label, '')]

    kept = []
    entries = data.get('terms') if isinstance(data, dict) else None
    if not isinstance(entries, list):
        print('  ⚠ %s 模型返回结构不对，按「不可判定」阻断' % label)
        return label, [('（%s 模型返回结构异常，需人工核查）' % label, '')]

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        term = str(entry.get('term') or '').strip()
        if not term:
            continue
        supported = bool(entry.get('supported'))
        reason = str(entry.get('reason') or '').strip()
        # 判「有依据」却没有引证的空理由，按无效处理（懒模型全标通过等于放水）。
        # 判「无依据」的保留；reason 可为空（附上下文兜底，见 main 打印）。
        if not supported or (supported and not reason):
            if supported and not reason:
                # 空理由的 supported 也升格为嫌疑：模型可能偷懒全标通过。
                reason = reason or '（模型判有依据但没给引证，按无依据保留）'
                kept.append((term, reason))
            else:
                kept.append((term, '无' if not reason else reason))
    return label, kept


def apply_allow(kept, allowed):
    """用 --allow 名单过滤模型报出的词。命中（归一形）即剔除。"""
    if not allowed:
        return kept
    return [(term, reason) for term, reason in kept
            if _normalize(term) not in allowed]


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
        'sources': list(sources),
        'allow': list(allow),
        'checked': checked,                       # 文件名 → mtime
        'unreadable': [name for name, _ in broken],
        'findings': [
            {'index': index, 'file': label,
             'terms': [{'term': term, 'reason': reason} for term, reason in hits]}
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


# ==================== 本地取词（仅供 eval/* 评估工具用，verify 主流程不用） ====================

# verify 闸门（方案一）已弃用本地折词，改纯 LLM 全文判定。但 offline 评估工具
# eval/metrics.py（term_stats / added_block_stats / greeting_stats）和
# eval/evaluate_materials.py 要靠**词表差异**量化「优化简历/招呼语新增了哪些原文没有的
# 技术词」当 KPI —— 那是跑分需要，不是投递闸门；拆词在那里的作用是精确计数。
# 因此以下函数保留给它们 import，verify 的 main/judge 路径一概不依赖（可参考头部注释）。

_TOKEN = re.compile(r'[A-Za-z][A-Za-z0-9+#._-]*')

# 与技术能力无关的通用词。宁可短也不要长 —— 每加一个词就少一次提醒的机会。
_GENERIC = frozenset(x.lower() for x in (
    'a', 'an', 'the', 'and', 'or', 'of', 'to', 'in', 'on', 'at', 'for', 'with', 'by',
    'is', 'are', 'be', 'as', 'it', 'no', 'not', 'all', 'any', 'my', 'me', 'i',
    'ok', 'hr', 'jd', 'qq', 'id', 'url', 'http', 'https', 'www', 'com', 'cn', 'net',
    'e', 'g', 'etc', 'vs', 'via', 'per', 'top', 'n', 'x', 'y', 'z',
))

# 同一个东西的不同写法。左边出现在源里就等于右边也出现过。
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
    """归一形：大小写、点、连字符、下划线都不算区别。
    C++ 的 + 和 C# 的 # 保留，因为 c / c++ / c# 是三种不同技能。"""
    return re.sub(r'[._\-]', '', token).strip().lower()


def _tokens(text):
    """一段文本里的技术词候选（归一形 → 原文首次出现的写法）。仅供评估工具。"""
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
    """基底文本 → 归一后的词集合（含别名展开）。仅供评估工具。"""
    keys = set(_tokens(text))
    for key in list(keys):
        keys.update(_ALIASES.get(key, ()))
    return keys


def load_baseline(run_dir, override=None):
    """(归一词集合, 来源说明列表)。仅供评估工具：原文可选 --resume-text，且并集 profile.json。"""
    _, sources, text = _load_sources(run_dir, override)
    return _baseline_keys(text), sources


# ==================== main ====================

def main(argv=None):
    ap = argparse.ArgumentParser(
        description='检查生成材料里有没有简历原文没有的技术词（verify 阶段）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='模型通读完整原简历 + 完整材料，识别并逐条判断所有技术词。'
               '不支持前缀词表，复合名词（Vibe Coding）由模型当一个整体判断。'
               '详见脚本头部注释。')
    ap.add_argument('run_dir')
    ap.add_argument('--only', help='只查这些序号（1,3,5-7），与 materials/render 同一套序号')
    ap.add_argument('--allow', action='append', default=[],
                    help='放行这些词（逗号分隔，可重复）—— 模型误判、但人核实有依据时用')
    ap.add_argument('--resume-text', help='原简历基底文件，默认 <run_dir>/resume_text.txt')
    ap.add_argument('--quiet', action='store_true', help='只打结论，不打上下文')
    ap.add_argument('--model', help='覆盖模型（透传自 pipeline，一般不用手敲）')
    ap.add_argument('--base-url', dest='base_url', help='覆盖 base_url（透传自 pipeline）')
    ap.add_argument('--api-key', dest='api_key', help='覆盖 api_key（透传自 pipeline）')
    args = ap.parse_args(argv)

    if not os.path.isdir(args.run_dir):
        print('[错误] 运行目录不存在: %s' % args.run_dir)
        return 1

    allow = []
    for chunk in args.allow:
        allow.extend(p.strip() for p in str(chunk).split(',') if p.strip())
    allowed = {_normalize(t) for t in allow}

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

    # 原简历全文必须有，否则模型没有「原文」可比；没有原文时无法判定，退 1。
    resume_ctx, sources = load_sources(args.run_dir, args.resume_text)
    if not sources or not resume_ctx.strip():
        print('[缺原文] 既没有 resume_text.txt 也没有 profile.json，无法判断材料里的技术词有没有依据')
        print('  给原文：--resume-text <文件>')
        return 1

    overrides = {'model': args.model, 'base_url': args.base_url,
                 'api_key': args.api_key}
    try:
        cfg = resolve(stage='verify', **overrides)
        cfg.require_key()
    except ConfigError as exc:
        # 纯 LLM 判定没有本地规则可退化：配不到模型就直接阻断，绝不静默放行。
        print('\n[错误] verify 需要 LLM 来通读全文判定，但没配到模型：%s'
              % str(exc).splitlines()[0])
        print('  配法：scripts/llm_config 相关，或 --model/--base-url/--api-key')
        return 1

    targets, broken = collect_targets(args.run_dir, picked)

    print('原简历: %s' % ('、'.join(sources)))

    for name, err in broken:
        print('  ⚠ 读不动，已跳过: %s（%s）' % (name, err))

    if not targets:
        print('[无材料] materials/ 下没有可查的 optimized_resume 或 greeting_*.txt')
        return 1

    print('待查: %d 份材料' % len(targets))

    # ── 并发逐份判定：只输出开始提示 + 每完成一条打一行计数，不做 verbose 进度条 ──
    workers = cfg.concurrency if cfg else 4
    print('开始判定 %d 份材料（并发 %d）...' % (len(targets), workers))

    _lock = threading.Lock()
    _done = [0]

    def _track_label(item):
        return '优化简历' if item[1].startswith('resume_') else '招呼语'

    def _count_line(ok_str):
        with _lock:
            _done[0] += 1
            print('  [%d/%d] %s' % (_done[0], len(targets), ok_str), flush=True)

    def _track(item):
        try:
            value = judge_target(item[0], item[1], item[2],
                                 resume_ctx, cfg, args.run_dir, _track_label(item))
            _count_line('完成')
            return value
        except Exception:
            _count_line('失败')
            raise                       # map_concurrent 捕获并记为 ok=False，不崩全批

    outcomes = map_concurrent(
        targets, _track,
        workers=workers,
        quiet=True,
    )

    ok_n = sum(1 for o in outcomes if o.ok)
    fail_n = len(outcomes) - ok_n
    tail = '' if not fail_n else '，%d 条判定失败（保持原样）' % fail_n
    print('判定完成: %d 份材料处理完（%d 成功%s)' % (len(outcomes), ok_n, tail))

    findings = []
    for oc in outcomes:
        if not oc.ok:
            continue
        label, kept = oc.value                 # judge_target 返回 (label, kept)
        kept = apply_allow(kept, allowed)
        if kept:
            index = next((i for i, l, _ in targets if l == label), -1)
            findings.append((index, label, kept))

    # 全量报告在判定之后写；--only 子集不写全量报告（会把没查的标成查过）。
    if picked is None:
        err = write_report(args.run_dir, sources, targets, findings, broken, allow)
        if err:
            print('  ⚠ verify_report.json 没写成（%s）—— 结论仍然有效，只是没留痕' % err)
    else:
        print('  （--only 子集，不写 verify_report.json）')

    if not findings:
        print('\n✅ 模型通读后没有查出简历原文之外的技术词')
        if broken:
            print('  （但有 %d 份材料读不动，那几份没查过）' % len(broken))
            return 3
        return 0

    total = sum(len(hits) for _, _, hits in findings)
    print('\n❌ %d 份材料里查出 %d 处简历原文没有的技术词：' % (len(findings), total))
    text_of = {label: text for _, label, text in targets}
    for index, label, hits in findings:
        print('\n  #%d  %s' % (index, label))
        for term, reason in hits:
            print('    • %s' % term)
            if reason and reason != '无':
                print('       模型评语: %s' % reason)
            ctx = _context(text_of.get(label, ''), term)
            if ctx and not args.quiet:
                print('       上下文: %s' % ctx)

    # 提示用户投递材料在哪、并自动打开该目录，方便逐份核对被标记的内容。
    dlv_dir = os.path.normpath(os.path.abspath(deliver_dir(args.run_dir)))
    print('\n📂 投递可读材料/报告: %s' % dlv_dir)
    print('   已为你打开: %s' % dlv_dir)
    open_in_file_manager(dlv_dir)

    # 三条命令都走 pipeline、都能直接粘出去跑。序号去重（同一岗位的简历和招呼语
    # 各算一条 finding，不去重会打出 --only 1,1,2）；放行名单按 **词** 原样写。
    base = 'python scripts/pipeline.py --run-dir "%s"' % args.run_dir
    indices = sorted({i for i, _, _ in findings})
    allow_terms = ','.join(allow) or '<在此填上你核实的词>'

    # 这道闸门只在「带 --to render 的整轮 pipeline」里成立：verify 排在 render 之前，
    # 查出疑点词时退出 1 会先拦住 render。单独运行 verify.py 只是自检，它不拦截任何
    # 下游 —— 所以 --allow / --skip-verify 这两个开关，也只在你**之后**跑 `--to render`
    # 那一遍时才有意义；单独跑 verify，放不放了结都是一次检测，拦不到 anything。
    print('\n说明：本次检查只在整轮 pipeline（`--to render`）里构成闸门 ——')
    print('  verify 排在 render 之前，查出问题会用退出码拦住 render。')
    print('  单独运行 verify.py 只是自检，不拦任何下游；因此下面命令里的')
    print('  --allow / --skip-verify，也都只作用于 `--to render` 那一遍。')

    print('\n下一步（三个方向，都能直接粘出去跑；render 会把材料原样发出去）：')
    print('  1) 模型误判、词其实有据 → 放行这些词，重验通过后走到 render：')
    print('       %s --from verify --to render --allow %s' % (base, allow_terms))
    print('  2) 确属编造 → 只重生成这几份材料后走到 render（--force 覆盖，--only 只动它们）：')
    print('       %s --from materials --to render --only %s --force'
          % (base, ','.join(str(i) for i in indices)))
    print('  3) 跳过本检查直接渲染（不查了，材料里可能有编造，发送前自己逐份过一遍）：')
    print('       %s --from verify --to render --skip-verify' % base)
    print('\n注意：本脚本只查「原文没有的技术词」。夸大、把「了解」写成「精通」、'
          '语气不当都查不出来 —— 发送前仍要逐条过一遍。')
    return 1


if __name__ == '__main__':
    reconfigure_stdout()
    sys.exit(main())