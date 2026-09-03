FROM python:3.12-slim

WORKDIR /app

# requirements primero: la capa de dependencias se cachea entre builds
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Los archivos del servidor. Si se agrega otro módulo, va aquí también: lo que no
# se copia no existe dentro del contenedor y el arranque truena en el import.
COPY kriterius_mx.py server_http.py uso.py padron.py cobros.py suscripcion.py limites.py alta_manual.py promos.py correo.py expedientes.py vigilancia.py historial.py plus_tools.py tepjf.py ./

# El snapshot del IUS Electoral. Es la fuente TEPJF entera: sin este archivo el
# servidor arranca, pero sus tres tools responden "fuente no disponible".
COPY kriterius_datos/ kriterius_datos/

# Render inyecta PORT; 8000 es solo el valor por defecto para correr en local
ENV PORT=8000
EXPOSE 8000

CMD ["python", "server_http.py"]
