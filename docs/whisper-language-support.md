# Idiomas y tareas de Whisper

Este documento fija la semántica usada por AIOpenStudio con `faster-whisper 1.2.1`. Idioma de
entrada e idioma de salida no son listas equivalentes y la interfaz no debe presentarlos como dos
selectores simétricos.

## Regla de entrada y salida

| Tarea | Idioma de entrada | Idioma de salida |
|---|---|---|
| `transcribe` | automático o uno admitido por el modelo | el idioma del audio |
| `translate` | automático o uno admitido por el modelo multilingüe | **inglés (`en`) solamente** |

Whisper no ofrece traducción nativa español→francés, inglés→alemán ni cualquier destino elegible.
Para esos casos se necesitaría un segundo backend de traducción, que queda fuera de esta fase.

Los snapshots `.en` aceptan sólo audio en inglés, no necesitan detección multilingüe y no exponen
traducción. Los snapshots `turbo` son multilingües para transcripción, pero OpenAI advierte que no
fueron entrenados para traducción; AIOpenStudio bloquea `translate` en vez de devolver silenciosamente
el texto original.

## Idiomas de entrada multilingüe

La lista fijada por `faster-whisper 1.2.1` contiene 100 códigos:

```text
af am ar as az ba be bg bn bo br bs ca cs cy da de el en es et eu fa fi fo fr
gl gu ha haw he hi hr ht hu hy id is it ja jw ka kk km kn ko la lb ln lo lt lv
mg mi mk ml mn mr ms mt my ne nl nn no oc pa pl ps pt ro ru sa sd si sk sl sn
so sq sr su sv sw ta te tg th tk tl tr tt uk ur uz vi yi yo zh yue
```

Los códigos menos obvios incluyen `haw` (hawaiano), `jw` (javanés), `tl` (tagalo), `zh`
(chino) y `yue` (cantonés). Esta lista describe el vocabulario del backend, no una garantía de la
misma calidad para todos los idiomas, acentos o tamaños de modelo.

## Descubrimiento local

AIOpenStudio lee `num_languages` desde el `config.json` del snapshot ya instalado, sin cargar pesos
ni acceder a la red:

- `num_languages = 1` o variante terminada en `.en`: sólo `en` como entrada;
- modelo multilingüe: los 100 códigos anteriores;
- variante con `turbo`: transcripción multilingüe, traducción deshabilitada;
- configuración ilegible o incompleta: catálogo oficial como fallback, marcado como no verificado.

El descriptor del modelo conserva `source_language_codes`, `translation_target_codes`, capacidad de
detección y una explicación de cualquier limitación. El servicio valida la solicitud antes de cargar
el modelo, de modo que un idioma o tarea incompatible falla con un mensaje localizado.

## Fuentes verificadas

- [Parámetros y lista de idiomas de faster-whisper 1.2.1](https://github.com/SYSTRAN/faster-whisper/blob/v1.2.1/faster_whisper/transcribe.py)
- [Uso de transcripción y traducción en faster-whisper 1.2.1](https://github.com/SYSTRAN/faster-whisper/blob/v1.2.1/README.md)
- [Modelos y límite de traducción de Whisper turbo](https://github.com/openai/whisper/blob/main/README.md)

