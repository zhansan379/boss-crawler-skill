# -*- coding: utf-8 -*-
"""resume_optimize 章节保真回归测试（离线，不发请求）。

背景：2026-08-16 复盘发现优化简历丢了两块对无经验应届生最值钱的背书——
「荣誉奖项」（6 竞赛奖 + 1 期刊）和「开源经历」（boss-crawler-skill）。两个根因：

  1. 荣誉奖项：白名单里有、原简历也有，但模型按岗位相关度排序时主动舍弃了 → 质量损失
  2. 开源经历：白名单写成硬编码 7 个章节，不含「开源经历」，并说「不要新造章节」
     → 被设计性丢弃

本测试盯住两条防线：
  · 提示词里**必须**列着 `## 开源经历`，且明文要求「原简历实有的章节一律保留」——
    相关度只决定顺序，不决定去留。
  · 无 resume_text.txt 时的兜底基底 build_generic_resume_text 也要把 profile 里
    的 awards / publications 带进 `## 荣誉奖项`，避免同样的丢在源头重演。

跑法：python tests/test_resume_optimize_structure.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "stages"))

for _stream in (sys.stdout, sys.stderr):
    _stream.reconfigure(encoding='utf-8', errors='replace')

from resume_matcher.prompts import get_optimize_prompt      # noqa: E402
import gen_materials as GM                                    # noqa: E402


def main():
    failures = []

    def check(label, cond, detail=''):
        if cond:
            print('  ✅ %s' % label)
        else:
            failures.append(label)
            print('  ❌ %s' % label + ('   ← %s' % detail if detail else ''))

    print('\n=== 提示词：开源经历被白名单收录 ===')
    prompt = get_optimize_prompt('原简历', 'A公司', '后端开发', '15-25K',
                                 '熟悉 Python、分布式', 82, '无', '突出项目')
    check('章节白名单包含 `## 开源经历`',
          '## 开源经历' in prompt)
    check('章节白名单包含 `## 荣誉奖项`（未回归丢失）',
          '## 荣誉奖项' in prompt)

    print('\n=== 提示词：原简历实有章节一律保留，相关度只定顺序 ===')
    check('明文要求「原简历实有的章节一律保留」',
          '一律保留' in prompt and '不得删除' in prompt)
    check('点名「荣誉奖项」「开源经历」为背书须保留',
          '荣誉奖项' in prompt and '开源经历' in prompt and '背书' in prompt)
    check('「相关度只决定先后顺序，不决定去留」',
          '相关度只决定先后顺序' in prompt and '去留' in prompt)
    check('开源条目按经历类章节用 `###` 条目格式',
          '开源经历' in prompt and '###' in prompt)
    check('仍未放开「不要新造章节」（防编造）',
          '不要新造章节' in prompt)

    print('\n=== 兜底基底：awards / publications 进荣誉奖项 ===')
    profile = {
        'education': {'school': '某大学', 'degree': '本科', 'major': '软件工程',
                      'graduation_year': '2026'},
        'skills': {'programming': ['Python']},
        'awards': [{'name': '国家二等奖', 'level': '国家级', 'year': '2024'},
                   {'name': '省级一等奖', 'year': '2025'}],
        # publications 目前是 dict 结构，兜底按字段拼接；list[str] 也兼容
        'publications': [{'title': '一篇期刊论文', 'venue': '某期刊',
                          'year': '2025'}],
    }
    text = GM.build_generic_resume_text(profile)
    check('含 `## 荣誉奖项` 章节', '## 荣誉奖项' in text)
    check('带出两个奖项', '国家二等奖' in text and '省级一等奖' in text)
    check('带出期刊', '期刊论文' in text)

    print('\n=== 兜底基底：没有奖项就不造章节 ===')
    bare = GM.build_generic_resume_text(
        {'education': {'school': '某大学'}, 'skills': {}})
    check('无奖项时不出现 `## 荣誉奖项`（不编造）',
          '## 荣誉奖项' not in bare)

    print('\n' + '=' * 60)
    if failures:
        print('❌ %d 项未通过：%s' % (len(failures), '、'.join(failures)))
        return 1
    print('✅ 全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())