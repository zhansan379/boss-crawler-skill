#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
简历文件解析：支持 PDF (PyPDF2) 和 Word (python-docx)
"""

import os

# 尝试导入文件解析库
try:
    from PyPDF2 import PdfReader
    HAS_PDF = True
except ImportError:
    HAS_PDF = False

try:
    from docx import Document
    HAS_DOCX = True
except ImportError:
    HAS_DOCX = False


def parse_resume_file(file_path: str) -> str:
    """解析PDF/Word文件内容"""
    ext = os.path.splitext(file_path)[1].lower()

    print(f"\n正在解析简历文件: {file_path}")

    try:
        if ext == '.pdf':
            if not HAS_PDF:
                raise ImportError("PyPDF2未安装，请运行: pip install PyPDF2")
            text = parse_pdf(file_path)
        elif ext in ['.docx', '.doc']:
            if not HAS_DOCX:
                raise ImportError("python-docx未安装，请运行: pip install python-docx")
            text = parse_docx(file_path)
        else:
            raise ValueError(f"不支持的文件格式: {ext}")

        if not text.strip():
            print("警告: 文件内容为空或无法解析")
            return ""

        print(f"解析完成，获取到 {len(text)} 个字符")
        return text

    except Exception as e:
        print(f"解析失败: {e}")
        return ""


def parse_pdf(file_path: str) -> str:
    """解析PDF文件"""
    reader = PdfReader(file_path)
    text_parts = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            text_parts.append(text)
    return '\n'.join(text_parts)


def parse_docx(file_path: str) -> str:
    """解析Word文件"""
    doc = Document(file_path)
    text_parts = []
    for para in doc.paragraphs:
        if para.text:
            text_parts.append(para.text)
    return '\n'.join(text_parts)
