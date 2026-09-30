"""把 docs/项目说明.md 重出成 .docx。

★为什么单独写这个脚本：docs/ 不在 git 里（`git ls-files docs` 是空的），
  所以覆盖之前先备份原文件；而本机没有 pandoc，只能自己渲染。
  只够用就好：标题 / 表格 / 列表 / 代码块 / 行内粗体·行内代码·删除线。
"""
import re
import sys

from docx import Document
from docx.shared import Pt
from docx.oxml.ns import qn

EA = qn('w:eastAsia')
BODY = ("Calibri", "宋体")
HEAD = ("Microsoft YaHei", "微软雅黑")


def style_run(run, ascii_name, ea_name, size, bold=False):
    run.font.name = ascii_name
    run.font.size = Pt(size)
    run.bold = bold
    rPr = run._element.get_or_add_rPr()
    rFonts = rPr.get_or_add_rFonts()
    rFonts.set(qn('w:ascii'), ascii_name)
    rFonts.set(qn('w:hAnsi'), ascii_name)
    rFonts.set(EA, ea_name)
    return run


def add_inline(p, text, size=10.5, base=BODY, bold=False):
    """`**粗**` / `` `代码` `` / `~~删除线~~` 三种行内标记，其余按正文。"""
    for tok in re.split(r'(\*\*[^*]+\*\*|`[^`]+`|~~[^~]+~~)', text):
        if not tok:
            continue
        if tok.startswith('**') and tok.endswith('**') and len(tok) > 4:
            style_run(p.add_run(tok[2:-2]), base[0], base[1], size, True)
        elif tok.startswith('`') and tok.endswith('`') and len(tok) > 2:
            style_run(p.add_run(tok[1:-1]), "Consolas", base[1], size)
        elif tok.startswith('~~') and tok.endswith('~~') and len(tok) > 4:
            style_run(p.add_run(tok[2:-2]), base[0], base[1], size).font.strike = True
        else:
            style_run(p.add_run(tok), base[0], base[1], size, bold)


BLOCK_START = re.compile(r'^(#{1,6}\s|\||```|\s*[-*]\s|\s*\d+\.\s|>)')
H_SIZES = {1: 18, 2: 14, 3: 12, 4: 11, 5: 10.5, 6: 10.5}


def build(md_path, docx_path):
    lines = open(md_path, encoding='utf-8').read().split('\n')
    doc = Document()
    st = doc.styles['Normal']
    st.font.name = BODY[0]
    st.font.size = Pt(10.5)
    st.element.rPr.rFonts.set(EA, BODY[1])

    i = 0
    while i < len(lines):
        line = lines[i]

        if line.startswith('```'):                                  # 代码块
            i += 1
            buf = []
            while i < len(lines) and not lines[i].startswith('```'):
                buf.append(lines[i])
                i += 1
            i += 1
            p = doc.add_paragraph()
            for k, b in enumerate(buf):
                style_run(p.add_run(('\n' if k else '') + b), "Consolas", BODY[1], 9.5)
            continue

        m = re.match(r'^(#{1,6})\s+(.*)$', line)                    # 标题
        if m:
            lvl = min(len(m.group(1)), 4)
            h = doc.add_heading(level=lvl)
            h.paragraph_format.space_before = Pt(10)
            add_inline(h, m.group(2), H_SIZES[lvl], HEAD, bold=True)
            i += 1
            continue

        m = re.match(r'^!\[(.*)\]\((.+)\)\s*$', line)               # 图片
        if m:
            import os
            from docx.shared import Cm
            img = os.path.join(os.path.dirname(os.path.abspath(md_path)), m.group(2))
            if os.path.exists(img):
                doc.add_picture(img, width=Cm(15))
                cap = doc.add_paragraph()
                cap.paragraph_format.space_after = Pt(8)
                style_run(cap.add_run(m.group(1)), BODY[0], BODY[1], 9)
            else:
                add_inline(doc.add_paragraph(), '(缺图：%s)' % m.group(2))
            i += 1
            continue

        if line.lstrip().startswith('|') and i + 1 < len(lines) and \
                re.match(r'^\|[\s:|-]+\|$', lines[i + 1].strip()):  # 表格
            header = [c.strip() for c in line.strip().strip('|').split('|')]
            i += 2
            rows = []
            while i < len(lines) and lines[i].lstrip().startswith('|'):
                rows.append([c.strip() for c in lines[i].strip().strip('|').split('|')])
                i += 1
            t = doc.add_table(rows=1, cols=len(header))
            t.style = 'Table Grid'
            for j, c in enumerate(header):
                cell = t.rows[0].cells[j]
                cell.text = ''
                add_inline(cell.paragraphs[0], c, 10, HEAD, bold=True)
            for row in rows:
                cells = t.add_row().cells
                for j, c in enumerate(row[:len(header)]):
                    cells[j].text = ''
                    add_inline(cells[j].paragraphs[0], c, 10)
            doc.add_paragraph()
            continue

        m = re.match(r'^\s*([-*]|\d+\.)\s+(.*)$', line)             # 列表
        if m:
            p = doc.add_paragraph(style='List Bullet' if m.group(1) in '-*' else 'List Number')
            add_inline(p, m.group(2))
            i += 1
            continue

        if line.lstrip().startswith('>'):                           # 引用
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Pt(18)
            add_inline(p, line.lstrip().lstrip('>').strip())
            i += 1
            continue

        if not line.strip():
            i += 1
            continue

        buf = [line]                                                # 普通段落（连续行合并）
        i += 1
        while i < len(lines) and lines[i].strip() and not BLOCK_START.match(lines[i]):
            buf.append(lines[i])
            i += 1
        add_inline(doc.add_paragraph(), ' '.join(buf))
    doc.save(docx_path)


if __name__ == '__main__':
    build(sys.argv[1], sys.argv[2])
    print('written', sys.argv[2])
