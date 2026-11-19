# Refaccionaria La Galera — Procesos compraventa digital (punta a punta)

Sistema de procesos comerciales con IDs estables (itera sobre ellos sin renumerar).
Cada proceso declara su **entrada** (proceso origen) y **salida** (proceso destino) formando una cadena trazable.
Columna **Estado** = cobertura del software v6.2 (`galera/`): ✅ implementado · 🟡 parcial · ⬜ pendiente

## Mapa conectado de extremo a extremo

```mermaid
flowchart TD
    MKT07[MKT-07 Vigilancia de tendencias] --> PROD01[PROD-01 Catalogo]
    PROD01 --> PROD02[PROD-02 Proveedores]
    PROD02 --> PROD03[PROD-03 Costeo y precio base]
    PROD03 --> COM02[COM-02 Precios competitivos]
    PROD01 --> PROD04[PROD-04 Inventario y calidad]
    PROD02 --> PROD04
    PROD04 --> VTA01[VTA-01 Tienda y checkout]
    PROD04 --> MKT04[MKT-04 WhatsApp]
    PROD01 --> COM01[COM-01 Posicionamiento]
    COM01 --> MKT01[MKT-01 Contenido]
    COM03[COM-03 Buyer persona] --> MKT01
    COM02 --> MKT01
    COM04[COM-04 Plan comercial] --> MKT01
    MKT01 --> MKT02[MKT-02 Campanas Meta]
    MKT01 --> MKT03[MKT-03 Campanas TikTok]
    MKT01 --> MKT05[MKT-05 Email y SMS]
    MKT06[MKT-06 Sitio y SEO] --> MKT05    MKT02 --> VTA01
    MKT03 --> VTA01
    MKT06 --> VTA01
    MKT02 --> VTA02[VTA-02 Venta WhatsApp]
    MKT03 --> VTA02
    MKT04 --> VTA02
    MKT05 --> VTA02
    VTA01 --> VTA03[VTA-03 Up/cross-sell]
    VTA02 --> VTA04[VTA-04 Cotizaciones B2B]
    VTA01 --> AB[Carrito abandonado]
    AB --> MKT04
    AB --> MKT05
    VTA01 --> PAGO01[PAGO-01 Cobro]
    VTA02 --> PAGO01
    VTA03 --> PAGO01
    VTA04 --> PAGO01
    PAGO01 --> PAGO02[PAGO-02 Confirmacion webhook]
    PAGO02 -->|aprobado| FAC01[FAC-01 Factura CFDI 4.0]
    PAGO02 -->|aprobado| ENT01[ENT-01 Preparacion y envio]
    PAGO02 -->|rechazado| MKT04
    PROD04 --> ENT01
    ENT01 --> ENT02[ENT-02 Rastreo y aviso]
    ENT02 --> ENT03[ENT-03 Confirmacion entrega]
    ENT03 --> CX01[CX-01 Posventa y resenas]
    FAC01 --> CX02[CX-02 Analitica y mejora]
    ENT03 --> CX02
    CX01 -->|prueba social| MKT01
    CX02 -->|precios| COM02
    CX02 -->|stock| PROD02
    NEU[NEU-01..05 Neuromarketing] -.capa transversal.-> MKT01
    NEU -.-> MKT02
    NEU -.-> VTA01
    NEU -.-> VTA02
```

---

## Fase PROD — Producción y abastecimiento

### PROD-01 Catálogo maestro ✅
- Objetivo: definir qué piezas y servicios se venden, con ficha, foto y categoría correcta (claves SAT de autopartes: faros `25172100`, espejos `25172500`, parrillas `25171905`).
- Disparador: ciclo de negocio | Proceso que lo activa: MKT-07 (demanda detectada), CX-02 (reposición).
- Entradas: tendencias (MKT-07), inventario físico real | Procesos de origen: MKT-07, PROD-02.
- Actividades: 1) alta de pieza por `/api/pieza` (op add); 2) foto desde el celular con recorte automático; 3) descripción generada con IA (🤖 en el ERP); 4) publicación simultánea al sitio, Meta y Mercado Libre.
- Salida: catálogo live → COM-01, COM-02, MKT-04, VTA-01 (`/api/public`, `/api/feed.csv`, `/api/meta/push`, `/api/meli/publicar`).
- Herramientas / APIs: ERP v6.1 (Inventario), IA (`/api/ia/descripcion`), feed CSV Meta/Instagram/TikTok/Google, Meta Commerce API, Mercado Libre API.
- KPIs: SKUs publicados con foto, rotación por categoría.
- NEU aplicadas: NEU-05 (descripciones que venden la solución, no la pieza).

### PROD-02 Proveedores y abastecimiento ✅
- Objetivo: reponer stock al mejor costo sin quiebres.
- Disparador: alerta de stock bajo | Proceso origen: PROD-04 (🤖 Agente Reposición IA).
- Actividades: 1) el Agente Reposición lista agotados y punto de reorden; 2) negociación por WhatsApp/e-mail con proveedores; 3) registro del costo real por pieza (`prov` en ficha).
- Salida: purchase order informal + costo → PROD-03, PROD-04.
- Herramientas: ERP (Inventario → filtro stock 0), Agente IA de reposición.
- KPIs: tiempo de reabasto, tasa de quiebres de stock.

### PROD-03 Costeo y precio base ✅
- Objetivo: precio base rentable (producto + margen + comisión MP + fiscal).
- Entradas: costos (PROD-02), referencia de mercado (COM-02) | Origen: PROD-02.
- Salida: precio base → COM-02 y FAC-01.
- KPIs: margen bruto por SKU, punto de equilibrio.
- NEU aplicadas: NEU-03 (anclaje contra precio de marketplace).

### PROD-04 Inventario y calidad ✅
- Objetivo: inventario exacto en tiempo real en todos los puntos de venta.
- Entradas: ventas/pedidos (VTA-01, VTA-02 descontan stock automáticamente), ajustes manuales (/api/pieza op update).
- Actividades: 1) ajustes ± desde el ERP; 2) sincronía garantizada — sync server-authoritative, sin pisar datos entre dispositivos; 3) conteo cíclico con corte semanal; 4) alertas del Agente Reposición.
- Salida: disponibilidad real → VTA-01, MKT-04, ENT-01 (nunca se vende sin stock).
- Herramientas: ERP + `/api/pieza` + autosave/backups cada 15 min.
- KPIs: exactitud de inventario, quiebres/mes, tiempo entre venta y descuento de stock (0).

---

## Fase COM — Comercialización

### COM-01 Posicionamiento y propuesta de valor ✅
- Objetivo: por qué comprarle a La Galera y no a Amazon/Mercado Libre: asesoría 1 a 1 por WhatsApp con foto de tu golpe, entregas y garantía directas, piezas exactas de colisión.
- Salida: mensaje de marca → MKT-01, MKT-06, NEU-05.
- KPIs: conversión por canal, tráfico a sitio.

### COM-02 Política de precios competitiva ✅
- Objetivo: precios competitivos sin destruir margen.
- Actividades: el 🤖 Agente de Precios IA sugiere movimientos según ticket promedio; ajuste granular por `/api/pieza`.
- Salida: lista vigente → VTA-01, MKT-02/03, FAC-01.
- KPIs: diferencial vs. competencia, margen neto.

### COM-03 Segmentación y buyer persona 🬡 depende de tu definición
- Objetivo: 2-4 personas nuevo (chocados), reparadores, refaccionarios B2B — mapeadas a Meta/TikTok/WhatsApp.
- Salida: audiencias → MKT-02, MKT-03, MKT-05.

### COM-04 Plan comercial y canales ⬜ por definir
- Objetivo: cuota de ventas por canal y calendario de promos (Buen Fin, Navidad).
- Salida: calendario → MKT-01, VTA-01, ENT-01.

---

## Fase MKT — Marketing digital (APIs conectadas)

### MKT-01 Contenido y creativos ⬜ producción de piezas editable
- Objetivo: contenido que detiene el scroll y vende soluciones.
- Herramientas: banco de fotos del ERP (productos ya con foto servible).
- KPIs: alcance, retención, CTR.

### MKT-02 Campañas Meta (FB + IG) ✅ conector
- Herramientas: Meta Marketing API — push del catálogo completo `/api/meta/push` (sincroniza: id, title, description, availability, price, link, image_link, brand), píxel/conversions a conectar en campañas.
- Datos sincronizados: catálogo → Meta Commerce; pedidos/ventas ← webhook pedido.
- KPIs: ROAS, CPM, costo por lead.

### MKT-03 Campañas TikTok 🬡 vía feed
- Herramientas: feed CSV compatible TikTok Shop `/api/feed.csv` (same fields).
- KPIs: CPA, visualizaciones 50%, ventas atribuidas.

### MKT-04 WhatsApp Business ✅ conector
- Herramientas: WhatsApp Cloud API (`/api/whatsapp/send`, `/api/whatsapp/notificar-venta` — sincroniza: confirmaciones de venta), webhook receptor con verificación (`/api/whatsapp/webhook`), click-to-chat con mensaje pre-armado del carrito en el sitio.
- KPIs: tasa de respuesta, conversión, tiempo de respuesta.

### MKT-05 Email y SMS ✅ campañas ERP
- Implementado v6.2: campañas `/api/campana` (crear/enviar) con segmentos `todos`, `compradores` y **carritos abandonados (≥ 24 h)** con mensaje de recuperación y link `wa.me` pre-armado (`op:'abandonados'`). Email por SMTP propio (`/api/config-email`, Gmail con app-password) y SMS por webhook genérico POST `{to,text}`.
- Pendiente: automatización programada (bienvenida/doble opt-in) y cumplimiento formal de aviso de privacidad.
- KPIs: open rate, ingreso por correo, bajas.
### MKT-06 Sitio central y SEO ✅
- Herramientas: sitio public (Pages + servidor local), datos estructurados, velocidad. Feed CSV también indexable.
- KPIs: tráfico orgánico, conversión orgánica.

### MKT-07 Vigilancia de tendencias 🬡 informal
- Fuentes: Google Trends, TikTok Creative Center, Meta Ad Library + búsquedas internas del sitio. Salida → PROD-01/02, MKT-01.

---

## NEU — Neuromarketing (transversal)

- NEU-01 Prueba social: reseñas reales en ficha/sitio (retro CX-01).
- NEU-02 Escasez/urgencia: etiqueta "Agotado" y stock real desde PROD-04 (nunca inventar).
- NEU-03 Anclaje: precio marketplace vs. tuyo; combos del Agente de Precios.
- NEU-04 Reciprocidad: diagnóstico gratis por foto o cotización sola.
- NEU-05 Storytelling: "Encuentra tu pieza exacta sin dar vueltas" (ya en hero del sitio).

Regla: cada pieza/pantalla declara qué técnica usa y su sustento real.

---

## Fase VTA — Venta

### VTA-01 Tienda y checkout ✅
- Herramientas: sitio público con carrito persistente + checkout WhatsApp o pedido web directo (`/api/pedido`) → entra al ERP; título y stock real.
- KPIs: conversión, abandono de carrito, ticket promedio.

### VTA-02 Venta conversacional WhatsApp ✅
- Actividades: asesoría con foto (NEU-01), envío de link de pago (PAGO-01), seguimiento 24/48/72h; push de confirmaciones de venta automáticas.
- Herramientas: WhatsApp Cloud API + plantillas Meta.
- KPIs: conversión por conversación, ticket.

### VTA-03 Up-selling y cross-selling ✅ sugerencias IA
- Herramientas: Agente Precios IA + combos sugeridos.
- KPIs: AOV, aceptación de recomendación.

### VTA-04 Cotizaciones B2B ✅ en ERP
- Implementado v6.2: `/api/cotizacion` con folio `COT-AAAAMMDD-NN`, piezas del inventario o conceptos manuales, descuento %, validez en días y desglose subtotal/IVA/total con la tasa de zona configurada. PDF/imprimible con marca en `/api/cotizacion.html?folio=...`; anulación con `op:'anular'`.
- Pendiente: seguimiento del cierre y la conversión cotización→venta (hoy manual).
- Objetivo: cotizar para refaccionarios/reparadores. KPIs: cierre, ciclo de venta.
---

## Fase FAC — Facturación CFDI 4.0 ✅

### FAC-01 Emisión de comprobante ✅
- Actividades: datos fiscales del receptor; timbrado vía FacturaLo API (`/api/facturar` individual y `/api/facturar-global` del día); envío automático de XML; validador fiscal SAT (`/api/fiscal/validar`); reformas SAT/DOF (`/api/fiscal/reformas`, `fiscal_worker` cada 24 h); IVA por zona (16% centro / 8% frontera).
- KPIs: % facturado, tiempo de emisión, % errores de timbrado.

---

## Fase PAGO — Medios de pago ✅

### PAGO-01 Cobro ✅ Mercado Pago
- Herramientas: link de checkout por pedido `/api/pagos/preferencia` (external_reference = pedido), opciones: tarjeta, Efectivo en tienda, transferencia.
- KPIs: tasa de aprobación, comisiones.

### PAGO-02 Confirmación transaccional ✅
- Herramientas: webhook `/api/pagos/webhook` → pedido PAGADO + caja `MP-{id}` + link en el ERP. Conciliación bancaria automática (`/api/banco/importar` CSV del banco + `/api/conciliar`).
- KPIs: latencia de confirmación, % de pagos conciliados.

---

## Fase ENT — Entrega directa

### ENT-01 Preparación y envío ✅ ERP (guía por API directa parcial)
- Implementado v6.2: `/api/envio` crea el envío ligado al pedido con paquetería de catálogo (Estafeta, DHL, 99minutos, FedEx), registro de guía, costo y estados `preparado → en_transito → entregado/rechazado`. Recomendador automático por peso en `/api/envio/recomendar`.
- Pendiente: generar la guía directamente con la API de la paquetería (requiere contrato/cuenta del negocio).
- KPIs: tiempo de preparación, costo de envío.
### ENT-02 Rastreo y notificaciones ✅ ERP
- Implementado v6.2: `op:'rastrear'` abre el rastreo oficial de la paquetería con la guía (Estafeta/DHL/FedEx/99minutos) y guarda la última consulta; estados visibles en la pestaña CRM. Aviso al cliente WhatsApp pre-armado (Cloud API o wa.me).
- KPIs: incidencias por envío.
### ENT-03 Confirmación de entrega ✅ ERP
- Implementado v6.2: marcar envío ENTREGADO en CRM → dispara **encuesta automática de satisfacción (CX-01)** y cierra el ciclo pedido→venta→entrega.
- KPIs: entregas a tiempo, devoluciones.
### CX-01 Posventa, reseñas y retención ✅ ERP
- Implementado v6.2: encuesta automática post-entrega (escala 1–10) con botón WhatsApp pre-armado; respuestas en `/api/resena` y **NPS automático** (promotores/pasivos/detractores + promedio) en la pestaña CRM.
- Pendiente: programa formal de recompra y garantías.
- KPIs: NPS, tasa de recompra.
### CX-02 Analítica y bucle de mejora ✅ parcial (dashboard ERP + IA)
- Herramientas: ERP (Caja corte, Ventas, IA — 🤖 agentes), datos de pedidos/canal/abandono.
- KPIs: ventas totales, canal, CAC vs LTV. Bucle → COM-02 (precios), PROD-02 (stock), MKT-01 (creativos).

---

## Leyenda de cobertura v6.2

| Estado | Significado |
|---|---|
| ✅ | Funciona hoy con el código en `galera/` |
| 🬡 (≈) | Conector listo o cobertura parcial, falta la operación/pieza |
| ⬜ | Pendiente: proceso de negocio o integración no cubierta |

## Cadena crítica (pedido web pago → entrega)

1. Cliente armará carrito en el sitio (VTA-01) → `/api/pedido` crea pedido "nuevo" en el ERP.
2. Cobro: enlace de pago MP (PAGO-01) o transferencia; webhook aprueba (PAGO-02) → pedido PAGADO + caja.
3. Botón "Registrar venta" → venta con folio, descuento de stock (PROD-04) y registro en caja.
4. Factura individual o global del día (FAC-01) vía FacturaLo; XML guardado en `facturas/`.
5. Entrega en tienda (ENT-01) + aviso WhatsApp (ENT-02) + reseña (CX-01) → datos para CX-02.
