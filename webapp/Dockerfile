FROM python:3.11-slim
 
# ffmpeg est installe une fois pour toutes dans l'image : personne n'a besoin
# de l'installer sur sa propre machine, il vit sur le serveur d'hebergement.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
 
WORKDIR /app
 
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
 
COPY . .
 
# Render (et la plupart des hebergeurs) fournissent le port d'ecoute via la
# variable d'environnement PORT ; on l'utilise telle quelle plutot qu'un port
# fixe, avec un timeout genereux pour laisser le temps a un gros lot de videos
# de se traiter avant que le serveur ne coupe la requete.
#
# Un seul worker (processus), avec quelques threads plutot que plusieurs
# processus : le plan gratuit n'a que 512 Mo de RAM, et ffmpeg consomme deja
# de la memoire pendant un traitement. Plusieurs processus en parallele
# faisaient saturer la RAM et redemarrer le serveur en boucle.
CMD ["sh", "-c", "gunicorn --workers 1 --threads 4 --worker-class gthread -b 0.0.0.0:${PORT:-10000} --timeout 300 app:app"]
 
