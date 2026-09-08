"""
core/media_utils.py

Utilitaires bas niveau autour de ffmpeg/ffprobe :
- detection des binaires sur la machine
- lecture de la duree d'un media via ffprobe

Aucune dependance Python externe pour cette partie : on pilote ffmpeg/ffprobe
en ligne de commande, ce qui reste la solution la plus simple et la plus
fiable pour ce cas d'usage (pas de couche d'abstraction supplementaire type
moviepy a maintenir, et un controle precis des options d'encodage).
"""

import shutil
import subprocess
from dataclasses import dataclass


class FFmpegNotFoundError(RuntimeError):
    """Levee quand ffmpeg et/ou ffprobe ne sont pas trouves sur la machine."""


class MediaProbeError(RuntimeError):
    """Levee quand un fichier media est illisible/corrompu ou de duree indeterminee."""


@dataclass
class FFmpegBinaries:
    ffmpeg: str
    ffprobe: str


def find_ffmpeg_binaries() -> FFmpegBinaries:
    """
    Cherche ffmpeg et ffprobe dans le PATH du systeme.
    Leve FFmpegNotFoundError si l'un des deux manque, avec un message actionnable.
    """
    ffmpeg_path = shutil.which("ffmpeg")
    ffprobe_path = shutil.which("ffprobe")

    missing = []
    if not ffmpeg_path:
        missing.append("ffmpeg")
    if not ffprobe_path:
        missing.append("ffprobe")

    if missing:
        raise FFmpegNotFoundError(
            "Binaire(s) introuvable(s) dans le PATH : "
            + ", ".join(missing)
            + ". Installez ffmpeg (voir instructions d'installation) puis relancez l'application."
        )

    return FFmpegBinaries(ffmpeg=ffmpeg_path, ffprobe=ffprobe_path)


def probe_duration(ffprobe_path: str, media_path: str) -> float:
    """
    Retourne la duree du media (en secondes, float) via ffprobe.
    Leve MediaProbeError si le fichier est illisible, corrompu, ou sans duree
    determinable (conteneur invalide, flux absent, etc.).
    """
    cmd = [
        ffprobe_path,
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        media_path,
    ]
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise MediaProbeError(f"Timeout lors de l'analyse de {media_path!r}") from exc
    except OSError as exc:
        raise MediaProbeError(f"Impossible d'executer ffprobe sur {media_path!r} : {exc}") from exc

    output = result.stdout.strip()
    if result.returncode != 0 or not output:
        stderr_tail = result.stderr.strip().splitlines()[-5:]
        detail = " | ".join(stderr_tail) if stderr_tail else "aucun detail"
        raise MediaProbeError(
            f"Fichier illisible ou corrompu : {media_path!r}. Detail ffprobe : {detail}"
        )

    try:
        duration = float(output)
    except ValueError as exc:
        raise MediaProbeError(
            f"Duree non determinable pour {media_path!r} (valeur ffprobe : {output!r})"
        ) from exc

    if duration <= 0:
        raise MediaProbeError(f"Duree nulle ou negative detectee pour {media_path!r}")

    return duration
