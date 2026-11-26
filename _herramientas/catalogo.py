# -*- coding: utf-8 -*-
"""Arma la carpeta 'biblioteca' lista para subir: catalogo.json + federal/*.epub"""
import json, os, re, shutil, hashlib, sys

src, dst = sys.argv[1], sys.argv[2]
rep = json.load(open(os.path.join(src, '_reporte.json')))
os.makedirs(os.path.join(dst, 'federal'), exist_ok=True)


def categoria(t):
    if t.startswith('Constitución'): return 'Constitución'
    if t.startswith('Código'): return 'Códigos'
    if t.startswith('Ley General'): return 'Leyes Generales'
    if t.startswith('Ley Nacional'): return 'Leyes Nacionales'
    if t.startswith('Ley Federal'): return 'Leyes Federales'
    if t.startswith('Ley Orgánica'): return 'Leyes Orgánicas'
    if t.startswith('Ley Reglamentaria'): return 'Leyes Reglamentarias'
    if t.startswith('Reglamento'): return 'Reglamentos'
    if t.startswith('Ley'): return 'Otras leyes'
    return 'Otros ordenamientos'


ORDEN = ['Constitución', 'Códigos', 'Leyes Generales', 'Leyes Nacionales', 'Leyes Federales',
         'Leyes Orgánicas', 'Leyes Reglamentarias', 'Otras leyes', 'Reglamentos', 'Otros ordenamientos']
items = []
for r in rep:
    if 'error' in r:
        continue
    fn = r['id'] + '.epub'
    shutil.copy(os.path.join(src, fn), os.path.join(dst, 'federal', fn))
    d, m, y = r['reforma'].split('-')
    data = open(os.path.join(src, fn), 'rb').read()
    items.append({
        'id': 'federal/' + r['id'],
        'titulo': r['titulo'],
        'ambito': 'Federal',
        'categoria': categoria(r['titulo']),
        'fecha': f'{y}-{m}-{d}',
        'tipoFecha': 'ultima_reforma' if r['tipo'] == 'reforma' else 'publicacion',
        'version': f'{y}{m}{d}',
        'archivo': 'federal/' + fn,
        'bytes': len(data),
        'sha256': hashlib.sha256(data).hexdigest(),
        'articulos': r['articulos'],
    })
items.sort(key=lambda x: (ORDEN.index(x['categoria']), x['titulo']))
cat = {
    'formato': 1,
    'actualizado': '2026-11-24',
    'fuente': 'Cámara de Diputados del H. Congreso de la Unión — Leyes Federales Vigentes',
    'aviso': 'Textos de consulta. El único texto con validez oficial es el publicado en el Diario Oficial de la Federación.',
    'ambitos': ['Federal'],
    'categorias': [c for c in ORDEN if any(i['categoria'] == c for i in items)],
    'leyes': items,
}
json.dump(cat, open(os.path.join(dst, 'catalogo.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
from collections import Counter
print(len(items), Counter(i['categoria'] for i in items))
