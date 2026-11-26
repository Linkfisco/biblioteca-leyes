# -*- coding: utf-8 -*-
"""Convierte PDFs de leyes de la Cámara de Diputados a EPUB3 ligero.

- Quita encabezados/pies de página.
- Reconstruye párrafos, títulos (LIBRO/TÍTULO/CAPÍTULO/SECCIÓN) y notas de reforma.
- Cada artículo lleva un id estable (art-302, art-11-bis) para anclar subrayados.
- Incluye indice.json dentro del EPUB para "ir al artículo".
"""
import sys, os, re, json, html, zipfile, unicodedata, statistics, hashlib
import pymupdf as fitz

HEAD_Y, FOOT_Y = 100, 740
RE_ART = re.compile(r'^(Art[íi]culo|ART[ÍI]CULO|Art\.)\s+(\d+[\s\-]?[oº°]?\.?(?:(?:\s*-?\s*(?i:bis|ter|qu[áa]ter|quintus|quinquies|sexies|septies|octies|nonies|decies|und[ée]cies|duod[ée]cies|terdecies|quaterdecies)|\s*-\s*[A-Z])\b(?:\s+\d+\b)?)*)', re.U)
RE_ART_WORD = re.compile(r'^(Art[íi]culo|ART[ÍI]CULO)\s+([ÚU]nico|[ÚU]NICO|Primero|Segundo|Tercero|Cuarto|Quinto|Sexto|S[ée]ptimo|Octavo|Noveno|D[ée]cimo\w*|PRIMERO|SEGUNDO|TERCERO|CUARTO|QUINTO|SEXTO|S[ÉE]PTIMO|OCTAVO|NOVENO|D[ÉE]CIMO\w*)', re.U)
RE_HEAD = re.compile(r'^(LIBRO|T[ÍI]TULO|CAP[ÍI]TULO|SECCI[ÓO]N|APARTADO|PARTE|SUBSECCI[ÓO]N|TRANSITORIOS?|ART[ÍI]CULOS? TRANSITORIOS?|DISPOSICIONES)\b', re.U)
RE_NOTA = re.compile(r'^(P[áa]rrafos?|Art[íi]culos?|Fracci[óo]n|Fracciones|Incisos?|Fe de|Denominaci[óo]n|Cap[íi]tulos?|T[íi]tulos?|Secci[óo]n|Apartados?|Libro|Reformad|Adicionad|Derogad|Numeral|Bases?|Subinciso|Ep[íi]grafe|Reubicad|Recorrid)')
LEVEL = {'LIBRO': 1, 'PARTE': 1, 'TITULO': 2, 'TÍTULO': 2, 'CAPITULO': 3, 'CAPÍTULO': 3,
         'SECCION': 4, 'SECCIÓN': 4, 'SUBSECCION': 4, 'SUBSECCIÓN': 4, 'APARTADO': 4}


def slug(s):
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode()
    s = re.sub(r'[^a-zA-Z0-9]+', '-', s).strip('-').lower()
    return s[:80]


def esc(s):
    return html.escape(s, quote=False)


def line_info(l):
    spans = [s for s in l['spans'] if s['text'].strip()]
    if not spans:
        return None
    text = ''.join(s['text'] for s in l['spans'])
    text = re.sub(r'\s+', ' ', text).strip()
    main = max(spans, key=lambda s: len(s['text']))
    bold_chars = sum(len(s['text']) for s in spans if (s['flags'] & 16) or 'Bold' in s['font'])
    ital = all((s['flags'] & 2) or 'It' in s['font'] for s in spans)
    return dict(x0=l['bbox'][0], y0=l['bbox'][1], x1=l['bbox'][2], y1=l['bbox'][3],
                size=round(main['size'], 1), text=text, spans=spans,
                bold=bold_chars >= 0.9 * sum(len(s['text']) for s in spans),
                ital=ital)


def runs_html(spans):
    """Texto de una línea con <b> para lo que venga en negrita (etiquetas I.-, a), Artículo...)."""
    out, cur_b, buf = [], None, ''
    for s in spans:
        b = bool((s['flags'] & 16) or 'Bold' in s['font'])
        if b != cur_b and buf:
            out.append((cur_b, buf)); buf = ''
        cur_b = b; buf += s['text']
    if buf:
        out.append((cur_b, buf))
    h = ''
    for b, t in out:
        t = esc(re.sub(r'\s+', ' ', t))
        h += f'<b>{t}</b>' if b and t.strip() else t
    return h


def page_tables(page, pn, H):
    """Tablas con líneas dibujadas -> filas de celdas (texto, colspan)."""
    try:
        if len(page.get_drawings()) <= 4:
            return []
        found = page.find_tables().tables
    except Exception:
        return []
    out = []
    words = None
    for tb in found:
        if tb.col_count < 2 or tb.row_count < 2:
            continue
        x0, y0, x1, y1 = tb.bbox
        if y1 < HEAD_Y * H / 792 or y0 > FOOT_Y * H / 792:
            continue
        rows = []
        for row in tb.rows:
            cells = []
            for c in row.cells:
                if c is None:
                    if cells:
                        cells[-1][1] += 1
                    else:
                        cells.append(['', 1])
                    continue
                if words is None:
                    words = page.get_text('words')
                cx0, cy0, cx1, cy1 = c
                ws = [w for w in words if cx0 - 1 <= (w[0] + w[2]) / 2 <= cx1 + 1 and cy0 - 1 <= (w[1] + w[3]) / 2 <= cy1 + 1]
                ws.sort(key=lambda w: (w[5], w[6], w[7]))
                t = ' '.join(w[4] for w in ws)
                cells.append([t, 1])
            if any(c[0] for c in cells):
                rows.append(cells)
        if not rows:
            continue
        spans_ = sum(1 for r in rows for c in r if c[1] > 1)
        cells_ = sum(len(r) for r in rows)
        clean = tb.col_count <= 8 and spans_ <= 0.15 * cells_
        if not clean:
            if words is None:
                words = page.get_text('words')
            alt = ' '.join(w[4] for w in words if x0 <= (w[0] + w[2]) / 2 <= x1 and y0 <= (w[1] + w[3]) / 2 <= y1)
            out.append(dict(kind='table', img=render_png(page, tb.bbox), alt=alt, x0=x0, y0=y0, x1=x1, y1=y1,
                            page=pn, rows=None, ncols=-1, text='', spans=[], bold=False, ital=False, size=0))
            continue
        out.append(dict(kind='table', x0=x0, y0=y0, x1=x1, y1=y1, page=pn, rows=rows,
                        ncols=tb.col_count, text='', spans=[], bold=False, ital=False, size=0))
    return out


def render_png(page, bbox):
    import io
    from PIL import Image
    pix = page.get_pixmap(dpi=150, clip=fitz.Rect(bbox).normalize() + (-2, -2, 2, 2), colorspace=fitz.csGRAY)
    im = Image.frombytes('L', (pix.width, pix.height), pix.samples)
    im = im.quantize(colors=8)
    buf = io.BytesIO(); im.save(buf, 'PNG', optimize=True)
    return buf.getvalue()


def page_images(page, pn, H, tables):
    """Imágenes incrustadas en el cuerpo (tarifas, fórmulas, esquemas). Omite el logo del encabezado."""
    out = []
    seen = []
    for info in page.get_image_info():
        x0, y0, x1, y1 = info['bbox']
        if y1 < HEAD_Y * H / 792 + 5 or y0 > FOOT_Y * H / 792:
            continue
        if (x1 - x0) < 25 or (y1 - y0) < 12:
            continue
        if any(t['x0'] - 2 <= (x0 + x1) / 2 <= t['x1'] + 2 and t['y0'] - 2 <= (y0 + y1) / 2 <= t['y1'] + 2 for t in tables):
            continue
        r = fitz.Rect(x0, y0, x1, y1) & page.rect
        if r.is_empty or any(abs(r.y0 - s.y0) < 1 and abs(r.x0 - s.x0) < 1 for s in seen):
            continue
        seen.append(r)
        out.append(dict(kind='table', img=render_png(page, r), alt='', x0=x0, y0=y0, x1=x1, y1=y1,
                        page=pn, rows=None, ncols=-1, text='', spans=[], bold=False, ital=False, size=0))
    return out


def table_html(rows):
    h = ['<div class="tabla"><table>']
    for i, r in enumerate(rows):
        h.append('<tr>' + ''.join(
            (f'<td colspan="{cs}">' if cs > 1 else '<td>') + esc(t) + '</td>' for t, cs in r) + '</tr>')
    h.append('</table></div>')
    return ''.join(h)


def extract_lines(doc):
    lines = []
    for pn, page in enumerate(doc):
        H = page.rect.height
        raw = []
        tables = page_tables(page, pn, H)
        tables += page_images(page, pn, H, tables)
        for b in page.get_text('dict', flags=fitz.TEXT_PRESERVE_WHITESPACE)['blocks']:
            if b['type'] != 0:
                continue
            for l in b['lines']:
                li = line_info(l)
                if not li:
                    continue
                if li['y0'] < HEAD_Y * H / 792 or li['y0'] > FOOT_Y * H / 792:
                    continue
                if re.fullmatch(r'\d+ de \d+', li['text']):
                    continue
                li['page'] = pn
                cx, cy = (li['x0'] + li['x1']) / 2, (li['y0'] + li['y1']) / 2
                if any(t['x0'] - 2 <= cx <= t['x1'] + 2 and t['y0'] - 2 <= cy <= t['y1'] + 2 for t in tables):
                    continue
                raw.append(li)
        raw.sort(key=lambda d: (round(d['y0'] / 3), d['x0']))
        # fusionar líneas en la misma altura (etiqueta de fracción + texto)
        merged = []
        for li in raw:
            if merged and abs(li['y0'] - merged[-1]['y0']) < 3 and li['x0'] > merged[-1]['x1'] - 2 \
                    and li['ital'] == merged[-1]['ital']:
                m = merged[-1]
                m['text'] += ' ' + li['text']
                m['spans'] = m['spans'] + [{'text': ' ', 'flags': 0, 'font': ''}] + li['spans']
                m['x1'] = li['x1']; m['bold'] = m['bold'] and li['bold']
            else:
                merged.append(li)
        merged.extend(tables)
        merged.sort(key=lambda d: d['y0'])
        lines.extend(merged)
    return lines


def build(pdf_path):
    doc = fitz.open(pdf_path)
    meta_text = doc[0].get_text()
    m = re.search(r'[ÚU]ltima [Rr]eforma(?: publicada)? DOF (\d{2}-\d{2}-\d{4})', meta_text)
    tipo = 'reforma'
    if not m:
        m = re.search(r'(?:Nuev[oa]|Publicad[oa])[^\n]{0,40}?DOF (\d{2}-\d{2}-\d{4})', meta_text)
        tipo = 'publicacion'
    reforma = m.group(1) if m else ''
    if not m:
        MES = dict(enero=1, febrero=2, marzo=3, abril=4, mayo=5, junio=6, julio=7, agosto=8,
                   septiembre=9, octubre=10, noviembre=11, diciembre=12)
        m = re.search(r'Diario Oficial de la Federaci[óo]n,? el (\d{1,2}) de (\w+)(?: de)? (\d{4})', meta_text)
        if m and m.group(2).lower() in MES:
            reforma = f'{int(m.group(1)):02d}-{MES[m.group(2).lower()]:02d}-{m.group(3)}'
    lines = extract_lines(doc)
    if not lines:
        return None
    body = [l for l in lines if l.get('kind') != 'table' and not l['ital'] and l['size'] >= 9]
    left = statistics.mode([round(l['x0']) for l in body]) if body else 71
    sizes = statistics.mode([l['size'] for l in body]) if body else 10
    center = (doc[0].rect.width) / 2

    # --- agrupar en bloques ---
    blocks = []  # dict(kind, lines)
    prev = None
    for l in lines:
        if l.get('kind') == 'table' and l.get('img'):
            blocks.append({'kind': 'img', 'img': l['img'], 'alt': l['alt'], 'lines': []})
            prev = None
            continue
        if l.get('kind') == 'table':
            last = blocks[-1] if blocks else None
            if last and last['kind'] == 'table' and last['ncols'] == l['ncols'] and l['page'] > last['page']:
                rows = l['rows']
                if rows and last['rows'] and [c[0] for c in rows[0]] == [c[0] for c in last['rows'][0]]:
                    rows = rows[1:]  # encabezado repetido
                last['rows'].extend(rows); last['page'] = l['page']
            else:
                blocks.append({'kind': 'table', 'rows': list(l['rows']), 'ncols': l['ncols'], 'page': l['page'], 'lines': []})
            prev = None
            continue
        if l['ital'] and l['size'] < sizes - 1.2:
            kind = 'nota'
        elif l['bold'] and l['size'] >= sizes + 0.5 and abs((l['x0'] + l['x1']) / 2 - center) < 40:
            kind = 'head'
        elif l['bold'] and abs((l['x0'] + l['x1']) / 2 - center) < 25 and l['x0'] > left + 40 \
                and (RE_HEAD.match(l['text']) or l['text'].isupper()):
            kind = 'head'
        else:
            kind = 'p'
        new = True
        if prev is not None and blocks and blocks[-1]['kind'] == kind:
            same_page = l['page'] == prev['page']
            gap = l['y0'] - prev['y1']
            if same_page:
                lim = 6 if kind == 'p' else 5
                if kind == 'head':
                    lim = 8
                new = gap > lim
                # fracción / artículo que empieza pegado
                if kind == 'p' and not new and (RE_ART.match(l['text']) or RE_ART_WORD.match(l['text'])) and l['x0'] > left + 5:
                    new = True
            else:
                # salto de página: continúa si la línea no viene con sangría
                new = not (kind == 'p' and l['x0'] < left + 6 and not RE_ART.match(l['text']))
        if new:
            blocks.append({'kind': kind, 'lines': [l]})
        else:
            blocks[-1]['lines'].append(l)
        prev = l

    # --- a HTML ---
    parts = []  # (html, level or None, title, id)
    images = []; used = set(); arts = []; in_trans = False; tcount = 0; hcount = 0
    title = os.path.splitext(os.path.basename(pdf_path))[0]

    def uid(base):
        i, u = 1, base
        while u in used:
            i += 1; u = f'{base}-{i}'
        used.add(u); return u

    for b in blocks:
        if b['kind'] == 'table':
            parts.append((table_html(b['rows']), None, None, None))
            continue
        if b['kind'] == 'img':
            name = f'img/t{len(images)+1:04d}.png'
            images.append((name, b['img']))
            parts.append((f'<div class="timg"><img src="{name}" alt="{html.escape(b["alt"][:2000]) or 'Imagen del documento original'}"/></div>', None, None, None))
            continue
        if b['kind'] == 'nota':
            t = ''
            for l in b['lines']:
                if t and RE_NOTA.match(l['text']):
                    t += '<br/>'
                elif t:
                    t += ' '
                t += esc(l['text'])
            parts.append((f'<p class="nota">{t}</p>', None, None, None))
            continue
        if b['kind'] == 'head':
            texts = [l['text'] for l in b['lines']]
            t0 = texts[0]
            key = unicodedata.normalize('NFC', t0.split()[0].upper()) if t0.split() else ''
            intoc = True
            up = t0.upper()
            if re.match(r'^(ART[ÍI]CULOS? )?TRANSITORIOS?', up):
                if not in_trans or 'DECRETO' in up or 'REFORMA' in up:
                    lvl = 2
                else:
                    lvl = 4; intoc = False
                in_trans = True; tcount += 1
            elif in_trans and up.startswith('DECRETO'):
                lvl = 3
            else:
                lvl = LEVEL.get(key, 3 if not in_trans else 4)
            hcount += 1
            hid = uid(f'h{hcount}')
            inner = esc(t0) + ''.join(f'<br/><span class="sub">{esc(x)}</span>' for x in texts[1:])
            parts.append((f'<h{lvl+1} id="{hid}">{inner}</h{lvl+1}>', lvl if intoc else None, ' — '.join(texts)[:140], hid))
            continue
        # párrafo
        htmls = [runs_html(l['spans']) for l in b['lines']]
        txt = ' '.join(l['text'] for l in b['lines'])
        inner = re.sub(r'\s+', ' ', ' '.join(htmls)).strip()
        inner = re.sub(r'\s{2,}', ' ', inner.replace('</b> <b>', ' '))
        ind = b['lines'][0]['x0'] - left
        cls = 'p'
        attr = ''
        m = RE_ART.match(txt) or RE_ART_WORD.match(txt)
        if m:
            num = re.sub(r'[\s\.\-]+', ' ', m.group(2)).strip()
            num_id = slug(re.sub(r'(\d+)\s*[oº°]', r'\1', num))
            if in_trans:
                aid = uid(f't{tcount}-art-{num_id}')
            else:
                aid = uid(f'art-{num_id}')
                arts.append({'n': re.sub(r'(\d+)\s*[oº°]', r'\1o', num), 'id': aid})
            attr = f' id="{aid}"'
            cls = 'art'
        elif re.match(r'^([IVXLC]+|[a-zñ]{1,2}|[A-Z]|\d{1,2})[\.\)]', txt) and ind > 8:
            cls = 'fr'
            if ind > 30:
                cls = 'fr2'
        parts.append((f'<p class="{cls}"{attr}>{inner}</p>', None, None, attr))

    return dict(images=images, title=title, tipo=tipo, reforma=reforma, parts=parts, arts=arts)


CSS = """body{font-family:serif;line-height:1.5;margin:0 4%;}
h1,h2,h3,h4,h5{font-family:sans-serif;text-align:center;line-height:1.3;margin:1.6em 0 .8em;}
h2{font-size:1.35em}h3{font-size:1.2em}h4{font-size:1.08em}h5{font-size:1em}
.sub{font-weight:normal;font-size:.92em}
p{margin:.55em 0;text-align:justify;hyphens:auto;-webkit-hyphens:auto;}
p.art{margin-top:1.1em}
p.fr{margin-left:1.2em}
p.fr2{margin-left:2.4em}
p.nota{font-size:.78em;font-style:italic;text-align:right;color:#666;margin:.1em 0 .6em}
.portada{text-align:center;margin-top:3em}
.portada .t{font-size:1.6em;font-weight:bold;font-family:sans-serif}
.portada .r{margin-top:1em;color:#555}
.tabla{overflow-x:auto;margin:.8em 0}\ntable{border-collapse:collapse;font-size:.78em;font-family:sans-serif;width:100%}\ntd{border:1px solid #999;padding:2px 4px;vertical-align:top;text-align:left}\ntr:first-child td{font-weight:bold;background:#eee}\n.timg{margin:.8em 0;text-align:center}\n.timg img{max-width:100%;height:auto}\n.aviso{font-size:.8em;color:#777;margin-top:3em;text-align:center}
"""


def xhtml(title, body):
    return ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="es-MX" xml:lang="es-MX">\n'
            f'<head><meta charset="utf-8"/><title>{esc(title)}</title><link rel="stylesheet" href="estilo.css"/></head>\n'
            f'<body>\n{body}\n</body></html>')


def write_epub(data, out_path, ident):
    title, reforma, parts = data['title'], data['reforma'], data['parts']
    # dividir en archivos (~90 KB) cortando en títulos de nivel <=2
    files, cur, size = [], [], 0
    for p in parts:
        if cur and size > 90000 and p[1] is not None and p[1] <= 3:
            files.append(cur); cur, size = [], 0
        cur.append(p); size += len(p[0])
    if cur:
        files.append(cur)
    names = [f's{i+1:03d}.xhtml' for i in range(len(files))]
    toc, idmap = [], {}
    for fn, f in zip(names, files):
        for h, lvl, t, hid in f:
            if lvl is not None:
                toc.append((lvl, t, f'{fn}#{hid}'))
            m = re.search(r' id="([^"]+)"', h)
            if m:
                idmap[m.group(1)] = fn
    arts = [{'n': a['n'], 'href': f"{idmap[a['id']]}#{a['id']}"} for a in data['arts']]

    portada = (f'<div class="portada"><p class="t">{esc(title)}</p>'
               f'<p class="r">Texto vigente<br/>{"Última reforma" if data["tipo"]=="reforma" else "Publicada"}: DOF {esc(reforma)}</p>'
               '<p class="aviso">Fuente: Cámara de Diputados del H. Congreso de la Unión.<br/>'
               'Este texto es de consulta; el único texto con validez oficial es el publicado en el Diario Oficial de la Federación.</p></div>')

    # nav anidado
    def nav_html():
        base = min([t[0] for t in toc] or [1])
        out = ['<ol><li><a href="portada.xhtml">Portada</a>']
        stack = [base]
        for lvl, t, href in toc:
            if lvl > stack[-1]:
                out.append('<ol>')
            else:
                out.append('</li>'); stack.pop()
                while stack and stack[-1] >= lvl:
                    out.append('</ol></li>'); stack.pop()
            out.append(f'<li><a href="{href}">{esc(t)}</a>'); stack.append(lvl)
        while stack:
            stack.pop(); out.append('</li>')
            if stack:
                out.append('</ol>')
        out.append('</ol>')
        return ''.join(out)

    nav_body = f'<nav epub:type="toc" id="toc"><h1>Índice</h1>{nav_html()}</nav>'
    manifest = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
                '<item id="css" href="estilo.css" media-type="text/css"/>',
                '<item id="portada" href="portada.xhtml" media-type="application/xhtml+xml"/>',
                '<item id="indice" href="indice.json" media-type="application/json"/>']
    spine = ['<itemref idref="portada"/>']
    for i, (iname, _) in enumerate(data['images']):
        manifest.append(f'<item id="img{i+1}" href="{iname}" media-type="image/png"/>')
    for i, fn in enumerate(names):
        manifest.append(f'<item id="s{i+1}" href="{fn}" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="s{i+1}"/>')
    date_iso = ''
    m = re.match(r'(\d{2})-(\d{2})-(\d{4})', reforma or '')
    if m:
        date_iso = f'{m.group(3)}-{m.group(2)}-{m.group(1)}'
    opf = f'''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid" xml:lang="es-MX">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:identifier id="uid">mx-leyes:{ident}</dc:identifier>
<dc:title>{esc(title)}</dc:title>
<dc:language>es-MX</dc:language>
<dc:publisher>Cámara de Diputados del H. Congreso de la Unión</dc:publisher>
{f'<dc:date>{date_iso}</dc:date>' if date_iso else ''}
<meta property="dcterms:modified">2026-11-24T00:00:00Z</meta>
</metadata>
<manifest>
{chr(10).join(manifest)}
</manifest>
<spine>
{chr(10).join(spine)}
</spine>
</package>'''
    with zipfile.ZipFile(out_path, 'w') as z:
        z.writestr(zipfile.ZipInfo('mimetype'), 'application/epub+zip', compress_type=zipfile.ZIP_STORED)
        zc = dict(compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        z.writestr('META-INF/container.xml', '<?xml version="1.0"?>\n<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>', **zc)
        z.writestr('OEBPS/content.opf', opf, **zc)
        z.writestr('OEBPS/estilo.css', CSS, **zc)
        z.writestr('OEBPS/nav.xhtml', xhtml('Índice', nav_body), **zc)
        z.writestr('OEBPS/portada.xhtml', xhtml(title, portada), **zc)
        z.writestr('OEBPS/indice.json', json.dumps({'titulo': title, 'reforma': reforma, 'articulos': arts}, ensure_ascii=False), **zc)
        for iname, ib in data['images']:
            z.writestr(f'OEBPS/{iname}', ib, compress_type=zipfile.ZIP_STORED)
        for fn, f in zip(names, files):
            z.writestr(f'OEBPS/{fn}', xhtml(title, '\n'.join(p[0] for p in f)), **zc)
    return len(arts), len(toc)


if __name__ == '__main__':
    src, dst = sys.argv[1], sys.argv[2]
    os.makedirs(dst, exist_ok=True)
    files = sorted(f for f in os.listdir(src) if f.lower().endswith('.pdf'))
    if len(sys.argv) > 3:
        files = [f for f in files if sys.argv[3].lower() in f.lower()]
    report = []
    for f in files:
        title = os.path.splitext(f)[0]
        ident = slug(title)
        try:
            data = build(os.path.join(src, f))
            if not data:
                report.append({'archivo': f, 'error': 'sin texto (¿escaneado?)'}); print('SIN TEXTO', f); continue
            out = os.path.join(dst, ident + '.epub')
            na, nt = write_epub(data, out, ident)
            report.append({'archivo': f, 'id': ident, 'titulo': title, 'reforma': data['reforma'], 'tipo': data['tipo'],
                           'articulos': na, 'secciones': nt, 'bytes': os.path.getsize(out),
                           'pdf_bytes': os.path.getsize(os.path.join(src, f))})
            print(f'{na:5d} art {nt:4d} sec {os.path.getsize(out)//1024:5d} KB  {title[:70]}')
        except Exception as e:
            import traceback; traceback.print_exc()
            report.append({'archivo': f, 'error': repr(e)}); print('ERROR', f, e)
    json.dump(report, open(os.path.join(dst, '_reporte.json'), 'w'), ensure_ascii=False, indent=1)
