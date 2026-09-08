"""
app.py

Application web (Flask) : meme moteur de traitement que l'appli desktop
(core/media_utils.py, core/audio_video_processor.py, inchanges), mais
exposee comme un site que Baptiste ouvre dans son navigateur, sans rien
installer sur sa machine. ffmpeg est installe dans l'image Docker qui
heberge le site (voir Dockerfile), pas chez l'utilisateur.

Flux :
  GET  /          -> formulaire d'upload (videos + musique + mot de passe)
  POST /process   -> traite le lot, renvoie un fichier zip avec les videos
                     traitees + un journal (log.txt) des succes/echecs.

Une simple protection par mot de passe partage (variable d'environnement
APP_PASSWORD) evite que n'importe qui tombant sur l'URL publique utilise
le service ; ce n'est pas une authentification robuste, juste un frein
raisonnable pour un outil interne a usage occasionnel.
"""

import io
import os
import shutil
import tempfile
import zipfile

from flask import Flask, render_template, request, send_file, abort

from core.audio_video_processor import (
    SUPPORTED_AUDIO_EXTENSIONS,
    SUPPORTED_VIDEO_EXTENSIONS,
    ProcessingError,
    check_output_dir_writable,
    process_video,
)
from core.media_utils import FFmpegNotFoundError, MediaProbeError, find_ffmpeg_binaries, probe_duration

# Limite haute de securite sur la taille totale d'une requete (uploads),
# pour ne pas faire tomber l'instance (RAM limitee sur les hebergements
# gratuits). Ajustable via la variable d'environnement MAX_UPLOAD_MB.
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "300"))

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

APP_PASSWORD = os.environ.get("APP_PASSWORD", "")


def _check_password(submitted: str) -> bool:
    # Pas de mot de passe configure => acces libre (deconseille en public,
    # mais on ne bloque pas un usage local/prive sans configuration).
    if not APP_PASSWORD:
        return True
    return submitted == APP_PASSWORD


@app.errorhandler(413)
def too_large(_exc):
    return (
        f"Fichiers trop volumineux (limite actuelle : {MAX_UPLOAD_MB} Mo au total). "
        "Traite tes videos par lots plus petits.",
        413,
    )


@app.route("/", methods=["GET"])
def index():
    try:
        find_ffmpeg_binaries()
        ffmpeg_ok = True
        ffmpeg_message = "ffmpeg est bien present sur le serveur."
    except FFmpegNotFoundError as exc:
        ffmpeg_ok = False
        ffmpeg_message = str(exc)

    return render_template(
        "index.html",
        ffmpeg_ok=ffmpeg_ok,
        ffmpeg_message=ffmpeg_message,
        password_required=bool(APP_PASSWORD),
        max_upload_mb=MAX_UPLOAD_MB,
        video_extensions=", ".join(sorted(SUPPORTED_VIDEO_EXTENSIONS)),
        audio_extensions=", ".join(sorted(SUPPORTED_AUDIO_EXTENSIONS)),
    )


@app.route("/process", methods=["POST"])
def process():
    if not _check_password(request.form.get("password", "")):
        abort(403, description="Mot de passe incorrect.")

    try:
        ffmpeg_bin = find_ffmpeg_binaries()
    except FFmpegNotFoundError as exc:
        abort(500, description=str(exc))

    music_file = request.files.get("music")
    video_files = [f for f in request.files.getlist("videos") if f and f.filename]

    if not music_file or not music_file.filename:
        abort(400, description="Aucun fichier musical fourni.")
    if not video_files:
        abort(400, description="Aucune video fournie.")

    work_dir = tempfile.mkdtemp(prefix="musicbatch_")
    output_dir = os.path.join(work_dir, "out")

    try:
        check_output_dir_writable(output_dir)
    except ProcessingError as exc:
        shutil.rmtree(work_dir, ignore_errors=True)
        abort(500, description=str(exc))

    music_dir = os.path.join(work_dir, "music")
    os.makedirs(music_dir, exist_ok=True)
    music_path = os.path.join(music_dir, music_file.filename)
    music_file.save(music_path)

    try:
        probe_duration(ffmpeg_bin.ffprobe, music_path)
    except MediaProbeError as exc:
        shutil.rmtree(work_dir, ignore_errors=True)
        abort(400, description=f"Fichier musical illisible : {exc}")

    log_lines = []
    result_paths = []

    for index, video_file in enumerate(video_files):
        original_name = video_file.filename
        # Chaque video est isolee dans son propre sous-dossier : ca evite les
        # collisions de noms entre videos identiques et garde le nom original
        # intact pour que le fichier de sortie s'appelle proprement
        # "<nom_original>_musique.mp4", sans prefixe technique.
        video_input_dir = os.path.join(work_dir, f"in_{index}")
        os.makedirs(video_input_dir, exist_ok=True)
        video_path = os.path.join(video_input_dir, original_name)
        video_file.save(video_path)

        try:
            result = process_video(
                ffmpeg_path=ffmpeg_bin.ffmpeg,
                ffprobe_path=ffmpeg_bin.ffprobe,
                video_path=video_path,
                music_path=music_path,
                output_dir=output_dir,
            )
            log_lines.append(f"OK - {original_name} -> {os.path.basename(result.output_path)}")
            result_paths.append(result.output_path)
        except ProcessingError as exc:
            log_lines.append(f"ECHEC - {original_name} : {exc}")
        finally:
            # Libere l'espace disque de la video source des que possible :
            # utile sur un hebergement a disque limite pour de gros lots.
            if os.path.exists(video_path):
                os.remove(video_path)

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in result_paths:
            zf.write(path, arcname=os.path.basename(path))
        zf.writestr("journal.txt", "\n".join(log_lines) + "\n")
    zip_buffer.seek(0)

    shutil.rmtree(work_dir, ignore_errors=True)

    return send_file(
        zip_buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name="videos_avec_musique.zip",
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")), debug=False)
