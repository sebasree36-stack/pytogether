# CLAUDE.md — PyTogether

IDE de Python colaborativo en el navegador. Fork de `SJRiz/pytogether`; este remoto es
`github.com/sebasree36-stack/pytogether`. Uso previsto por el dueño del fork: auto-hospedarlo en
un PC con Windows 11 para dar clases de Python a niños.

**Idioma:** el dueño del repo no es programador de oficio y prefiere explicaciones en español.
Explica en español qué vas a hacer y por qué antes de cualquier paso importante.

## Levantar el entorno de desarrollo

Desde la raíz del repo (`pytogether/`):

```bash
npm install     # instala raíz + frontend/reactapp (via postinstall)
npm run dev     # concurrently: vite (frontend) + docker compose -f docker-compose-dev.yaml up --build
```

- Frontend: http://localhost:5173 (Vite)
- Backend: http://localhost:8000 (Django), admin en `/admin/`
- `Ctrl+C` detiene ambos.

Usuarios de prueba creados automáticamente por el servicio `django-init`:
`test1@gmail.com` y `test2@gmail.com`, ambos con contraseña `testtest` (son superusuarios).

Configuración de desarrollo ya lista en el repo (no son secretos reales):
`backend/.env.dev` y `frontend/reactapp/.env.development`.

## Arquitectura

### Backend — Django ASGI (`backend/`)

Corre sobre **Daphne/Channels** porque mezcla HTTP (DRF) y WebSockets.
`backend/backend/asgi.py` enruta: HTTP → Django normal; WebSocket → `JWTAuthMiddleware` → `codes.routing`.

Apps Django:

| App | Modelo | Rol |
|---|---|---|
| `users` | `User` (email como login, sin username), `Feedback` | auth, JWT, OAuth Google/Microsoft |
| `usergroups` | `Group` (owner, miembros M2M, `access_code` aleatorio) | "clases"/equipos |
| `projects` | `Project` (pertenece a un Group) | archivos de código |
| `codes` | `Code` (1-a-1 con Project, campo `content`) | snapshot persistido del código |

Settings por entorno en `backend/backend/settings/`: `base.py` + `dev.py` / `prod.py` / `selfhost.py`.
Se elige con `DJANGO_SETTINGS_MODULE`. **Para auto-hospedar se usa `selfhost.py`**, no `dev.py`.

Rutas HTTP (`backend/backend/urls.py`):
- `/api/...` → `users.urls` (login `auth/token/`, refresh, register, `me/`, google, microsoft, feedback,
  validación de links compartidos, snippets públicos)
- `/groups/...` → `usergroups.urls`, que anida `/groups/<group_id>/projects/...` → `projects.urls`
- `/admin/` → admin de Django

Ruta WebSocket (`backend/codes/routing.py`):
`ws/groups/<group_id>/projects/<project_id>/code/` → `YjsCodeConsumer`.

### El corazón: `backend/codes/consumers.py`

`YjsCodeConsumer` es el archivo más importante del backend. Un solo WebSocket por usuario multiplexa
todo lo colaborativo según el campo `type` del mensaje:

`update` (deltas Yjs del código) · `request_sync` · `awareness` (cursores/selecciones) ·
`chat_message` · `join_voice` / `leave_voice` / `voice_signal` (señalización WebRTC para la voz) · `ping`

Flujo del estado del código:
1. El cliente manda un delta Yjs en base64 (`update_b64`).
2. El servidor toma un **lock de Redis** por proyecto, aplica el delta al `YDoc` guardado en Redis
   (`project_ydoc:<id>`), re-serializa y lo guarda. Un delta corrupto provoca `force_disconnect`
   para que ese cliente resincronice.
3. Marca el proyecto en el set `projects:dirty`.
4. Hace broadcast del delta al resto del room vía channel layer (Redis).
5. **Celery beat** corre `codes.tasks.snapshot_dirty_projects` cada `AUTO_SAVE_INTERVAL` segundos:
   saca los proyectos sucios y los persiste a PostgreSQL con `utils/redis_helpers.persist_ydoc_to_db`.

Es decir: **Redis es la fuente de verdad en vivo, PostgreSQL es el respaldo periódico.** Si Redis se
pierde, se pierde hasta un intervalo de autoguardado de trabajo.

Claves de Redis y helpers en `backend/utils/redis_helpers.py`:
`project_ydoc:<pid>` (bytes del CRDT) · `project_active:<pid>` (hash user_id → nº de pestañas, TTL 60s
renovado por heartbeat) · `voice_room:<pid>` · `user_profile:<uid>` (email + color asignado) ·
sets globales `projects:active` y `projects:dirty`.

Tareas Celery (`backend/codes/tasks.py`, agendadas en `backend/backend/celery.py`):
`snapshot_dirty_projects` (autoguardado) · `cleanup_ghost_projects` (proyectos marcados activos sin
nadie conectado) · `log_daily_stats_task` · `record_system_resources_task`.

### Autenticación

- JWT (`rest_framework_simplejwt`): access token de 60 min guardado en `sessionStorage`;
  refresh token de 30 días en cookie HttpOnly, con rotación.
- `frontend/reactapp/axiosConfig.jsx` tiene el interceptor que ante un 401 llama a
  `/api/auth/token/refresh/`, encola las peticiones fallidas y las reintenta. También emite los
  eventos `backendDown` / `backendUp` que `App.jsx` usa para mostrar la pantalla "System Offline".
- WebSocket: el token JWT va como query param `?token=...`, validado en
  `backend/backend/jwt_auth_middleware.py`.
- **Links compartidos:** `django.core.signing.TimestampSigner` firma un objeto `{gid, pid, type}`.
  Permite entrar a un proyecto sin ser miembro del grupo. Se valida tanto en HTTP
  (`projects/views.py`) como en el consumer.

### Frontend — React + Vite (`frontend/reactapp/`)

Rutas en `src/App.jsx`. Las importantes:
`/` (landing `About`) · `/login` · `/register` · `/home` (`GroupsProjects`) ·
`/groups/:groupId/projects/:projectId` (`PyIDE`, el IDE colaborativo) ·
`/playground` y `/snippet/:token` (`OfflinePlayground`, sin backend) · `/embed/:token` ·
`/join-shared/:token`.

- `src/pages/PyIDE.jsx` (~930 líneas) es el componente grande: conecta el WebSocket, el `Y.Doc`,
  CodeMirror (`y-codemirror.next`), chat, voz y dibujo. `src/components/CodeLayout.jsx` es el layout.
- Hooks en `src/hooks/`: `usePyRunner` (ejecutar Python), `useSharedCanvas` / `useLocalCanvas`
  (dibujo sobre el editor), `useVoiceChat` (`simple-peer`/WebRTC), `useVersionCheck`, `useUmamiHeartbeat`.

### Ejecución de Python — Pyodide en el navegador

**El código de los alumnos nunca se ejecuta en el servidor.** Corre en un Web Worker con Pyodide:
`src/pyrunner/Worker.js` (envoltorio `python_runner`/`PyodideRunner`, intercepta `plt.show()` para
devolver la imagen en base64) y `src/pyrunner/TaskClient.js` (Comlink), consumidos por
`src/hooks/usePyRunner.js`.

Dos consecuencias prácticas:
- `input()` necesita un **service worker** (`src/sw.js` + `sync-message`), y los service workers
  **no funcionan sobre HTTP sin TLS** (salvo en `localhost`). En una instancia auto-hospedada por IP
  sin HTTPS, `input()` no funcionará.
- `vite.config.js` fija las cabeceras `Cross-Origin-Opener-Policy: same-origin` y
  `Cross-Origin-Embedder-Policy: require-corp`, requeridas por `SharedArrayBuffer`. No las quites.

Plantillas de código inicial (`NONE_TEMPLATE`, `PYTEST_TEMPLATE`, `PLT_TEMPLATE`) están al final de
`backend/backend/settings/base.py`.

## Despliegue

- `docker-compose-dev.yaml` — desarrollo: `django-builder` (solo construye la imagen
  `pytogether-backend:latest`), `django-init` (migraciones + superusuarios de prueba), `django`
  (`runserver`), `db` (postgres:15), `redis`, `celery`, `celery-beat`.
  Los servicios que no son `django-builder` **no tienen sección `build:`**, solo reusan la imagen.
- `self-hosting/docker-compose.yaml` + `self-hosting/nginx.conf` — auto-hospedaje. Requiere
  `npm run build` previo del frontend y ajustar `PROD=selfhost`, `DOMAIN`, `USE_HTTPS` en
  `backend/.env.dev`, más `VITE_DOMAIN` en `frontend/reactapp/.env.production`.
  Google Login no funciona auto-hospedado.
- `docker-compose.yaml` (raíz) + `.github/workflows/deploy.yml` — producción del autor original
  (VPS). No aplica a este fork.

## Notas y trampas conocidas

- **El repo vive dentro de OneDrive** (`...\OneDrive\Escritorio\A3\Algorithmics\PyTogether`).
  OneDrive intenta sincronizar `node_modules` y puede bloquear archivos. Si `npm install` o Vite
  fallan con errores raros de permisos, la solución es mover el repo a una ruta fuera de OneDrive
  (por ejemplo `C:\dev\pytogether`).
- En el primer `npm run dev`, Docker Compose imprime varias veces
  `Error pull access denied for pytogether-backend` mientras `django-builder` todavía construye la
  imagen. **Es ruido esperado**, no un fallo: los demás servicios la encuentran localmente cuando
  el build termina.
- El primer build tarda bastante porque `y_py==0.6.2` se compila desde fuente (extensión en Rust);
  no hay wheel precompilado para Python 3.13, que es la base del `Dockerfile`.
- **`/admin/` devuelve 500 en desarrollo.** `django.contrib.sites` está en `INSTALLED_APPS` pero
  `base.py` nunca define `SITE_ID`, así que Django resuelve el sitio por host y no existe una fila
  `Site` con dominio `localhost:8000` → `Site.DoesNotExist`. La API y el frontend no se ven
  afectados. Se arregla con `SITE_ID = 1` en settings o creando la fila `Site` correspondiente.
- **`secure=True` hardcodeado en dos cookies.** `backend/users/views.py:123` (login con email) y
  `:182` (registro) fijan `secure=True` en la cookie `refresh_token`, en vez de usar
  `settings.SESSION_COOKIE_SECURE` como sí hacen las rutas OAuth y `users/tokens.py`. En
  `localhost` no molesta (los navegadores lo tratan como contexto seguro), pero **auto-hospedando
  por IP de LAN sobre HTTP plano (`USE_HTTPS=False`) el navegador rechaza esa cookie**: el refresh
  token nunca se guarda y el alumno queda desconectado al expirar el access token (60 min), sin
  poder renovarlo. Si se auto-hospeda sin HTTPS, hay que corregir esas dos líneas o poner un proxy
  con TLS delante.
- `backend/codes/views.py` está vacío a propósito: todo lo de código va por WebSocket.
- Límite de tamaño de documento: `MAX_MESSAGE_SIZE` ≈ 70 KB (`base.py`). Updates o snapshots más
  grandes se descartan silenciosamente (solo con `print`).
- El throttling de DRF (`100/minute` por usuario) puede molestar en un salón con muchos alumnos;
  se ajusta en `REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]` en `base.py`.
- En `dev.py` los permisos de DRF se abren a `AllowAny` globalmente, pero cada vista declara
  `@permission_classes` explícitamente, así que siguen protegidas.
- No hay suite de tests real: los `tests.py` de las apps están vacíos.

## Comandos útiles

```bash
# Ver contenedores y logs
docker compose -f docker-compose-dev.yaml ps
docker compose -f docker-compose-dev.yaml logs -f django

# Comandos de Django dentro del contenedor
docker compose -f docker-compose-dev.yaml exec django python manage.py migrate
docker compose -f docker-compose-dev.yaml exec django python manage.py createsuperuser
docker compose -f docker-compose-dev.yaml exec django python manage.py shell

# Parar todo / borrar también la base de datos
docker compose -f docker-compose-dev.yaml down
docker compose -f docker-compose-dev.yaml down -v

# Lint del frontend
npm run lint --prefix frontend/reactapp
```
