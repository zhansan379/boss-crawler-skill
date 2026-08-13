#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Profile 交叉校验脚本

从原始简历文本中提取所有疑似技术名词，与 profile.json 对比，
检测 Claude 解析简历时可能遗漏的技能、项目和经历。

用法:
    python validate_profile.py <resume_text_path> <profile_json_path>

输出:
    - 差异报告（文本 + JSON）
    - 退出码 0 = 无差异 / 1 = 有差异（供 CI/自动化判断）
"""

import json
import re
import sys
import os
from collections import defaultdict
from typing import List, Set, Tuple


# ============================================================
# 已知技术名词字典（防止正则遗漏的常见技术术语）
# ============================================================

KNOWN_TECH_TERMS: Set[str] = {
    # 编程语言
    'Python', 'Java', 'JavaScript', 'TypeScript', 'Go', 'Golang', 'Rust', 'C++', 'C#',
    'PHP', 'Ruby', 'Swift', 'Kotlin', 'Dart', 'Scala', 'R', 'Shell', 'Bash',
    # 后端框架
    'SpringBoot', 'Spring Cloud', 'Spring MVC', 'Spring', 'MyBatis', 'MyBatis Plus',
    'Django', 'Flask', 'FastAPI', 'Express', 'NestJS', 'Laravel', 'Gin', 'Beego',
    'ASP.NET', 'Koa', 'Hibernate', 'JPA', 'Struts',
    # 前端框架/库
    'React', 'Vue', 'Vue2', 'Vue3', 'Angular', 'Next.js', 'Nuxt', 'Svelte',
    'Element', 'Element Plus', 'Vant', 'Ant Design', 'Bootstrap', 'jQuery',
    'Pinia', 'Redux', 'Vuex', 'Zustand',
    # AI/LLM 框架
    'LangChain', 'LangGraph', 'LlamaIndex', 'AutoGen', 'CrewAI', 'MetaGPT',
    'Transformers', 'HuggingFace', 'PyTorch', 'TensorFlow', 'Keras', 'Scikit-learn',
    'OpenCV', 'YOLO', 'YOLOv8', 'YOLOv8n', 'MediaPipe', 'Qwen', 'Qwen-VL',
    'DeepSeek', 'OpenAI', 'Claude', 'Gemini', 'LiveTalking',
    # RAG/Agent 相关
    'RAG', 'GraphRAG', 'RAGAS', 'Agent', 'Prompt Engineering', 'Function Calling',
    'MCP', 'Tool Calling', 'Coze', 'Dify', 'FastGPT', 'n8n', 'ReAct',
    # 数据库
    'MySQL', 'PostgreSQL', 'MongoDB', 'Redis', 'Elasticsearch', 'Cassandra',
    'Neo4j', 'Milvus', 'FAISS', 'Chroma', 'Pinecone', 'Weaviate', 'PgVector',
    'SQLite', 'Oracle', 'SQL Server', 'DynamoDB',
    # 中间件/工具
    'Docker', 'Kubernetes', 'K8s', 'Jenkins', 'Git', 'GitHub', 'GitLab',
    'Maven', 'Gradle', 'Nginx', 'Apache', 'Tomcat', 'RabbitMQ', 'Kafka',
    'Redisson', 'Zookeeper', 'Consul', 'Nacos', 'Sentinel',
    # 云服务
    'AWS', '阿里云', '腾讯云', 'Azure', 'GCP', 'Serverless', 'Lambda',
    # 协议/标准
    'RESTful', 'GraphQL', 'gRPC', 'WebSocket', 'MQTT', 'OPC UA', 'SSE',
    'OAuth', 'JWT', 'SAML', 'OpenAPI', 'Swagger',
    # 操作系统/命令
    'Linux', 'Unix', 'Windows', 'MacOS',
    # 编辑器/IDE/工具
    'Cursor', 'Copilot', 'GitHub Copilot', 'Claude Code', 'Codex', 'VS Code',
    'IntelliJ', 'PyCharm', 'WebStorm', 'Eclipse', 'Vim', 'Neovim',
    # 通用术语
    'SaaS', 'PaaS', 'IaaS', 'API', 'SDK', 'CLI', 'CI/CD', 'DevOps',
    '微服务', '分布式', '高并发', '多线程', '异步IO', 'JVM',
    'DOM', 'CSS', 'HTML', 'Sass', 'Less', 'Webpack', 'Vite', 'ESBuild',
    'ES6', 'Node.js', 'Deno', 'Bun',
    # Java 生态
    'JPA', 'JDBC', 'Sa-Token', 'Tika', 'Lombok', 'MapStruct',
    # 其他
    'Selenium', 'Scrapy', 'BeautifulSoup', 'Requests', 'Aiohttp',
    'Celery', 'Gunicorn', 'Uvicorn', 'Supervisor', 'PM2',
    'Jira', 'Confluence', 'Trello', 'Slack', 'Figma', 'Postman',
    # 设计模式/架构
    'DDD', 'TDD', 'MVC', 'MVP', 'MVVM', 'Clean Architecture',
    '微服务架构', 'Serverless架构', '事件驱动',
}


def load_resume_text(path: str) -> str:
    """加载原始简历文本"""
    if not os.path.exists(path):
        print(f"[ERROR] Resume file not found: {path}")
        sys.exit(2)
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


def load_profile(path: str) -> dict:
    """加载 profile.json"""
    if not os.path.exists(path):
        print(f"[ERROR] Profile file not found: {path}")
        sys.exit(2)
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def extract_camelcase_terms(text: str) -> Set[str]:
    """从文本中提取所有疑似技术名词（大写驼峰、全大写缩写等）"""
    patterns = [
        # 大写驼峰: SpringBoot, MyBatisPlus
        r'\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b',
        # 全大写缩写: RAG, LLM, MCP (至少2个)
        r'\b[A-Z]{2,}(?:\s*/\s*[A-Z]{2,})?\b',
        # 小写开头但有驼峰: iOS, iPhone
        r'\b[a-z]+[A-Z][a-z]+(?:[A-Z][a-z]+)*\b',
        # 数字+版本: Vue2, Vue3, ES6, YOLOv8
        r'\b[A-Z][a-z]*\d[a-z]*\b',
        # 带连字符的技术名: Scikit-learn, Sa-Token
        r'\b[A-Z][a-z]+-[a-z]+\b',
        # 中文技术词
        r'(?:大模型|微服务|分布式|高并发|多线程|异步IO|全栈|后端开发|前端开发|爬虫|运维|数据结构|算法)',
    ]

    terms = set()
    for pattern in patterns:
        matches = re.findall(pattern, text)
        terms.update(matches)
    return terms


def extract_from_known_dict(text: str) -> Set[str]:
    """用已知技术字典扫描文本中出现的所有术语"""
    text_lower = text.lower()
    found = set()
    for term in KNOWN_TECH_TERMS:
        if term.lower() in text_lower:
            found.add(term)
    return found


def collect_profile_skills(profile: dict) -> Set[str]:
    """从 profile JSON 中收集所有技能"""
    skills = set()
    skill_categories = profile.get('skills', {})
    if isinstance(skill_categories, dict):
        for category, items in skill_categories.items():
            if isinstance(items, list):
                skills.update(items)
    # 也检查 keywords
    keywords = profile.get('keywords', [])
    if isinstance(keywords, list):
        skills.update(keywords)
    return skills


def collect_profile_project_names(profile: dict) -> Set[str]:
    """收集 profile 中的项目名"""
    projects = profile.get('projects', [])
    return {p.get('name', '') for p in projects if isinstance(p, dict)}


def collect_profile_companies(profile: dict) -> Set[str]:
    """收集 profile 中的公司名"""
    companies = profile.get('experience', {}).get('companies', [])
    return {c.get('name', '') for c in companies if isinstance(c, dict)}


def extract_project_names(text: str) -> Set[str]:
    """Extract project names from resume text. Conservative: only well-known patterns."""
    projects = set()

    # Extract URLs that look like project demos
    url_matches = re.findall(r'(?:项目地址|项目链接|demo|Demo)[：:]\s*(https?://[^\s]+)', text)
    projects.update(url_matches)

    # Extract project names from the "项目经历" section: match lines with project-like titles
    # Pattern: date range followed by project name
    date_project = re.findall(r'\d{4}\.\d{2}[–\-]\d{4}\.\d{2}\s*\n\s*(.+)', text)
    projects.update(p.strip() for p in date_project)

    # Pattern: project name lines that contain keywords + are standalone
    project_lines = re.findall(r'(?:^|\n)\s*([一-龥]{3,20}(?:AI|LLM|RAG|Agent|平台|系统|商城|客服|面试|问答|助手)\s*)(?:\n|$)', text)
    projects.update(p.strip() for p in project_lines)

    return projects


def extract_company_names(text: str) -> Set[str]:
    """Extract company names from resume text. Conservative: only 'XX Company' pattern."""
    pattern = r'([一-龥]{3,12}(?:科技|信息|技术|网络|数据|智能|云计算|软件|互联|传媒|集团|公司|研究院|研究所)(?:有限公司|股份有限公司|有限责任公司)?)'
    matches = re.findall(pattern, text)
    # Filter out common false positives
    return {m for m in matches if len(m) >= 6}


def check_projects_empty(profile: dict) -> Tuple[bool, str]:
    """检查 projects 是否为空"""
    projects = profile.get('projects', [])
    if not projects:
        return True, "[WARN] projects is empty array but resume contains project content"
    return False, ""


def check_skills_categories(profile: dict) -> List[str]:
    """检查 skills 各类别是否完整"""
    warnings = []
    skills = profile.get('skills', {})
    if isinstance(skills, dict):
        for cat, items in skills.items():
            if isinstance(items, list) and len(items) < 2:
                warnings.append(f"[WARN] skills.{cat} has only {len(items)} item(s), may be incomplete")
    return warnings


def run_validation(resume_text: str, profile: dict) -> dict:
    """执行完整校验，返回差异报告"""
    # --- 技能提取 ---
    camel_terms = extract_camelcase_terms(resume_text)
    known_terms = extract_from_known_dict(resume_text)
    all_extracted_terms = camel_terms | known_terms

    profile_skills = collect_profile_skills(profile)

    # 过滤太短/太通用的词
    skip_words = {'The', 'To', 'In', 'On', 'We', 'He', 'It', 'Is', 'Be', 'No', 'Go',
                  'At', 'By', 'Or', 'An', 'As', 'If', 'So', 'My', 'Up', 'Do', 'Me',
                  'Us', 'Oh', 'Hi', 'Am', 'Bb', 'Cc', 'Dd', 'Ee', 'Ff', 'Gg', 'Hh',
                  'Ok', 'Io', 'Os', 'Db', 'Ai', 'Id', 'Mr', 'Ms', 'Dr', 'St', 'Rd',
                  'Av', 'Po', 'Pr', 'Wi', 'Fi', 'Cd', 'Pc', 'Ip', 'Cp', 'Ct', 'Rn',
                  'Saas', 'Paas', 'Iaas', 'Aes', 'Tls', 'Ssl', 'Xml', 'Csv', 'Dom',
                  'Cpu', 'Gpu', 'Ram', 'Ssd', 'Hdd', 'Tcp', 'Udp', 'Dns', 'Dos',
                  'PaaS', 'IaaS', 'SaaS', 'API', 'SDK', 'CLI', 'CI', 'CD'}

    meaningful_terms = {t for t in all_extracted_terms if t not in skip_words and len(t) > 1}

    # 与 profile 对比
    missing_skills = set()
    matched_skills = set()

    for term in sorted(meaningful_terms):
        term_lower = term.lower()
        # 检查是否在 profile skills 的任一类中
        found = False
        for skill in profile_skills:
            if term_lower == skill.lower() or skill.lower() in term_lower or term_lower in skill.lower():
                found = True
                matched_skills.add(term)
                break
        if not found:
            # 再次检查：是否在 projects tech_stack 中
            for proj in profile.get('projects', []):
                tech_stack = proj.get('tech_stack', [])
                for tech in tech_stack:
                    if term_lower == tech.lower() or tech.lower() in term_lower:
                        found = True
                        matched_skills.add(term)
                        break
                if found:
                    break

        if not found and term in KNOWN_TECH_TERMS:
            missing_skills.add(term)

    # --- 项目检查 ---
    profile_projects = collect_profile_project_names(profile)
    text_projects = extract_project_names(resume_text)
    missing_projects = text_projects - profile_projects

    # --- 经历检查 ---
    profile_companies = collect_profile_companies(profile)
    text_companies = extract_company_names(resume_text)
    missing_companies = text_companies - profile_companies

    # --- 软检查 ---
    soft_warnings = []
    empty_proj, proj_warning = check_projects_empty(profile)
    if proj_warning:
        soft_warnings.append(proj_warning)
    soft_warnings.extend(check_skills_categories(profile))

    return {
        'matched_skills': sorted(matched_skills),
        'missing_skills': sorted(missing_skills),
        'missing_projects': sorted(missing_projects),
        'missing_companies': sorted(missing_companies),
        'soft_warnings': soft_warnings,
        'total_extracted': len(meaningful_terms),
        'total_profile_skills': len(profile_skills),
        'has_gaps': bool(missing_skills or missing_projects or missing_companies or soft_warnings),
    }


def print_report(report: dict):
    """格式化输出校验报告"""
    print()
    print("=" * 60)
    print("  [Profile Validation Report]")
    print("=" * 60)
    print(f"  简历提取术语: {report['total_extracted']}")
    print(f"  Profile 已有技能: {report['total_profile_skills']}")
    print()

    if report['matched_skills']:
        print(f"  [MATCHED] Skills ({len(report['matched_skills'])}):")
        print(f"     {', '.join(report['matched_skills'][:30])}")
        if len(report['matched_skills']) > 30:
            print(f"     ... and {len(report['matched_skills'])} total")

    if report['missing_skills']:
        print()
        print(f"  [MISSING] Skills ({len(report['missing_skills'])}):")
        print(f"     {', '.join(report['missing_skills'])}")

    if report['missing_projects']:
        print()
        print(f"  [MISSING] Projects ({len(report['missing_projects'])}):")
        for p in report['missing_projects']:
            print(f"     - {p}")

    if report['missing_companies']:
        print()
        print(f"  [MISSING] Experience ({len(report['missing_companies'])}):")
        for c in report['missing_companies']:
            print(f"     - {c}")

    if report['soft_warnings']:
        print()
        for w in report['soft_warnings']:
            print(f"  [WARN] {w}")

    if report['has_gaps']:
        print()
        print("  [ACTION REQUIRED] Gaps found! Update profile.json then re-run matching.")
    else:
        print()
        print("  [OK] No gaps. Profile matches resume text.")

    print()
    print("=" * 60)

    # 输出 JSON 报告（供程序化消费）
    json_path = os.path.join(
        os.path.dirname(os.path.abspath(sys.argv[2])),
        'profile_validation.json'
    )
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"  JSON 报告已保存: {json_path}")


def main():
    # Fix Windows console encoding for emoji/Chinese output
    if sys.platform == 'win32':
        for _stream in (sys.stdout, sys.stderr):
            _stream.reconfigure(encoding='utf-8', errors='replace')

    if len(sys.argv) < 3:
        print("用法: python validate_profile.py <resume_text_path> <profile_json_path>")
        print("示例: python validate_profile.py ./resume_output/resume_text.txt ./resume_output/profile.json")
        sys.exit(2)

    resume_path = sys.argv[1]
    profile_path = sys.argv[2]

    resume_text = load_resume_text(resume_path)
    profile = load_profile(profile_path)

    report = run_validation(resume_text, profile)
    print_report(report)

    sys.exit(1 if report['has_gaps'] else 0)


if __name__ == '__main__':
    main()
