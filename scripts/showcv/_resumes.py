#!/usr/bin/env python3
"""读某个 origin 的简历清单，并把用户给的 `--id/--name` 解析成真实 id。

export/delete 都要先回答同一个问题：这个 origin 里现在有哪些简历、叫什么、id 是什么。
前端的 `/export`、`/delete` 只认 id（见 app 里的 `qD`/`sO` 解析器），而人只记得名字，
所以「名字 → id」这一步必须在本地做完，顺带获得两个前端给不了的好处：

1. 未知的 id/名字能在**导航之前**报错。前端对找不到的 id 是静默忽略的
   （删除页只会在角落写一行「另有 N 个 id 在本机没找到，已忽略」），
   等页面告诉我们就太晚了 —— 那时候另外几份已经删了。
2. 打印出确切的名字清单，让人核对的是简历名而不是一串 id。
"""

from __future__ import annotations

import json

from storage import KEY


def read_state(tab) -> dict:
    """读 zustand persist 落盘的 state；空 origin 返回 {}。

    内容存在却解析不了时**必须报错**：把它当成空的，后面所有对账都是错的。
    """
    raw = tab.local_storage(KEY)
    if not raw:
        return {}

    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        raise SystemExit(f'{KEY} 的内容不是合法 JSON，拒绝在看不懂现状的情况下操作')

    return (value.get('state') or {}) if isinstance(value, dict) else {}


def read_resumes(tab) -> tuple[list[dict], str | None]:
    """返回 ([{'id','name'}, ...], currentResumeId)。

    只取 id 和 name：content 可能有几十 KB，而调用方要的是清单，
    整份读回来只是白白把简历正文塞进 stdout 和内存。
    """
    state = read_state(tab)
    resumes = state.get('resumes') or []
    items = [
        {'id': item.get('id') or '', 'name': item.get('name') or '?'}
        for item in resumes
        if isinstance(item, dict)
    ]
    return items, state.get('currentResumeId')


def resolve(
    resumes: list[dict],
    ids: list[str],
    names: list[str],
) -> tuple[list[dict], list[str]]:
    """把 --id/--name 解析成简历对象，返回 (命中的简历, 找不到的选择器)。

    去重按 id，且保持「清单里的原始顺序」而不是用户给参数的顺序 ——
    打印出来的确认清单要和编辑器侧边栏对得上。

    同名多份时报错而不是猜一个：重名在这个编辑器里是可能的（导入会自动加
    ' (2)' 后缀，但手动改名不会拦），而猜错的代价在删除那边是不可逆的。
    """
    by_id = {item['id']: item for item in resumes if item['id']}
    hit: dict[str, dict] = {}
    missing: list[str] = []

    for wanted in ids:
        item = by_id.get(wanted)
        if item is None:
            missing.append(f'id={wanted}')
        else:
            hit[item['id']] = item

    for wanted in names:
        matched = [item for item in resumes if item['name'] == wanted]
        if not matched:
            missing.append(f'name={wanted!r}')
        elif len(matched) > 1:
            raise SystemExit(
                f'有 {len(matched)} 份简历都叫 {wanted!r}，无法确定指哪一份。\n'
                '改用 --id 指定：' + '、'.join(item['id'] for item in matched)
            )
        else:
            hit[matched[0]['id']] = matched[0]

    ordered = [item for item in resumes if item['id'] in hit]
    return ordered, missing


def format_list(resumes: list[dict], current_id: str | None = None, limit: int = 20) -> str:
    """把简历清单排成人能核对的样子。"""
    lines = []
    for index, item in enumerate(resumes[:limit], 1):
        mark = ' ← 当前' if current_id and item['id'] == current_id else ''
        lines.append(f'  {index}. {item["name"]}  [{item["id"]}]{mark}')
    if len(resumes) > limit:
        lines.append(f'  …… 另外 {len(resumes) - limit} 份')
    return '\n'.join(lines)
