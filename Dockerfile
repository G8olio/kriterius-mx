FROM python:3.12-slim

WORKDIR /app

# requirements primero: la capa de dependencias se cachea entre builds
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Los archivos del servidor. Si se agrega otro módulo, va aquí también: lo que no
# se copia no existe dentro del contenedor y el arranque truena en el import.
COPY kriterius_mx.py server_http.py uso.py tepjf.py sjf_local.py ./

# Los datos locales: el snapshot del IUS Electoral (la fuente TEPJF entera) y el
# acervo de la Gaceta del SJF (respaldo de buscar_tesis y ver_tesis cuando el API de
# la Corte está bloqueado). Sin ellos el servidor arranca, pero esas tools responden
# "fuente no disponible".
COPY kriterius_datos/ kriterius_datos/

# El índice FTS del acervo del SJF se construye al arrancar y ocupa ~440 MB. Va a una
# ruta escribible fuera del código; si el disco no da, sjf_local cae solo a un índice
# en memoria (~500 MB de RSS) y sigue funcionando.
ENV KRITERIUS_CACHE_DIR=/tmp/kriterius-cache

# Render inyecta PORT; 8000 es solo el valor por defecto para correr en local
ENV PORT=8000
EXPOSE 8000

CMD ["python", "server_http.py"]
