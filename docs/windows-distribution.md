# Distribución Windows reproducible y actualizable

## Alcance implementado

El candidato usa PyInstaller `onedir`. La especificación incluye AIOpenStudio, migraciones y
licencia; excluye PyTorch, modelos, runtimes externos, documentación local, `.env`, `.vscode`,
`data/`, bases, perfiles, logs, outputs y cachés.

En modo empaquetado, las rutas relativas se resuelven bajo los directorios devueltos por
`platformdirs`, nunca junto al ejecutable. La configuración opcional se busca en el directorio de
configuración del usuario y los datos en el directorio de datos local. Una ruta absoluta elegida por
el usuario se conserva.

La biblioteca de modelos usa `data/models` como valor relativo portable. Los comandos aceptan una
carpeta explícita antes del subcomando:

```powershell
python scripts/model_library.py --root D:\AIModels init
python scripts/model_library.py --root D:\AIModels download llm.gemma4-e4b-it-qat
```

El valor relativo puede conservarse; ninguna ruta de un desarrollador es un default válido.

## Construcción

PyInstaller no se instala automáticamente. Tras una autorización explícita, el entorno de build se
prepara con el extra fijado `distribution` y se ejecuta:

```powershell
& .\scripts\build_windows_distribution.ps1
```

El script:

1. falla si PyInstaller no está instalado;
2. obtiene versión y `SOURCE_DATE_EPOCH` del repositorio;
3. construye `onedir` sin UPX;
4. rechaza configuración y rutas privadas;
5. genera inventario de dependencias, avisos y textos de licencia;
6. crea un ZIP determinista y un manifiesto de update con SHA-256;
7. valida el conjunto y genera `AIOpenStudio-<versión>-candidate-report.json`.

Los resultados quedan bajo `build/windows-distribution/`, ignorado por Git.

## Barrera de privacidad

`scripts/verify_windows_distribution.py` rechaza:

- cualquier `.env`, `.vscode`, `.git`, `data`, `outputs` o `cache` en la raíz del bundle;
- SQLite, catálogo de modelos, perfil PostgreSQL y checklist local;
- rutas ASCII o UTF-16 con `C:\Users\<usuario>`;
- `USERPROFILE`, `USERNAME` y la raíz del repositorio de la máquina de build.

Una coincidencia bloquea la publicación. No se permite una lista de excepciones para configuración
privada; un falso positivo debe resolverse eliminando el dato del artefacto.

## Cumplimiento y barrera de candidatura

`scripts/generate_dependency_inventory.py` recorre el cierre de dependencias instalado para los
extras PostgreSQL, Whisper y Fooocus. El resultado no contiene rutas absolutas y conserva, cuando
están disponibles, los textos declarados por cada distribución. El modo estricto detiene el build
ante una dependencia ausente o licencia `UNKNOWN`.

El bundle debe incluir `LICENSE`, `THIRD_PARTY_NOTICES.txt`, `dependency-inventory.json`, la guía y
la solución de problemas. `scripts/validate_release_candidate.py` comprueba esos archivos, repite la
barrera de privacidad, valida versión/tamaño/SHA-256 del manifiesto y exige que el ZIP coincida
exactamente con el bundle aprobado. Una firma ausente es advertencia para un candidato local y pasa
a error con `--require-signature` para una publicación firmada.

La revisión automatizada no sustituye la auditoría de licencias de checkpoints ni la validación en
un Windows limpio. Ambas están en `docs/release-checklist.md`.

## Actualización

El manifiesto actual describe el artefacto, versión, plataforma, tamaño, hash y contrato. Todavía
no descarga ni aplica updates. Antes de distribuir se deben implementar y probar el proceso externo
de activación lado a lado, firma, primer arranque y rollback descritos en el ADR. El ZIP actual es un
artefacto portable candidato, no un autoactualizador terminado.
