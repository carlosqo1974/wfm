# Plan de Evolución WFM para Contact Center (nivel enterprise)

## Diagnóstico rápido del estado actual

La aplicación actual cubre una base útil de WFM táctico:
- Alta/edición de campañas inbound, outbound y chat.
- Cálculo de dotación por intervalos (Erlang C, productividad outbound, concurrencia chat).
- Persistencia local en SQLite y exportación de reportes Excel/CSV.

Sin embargo, para parecerse a suites líderes del mercado (Genesys WFM, Verint, NICE, Calabrio, etc.) faltan bloques funcionales clave en **forecasting avanzado, scheduling, intradía, automatización y gobierno**.

## Qué agregar/cambiar (priorizado)

### 1) Forecasting avanzado (prioridad crítica)

**Situación actual**
- El forecast es muy simple: usa promedio reciente y factor lineal de tendencia. No contempla estacionalidad robusta, eventos o interval-level patterns complejos.

**Evolución recomendada**
- Modelos por jerarquía: canal → cola → skill → intervalo.
- Estacionalidad múltiple: día de semana, semana del mes, mes, festivos.
- Detección y tratamiento de outliers (incidentes, caídas, campañas).
- Backtesting automático con métricas MAPE/WAPE/Bias por serie.
- Forecast por percentiles (P50/P80/P95) para planificación por riesgo.
- “What-if” drivers: marketing, facturación, cambios de IVR/bots.

### 2) Motor de scheduling y turnos (la gran brecha)

**Situación actual**
- El sistema calcula cuántos agentes se requieren por intervalo, pero no construye un roster real de personas/turnos.

**Evolución recomendada**
- Generación de turnos optimizada (MILP/CP-SAT): cobertura vs costo.
- Reglas laborales: jornadas, descansos, comidas, nocturnidad, horas extra, sindicatos.
- Multi-skill scheduling (blending inbound/chat/outbound por skill).
- Preferencias de agentes y fairness (equidad en fines de semana, horarios premium).
- Gestión de time-off/vacaciones/ausencias con impacto en cobertura.

### 3) Intradía en tiempo real (RTA)

**Situación actual**
- No hay control en vivo ni replaneación intradía.

**Evolución recomendada**
- Ingesta near real-time desde ACD/CCaaS (cada 5-15 min).
- Monitor de desvíos: volumen, AHT, SL, ocupación, adherence.
- Reforecast intradía y recomendaciones automáticas: mover breaks, overtime, VTO, re-skill.
- Simulador de impacto inmediato (si muevo 10 agentes, qué pasa con SL en 60 min).

### 4) Gestión de adherence y performance de agentes

- Adherence por estado real vs estado planificado (login, AUX, after-call, etc.).
- Conformance diario/semanal/mensual y scoring por supervisor.
- Alertas por umbral y workflow de corrección.
- Integración con QA y productividad para decisiones de coaching.

### 5) Arquitectura y datos (para escalar)

**Situación actual**
- Monolito Flask + SQLite, adecuado para PoC, no para operación enterprise multiusuario.

**Evolución recomendada**
- Migrar a PostgreSQL (+ Alembic) y separar capas (API, dominio, workers).
- Modelo de datos histórico granular (facts por intervalo + dimensiones).
- Cola de tareas (Celery/RQ) para forecast, optimización, exports pesados.
- Cache para dashboards (Redis).
- API versionada + autenticación robusta (OIDC/SAML).

### 6) Gobierno, seguridad y auditoría

- RBAC por rol (planner, supervisor, manager, auditor).
- Bitácora/auditoría de cambios de forecast, staffing y schedule.
- Versionado de planes (baseline vs revisiones intradía).
- Trazabilidad completa para cumplimiento regulatorio.

### 7) UX y producto (adopción)

- Vistas separadas por rol con KPIs específicos.
- Heatmaps de cobertura y gaps por intervalo/skill.
- Flujos guiados: Forecast → Staffing → Schedule → Intra-day.
- Catálogo de escenarios y comparador side-by-side.

## Roadmap sugerido

### Fase 1 (4-8 semanas): Base sólida
- Migración a PostgreSQL + Alembic.
- RBAC y autenticación.
- Forecast con backtesting y métricas de error.
- Dashboard de exactitud de forecast.

### Fase 2 (8-12 semanas): Scheduling y reglas
- Motor de turnos con restricciones laborales.
- Gestión de ausencias/time-off.
- Publicación y versionado de schedules.

### Fase 3 (8-12 semanas): Intradía y optimización continua
- Conectores en vivo con ACD/CCaaS.
- RTA con alertas y acciones recomendadas.
- Reforecast intradía + simulaciones.

### Fase 4 (continuo): Inteligencia y automatización
- ML avanzado por skill/canal.
- Recomendador de decisiones (overtime/VTO/re-skill).
- Benchmarking entre campañas/sedes.

## KPIs que debes gestionar para parecerte a “best-in-class”

- Forecast Accuracy: WAPE, MAPE, Bias (diario/semanal, por skill).
- Service Level attainment (% intervalos en objetivo).
- Occupancy saludable (evitar sobrecarga crónica).
- Schedule Efficiency (paid hours vs required hours).
- Adherence/Conformance de agentes.
- Cost to serve por interacción.
- Impacto de intradía (recuperación de SL tras desvíos).

## Siguiente paso técnico recomendado en este repositorio

1. Reemplazar SQLite por PostgreSQL y crear migraciones.
2. Agregar tablas de `agent`, `skill`, `shift`, `schedule`, `adherence_event`.
3. Incorporar un módulo de optimización de turnos (OR-Tools CP-SAT).
4. Añadir endpoints para backtesting de forecast y error tracking.
5. Crear dashboard de cobertura requerida vs programada por intervalo.

