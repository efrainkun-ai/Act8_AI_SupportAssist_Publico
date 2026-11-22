# Monitorización y respuesta a incidentes de AI-SupportAssist

**Actividad 8 · Temas 19 y 20**

## Objetivo y continuidad con la Fase 2

Instrumentar el servicio de triaje financiero para detectar fallas de disponibilidad y cambios en los datos de entrada, priorizar alertas y demostrar la recuperación en un ejercicio controlado. Se extendió la API FastAPI de la Fase 2 y se conservó el modelo act6-nb-financial-v1 con TF-IDF, MultinomialNB y umbral 0.24. La referencia histórica es F1 financiero de 0.9973, obtenido con 4,803 registros sintéticos en inglés.

## Diseño de observabilidad

Las sondas emiten solicitudes HTTP a /ready y /api/v1/predict. La API registra un evento JSON y un span por solicitud, más un span hijo para la inferencia. El identificador de traza enlaza la sonda, el log y los spans. Los registros excluyen el cuerpo de los tickets. Si el proceso no inicia, el log conserva el error de arranque y la sonda externa mide la ausencia de respuesta. El monitor envía métricas a MLflow por ventana y actualiza el estado local. El tablero consulta ese estado cada segundo. MLflow usa SQLite local con la telemetría externa desactivada.

| Señal | Medición | Objetivo o decisión |
|---|---|---|
| Disponibilidad | Solicitudes exitosas sobre 25 sondas por ventana, una de preparación y 24 de predicción válida. La ausencia de respuesta cuenta como fallo. | SLO de ejercicio ≥99 % y presupuesto de error de 1 %. P1 si el *burn rate* alcanza 10× en dos ventanas consecutivas. |
| Latencia | p95 HTTP sobre sondas de predicción exitosas, con método de rango más próximo. También se registra el p95 interno. | Al menos 95 % de solicitudes dentro del límite. El límite es el mayor de 25 ms o dos veces el p95 HTTP de la línea base. Presupuesto de 5 %. P2 a partir de 2× en dos ventanas con mínimo 20 mediciones. |
| Drift léxico | Divergencia Jensen-Shannon (JSD) en bits entre dos distribuciones binarias, vocabulario reconocido y desconocido, en lotes sintéticos declarados en inglés. | P2 si JSD ≥0.08 durante dos ventanas con mínimo 20 observaciones cada una. El umbral es didáctico y requiere calibración antes de aplicarse en operación. |
| Clases de control | Reembolsos, facturas y pagos frente a contraseñas, cuentas y pedidos, con etiqueta esperada. | Verificar que las sondas conocidas conserven la clase tras la recuperación. |

El *burn rate* divide la proporción de eventos malos por el margen de error. Un 10 % de fallos contra un margen de 1 % equivale a 10×. Las alertas se abren después de dos ventanas consecutivas con incumplimiento y se cierran después de dos ventanas consecutivas recuperadas. Se conserva una alerta activa por regla y se registran los duplicados suprimidos. La indisponibilidad tiene prioridad sobre latencia y drift. Los rechazos previstos por idioma se registran por separado. Las ventanas son lotes de sondas con una pausa programada y un denominador conservado en la evidencia.

## Resultados del ejercicio

La ejecución registró **15 ventanas** y **743 sondas HTTP**. La línea base incluyó 96 solicitudes etiquetadas de inferencia y obtuvo un p95 HTTP de **4.006 ms**. El límite resultante fue **25 ms**. Pasaron **35 pruebas automatizadas**, incluidas las 30 heredadas de la Fase 2.

| Incidente | Evidencia observada | Intervención y cierre | Detección / recuperación |
|---|---|---|---|
| RB-01 · Integridad del modelo | 0/25 sondas de disponibilidad durante la falla y burn rate de 100×. P1 en ventana 7. | Reinicio con el paquete estable, 25/25 sondas exitosas en cada ventana de validación y cierre en ventana 10. | 3.54 s / 7.18 s |
| RB-02 · Drift léxico | 16/24 entradas sin vocabulario por ventana de falla y JSD de 0.459 bits. P2 en ventana 12. Disponibilidad de 25/25. | 48 solicitudes identificadas en cola local de revisión. JSD de 0 en ambas ventanas de validación y cierre en ventana 15. | 2.06 s / 5.87 s |

La ventana de control tuvo ocho respuestas 422 esperadas por idioma y cero alertas. Cada incidente abrió una alerta y suprimió una repetición. Ninguna alerta quedó abierta. MLflow registró la ejecución completada con ID **4ef2c00995e146cfad88c48e50f63068** y conservó 222 puntos de métricas. La exportación incluye parámetros, métricas y nombres de artefactos. La reproducción de las 15 ventanas verificó por HTTP que el tablero mostró la apertura y el cierre de ambas alertas. El detalle de la prueba de JavaScript se conserva en tablero_verificacion.json.

[Gráfica de series operativas](evidencia/verificada/series_operativas.png) · [Ventanas en CSV](evidencia/verificada/metricas_ventanas.csv) · [Exportación de MLflow](evidencia/verificada/mlflow_export.json)

Los tiempos se miden con reloj monotónico. El tiempo de detección va de la inyección a la apertura de la alerta. El tiempo de recuperación va de la apertura hasta la segunda ventana normal. La tercera ventana de cada falla se mantuvo deliberadamente para comprobar la deduplicación y su duración forma parte del tiempo de recuperación medido.

## Cobertura de la rúbrica

| Criterio | Evidencia verificable |
|---|---|
| Monitorización y observabilidad | Estado por ventana, sondas, telemetría, tablero operativo y gráfica de series. |
| Alertas inteligentes | Configuración, registro de alertas, ventanas con umbrales persistentes, cierres y control de ruido. |
| Simulación y runbooks | Runbooks RB-01 y RB-02, log del servicio, acciones y resumen de los incidentes. |
| Respuesta automatizada | Orquestación del reinicio con paquete verificado, validación por sondas y cola local de revisión con los identificadores de las trazas. |
| Seguimiento y reproducibilidad | Exportación de MLflow, pruebas automatizadas y comandos de ejecución en el README. |

## Alcance

El ejercicio usa tráfico sintético, un proceso local de Uvicorn y volúmenes pequeños. Los objetivos corresponden a esas condiciones. La API pública de la Fase 2 queda fuera del ejercicio. La reversión cambia el paquete con el que arranca el proceso de laboratorio. El escalamiento se representa con una cola local. La regla de latencia se comprobó con pruebas automatizadas y se monitorizó en la ejecución. Los incidentes ejecutados fueron integridad del modelo y drift léxico.

El cambio probado afecta cobertura léxica. Para comprobar si también baja la precisión o el recall en operación se necesitan tickets etiquetados y evaluación por segmento. Si persiste una caída confirmada, el responsable del modelo puede preparar y validar una nueva versión candidata.

## Referencias

- Universidad Tecmilenio. (s. f.). *Tema 19. Monitorización proactiva y alertas en tiempo real* [Material de curso].
- Universidad Tecmilenio. (s. f.). *Tema 20. Mantenimiento, respuesta a incidentes y gobernanza operativa* [Material de curso].
- Universidad Tecmilenio. (s. f.). *Rúbrica de evaluación para actividad 8* [Rúbrica].
- Mendoza Torres, T. J. (2026, 5 de noviembre). *Gestión de proyectos de inteligencia artificial. Sesión 9* [Diapositivas]. Universidad Tecmilenio.
- Mendoza Torres, T. J. (2026, 12 de noviembre). *Gestión de proyectos de inteligencia artificial. Sesión 10* [Diapositivas]. Universidad Tecmilenio.
- MLflow. (s. f.). *MLflow Tracking APIs*. https://mlflow.org/docs/latest/tracking/tracking-api/
