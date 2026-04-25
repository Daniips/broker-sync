# Contribuir a tr-sync

¡Gracias por interesarte en mejorar tr-sync! Esta guía describe cómo contribuir.

---

## Cómo reportar un bug

Abre un issue describiendo:

1. **Qué esperabas** que pasase.
2. **Qué pasó** en realidad (incluye el output completo de la consola, mejor con `make sync` o `make renta` que estés ejecutando).
3. **Cómo reproducirlo** (versión de Python, sistema operativo, comando ejecutado, contenido relevante de `config.yaml` **sin tus datos personales** — sustituye Sheet ID/ISINs/nombres por placeholders).
4. **Versión** de `tr-sync`: `git rev-parse HEAD`.

Si el bug es relativo a un evento concreto de TR, lánzalo:

```bash
.venv/bin/python inspect_events.py --eventtype <TIPO>
```

y pega el JSON resultante (ofusca cualquier dato personal antes).

---

## Cómo proponer una feature

Abre un issue describiendo:

- El **caso de uso** (cuándo y por qué te haría falta).
- Si ya tienes en mente cómo implementarla.

Antes de empezar a programar, espera a que alguien (probablemente el mantenedor) la valide. Hay features que tienen sentido para ciertos usuarios pero no encajan con el diseño general.

---

## Cómo enviar un Pull Request

1. **Fork** del repo y clónalo.
2. **Crea una rama** descriptiva: `git checkout -b fix/dividend-without-isin` o `feat/csv-export`.
3. **Asegúrate de que los tests pasan**: `make test`.
4. **Añade tests** para tu cambio si es lógica nueva. El proyecto valora tests de la lógica pura (parsers, FIFO, agregadores) sobre tests con red.
5. **Haz commit** con mensajes descriptivos. Sigue [Conventional Commits](https://www.conventionalcommits.org/) si puedes (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`).
6. **Abre el PR** describiendo qué hace el cambio y cómo probarlo.

### Estilo de código

- **Python 3.11+**.
- Sin formateador automático impuesto, pero respeta el estilo existente (docstrings cortas en español, un par de líneas máximo; nombres en `snake_case`).
- No añadas dependencias nuevas sin justificación. Si las añades, mete la pinned version en `requirements.txt`.
- No añadas comentarios obvios; solo cuando expliquen el *por qué*, no el *qué*.

### Tests

- Los tests están en `test_tr_sync.py` y usan `unittest` (sin pytest, sin red).
- Si tu cambio toca un parser de eventos de TR, añade un test con un fixture mínimo que reproduzca el JSON real de TR.
- Si tu cambio toca el FIFO o agregadores, añade casos límite (cero shares, fracciones, dedup, mezcla de regalos…).

### Documentación

Si tu cambio:

- **Cambia o añade un campo de `config.yaml`** → actualiza `config.example.yaml` y `CONFIG.md`.
- **Cambia algo del informe IRPF** → actualiza `RENTA.md`.
- **Cambia la estructura esperada del Sheet** → actualiza `SHEET_TEMPLATE.md`.
- **Añade un comando** → actualiza `Makefile` (ayuda incluida) y `README.md` (sección de comandos).

---

## Pruebas locales sin tocar tu Sheet real

Antes de enviar un PR, verifica que tus cambios no rompen tu propio sync:

```bash
make verify        # portfolio dry-run
make test          # tests unitarios
```

Si vas a probar un sync completo, considera apuntar `config.yaml` a un Sheet de prueba para no contaminar el real.

---

## Contacto

Si tienes una duda que no encaja como issue (p.ej. consulta arquitectónica), abre una discusión en GitHub Discussions si está habilitado, o pon contexto en un issue marcado como `question`.

---

## Licencia

Al contribuir aceptas que tu código se distribuya bajo la licencia MIT del proyecto.
