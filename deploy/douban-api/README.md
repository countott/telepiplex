# Douban API on Unraid

This helper builds `wanglin2/douban_api` as a local Docker image for Telepiplex.

The upstream project uses PhantomJS 2.1.1. The bundled Dockerfile sets `OPENSSL_CONF=/tmp/openssl-empty.cnf` because PhantomJS fails on modern Debian/OpenSSL defaults with `libssl_conf.so` errors.

The installer also patches `models/detail.js`. For movie subject links, the patch first tries Douban's lighter JSON/mobile endpoints and only falls back to the upstream PhantomJS detail parser if those do not return a title. This keeps `/movie/detail?url=` useful for Telepiplex even when the original page parser returns an empty `title`.

## Install

From this Telepiplex checkout on Unraid:

```sh
sh deploy/douban-api/install-unraid.sh
```

Defaults:

- App directory: `/mnt/user/appdata/douban-api`
- Image: `douban-api:latest`
- Container: `douban-api`
- Host port: `8085`

Override them if needed:

```sh
DOUBAN_API_PORT=18085 sh deploy/douban-api/install-unraid.sh
```

## Verify

First confirm PhantomJS starts:

```sh
docker exec douban-api sh -lc 'echo $OPENSSL_CONF && phantomjs --version'
```

Expected output:

```text
/tmp/openssl-empty.cnf
2.1.1
```

Then test the API:

```sh
curl -v "http://192.168.7.7:8085/movie/detail?url=https%3A%2F%2Fmovie.douban.com%2Fsubject%2F4864908%2F"
```

Then strictly verify the title field:

```sh
curl -s "http://192.168.7.7:8085/movie/detail?url=https%3A%2F%2Fmovie.douban.com%2Fsubject%2F4864908%2F" \
  | sed -n 's/.*"title":"\([^"]*\)".*/\1/p'
```

Expected output should be a real movie title, for example `影(2018)`. `豆瓣` or an empty value is not sufficient for Telepiplex search.

## Telepiplex Config

Set this in the runtime `/config/config.yaml`:

```yaml
search:
  enable: true
  douban_api:
    enable: true
    base_url: "http://192.168.7.7:8085"
```

Restart Telepiplex after editing the runtime config.
