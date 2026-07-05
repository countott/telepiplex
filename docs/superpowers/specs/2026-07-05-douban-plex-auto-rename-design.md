# Douban Plex Auto Rename Design

## Goal

When a search is started from a Douban subject URL or a plain `/s title` query, automatically organize the completed 115 offline result into a Plex-friendly layout.

## Scope

This implementation changes the `/s Douban URL -> Prowlarr candidate -> 115 offline result` path and the plain `/s title -> Prowlarr candidate -> 115 offline result` path. Direct magnet/URL downloads keep the current manual rename flow because they do not carry search metadata.

## Naming Contract

Douban metadata supplies the Chinese title and English/original title when the search starts from a Douban URL. For plain `/s title`, the system first searches Douban and uses Douban metadata when the returned subject exactly matches the query. If Douban lookup fails or the result is not an exact metadata match, the user query becomes the Chinese folder hint and the selected Prowlarr release title is cleaned to infer the English folder name. Prowlarr remains the release search and download source.

Movies are organized as:

```text
/保存文件夹/中文名/英文名/英文名.后缀名
```

Episodes are organized as:

```text
/保存文件夹/中文名/英文名/英文名 SxxExx.后缀名
```

Season and episode numbers are parsed from the selected Prowlarr release title. If the selected title does not provide an episode marker, the system treats the result as a movie.

## Flow

1. Parse Douban subject metadata into Chinese title, English title, year, and search query; for plain search text, first try an exact Douban metadata match, otherwise keep the query as the Chinese folder hint.
2. Store this metadata with the pending search task.
3. When the user chooses a candidate and save path, pass the metadata and selected release title into `download_task`.
4. After 115 reports the offline result as complete, normalize the result into a single working directory as today.
5. Create `/保存文件夹/中文名/英文名` if needed, rename the main video file to the Plex filename, and move it there.
6. Generate STRM files and notify Emby for the final English-title folder.
7. If Douban lookup fails, metadata is missing, the English title cannot be inferred, or any 115 operation fails, fall back to the existing manual rename prompt or the plain-query metadata flow.

## Testing

Unit tests cover Douban metadata parsing, release title season/episode parsing, Plex naming plan generation, search metadata propagation, and the download task's successful auto-rename behavior with a mocked 115 client.
