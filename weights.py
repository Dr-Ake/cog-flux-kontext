import time
from pathlib import Path
import requests
import tarfile
import tempfile


def download_weights(url: str, dest: Path):
    """Download weights from URL to destination path"""
    start = time.time()
    print("downloading url: ", url)
    print("downloading to: ", dest)

    dest = Path(dest)

    def _download_file(source: str, target: Path) -> None:
        """Stream ``source`` to ``target`` using ``requests``."""
        response = requests.get(source, stream=True)
        response.raise_for_status()
        with open(target, "wb") as fh:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    fh.write(chunk)

    # If the URL points to a tar archive we download to a temporary file and
    # extract it into the destination directory.  Otherwise we download the file
    # directly to ``dest``.
    if url.endswith("tar"):
        dest.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_path = Path(tmp.name)
        _download_file(url, tmp_path)
        with tarfile.open(tmp_path) as tar:
            def is_within_directory(directory: str, target: str) -> bool:
                abs_directory = Path(directory).resolve()
                abs_target = Path(target).resolve()
                return abs_directory in abs_target.parents or abs_directory == abs_target

            for member in tar.getmembers():
                member_path = dest / member.name
                if not is_within_directory(dest, member_path):
                    raise Exception("Attempted Path Traversal in Tar File")
            tar.extractall(path=dest)
        tmp_path.unlink()
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        _download_file(url, dest)
    print("downloading took: ", time.time() - start)
