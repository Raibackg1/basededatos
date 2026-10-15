"""Cockpit — capa de observabilidad honesta sobre el motor de detección.

Nada de lo que hay aquí decide trades: solo LEE y MUESTRA. La regla es que el
cockpit nunca puede mostrar un número que el pipeline no pueda justificar, y
siempre etiqueta cada métrica con su procedencia y su sesgo.

Componentes:
- ``snapshot``: escaneo de solo lectura de la base canónica -> JSON cacheado.
- ``server``:   servidor HTTP local (stdlib) que sirve la API y el visor 3D.
- ``launch``:   arranca servidor + abre ventana nativa (pywebview) o navegador.
"""
