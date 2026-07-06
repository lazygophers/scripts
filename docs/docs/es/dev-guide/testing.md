# Pruebas

```bash
python3 -m unittest discover -s tests -q
```

La suite de pruebas se encuentra en `tests/` en la raíz del repositorio. Se recomienda añadir pruebas unitarias correspondientes para comandos nuevos, manteniendo la granularidad de prueba separada entre entrada ligera y lógica de negocio (la lógica de negocio se prueba bajo `lib/commands/`, las entradas ligeras solo transmiten datos y no necesitan pruebas separadas).
