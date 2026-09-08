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
CMD ["sh", "-c", "gunicorn -w 2 -b 0.0.0.0:${PORT:-10000} --timeout 300 app:app"]
