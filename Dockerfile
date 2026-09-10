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

# El índice FTS del acervo del SJF se construye AQUÍ, en el build, y viaja dentro de
# la imagen. No en el arranque: el contenedor tiene 512 MB de RAM y un vCPU
# compartido, y armar 34 041 tesis ahí tarda lo suficiente para que el readiness
# probe falle y la plataforma mate el proceso. El build, en cambio, dispone de
# 15 GiB de RAM y 24 GiB de disco.
#
# KRITERIUS_SJF_SOLO_LECTURA=1 es el cinturón: en runtime el servidor abre el índice
# si sirve, y si no se declara sin respaldo, pero nunca intenta reconstruirlo.
ENV KRITERIUS_CACHE_DIR=/app/cache
ENV KRITERIUS_SJF_SOLO_LECTURA=1
RUN python -c "import sjf_local, sys; n = sjf_local.cargar(); print(f'índice FTS del SJF: {n} tesis'); sys.exit(0 if n > 30000 else 1)"

# Render inyecta PORT; 8000 es solo el valor por defecto para correr en local
ENV PORT=8000
EXPOSE 8000

CMD ["python", "server_http.py"]
