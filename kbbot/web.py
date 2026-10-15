"""Статический сервер сайта для tailscale serve (служба kb-web).

Стандартный http.server, но без листинга каталогов и с 404.html VitePress.
Корень — симлинк /srv/kb/site: путь пересчитывается на каждый запрос,
поэтому новый релиз виден сразу после переключения ссылки.
"""

import argparse
import os
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

# tailscale serve --set-path /kb отрезает префикс; если вдруг не отрежет —
# запросы /kb/... тоже обслуживаются
PREFIX = "/kb"


class Handler(SimpleHTTPRequestHandler):
    server_version = "kb-web"
    sys_version = ""

    def translate_path(self, path: str) -> str:
        bare = path.split("?", 1)[0].split("#", 1)[0]
        if bare == PREFIX or bare.startswith(PREFIX + "/"):
            path = path[len(PREFIX):] or "/"
        return super().translate_path(path)

    def send_head(self):
        path = self.translate_path(self.path)
        if os.path.isdir(path):
            bare = self.path.split("?", 1)[0]
            if not bare.endswith("/"):
                # Свой редирект: стандартный потерял бы префикс /kb, отрезанный
                # tailscale serve, и увёл бы на code-server
                target = bare if bare == PREFIX or bare.startswith(PREFIX + "/") else PREFIX + bare
                self.send_response(HTTPStatus.MOVED_PERMANENTLY)
                self.send_header("Location", target + "/")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return None
            if not os.path.isfile(os.path.join(path, "index.html")):
                return self._not_found()
        elif not os.path.isfile(path):
            return self._not_found()
        return super().send_head()

    def list_directory(self, path):
        return self._not_found()

    def end_headers(self) -> None:
        # Хэшированные ассеты VitePress можно кэшировать надолго, HTML — нет
        if "/assets/" in self.path:
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        else:
            self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def _not_found(self):
        page = os.path.join(self.directory, "404.html")
        try:
            f = open(page, "rb")
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return None
        fs = os.fstat(f.fileno())
        self.send_response(HTTPStatus.NOT_FOUND)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(fs.st_size))
        self.end_headers()
        return f

    def log_message(self, format: str, *args) -> None:
        # Журнал systemd: без IP (всегда 127.0.0.1) и без даты (её пишет journald)
        print(format % args, flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bind", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--root", default="/srv/kb/site")
    a = ap.parse_args()
    httpd = ThreadingHTTPServer((a.bind, a.port), partial(Handler, directory=a.root))
    httpd.serve_forever()


if __name__ == "__main__":
    main()
