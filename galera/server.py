#!/usr/bin/env python3
import http.server, socketserver, os, sys, json, threading, datetime, time, shutil, socket, urllib.request, urllib.error, urllib.parse, base64

PUERTO = 8000
CARPETA = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(CARPETA, 'db_galera.json')
FACTURAS_DIR = os.path.join(CARPETA, 'facturas')
BACKUPS_DIR = os.path.join(CARPETA, 'backups')
PRODUCTOS_DIR = os.path.join(CARPETA, 'productos')
os.makedirs(FACTURAS_DIR, exist_ok=True)
os.makedirs(BACKUPS_DIR, exist_ok=True)
os.makedirs(PRODUCTOS_DIR, exist_ok=True)

LOCK = threading.RLock()
DIRTY = False

MIME_EXTRA = {
    '.json': 'application/json; charset=utf-8',
    '.webmanifest': 'application/manifest+json; charset=utf-8',
    '.js': 'application/javascript; charset=utf-8',
    '.html': 'text/html; charset=utf-8',
    '.xml': 'application/xml; charset=utf-8',
    '.pdf': 'application/pdf'
}

FORMA_PAGO_MAP = {
    'Efectivo': '01',
    'Transferencia': '03',
    'Tarjeta': '04'
}

FEED_HEADER = ['id', 'title', 'description', 'availability', 'condition', 'price', 'link', 'image_link', 'brand']

def csv_escape(v):
    s = str(v if v is not None else '')
    if any(c in s for c in ',"\n'):
        return '"' + s.replace('"', '""') + '"'
    return s

def registrar_caja(tipo, concepto, monto, metodo='Efectivo', ref=''):
    with LOCK:
        MEM_DB.setdefault('caja', []).insert(0, {
            'id': int(datetime.datetime.now().timestamp() * 1000),
            'tipo': tipo,
            'concepto': concepto,
            'monto': round(float(monto), 2),
            'metodo': metodo,
            'ref': ref,
            'dia': datetime.datetime.now().strftime('%Y-%m-%d'),
            'ts': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        })

def default_db():
    return {
        "version": 1,
        "folio_consecutivo": 1,
        "updated_at": datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "inventario": [],
        "ventas": [],
        "pedidos": [],
        "caja": [],
        "mensajes": [],
        "config": {"integraciones": {}}
    }

def load_initial_db():
    with LOCK:
        if os.path.exists(DB_FILE):
            try:
                with open(DB_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    data.setdefault('folio_consecutivo', 1)
                    data.setdefault('version', 1)
                    data.setdefault('pedidos', [])
                    data.setdefault('caja', [])
                    data.setdefault('mensajes', [])
                    data.setdefault('config', {}).setdefault('integraciones', {})
                    return data
            except Exception:
                pass
        bks = sorted([x for x in os.listdir(BACKUPS_DIR) if x.endswith('.json')], reverse=True)
        for bk in bks:
            try:
                with open(os.path.join(BACKUPS_DIR, bk), 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                continue
        return default_db()

MEM_DB = load_initial_db()

def persist_to_disk(create_backup=False):
    global DIRTY
    with LOCK:
        MEM_DB['updated_at'] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        tmp = DB_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(MEM_DB, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, DB_FILE)
        DIRTY = False
        if create_backup:
            stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
            bk_path = os.path.join(BACKUPS_DIR, f"auto_bk_{stamp}.json")
            shutil.copy2(DB_FILE, bk_path)
            bks = sorted([x for x in os.listdir(BACKUPS_DIR) if x.startswith('auto_bk_')])
            for old in bks[:-30]:
                try: os.remove(os.path.join(BACKUPS_DIR, old))
                except Exception: pass

def autosave_worker():
    last_backup = time.time()
    while True:
        time.sleep(10)
        try:
            now = time.time()
            need_bk = (now - last_backup) >= 900
            with LOCK:
                if archivar_si_necesario(): DIRTY = True
                if DIRTY or need_bk:
                    persist_to_disk(create_backup=need_bk)
                    if need_bk: last_backup = now
        except Exception: pass


# ------------------------------------------------------------
# MEMORIA LOCAL EXPANSIBLE: archivado automatico por volumen
# La RAM y el JSON caliente se mantienen ligeros; el historial
# crece hacia archivos mensuales sin limite de capacidad.
# ------------------------------------------------------------
ARCHIVO_LIMITE = 500
ARCHIVOS_DIR = os.path.join(CARPETA, 'archivo')
os.makedirs(ARCHIVOS_DIR, exist_ok=True)

def fecha_reg(reg):
    return str(reg.get('ts') or reg.get('fecha') or (str(reg.get('dia', '')) + ' 00:00:00'))

def archivar_si_necesario():
    with LOCK:
        cambios = False
        for coleccion in ('ventas', 'caja', 'pedidos', 'mensajes'):
            datos = MEM_DB.get(coleccion, [])
            if len(datos) <= ARCHIVO_LIMITE:
                continue
            enviados = datos[ARCHIVO_LIMITE:]
            conservados = datos[:ARCHIVO_LIMITE]
            grupos = {}
            for reg in enviados:
                grupos.setdefault(fecha_reg(reg)[:7], []).append(reg)
            for mes, parte in grupos.items():
                path_mes = os.path.join(ARCHIVOS_DIR, f"{coleccion}_{mes}.json")
                existentes = []
                if os.path.exists(path_mes):
                    try:
                        with open(path_mes, 'r', encoding='utf-8') as f:
                            existentes = json.load(f)
                    except Exception:
                        existentes = []
                with open(path_mes, 'w', encoding='utf-8') as f:
                    json.dump(existentes + parte, f, ensure_ascii=False)
            MEM_DB[coleccion] = conservados
            cambios = True
        return cambios

def leer_archivo(coleccion, mes=None):
    resultado = []
    if not os.path.isdir(ARCHIVOS_DIR):
        return resultado
    for fname in sorted(os.listdir(ARCHIVOS_DIR)):
        if not fname.startswith(f"{coleccion}_") or not fname.endswith('.json'):
            continue
        if mes and mes not in fname:
            continue
        try:
            with open(os.path.join(ARCHIVOS_DIR, fname), 'r', encoding='utf-8') as f:
                resultado.extend(json.load(f))
        except Exception:
            continue
    return resultado

def estado_memoria():
    with LOCK:
        caliente = {c: len(MEM_DB.get(c, [])) for c in ('inventario', 'ventas', 'pedidos', 'caja', 'mensajes')}
    frio = {}
    if os.path.isdir(ARCHIVOS_DIR):
        for fname in sorted(os.listdir(ARCHIVOS_DIR)):
            try: frio[fname] = os.path.getsize(os.path.join(ARCHIVOS_DIR, fname))
            except Exception: pass
    total_mb = round(sum(os.path.getsize(os.path.join(ARCHIVOS_DIR, f)) for f in frio) / 1048576, 2) + round(os.path.getsize(DB_FILE) / 1048576 if os.path.exists(DB_FILE) else 0, 2)
    return {'caliente': caliente, 'archivo_frio': frio, 'total_mb': total_mb, 'limite_caliente': ARCHIVO_LIMITE}

def commit_db(increment_version=True, backup_now=False):
    global DIRTY
    with LOCK:
        if increment_version:
            MEM_DB['version'] = int(MEM_DB.get('version', 1)) + 1
        DIRTY = True
        persist_to_disk(create_backup=backup_now)
        return json.loads(json.dumps(MEM_DB))

def next_folio():
    with LOCK:
        folio = int(MEM_DB.get('folio_consecutivo', 1))
        MEM_DB['folio_consecutivo'] = folio + 1
        return folio

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def construir_cfdi40(venta, receptor, fcfg):
    rec_def = fcfg.get('receptor_default', {})
    rfc_rec = (receptor.get('rfc') or rec_def.get('rfc') or 'XAXX010101000').strip().upper()
    nom_rec = (receptor.get('nombre') or rec_def.get('nombre') or 'PUBLICO EN GENERAL').strip().upper()
    cp_rec = (receptor.get('cp') or rec_def.get('cp') or fcfg.get('emisor_cp') or '72050').strip()
    
    if rfc_rec == 'XAXX010101000':
        reg_rec = '616'
        uso_rec = 'S01'
    else:
        reg_rec = (receptor.get('regimen') or rec_def.get('regimen') or '601').strip()
        uso_rec = (receptor.get('uso_cfdi') or rec_def.get('uso_cfdi') or 'G03').strip()

    conceptos = []
    subtotal_sum = 0.0
    iva_sum = 0.0

    for it in venta.get('items', []):
        cant = float(it.get('cant', 1))
        precio_con_iva = float(it.get('precio', 0))
        valor_unit = round(precio_con_iva / 1.16, 6)
        importe = round(valor_unit * cant, 2)
        iva_item = round(importe * 0.16, 2)
        subtotal_sum += importe
        iva_sum += iva_item

        cat_lower = str(it.get('cat', '')).lower()
        if 'faro' in cat_lower: c_prod = '25172100'
        elif 'espejo' in cat_lower: c_prod = '25172500'
        elif 'parrilla' in cat_lower: c_prod = '25171905'
        else: c_prod = '25171900'

        conceptos.append({
            "ClaveProdServ": c_prod,
            "NoIdentificacion": str(it.get('id', 'REF')),
            "Cantidad": cant,
            "ClaveUnidad": "H87",
            "Unidad": "Pieza",
            "Descripcion": it.get('desc', 'Autoparte de colisión'),
            "ValorUnitario": f"{valor_unit:.6f}",
            "Importe": f"{importe:.2f}",
            "ObjetoImp": "02",
            "Impuestos": {
                "Traslados": [
                    {
                        "Base": f"{importe:.2f}",
                        "Impuesto": "002",
                        "TipoFactor": "Tasa",
                        "TasaOCuota": "0.160000",
                        "Importe": f"{iva_item:.2f}"
                    }
                ]
            }
        })

    subtotal_sum = round(subtotal_sum, 2)
    iva_sum = round(iva_sum, 2)
    total_cfdi = round(subtotal_sum + iva_sum, 2)
    fecha_iso = datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
    forma_pago = FORMA_PAGO_MAP.get(venta.get('metodo', 'Efectivo'), '01')
    folio_venta = venta.get('folio') or str(venta.get('id', '1'))[-6:]

    comprobante = {
        "Version": "4.0",
        "Serie": fcfg.get('serie', 'LG'),
        "Folio": str(folio_venta),
        "Fecha": fecha_iso,
        "FormaPago": forma_pago,
        "SubTotal": f"{subtotal_sum:.2f}",
        "Moneda": "MXN",
        "Total": f"{total_cfdi:.2f}",
        "TipoDeComprobante": "I",
        "Exportacion": "01",
        "MetodoPago": "PUE",
        "LugarExpedicion": fcfg.get('emisor_cp', '72050'),
        "Emisor": {
            "Rfc": fcfg.get('emisor_rfc', 'XAXX010101000'),
            "Nombre": fcfg.get('emisor_nombre', 'LA GALERA AUTOPARTES'),
            "RegimenFiscal": fcfg.get('emisor_regimen', '612')
        },
        "Receptor": {
            "Rfc": rfc_rec,
            "Nombre": nom_rec,
            "DomicilioFiscalReceptor": cp_rec,
            "RegimenFiscalReceptor": reg_rec,
            "UsoCFDI": uso_rec
        },
        "Conceptos": conceptos,
        "Impuestos": {
            "TotalImpuestosTrasladados": f"{iva_sum:.2f}",
            "Traslados": [
                {
                    "Base": f"{subtotal_sum:.2f}",
                    "Impuesto": "002",
                    "TipoFactor": "Tasa",
                    "TasaOCuota": "0.160000",
                    "Importe": f"{iva_sum:.2f}"
                }
            ]
        }
    }
    return comprobante

def construir_cfdi_global(ventas_dia, fcfg):
    subtotal_sum = 0.0
    iva_sum = 0.0
    total_sum = 0.0

    for v in ventas_dia:
        total_sum += float(v.get('total', 0))

    subtotal_global = round(total_sum / 1.16, 2)
    iva_global = round(total_sum - subtotal_global, 2)
    valor_unit = subtotal_global

    conceptos = [{
        "ClaveProdServ": "01010101",
        "NoIdentificacion": "VENTAS_GLOBAL",
        "Cantidad": 1.0,
        "ClaveUnidad": "ACT",
        "Unidad": "Actividad",
        "Descripcion": f"Venta global del dia {datetime.datetime.now().strftime('%Y-%m-%d')} - Publico en General",
        "ValorUnitario": f"{valor_unit:.2f}",
        "Importe": f"{subtotal_global:.2f}",
        "ObjetoImp": "02",
        "Impuestos": {
            "Traslados": [
                {
                    "Base": f"{subtotal_global:.2f}",
                    "Impuesto": "002",
                    "TipoFactor": "Tasa",
                    "TasaOCuota": "0.160000",
                    "Importe": f"{iva_global:.2f}"
                }
            ]
        }
    }]

    now = datetime.datetime.now()
    fecha_iso = now.strftime('%Y-%m-%dT%H:%M:%S')
    folio_venta = f"G{now.strftime('%d%H%M')}"

    comprobante = {
        "Version": "4.0",
        "Serie": fcfg.get('serie', 'LG'),
        "Folio": str(folio_venta),
        "Fecha": fecha_iso,
        "FormaPago": "01",
        "SubTotal": f"{subtotal_global:.2f}",
        "Moneda": "MXN",
        "Total": f"{round(subtotal_global + iva_global, 2):.2f}",
        "TipoDeComprobante": "I",
        "Exportacion": "01",
        "MetodoPago": "PUE",
        "LugarExpedicion": fcfg.get('emisor_cp', '72050'),
        "Emisor": {
            "Rfc": fcfg.get('emisor_rfc', 'XAXX010101000'),
            "Nombre": fcfg.get('emisor_nombre', 'LA GALERA AUTOPARTES'),
            "RegimenFiscal": fcfg.get('emisor_regimen', '612')
        },
        "Receptor": {
            "Rfc": "XAXX010101000",
            "Nombre": "PUBLICO EN GENERAL",
            "DomicilioFiscalReceptor": fcfg.get('emisor_cp', '72050'),
            "RegimenFiscalReceptor": "616",
            "UsoCFDI": "S01"
        },
        "InformacionGlobal": {
            "Periodicidad": "01",
            "Meses": now.strftime('%m'),
            "Año": str(now.year)
        },
        "Conceptos": conceptos,
        "Impuestos": {
            "TotalImpuestosTrasladados": f"{iva_global:.2f}",
            "Traslados": [
                {
                    "Base": f"{subtotal_global:.2f}",
                    "Impuesto": "002",
                    "TipoFactor": "Tasa",
                    "TasaOCuota": "0.160000",
                    "Importe": f"{iva_global:.2f}"
                }
            ]
        }
    }
    return comprobante

def timbrar_facturalo(comprobante, fcfg):
    apikey = (fcfg.get('apikey') or '').strip()
    if not apikey:
        return {"ok": False, "error": "Falta configurar el API Key de FacturaLo en Sync/Red."}
    url = (fcfg.get('url_prod') or 'https://sistema.facturaloplus.com/api/rest/servicio/timbrarJSON3').strip()
    form_data = urllib.parse.urlencode({
        "apikey": apikey,
        "jsonB64": base64.b64encode(json.dumps(comprobante, ensure_ascii=False).encode('utf-8')).decode('ascii')
    }).encode('utf-8')

    req = urllib.request.Request(url, data=form_data, headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "LaGaleraERP/6.0"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            raw = resp.read().decode('utf-8')
            data = json.loads(raw)
            code = str(data.get('code', ''))
            if code in ('200', '201') or data.get('uuid') or data.get('UUID'):
                uuid = data.get('uuid') or data.get('UUID') or data.get('data', {}).get('uuid', 'TIMBRADO')
                xml_str = data.get('xml') or data.get('XML') or data.get('data', {}).get('xml', '')
                return {"ok": True, "uuid": uuid, "xml": xml_str, "raw": data}
            msg = data.get('message') or data.get('mensaje') or raw
            return {"ok": False, "error": f"FacturaLo ({code}): {msg}"}
    except urllib.error.HTTPError as e:
        err_txt = e.read().decode('utf-8', errors='replace')
        return {"ok": False, "error": f"Error HTTP {e.code}: {err_txt}"}
    except Exception as e:
        return {"ok": False, "error": f"Error de conexión: {str(e)}"}


# ------------------------------------------------------------
# CONECTORES DE SALIDA (Meta / WhatsApp / Mercado Libre) 
# ------------------------------------------------------------
def http_json(url, data=None, headers=None, timeout=20):
    payload = json.dumps(data).encode('utf-8') if isinstance(data, (dict, list)) else data
    hdrs = {'Content-Type': 'application/json', 'User-Agent': 'LaGaleraERP/6.0'}
    if headers: hdrs.update(headers)
    req = urllib.request.Request(url, data=payload, headers=hdrs, method='POST' if payload is not None else 'GET')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {'ok': True, 'code': resp.status, 'data': json.loads(resp.read().decode('utf-8', errors='replace'))}
    except urllib.error.HTTPError as e:
        raw = e.read().decode('utf-8', errors='replace')
        try: err = json.loads(raw)
        except Exception: err = {'raw': raw}
        er = err.get('error')
        msg = er.get('message') if isinstance(er, dict) else (err.get('message') or raw)
        return {'ok': False, 'code': e.code, 'error': f'HTTP {e.code}: {msg}'}
    except Exception as e:
        return {'ok': False, 'error': f'Error de conexion: {str(e)}'}

def get_integraciones():
    with LOCK:
        return dict(MEM_DB.get('config', {}).get('integraciones', {}))

def whatsapp_send(icfg, tel, mensaje):
    token = (icfg.get('wa_token') or '').strip()
    phone_id = (icfg.get('wa_phone_id') or '').strip()
    if not token or not phone_id:
        return {'ok': False, 'error': 'Configura Token de WhatsApp Business y Phone Number ID en Integraciones.'}
    tel = ''.join(c for c in str(tel) if c.isdigit())
    url = f'https://graph.facebook.com/v21.0/{phone_id}/messages'
    body = {
        'messaging_product': 'whatsapp',
        'recipient_type': 'individual',
        'to': tel,
        'type': 'text',
        'text': {'preview_url': True, 'body': str(mensaje)[:4096]}
    }
    return http_json(url, data=body, headers={'Authorization': f'Bearer {token}'})

def armarMensajeVenta(venta):
    cfg = MEM_DB.get('config', {})
    lineas = ["\U0001F527 *La Galera Autopartes*\n\u2714 Pedido N\u00ba " + str(venta.get('folio')) + " recibido\n"]
    for it in venta.get('items', []):
        lineas.append(f"\u2022 {it.get('cant', 1)} x {it.get('desc', '')} \u2014 ${float(it.get('precio', 0)):.2f}")
    lineas.append(f"\n*Total: ${float(venta.get('total', 0)):.2f}* (IVA incluido)")
    lineas.append(f"\U0001F4CD {cfg.get('dir', 'Puebla')}")
    lineas.append(f"\U0001F4F1 {cfg.get('whats', '')}")
    return '\n'.join(lineas)

def meta_catalog_push(icfg, base_url):
    token = (icfg.get('meta_token') or '').strip()
    catalog_id = (icfg.get('meta_catalog_id') or '').strip()
    if not token or not catalog_id:
        return {'ok': False, 'error': 'Configura Token de Meta Commerce y Catalog ID.'}
    with LOCK:
        items = []
        for p in MEM_DB.get('inventario', [])[:5000]:
            items.append({
                'retailer_id': str(p.get('id', '')),
                'title': p.get('desc', 'Autoparte')[:100],
                'description': f"{p.get('cat', '')} - {p.get('desc', '')}. Refaccion de colision con ajuste exacto. Puebla, Mexico."[:5000],
                'availability': 'in stock' if int(p.get('stock', 0)) > 0 else 'out of stock',
                'condition': 'new',
                'price': int(round(float(p.get('precio', 0)) * 1000000)),
                'currency': 'MXN',
                'link': f"{base_url}/index.html#p{p.get('id', '')}",
                'image_link': f"{base_url}{p.get('img', '')}" if p.get('img') else f'{base_url}/productos/placeholder.png',
                'brand': p.get('prov', 'LA GALERA')[:100]
            })
    if not items:
        return {'ok': False, 'error': 'Catalogo vacio'}
    url = f'https://graph.facebook.com/v21.0/{catalog_id}/items_batch'
    return http_json(url, data={'access_token': token, 'item_type': 'AUTOMOTIVE', 'requests': items})

def meli_publicar(item, icfg):
    token = (icfg.get('meli_token') or '').strip()
    user_id = (icfg.get('meli_user_id') or '').strip()
    if not token or not user_id:
        return {'ok': False, 'error': 'Configura Token de Mercado Libre y User ID (developer.mercadolibre.com.mx).'}
    precio_sin_iva = round(float(item.get('precio', 0)) / 1.16, 2)
    pic = [{'source': item.get('img')}] if item.get('img') else []
    body = {
        'title': str(item.get('desc', 'Autoparte'))[:60],
        'category_id': (icfg.get('meli_categoria') or 'MLM1705').strip(),
        'price': max(1, int(round(precio_sin_iva))),
        'currency_id': 'MXN',
        'available_quantity': max(1, int(item.get('stock', 1))),
        'buying_mode': 'buy_it_now',
        'condition': 'new',
        'listing_type_id': (icfg.get('meli_listing') or 'gold_special').strip(),
        'description': {'plain_text': f"{item.get('cat', '')} - {item.get('desc', '')}. Refaccion de colision con ajuste exacto. La Galera Autopartes, Puebla."[:5000]},
        'pictures': pic or [{'source': 'https://http2.mlstatic.com/storage/mlapktor/places/homesquare.png'}],
        'attributes': [{'id': 'BRAND', 'value_name': item.get('prov', 'GENERICO')[:60]}]
    }
    return http_json('https://api.mercadolibre.com/items', data=body, headers={'Authorization': f'Bearer {token}'})

def partner_key_valido(payload_key):
    icfg = get_integraciones()
    key = (icfg.get('api_key') or '').strip()
    return bool(key) and str(payload_key or '') == key


# ------------------------------------------------------------
# IA Y AGENTES: asesor de negocio, reposicion, precios, contenido
# Funciona sin llave (agentes con reglas offline) y mejora con
# cualquier API compatible OpenAI/Mistral (config.ia_url/ia_key).
# ------------------------------------------------------------
def get_ia_cfg():
    with LOCK:
        c = MEM_DB.get('config', {}).get('ia', {}) or {}
    return {
        'url': (c.get('url') or 'https://api.mistral.ai/v1/chat/completions').strip(),
        'key': (c.get('key') or '').strip(),
        'model': (c.get('model') or 'mistral-small-latest').strip()
    }

def ia_chat(mensajes, max_tokens=900):
    cfg = get_ia_cfg()
    if not cfg['key']:
        return None
    body = {'model': cfg['model'], 'messages': mensajes, 'max_tokens': max_tokens, 'temperature': 0.4}
    res = http_json(cfg['url'], data=body, headers={'Authorization': f"Bearer {cfg['key']}"}, timeout=45)
    if res.get('ok'):
        try:
            return res['data']['choices'][0]['message']['content'].strip()
        except (KeyError, IndexError, TypeError):
            return None
    return None

def resumen_negocio():
    with LOCK:
        inv = json.loads(json.dumps(MEM_DB.get('inventario', [])))
        ventas = json.loads(json.dumps(MEM_DB.get('ventas', [])))[:200]
        pedidos = json.loads(json.dumps(MEM_DB.get('pedidos', [])))[:100]
    hoy = datetime.datetime.now().strftime('%Y-%m-%d')
    ventas_hoy = [v for v in ventas if v.get('dia') == hoy]
    total_hoy = round(sum(float(v.get('total', 0)) for v in ventas_hoy), 2)
    ticket = round(total_hoy / len(ventas_hoy), 2) if ventas_hoy else 0
    valor_inv = round(sum(float(p.get('precio', 0)) * int(p.get('stock', 0)) for p in inv), 2)
    sin_stock = [p.get('desc') for p in inv if int(p.get('stock', 0)) == 0]
    bajos = [f"{p.get('desc')} (quedan {p.get('stock')})" for p in inv if 0 < int(p.get('stock', 0)) <= 1]
    sin_foto = [p.get('desc') for p in inv if not p.get('img')]
    pendientes = [p for p in pedidos if p.get('estado') == 'nuevo']
    return {
        'hoy': hoy, 'ventas_hoy': len(ventas_hoy), 'total_hoy': total_hoy, 'ticket_promedio': ticket,
        'piezas': len(inv), 'valor_inventario': valor_inv,
        'sin_stock': sin_stock, 'stock_bajo': bajos, 'sin_foto': sin_foto,
        'pedidos_web_nuevos': len(pendientes),
        'total_mes': round(sum(float(v.get('total', 0)) for v in ventas), 2),
        'num_piezas_stock': sum(int(p.get('stock', 0)) for p in inv)
    }

def agente_reposicion(r):
    avisos = []
    if r['sin_stock']:
        avisos.append(f"[Reposicion] Reordena YA: {', '.join(r['sin_stock'][:5])} - piezas agotadas pierden ventas diarias.")
    if r['stock_bajo']:
        avisos.append(f"[Reposicion] Punto de reorden: {', '.join(r['stock_bajo'][:5])}.")
    if not avisos:
        avisos.append("[Reposicion] Inventario saludable: ninguna pieza en cero o punto de reorden.")
    return avisos

def agente_precios(r):
    avisos = []
    if r['total_hoy'] == 0 and r['piezas'] > 0:
        avisos.append("[Precios] Hoy sin ventas: considera un paquete 2x1 en faros/calaveras o 5% pagando de contado en efectivo.")
    elif r['ticket_promedio'] and r['ticket_promedio'] < 500:
        avisos.append("[Precios] Ticket promedio bajo: ofrece combo fascia + faro para subir el boleto hacia $1,500+.")
    if r['valor_inventario'] > 0 and r['total_mes'] == 0:
        avisos.append("[Precios] Sin ventas registradas este mes: revisa que el catalogo web tenga fotos y precios competitivos.")
    if not avisos:
        avisos.append("[Precios] Rendimiento de precios estable; revisa margen por familia cada quincena.")
    return avisos

def agente_canales(r):
    avisos = []
    if r['sin_foto']:
        avisos.append(f"[Canales] {len(r['sin_foto'])} piezas sin foto. El usuario decide 90% por imagen: fotografelas y subalas desde Inventario.")
    if r['pedidos_web_nuevos']:
        avisos.append(f"[Canales] Tiene {r['pedidos_web_nuevos']} pedido(s) web nuevos sin confirmar. Confirmelos para vender y descontar stock.")
    avisos.append("[Canales] Empuje semanal: publique el feed CSV en Meta Commerce y comparta 3 piezas en estados de WhatsApp.")
    return avisos

def agentes_analizar():
    r = resumen_negocio()
    ideas = agente_reposicion(r) + agente_precios(r) + agente_canales(r)
    return {'resumen': r, 'ideas': ideas, 'motor': 'agentes-locales'}

def ia_asesor(pregunta):
    r = resumen_negocio()
    contexto = json.dumps({'resumen': r, 'catalogo': r['piezas']}, ensure_ascii=False)
    respuesta_llm = ia_chat([
        {'role': 'system', 'content': f"Eres el asesor de negocio de la refaccionaria La Galera Autopartes (Puebla, Mexico). Datos actuales: {contexto}. Da respuestas breves, practicas y accionables para el dueno de la tienda en Mexico."},
        {'role': 'user', 'content': str(pregunta)[:2000]}
    ])
    if respuesta_llm:
        return {'respuesta': respuesta_llm, 'motor': 'ia-nube', 'resumen': r}
    pregunta_lower = str(pregunta).lower()
    ideas = agentes_analizar()
    if any(k in pregunta_lower for k in ('precio', 'cobrar', 'margen')):
        base = agente_precios(r)
    elif any(k in pregunta_lower for k in ('stock', 'reponer', 'pedido', 'comprar')):
        base = agente_reposicion(r)
    else:
        base = ideas['ideas']
    return {'respuesta': '\n'.join(base), 'motor': 'agentes-locales', 'resumen': r}

def ia_descripcion(pid):
    with LOCK:
        item = next((dict(x) for x in MEM_DB.get('inventario', []) if int(x.get('id', 0)) == pid), None)
    if not item:
        return {'ok': False, 'error': 'Pieza no encontrada'}
    descripcion_llm = ia_chat([
        {'role': 'system', 'content': 'Eres un redactor de marketplace de refacciones. Escribe una descripcion comercial en espanol, 2 frases maximo, sin inventar datos tecnicos.'},
        {'role': 'user', 'content': f"Producto: {item.get('desc', '')}. Categoria: {item.get('cat', '')}. Marca: {item.get('prov', '')}. Precio: ${item.get('precio', 0)} IVA incluido."}
    ], max_tokens=120)
    if not descripcion_llm:
        if item.get('prov') and item.get('prov') != 'GENERICO':
            descripcion_llm = f"{item.get('desc')} marca {item.get('prov')}, linea {item.get('cat')}. Pieza con ajuste exacto, en venta en La Galera Autopartes Puebla con ${item.get('precio')} IVA incluido."
        else:
            descripcion_llm = f"{item.get('desc')} linea {item.get('cat')}. Pieza con ajuste exacto, en venta en La Galera Autopartes Puebla con ${item.get('precio')} IVA incluido."
    return {'ok': True, 'id': pid, 'descripcion': descripcion_llm, 'motor': 'ia-nube' if get_ia_cfg()['key'] else 'agentes-locales'}

class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map, **MIME_EXTRA}
    def __init__(self, *a, **k): super().__init__(*a, directory=CARPETA, **k)

    def send_json(self, code, payload):
        raw = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        path = self.path.split('?')[0]
        if path == '/api/health':
            self.send_json(200, {"status": "alive", "version": MEM_DB.get("version", 1)})
            return
        if path == '/api/state':
            with LOCK: snap = json.loads(json.dumps(MEM_DB))
            snap['server_ip'] = get_local_ip()
            snap['server_port'] = PUERTO
            self.send_json(200, snap)
            return
        if path == '/api/public':
            with LOCK:
                pub = {
                    "version": MEM_DB.get("version", 1),
                    "updated_at": MEM_DB.get("updated_at", ""),
                    "inventario": MEM_DB.get("inventario", []),
                    "config": {
                        "whats": MEM_DB.get("config", {}).get("whats", "2225738872"),
                        "dir": MEM_DB.get("config", {}).get("dir", "Av. 44 Pte. 1703, Col. Cleotilde Torres, Puebla"),
                        "horario": MEM_DB.get("config", {}).get("horario", "Lun-Vie 9:00 - 18:00 · Sáb 9:00 - 14:00")
                    }
                }
            self.send_json(200, pub)
            return
        if path == '/api/feed.csv':
            self.handle_feed()
            return
        if path == '/api/whatsapp/webhook':
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            icfg = get_integraciones()
            if qs.get('hub.mode', [''])[0] == 'subscribe' and qs.get('hub.verify_token', [''])[0] == icfg.get('wa_verify', ''):
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.end_headers()
                self.wfile.write(qs.get('hub.challenge', [''])[0].encode('utf-8'))
            else:
                self.send_json(403, {"ok": False, "error": "Verificacion fallida (Token de verificacion incorrecto)"})
            return
        if path == '/api/partner/catalog':
            qk = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get('key', [''])[0]
            hk = self.headers.get('X-API-Key', '')
            if not partner_key_valido(hk or qk):
                self.send_json(401, {"ok": False, "error": "API Key invalida u omitida"})
                return
            with LOCK:
                catalogo = [{
                    'id': p.get('id'), 'cat': p.get('cat', ''), 'desc': p.get('desc', ''),
                    'precio': p.get('precio', 0), 'stock': p.get('stock', 0),
                    'prov': p.get('prov', ''), 'img': p.get('img', '')
                } for p in MEM_DB.get('inventario', [])]
            self.send_json(200, {"ok": True, "api_version": "1.0", "catalogo": catalogo})
            return
        super().do_GET()

    def do_POST(self):
        path = self.path.split('?')[0]
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length).decode('utf-8') if length else '{}'
        try: payload = json.loads(body)
        except Exception:
            self.send_json(400, {"error": "JSON inválido"})
            return

        if path == '/api/sync':
            with LOCK:
                if 'inventario' in payload: MEM_DB['inventario'] = payload['inventario']
                if 'ventas' in payload: MEM_DB['ventas'] = payload['ventas']
                if 'config' in payload: MEM_DB['config'] = payload['config']
                saved = commit_db(increment_version=True, backup_now=False)
            self.send_json(200, {"ok": True, "state": saved})
            return

        if path == '/api/venta':
            with LOCK:
                items = payload.get('items', [])
                cliente = payload.get('cliente', 'Mostrador')
                auto = payload.get('auto', '')
                metodo = payload.get('metodo', 'Efectivo')
                total = 0.0

                inv_map = {int(p['id']): p for p in MEM_DB.get('inventario', [])}
                for it in items:
                    pid = int(it['id'])
                    cant = int(it['cant'])
                    if pid in inv_map:
                        inv_map[pid]['stock'] = max(0, int(inv_map[pid]['stock']) - cant)
                        total += float(inv_map[pid]['precio']) * cant
                    else:
                        total += float(it.get('precio', 0)) * cant

                folio_asignado = next_folio()
                venta_reg = {
                    "id": int(datetime.datetime.now().timestamp() * 1000),
                    "folio": folio_asignado,
                    "fecha": datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
                    "dia": datetime.datetime.now().strftime('%Y-%m-%d'),
                    "cliente": cliente, "auto": auto, "metodo": metodo,
                    "items": items, "total": round(total, 2),
                    "facturado": False, "uuid": ""
                }
                MEM_DB.setdefault('ventas', []).insert(0, venta_reg)
                registrar_caja('venta', f"Venta #{folio_asignado} {cliente}", total, metodo, ref=str(venta_reg['id']))
                saved = commit_db(increment_version=True, backup_now=True)
            self.send_json(200, {"ok": True, "venta": venta_reg, "state": saved})
            return

        if path == '/api/facturar':
            with LOCK:
                venta_id = payload.get('venta_id')
                receptor = payload.get('receptor', {})
                venta = next((v for v in MEM_DB.get('ventas', []) if v.get('id') == venta_id), None)
                if not venta:
                    self.send_json(404, {"ok": False, "error": "Venta no encontrada"})
                    return
                if venta.get('facturado') and venta.get('uuid'):
                    self.send_json(400, {"ok": False, "error": f"Venta ya timbrada ({venta.get('uuid')})"})
                    return
                if venta.get('timbrando'):
                    self.send_json(400, {"ok": False, "error": "Timbrado en proceso..."})
                    return
                venta['timbrando'] = True
                fcfg = MEM_DB.get('config', {}).get('facturalo', {})
                cfdi = construir_cfdi40(venta, receptor, fcfg)

            try:
                res = timbrar_facturalo(cfdi, fcfg)
            finally:
                with LOCK: venta.pop('timbrando', None)

            if res.get('ok'):
                with LOCK:
                    uuid = res['uuid']
                    venta['facturado'] = True
                    venta['uuid'] = uuid
                    if res.get('xml'):
                        xml_path = os.path.join(FACTURAS_DIR, f"{uuid}.xml")
                        with open(xml_path, 'w', encoding='utf-8') as xf: xf.write(res['xml'])
                        venta['xml_url'] = f"/facturas/{uuid}.xml"
                    saved = commit_db(increment_version=True, backup_now=True)
                self.send_json(200, {"ok": True, "uuid": uuid, "xml_url": venta.get('xml_url', ''), "state": saved})
            else:
                self.send_json(400, {"ok": False, "error": res.get('error', 'Error desconocido')})
            return

        if path == '/api/facturar-global':
            with LOCK:
                dia_objetivo = payload.get('dia') or datetime.datetime.now().strftime('%Y-%m-%d')
                ventas_dia = [v for v in MEM_DB.get('ventas', []) if v.get('dia') == dia_objetivo and not v.get('facturado')]
                if not ventas_dia:
                    self.send_json(400, {"ok": False, "error": f"No hay ventas sin facturar para el día {dia_objetivo}."})
                    return
                fcfg = MEM_DB.get('config', {}).get('facturalo', {})
                cfdi_global = construir_cfdi_global(ventas_dia, fcfg)

            res = timbrar_facturalo(cfdi_global, fcfg)
            if res.get('ok'):
                with LOCK:
                    uuid = res['uuid']
                    for v in ventas_dia:
                        v['facturado'] = True
                        v['uuid'] = uuid + " (GLOBAL)"
                        if res.get('xml'):
                            xml_path = os.path.join(FACTURAS_DIR, f"{uuid}.xml")
                            with open(xml_path, 'w', encoding='utf-8') as xf: xf.write(res['xml'])
                            v['xml_url'] = f"/facturas/{uuid}.xml"
                    saved = commit_db(increment_version=True, backup_now=True)
                self.send_json(200, {"ok": True, "uuid": uuid, "state": saved})
            else:
                self.send_json(400, {"ok": False, "error": res.get('error', 'Error desconocido en Factura Global')})
            return

        if path == '/api/partner/stock':
            hk = self.headers.get('X-API-Key', '')
            if not partner_key_valido(hk or payload.get('key', '')):
                self.send_json(401, {"ok": False, "error": "API Key invalida u omitida"})
                return
            try: pid = int(payload.get('id', 0))
            except (TypeError, ValueError):
                self.send_json(400, {"ok": False, "error": "id invalido"})
                return
            with LOCK:
                p = next((x for x in MEM_DB.get('inventario', []) if int(x.get('id', 0)) == pid), None)
                if not p:
                    self.send_json(404, {"ok": False, "error": "Producto no encontrado"})
                    return
                if 'precio' in payload: p['precio'] = round(float(payload['precio']), 2)
                if 'stock' in payload: p['stock'] = max(0, int(payload['stock']))
                saved = commit_db(increment_version=True, backup_now=False)
            self.send_json(200, {"ok": True, "producto": p, "state": saved})
            return
        if path == '/api/integraciones':
            with LOCK:
                actuales = MEM_DB.setdefault('config', {}).setdefault('integraciones', {})
                for k in ('wa_token', 'wa_phone_id', 'wa_verify', 'meta_token', 'meta_catalog_id', 'meli_token', 'meli_user_id', 'meli_categoria', 'meli_listing', 'api_key'):
                    if k in payload: actuales[k] = str(payload[k]).strip()
                saved = commit_db(increment_version=True, backup_now=False)
            self.send_json(200, {"ok": True, "state": saved})
            return
        if path == '/api/whatsapp/send':
            res = whatsapp_send(get_integraciones(), payload.get('tel', ''), payload.get('mensaje', ''))
            code = 200 if res.get('ok') else 400
            self.send_json(code, res)
            return
        if path == '/api/whatsapp/notificar-venta':
            venta = next((v for v in MEM_DB.get('ventas', []) if v.get('id') == payload.get('venta_id')), None)
            if not venta:
                self.send_json(404, {"ok": False, "error": "Venta no encontrada"})
                return
            tel = payload.get('tel') or ''.join(c for c in (venta.get('cliente', '') if venta.get('cliente', '').replace(' ', '').isdigit() else '') if c.isdigit())
            if not payload.get('tel') and not venta.get('tel'):
                icfg = get_integraciones()
                tel_destino = icfg.get('wa_owner')
                mensaje = armarMensajeVenta(venta)
                res = whatsapp_send(icfg, tel_destino, mensaje) if (payload.get('tel') or icfg.get('wa_owner')) else {'ok': False, 'error': 'Sin numero de destino'}
                code = 200 if res.get('ok') else 400
                self.send_json(code, res)
                return
            res = whatsapp_send(get_integraciones(), payload.get('tel') or venta.get('tel'), armarMensajeVenta(venta))
            code = 200 if res.get('ok') else 400
            self.send_json(code, res)
            return
        if path == '/api/whatsapp/webhook':
            try:
                entry = payload.get('entry', [])[0].get('changes', [])[0].get('value', {})
                msgs = entry.get('messages', [])
            except (IndexError, AttributeError, KeyError, TypeError):
                msgs = []
            if msgs:
                with LOCK:
                    for m in msgs[:20]:
                        texto = ''
                        txt = m.get('text', {})
                        if isinstance(txt, dict): texto = txt.get('body', '')
                        MEM_DB.setdefault('mensajes', []).insert(0, {
                            'id': m.get('id', ''), 'de': m.get('from', ''),
                            'texto': str(texto)[:1000],
                            'ts': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        })
                    MEM_DB['mensajes'] = MEM_DB['mensajes'][:200]
                    commit_db(increment_version=False, backup_now=False)
            self.send_json(200, {"ok": True})
            return
        if path == '/api/meta/push':
            base = payload.get('base_url') or f"http://{self.headers.get('Host') or (get_local_ip() + ':' + str(PUERTO))}"
            res = meta_catalog_push(get_integraciones(), base.rstrip('/'))
            code = 200 if res.get('ok') else 400
            self.send_json(code, res)
            return
        if path == '/api/meli/publicar':
            try: pid = int(payload.get('id', 0))
            except (TypeError, ValueError):
                self.send_json(400, {"ok": False, "error": "id invalido"})
                return
            with LOCK:
                item = next((dict(x) for x in MEM_DB.get('inventario', []) if int(x.get('id', 0)) == pid), None)
            if not item:
                self.send_json(404, {"ok": False, "error": "Pieza no encontrada"})
                return
            if not item.get('img'):
                self.send_json(400, {"ok": False, "error": "La pieza no tiene foto. Mercado Libre la exige para publicar. Subela desde Inventario."})
                return
            res = meli_publicar(item, get_integraciones())
            code = 200 if res.get('ok') else 400
            self.send_json(code, res)
            return
        if path == '/api/ia/asesor':
            pregunta = str(payload.get('pregunta', '')).strip()
            if not pregunta:
                self.send_json(400, {"ok": False, "error": "Escribe una pregunta"})
                return
            self.send_json(200, ia_asesor(pregunta))
            return
        if path == '/api/ia/agentes':
            self.send_json(200, {"ok": True, **agentes_analizar()})
            return
        if path == '/api/ia/descripcion':
            try: pid = int(payload.get('id', 0))
            except (TypeError, ValueError):
                self.send_json(400, {"ok": False, "error": "id invalido"})
                return
            self.send_json(200, ia_descripcion(pid))
            return
        if path == '/api/ia/config':
            with LOCK:
                actuales = MEM_DB.setdefault('config', {}).setdefault('ia', {})
                for k in ('url', 'key', 'model'):
                    if k in payload: actuales[k] = str(payload[k]).strip()
                saved = commit_db(increment_version=True, backup_now=False)
            self.send_json(200, {"ok": True, "motor_actual": 'ia-nube' if get_ia_cfg()['key'] else 'agentes-locales', "state": saved})
            return
        if path == '/api/memoria':
            self.send_json(200, {"ok": True, **estado_memoria()})
            return
        if path == '/api/archivo':
            coleccion = payload.get('coleccion', 'ventas')
            if coleccion not in ('ventas', 'caja', 'pedidos', 'mensajes'):
                self.send_json(400, {"ok": False, "error": "Coleccion invalida"})
                return
            datos = leer_archivo(coleccion, payload.get('mes'))
            self.send_json(200, {"ok": True, "coleccion": coleccion, "registros": len(datos), "datos": datos[:300]})
            return
        if path == '/api/upload-img':
            self.handle_upload_img(payload)
            return
        if path == '/api/pedido':
            self.handle_pedido(payload)
            return
        if path == '/api/pedido-accion':
            self.handle_pedido_accion(payload)
            return
        if path == '/api/caja/mov':
            self.handle_caja_mov(payload)
            return
        if path == '/api/caja/corte':
            self.handle_caja_corte(payload)
            return
        self.send_json(404, {"error": "Ruta no encontrada"})

    def handle_feed(self):
        host = self.headers.get('Host') or f'{get_local_ip()}:{PUERTO}'
        base = f'http://{host}'
        with LOCK:
            rows = [(','.join(FEED_HEADER))]
            for p in MEM_DB.get('inventario', []):
                desc = p.get('desc', '')
                cat = p.get('cat', '')
                rows.append(','.join([
                    csv_escape(p.get('id', '')),
                    csv_escape(desc),
                    csv_escape(f"{cat} - {desc}. Refacción de colisión con ajuste exacto. Envío y entrega en Puebla."),
                    'in stock' if int(p.get('stock', 0)) > 0 else 'out of stock',
                    'new',
                    csv_escape(f"{float(p.get('precio', 0)):.2f} MXN"),
                    csv_escape(f"{base}/index.html#p{p.get('id', '')}"),
                    csv_escape(f"{base}{p.get('img', '')}" if p.get('img') else f"{base}/productos/placeholder.png"),
                    csv_escape(p.get('prov', 'LA GALERA'))
                ]))
        raw = ('\n'.join(rows) + '\n').encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/csv; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(raw)

    def handle_upload_img(self, payload):
        try:
            pid = int(payload.get('id', 0))
            data = (payload.get('data', '') or '').split(',', 1)[-1]
            img_bytes = base64.b64decode(data)
            if not img_bytes or len(img_bytes) > 4 * 1024 * 1024:
                raise ValueError('imagen inválida o mayor a 4MB')
        except (ValueError, TypeError) as e:
            self.send_json(400, {"ok": False, "error": f"Datos de imagen inválidos ({str(e)})"})
            return
        meta = None
        if img_bytes[:8] == b'\x89PNG\r\n\x1a\n': meta = ('png', '.png')
        elif img_bytes[:3] == b'\xff\xd8\xff': meta = ('jpg', '.jpg')
        elif img_bytes[:6] in (b'GIF87a', b'GIF89a'): meta = ('gif', '.gif')
        elif img_bytes[:4] == b'RIFF' and img_bytes[8:12] == b'WEBP': meta = ('webp', '.webp')
        else:
            self.send_json(400, {"ok": False, "error": "Formato no soportado (us JPG o PNG)"})
            return
        old = [f for f in os.listdir(PRODUCTOS_DIR) if f.startswith(f'p{pid}.')]
        for f in old:
            try: os.remove(os.path.join(PRODUCTOS_DIR, f))
            except Exception: pass
        fname = f"p{pid}{meta[1]}"
        with open(os.path.join(PRODUCTOS_DIR, fname), 'wb') as f:
            f.write(img_bytes)
        url = f"/productos/{fname}"
        with LOCK:
            for p in MEM_DB.get('inventario', []):
                if int(p.get('id', 0)) == pid:
                    p['img'] = url
                    break
            saved = commit_db(increment_version=True, backup_now=False)
        self.send_json(200, {"ok": True, "img": url, "state": saved})

    def handle_pedido(self, payload):
        items_raw = payload.get('items', [])
        if not isinstance(items_raw, list) or not items_raw:
            self.send_json(400, {"ok": False, "error": "Carrito vacío"})
            return
        with LOCK:
            inv_map = {int(p['id']): p for p in MEM_DB.get('inventario', [])}
            items, total, stock_ok = [], 0.0, []
            for it in items_raw:
                pid = int(it.get('id', 0))
                cant = max(1, int(it.get('cant', 1)))
                p = inv_map.get(pid)
                if not p:
                    self.send_json(400, {"ok": False, "error": f"Producto {pid} no existe"})
                    return
                if cant > int(p.get('stock', 0)):
                    stock_ok.append(p.get('desc', pid))
                    continue
                items.append({'id': pid, 'desc': p.get('desc', ''), 'precio': float(p.get('precio', 0)), 'cant': cant, 'cat': p.get('cat', '')})
                total += float(p.get('precio', 0)) * cant
            if stock_ok:
                self.send_json(409, {"ok": False, "error": "Stock insuficiente para: " + ', '.join(stock_ok)})
                return
            if not items:
                self.send_json(400, {"ok": False, "error": "No hay artículos disponibles"})
                return
            pedido = {
                'id': int(datetime.datetime.now().timestamp() * 1000),
                'nombre': (payload.get('nombre', '') or 'Cliente Web').strip()[:60],
                'tel': (payload.get('tel', '') or '').strip()[:15],
                'notas': (payload.get('notas', '') or '').strip()[:300],
                'items': items,
                'total': round(total, 2),
                'estado': 'nuevo',
                'dia': datetime.datetime.now().strftime('%Y-%m-%d'),
                'ts': datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
            }
            MEM_DB.setdefault('pedidos', []).insert(0, pedido)
            saved = commit_db(increment_version=True, backup_now=False)
        self.send_json(200, {"ok": True, "pedido": pedido, "state": saved})

    def handle_pedido_accion(self, payload):
        accion = payload.get('accion', '')
        with LOCK:
            ped = next((p for p in MEM_DB.get('pedidos', []) if p.get('id') == payload.get('id')), None)
            if not ped:
                self.send_json(404, {"ok": False, "error": "Pedido no encontrado"})
                return
            if ped.get('estado') != 'nuevo':
                self.send_json(400, {"ok": False, "error": f"Pedido ya procesado ({ped.get('estado')})"})
                return
            if accion == 'cancelar':
                ped['estado'] = 'cancelado'
                saved = commit_db(increment_version=True, backup_now=False)
                self.send_json(200, {"ok": True, "pedido": ped, "state": saved})
                return
            if accion != 'confirmar':
                self.send_json(400, {"ok": False, "error": "Acción inválida"})
                return
            folio_asignado = next_folio()
            venta_reg = {
                'id': int(datetime.datetime.now().timestamp() * 1000),
                'folio': folio_asignado,
                'fecha': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
                'dia': datetime.datetime.now().strftime('%Y-%m-%d'),
                'cliente': ped.get('nombre', 'Pedido Web'), 'auto': ped.get('notas', ''),
                'metodo': payload.get('metodo', 'Efectivo'),
                'items': ped.get('items', []), 'total': float(ped.get('total', 0)),
                'facturado': False, 'uuid': '', 'origen': 'web'
            }
            inv_map = {int(p['id']): p for p in MEM_DB.get('inventario', [])}
            for it in venta_reg['items']:
                if int(it['id']) in inv_map:
                    p = inv_map[int(it['id'])]
                    p['stock'] = max(0, int(p.get('stock', 0)) - int(it.get('cant', 1)))
            ped['estado'] = 'confirmado'
            ped['folio'] = folio_asignado
            MEM_DB.setdefault('ventas', []).insert(0, venta_reg)
            registrar_caja('venta', f"Venta web #{folio_asignado} {venta_reg['cliente']}", venta_reg['total'], venta_reg['metodo'], ref=str(venta_reg['id']))
            saved = commit_db(increment_version=True, backup_now=True)
        self.send_json(200, {"ok": True, "venta": venta_reg, "pedido": ped, "state": saved})

    def handle_caja_mov(self, payload):
        tipo = payload.get('tipo', '')
        if tipo not in ('ingreso', 'egreso'):
            self.send_json(400, {"ok": False, "error": "Tipo debe ser ingreso o egreso"})
            return
        try:
            monto = round(float(payload.get('monto', 0)), 2)
            assert monto > 0
        except (ValueError, TypeError, AssertionError):
            self.send_json(400, {"ok": False, "error": "Monto inválido"})
            return
        registrar_caja(tipo, (payload.get('concepto', '') or tipo.capitalize()).strip()[:120], monto, payload.get('metodo', 'Efectivo'))
        with LOCK:
            saved = commit_db(increment_version=True, backup_now=False)
        self.send_json(200, {"ok": True, "state": saved})

    def handle_caja_corte(self, payload):
        dia = payload.get('dia') or datetime.datetime.now().strftime('%Y-%m-%d')
        with LOCK:
            movs = [m for m in MEM_DB.get('caja', []) if m.get('dia') == dia]
            ventas_dia = [v for v in MEM_DB.get('ventas', []) if v.get('dia') == dia]
        resumen = {'dia': dia, 'ventas_total': 0.0, 'por_metodo': {'Efectivo': 0.0, 'Transferencia': 0.0, 'Tarjeta': 0.0},
                   'ingresos': 0.0, 'egresos': 0.0, 'num_ventas': 0, 'num_movs': len(movs)}
        for v in ventas_dia:
            resumen['ventas_total'] += float(v.get('total', 0))
            resumen['num_ventas'] += 1
            m = v.get('metodo', 'Efectivo')
            resumen['por_metodo'][m] = resumen['por_metodo'].get(m, 0.0) + float(v.get('total', 0))
        for m in movs:
            mt = float(m.get('monto', 0))
            if m.get('tipo') == 'venta':
                continue
            if m.get('tipo') == 'ingreso':
                resumen['ingresos'] += mt
                resumen['por_metodo'][m.get('metodo', 'Efectivo')] = resumen['por_metodo'].get(m.get('metodo', 'Efectivo'), 0.0) + mt
            elif m.get('tipo') == 'egreso':
                resumen['egresos'] += mt
                met = m.get('metodo', 'Efectivo')
                if met == 'Efectivo':
                    resumen['por_metodo']['Efectivo'] -= mt
        resumen['efectivo_esperado'] = round(resumen['por_metodo'].get('Efectivo', 0.0), 2)
        resumen['total_dia'] = round(resumen['ventas_total'] + resumen['ingresos'] - resumen['egresos'], 2)
        for k in ('ventas_total', 'ingresos', 'egresos'):
            resumen[k] = round(resumen[k], 2)
        resumen['por_metodo'] = {k: round(v, 2) for k, v in resumen['por_metodo'].items()}
        self.send_json(200, {"ok": True, "corte": resumen})

    def handle_img(self, payload):
        with LOCK:
            inv_map = {int(p['id']): p for p in MEM_DB.get('inventario', [])}
            pid = int(payload.get('id', 0))
            p = inv_map.get(pid)
        if not p:
            self.send_json(404, {"ok": False, "error": "Pieza no encontrada"})
            return
        old = [f for f in os.listdir(PRODUCTOS_DIR) if f.startswith(f'p{pid}.')]
        for f in old:
            try: os.remove(os.path.join(PRODUCTOS_DIR, f))
            except Exception: pass
        with LOCK:
            p['img'] = ''
            saved = commit_db(increment_version=True, backup_now=False)
        self.send_json(200, {"ok": True, "state": saved})

    def log_message(self, fmt, *args):
        sys.stdout.write('[%s] %s\n' % (self.log_date_time_string(), fmt % args))
        sys.stdout.flush()

def main():
    persist_to_disk(create_backup=True)
    threading.Thread(target=autosave_worker, daemon=True).start()
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    httpd = socketserver.ThreadingTCPServer(('0.0.0.0', PUERTO), Handler)
    ip = get_local_ip()
    print('=' * 58)
    print('  LA GALERA AUTOPARTES v6.0 - E-commerce + ERP + SAT')
    print('=' * 58)
    print(f'  Sitio Público    : http://localhost:{PUERTO}/index.html')
    print(f'  Panel ERP Táctil : http://localhost:{PUERTO}/app.html')
    print(f'  Red Local (WiFi) : http://{ip}:{PUERTO}/index.html')
    print('=' * 58)
    httpd.serve_forever()

if __name__ == '__main__':
    main()
