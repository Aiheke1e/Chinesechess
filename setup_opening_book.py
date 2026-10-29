"""Download the licensed CCPD archive and build the local opening index."""
from __future__ import annotations

import argparse
from pathlib import Path, PurePosixPath
import shutil
import urllib.request
import zipfile

from import_ccpd import build_index, DEFAULT_OUTPUT


HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
ARCHIVE = DATA / "ccpd-main.zip"
SOURCE = DATA / "ccpd_source"
URL = ("https://github.com/Yvonne761/Chinese-Chess-Practical-Dataset/"
       "archive/refs/heads/main.zip")
WANTED = (
    "/Dataset/開局/",
    "/Dataset/對局/大師對局/以開局分類/",
)


def download(url=URL, destination=ARCHIVE):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".download")
    print("[棋谱库] 正在下载 CCPD……")
    with urllib.request.urlopen(url, timeout=45) as response, \
            open(temporary, "wb") as stream:
        shutil.copyfileobj(response, stream, length=1024 * 1024)
    temporary.replace(destination)
    return destination


def extract_selected(archive=ARCHIVE, destination=SOURCE):
    archive, destination = Path(archive), Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    extracted = 0
    with zipfile.ZipFile(archive) as book:
        for info in book.infolist():
            normalized = "/" + info.filename.replace("\\", "/")
            wanted = (any(marker in normalized for marker in WANTED) or
                      normalized.endswith("/README.md") or
                      normalized.endswith("/LICENSE"))
            if not wanted or info.is_dir():
                continue
            parts = PurePosixPath(info.filename).parts[1:]
            if not parts or any(part in ("", ".", "..") for part in parts):
                continue
            target = destination.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with book.open(info) as source, open(target, "wb") as output:
                shutil.copyfileobj(source, output)
            extracted += 1
    if not extracted:
        raise RuntimeError("压缩包里没有找到 CCPD 开局棋谱")
    print(f"[棋谱库] 已解压 {extracted:,} 份相关文件。")
    return extracted


def main():
    parser = argparse.ArgumentParser(description="安装 CCPD 本地象棋开局库")
    parser.add_argument("--archive", default=str(ARCHIVE))
    parser.add_argument("--redownload", action="store_true")
    args = parser.parse_args()
    archive = Path(args.archive)
    if args.redownload or not archive.is_file():
        archive = download(destination=archive)
    else:
        print("[棋谱库] 使用已下载的压缩包：" + str(archive))
    extract_selected(archive, SOURCE)
    metadata = build_index(SOURCE, DEFAULT_OUTPUT)
    print("[棋谱库] 索引完成："
          f"{metadata['source_games']:,} 份棋谱，"
          f"{metadata['entries']:,} 个开局前缀。")
    print("[棋谱库] 输出：" + str(DEFAULT_OUTPUT))


if __name__ == "__main__":
    main()
