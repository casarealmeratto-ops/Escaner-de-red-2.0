#!/usr/bin/env python3
import http.server, socketserver, os, sys, json, threading, datetime, time, shutil, socket, re, zipfile, io, urllib.request, urllib.error, urllib.parse, base64, smtplib
from email.mime.text import MIMEText

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
        "banco": [],
        "fiscal_check": {},
        "envios": [],
        "resenas": [],
        "campanas": [],
        "contactos": [],
        "cotizaciones": [],
        "cotizacion_consec": 1,
        "config": {"integraciones": {}, "pagos": {}, "logistica": {}, "email": {}, "sms": {}}
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
                    data.setdefault('banco', [])
                    data.setdefault('fiscal_check', {})
                    data.setdefault('envios', [])
                    data.setdefault('resenas', [])
                    data.setdefault('campanas', [])
                    data.setdefault('contactos', [])
                    data.setdefault('cotizaciones', [])
                    data.setdefault('cotizacion_consec', 1)
                    data.setdefault('config', {}).setdefault('integraciones', {})
                    data.setdefault('config', {}).setdefault('logistica', {})
                    data.setdefault('config', {}).setdefault('email', {})
                    data.setdefault('config', {}).setdefault('sms', {})
                    data.setdefault('config', {}).setdefault('pagos', {})
                    data.setdefault('config', {}).setdefault('facturalo', {}).setdefault('tasa_iva', 0.16)
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
        for coleccion in ('ventas', 'caja', 'pedidos', 'mensajes', 'cotizaciones', 'envios', 'resenas', 'campanas'):
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

    tasa_iva = float(fcfg.get('tasa_iva', 0.16))
    divisor = 1 + tasa_iva

    for it in venta.get('items', []):
        cant = float(it.get('cant', 1))
        precio_con_iva = float(it.get('precio', 0))
        valor_unit = round(precio_con_iva / divisor, 6)
        importe = round(valor_unit * cant, 2)
        iva_item = round(importe * tasa_iva, 2)
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
                        "TasaOCuota": f"{tasa_iva:.6f}",
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
            "Nombre": fcfg.get('emisor_nombre', 'REFACCIONARIA LA GALERA'),
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
                    "TasaOCuota": f"{tasa_iva:.6f}",
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

    tasa_iva = float(fcfg.get('tasa_iva', 0.16))
    subtotal_global = round(total_sum / (1 + tasa_iva), 2)
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
                    "TasaOCuota": f"{tasa_iva:.6f}",
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
            "Nombre": fcfg.get('emisor_nombre', 'REFACCIONARIA LA GALERA'),
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
                    "TasaOCuota": f"{tasa_iva:.6f}",
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
    lineas = ["\U0001F527 *Refaccionaria La Galera*\n\u2714 Pedido N\u00ba " + str(venta.get('folio')) + " recibido\n"]
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
        'description': {'plain_text': f"{item.get('cat', '')} - {item.get('desc', '')}. Refaccion de colision con ajuste exacto. Refaccionaria La Galera, Puebla."[:5000]},
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
# ==============================================================
# v6.2 - ENT logistica + CX resenas/NPS + MKT-05 campanas + VTA-04 cotizaciones
# ==============================================================

UA_NAVEGADOR = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36'

PAQUETERIAS = {
    'Estandar': {'nombre': 'Estafeta (2-3 dias)', 'dias': '2-3', 'costo_base': 80.0, 'max_kg': 30,
                 'rastreo': 'https://rastreo.estafeta.com/Rastreo?guia={guia}'},
    'Express': {'nombre': 'DHL Express (1 dia)', 'dias': '1', 'costo_base': 150.0, 'max_kg': 20,
                'rastreo': 'https://www.dhl.com/mx-es/home/tracking.html?tracking-id={guia}'},
    '99minutos': {'nombre': '99minutos (mismo dia)', 'dias': 'mismo dia', 'costo_base': 95.0, 'max_kg': 15,
                  'rastreo': 'https://sistema.99minutos.com/rastreo/{guia}'},
    'FedEx': {'nombre': 'FedEx (2 dias)', 'dias': '2', 'costo_base': 130.0, 'max_kg': 68,
              'rastreo': 'https://www.fedex.com/fedextrack/?trknbr={guia}'}
}

ESTADOS_ENVIO = ('preparado', 'en_transito', 'entregado', 'rechazado')


def get_logistica_cfg():
    with LOCK:
        return dict(MEM_DB.setdefault('config', {}).setdefault('logistica', {}) or {})


# ==============================================================
# v6.2 - Seguridad de acceso (PIN + sesiones con expiracion)
# ==============================================================

PIN_INICIAL = '8888'
TTL_SESION = 12 * 3600
SESIONES = {}
INTENTOS_LOGIN = {'n': 0, 'bloqueo_hasta': 0}

RUTAS_PUBLICAS_GET = {
    '/api/health', '/api/public', '/api/feed.csv', '/api/seguridad',
    '/api/whatsapp/webhook', '/api/partner/catalog'
}
RUTAS_PUBLICAS_POST = {
    '/api/pedido', '/api/pagos/webhook', '/api/whatsapp/webhook',
    '/api/partner/stock', '/api/login'
}


def ruta_publica(path, metodo):
    return path in (RUTAS_PUBLICAS_GET if metodo == 'GET' else RUTAS_PUBLICAS_POST)


def get_pin():
    seg = MEM_DB.setdefault('config', {}).setdefault('seguridad', {})
    return str(seg.setdefault('pin', PIN_INICIAL)), seg.get('pin', PIN_INICIAL) == PIN_INICIAL


def iniciar_sesion(pin):
    ahora = time.time()
    if ahora < INTENTOS_LOGIN['bloqueo_hasta']:
        return {'ok': False, 'error': 'Demasiados intentos fallidos; espera ' + str(int((INTENTOS_LOGIN['bloqueo_hasta'] - ahora) // 60) + 1) + ' minuto(s)', 'bloqueo': True}
    pin_cfg, es_inicial = get_pin()
    if str(pin or '').strip() != pin_cfg:
        INTENTOS_LOGIN['n'] += 1
        if INTENTOS_LOGIN['n'] >= 5:
            INTENTOS_LOGIN['bloqueo_hasta'] = ahora + 300
            INTENTOS_LOGIN['n'] = 0
        return {'ok': False, 'error': 'PIN incorrecto' if not es_inicial else 'PIN incorrecto (el PIN inicial es ' + PIN_INICIAL + ')'}
    INTENTOS_LOGIN['n'] = 0
    token = base64.b64encode(os.urandom(18)).decode()
    with LOCK:
        SESIONES[token] = ahora
        for t in list(SESIONES):
            if ahora - SESIONES[t] > TTL_SESION:
                del SESIONES[t]
    return {'ok': True, 'token': token, 'expira_horas': TTL_SESION // 3600}


def token_valido(self):
    t = self.headers.get('X-Auth-Token', '') or ''
    if not t:
        return False
    with LOCK:
        ts = SESIONES.get(t)
    try:
        return bool(ts) and (time.time() - ts) < TTL_SESION
    except (TypeError, ValueError):
        return False


def cerrar_sesion(self):
    t = self.headers.get('X-Auth-Token', '') or ''
    with LOCK:
        SESIONES.pop(t, None)


def pedir_pin(payload):
    pin_actual, _ = get_pin()
    if str(payload.get('pin_actual', '')) != pin_actual:
        return {'ok': False, 'error': 'PIN actual incorrecto'}
    nuevo = str(payload.get('pin_nuevo', '')).strip()
    if not nuevo.isdigit() or len(nuevo) < 4 or len(nuevo) > 8:
        return {'ok': False, 'error': 'El PIN nuevo debe ser de 4 a 8 digitos'}
    with LOCK:
        MEM_DB.setdefault('config', {}).setdefault('seguridad', {})['pin'] = nuevo
        saved = commit_db(increment_version=True, backup_now=False)
    return {'ok': True, 'state': saved}


def recomendar_paqueteria(peso_kg=1.0, urgente=False):
    try:
        peso_kg = max(0.1, float(peso_kg))
    except (TypeError, ValueError):
        peso_kg = 1.0
    opciones = []
    for clave, p in PAQUETERIAS.items():
        if peso_kg > float(p.get('max_kg', 30)):
            continue
        opciones.append({'clave': clave, 'nombre': p['nombre'], 'dias': p['dias'],
                         'costo': p.get('costo_base', 0.0), 'max_kg': p.get('max_kg', 30)})
    opciones.sort(key=lambda o: o['costo'])
    recomendada = opciones[0]['clave'] if opciones else None
    if urgente and opciones:
        rapidos = [o for o in opciones if o['dias'] in ('1', 'mismo dia')]
        recomendada = rapidos[0]['clave'] if rapidos else opciones[0]['clave']
    return {'peso_kg': round(peso_kg, 2), 'recomendada': recomendada, 'opciones': opciones}


def rastrear_guia(paqueteria, guia):
    p = PAQUETERIAS.get(paqueteria)
    if not p:
        return {'ok': False, 'error': 'Paqueteria no soportada', 'url': ''}
    url = p['rastreo'].format(guia=urllib.parse.quote(str(guia)))
    codigo = None
    try:
        req = urllib.request.Request(url, headers={'User-Agent': UA_NAVEGADOR, 'Accept-Language': 'es-MX,es'})
        with urllib.request.urlopen(req, timeout=8) as r:
            codigo = r.status
    except Exception as e:
        codigo = 'sin_respuesta'
    return {'ok': True, 'paqueteria': paqueteria, 'guia': str(guia), 'url': url,
            'http': codigo, 'nota': 'Si el sitio de la paqueteria bloquea peticiones automaticas, abre la URL directamente'}


def contactos_segmento(segmento='todos'):
    with LOCK:
        contactos = [dict(c) for c in MEM_DB.get('contactos', [])]
    segmento = str(segmento or 'todos')
    if segmento == 'abandonados':
        tels = {p.get('tel', '') for p in carritos_abandonados(24) if p.get('tel', '')}
        return [dict(c, **{'motivo': 'carrito abandonado'}) for c in contactos if c.get('tel', '') in tels]
    if segmento == 'compradores':
        with LOCK:
            tels = {p.get('tel', '') for p in MEM_DB.get('pedidos', []) if p.get('estado') == 'confirmado' and p.get('tel', '')}
        return [dict(c, **{'motivo': 'comprador'}) for c in contactos if c.get('tel', '') in tels]
    return contactos


def carritos_abandonados(horas=24):
    try:
        horas = max(0, int(horas))
    except (TypeError, ValueError):
        horas = 24
    limite = datetime.datetime.now() - datetime.timedelta(hours=horas)
    out = []
    with LOCK:
        for p in MEM_DB.get('pedidos', []):
            if p.get('estado') != 'nuevo':
                continue
            try:
                ts = datetime.datetime.strptime(p.get('ts', ''), '%Y-%m-%d %H:%M')
            except (ValueError, TypeError):
                continue
            if ts < limite:
                out.append(p)
    return out


def nps_resumen(lista):
    respondidas = [r for r in lista if r.get('score') is not None]
    n = len(respondidas)
    if not n:
        return {'total': len(lista), 'respondidas': 0, 'nps': None, 'promedio': None}
    promotores = sum(1 for r in respondidas if int(r.get('score', 0)) >= 9)
    detractores = sum(1 for r in respondidas if int(r.get('score', 0)) <= 6)
    pasivos = n - promotores - detractores
    promedio = sum(int(r.get('score', 0)) for r in respondidas) / float(n)
    return {'total': len(lista), 'respondidas': n, 'promotores': promotores,
            'pasivos': pasivos, 'detractores': detractores,
            'nps': round((promotores - detractores) * 100.0 / n), 'promedio': round(promedio, 2)}


def get_email_cfg():
    with LOCK:
        return dict(MEM_DB.setdefault('config', {}).setdefault('email', {}) or {})


def get_sms_cfg():
    with LOCK:
        return dict(MEM_DB.setdefault('config', {}).setdefault('sms', {}) or {})


def enviar_email_smtp(para, asunto, texto):
    ecfg = get_email_cfg()
    para = (para or '').strip()
    if not (ecfg.get('host') and ecfg.get('user') and para):
        return {'ok': False, 'error': 'SMTP sin configurar (Config > Email/SMS) o destinatario vacio'}
    try:
        msg = MIMEText(texto, 'plain', 'utf-8')
        msg['Subject'] = asunto or 'Refaccionaria La Galera'
        msg['From'] = ecfg.get('remitente') or ecfg.get('user')
        msg['To'] = para
        puerto = int(ecfg.get('port') or 587)
        if ecfg.get('ssl') in ('1', 'true', 'True'):
            srv = smtplib.SMTP_SSL(ecfg['host'], puerto, timeout=15)
        else:
            srv = smtplib.SMTP(ecfg['host'], puerto, timeout=15)
            srv.ehlo()
            srv.starttls()
            srv.ehlo()
        srv.login(ecfg['user'], ecfg.get('pass', ''))
        srv.sendmail(msg['From'], [para], msg.as_string())
        srv.quit()
        return {'ok': True}
    except Exception as e:
        return {'ok': False, 'error': str(e)[:200]}


def enviar_sms_gateway(tel, texto):
    scfg = get_sms_cfg()
    url = (scfg.get('webhook') or '').strip()
    tel = (tel or '').strip()
    if not (url and tel):
        return {'ok': False, 'error': 'SMS sin configurar (Config > Email/SMS) o telefono vacio'}
    try:
        datos = json.dumps({'to': tel, 'text': texto, 'mensaje': texto}).encode('utf-8')
        req = urllib.request.Request(url, data=datos, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=12) as r:
            codigo = r.status
        return {'ok': True, 'codigo': codigo}
    except Exception as e:
        return {'ok': False, 'error': str(e)[:200]}


def texto_recuperacion(pedido):
    tel = (pedido.get('tel', '') or '').replace('+', '')
    total = float(pedido.get('total', 0))
    piezas = ', '.join(('%s x%s' % (it.get('desc', ''), it.get('cant', 1))) for it in pedido.get('items', []))
    texto = ('Hola %s, vemos que dejaste tu pedido: %s (total $%.2f) pendiente. '
             'Te apartamos las piezas por 24 h. Respondeme ENVIAR y te lo despacho hoy. '
             'Refaccionaria La Galera, Puebla.' % (pedido.get('nombre', ''), piezas or 'refacciones', total))
    return {'tel': tel, 'texto': texto, 'link': 'https://wa.me/%s?text=%s' % (tel, urllib.parse.quote(texto)) if tel else ''}


def crear_cotizacion(payload):
    with LOCK:
        items_in = payload.get('items', [])
        if not isinstance(items_in, list) or not items_in:
            return {'ok': False, 'error': 'Cotizacion sin itens'}
        inv_map = {int(p['id']): p for p in MEM_DB.get('inventario', [])}
        items = []
        for it in items_in:
            try:
                pid = int(it.get('id') or 0)
            except (TypeError, ValueError):
                pid = 0
            cant = max(1, int(it.get('cant', it.get('qty', 1)) or 1))
            p = inv_map.get(pid) if pid else None
            precio = float(it.get('precio') or (p.get('precio', 0) if p else 0) or 0)
            desc = (it.get('desc') or (p.get('desc', '') if p else 'item manual')).strip()[:120]
            if precio <= 0:
                return {'ok': False, 'error': 'Item sin precio: ' + desc}
            items.append({'id': pid, 'desc': desc, 'precio': round(precio, 2), 'cant': cant})
        try:
            descuento_pct = min(50.0, max(0.0, float(payload.get('descuento_pct', 0) or 0)))
        except (TypeError, ValueError):
            descuento_pct = 0.0
        try:
            validez_dias = max(1, int(payload.get('validez_dias', 15) or 15))
        except (TypeError, ValueError):
            validez_dias = 15
        tasa_iva = float(MEM_DB.get('config', {}).get('facturalo', {}).get('tasa_iva', 0.16) or 0.16)
        subtotal = sum(i['precio'] * i['cant'] for i in items)
        descuento_monto = round(subtotal * descuento_pct / 100.0, 2)
        base = subtotal - descuento_monto
        iva = round(base * tasa_iva, 2)
        total = round(base + iva, 2)
        hoy = datetime.datetime.now()
        consec = int(MEM_DB.get('cotizacion_consec', 1) or 1)
        MEM_DB['cotizacion_consec'] = consec + 1
        cot = {
            'folio': 'COT-%s-%04d' % (hoy.strftime('%Y%m%d'), consec),
            'ts': hoy.strftime('%Y-%m-%d %H:%M'),
            'fecha': hoy.strftime('%Y-%m-%d'),
            'validez_hasta': (hoy + datetime.timedelta(days=validez_dias)).strftime('%Y-%m-%d'),
            'cliente': (payload.get('cliente', '') or 'Cliente B2B').strip()[:80],
            'empresa': (payload.get('empresa', '') or '').strip()[:80],
            'tel': (payload.get('tel', '') or '').strip()[:15],
            'items': items, 'descuento_pct': descuento_pct, 'descuento_monto': descuento_monto,
            'tasa_iva': tasa_iva, 'subtotal': round(subtotal, 2), 'iva': iva, 'total': total,
            'notas': (payload.get('notas', '') or '').strip()[:300],
            'estado': 'vigente'
        }
        MEM_DB.setdefault('cotizaciones', []).insert(0, cot)
        saved = commit_db(increment_version=True, backup_now=False)
    return {'ok': True, 'cotizacion': cot, 'state': saved}

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
        {'role': 'system', 'content': f"Eres el asesor de negocio de la refaccionaria Refaccionaria La Galera (Puebla, Mexico). Datos actuales: {contexto}. Da respuestas breves, practicas y accionables para el dueno de la tienda en Mexico."},
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
            descripcion_llm = f"{item.get('desc')} marca {item.get('prov')}, linea {item.get('cat')}. Pieza con ajuste exacto, en venta en Refaccionaria La Galera Puebla con ${item.get('precio')} IVA incluido."
        else:
            descripcion_llm = f"{item.get('desc')} linea {item.get('cat')}. Pieza con ajuste exacto, en venta en Refaccionaria La Galera Puebla con ${item.get('precio')} IVA incluido."
    return {'ok': True, 'id': pid, 'descripcion': descripcion_llm, 'motor': 'ia-nube' if get_ia_cfg()['key'] else 'agentes-locales'}


# ------------------------------------------------------------
# PASARELA DE PAGOS (Mercado Pago) + BANCO + CONCILIACIÓN
# ------------------------------------------------------------
def get_pagos_cfg():
    with LOCK:
        return dict(MEM_DB.get('config', {}).get('pagos', {}) or {})

def mp_preferencia(pedido, pcfg, base_url):
    token = (pcfg.get('mp_token') or '').strip()
    if not token:
        return {'ok': False, 'error': 'Configura tu Access Token de Mercado Pago en Config > Pagos.'}
    items = [{
        'title': f"{it.get('desc', 'Autoparte')} x{it.get('cant', 1)}"[:250],
        'quantity': max(1, int(it.get('cant', 1))),
        'unit_price': round(float(it.get('precio', 0)), 2),
        'currency_id': 'MXN'
    } for it in pedido.get('items', [])]
    if not items:
        return {'ok': False, 'error': 'Pedido sin artículos'}
    body = {
        'items': items,
        'external_reference': str(pedido.get('id', '')),
        'back_urls': {
            'success': f'{base_url}/index.html?pagado=1',
            'pending': f'{base_url}/index.html?pagado=0',
            'failure': f'{base_url}/index.html?pagado=0'
        },
        'auto_return': 'approved',
        'statement_descriptor': 'LA GALERA'
    }
    return http_json('https://api.mercadopago.com/checkout/preferences', data=body,
                     headers={'Authorization': f'Bearer {token}'}, timeout=25)

def mp_pago_detalle(payment_id, pcfg):
    token = (pcfg.get('mp_token') or '').strip()
    if not token:
        return {'ok': False, 'error': 'Token de Mercado Pago no configurado'}
    return http_json(f'https://api.mercadopago.com/v1/payments/{payment_id}',
                     headers={'Authorization': f'Bearer {token}'}, timeout=25)

def registrar_pago_aprobado(pago, ref):
    monto = float(pago.get('transaction_amount') or 0)
    metodo = 'Transferencia' if str(pago.get('payment_type_id', '')) == 'bank_transfer' else 'Tarjeta'
    with LOCK:
        ped = next((p for p in MEM_DB.get('pedidos', []) if str(p.get('id', '')) == str(ref)), None)
        if ped and ped.get('estado') in ('nuevo', 'pagado'):
            ped['estado'] = 'pagado'
            ped['pago'] = {'id': str(pago.get('id', '')), 'monto': monto, 'metodo': metodo,
                           'ts': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
        ya = next((m for m in MEM_DB.get('caja', []) if m.get('ref') == f"MP-{pago.get('id', '')}"), None)
        if not ya:
            registrar_caja('ingreso', f'Cobro Mercado Pago ref {ref}', monto, metodo, ref=f"MP-{pago.get('id', '')}")
        return commit_db(increment_version=True, backup_now=True)

def conciliar_banco(dias=7):
    with LOCK:
        banco = json.loads(json.dumps(MEM_DB.get('banco', [])))
        movs = json.loads(json.dumps(MEM_DB.get('caja', []))) + json.loads(json.dumps(MEM_DB.get('ventas', [])))
    parejas, sin_match = [], []
    usados = set()
    pivotes = []
    for m in movs:
        pivotes.append({'m': m, 'clave': f"{m.get('tipo', 'v')}-{m.get('ref') or m.get('id') or m.get('folio')}",
                        'fecha': str(m.get('dia', ''))[:10], 'monto': float(m.get('monto', m.get('total', 0)) or 0)})
    for b in banco:
        if b.get('conciliado'):
            parejas.append(b)
            continue
        mejor = None
        for p in pivotes:
            if p['clave'] in usados or abs(p['monto'] - float(b.get('monto', 0) or 0)) > 0.005:
                continue
            try:
                delta = abs((datetime.date.fromisoformat(str(b.get('fecha', ''))[:10]) - datetime.date.fromisoformat(p['fecha'])).days) if p['fecha'] and b.get('fecha') else 99
            except (ValueError, TypeError):
                delta = 99
            if delta > dias:
                continue
            folio_en_concepto = re.search(r'(\d{1,6})', f"{b.get('concepto', '')} {b.get('referencia', '')}")
            folio = str(p['m'].get('folio', ''))
            si_folio = folio and folio_en_concepto and folio_en_concepto.group(1) == folio
            puntaje = delta - (10 if si_folio else 0)
            if mejor is None or puntaje < mejor[0]:
                mejor = (puntaje, p)
        if mejor:
            usados.add(mejor[1]['clave'])
            parejas.append({**b, 'conciliado': True, 'contra': mejor[1]['clave'], 'movimiento': mejor[1]['m']})
        else:
            sin_match.append(b)
    with LOCK:
        cambios = 0
        for b in parejas:
            if not b.get('conciliado'):
                continue
            for reg in MEM_DB.get('banco', []):
                if reg.get('id') == b.get('id'):
                    reg['conciliado'] = True
                    reg['contra'] = b.get('contra'); break
            contra = b.get('contra') or ''
            for reg in MEM_DB.get('ventas', []) + MEM_DB.get('caja', []):
                rk = f"{reg.get('tipo', 'v')}-{reg.get('ref') or reg.get('id') or reg.get('folio')}"
                if contra == rk:
                    if not reg.get('conciliado'):
                        reg['conciliado'] = True
                        cambios += 1
                    break
        if cambios:
            commit_db(increment_version=True, backup_now=False)
    return {'ok': True, 'conciliados': len([b for b in parejas if b.get('conciliado')]),
            'sin_match': len(sin_match), 'pendientes': sin_match[:80]}

def validar_fiscales():
    cfg = MEM_DB.get('config', {}).get('facturalo', {})
    avisos, ok = [], True
    rfc = (cfg.get('emisor_rfc', '') or '').strip().upper()
    if not re.match(r'^[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}$', rfc):
        avisos.append(f'❌ RFC emisor inválido: "{rfc}" (formato AAAA000000XXX)')
        ok = False
    regimenes = {'601','603','605','606','607','608','609','610','611','612','614','615','616','620','621','622','623','624','625','626'}
    regimen = str(cfg.get('emisor_regimen', ''))
    if regimen not in regimenes:
        avisos.append(f'⚠ Régimen Fiscal "{regimen}" no está en el catálogo CFDI 4.0 vigente')
        ok = False
    if rfc.startswith(('XAXX', 'XEXX')) and regimen != '616':
        avisos.append('⚠ RFC genérico (XAXX010101000/XEXX010101000) requiere Régimen 616')
    receptor = cfg.get('receptor_default', {}) or {}
    uso = str(receptor.get('uso_cfdi', ''))
    usos = {'S01','G01','G02','G03','I01','I02','I03','I04','I05','I06','I07','I08','D01','D02','D03','D04','D05','D06','D07','D08','D09','D10','P01'}
    if uso not in usos:
        avisos.append(f'⚠ UsoCFDI "{uso}" fuera del catálogo 4.0 (S01 = sin efectos fiscales / público general)')
        ok = False
    cp = str(cfg.get('emisor_cp', ''))
    if not (re.match(r'^\d{5}$', cp) and cp != '00000'):
        avisos.append(f'⚠ Código Postal "{cp}" inválido (5 dígitos)')
        ok = False
    serie = str(cfg.get('serie', ''))
    if not re.match(r'^[A-Za-z0-9_-]{1,25}$', serie):
        avisos.append('⚠ Serie inválida (máx 25, alfanumérica)')
        ok = False
    tasa = float(cfg.get('tasa_iva', 0.16))
    if tasa <= 0 or tasa > 1:
        avisos.append('⚠ tasa_iva fuera de rango (0 < tasa <= 1). Si hubo reforma fiscal, actualízala en Config')
        ok = False
    elif abs(tasa - 0.16) > 0.0001 and abs(tasa - 0.08) > 0.0001:
        avisos.append('⚠ tasa_iva inusual (' + str(tasa) + '). Vigentes 2026: 0.16 general (zona centro, ej. Puebla) y 0.08 en región fronteriza norte/sur (estímulo, fichas 3/DEC-5 y 4/DEC-5 del Anexo 2 de la RMF, vigentes hasta 31-dic-2026). Verifica en SAT/DOF')
    if not (cfg.get('url_prod') or '').startswith('https://'):
        avisos.append('⚠ URL del PAC debe ser HTTPS')
        ok = False
    return {'ok': ok, 'avisos': avisos or ['✔ Configuración fiscal coherente con CFDI 4.0 y catálogos vigentes (validación local)']}

FUENTES_FISCALES = [
    ('SAT - Novedades', 'https://www.sat.gob.mx/novedades'),
    ('SAT - Anexo 20 (CFDI)', 'https://www.sat.gob.mx/consultas/860/comprobante-fiscal-digital-a-traves-de-internet-cfdi'),
    ('SAT - Miscelánea Fiscal 2026', 'https://www.sat.gob.mx/minisitio/NormatividadRMFyRGCE'),
    ('DOF - Edición del día', 'https://dof.gob.mx/'),
    ('DOF - Búsqueda: estímulo IVA frontera', 'https://dof.gob.mx/busqueda_detalle.php?texto=estimulo+fiscal+IVA+region+fronteriza')
]

def buscar_reformas():
    fuentes = FUENTES_FISCALES
    palabras = ['reforma', 'fiscal', 'cfdi', 'anexo 20', 'comercio exterior', 'iva', 'timbrado', 'carta porte']
    hallados = []
    for nombre, url in fuentes:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36'})
            with urllib.request.urlopen(req, timeout=25) as resp:
                html = resp.read().decode('utf-8', errors='replace')
            conectado = True
        except Exception as e:
            hallados.append({'fuente': nombre, 'error': f'No se pudo conectar: {str(e)[:90]}'})
            continue
        cnt = 0
        for m in re.finditer(r'<a[^>]*href="([^"]+)"[^>]*>([^<]{20,220})</a>', html):
            href, titulo = m.group(1).strip(), ' '.join(m.group(2).split())
            tl = titulo.lower()
            if any(p in tl for p in palabras):
                if href.startswith('/'):
                    href = ('https://dof.gob.mx' if nombre.startswith('DOF') else url.rstrip('/')) + href
                hallados.append({'fuente': nombre, 'titulo': titulo[:180], 'url': href})
                cnt += 1
                if cnt >= 15:
                    break
        if not cnt:
            hallados.append({'fuente': nombre, 'titulo': 'Conectado sin novedades fiscales visibles en portada', 'url': url})
    unicos, ya = [], set()
    for h in hallados:
        k = h.get('url') or (h.get('fuente', '') + h.get('titulo', ''))
        if k in ya:
            continue
        ya.add(k)
        unicos.append(h)
    with LOCK:
        MEM_DB['fiscal_check'] = {'ts': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'resultados': unicos[:60]}
        commit_db(increment_version=False, backup_now=False)
    return unicos[:60]

def crear_respaldo_zip():
    bufferx = io.BytesIO()
    with zipfile.ZipFile(bufferx, 'w', zipfile.ZIP_DEFLATED) as z:
        if os.path.exists(DB_FILE):
            z.write(DB_FILE, 'db_galera.json')
        if os.path.isdir(PRODUCTOS_DIR):
            for a in os.listdir(PRODUCTOS_DIR):
                pa = os.path.join(PRODUCTOS_DIR, a)
                if os.path.isfile(pa):
                    z.write(pa, f'productos/{a}')
        if os.path.isdir(ARCHIVOS_DIR):
            for a in os.listdir(ARCHIVOS_DIR):
                pa = os.path.join(ARCHIVOS_DIR, a)
                if os.path.isfile(pa):
                    z.write(pa, f'archivo/{a}')
        if os.path.isdir(FACTURAS_DIR):
            for a in os.listdir(FACTURAS_DIR):
                pa = os.path.join(FACTURAS_DIR, a)
                if os.path.isfile(pa):
                    z.write(pa, f'facturas/{a}')
        z.writestr('INFO.txt', f'Respaldo La Galera v6.0\nGenerado: {datetime.datetime.now().isoformat()}\nIncluye: base, fotos, archivo historico y facturas.\n')
    return bufferx.getvalue()
def fiscal_worker():
    time.sleep(3)
    while True:
        try:
            buscar_reformas()
        except Exception:
            pass
        time.sleep(86400)

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
        if path.startswith('/api/') and not ruta_publica(path, self.command) and not token_valido(self):
            self.send_json(401, {"error": "Sesion no iniciada o expirada", "login": True})
            return
        if path == '/api/health':
            self.send_json(200, {"status": "alive", "version": MEM_DB.get("version", 1)})
            return
        if path == '/api/seguridad':
            _, es_inicial = get_pin()
            self.send_json(200, {"ok": True, "activa": True, "es_pin_inicial": es_inicial})
            return
        if path == '/api/cotizacion.html':
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            self.handle_cotizacion_html(qs.get('folio', [''])[0])
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
        if path == '/api/fiscal/estado':
            with LOCK:
                estado = {
                    'ultima_revision': MEM_DB.get('fiscal_check', {}).get('ts', ''),
                    'fuentes': len(MEM_DB.get('fiscal_check', {}).get('resultados', [])),
                    'resultados': MEM_DB.get('fiscal_check', {}).get('resultados', [])[:30],
                    'tasa_iva': MEM_DB.get('config', {}).get('facturalo', {}).get('tasa_iva', 0.16),
                    'sitios': [{'nombre': n, 'url': u} for n, u in FUENTES_FISCALES]
                }
            self.send_json(200, estado)
            return
        if path == '/api/corte.csv':
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            dia = qs.get('dia', [''])[0] or datetime.datetime.now().strftime('%Y-%m-%d')
            self.handle_corte_csv(dia)
            return
        if path == '/api/feed.csv':
            self.handle_feed()
            return
        if path == '/api/envios':
            with LOCK:
                envios = json.loads(json.dumps(MEM_DB.get('envios', [])))
            self.send_json(200, {"ok": True, "envios": envios,
                                 "paqueterias": [{'clave': k, 'nombre': v['nombre'], 'dias': v['dias'], 'costo': v['costo_base'], 'max_kg': v['max_kg']} for k, v in PAQUETERIAS.items()]})
            return
        if path == '/api/envio/recomendar':
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            self.send_json(200, {"ok": True, **recomendar_paqueteria(qs.get('peso', ['1'])[0], qs.get('urgente', ['0'])[0] in ('1', 'true'))})
            return
        if path == '/api/contactos':
            with LOCK:
                lista = json.loads(json.dumps(MEM_DB.get('contactos', [])))
            self.send_json(200, {"ok": True, "contactos": lista})
            return
        if path == '/api/resenas':
            with LOCK:
                lista = json.loads(json.dumps(MEM_DB.get('resenas', [])))
            self.send_json(200, {"ok": True, "resenas": lista, "resumen": nps_resumen(lista)})
            return
        if path == '/api/campanas':
            with LOCK:
                lista = json.loads(json.dumps(MEM_DB.get('campanas', [])))
            self.send_json(200, {"ok": True, "campanas": lista,
                                 "email_configurado": bool(get_email_cfg().get('host') and get_email_cfg().get('user')),
                                 "sms_configurado": bool(get_sms_cfg().get('webhook'))})
            return
        if path == '/api/cotizaciones':
            with LOCK:
                lista = json.loads(json.dumps(MEM_DB.get('cotizaciones', [])))
            self.send_json(200, {"ok": True, "cotizaciones": lista})
            return
        if path == '/api/respaldo':
            try:
                data = crear_respaldo_zip()
            except Exception as e:
                self.send_json(500, {"ok": False, "error": str(e)})
                return
            self.send_response(200)
            self.send_header('Content-Type', 'application/zip')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Content-Disposition', f'attachment; filename="galera_respaldo_{datetime.date.today().isoformat()}.zip"')
            self.end_headers()
            self.wfile.write(data)
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
        if path.startswith('/api/') and not ruta_publica(path, self.command) and not token_valido(self):
            self.send_json(401, {"error": "Sesion no iniciada o expirada", "login": True})
            return
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length).decode('utf-8') if length else '{}'
        try: payload = json.loads(body)
        except Exception:
            self.send_json(400, {"error": "JSON inválido"})
            return

        if path == '/api/login':
            res = iniciar_sesion(str(payload.get('pin', '')))
            self.send_json(200 if res.get('ok') else 401, res)
            return
        if path == '/api/logout':
            cerrar_sesion(self)
            self.send_json(200, {"ok": True})
            return
        if path == '/api/seguridad':
            res = pedir_pin(payload)
            self.send_json(200 if res.get('ok') else 400, res)
            return
        if path == '/api/sync':
            with LOCK:
                if 'inventario' in payload: MEM_DB['inventario'] = payload['inventario']
                if 'config' in payload: MEM_DB.setdefault('config', {}).update(payload.get('config', {}) or {})
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
                    try: pid = int(it.get('id') or 0)
                    except (TypeError, ValueError): pid = 0
                    try: cant = max(1, int(it.get('cant', it.get('qty', 1)) or 1))
                    except (TypeError, ValueError): cant = 1
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
        if path == '/api/pagos/preferencia':
            pcfg = get_pagos_cfg()
            try: ref = str(payload.get('pedido_id', ''))
            except (TypeError, ValueError): ref = ''
            with LOCK:
                ped = next((p for p in MEM_DB.get('pedidos', []) if str(p.get('id', '')) == ref), None)
            if not ped:
                self.send_json(404, {"ok": False, "error": "Pedido no encontrado"})
                return
            base = f"http://{self.headers.get('Host') or (get_local_ip() + ':' + str(PUERTO))}"
            res = mp_preferencia(ped, pcfg, base.rstrip('/'))
            if res.get('ok'):
                init_point = res['data'].get('init_point', '') or res['data'].get('sandbox_init_point', '')
                with LOCK:
                    for p in MEM_DB['pedidos']:
                        if str(p.get('id', '')) == ref: p['pago_link'] = init_point; break
                    commit_db(increment_version=False, backup_now=False)
                self.send_json(200, {"ok": True, "init_point": init_point})
            else:
                self.send_json(400, {"ok": False, "error": res.get('error', 'Error Mercado Pago')})
            return
        if path == '/api/pagos/webhook':
            payment_id = ''
            try:
                payment_id = str(payload.get('data', {}).get('id', ''))
            except (AttributeError, TypeError):
                payment_id = ''
            if not payment_id:
                self.send_json(200, {"ok": True})
                return
            res = mp_pago_detalle(payment_id, get_pagos_cfg())
            if res.get('ok'):
                pago = res['data']
                if str(pago.get('status', '')) == 'approved':
                    ref = str(pago.get('external_reference', ''))
                    if ref:
                        registrar_pago_aprobado(pago, ref)
            self.send_json(200, {"ok": True})
            return
        if path == '/api/banco/importar':
            lineas = payload.get('lineas', [])
            if isinstance(lineas, str):
                lineas = [l for l in lineas.replace(';\r', ';\n').split('\n') if l.strip()]
            agregados = 0
            with LOCK:
                for l in lineas[:2000]:
                    partes = [x.strip().strip('"') for x in re.split(r'[,;	]', str(l))]
                    if len(partes) < 3:
                        continue
                    try:
                        monto = round(float(partes[-1].replace('$', '').replace(',', '')), 2)
                    except (ValueError, TypeError):
                        continue
                    if not re.match(r'^\d{4}-\d{2}-\d{2}', partes[0]):
                        continue
                    MEM_DB.setdefault('banco', []).insert(0, {
                        'id': f"BK{int(datetime.datetime.now().timestamp()*1000)}{agregados}",
                        'fecha': partes[0][:10],
                        'concepto': (partes[1] if len(partes) > 2 else 'Movimiento')[:120],
                        'monto': monto,
                        'conciliado': False
                    })
                    agregados += 1
                saved = commit_db(increment_version=True, backup_now=False)
            self.send_json(200, {"ok": True, "agregados": agregados, "state": saved})
            return
        if path == '/api/conciliar':
            dias = int(payload.get('dias', 7))
            self.send_json(200, conciliar_banco(dias))
            return
        if path == '/api/fiscal/validar':
            with LOCK:
                self.send_json(200, validar_fiscales())
            return
        if path == '/api/fiscal/reformas':
            resultados = buscar_reformas()
            self.send_json(200, {"ok": True, "total": len(resultados), "resultados": resultados,
                                 "ts": MEM_DB.get('fiscal_check', {}).get('ts', '')})
            return
        if path == '/api/upload-img':
            self.handle_upload_img(payload)
            return
        if path == '/api/pieza':
            op = payload.get('op', '')
            with LOCK:
                MEM_DB.setdefault('inventario', [])
                if op == 'add':
                    p = payload.get('pieza', {})
                    if not isinstance(p, dict) or not (p.get('desc', '') or '').strip():
                        self.send_json(400, {"ok": False, "error": "Falta descripci\u00f3n de la pieza"})
                        return
                    novo = {
                        'id': int(p.get('id') or datetime.datetime.now().timestamp() * 1000),
                        'cat': (p.get('cat', '') or 'Colisi\u00f3n').strip()[:40],
                        'desc': (p.get('desc', '') or '').strip()[:80],
                        'prov': (p.get('prov', '') or '').strip()[:40],
                        'precio': round(float(p.get('precio', 0) or 0), 2),
                        'stock': max(0, int(p.get('stock', 0) or 0)),
                        'img': (p.get('img', '') or '').strip()[:120]
                    }
                    MEM_DB['inventario'].insert(0, novo)
                    saved = commit_db(increment_version=True, backup_now=False)
                    self.send_json(200, {"ok": True, "pieza": novo, "state": saved})
                    return
                if op in ('update', 'del'):
                    try: pid = int(payload.get('id') or 0)
                    except (TypeError, ValueError): pid = 0
                    pieza = next((p for p in MEM_DB['inventario'] if int(p.get('id', 0)) == pid), None)
                    if not pieza:
                        self.send_json(404, {"ok": False, "error": "Pieza no encontrada"})
                        return
                    if op == 'del':
                        MEM_DB['inventario'].remove(pieza)
                    else:
                        if 'stock' in payload:
                            pieza['stock'] = max(0, int(payload.get('stock') or 0))
                        patch = payload.get('patch', {})
                        if isinstance(patch, dict):
                            for k in ('cat', 'desc', 'prov'):
                                if k in patch: pieza[k] = str(patch.get(k, '')).strip()[:80 if k == 'desc' else 40]
                            for k in ('precio',):
                                if k in patch: pieza[k] = round(float(patch.get(k, 0) or 0), 2)
                            if 'img' in patch: pieza['img'] = str(patch.get('img', '')).strip()[:120]
                    saved = commit_db(increment_version=True, backup_now=False)
                    self.send_json(200, {"ok": True, "state": saved})
                    return
            self.send_json(400, {"ok": False, "error": "Operaci\u00f3n inv\u00e1lida"})
            return
        if path == '/api/envio':
            self.handle_envio(payload)
            return
        if path == '/api/resena':
            self.handle_resena(payload)
            return
        if path == '/api/contacto':
            self.handle_contacto(payload)
            return
        if path == '/api/campana':
            self.handle_campana(payload)
            return
        if path == '/api/cotizacion':
            self.handle_cotizacion(payload)
            return
        if path == '/api/config-email':
            with LOCK:
                cfg = MEM_DB.setdefault('config', {}).setdefault('email', {})
                for k in ('host', 'port', 'user', 'pass', 'remitente', 'ssl'):
                    if k in payload: cfg[k] = str(payload[k]).strip()
                cfg2 = MEM_DB.setdefault('config', {}).setdefault('sms', {})
                if 'sms_webhook' in payload: cfg2['webhook'] = str(payload['sms_webhook']).strip()
                saved = commit_db(increment_version=True, backup_now=False)
            self.send_json(200, {"ok": True, "state": saved})
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

    def handle_envio(self, payload):
        op = payload.get('op', 'crear')
        with LOCK:
            if op == 'crear':
                try:
                    pedido_id = int(payload.get('pedido_id') or 0)
                except (TypeError, ValueError):
                    self.send_json(400, {"ok": False, "error": "pedido_id invalido"})
                    return
                ped = next((p for p in MEM_DB.get('pedidos', []) if p.get('id') == pedido_id), None)
                if not ped:
                    self.send_json(404, {"ok": False, "error": "Pedido no encontrado"})
                    return
                paqueteria = payload.get('paqueteria', 'Estandar')
                if paqueteria not in PAQUETERIAS:
                    self.send_json(400, {"ok": False, "error": "Paqueteria invalida: use " + ', '.join(PAQUETERIAS)})
                    return
                guia = (payload.get('guia', '') or ('PEND-' + str(int(datetime.datetime.now().timestamp()) % 100000))).strip()[:40]
                try:
                    costo = round(float(payload.get('costo') or PAQUETERIAS[paqueteria]['costo_base']), 2)
                except (TypeError, ValueError):
                    costo = PAQUETERIAS[paqueteria]['costo_base']
                envio = {
                    'id': int(datetime.datetime.now().timestamp() * 1000),
                    'pedido_id': pedido_id, 'cliente': ped.get('nombre', ''), 'tel': ped.get('tel', ''),
                    'folio_venta': ped.get('folio', ''), 'paqueteria': paqueteria, 'guia': guia,
                    'costo': costo, 'estado': 'preparado',
                    'ts': datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
                }
                MEM_DB.setdefault('envios', []).insert(0, envio)
                saved = commit_db(increment_version=True, backup_now=False)
                self.send_json(200, {"ok": True, "envio": envio, "state": saved})
                return
            envio = next((e for e in MEM_DB.get('envios', []) if e.get('id') == payload.get('id') or e.get('guia') == payload.get('guia')), None)
            if not envio:
                self.send_json(404, {"ok": False, "error": "Envio no encontrado"})
                return
            if op == 'estado':
                estado = payload.get('estado', '')
                if estado not in ESTADOS_ENVIO:
                    self.send_json(400, {"ok": False, "error": "Estado invalido: " + '/'.join(ESTADOS_ENVIO)})
                    return
                envio['estado'] = estado
                if estado == 'entregado':
                    res = {
                        'id': int(datetime.datetime.now().timestamp() * 1000),
                        'envio_id': envio.get('id'), 'cliente': envio.get('cliente', ''),
                        'tel': envio.get('tel', ''),
                        'texto': 'Hola %s, tu pedido llego. De 1 a 10, que tan contento estas con tu compra en Refaccionaria La Galera? Tu opinion nos ayuda mucho.' % envio.get('cliente', ''),
                        'estado': 'pendiente',
                        'ts': datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
                    }
                    MEM_DB.setdefault('resenas', []).insert(0, res)
                    envio['encuesta_enviada'] = True
                saved = commit_db(increment_version=True, backup_now=False)
                self.send_json(200, {"ok": True, "envio": envio, "state": saved})
                return
            if op == 'rastrear':
                res = rastrear_guia(envio.get('paqueteria', ''), envio.get('guia', ''))
                if res.get('ok'):
                    envio['ultimo_rastreo'] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
                    envio['http_rastreo'] = res.get('http')
                    commit_db(increment_version=False, backup_now=False)
                self.send_json(200 if res.get('ok') else 400, res)
                return
            self.send_json(400, {"ok": False, "error": "Op invalida: crear/estado/rastrear"})

    def handle_resena(self, payload):
        op = payload.get('op', 'responder')
        with LOCK:
            if op == 'crear':
                res = {
                    'id': int(datetime.datetime.now().timestamp() * 1000),
                    'cliente': (payload.get('cliente', '') or 'Cliente').strip()[:60],
                    'tel': (payload.get('tel', '') or '').strip()[:15],
                    'texto': 'Hola %s, como calificas tu compra en Refaccionaria La Galera? De 1 a 10.' % (payload.get('cliente', '') or ''),
                    'estado': 'pendiente', 'ts': datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
                }
                MEM_DB.setdefault('resenas', []).insert(0, res)
                saved = commit_db(increment_version=True, backup_now=False)
                self.send_json(200, {"ok": True, "resena": res, "state": saved})
                return
            res = next((r for r in MEM_DB.get('resenas', []) if r.get('id') == payload.get('id')), None)
            if not res:
                self.send_json(404, {"ok": False, "error": "Resena no encontrada"})
                return
            if op != 'responder':
                self.send_json(400, {"ok": False, "error": "Op invalida: crear/responder"})
                return
            try:
                score = int(payload.get('score'))
                assert 1 <= score <= 10
            except (TypeError, ValueError, AssertionError):
                self.send_json(400, {"ok": False, "error": "score debe ser 1..10"})
                return
            res['score'] = score
            res['comentario'] = (payload.get('comentario', '') or '').strip()[:300]
            res['estado'] = 'respondida'
            res['respondida_ts'] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
            saved = commit_db(increment_version=True, backup_now=False)
        self.send_json(200, {"ok": True, "resena": res, "resumen": nps_resumen(MEM_DB.get('resenas', [])), "state": saved})

    def handle_contacto(self, payload):
        op = payload.get('op', 'add')
        with LOCK:
            if op == 'del':
                antes = len(MEM_DB.get('contactos', []))
                MEM_DB['contactos'] = [c for c in MEM_DB.get('contactos', []) if c.get('tel') != payload.get('tel')]
                saved = commit_db(increment_version=True, backup_now=False)
                self.send_json(200, {"ok": True, "eliminados": antes - len(MEM_DB['contactos']), "state": saved})
                return
            tel = (payload.get('tel', '') or '').strip()[:15]
            if not tel:
                self.send_json(400, {"ok": False, "error": "Telefono requerido"})
                return
            contactos = MEM_DB.setdefault('contactos', [])
            existente = next((c for c in contactos if c.get('tel') == tel), None)
            data = {
                'tel': tel,
                'nombre': (payload.get('nombre', '') or '').strip()[:60],
                'email': (payload.get('email', '') or '').strip()[:80],
                'tags': (payload.get('tags', '') or '').strip()[:60]
            }
            if existente: existente.update(data)
            else: contactos.insert(0, dict(data, **{'ts': datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}))
            saved = commit_db(increment_version=True, backup_now=False)
        self.send_json(200, {"ok": True, "contacto": existente or data, "state": saved})

    def handle_campana(self, payload):
        op = payload.get('op', 'crear')
        with LOCK:
            if op == 'abandonados':
                hora = payload.get('horas', 24)
                lista = [texto_recuperacion(p) for p in carritos_abandonados(hora)]
                self.send_json(200, {"ok": True, "hay": len(lista), "recuperaciones": lista})
                return
            if op == 'crear':
                seg = payload.get('segmento', 'todos')
                canal = payload.get('canal', 'email')
                if seg not in ('todos', 'abandonados', 'compradores') or canal not in ('email', 'sms', 'ambos'):
                    self.send_json(400, {"ok": False, "error": "segmento: todos/abandonados/compradores · canal: email/sms/ambos"})
                    return
                camp = {
                    'id': int(datetime.datetime.now().timestamp() * 1000),
                    'nombre': (payload.get('nombre', '') or 'Campana').strip()[:60],
                    'canal': canal, 'segmento': seg,
                    'asunto': (payload.get('asunto', '') or 'Refaccionaria La Galera').strip()[:80],
                    'texto': (payload.get('texto', '') or '').strip()[:1000],
                    'estado': 'creada',
                    'ts': datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
                }
                MEM_DB.setdefault('campanas', []).insert(0, camp)
                saved = commit_db(increment_version=True, backup_now=False)
                self.send_json(200, {"ok": True, "campana": camp, "state": saved})
                return
            camp = next((c for c in MEM_DB.get('campanas', []) if c.get('id') == payload.get('id')), None)
            if not camp:
                self.send_json(404, {"ok": False, "error": "Campana no encontrada"})
                return
            if op != 'enviar':
                self.send_json(400, {"ok": False, "error": "Op invalida: crear/enviar/abandonados"})
                return
            destinatarios = contactos_segmento(camp.get('segmento', 'todos'))
            resultados = []
            for c in destinatarios:
                reg = {'tel': c.get('tel', ''), 'email': c.get('email', ''), 'estado': 'sin_destino'}
                canal = camp.get('canal', 'email')
                if canal in ('email', 'ambos') and c.get('email'):
                    rr = enviar_email_smtp(c['email'], camp.get('asunto', ''), camp.get('texto', ''))
                    reg['email_estado'] = 'enviado' if rr.get('ok') else 'error: ' + rr.get('error', '')
                if canal in ('sms', 'ambos') and c.get('tel'):
                    rr = enviar_sms_gateway(c['tel'], camp.get('texto', ''))
                    reg['sms_estado'] = 'enviado' if rr.get('ok') else 'error: ' + rr.get('error', '')
                if reg.get('email_estado') == 'enviado' or reg.get('sms_estado') == 'enviado':
                    reg['estado'] = 'enviado'
                resultados.append(reg)
            camp['estado'] = 'enviada'
            camp['enviada_ts'] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
            camp['resultados'] = resultados
            camp['enviados'] = sum(1 for r in resultados if r['estado'] == 'enviado')
            saved = commit_db(increment_version=True, backup_now=False)
        self.send_json(200, {"ok": True, "campana": camp, "enviados": camp.get('enviados', 0), "destinatarios": len(resultados), "state": saved})

    def handle_cotizacion(self, payload):
        op = payload.get('op', 'crear')
        if op == 'anular':
            with LOCK:
                cot = next((c for c in MEM_DB.get('cotizaciones', []) if c.get('folio') == payload.get('folio')), None)
                if not cot:
                    self.send_json(404, {"ok": False, "error": "Cotizacion no encontrada"})
                    return
                cot['estado'] = 'anulada'
                saved = commit_db(increment_version=True, backup_now=False)
            self.send_json(200, {"ok": True, "cotizacion": cot, "state": saved})
            return
        if op != 'crear':
            self.send_json(400, {"ok": False, "error": "Op invalida: crear/anular"})
            return
        res = crear_cotizacion(payload)
        self.send_json(200 if res.get('ok') else 400, res)

    def handle_cotizacion_html(self, folio):
        with LOCK:
            cot = next((c for c in MEM_DB.get('cotizaciones', []) if c.get('folio') == folio), None)
        if not cot:
            self.send_json(404, {"ok": False, "error": "Cotizacion no encontrada"})
            return
        filas = ''
        for i in cot.get('items', []):
            imp = float(i['precio']) * int(i['cant'])
            filas += '<tr><td>%s</td><td style="text-align:center">%s</td><td style="text-align:right">$%.2f</td><td style="text-align:right">$%.2f</td></tr>' % (i['desc'], i['cant'], float(i['precio']), imp)
        html = """<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">
<title>Cotizacion %s · Refaccionaria La Galera</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{font-family:Arial,Helvetica,sans-serif;margin:24px;color:#1e293b}
h1{font-size:1.3rem;margin:0;color:#b91c1c}h2{font-size:1rem;font-weight:400;margin:4px 0 18px;color:#475569}
table{width:100%%;border-collapse:collapse;margin-top:12px}th,td{border:1px solid #cbd5e1;padding:6px 8px;font-size:.9rem}
th{background:#f1f5f9;text-align:left}.tot td{font-weight:bold;border:none;padding-top:8px}
@media print{.no-print{display:none}}
</style></head><body>
<div style="display:flex;justify-content:space-between;align-items:flex-start">
<div><h1>REFACCIONARIA LA GALERA</h1><h2>Cotizacion %s · Vigencia hasta %s</h2></div>
<div style="text-align:right;font-size:.85rem;color:#475569">Puebla, Puebla, MX<br>WhatsApp 222 573 8872</div></div>
<p style="font-size:.9rem"><b>Cliente:</b> %s %s<br><b>Fecha:</b> %s</p>
<table><tr><th>Descripcion</th><th style="text-align:center">Cant.</th><th style="text-align:right">P. unitario</th><th style="text-align:right">Importe</th></tr>
%s
<tr class="tot"><td colspan="3" style="text-align:right">Subtotal</td><td style="text-align:right">$%.2f</td></tr>
<tr class="tot"><td colspan="3" style="text-align:right">Descuento (%.0f%%)</td><td style="text-align:right">-$%.2f</td></tr>
<tr class="tot"><td colspan="3" style="text-align:right">IVA (%.2f%%)</td><td style="text-align:right">$%.2f</td></tr>
<tr class="tot" style="font-size:1.1rem"><td colspan="3" style="text-align:right">TOTAL</td><td style="text-align:right">$%.2f</td></tr>
</table>
<p style="font-size:.85rem;color:#475569;margin-top:14px">%s</p>
<p style="font-size:.8rem;color:#64748b">Precios en MXN, pueden cambiar sin previo aviso una vez vencida la cotizacion. Gracias por su preferencia.</p>
<button class="no-print" onclick="window.print()" style="padding:10px 18px;background:#b91c1c;color:#fff;border:none;border-radius:8px;font-size:1rem;cursor:pointer">🖨️ Guardar PDF / Imprimir</button>
</body></html>""" % (
            cot['folio'], cot['folio'], cot.get('validez_hasta', ''),
            cot.get('cliente', ''), ('· ' + cot['empresa']) if cot.get('empresa') else '',
            cot.get('ts', ''), filas,
            float(cot.get('subtotal', 0)) + float(cot.get('descuento_monto', 0)),
            float(cot.get('descuento_pct', 0)), float(cot.get('descuento_monto', 0)),
            float(cot.get('tasa_iva', 0.16)) * 100, float(cot.get('iva', 0)), float(cot.get('total', 0)),
            ('Notas: ' + cot['notas']) if cot.get('notas') else '')
        raw = html.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

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
                pid = int(it.get('id') or 0)
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
            if ped.get('estado') not in ('nuevo', 'pagado'):
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
        resumen['ventas_detalle'] = [{
            'Folio': v.get('folio', ''), 'ID': v.get('id', ''), 'Fecha': v.get('fecha', ''),
            'Cliente': v.get('cliente', ''), 'Vehiculo': v.get('auto', ''), 'Metodo': v.get('metodo', ''),
            'Facturado': 'SI' if v.get('facturado') else 'NO', 'UUID': v.get('uuid', ''), 'Total': float(v.get('total', 0))
        } for v in ventas_dia]
        self.send_json(200, {"ok": True, "corte": resumen})

    def handle_corte_csv(self, dia):
        with LOCK:
            ventas_dia = [v for v in MEM_DB.get('ventas', []) if v.get('dia') == dia]
        lineas = ['Folio,ID,Fecha,Cliente,Vehiculo,Metodo,Facturado,UUID,Total']
        for v in ventas_dia:
            celdas = [str(v.get('folio', '')), str(v.get('id', '')), str(v.get('fecha', '')),
                      str(v.get('cliente', '')), str(v.get('auto', '')), str(v.get('metodo', '')),
                      'SI' if v.get('facturado') else 'NO', str(v.get('uuid', '')), str(round(float(v.get('total', 0)), 2))]
            seguras = []
            for c in celdas:
                if ',' in c or '"' in c or '\n' in c:
                    seguras.append('"' + c.replace('"', '""') + '"')
                else:
                    seguras.append(c)
            lineas.append(','.join(seguras))
        raw = '\ufeff' + '\n'.join(lineas) + '\n'
        self.send_response(200)
        self.send_header('Content-Type', 'text/csv; charset=utf-8')
        self.send_header('Content-Disposition', f'attachment; filename="corte-caja-galera-{dia}.csv"')
        self.send_header('Content-Length', str(len(raw.encode('utf-8'))))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(raw.encode('utf-8'))

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
    threading.Thread(target=fiscal_worker, daemon=True).start()
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    httpd = socketserver.ThreadingTCPServer(('0.0.0.0', PUERTO), Handler)
    ip = get_local_ip()
    print('=' * 58)
    print('  REFACCIONARIA LA GALERA v6.2 - E-commerce + ERP + SAT + CRM (ERP con PIN)')
    print('=' * 58)
    print(f'  Sitio Público    : http://localhost:{PUERTO}/index.html')
    print(f'  Panel ERP Táctil : http://localhost:{PUERTO}/app.html')
    print(f'  Red Local (WiFi) : http://{ip}:{PUERTO}/index.html')
    print('=' * 58)
    httpd.serve_forever()

if __name__ == '__main__':
    main()
