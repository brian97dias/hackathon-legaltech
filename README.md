Crear en torno virtual con 

python -m venv .venv

Instalar dependencias:

pip install -r requirements.txt

Para correr proyecto ejecutar en terminal:

python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000

Debo de ejecutarlo en el directorio:

cd .\proyecto\

Para ver el proyecto ingresar a:

http://127.0.0.1:5500/web/index.html?api=http://127.0.0.1:8000