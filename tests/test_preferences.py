#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""参数预设的回归测试。

覆盖 2026-08-14 加的 scripts/preferences.py。最要紧的一组是 [3] ——
白名单丢弃未知键，那是「预设不能变成绕过 gate:send 发送前确认的开关」这条安全边界。

用 BOSS_SKILL_ASSETS 指向临时目录，不碰真实的 assets/preferences.json。

跑法: python tests/test_preferences.py
"""

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
sys.path.insert(0, HERE)

FAILURES = []
_TMP = None


def check(label, cond, detail=''):
    if cond:
        print('  ✅ %s' % label)
    else:
        print('  ❌ %s  %s' % (label, detail))
        FAILURES.append(label)


def setup_tmp_assets():
    """把 assets 指到临时目录，然后才 import preferences。"""
    global _TMP
    _TMP = tempfile.mkdtemp(prefix='prefs_test_')
    os.environ['BOSS_SKILL_ASSETS'] = _TMP
    return _TMP


def write_raw(data):
    """绕过 save()，直接把任意 JSON 写进预设文件（模拟用户手改）。"""
    import preferences
    path = preferences.prefs_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        if isinstance(data, str):
            f.write(data)
        else:
            json.dump(data, f, ensure_ascii=False)


# ==================== 1. save / load 往返 ====================

def test_roundtrip():
    print('\n[1] save → load 往返')
    import preferences

    preferences.clear()
    preferences.save(
        cities='太原', keywords='AI应用开发,Python',
        match_mode='deep', top_n=10, count=20, degree='本科', mode='custom',
    )
    prefs = preferences.load()

    check('城市存成 list', prefs['cities'] == ['太原'], prefs.get('cities'))
    check('逗号分隔的关键词被切开',
          prefs['keywords'] == ['AI应用开发', 'Python'], prefs.get('keywords'))
    check('top_n 是 int', prefs['top_n'] == 10 and isinstance(prefs['top_n'], int),
          repr(prefs.get('top_n')))
    check('count 是 int', prefs['count'] == 20 and isinstance(prefs['count'], int),
          repr(prefs.get('count')))
    check('match_mode 存下来', prefs['match_mode'] == 'deep', prefs.get('match_mode'))
    check('学历存成 list', prefs['degree'] == ['本科'], prefs.get('degree'))
    check('写了 saved_at', bool(prefs.get('saved_at')), prefs.get('saved_at'))
    check('写了 version', prefs.get('version') == preferences.SCHEMA_VERSION,
          prefs.get('version'))
    check('今天存的 → age 0', preferences.age_days(prefs) == 0,
          preferences.age_days(prefs))


# ==================== 2. show 的退出码 ====================

def test_show_exit_codes():
    print('\n[2] show 的退出码（Claude 靠它分支）')
    import preferences

    preferences.clear()
    check('没有预设 → 退出 1', preferences.main(['show']) == 1)

    preferences.save(cities='太原', keywords='Python', match_mode='quick')
    check('有预设 → 退出 0', preferences.main(['show']) == 0)


# ==================== 2b. missing：预设缺字段 → 主代理该问 ====================

def test_missing_fields():
    """预设存在但缺字段时，missing 要报出来（退出 1），好让主代理补问而不是静默放行。"""
    print('\n[2b] missing —— 预设缺失的可补问字段')
    import preferences

    preferences.clear()
    check('没有预设 → 全部可补问字段缺失',
          preferences.main(['missing']) == 1)

    # 只填核心：cities/keywords 不在可补问集里，所以 missing 应报其余全部
    preferences.save(cities='太原', keywords='Python', count=20, mode='custom')
    missing = preferences.missing_fields(preferences.load())
    check('核心字段不算缺失',
          'cities' not in missing and 'keywords' not in missing
          and 'mode' not in missing and 'count' not in missing, missing)
    check('薪资/规模/最低岗位数被报为缺失',
          all(k in missing for k in ('salary', 'scale', 'min_count')), missing)
    check('有缺失 → missing 子命令退出 1',
          preferences.main(['missing']) == 1)

    # 补上全部可补问字段 → 退出 0
    preferences.save(cities='太原', keywords='Python', count=20, mode='custom',
                     match_mode='deep', top_n=10, degree='本科', experience='应届生',
                     salary='5-10K', scale='100-499', job_type='全职', min_count=10)
    check('全部覆盖 → 退出 0', preferences.main(['missing']) == 0)
    check('missing_fields 返回空', preferences.missing_fields(preferences.load()) == [])


# ==================== 3. 白名单：安全边界 ====================

def test_unknown_keys_dropped():
    """预设不能成为绕过 gate:send 的配置开关。

    preferences.json 是用户手改得到的文件。若 load() 原样透传未知键，
    往里塞一个 auto_apply: true 就等于给「发送前确认」加了个 off 开关。
    """
    print('\n[3] 未知键被丢弃（gate:send 不可被预设绕过）')
    import preferences

    write_raw({
        'cities': ['太原'],
        'keywords': ['Python'],
        # 以下全是白名单外的键，尤其是前三个 —— 它们要是能存活就是安全问题
        'auto_apply': True,
        'send_without_confirm': True,
        'jobs': ['https://www.zhipin.com/job_detail/x.html'],
        'greeting': '你好我想投这个岗位',
        'skip_gate_send': True,
        'random_junk': 123,
    })
    prefs = preferences.load()

    for key in ('auto_apply', 'send_without_confirm', 'jobs',
                'greeting', 'skip_gate_send', 'random_junk'):
        check('丢弃 %s' % key, key not in prefs, '仍在: %r' % prefs.get(key))

    check('白名单内的键保留', prefs.get('cities') == ['太原'], prefs.get('cities'))
    check('load 结果只含白名单键',
          set(prefs) <= set(preferences.ALLOWED_KEYS),
          set(prefs) - set(preferences.ALLOWED_KEYS))
    check('save 的 CLI 没有投递相关 flag',
          not any(f in preferences.build_parser().format_help()
                  for f in ('--auto-apply', '--send', '--greeting', '--jobs')))


def test_bad_types_dropped():
    print('\n[4] 类型不对的值当作缺失')
    import preferences

    write_raw({'cities': ['太原'], 'top_n': 'abc', 'count': -5, 'detail': 'yes'})
    prefs = preferences.load()

    check('top_n="abc" 被丢弃', 'top_n' not in prefs, prefs.get('top_n'))
    check('count=-5 被丢弃', 'count' not in prefs, prefs.get('count'))
    check('detail="yes"（非 bool）被丢弃', 'detail' not in prefs, prefs.get('detail'))
    check('城市还在', prefs.get('cities') == ['太原'])


# ==================== 5. crawl-args ====================

def test_crawl_args():
    print('\n[5] crawl-args 拼装')
    import preferences

    preferences.clear()
    preferences.save(cities='太原,西安', keywords='AI应用开发,Python',
                     count=20, degree='本科', match_mode='deep', top_n=10)
    command = preferences.crawl_command(preferences.load())

    check('是完整命令', command.startswith('python scripts/stages/boss_post_interactive.py'),
          command)
    check('带 -m custom', '-m custom' in command, command)
    check('中文关键词被引号包住', '-p "AI应用开发,Python"' in command, command)
    check('多城市被引号包住', '-c "太原,西安" ' in command + ' ', command)
    check('带 -n 20', '-n 20' in command, command)
    check('带 -deg "本科"', '-deg "本科"' in command, command)
    check('默认带 -d（详情决定匹配质量）', ' -d' in command, command)
    check('带 -y（Claude 驱动读不到 stdin）', command.endswith('-y'), command)
    check('不含匹配参数（match_mode/top_n 不是爬取 flag）',
          'deep' not in command and '--top' not in command, command)

    check('缺关键词时 crawl-args 退出 1',
          (preferences.clear(), preferences.save(cities='太原'),
           preferences.main(['crawl-args']))[-1] == 1)


# ==================== 6. 就地更新：默认合并，--replace 才整份替换 ====================

def test_merge_and_replace():
    print('\n[6] 默认合并 / --replace 整份替换，都只留一份')
    import preferences

    preferences.clear()
    preferences.save(cities='太原', keywords='Python', top_n=10)
    preferences.save(cities='西安', keywords='Java')
    prefs = preferences.load()

    check('给出的字段被更新（城市）', prefs['cities'] == ['西安'], prefs.get('cities'))
    check('给出的字段被更新（关键词）', prefs['keywords'] == ['Java'], prefs.get('keywords'))
    # 这一条是 P4 的核心：省略 top_n 不等于要求清掉 top_n。
    check('未给出的字段保留，不被静默清空', prefs.get('top_n') == 10, prefs.get('top_n'))
    check('文件只有一个', len([f for f in os.listdir(_TMP)
                          if f.endswith('.json')]) == 1,
          os.listdir(_TMP))

    # 合并语义下也不能靠省略清字段 —— 要整份重写就 --replace。
    preferences.save(_replace=True, cities='太原', keywords='Python')
    prefs = preferences.load()
    check('--replace 下未给出的字段被清掉', 'top_n' not in prefs, prefs.get('top_n'))

    # CLI 的 --replace 走的是同一条路
    preferences.save(cities='太原', keywords='Python', top_n=10)
    check('CLI save --replace 退出 0',
          preferences.main(['save', '-c', '太原', '-p', 'Python', '--replace']) == 0)
    check('CLI --replace 也清掉未给出的字段',
          'top_n' not in preferences.load(), preferences.load().get('top_n'))

    # partial save 不该顺手改掉 mode（旧代码里 --mode 的 default='custom' 会）
    preferences.save(_replace=True, cities='太原', keywords='Python', mode='list')
    preferences.main(['save', '--top', '20'])
    prefs = preferences.load()
    check('CLI 部分保存不覆盖已存的 mode', prefs.get('mode') == 'list', prefs.get('mode'))
    check('CLI 部分保存写入了 top_n', prefs.get('top_n') == 20, prefs.get('top_n'))
    check('CLI 部分保存保留了城市', prefs.get('cities') == ['太原'], prefs.get('cities'))


# ==================== 7. 损坏降级 ====================

def test_corrupt_degrades():
    """预设坏掉时的正确降级是「回去问用户」，不是把整轮跑崩。"""
    print('\n[7] 损坏的 JSON 不崩，降级成「没有预设」')
    import preferences

    write_raw('{"cities": ["太原",,,}')
    try:
        prefs = preferences.load()
        check('load 不抛异常', True)
        check('返回空 dict', prefs == {}, prefs)
        check('show 退出 1（当作没有预设）', preferences.main(['show']) == 1)
    except Exception as e:
        check('load 不抛异常', False, repr(e))

    write_raw('["not", "a", "dict"]')
    check('顶层是数组时也返回空 dict', preferences.load() == {})


def test_clear():
    print('\n[8] clear')
    import preferences

    preferences.save(cities='太原', keywords='Python')
    check('删掉返回 True', preferences.clear() is True)
    check('文件真的没了', not os.path.exists(preferences.prefs_path()))
    check('再删返回 False', preferences.clear() is False)
    check('clear 子命令退出 0', preferences.main(['clear']) == 0)


def main():
    print('=' * 60)
    print('参数预设 回归测试')
    print('=' * 60)

    setup_tmp_assets()
    try:
        test_roundtrip()
        test_show_exit_codes()
        test_missing_fields()
        test_unknown_keys_dropped()
        test_bad_types_dropped()
        test_crawl_args()
        test_merge_and_replace()
        test_corrupt_degrades()
        test_clear()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)

    print('\n' + '=' * 60)
    if FAILURES:
        print('❌ %d 项失败: %s' % (len(FAILURES), ', '.join(FAILURES)))
        return 1
    print('✅ 全部通过')
    return 0


if __name__ == '__main__':
    for _stream in (sys.stdout, sys.stderr):      # Windows 控制台是 GBK
        _stream.reconfigure(encoding='utf-8', errors='replace')
    sys.exit(main())
