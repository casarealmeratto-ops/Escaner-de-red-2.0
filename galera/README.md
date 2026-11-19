# 🔧 Sistema Integral La Galera Autopartes v6.0

**E-commerce + ERP táctil + IA + Facturación CFDI 4.0 (SAT México)**

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

- Venta mostrador → folio, stock descontado, registro en caja ✅
- Pedido web (carrito) → confirmación → venta + caja ✅
- Corte de caja: efectivo esperado, por método, egresos ✅
- CFDI 4.0 individual (claves autopartes, sin InformacionGlobal) y global (01010101 + InformacionGlobal) ✅
- Feed CSV, API Partner con llave (401 sin llave) ✅
- WhatsApp webhook verificación challenge + recepción de mensajes ✅
- IA: asesor y agentes locales; descripción automática de producto ✅
- Archivado automático: 600 registros → 500 calientes + archivo mensual ✅
