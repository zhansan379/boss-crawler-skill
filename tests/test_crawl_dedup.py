#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""跨关键词去重 + 单页重复率提前退出的回归测试。

覆盖 2026-08-14 改造（复盘：太原 5 关键词 → 117 行 / 53 唯一 link）：
  crawler.process_job_list             写盘时的整轮去重
  crawler.should_skip_remaining_pages  单页重复率判定
  resume_matcher.load_job_data         加载侧兜底去重

不开浏览器，全是纯函数。跑法: python tests/test_crawl_dedup.py
"""

import csv
import io
import os
import shutil
import sys
import tempfile

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
sys.path.insert(0, HERE)

from boss_crawler.config import CSV_FIELDS, DUP_PAGE_MIN_LINKS, ENCODING
from boss_crawler.crawler import process_job_list, should_skip_remaining_pages
from resume_matcher.data_loader import load_job_data

FAILURES = []


def check(label, cond, detail=''):
    if cond:
        print('  ✅ %s' % label)
    else:
        print('  ❌ %s  %s' % (label, detail))
        FAILURES.append(label)


def make_page(ids):
    """造一页 joblist.json 里的 jobList，字段只留 process_job_list 会读的。"""
    return [{'encryptJobId': i, 'jobName': 'Python开发-%s' % i,
             'brandName': '公司%s' % i, 'cityName': '太原',
             'skills': ['Python'], 'welfareList': ['五险一金']} for i in ids]


def link_of(job_id):
    return 'https://www.zhipin.com/job_detail/%s.html' % job_id


def run_page(job_list, existing_links, run_seen=None):
    """把一页数据喂给 process_job_list，返回 (written, skipped, run_dups, 写出的行)。"""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_FIELDS)
    written, skipped, run_dups = process_job_list(
        job_list, 'unused.csv', existing_links, writer, run_seen
    )
    rows = [r for r in buf.getvalue().splitlines() if r.strip()]
    return written, skipped, run_dups, rows


# ==================== 1. 跨关键词去重 ====================

def test_cross_keyword_dedup():
    print('\n[1] 跨关键词去重（run_seen 共享）')

    run_seen = set()

    # 关键词 A：全新，15 条全写
    w, s, d, rows = run_page(make_page(range(1, 16)), set(), run_seen)
    check('关键词 A 写入 15 条', w == 15, 'written=%d' % w)
    check('关键词 A 无重复', (s, d) == (0, 0), 'skipped=%d run_dups=%d' % (s, d))
    check('run_seen 收下 15 个 link', len(run_seen) == 15, len(run_seen))

    # 关键词 B（同义词）：命中 A 的 12 条 + 3 条新的。
    # existing_links 是 B 自己文件的（空），单靠它拦不住 —— 这正是改造前的漏洞。
    w, s, d, rows = run_page(make_page(list(range(1, 13)) + [90, 91, 92]), set(), run_seen)
    check('关键词 B 只写入 3 条新岗位', w == 3, 'written=%d' % w)
    check('关键词 B 拦下 12 条跨关键词重复', d == 12, 'run_dups=%d' % d)
    check('run_dups 计入 skipped', s == 12, 'skipped=%d' % s)
    check('CSV 只多出 3 行', len(rows) == 3, len(rows))
    check('run_seen 累加到 18', len(run_seen) == 18, len(run_seen))


def test_backward_compatible_without_run_seen():
    print('\n[2] 不传 run_seen 时行为不变（公开 API 兼容）')

    existing = {link_of(1), link_of(2)}
    w, s, d, rows = run_page(make_page(range(1, 6)), existing)
    check('按 existing_links 写入 3 条', w == 3, 'written=%d' % w)
    check('跳过 2 条', s == 2, 'skipped=%d' % s)
    check('run_dups 恒为 0', d == 0, 'run_dups=%d' % d)


# ==================== 3. 单页重复率判定 ====================

def test_skip_decision():
    print('\n[3] 单页重复率提前退出')

    check('15 条里 14 条本轮重复 → 跳过', should_skip_remaining_pages(15, 14))
    check('15 条里 12 条本轮重复（80%）→ 跳过', should_skip_remaining_pages(15, 12))
    check('15 条里 11 条本轮重复（73%）→ 继续', not should_skip_remaining_pages(15, 11))
    check('全新的一页 → 继续', not should_skip_remaining_pages(15, 0))

    # 尾页保护：2 条且全重复，比例 100% 但样本太小，不该打出跳过日志
    check('尾页只有 2 条全重复 → 不判定',
          not should_skip_remaining_pages(2, 2),
          'DUP_PAGE_MIN_LINKS=%d' % DUP_PAGE_MIN_LINKS)
    check('空页 → 不判定（不能除零）', not should_skip_remaining_pages(0, 0))


def test_resume_deeper_crawl_not_interrupted():
    """回归锁：判据必须是 run_dups，不能是 skipped。

    场景：某关键词的 CSV 已有 200 行，今天想从 -n 20 加深到 -n 100。
    第 1 页 100% 命中旧文件（skipped 满格），但本轮还没有别的关键词跑过，
    run_dups 是 0 —— 必须继续翻页，否则永远拿不到后面的新岗位。
    """
    print('\n[4] 续爬更深的页不被打断')

    existing = {link_of(i) for i in range(1, 16)}  # 旧文件已有这 15 条
    run_seen = set()
    w, s, d, rows = run_page(make_page(range(1, 16)), existing, run_seen)

    check('第 1 页全是旧数据，一条没写', w == 0, 'written=%d' % w)
    check('skipped 满格 15', s == 15, 'skipped=%d' % s)
    check('run_dups 为 0（本轮没有别的关键词采过）', d == 0, 'run_dups=%d' % d)
    check('→ 不触发跳过，能继续翻到第 2 页',
          not should_skip_remaining_pages(15, d),
          '若判据误用 skipped，这里会 break')


# ==================== 5. 加载侧兜底去重 ====================

def write_csv(path, rows):
    with open(path, 'w', encoding=ENCODING, newline='') as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction='ignore')
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, '') for k in CSV_FIELDS})


def test_load_job_data_dedup():
    print('\n[5] load_job_data 去重并保留有 JD 的那条')

    tmp = tempfile.mkdtemp(prefix='dedup_test_')
    try:
        a = os.path.join(tmp, 'A_太原.csv')
        b = os.path.join(tmp, 'B_太原.csv')

        # A 文件：link 1 有 JD，link 2 有 JD
        write_csv(a, [
            {'link': link_of(1), '职位': '岗位1', '岗位要求和职责': '负责 RAG 系统',
             '公司信息': '一家公司'},
            {'link': link_of(2), '职位': '岗位2', '岗位要求和职责': '负责后端'},
        ])
        # B 文件：link 1 是影子行（JD 空，详情去重没回填它）+ 一条新岗位
        write_csv(b, [
            {'link': link_of(1), '职位': '岗位1', '岗位要求和职责': '', '公司信息': ''},
            {'link': link_of(3), '职位': '岗位3', '岗位要求和职责': '负责前端'},
        ])

        jobs = load_job_data([a, b])
        check('4 行去重成 3 条', len(jobs) == 3, len(jobs))

        by_link = {j['link']: j for j in jobs}
        check('link 1 只剩一条', len(jobs) == len({j['link'] for j in jobs}))
        check('link 1 保留的是有 JD 的那条',
              by_link[link_of(1)]['岗位要求和职责'] == '负责 RAG 系统',
              repr(by_link[link_of(1)]['岗位要求和职责']))
        check('link 1 的公司信息也跟着保留',
              by_link[link_of(1)]['公司信息'] == '一家公司')
        check('link 3 在结果里', link_of(3) in by_link)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_load_job_data_shadow_row_first():
    """影子行排在前面时也要挑到有 JD 的那条（不能只保留第一条）。"""
    print('\n[6] 影子行排在前面时的取舍')

    tmp = tempfile.mkdtemp(prefix='dedup_test_')
    try:
        path = os.path.join(tmp, 'C_太原.csv')
        write_csv(path, [
            {'link': link_of(7), '职位': '岗位7', '岗位要求和职责': ''},
            {'link': link_of(7), '职位': '岗位7', '岗位要求和职责': '真正的 JD'},
        ])
        jobs = load_job_data([path])
        check('2 行去重成 1 条', len(jobs) == 1, len(jobs))
        check('留下的是后出现的那条有 JD 的',
              jobs[0]['岗位要求和职责'] == '真正的 JD',
              repr(jobs[0]['岗位要求和职责']))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    print('=' * 60)
    print('跨关键词去重 + 提前退出 回归测试')
    print('=' * 60)

    test_cross_keyword_dedup()
    test_backward_compatible_without_run_seen()
    test_skip_decision()
    test_resume_deeper_crawl_not_interrupted()
    test_load_job_data_dedup()
    test_load_job_data_shadow_row_first()

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
