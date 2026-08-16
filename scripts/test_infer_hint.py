# -*- coding: utf-8 -*-
"""infer_params.py 缺核心参数时的修复提示测试。

盯的是一个**跨进程契约**：pipeline.py 往子进程环境里塞 BOSS_PIPELINE_STAGE，
infer_params.py 读它来决定印哪种形态的修复命令。这种契约两头分别改都不会报错 ——
改坏了只是提示悄悄退回单独跑的形式，而人照着粘完发现后面的阶段还得自己一个个敲。

第 8 组盯的是同一类事的另一面：印出来的命令里那个**路径**在当前工作目录下必须真的
存在。写死路径的提示在一半场景下粘不动，而这些提示恰恰只有卡住的人才会读到。

跑法：python scripts/test_infer_hint.py
"""

import io
import os
import re
import sys
import json
import shutil
import tempfile
import contextlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

for _stream in (sys.stdout, sys.stderr):
    _stream.reconfigure(encoding='utf-8', errors='replace')

import pipeline
import infer_params as I
from llm.config import LLMConfig

# 不碰开发机上真实的 llm_config.json，也永远不发请求
FAKE_CFG = LLMConfig(base_url='https://fake.local/v1', api_key='sk-fake',
                     model='m-test')

# 用户实际遇到的那一次：关键词/学历/经验都推出来了，只有城市是 None
INFER_NO_CITY = {'keywords': ['AI应用开发', '全栈开发', 'RAG'], 'cities': None,
                 'degree': '本科', 'experience': '在校生', 'job_type': '实习',
                 'match_mode': 'deep', 'top_n': 15,
                 'reasoning': '简历未提供期望城市'}


def run_infer(argv, pipeline_stage=None, infer=None):
    """跑 infer_params.main()，返回 (退出码, 打印出来的全文)。"""
    old_argv, old_chat, old_resolve = sys.argv, I.chat_json, I.resolve
    old_env = os.environ.get('BOSS_PIPELINE_STAGE')
    sys.argv = ['infer_params.py'] + argv
    I.chat_json = lambda *a, **k: dict(infer if infer is not None else INFER_NO_CITY)
    I.resolve = lambda **kw: FAKE_CFG
    if pipeline_stage:
        os.environ['BOSS_PIPELINE_STAGE'] = pipeline_stage
    else:
        os.environ.pop('BOSS_PIPELINE_STAGE', None)

    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            code = I.main()
    finally:
        sys.argv, I.chat_json, I.resolve = old_argv, old_chat, old_resolve
        if old_env is None:
            os.environ.pop('BOSS_PIPELINE_STAGE', None)
        else:
            os.environ['BOSS_PIPELINE_STAGE'] = old_env
    return code, buf.getvalue()


def build_run_dir(expected_city=None):
    run_dir = tempfile.mkdtemp(prefix='infer_hint_')
    json.dump({'basic_info': {'name': '张三', 'expected_city': expected_city},
               'skills': ['Python'], 'projects': None, 'work_experience': []},
              open(os.path.join(run_dir, 'profile.json'), 'w', encoding='utf-8'),
              ensure_ascii=False)
    return run_dir


def main():
    failures = []

    def check(label, cond, detail=''):
        print('%s %s%s' % ('  ✅' if cond else '  ❌', label,
                           '' if cond else '  ← ' + detail))
        if not cond:
            failures.append(label)

    print('=== 1. pipeline.run_stage 必须把 BOSS_PIPELINE_STAGE 传给子进程 ===')
    seen = {}

    def fake_call(cmd, cwd=None, env=None):
        seen['env'] = env or {}
        return 0

    old_call = pipeline.subprocess.call
    pipeline.subprocess.call = fake_call
    try:
        run_dir = build_run_dir()
        pipeline.run_stage('infer', ['python', 'x.py'], run_dir)
        check('子进程环境里有 BOSS_PIPELINE_STAGE',
              'BOSS_PIPELINE_STAGE' in seen['env'],
              '实际键：%s' % sorted(k for k in seen['env'] if k.startswith('BOSS')))
        check('值就是阶段名', seen['env'].get('BOSS_PIPELINE_STAGE') == 'infer',
              '实际 %r' % seen['env'].get('BOSS_PIPELINE_STAGE'))
        check('PYTHONIOENCODING 没被这次改动挤掉',
              seen['env'].get('PYTHONIOENCODING') == 'utf-8')
        check('父进程自己的环境变量还在（是 dict(os.environ, ...) 而不是只有两个键）',
              len(seen['env']) > 3, '只有 %d 个键' % len(seen['env']))
    finally:
        pipeline.subprocess.call = old_call
        shutil.rmtree(run_dir, ignore_errors=True)

    print('\n=== 2. 缺城市 · 由 pipeline 启动 → 印 pipeline 形式 ===')
    run_dir = build_run_dir()
    try:
        code, out = run_infer([run_dir], pipeline_stage='infer')
        check('退出码 1（缺城市不许往下爬）', code == 1, '实际 %s' % code)
        check('印的是 pipeline.py --from infer',
              'pipeline.py --run-dir' in out and '--from infer' in out, out)
        check('带上了缺的那个参数 --city', '--city "' in out, out)
        check('运行目录加了引号（Windows 路径带空格时能直接粘）',
              '--run-dir "%s"' % run_dir in out, out)
        check('没同时印单独跑的形式（两条命令并列会让人不知道该用哪条）',
              'infer_params.py "' not in out, out)
        check('说清了重跑会再调一次模型', '再调一次模型' in out, out)
        check('提到连 --keywords 一起给就能跳过推断', '--keywords 一起给' in out, out)
        check('没写 crawl_params.json（参数不全就不该留半成品）',
              not os.path.exists(os.path.join(run_dir, 'crawl_params.json')))
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)

    print('\n=== 2b. 缺城市时要说清「不给城市 ≠ 全国搜」并给出全国怎么写 ===')
    run_dir = build_run_dir()
    try:
        _, out = run_infer([run_dir], pipeline_stage='infer')
        check('点明了缺城市不等于全国搜', '≠ 全国搜' in out, out)
        check('给出了 --city 全国', '--city 全国' in out, out)
        check('提到「不限」也是同一个意思', '不限' in out, out)
        check('给了 --list-cities 兜底', '--list-cities' in out, out)
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)

    print('\n=== 2c. 「全国」得真的能一路走到爬虫，不能只是提示里好看 ===')
    from boss_crawler.data_loader import load_city_data, find_cities_by_name
    import preferences

    city_data = load_city_data()
    for alias in ('全国', '不限'):
        resolved = find_cities_by_name(city_data, [alias])
        check('%s → BOSS 全国代码 100010000' % alias,
              resolved == [('全国', '100010000')], '实际 %r' % resolved)

    run_dir = build_run_dir()
    try:
        code, out = run_infer([run_dir, '--city', '全国'], pipeline_stage='infer')
        check('--city 全国 能跑通（退出码 0）', code == 0, '实际 %s\n%s' % (code, out))
        params = json.load(open(os.path.join(run_dir, 'crawl_params.json'),
                                encoding='utf-8'))
        check('crawl_params.json 里 cities=["全国"]',
              params.get('cities') == ['全国'], '实际 %r' % params.get('cities'))
        argv = preferences.crawl_argv(params)
        check('拼给爬虫的 argv 里是 -c 全国',
              '-c' in argv and argv[argv.index('-c') + 1] == '全国', '实际 %r' % argv)
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)

    # 岗位池比任何单个城市都大，按小城市的 3 个关键词卡它没道理。weizhi.json 不在
    # 仓库里，所以首次运行只有兜底名单 —— 这条断言盯的就是那个首次运行的场景。
    check('全国/不限 在兜底热门名单里（首次运行没有 weizhi.json 时也给满关键词预算）',
          '全国' in I._FALLBACK_HOT_CITIES and '不限' in I._FALLBACK_HOT_CITIES,
          '实际 %r' % (I._FALLBACK_HOT_CITIES[:3],))
    check('全国的关键词预算是热门城市档',
          I.keyword_budget(['全国']) == I.HOT_CITY_KEYWORDS,
          '实际 %s' % I.keyword_budget(['全国']))

    print('\n=== 2d. 缺参数时要顺手列出常见参数，枚举值必须与爬虫同源 ===')
    run_dir = build_run_dir()
    try:
        _, out = run_infer([run_dir], pipeline_stage='infer')
        for flag in ('--count', '--degree', '--experience', '--job-type',
                     '--salary', '--scale', '--match-mode'):
            check('列出了 %s' % flag, flag in out, out)
        # 值不能是手写的，得跟 boss_crawler 的 FILTER_LABELS 一致 —— 值写错时爬虫
        # 只印一行「未匹配」然后少爬一批，不会失败，所以错的那次看起来跟成功一样。
        for field in ('degree', 'experience', 'job_type', 'salary', 'scale'):
            missing = [v for v in I.valid_values(field) if v not in out]
            check('%s 的全部合法值都印出来了' % field, not missing,
                  '缺 %r' % missing)
        check('说明了传空串表示不筛该项', '空串' in out, out)
        check('说明了 --scale 不做推断', '不做推断' in out, out)
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)

    print('\n=== 2e. 中文按显示宽度对齐（%-*s 会把带中文的行推歪） ===')
    check('_cjk_width 中文算两列', I._cjk_width('本科') == 4,
          '实际 %s' % I._cjk_width('本科'))
    check('_cjk_width 混排', I._cjk_width('--degree 本科') == 13,
          '实际 %s' % I._cjk_width('--degree 本科'))
    check('_cjk_pad 补到指定显示宽度',
          I._cjk_width(I._cjk_pad('--degree 本科', 20)) == 20,
          '实际 %s' % I._cjk_width(I._cjk_pad('--degree 本科', 20)))
    check('_cjk_pad 不截断超宽的串',
          I._cjk_pad('--experience 在校生', 3) == '--experience 在校生')
    run_dir = build_run_dir()
    try:
        _, out = run_infer([run_dir], pipeline_stage='infer')
        # 只看「其余参数」那张表：说明文字的起始列必须一致。用正则找 flag 与说明之间
        # 的空白，而不是找第一个双空格 —— 补出来的空格本身就是双空格，那样量到的是
        # 没补齐的 flag 宽度，永远不相等（第一版断言就栽在这儿）。
        table = [ln for ln in out.splitlines()
                 if re.match(r'^    --(count|degree|experience|job-type|salary'
                             r'|scale|match-mode) ', ln)]
        check('抓到了 7 行参数说明', len(table) == 7, '实际 %d 行' % len(table))
        cols = set()
        for ln in table:
            m = re.match(r'^(\s*\S.*?\S)(\s{2,})(\S.*)$', ln)
            cols.add(I._cjk_width(m.group(1) + m.group(2)))
        check('表格里每行的说明文字都从同一列开始', len(cols) == 1,
              '实际列位置 %r' % sorted(cols))
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)

    print('\n=== 3. 缺城市 · 单独跑 → 印 infer_params 形式 ===')
    run_dir = build_run_dir()
    try:
        code, out = run_infer([run_dir])
        check('退出码 1', code == 1, '实际 %s' % code)
        check('印的是 infer_params.py <run_dir>',
              'infer_params.py "%s"' % run_dir in out, out)
        check('不印 pipeline 形式（没在流水线里就不该提它）',
              'pipeline.py' not in out, out)
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)

    print('\n=== 4. 缺关键词 → 提示换成 --keywords，反向提示换成 --city ===')
    run_dir = build_run_dir()
    try:
        code, out = run_infer([run_dir], pipeline_stage='infer',
                              infer={'keywords': None, 'cities': ['西安']})
        check('退出码 1', code == 1, '实际 %s' % code)
        check('示例参数是 --keywords', '--keywords "' in out, out)
        check('不会把 --city 印成要补的参数',
              '--from infer --city' not in out, out)
        check('反向提示指向 --city', '--city 一起给' in out, out)
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)

    print('\n=== 5. 只给 --profile（没有 run_dir）→ 印 --profile 形式 ===')
    run_dir = build_run_dir()
    profile_path = os.path.join(run_dir, 'profile.json')
    try:
        code, out = run_infer(['--profile', profile_path], pipeline_stage='infer')
        check('退出码 1', code == 1, '实际 %s' % code)
        check('印的是 --profile 形式（run_dir 是 None，不能瞎拼一个）',
              '--profile "%s"' % profile_path in out, out)
        check('没印出 None', 'None' not in out, out)
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)

    print('\n=== 6. 参数齐全时一个字都不该多印 ===')
    run_dir = build_run_dir()
    try:
        code, out = run_infer([run_dir, '--city', '西安'], pipeline_stage='infer')
        check('退出码 0', code == 0, '实际 %s\n%s' % (code, out))
        check('没有修复提示', '补上就能接着跑' not in out, out)
        check('写出了 crawl_params.json',
              os.path.exists(os.path.join(run_dir, 'crawl_params.json')))
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)

    print('\n=== 7. 简历里本来就有期望城市时走正常路径 ===')
    run_dir = build_run_dir(expected_city='杭州')
    try:
        code, out = run_infer([run_dir], pipeline_stage='infer',
                              infer=dict(INFER_NO_CITY, cities=['杭州']))
        check('退出码 0', code == 0, '实际 %s' % code)
        check('没有修复提示', '补上就能接着跑' not in out, out)
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)

    print('\n=== 8. 爬虫印的登录命令也得能直接粘（路径按 cwd 算，不写死） ===')
    # 用户实际踩到的：未登录时爬虫印 `python boss_post_interactive.py --ensure-login`，
    # 而流水线是从仓库根目录起子进程的，脚本在 scripts/ 里 —— 粘过去就是 Errno 2。
    from boss_crawler.utils import entry_cmd, _ENTRY
    import shlex
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    old_cwd = os.getcwd()
    try:
        for label, cwd in (('仓库根目录', repo_root),
                           ('scripts/ 目录', os.path.join(repo_root, 'scripts')),
                           ('用户主目录', os.path.expanduser('~'))):
            os.chdir(cwd)
            cmd = entry_cmd('--ensure-login')
            # 照着粘等于在这个 cwd 下执行：把命令拆开，第二段必须指到真实存在的文件
            path = shlex.split(cmd, posix=False)[1].strip('"')
            check('cwd=%s 时印的路径存在' % label, os.path.isfile(path),
                  '%s → %r' % (cmd, path))
            check('cwd=%s 时指到的就是入口脚本' % label,
                  os.path.samefile(path, _ENTRY) if os.path.isfile(path) else False,
                  cmd)
            check('cwd=%s 时带上了参数' % label, cmd.endswith(' --ensure-login'), cmd)
    finally:
        os.chdir(old_cwd)

    print('\n' + '=' * 60)
    if failures:
        print('❌ %d 项失败：%s' % (len(failures), '；'.join(failures)))
        return 1
    print('✅ 修复提示全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
