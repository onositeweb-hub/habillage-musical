"""
core/audio_video_processor.py

Coeur du traitement audio/video : construit et execute la commande ffmpeg qui
remplace entierement la piste audio d'une video par la musique fournie,
calee sur la duree exacte de la video (bouclage si la musique est plus
courte, coupe si elle est plus longue), sans jamais modifier la duree ni
l'image de la video source.

Principe technique retenu (important, a respecter en cas d'evolution) :
  - la musique est lue en entree avec "-stream_loop -1" : ffmpeg la boucle
    indefiniment. Elle couvre donc toujours au moins la duree de la video,
    qu'elle soit plus courte ou plus longue que celle-ci a l'origine ;
  - la video n'est jamais tronquee ni etiree en duree : c'est elle qui
    determine la fin de l'export via "-shortest" (l'export s'arrete des que
    le flux video - plus court que la musique bouclee a l'infini - se
    termine) ;
  - la piste audio d'origine de la video n'est jamais mappee en sortie : elle
    est donc totalement absente du resultat (aucun mixage possible) ;
  - la video est re-encodee en H.264/yuv420p plutot que copiee telle quelle
    ("-c:v copy"), car certains conteneurs d'entree (AVI, MKV, MOV) embarquent
    des codecs video non valides dans un conteneur MP4. Le re-encodage
    garantit un MP4 de sortie lisible partout, sans changer la duree ni le
    nombre d'images ni la cadence, au prix d'un export un peu plus lent que
    du simple "stream copy" : c'est le compromis fiabilite > sophistication
    retenu pour cet outil.
"""

import os
import re
import subprocess
import threading
from dataclasses import dataclass
from typing import Callable, List, Optional

from core.media_utils import MediaProbeError, probe_duration

# Extensions couramment supportees en entree (indicatif : la validation reelle
# est faite par ffprobe/ffmpeg eux-memes ; cette liste ne sert qu'a filtrer
# l'affichage et le parcours de dossier dans l'interface).
SUPPORTED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".webm"}
SUPPORTED_AUDIO_EXTENSIONS = {".mp3", ".wav", ".aac", ".m4a", ".flac", ".ogg"}


class ProcessingError(RuntimeError):
    """Levee quand le traitement d'une video (probe ou export ffmpeg) echoue."""


@dataclass
class VideoResult:
    source_path: str
    output_path: str
    duration_seconds: float


def build_output_path(video_path: str, output_dir: str) -> str:
    """
    Construit un nom de fichier de sortie clair et unique dans output_dir,
    derive du nom d'origine : "<nom>_musique.mp4". En cas de collision
    (fichier deja present), un suffixe numerique est ajoute.
    """
    stem = os.path.splitext(os.path.basename(video_path))[0]
    candidate = os.path.join(output_dir, f"{stem}_musique.mp4")

    counter = 1
    while os.path.exists(candidate):
        candidate = os.path.join(output_dir, f"{stem}_musique_{counter}.mp4")
        counter += 1

    return candidate


def check_output_dir_writable(output_dir: str) -> None:
    """
    Verifie que le dossier de sortie existe (le cree si besoin) et est
    accessible en ecriture. Leve ProcessingError sinon.

    Un essai d'ecriture reel (creation puis suppression d'un fichier temoin)
    est utilise plutot qu'un simple os.access(), ce dernier etant peu fiable
    sur certaines configurations (droits reseau/ACL Windows, execution en
    administrateur, etc.).
    """
    try:
        os.makedirs(output_dir, exist_ok=True)
    except OSError as exc:
        raise ProcessingError(
            f"Impossible de creer le dossier de sortie {output_dir!r} : {exc}"
        ) from exc

    probe_file = os.path.join(output_dir, ".ecriture_test.tmp")
    try:
        with open(probe_file, "wb") as handle:
            handle.write(b"0")
    except OSError as exc:
        raise ProcessingError(
            f"Le dossier de sortie {output_dir!r} n'est pas accessible en ecriture : {exc}"
        ) from exc
    finally:
        try:
            os.remove(probe_file)
        except OSError:
            pass


def _drain_stream(stream, sink: List[str]) -> None:
    """Lit un flux ligne par ligne jusqu'a EOF et accumule dans `sink`."""
    try:
        for line in iter(stream.readline, ""):
            sink.append(line)
    finally:
        stream.close()


def _parse_progress_line(line: str, total_duration: float) -> Optional[int]:
    """
    Parse une ligne de la sortie "-progress pipe:1" de ffmpeg et retourne un
    pourcentage (0-100) si la ligne contient "out_time_ms=...", sinon None.
    """
    match = re.match(r"out_time_ms=(\d+)", line.strip())
    if not match or total_duration <= 0:
        return None
    out_seconds = int(match.group(1)) / 1_000_000
    percent = int(min(100, max(0, (out_seconds / total_duration) * 100)))
    return percent


def process_video(
    ffmpeg_path: str,
    ffprobe_path: str,
    video_path: str,
    music_path: str,
    output_dir: str,
    progress_callback: Optional[Callable[[int], None]] = None,
) -> VideoResult:
    """
    Traite une seule video : probe de la duree, export ffmpeg avec la musique
    calee sur la duree exacte de la video, piste audio d'origine remplacee.

    Leve ProcessingError en cas d'echec (fichier illisible, ffmpeg en echec,
    etc.) ; l'appelant (le worker de lot) est responsable de continuer le
    traitement des autres fichiers.
    """
    if not os.path.isfile(video_path):
        raise ProcessingError(f"Fichier video introuvable : {video_path!r}")

    try:
        duration = probe_duration(ffprobe_path, video_path)
    except MediaProbeError as exc:
        raise ProcessingError(str(exc)) from exc

    output_path = build_output_path(video_path, output_dir)

    cmd = [
        ffmpeg_path, "-y",
        "-stream_loop", "-1", "-i", music_path,
        "-i", video_path,
        "-map", "1:v:0",
        "-map", "0:a:0",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-threads", "1",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        "-movflags", "+faststart",
        "-progress", "pipe:1",
        "-nostats",
        "-loglevel", "error",
        output_path,
    ]

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
    except OSError as exc:
        raise ProcessingError(f"Impossible de lancer ffmpeg : {exc}") from exc

    # Le flux stderr est draine dans un thread dedie pendant qu'on lit le
    # flux stdout (progression) dans la boucle principale : cela evite tout
    # blocage si ffmpeg ecrit sur les deux flux en meme temps.
    stderr_lines: List[str] = []
    stderr_thread = threading.Thread(
        target=_drain_stream, args=(process.stderr, stderr_lines), daemon=True
    )
    stderr_thread.start()

    assert process.stdout is not None
    for line in iter(process.stdout.readline, ""):
        percent = _parse_progress_line(line, duration)
        if percent is not None and progress_callback is not None:
            progress_callback(percent)
    process.stdout.close()

    returncode = process.wait()
    stderr_thread.join(timeout=5)

    if returncode != 0:
        # Nettoyage d'un eventuel export partiel avant de signaler l'echec.
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except OSError:
                pass
        tail = "".join(stderr_lines[-15:]).strip() or "aucun detail disponible"
        raise ProcessingError(
            f"Echec de l'export ffmpeg pour {video_path!r} (code {returncode}) : {tail}"
        )

    if progress_callback is not None:
        progress_callback(100)

    return VideoResult(source_path=video_path, output_path=output_path, duration_seconds=duration)
