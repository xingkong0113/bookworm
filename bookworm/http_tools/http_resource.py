# coding: utf-8

import math
import threading
import requests
from dataclasses import dataclass, field
from bookworm import typehints as t
from bookworm.logger import logger


log = logger.getChild(__name__)


@dataclass(repr=False)
class ResourceDownloadProgress:
    chunk: bytes
    total_size: int
    downloaded: int

    def __repr__(self):
        return f"<ResourceDownloadProgress: downloaded={self.downloaded_mb} MB>"

    @property
    def percentage(self) -> int:
        return math.floor((self.downloaded / self.total_size) * 100)

    @property
    def total_mb(self) -> float:
        return round(self.total_size / (1024 ** 2), 2)

    @property
    def downloaded_mb(self) -> float:
        return round(self.downloaded / (1024 ** 2), 2)


@dataclass(repr=False)
class ResourceDownloadRequest:
    request: requests.Request
    _cancellation_event: threading.Event = field(default_factory=threading.Event)
    DEFAULT_CHUNK_SIZE: t.ClassVar = 1024 ** 2

    def __post_init__(self):
        self.total_size= None
        if "content-length" in self.request.headers:
            self.total_size = int(self.request.headers["content-length"])
        self.chunk_size = (
            self.DEFAULT_CHUNK_SIZE
            if self.total_size is None
            else math.ceil(self.total_size / 100)
        )

    def __iter__(self) -> t.Iterable[ResourceDownloadProgress]:
        yield from self.iter_chunks()

    def iter_chunks(self) -> t.Iterable[ResourceDownloadProgress]:
        downloaded = 0
        try:
            for chunk in self.request.iter_content(chunk_size=self.chunk_size):
                if self._cancellation_event.is_set():
                    return
                downloaded += len(chunk)
                yield ResourceDownloadProgress(
                    chunk=chunk,
                    total_size=self.total_size if self.total_size is not None else 1,
                    downloaded=downloaded
                )
        except requests.RequestException as e:
            log.exception("Failed to download resource from the web", exc_info=True)
            raise ConnectionError("Error downloading resource.")

    def download_to_file(
        self,
        outfile: t.BinaryIO,
        progress_callback: t.Callable[[ResourceDownloadProgress], None]
    ) -> t.BinaryIO:
        if outfile.seekable():
            outfile.seek(0)
        for progress in self.iter_chunks():
            outfile.write(progress.chunk)
            progress_callback(progress)
        return outfile

    def cancel(self):
        self._cancellation_event.set()

    @property
    def can_report_progress(self):
        return self.total_size is not None


@dataclass
class HttpResource:
    url: str

    def download(self) -> ResourceDownloadRequest:
        try:
            log.info(f"Requesting resource: {self.url}")
            requested_resource = requests.get(self.url, stream=True)
            requested_resource.raise_for_status()
        except requests.RequestException as e:
            log.exception(f"Faild to get resource from {self.url}", exc_info=True)
            raise ConnectionError(f"Failed to get resource from {self.url}")
        return ResourceDownloadRequest(requested_resource)