# Convierte todos los PDF en paralelo (usa pdf2epub.py)
import sys, os, json, warnings
from multiprocessing import Pool
warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pdf2epub as P

src, dst = sys.argv[1], sys.argv[2]
os.makedirs(dst, exist_ok=True)


def one(f):
    title = os.path.splitext(f)[0]; ident = P.slug(title)
    try:
        data = P.build(os.path.join(src, f))
        if not data:
            return {'archivo': f, 'error': 'sin texto'}
        out = os.path.join(dst, ident + '.epub')
        na, nt = P.write_epub(data, out, ident)
        r = {'archivo': f, 'id': ident, 'titulo': title, 'reforma': data['reforma'], 'tipo': data['tipo'],
             'articulos': na, 'secciones': nt, 'imagenes': len(data['images']),
             'tablas': sum(p[0].startswith('<div class="tabla">') for p in data['parts']),
             'bytes': os.path.getsize(out), 'pdf_bytes': os.path.getsize(os.path.join(src, f))}
        print(f"{na:5d} art {r['tablas']:4d} tab {r['imagenes']:4d} img {r['bytes']//1024:6d} KB {title[:60]}", flush=True)
        return r
    except Exception as e:
        print('ERROR', f, e, flush=True)
        return {'archivo': f, 'error': repr(e)}


if __name__ == '__main__':
    files = sorted(f for f in os.listdir(src) if f.lower().endswith('.pdf'))
    # los más grandes primero para repartir mejor
    files.sort(key=lambda f: -os.path.getsize(os.path.join(src, f)))
    with Pool(int(os.environ.get('PROCS', '2'))) as pool:
        rep = pool.map(one, files, chunksize=1)
    json.dump(rep, open(os.path.join(dst, '_reporte.json'), 'w'), ensure_ascii=False, indent=1)
    print('LISTO', len(rep))
