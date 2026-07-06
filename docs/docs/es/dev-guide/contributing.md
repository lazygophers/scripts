# Contribuir

- Los PR deben incluir las pruebas unitarias necesarias.
- Los scripts nuevos siguen los dos pasos de [« Añadir un script »](./add-script.md), manteniendo la separación entre entrada ligera y lógica de negocio.
- Las capacidades compartidas se colocan en `lib/{dominio}.py`, reutilizables entre comandos, evitando duplicar código en entradas ligeras o comandos individuales.
- Las operaciones Git usan `git` estándar, evitando envolturas interactivas ; antes de la automatización, son obligatorias las verificaciones de limpieza del árbol de trabajo y las reversiones.
