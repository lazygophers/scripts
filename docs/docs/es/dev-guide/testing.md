# Pruebas

```bash
python3 -m unittest discover -s tests -q
```

La suite está en `tests/` en la raíz. Los comandos nuevos deberían añadir tests unitarios: la lógica se prueba en `lib/{nombre}.py`; las entradas finas solo transmiten. `tests/test_lazyhelp.py` verifica que bin/ coincida con `TOOLS` — recuerda registrar las nuevas entradas.
