# 🔧 Sistema Integral Refaccionaria La Galera v6.2

**E-commerce + ERP táctil + IA + CFDI 4.0 + Pagos + Conciliación bancaria + CRM (envíos, NPS, campañas, cotizaciones B2B)**

Servidor 24/7 que corre igual en **Android (Termux), Windows, Linux y macOS** — solo necesita Python 3 (sin dependencias externas). Memoria local que crece automáticamente con la base de datos.

---

## ✨ Qué incluye v6.0

| Módulo | Descripción |
|---|---|
| 🌐 **Sitio web público** (`index.html`) | Catálogo en vivo con **fotos de producto**, buscador, modo oscuro y **carrito de compras** (persistente en el dispositivo) |
| 🛒 **Checkout doble** | Cliente puede pedir por **WhatsApp** (mensaje pre-armado con pedido y total) o enviar **pedido web directo** que entra al ERP |
| 📦 **Inventario** | Alta/baja/ajuste stock, **subida de fotos** desde el celular (recorte automático a JPEG), precio IVA incluido |
| 💰 **Caja** | Corte del día (efectivo esperado, transferencias, tarjeta, ingresos/egresos manuales), todo alimentado automáticamente por las ventas |
| 🧾 **Facturación SAT CFDI 4.0** | Individual (`/api/facturar`) y **Factura Global del día** (`/api/facturar-global`) vía FacturaLo, con reglas Anexo 20 a 6 decimales y nodo `InformacionGlobal` |
| 🤖 **IA y Agentes** | Asesor de negocio + agentes de reposición, precios y canales. Funciona **sin internet** (reglas locales) y mejora con API de IA (Mistral u otra compatible) |
| 🛍️ **Conectores** | **Meta Commerce** (push del catálogo vía Graph API), **WhatsApp Cloud API** (envío de confirmaciones + webhook receptor), **Mercado Libre** (publicación de piezas), **feed CSV** compatible con Meta / Instagram / TikTok / Google |
| 🔑 **API Partner** | API propia con llave para enlazar sistemas externos (leer catálogo, actualizar precios y stock) |
| 💾 **Memoria expansible** | La base caliente (`db_galera.json`) se mantiene ligera (500 registros por colección) y el historial crece sin límite en `archivo/` por mes |

---

## 🆕 Novedades v6.1

| Módulo | Descripción |
|---|---|
| 💳 **Pasarela de pagos (Mercado Pago)** | Botón "Cobrar" en cada pedido web → genera link de checkout (`/api/pagos/preferencia`). El webhook `/api/pagos/webhook` marca el pedido como **PAGADO** y registra el ingreso en caja con referencia `MP-{id}` |
| 🏦 **Conciliación bancaria automática** | Importa el estado de cuenta (CSV/TSV) en Config → Banco (`/api/banco/importar`); `/api/conciliar` cruza montos de banco contra ventas/caja (±$0.01, ventana de días y folio en el concepto) marcando ambos lados como conciliados |
| 🧾 **Validador fiscal SAT** | `/api/fiscal/validar` revisa RFC, régimen/uso CFDI (catálogo 4.0), CP, serie y tasa de IVA antes de timbrar |
| 📜 **Reformas SAT/DOF (automático)** | Conector a los sitios oficiales: sat.gob.mx (Novedades, Anexo 20, Miscelánea Fiscal) y dof.gob.mx. Se ejecuta **solo al arrancar y cada 24 h** (`fiscal_worker`); estado visible en ERP → Config → Fiscal (`/api/fiscal/estado`) y manual en `/api/fiscal/reformas` |
| 🛡️ **Resguardo ZIP** | Config → "Descargar respaldo" → ZIP con `db_galera.json` + fotos + archivo histórico + facturas (`/api/respaldo`) |
| ⚡ **Sync robusto** | Ventas/pedidos/caja/mensajes son del servidor: los clientes solo editan inventario y config. Alta/ajuste/baja de piezas va por `/api/pieza` (operación granular) sin pisar datos entre dispositivos |
| 📊 **Corte CSV fiel** | `/api/corte.csv` exporta el corte del día en el formato histórico `Folio,ID,Fecha,Cliente,Vehiculo,Metodo,Facturado,UUID,Total` (compatible Excel) |
| 📈 **IVA por zona** | Botones "🏙️ Zona Centro 16%" y "🌎 Frontera 8%" en Config → Fiscal. El validador avisa si la tasa usada no es 0.16 ó 0.08 (tasas vigentes 2026: Ley del IVA + estímulo fronterizo fichas 3/DEC-5 y 4/DEC-5 del Anexo 2 RMF, vigente hasta 31-dic-2026) |

## 🆕 Novedades v6.2

| Módulo | Descripción |
|---|---|
| 🚚 **Envíos y rastreo (ENT)** | Nueva pestaña CRM: asigna paquetería al pedido (Estafeta, DHL, 99minutos, FedEx), genera/registra guía, recomendador automático por peso (`/api/envio/recomendar`), rastreo con un toque (URL oficial de la paquetería) y flujo de estados preparado → tránsito → entregado |
| 🌟 **Reseñas y NPS (CX)** | Al marcar un envío ENTREGADO se genera encuesta automática de satisfacción (1–10) con botón WhatsApp pre-armado. `/api/resenas` calcula **NPS** (promotores − detractores), promedio y desglose |
| 📣 **Campañas Email/SMS (MKT-05)** | Segmentos: todos, compradores y **carritos abandonados (&gt;24 h)** con mensaje de recuperación y link wa.me listo. Email por SMTP propio (Gmail con app-password) y SMS por webhook genérico `(POST {to,text})`. Configuración en ERP → Config → Email/SMS |
| 🧾 **Cotizaciones B2B (VTA-04)** | Erre que erre con folio `COT-AAAAMMDD-NN`, piezas del inventario o conceptos manuales, descuento %, validez en días y desglose subtotal/IVA/total con la tasa de tu zona. Impresión/PDF profesional (`/api/cotizacion.html?folio=...`) con marca Refaccionaria La Galera |
| 📑 **Contactos (CRM base)** | Agenda propia; se alimenta sola con pedidos web. Alimenta segmentos de campañas |
| 🔒 **Protección del ERP con PIN** | Al abrir el panel pide PIN (inicial **8888**; sésiones de 12 h, bloqueo de 5 min tras 5 intentos fallidos). Cámbialo en Config → Seguridad. La tienda pública, pedidos web, webhooks de pago/WhatsApp y API Partner siguen abiertos; todo lo demás (inventario, ventas, caja, config, respaldo) exige sesión válida (`X-Auth-Token`) |
| 💾 **Archivo histórico extendido** | cotizaciones, envíos, reseñas y campañas se archivan solos (500 calientes + archivo mensual) |

---

## 🚀 Instalación

### Opción A — Android (Termux)
```bash
pkg install python -y
python instalar.py     # genera archivos, comandos 'galera' y arranque automático
galera                 # arranca el servidor 24/7
```

### Opción B — Windows
1. Instalar Python desde microsoft.com o python.org.
2. Doble clic o terminal: `python instalar.py` (crea además `galera-start.bat`).
3. Ejecutar `galera-start.bat` o `python server.py`.

### Opción C — Linux / macOS
```bash
python3 instalar.py
python3 server.py
```

**Accesos después de arrancar:**
- Sitio web: `http://localhost:8000/index.html`
- Panel ERP: `http://localhost:8000/app.html`
- Red local: `http://TU_IP:8000/index.html` (empleados/vecinos pueden ver catálogo)
- Feed catálogo: `http://localhost:8000/api/feed.csv`

---

## 🤖 IA y Agentes

Sin configuración → **agentes locales** con reglas de negocio (offline):
- **Agente Reposición:** piezas agotadas y punto de reorden.
- **Agente Precios:** sugerencias de combo/paquete según ticket promedio.
- **Agente Canales:** fotos faltantes, pedidos web sin confirmar, empuje semanal.

Con API key (ERP → Config → IA) → responde con IA real usando el estado del negocio como contexto. Compatible con Mistral (`https://api.mistral.ai/v1/chat/completions`, modelo `mistral-small-latest`) o cualquier endpoint OpenAI-compatible.

---

## 🛒 Conectores de venta

| Canal | Cómo se conecta | Dónde se configura |
|---|---|---|
| **WhatsApp** | Cloud API (envío) + click-to-chat (recibo de pedidos) | ERP → Config → WhatsApp Business |
| **Instagram / Facebook** | Catálogo de Commerce: push Graph API o feed CSV | ERP → Config → Meta Commerce |
| **TikTok Shop** | Feed CSV de catálogo (`/api/feed.csv`) | ERP → Config (enlace) |
| **Mercado Libre** | API oficial: publicar piezas con foto | ERP → Config → Mercado Libre |
| **Sistemas externos** | API Partner con `X-API-Key` | ERP → Config → API Partner |

**API Partner:**
```bash
# leer catálogo
curl -H "X-API-Key: TU_LLAVE" http://localhost:8000/api/partner/catalog
# actualizar precio/stock
curl -X POST -H "X-API-Key: TU_LLAVE" -H "Content-Type: application/json" \
  -d '{"id":1001,"precio":1450,"stock":4}' http://localhost:8000/api/partner/stock
```

> ℹ️ Webhook receptor de WhatsApp y que Meta/TikTok tomen el feed requieren que el servidor sea visible en internet (dominio público o túnel del propio dueño). Los endpoints ya están listos: `/api/whatsapp/webhook`.

---

## 📁 Estructura

```text
galera/
├── server.py          # Servidor REST completo (SAT, IA, caja, conectores)
├── index.html         # Sitio público con carrito
├── app.html           # ERP táctil (6 pestañas)
├── db_galera.json     # Base caliente (autoguardado + backups rotativos)
├── instalar.py        # Instalador universal multiplataforma
├── manifest.json      # PWA (instalable como app en Android)
├── productos/         # Fotos de productos + placeholder
├── facturas/          # XMLs timbrados
├── backups/           # Respaldos rotativos
└── archivo/           # Memoria histórica mensual (crece sin límite)
```

---

## 🧪 Verificado en este desarrollo

- `/api/pieza`: alta, ajuste de stock, edición y baja granular ✅
- Mercado Pago: preferencia sin token → error claro; webhook sin token → respuesta sin crash ✅
- Banco: importar CSV/TSV (fechas válidas, montos con $ y comas), conciliar por monto+fecha, descartar líneas raras ✅
- Conciliación marca ambos lados (banco y caja/ventas) ✅
- Validador fiscal detecta RFC/régimen/UsoCFDI/CP faltantes ✅
- Conector automático SAT/DOF: DOF conecta y lista decretos reales; errores de red manejados sin crash ✅
- Tasas IVA: 0.16 y 0.08 aceptadas, 0.15 → aviso con fundamento oficial ✅
- Respaldo ZIP válido (PK) con db + productos + archivo histórico ✅
- Corte CSV con formato histórico `Folio,ID,...,Total` ✅
- Tasa IVA configurable aplicada a CFDI nuevos ✅
- Sync multi-dispositivo: config de un cliente no borra ventas/pedidos/caja ✅
- Venta mostrador → folio, stock descontado, registro en caja ✅
- Pedido web (carrito) → confirmación → venta + caja ✅
- Corte de caja: efectivo esperado, por método, egresos ✅
- Archivado automático 600+ → 500 calientes (tras tick autosave) + archivo mensual ✅
- CFDI 4.0 individual (claves autopartes, sin InformacionGlobal) y global (01010101 + InformacionGlobal) ✅
- Feed CSV, API Partner con llave (401 sin llave) ✅
- WhatsApp webhook verificación challenge + recepción de mensajes ✅
- IA: asesor y agentes locales; descripción automática de producto ✅
- Cotizaciones B2B: folio, descuento, IVA por zona, PDF imprimible y anulación ✅
- Envíos: crear por pedido, recomendar por peso, rastrear (URL oficial), estados y encuesta automática al entregar ✅
- Reseñas/NPS: score 1–10 validado, NPS con promotores/pasivos/detractores ✅
- Campañas: crear/enviar por email(SMTP)/SMS(webhook), segmentos y carritos abandonados ✅
- Contactos: alta/actualización/borrado ✅
- PIN ERP: rutas protegidas 401 sin sesión, login 8888, PIN incorrecto rechazado, bloqueo por intentos, cambio de PIN, logout invalida token ✅
- Público intacto: tienda, feed, pedidos web, webhooks, partner ✅
- Archivado con las nuevas colecciones (cotizaciones/envíos/resenas/campañas) ✅
- Archivado automático: 600 registros → 500 calientes + archivo mensual ✅
- **Auditoría de escenarios (43 pruebas):** archivos sensibles (`db_galera.json`, `backups/`, `archivo/`, `.git`, `.env`) → 404 sin sesión; cotización imprimible escapa HTML/inyección; venta sin artículos rechazada (400); clamps de descuento/peso/cantidad; entrada unicode/emoji; webhook MP sin token sin crash; checkout público → confirmación protegida; HEAD no filtra datos; persistencia verificada tras reinicio del servidor ✅
