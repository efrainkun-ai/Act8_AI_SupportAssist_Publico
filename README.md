# Actividad 8 · AI-SupportAssist

Observabilidad y respuesta ante incidentes del servicio de triaje financiero de la Fase 2. El modelo reutilizado es `act6-nb-financial-v1` con umbral 0.24. La simulación emplea una copia local y tráfico sintético.

El servicio reutiliza el modelo verificado de la Fase 2. Las evidencias verificadas incluyen el exporte portable de MLflow. El comando de reproducción genera la base de datos y los artefactos de una nueva ejecución local.

## Contenido

| Ruta | Contenido |
|---|---|
| [Documento técnico](documentos/01_Documento_Tecnico_Actividad_8.pdf) | Métricas, logs, trazas, alertas, tablero, drift y respuesta. |
| [Evidencia de implementación](documentos/02_Evidencia_Implementacion_Actividad_8.pdf) | Exporte de MLflow, series operativas y verificación del tablero. |
| [Simulación de incidentes](documentos/03_Simulacion_Incidentes_Actividad_8.pdf) | Cronología, ejecución de cada runbook y recuperación. |
| [Runbooks](runbooks/) | Procedimientos RB-01 y RB-02, con criterios de escalamiento y cierre. |
| [Código de monitoreo](observabilidad/) | Sondas, agregación, alertas, simulación, acciones y tablero. |
| [Servicio](servicio/) | FastAPI, modelo y recursos heredados de la Fase 2, con instrumentación adicional. |
| [Evidencia verificada](evidencia/verificada/) | Ventanas, sondas HTTP, logs, spans, alertas, acciones, resultados y exportación de MLflow. |

## Reproducción local

Se utilizó Python 3.12. Desde la carpeta Actividad8:

```console
python -m venv .venv
```

En PowerShell, usa `.\.venv\Scripts\Activate.ps1`. En Linux o macOS, usa `source .venv/bin/activate`. Antes de ejecutar, descarga [financial_nb.joblib desde la revisión fija de la Fase 2](https://github.com/efrainkun-ai/Act5_GP/blob/eba2c4fe6ecbda062e71d6d3f71f8b6a29f97685/Fase2/models/financial_nb.joblib) con el botón de descarga del archivo. Guárdalo en `servicio/models/financial_nb.joblib`. El servicio verifica el SHA-256 registrado en `servicio/models/metadata.json` antes de cargarlo.

En la evidencia pública, [servicio_publico.txt](evidencia/verificada/servicio_publico.txt) resume el registro de arranque citado como `servicio.log` en el PDF. La prueba también se puede comprobar en `telemetria.jsonl` y `acciones.jsonl`.

Instala y ejecuta:

```console
python -m pip install -r requirements.txt
python -m observabilidad.execute --output evidencia/nueva_ejecucion --interval 2
```

El comando inicia FastAPI en un puerto local, ejecuta las sondas, simula las fallas en un proceso aislado y cierra el servicio al terminar. Usa un nombre de carpeta nuevo para cada ejecución. El paquete inválido se crea en un directorio temporal. El estimador de la Fase 2 se carga desde el paquete verificado. Las solicitudes son sintéticas. No se necesitan credenciales ni se llama al despliegue público.

En otra terminal, levanta el tablero y abre [http://127.0.0.1:8765](http://127.0.0.1:8765):

```console
python -m observabilidad.dashboard --evidence evidencia/nueva_ejecucion
```

El tablero consulta el estado agregado cada segundo. Para revisar una ejecución ya concluida, indica `--evidence evidencia/verificada`.

Para consultar en MLflow la ejecución generada en tu equipo:

```console
$env:MLFLOW_DISABLE_TELEMETRY = "true"
mlflow server --backend-store-uri sqlite:///evidencia/nueva_ejecucion/mlflow.sqlite --host 127.0.0.1 --port 5000
```

En Linux o macOS, usa `export MLFLOW_DISABLE_TELEMETRY=true` antes de iniciar el servidor. Abre [http://127.0.0.1:5000](http://127.0.0.1:5000). El almacén de artefactos de MLflow conserva rutas de la máquina que creó la ejecución. La carpeta verificada incluye una exportación legible y los archivos de evidencia originales. Una ejecución nueva reconstruye una base consultable y las rutas válidas para tu equipo.

Para correr las 30 pruebas de regresión heredadas y las cinco de observabilidad desde la raíz:

```powershell
$env:PYTHONPATH = "servicio"
python -m pytest -q servicio/tests observabilidad/test_monitor.py
```

En Linux o macOS, usa `PYTHONPATH=servicio python -m pytest -q servicio/tests observabilidad/test_monitor.py`.
