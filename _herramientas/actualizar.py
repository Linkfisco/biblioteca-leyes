# -*- coding: utf-8 -*-
"""Revisa la lista de Leyes Federales Vigentes de la Cámara de Diputados y, si alguna
ley tiene una reforma más nueva que la del catálogo, baja el PDF, lo convierte a EPUB
y actualiza catalogo.json. Pensado para correr solo cada día en GitHub Actions.

Uso:  python _herramientas/actualizar.py            (desde la raíz del repositorio)
      python _herramientas/actualizar.py --solo-revisar   (no descarga, solo informa)
"""
import os, re, sys, json, html, hashlib, unicodedata, urllib.request, datetime, tempfile

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
sys.path.insert(0, AQUI)
import pdf2epub as P  # noqa: E402

LISTA = os.environ.get('XEROVA_LISTA', 'https://www.diputados.gob.mx/LeyesBiblio/index.htm')
BASE = os.environ.get('XEROVA_BASE', 'https://www.diputados.gob.mx/LeyesBiblio/')
UA = {'User-Agent': 'Mozilla/5.0 (Xerova actualizador)'}
SOLO_REVISAR = '--solo-revisar' in sys.argv


def bajar(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def norm(s):
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+', ' ', s).strip()


def texto(h):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', h))).strip()


def leer_lista():
    raw = bajar(LISTA)
    for enc in ('utf-8', 'cp1252', 'latin-1'):
        try:
            pagina = raw.decode(enc); break
        except UnicodeDecodeError:
            continue
    filas = []
    for trozo in re.split(r'<tr[\s>]', pagina, flags=re.I)[1:]:
        trozo = trozo.split('</tr>')[0] if '</tr>' in trozo.lower() else trozo
        pdfs = [h for h in re.findall(r'href\s*=\s*["\']([^"\']+\.pdf)["\']', trozo, re.I)
                if '/pdf/' in h.replace('\\', '/') or h.lower().startswith('pdf/')]
        pdfs = [h for h in pdfs if 'mov' not in h.lower()] or pdfs
        if not pdfs:
            continue
        plano = texto(trozo)
        # solo fechas de publicación en el DOF (no la fecha en que se abrogará, ni notificaciones)
        fechas = re.findall(r'DOF\s*(\d{2})/(\d{2})/(\d{4})', plano)
        if not fechas:
            sin_abrog = re.sub(r'(?i)abrogad[oa][^\d]{0,80}\d{2}/\d{2}/\d{4}', '', plano)
            fechas = re.findall(r'(\d{2})/(\d{2})/(\d{4})', sin_abrog)
        if not fechas:
            continue
        ultima = max(f'{y}-{m}-{d}' for d, m, y in fechas)
        nombre = ''
        for a in re.findall(r'<a[^>]*>(.*?)</a>', trozo, re.I | re.S):
            t = texto(a)
            if len(t) > 8 and not re.fullmatch(r'[\d\s/.-]+', t):
                nombre = t; break
        if not nombre:
            continue
        abrogada, abroga_el = False, None
        ma = re.search(r'(?i)abrogad[oa]', plano)
        if ma:
            fa = re.search(r'(\d{2})/(\d{2})/(\d{4})', plano[ma.end():ma.end() + 90])
            cuando = f'{fa.group(3)}-{fa.group(2)}-{fa.group(1)}' if fa else None
            if cuando and cuando > datetime.date.today().isoformat():
                abroga_el = cuando          # se abrogará en el futuro: sigue vigente
            else:
                abrogada = True
        pdf = pdfs[0]
        if not pdf.lower().startswith('http'):
            pdf = BASE + pdf.lstrip('./')
        filas.append({'nombre': nombre, 'pdf': pdf, 'fecha': ultima, 'abrogada': abrogada, 'abroga_el': abroga_el})
    return filas


STOP = {'de', 'la', 'las', 'el', 'los', 'y', 'del', 'para', 'en', 'a', 'que', 'por', 'e', 'con', 'al', 'o'}


def siglas_de(nombre):
    return ''.join(w[0] for w in norm(nombre).split() if w not in STOP).upper()


def parecido(titulo_cat, nombre_pag):
    """Puntaje (0 = no es la misma ley) para títulos recortados con '...' como venían en los nombres de archivo."""
    a, b = norm(titulo_cat), norm(nombre_pag)
    if a == b:
        return 1000
    if '...' not in titulo_cat and '…' not in titulo_cat:
        return 0
    limpio = re.sub(r'\(.*?\)', ' ', titulo_cat)
    partes = [norm(p) for p in re.split(r'\.\.\.|…', limpio) if norm(p)]
    if not partes or len(partes[0]) < 25 or not b.startswith(partes[0]):
        return 0
    pos = len(partes[0])
    for p in partes[1:]:
        if re.fullmatch(r'[a-z]{2,5}', p):            # siglas sueltas: AIF, BID
            if p.upper() not in siglas_de(nombre_pag) and p not in b.split():
                return 0
            continue
        i = b.find(p, pos)
        if i < 0:
            return 0
        pos = i + len(p)
    return len(partes[0])


def categoria(t):
    for pref, cat in [('Constitución', 'Constitución'), ('Código', 'Códigos'), ('Ley General', 'Leyes Generales'),
                      ('Ley Nacional', 'Leyes Nacionales'), ('Ley Federal', 'Leyes Federales'),
                      ('Ley Orgánica', 'Leyes Orgánicas'), ('Ley Reglamentaria', 'Leyes Reglamentarias'),
                      ('Reglamento', 'Reglamentos'), ('Ley', 'Otras leyes')]:
        if t.startswith(pref):
            return cat
    return 'Otros ordenamientos'


def titulo_bonito(nombre):
    # "CÓDIGO Penal Federal" -> "Código Penal Federal"
    p = nombre.split(' ', 1)
    return (p[0].capitalize() + (' ' + p[1] if len(p) > 1 else '')).strip()


def guardar_indicadores():
    """Respaldo para la app: indicadores del día del DOF en indicadores.json."""
    ruta = os.path.join(RAIZ, 'indicadores.json')
    previo = {}
    if os.path.exists(ruta):
        try: previo = json.load(open(ruta, encoding='utf-8'))
        except Exception: previo = {}
    for url in ('https://sidof.segob.gob.mx/dof/sidof/indicadores/', 'https://sidofqa.segob.gob.mx/dof/sidof/indicadores/'):
        try:
            j = json.loads(bajar(url).decode('utf-8'))
            l = j.get('ListaIndicadores') or []
            if not l:
                continue
            datos = {'fecha': l[0]['fecha'], 'lista': {str(x['codTipoIndicador']): str(x['valor']) for x in l},
                     'fuente': 'DOF', 'consultado': datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%MZ')}
            if previo.get('anuales'):
                datos['anuales'] = previo['anuales']   # los valores anuales los capturas tú
            json.dump(datos, open(ruta, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
            print('Indicadores del', datos['fecha'], datos['lista'])
            return
        except Exception as e:
            print('Indicadores: no se pudo con', url, e)


def main():
    if not SOLO_REVISAR:
        guardar_indicadores()
    ruta_cat = os.path.join(RAIZ, 'catalogo.json')
    cat = json.load(open(ruta_cat, encoding='utf-8'))
    por_nombre = {norm(l['titulo']): l for l in cat['leyes']}
    por_fuente = {l.get('fuente'): l for l in cat['leyes'] if l.get('fuente')}
    filas = leer_lista()
    print(f'Leyes en la página de la Cámara: {len(filas)}')
    if len(filas) < 200:
        sys.exit('La página trae muy pocas leyes; puede que haya cambiado su formato. No toco nada.')

    hoy = datetime.date.today().isoformat()
    cambios, nuevas, sin_par, fallas, reparado = [], [], [], [], []

    def quitar_ley(x):
        reparado.append(x['id'])
        cat['leyes'].remove(x)
        ruta = os.path.join(RAIZ, x['archivo'])
        if os.path.exists(ruta) and not any(y['archivo'] == x['archivo'] for y in cat['leyes']):
            os.remove(ruta)
    vistos = set()
    sin_fuente = [l for l in cat['leyes'] if not l.get('fuente')]
    hoy_iso = datetime.date.today().isoformat()
    reemplazadas = set()
    for f in filas:
        l = por_fuente.get(f['pdf']) or por_nombre.get(norm(f['nombre']))
        # títulos recortados: buscar el más parecido entre los que aún no tienen fuente
        cand = max(sin_fuente, key=lambda x: parecido(x['titulo'], f['nombre']), default=None)
        if cand is not None and parecido(cand['titulo'], f['nombre']) > 0:
            if l is not None and l is not cand:
                # REPARACIÓN: ya existe otra entrada con esta fuente (se creó como 'nueva' por error).
                # Se queda la nueva (tiene el título completo) y se borra la recortada.
                reemplazadas.add(l['id'])
                quitar_ley(cand)
                sin_fuente.remove(cand)
            elif l is None:
                l = cand; sin_fuente.remove(cand)
        if l is None:
            if f['abrogada']:
                continue
            sin_par.append(f); continue
        vistos.add(l['id'])
        l['fuente'] = f['pdf']
        # fecha imposible (en el futuro) por una versión anterior del actualizador
        if l.get('fecha', '') > hoy_iso:
            l['fecha'] = f['fecha']; l['version'] = f['fecha'].replace('-', '')
        if f['abroga_el']:
            l['abrogaEl'] = f['abroga_el']
        else:
            l.pop('abrogaEl', None)
        if not f['abrogada'] and l.get('abrogada'):
            l.pop('abrogada'); reparado.append(l['id'])      # se había marcado mal
        if f['abrogada'] and not l.get('abrogada'):
            l['abrogada'] = True; cambios.append((l, 'abrogada'))
            continue
        if f['fecha'] > l['fecha']:
            cambios.append((l, 'reforma', f))

    # leyes nuevas en la página (no estaban en el catálogo)
    for f in sin_par:
        t = titulo_bonito(f['nombre'])
        ident = 'federal/' + P.slug(t)[:76]
        usados = {x['id'] for x in cat['leyes']} | {x[0]['id'] for x in nuevas}
        base, k = ident, 1
        while ident in usados:
            k += 1; ident = f'{base}-{k}'
        l = {'id': ident, 'titulo': t, 'ambito': 'Federal', 'categoria': categoria(t), 'fecha': '0000-00-00',
             'tipoFecha': 'publicacion', 'version': '0', 'archivo': ident + '.epub', 'bytes': 0, 'sha256': '',
             'articulos': 0, 'fuente': f['pdf'], 'nueva': True}
        nuevas.append((l, f))

    print(f'Con reforma nueva: {sum(1 for c in cambios if c[1] == "reforma")} · nuevas: {len(nuevas)} · abrogadas: {sum(1 for c in cambios if c[1] == "abrogada")}')
    for c in cambios:
        print('  -', c[0]['titulo'], c[1], c[2]['fecha'] if len(c) > 2 else '')
    for l, f in nuevas:
        print('  + NUEVA', l['titulo'], f['fecha'])
    if SOLO_REVISAR:
        return

    novedades = cat.get('novedades', [])
    ids_ok = {x['id'] for x in cat['leyes']}
    abrog = {x['id'] for x in cat['leyes'] if x.get('abrogada')}
    novedades = [n for n in novedades if n['id'] in ids_ok
                 and not (n['tipo'] == 'nueva' and n['id'] in reemplazadas)
                 and not (n['tipo'] == 'abrogada' and n['id'] not in abrog)]
    fechas_ok = {x['id']: x['fecha'] for x in cat['leyes']}
    for n in novedades:
        if n.get('fecha', '') > hoy:
            n['fecha'] = fechas_ok.get(n['id'], hoy)
    if reparado:
        print(f'Reparadas {len(reparado)} entradas del catálogo')
    trabajo = [(c[0], c[2]) for c in cambios if c[1] == 'reforma'] + nuevas
    for l, f in trabajo:
        try:
            pdf = bajar(f['pdf'])
            with tempfile.TemporaryDirectory() as tmp:
                ruta_pdf = os.path.join(tmp, l['titulo'][:80] + '.pdf')
                open(ruta_pdf, 'wb').write(pdf)
                data = P.build(ruta_pdf)
                if not data:
                    raise RuntimeError('el PDF no trae texto')
                data['title'] = l['titulo']
                ruta_epub = os.path.join(tmp, 'x.epub')
                na, _ = P.write_epub(data, ruta_epub, l['id'].split('/')[-1])
                # control de calidad: no reemplazar si la conversión salió mucho peor
                if l.get('articulos') and na < 0.7 * l['articulos']:
                    raise RuntimeError(f'la conversión encontró {na} artículos y antes había {l["articulos"]}')
                epub = open(ruta_epub, 'rb').read()
            os.makedirs(os.path.join(RAIZ, os.path.dirname(l['archivo'])), exist_ok=True)
            open(os.path.join(RAIZ, l['archivo']), 'wb').write(epub)
            fecha = f['fecha']
            m = re.match(r'(\d{2})-(\d{2})-(\d{4})', data['reforma'] or '')
            if m:
                fecha = max(fecha, f'{m.group(3)}-{m.group(2)}-{m.group(1)}')
            l.update({'fecha': fecha, 'version': fecha.replace('-', ''), 'bytes': len(epub),
                      'sha256': hashlib.sha256(epub).hexdigest(), 'articulos': na,
                      'tipoFecha': 'ultima_reforma' if data['tipo'] == 'reforma' else 'publicacion'})
            if l.pop('nueva', None):
                cat['leyes'].append(l)
                novedades.insert(0, {'detectado': hoy, 'id': l['id'], 'titulo': l['titulo'], 'tipo': 'nueva', 'fecha': fecha})
            else:
                novedades.insert(0, {'detectado': hoy, 'id': l['id'], 'titulo': l['titulo'], 'tipo': 'reforma', 'fecha': fecha})
            print('  ✓', l['titulo'], fecha, na, 'artículos')
        except Exception as e:
            fallas.append(f"{l['titulo']}: {e}")
            print('  ✗', l['titulo'], e)
    for c in cambios:
        if c[1] == 'abrogada':
            novedades.insert(0, {'detectado': hoy, 'id': c[0]['id'], 'titulo': c[0]['titulo'], 'tipo': 'abrogada'})

    orden = ['Constitución', 'Códigos', 'Leyes Generales', 'Leyes Nacionales', 'Leyes Federales', 'Leyes Orgánicas',
             'Leyes Reglamentarias', 'Otras leyes', 'Reglamentos', 'Otros ordenamientos']
    cat['leyes'].sort(key=lambda x: (orden.index(x['categoria']) if x['categoria'] in orden else 99, x['titulo']))
    cat['categorias'] = [c for c in orden if any(x['categoria'] == c for x in cat['leyes'])]
    hubo = bool(trabajo) or any(c[1] == 'abrogada' for c in cambios) or bool(reparado)
    if not hubo and not fallas and all(l.get('fuente') for l in cat['leyes'] if l['id'] in vistos) and cat.get('_fuentes'):
        print('Sin cambios.'); return
    cat['_fuentes'] = True
    cat['novedades'] = novedades[:60]
    if hubo:
        cat['actualizado'] = hoy
    json.dump(cat, open(ruta_cat, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    if fallas:
        open(os.path.join(RAIZ, 'FALLAS.txt'), 'w', encoding='utf-8').write('\n'.join(fallas))
        print('Hubo fallas; revisa FALLAS.txt')


if __name__ == '__main__':
    main()
