# -*- coding: utf-8 -*-
"""llm/ 模块的测试：JSON 提取、重试边界、并发隔离、配置优先级、key 不外泄。

一个真实请求都不发 —— requests 被整体换成脚本化的假模块，所以这套测试可以离线跑，
也不会因为某天限流而红。

为什么测这几项：
  · JSON 提取：模型输出格式是**外部输入**，三种形态（裸 JSON / ``` 围栏 / 前后带废话）
    在实测里都出现过，少兜一种就是一整轮白跑。
  · 重试边界：401（key 错）重试三遍毫无意义还让人多等三轮退避；429 不重试则整批报废。
    「哪些错该重试」的判断错了，两个方向都很贵。
  · 并发隔离：一个岗位分析失败不该带崩另外 14 个，且结果必须**按输入顺序**回来 ——
    下游全靠序号 i 对齐招呼语和简历，顺序错位就是把 A 的招呼语发给 B。
  · 配置优先级：「我明明配了却没生效」是这类模块最常见的困惑，五个阶段名也在这里钉住。
  · key 不外泄：报错信息和配置摘要会被贴进 issue、日志、终端截图。

跑法：python tests/test_llm_client.py
"""

import os
import sys
import json
import shutil
import tempfile
import threading

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

for _stream in (sys.stdout, sys.stderr):
    _stream.reconfigure(encoding='utf-8', errors='replace')

import requests as _real_requests

from llm import client as C
from llm import config as CF

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "scripts", "utils"))
import llm_check

SECRET = 'sk-testsecret-abcdefghijklmnop-9999'


# ==================== 假 requests ====================

class _Resp:
    def __init__(self, status=200, body=None, text='', headers=None):
        self.status_code = status
        self._body = body
        self.text = text or (json.dumps(body, ensure_ascii=False) if body else '')
        self.headers = headers or {}

    def json(self):
        if self._body is None:
            raise ValueError('not json')
        return self._body


class _FakeRequests:
    """脚本化的 requests 替身。script 里放 _Resp 或要抛的异常，按序取用；
    取完之后重复最后一个（这样「一直超时」不必写 4 遍）。"""

    exceptions = _real_requests.exceptions

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def post(self, url, headers=None, json=None, timeout=None):   # noqa: A002
        self.calls.append({'url': url, 'headers': headers or {},
                           'payload': json or {}, 'timeout': timeout})
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, BaseException):
            raise item
        return item


def ok_body(content, tokens=(10, 5, 15)):
    return {'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': tokens[0], 'completion_tokens': tokens[1],
                      'total_tokens': tokens[2]}}


def anthropic_body(content, tokens=(10, 5)):
    """Anthropic Messages API 的响应形态：正文在 content 块数组里，用量是 input/output。"""
    return {'content': [{'type': 'text', 'text': content}],
            'usage': {'input_tokens': tokens[0], 'output_tokens': tokens[1]}}


def cfg(**over):
    """测试用配置：不读真实配置文件、不读真实环境变量。"""
    base = dict(base_url='https://fake.local/v1', api_key=SECRET, model='m-test',
                max_retries=2, timeout=5, temperature=0.3, json_mode=True)
    base.update(over)
    return CF.LLMConfig(**base)


def install(script):
    fake = _FakeRequests(script)
    C.requests = fake
    return fake


def main():
    failures = []

    def check(label, cond, detail=''):
        print('%s %s%s' % ('  ✅' if cond else '  ❌', label,
                           '' if cond else '  ← ' + str(detail)[:300]))
        if not cond:
            failures.append(label)

    real_requests_module = C.requests
    C._sleep_for = lambda *a, **k: 0.0        # 别在测试里真退避
    tmp = tempfile.mkdtemp(prefix='llm_test_')

    try:
        # ================================================================
        print('=== 1. extract_json：三种形态都得吃下 ===')
        payload = {'keywords': ['Python', '后端'], 'cities': ['杭州']}

        check('裸 JSON', C.extract_json(json.dumps(payload)) == payload)
        check('```json 围栏',
              C.extract_json('```json\n%s\n```' % json.dumps(payload)) == payload)
        check('无语言标记的围栏',
              C.extract_json('```\n%s\n```' % json.dumps(payload)) == payload)
        check('前后带废话',
              C.extract_json('好的，以下是分析结果：\n%s\n希望对你有帮助！'
                             % json.dumps(payload)) == payload)
        check('顶层是数组', C.extract_json('[1, 2, 3]') == [1, 2, 3])

        # JD 正文里带花括号：单纯数括号会在这里切错
        tricky = {'jd': '要求熟悉 dict{key: value} 与 f"{name}" 写法', 'rank': 3}
        check('字符串里含 {} 不影响括号平衡',
              C.extract_json('分析如下：' + json.dumps(tricky, ensure_ascii=False))
              == tricky, )
        check('转义引号不影响',
              C.extract_json(r'噪声 {"text": "他说 \"好\"", "n": 1} 结束')
              == {'text': '他说 "好"', 'n': 1})

        for bad, why in (('完全没有 JSON 的一段话', '没有括号'),
                         ('{"unclosed": ', '括号不平衡'),
                         ('', '空字符串')):
            try:
                C.extract_json(bad)
                check('解析不出来要抛 LLMError（%s）' % why, False, '没抛')
            except C.LLMError:
                check('解析不出来要抛 LLMError（%s）' % why, True)

        try:
            C.extract_json(None)
            check('None 也抛 LLMError', False, '没抛')
        except C.LLMError:
            check('None 也抛 LLMError', True)

        # ================================================================
        print('\n=== 2. strip_fence：招呼语的前 15 字不能被 ``` 占掉 ===')
        greeting = '您好，我是 26 届本科生，做过日均百万请求的服务重构。'
        check('剥掉围栏', C.strip_fence('```\n%s\n```' % greeting) == greeting)
        check('剥掉带语言标记的围栏',
              C.strip_fence('```markdown\n%s\n```' % greeting) == greeting)
        check('普通文本原样返回（只去首尾空白）',
              C.strip_fence('  %s  ' % greeting) == greeting)
        check('正文里的 ``` 不误伤',
              C.strip_fence('代码块写法是 ``` 三个反引号') == '代码块写法是 ``` 三个反引号')

        # ================================================================
        print('\n=== 3. 重试边界：429 重试、401 立刻失败 ===')
        fake = install([_Resp(429, text='rate limited', headers={'Retry-After': '0'}),
                        _Resp(429, text='rate limited'),
                        _Resp(200, ok_body('第三次成功'))])
        run_dir = os.path.join(tmp, 'retry_ok')
        out = C.chat('问', cfg=cfg(), run_dir=run_dir, stage='parse')
        check('429 重试后拿到结果', out == '第三次成功', out)
        check('一共发了 3 次请求', len(fake.calls) == 3, len(fake.calls))
        summary = C.usage_summary(run_dir)
        check('用量记了 1 次调用 2 次重试',
              summary['calls'] == 1 and summary['retries'] == 2, summary)
        check('token 数被记下', summary['total_tokens'] == 15, summary)

        fake = install([_Resp(401, text='invalid api key')])
        try:
            C.chat('问', cfg=cfg(), run_dir=os.path.join(tmp, 'key_bad'), stage='parse')
            check('401 抛 LLMError', False, '没抛')
        except C.LLMError as exc:
            check('401 抛 LLMError', True)
            # 这条是重点：key 错了重试三遍只是把同样的错再犯三遍，还让人多等三轮退避
            check('401 只发 1 次请求（不重试）', len(fake.calls) == 1, len(fake.calls))
            check('错误信息里有 endpoint 和 model（够定位）',
                  'fake.local' in str(exc) and 'm-test' in str(exc), str(exc))
            check('错误信息里没有 api_key 明文', SECRET not in str(exc), str(exc))

        fake = install([_real_requests.exceptions.Timeout('timed out')])
        try:
            C.chat('问', cfg=cfg(max_retries=2), run_dir=os.path.join(tmp, 'to'),
                   stage='parse')
            check('一直超时最终抛 LLMError', False, '没抛')
        except C.LLMError as exc:
            check('一直超时最终抛 LLMError', True)
            check('超时被重试满（1 + 2 次）', len(fake.calls) == 3, len(fake.calls))
            check('失败也记账', C.usage_summary(os.path.join(tmp, 'to'))['failures'] == 1)

        fake = install([_Resp(200, {'choices': [{'message': {'content': '  '},
                                                'finish_reason': 'length'}]}),
                        _Resp(200, ok_body('补上了'))])
        out = C.chat('问', cfg=cfg(), stage='parse')
        check('空正文当作可重试', out == '补上了' and len(fake.calls) == 2,
              '%r / %d 次' % (out, len(fake.calls)))

        fake = install([_Resp(200, None, text='<html>502 bad gateway</html>'),
                        _Resp(200, ok_body('好了'))])
        out = C.chat('问', cfg=cfg(), stage='parse')
        check('响应不是 JSON 时重试', out == '好了' and len(fake.calls) == 2,
              '%r / %d 次' % (out, len(fake.calls)))

        # ================================================================
        print('\n=== 4. 请求体：json_mode 只在要 JSON 时加 ===')
        fake = install([_Resp(200, ok_body('{"a":1}'))])
        C.chat_json('问', cfg=cfg(), stage='infer')
        check('chat_json 带 response_format',
              fake.calls[0]['payload'].get('response_format') == {'type': 'json_object'},
              fake.calls[0]['payload'])

        fake = install([_Resp(200, ok_body('纯文本'))])
        C.chat('问', cfg=cfg(), stage='greeting')
        check('chat 不带 response_format',
              'response_format' not in fake.calls[0]['payload'],
              fake.calls[0]['payload'])

        fake = install([_Resp(200, ok_body('{"a":1}'))])
        C.chat_json('问', cfg=cfg(json_mode=False), stage='infer')
        check('json_mode=False 时不带（有些兼容端点不认这个字段）',
              'response_format' not in fake.calls[0]['payload'],
              fake.calls[0]['payload'])

        fake = install([_Resp(200, ok_body('答'))])
        C.chat('问', system='你是助手', cfg=cfg(), stage='parse')
        msgs = fake.calls[0]['payload']['messages']
        check('system 在前、user 在后',
              [m['role'] for m in msgs] == ['system', 'user'], msgs)
        check('Authorization 是 Bearer <key>',
              fake.calls[0]['headers'].get('Authorization') == 'Bearer %s' % SECRET)

        # ================================================================
        print('\n=== 5. chat_json 解析失败：带着原输出再问一次 ===')
        garbage = '我觉得这个岗位不错，但我不打算输出 JSON。'
        fake = install([_Resp(200, ok_body(garbage)),
                        _Resp(200, ok_body('{"rank": 1, "score": 88}'))])
        result = C.chat_json('问', cfg=cfg(), stage='deep')
        check('第二次成功就返回', result == {'rank': 1, 'score': 88}, result)
        check('只追问一次（共 2 次请求）', len(fake.calls) == 2, len(fake.calls))
        second = fake.calls[1]['payload']['messages']
        check('追问时把上次原输出发回去了',
              any(m.get('role') == 'assistant' and garbage in m.get('content', '')
                  for m in second), second)

        fake = install([_Resp(200, ok_body(garbage))])
        try:
            C.chat_json('问', cfg=cfg(), stage='deep')
            check('两次都失败要抛', False, '没抛')
        except C.LLMError as exc:
            check('两次都失败要抛', True)
            check('报错说清是两次都不行', '两次' in str(exc), str(exc))

        # ================================================================
        print('\n=== 6. map_concurrent：一条失败不带崩全批，顺序按输入 ===')
        items = list(range(12))

        def work(n):
            if n in (3, 7):
                raise ValueError('第 %d 条故意失败' % n)
            return n * 10

        outcomes = C.map_concurrent(items, work, workers=4, quiet=True)
        check('结果条数 = 输入条数', len(outcomes) == len(items), len(outcomes))
        check('index 严格按输入顺序',
              [o.index for o in outcomes] == list(range(12)),
              [o.index for o in outcomes])
        check('成功的 10 条都在',
              [o.value for o in outcomes if o.ok] == [n * 10 for n in items
                                                      if n not in (3, 7)])
        check('失败的 2 条被标记而不是抛出',
              [o.index for o in outcomes if not o.ok] == [3, 7])
        check('失败项带异常类型和消息',
              all('ValueError' in o.error and '故意失败' in o.error
                  for o in outcomes if not o.ok))
        check('item 原样带回（下游要靠它对齐）',
              all(o.item == o.index for o in outcomes))
        check('空输入返回空列表', C.map_concurrent([], work, quiet=True) == [])

        # 记账文件跨线程追加：撕行了就解析不出 8 条
        conc_dir = os.path.join(tmp, 'concurrent')
        fake = install([_Resp(200, ok_body('并发'))])

        def one(i):
            return C.chat('问 %d' % i, cfg=cfg(), run_dir=conc_dir, stage='deep')

        C.map_concurrent(list(range(8)), one, workers=8, quiet=True)
        # llm_usage.jsonl 落在 run_dir/intermediate/ 下（四桶重构后），别用旧平铺路径
        with open(os.path.join(conc_dir, 'intermediate', 'llm_usage.jsonl'),
                  encoding='utf-8') as f:
            lines = [l for l in f.read().splitlines() if l.strip()]
        check('8 次并发写出 8 行', len(lines) == 8, len(lines))
        parsed = 0
        for line in lines:
            try:
                json.loads(line)
                parsed += 1
            except ValueError:
                pass
        check('8 行都是完整 JSON（没撕行）', parsed == 8, parsed)
        check('usage_summary 汇总得上',
              C.usage_summary(conc_dir)['calls'] == 8, C.usage_summary(conc_dir))
        check('format_usage 里有阶段名', 'deep' in C.format_usage(conc_dir),
              C.format_usage(conc_dir))
        check('format_usage 不含 api_key', SECRET not in C.format_usage(conc_dir))
        check('没有记录时不报错',
              C.format_usage(os.path.join(tmp, '不存在')).startswith('（'))

        # ================================================================
        print('\n=== 7. 配置优先级：默认 → 文件 → stages → 环境 → 命令行 ===')
        conf_path = os.path.join(tmp, 'llm_config.json')
        json.dump({
            'base_url': 'https://file.example/v1',
            'api_key': 'file-key',
            'model': 'file-model',
            'concurrency': 6,
            'stages': {
                'deep': {'model': 'stage-deep-model'},
                'parse': {'model': 'stage-parse-model', 'temperature': 0.1},
            },
            '未知的键': 1,
        }, open(conf_path, 'w', encoding='utf-8'), ensure_ascii=False)

        warns = []
        c1 = CF.resolve(config_path=conf_path, env={}, warnings_out=warns)
        check('文件层生效', c1.model == 'file-model' and c1.api_key == 'file-key', c1)
        check('来源标成 file', c1.source['model'] == 'file', c1.source)
        check('未知键告警而不是静默透传',
              any('未知键' in w for w in warns), warns)

        c2 = CF.resolve(stage='deep', config_path=conf_path, env={})
        check('stages.deep 盖住顶层', c2.model == 'stage-deep-model', c2.model)
        check('未被 stage 覆盖的字段保留文件层', c2.concurrency == 6, c2.concurrency)
        check('来源标成 file:stages.deep',
              c2.source['model'] == 'file:stages.deep', c2.source)

        c3 = CF.resolve(stage='deep', config_path=conf_path,
                        env={'LLM_MODEL': 'env-model'})
        check('环境变量盖住 stages', c3.model == 'env-model', c3.model)
        check('来源标出具体变量名',
              c3.source['model'] == 'env:LLM_MODEL', c3.source)

        c4 = CF.resolve(stage='deep', config_path=conf_path,
                        env={'LLM_MODEL': 'env-model'}, model='cli-model')
        check('命令行盖住环境变量', c4.model == 'cli-model', c4.model)
        check('来源标成 cli', c4.source['model'] == 'cli', c4.source)
        check('None 的覆盖等于没传',
              CF.resolve(config_path=conf_path, env={}, model=None).model == 'file-model')

        c5 = CF.resolve(config_path=conf_path,
                        env={'OPENAI_API_KEY': 'openai-key',
                             'OPENAI_BASE_URL': 'https://openai.example/v1'})
        check('认 OPENAI_* 别名', c5.api_key == 'openai-key', c5.api_key)
        c6 = CF.resolve(config_path=conf_path,
                        env={'OPENAI_API_KEY': 'openai-key', 'LLM_API_KEY': 'llm-key'})
        check('LLM_* 优先于 OPENAI_*', c6.api_key == 'llm-key', c6.api_key)

        check('空环境变量视为没设',
              CF.resolve(config_path=conf_path, env={'LLM_MODEL': '   '}).model
              == 'file-model')
        warns = []
        c7 = CF.resolve(config_path=conf_path, env={'LLM_TIMEOUT': '不是数字'},
                        warnings_out=warns)
        check('环境变量类型不对：告警 + 回落', c7.timeout == 120 and warns, (c7.timeout, warns))

        # 五个阶段名是写死的契约：拼错的阶段名不会报错，只会静默不生效 ——
        # 所以每个调用点的 stage= 都必须是这五个之一。
        for stage in ('parse', 'infer', 'deep', 'greeting', 'resume'):
            CF.resolve(stage=stage, config_path=conf_path, env={})
        c8 = CF.resolve(stage='params', config_path=conf_path, env={})
        check('拼错的阶段名静默不生效（所以只能用那五个）',
              c8.model == 'file-model', c8.model)

        check('concurrency < 1 被抬到 1',
              CF.resolve(config_path=conf_path, env={}, concurrency=0).concurrency == 1)
        check('max_retries < 0 被抬到 0',
              CF.resolve(config_path=conf_path, env={}, max_retries=-3).max_retries == 0)

        bad_path = os.path.join(tmp, 'broken.json')
        with open(bad_path, 'w', encoding='utf-8') as f:
            f.write('{ 这不是 JSON')
        try:
            CF.resolve(config_path=bad_path, env={})
            check('配置文件语法错要抛 ConfigError', False, '没抛')
        except CF.ConfigError as exc:
            check('配置文件语法错要抛 ConfigError', '语法错误' in str(exc), str(exc))

        check('配置文件不存在 = 用默认',
              CF.resolve(config_path=os.path.join(tmp, '没有这个文件'), env={}).model
              == 'gpt-4o-mini')

        # ================================================================
        print('\n=== 8. endpoint 拼装：不能无脑追加 /v1 ===')
        check('裸域名补 /v1',
              CF.chat_endpoint('https://api.deepseek.com')
              == 'https://api.deepseek.com/v1/chat/completions')
        check('已有 /v1 不重复补',
              CF.chat_endpoint('https://x.com/v1') == 'https://x.com/v1/chat/completions')
        check('火山方舟 /api/v3 原样保留',
              CF.chat_endpoint('https://ark.cn-beijing.volces.com/api/v3')
              == 'https://ark.cn-beijing.volces.com/api/v3/chat/completions')
        check('末尾斜杠不影响',
              CF.chat_endpoint('https://x.com/v1/') == 'https://x.com/v1/chat/completions')
        check('已经是完整 endpoint 就不再追加',
              CF.chat_endpoint('https://x.com/v1/chat/completions')
              == 'https://x.com/v1/chat/completions')
        try:
            CF.chat_endpoint('')
            check('base_url 空要抛 ConfigError', False, '没抛')
        except CF.ConfigError:
            check('base_url 空要抛 ConfigError', True)

        # ================================================================
        print('\n=== 8.5 Anthropic Messages API（Claude Code 的协议） ===')
        # 请求体：x-api-key + anthropic-version 头、system 提顶层、content 块数组、
        # max_tokens 强制、没有 response_format
        fake = install([_Resp(200, anthropic_body('答'))])
        C.chat('问', system='你是助手', cfg=cfg(protocol='anthropic'), stage='parse')
        call = fake.calls[0]
        check('anthropic 用 x-api-key 头', call['headers'].get('x-api-key') == SECRET,
              call['headers'])
        check('anthropic 带 anthropic-version',
              call['headers'].get('anthropic-version') == '2023-06-01', call['headers'])
        check('anthropic 不用 Bearer Authorization', 'Authorization' not in call['headers'],
              call['headers'])
        payload = call['payload']
        check('system 提到顶层', payload.get('system') == '你是助手', payload)
        check('messages 里只剩 user', [m['role'] for m in payload['messages']] == ['user'],
              payload)
        check('content 是 {type:text} 块数组',
              payload['messages'][0]['content'] == [{'type': 'text', 'text': '问'}],
              payload['messages'][0]['content'])
        check('anthropic 强制带 max_tokens', isinstance(payload.get('max_tokens'), int),
              payload)
        check('anthropic 不加 response_format', 'response_format' not in payload, payload)

        # 响应解析：content[] 里的多个 text 块拼接；用量 input/output 归一
        multi = {'content': [{'type': 'text', 'text': '{"a":'},
                             {'type': 'text', 'text': '1}'}],
                 'usage': {'input_tokens': 10, 'output_tokens': 5}}
        fake = install([_Resp(200, multi)])
        C.chat_json('{"a":', cfg=cfg(protocol='anthropic'), stage='parse')
        check('anthropic 只发一次请求（各块拼接后能解析）',
              len(fake.calls) == 1, len(fake.calls))

        anth_dir = os.path.join(tmp, 'anthropic')
        fake = install([_Resp(200, anthropic_body('{"ok": true}', (10, 5)))])
        result = C.chat_json('问', cfg=cfg(protocol='anthropic'),
                             run_dir=anth_dir, stage='parse')
        check('anthropic 正文解析为 JSON', result == {'ok': True}, result)
        s = C.usage_summary(anth_dir)
        check('anthropic 用量 input/output→prompt/completion',
              s['prompt_tokens'] == 10 and s['completion_tokens'] == 5
              and s['total_tokens'] == 15, s)

        # anthropic 空正文也当可重试
        fake = install([_Resp(200, {'content': [{'type': 'text', 'text': '  '}],
                                    'stop_reason': 'max_tokens'}),
                        _Resp(200, anthropic_body('补上了'))])
        out = C.chat('问', cfg=cfg(protocol='anthropic'), stage='parse')
        check('anthropic 空正文当作可重试',
              out == '补上了' and len(fake.calls) == 2, '%r / %d 次' % (out, len(fake.calls)))

        # endpoint 拼装：anthropic 走 /v1/messages，openai 不受影响
        check('anthropic 裸域名',
              CF.chat_endpoint('https://api.anthropic.com', 'anthropic')
              == 'https://api.anthropic.com/v1/messages')
        check('anthropic 已有 /v1',
              CF.chat_endpoint('https://x.com/v1', 'anthropic')
              == 'https://x.com/v1/messages')
        check('anthropic 已是完整 /messages 不再追加',
              CF.chat_endpoint('https://x.com/v1/messages', 'anthropic')
              == 'https://x.com/v1/messages')
        check('默认协议仍走 chat/completions',
              CF.chat_endpoint('https://x.com/v1')
              == 'https://x.com/v1/chat/completions')

        # 协议自动识别 + 显式覆盖 + ANTHROPIC_* 环境变量
        empty_cfg = os.path.join(tmp, '无配置文件.json')
        ca = CF.resolve(config_path=empty_cfg, env={}, base_url='https://api.anthropic.com')
        check('host 含 anthropic 自动识别协议', ca.protocol == 'anthropic', ca.protocol)
        check('自动识别来源标 auto', ca.source['protocol'] == 'auto', ca.source)
        check('自动识别的 endpoint 是 /v1/messages',
              ca.endpoint() == 'https://api.anthropic.com/v1/messages', ca.endpoint())
        check('普通端点默认 openai',
              CF.resolve(config_path=empty_cfg, env={},
                         base_url='https://api.deepseek.com/v1').protocol == 'openai')
        cex = CF.resolve(config_path=empty_cfg, env={},
                         base_url='https://api.deepseek.com/v1', protocol='anthropic')
        check('显式 protocol 覆盖自动判断', cex.protocol == 'anthropic', cex.protocol)
        check('显式来源标 cli', cex.source['protocol'] == 'cli', cex.source)
        canth = CF.resolve(config_path=empty_cfg,
                           env={'ANTHROPIC_BASE_URL': 'https://api.anthropic.com',
                                'ANTHROPIC_AUTH_TOKEN': 'anth-key',
                                'ANTHROPIC_MODEL': 'claude-sonnet-5'})
        check('认 ANTHROPIC_* 别名',
              canth.api_key == 'anth-key' and canth.model == 'claude-sonnet-5',
              (canth.api_key, canth.model))
        check('ANTHROPIC_BASE_URL 自动切协议', canth.protocol == 'anthropic', canth.protocol)
        check('describe 显示协议', 'anthropic' in CF.describe(canth), CF.describe(canth))

        # 非法协议：告警并回落到自动判断，而不是抛错
        warns = []
        cinv = CF.resolve(config_path=empty_cfg, env={},
                          base_url='https://api.deepseek.com/v1', protocol='grpc',
                          warnings_out=warns)
        check('非法协议告警 + 回落 openai',
              cinv.protocol == 'openai' and any('不合法' in w for w in warns),
              (cinv.protocol, warns))

        # ================================================================
        print('\n=== 8.6 404 协议回退：llm_check._text_probe ===')
        # LLMError 带 status_code，才有结构化判断「404 端点/协议不对」的依据
        fake = install([_Resp(404, text='not found')])
        try:
            C.chat('问', cfg=cfg(), stage='parse')
            check('404 抛 LLMError', False, '没抛')
        except C.LLMError as exc:
            check('404 抛 LLMError', True)
            check('404 记下 status_code=404', exc.status_code == 404, exc.status_code)

        fake = install([_Resp(401, text='bad key')])
        try:
            C.chat('问', cfg=cfg(), stage='parse')
        except C.LLMError as exc:
            check('401 记下 status_code=401', exc.status_code == 401, exc.status_code)

        fake = install([_real_requests.exceptions.ConnectionError('refused')])
        try:
            C.chat('问', cfg=cfg(max_retries=0), stage='parse')
        except C.LLMError as exc:
            check('网络错误 status_code 是 None', exc.status_code is None,
                  exc.status_code)

        # 回退：openai 端点打 404 → 自动换 anthropic 端点 → 成功
        fake = install([_Resp(404, text='not found'),
                        _Resp(200, anthropic_body('可用'))])
        probe_cfg = cfg(base_url='https://gateway.example.com', protocol='openai')
        working, reply = llm_check._text_probe(probe_cfg, None)
        check('404 回退后 working.protocol=anthropic',
              working.protocol == 'anthropic', working.protocol)
        check('回退共发 2 次请求（1 次 404 + 1 次回退）',
              len(fake.calls) == 2, len(fake.calls))
        check('第二次请求打 anthropic 端点',
              fake.calls[1]['url'].endswith('/v1/messages'), fake.calls[1]['url'])
        check('回退后拿到回复', reply == '可用', reply)

        # 协议本来就对：不发多余请求，working 原样返回
        fake = install([_Resp(200, anthropic_body('可用'))])
        probe_cfg = cfg(base_url='https://gateway.example.com', protocol='anthropic')
        working, reply = llm_check._text_probe(probe_cfg, None)
        check('协议正确时只发 1 次请求', len(fake.calls) == 1, len(fake.calls))
        check('协议正确时 working 不变', working.protocol == 'anthropic', working.protocol)

        # 非 404 错误不触发回退，直接抛
        fake = install([_Resp(401, text='bad key')])
        try:
            llm_check._text_probe(cfg(), None)
            check('401 不触发回退直接抛', False, '没抛')
        except C.LLMError as exc:
            check('401 不触发回退直接抛', True)
            check('401 不触发回退就只发 1 次请求', len(fake.calls) == 1, len(fake.calls))

        # 回退也失败：抛错消息里带原 404
        fake = install([_Resp(404, text='not found'),
                        _Resp(404, text='also not found')])
        try:
            llm_check._text_probe(cfg(), None)
            check('回退也失败时抛错', False, '没抛')
        except C.LLMError as exc:
            check('回退也失败时抛错', True)
            check('报错里带原 404', '原错误' in str(exc), str(exc))

        # ================================================================
        print('\n=== 9. key 不外泄：报错与摘要里只有打码形态 ===')
        check('mask_key 只留前 3 后 4',
              CF.mask_key(SECRET) == 'sk-...9999', CF.mask_key(SECRET))
        check('短 key 也不全露', CF.mask_key('abc123') == 'ab***', CF.mask_key('abc123'))
        check('空 key 说未设置', CF.mask_key('') == '(未设置)')

        described = CF.describe(cfg())
        check('describe 不含明文 key', SECRET not in described, described)
        check('describe 含打码 key', 'sk-...9999' in described, described)
        check('describe 标出每项来源', '←' in CF.describe(c4), CF.describe(c4))

        try:
            cfg(api_key='').require_key()
            check('没有 key 要抛 ConfigError', False, '没抛')
        except CF.ConfigError as exc:
            msg = str(exc)
            check('没有 key 要抛 ConfigError', True)
            check('报错里给出三种配法',
                  '配置文件' in msg and '环境变量' in msg and '命令行' in msg, msg)
            check('报错里给出验证命令', 'llm_check.py' in msg, msg)

        # 假 requests 记下了真实请求头，确认 key 只出现在 Authorization 里、
        # 不会被我们自己 print 到别处
        fake = install([_Resp(400, text='bad request: model not found')])
        try:
            C.chat('问', cfg=cfg(), run_dir=os.path.join(tmp, 'leak'), stage='parse')
        except C.LLMError as exc:
            check('HTTP 400 的报错不含 key', SECRET not in str(exc), str(exc))
        with open(os.path.join(tmp, 'leak', 'intermediate', 'llm_usage.jsonl'),
                  encoding='utf-8') as f:
            check('记账文件不含 key', SECRET not in f.read())

    finally:
        C.requests = real_requests_module
        shutil.rmtree(tmp, ignore_errors=True)

    print('\n' + '=' * 60)
    if failures:
        print('❌ %d 条断言失败：' % len(failures))
        for item in failures:
            print('  - %s' % item)
        return 1
    print('✅ llm 模块测试全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
