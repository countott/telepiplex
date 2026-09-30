import json
from dataclasses import replace

import pytest

from telepiplex_caption.catalog_providers import HaruhanaProvider, KitaujiProvider, LocalArchiveProvider, MingYProvider, NekomoeProvider, mapped_episode
from telepiplex_caption.matching import match_document
from telepiplex_caption.models import MediaQuery, SubtitleCandidate, SubtitleDocument
from telepiplex_caption.providers import HttpResponse, ProviderError


class Transport:
    max_bytes = 20 * 1024 * 1024

    def __init__(self, *bodies):
        self.bodies, self.calls = list(bodies), []

    def request(self, url, **kwargs):
        self.calls.append((url, kwargs))
        body = self.bodies.pop(0)
        if isinstance(body, Exception):
            raise body
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        return HttpResponse(body)


def tree(*paths, truncated=False):
    return {'truncated': truncated, 'tree': [{'path': p, 'type': 'blob'} for p in paths]}


def test_mingy_finished_release_aliases_cache_and_no_key():
    url = 'https://github.com/MingYSub/SubsArchive/releases/download/202507/Food.Court.7z'
    release = {'tag_name': '202507', 'body': f'| 明天，美食广场见。 <br> フードコートで、また明日。 <br> Food Court de, Mata Ashita. | 6 | BD | [Food.Court.7z]({url}) |', 'assets': [{'name': 'Food.Court.7z', 'browser_download_url': url}]}
    provider = MingYProvider(transport=Transport([release], b'7zbody'))
    query = MediaQuery('明天，美食广场见。', media_type='series', original_language='ja')
    candidate, = provider.search(query).candidates
    assert provider.search(query).candidates == (candidate,)
    assert len(provider.transport.calls) == 1
    assert 'フードコートで、また明日。' in candidate.metadata['document_aliases']
    assert 'episode_mapping' not in candidate.metadata
    assert provider.download(candidate) == (b'7zbody', 'Food.Court.7z')
    assert all('Authorization' not in x[1].get('headers', {}) for x in provider.transport.calls)


def test_token_only_metadata_api_not_download():
    url = 'https://github.com/MingYSub/SubsArchive/releases/download/Movie/Example.7z'
    provider = MingYProvider({'github_token': 'secret'}, transport=Transport([{'tag_name': 'Movie', 'body': f'| Example | [Example]({url}) |', 'assets': [{'name': 'Example.7z', 'browser_download_url': url}]}], b'body'))
    candidate, = provider.search(MediaQuery('Example')).candidates
    provider.download(candidate)
    assert provider.transport.calls[0][1]['headers']['Authorization'] == 'Bearer secret'
    assert 'headers' not in provider.transport.calls[1][1]


def test_mingy_excludes_font_asset():
    url = 'https://github.com/MingYSub/SubsArchive/releases/download/202507/Example.Fonts.7z'
    provider = MingYProvider(transport=Transport([{'tag_name': '202507', 'body': f'| Example | [Example]({url}) |', 'assets': [{'name': 'Example.Fonts.7z', 'browser_download_url': url}]}]))
    assert not provider.search(MediaQuery('Example', media_type='series')).candidates


def test_nekomoe_readme_explicit_season_finished_package_only():
    url = 'https://github.com/Nekomoekissaten-SUB/Nekomoekissaten-Storage/releases/download/subtitle_pkg/Example_BD_zho.7z'
    provider = NekomoeProvider(transport=Transport(tree('Example/README.md', 'Example/OP.ass'), f'# Example\n## Season 2\n[01-12 TV 简繁中文 BD 字幕]({url})\n[日语](https://github.com/Nekomoekissaten-SUB/Nekomoekissaten-Storage/releases/download/subtitle_jpn/Example.7z)'))
    candidate, = provider.search(MediaQuery('Example', media_type='series', season=2)).candidates
    assert candidate.metadata['episode_mapping'] == {'season': 2, 'offset': 0, 'first': 1, 'last': 12}
    assert mapped_episode(candidate, '[Group] Example [03].SC.ass') == (2, 3)
    assert mapped_episode(candidate, '[Group] Example [13].SC.ass') is None


def test_nekomoe_small_movie_body_alias_only_matches_same_work():
    provider = NekomoeProvider({'title_aliases': {'shortkey': ['示例', 'Example Film']}}, transport=Transport(tree('shortkey/README.md', 'shortkey/[Group] Example Film [BDRip].SC.ass'), '# Example Film'))
    candidate, = provider.search(MediaQuery('示例')).candidates
    document = SubtitleDocument('[Group] Example Film [BDRip].SC.ass', 'ass', b'')
    assert match_document(MediaQuery('示例'), candidate, document).accepted
    assert not match_document(MediaQuery('示例'), candidate, replace(document, filename='[Group] Other Film [BDRip].SC.ass')).accepted


def test_nekomoe_large_source_tree_not_finished_download():
    provider = NekomoeProvider(transport=Transport(tree('Example/README.md', *(f'Example/Example EP{i}.ass' for i in range(1, 8))), '# Example'))
    assert not provider.search(MediaQuery('Example', media_type='series')).candidates


def test_public_project_rows_resolve_alias_without_font_download():
    data = {'groupedItems': [{'nodes': [{'memexProjectColumnValues': [{'value': {'title': {'raw': '示例电影'}}}, {'value': {'raw': 'Example Movie'}}, {'value': {'raw': 'https://github.com/HaruhanaSub/Haruhana-Storage/tree/main/shortkey'}}]}]}]}
    page = '<script type="application/json" id="memex-paginated-items-data">' + json.dumps(data) + '</script>'
    provider = HaruhanaProvider(transport=Transport(tree('shortkey/README.md', 'shortkey/[Group] Example Movie [Subtitles].7z', 'shortkey/[Fonts].7z'), page, '# Example Movie'))
    candidate, = provider.search(MediaQuery('示例电影')).candidates
    assert 'Example%20Movie' in candidate.download_url
    assert 'Example Movie' in candidate.metadata['document_aliases']
    assert 'filterQuery=' in provider.transport.calls[1][0]


def test_truncated_tree_explicit_status():
    assert HaruhanaProvider(transport=Transport(tree('Example/Example.ass', truncated=True))).search(MediaQuery('Example')).status == 'catalog_incomplete'


def test_kitauji_only_finished_mono_and_explicit_mapping():
    provider = KitaujiProvider({'episode_mappings': {'subs-shikanoko': {'season': 1, 'first': 1, 'last': 12}}}, transport=Transport({'assets': [{'name': 'fonts.zip', 'browser_download_url': 'https://github.com/fonts.zip'}, {'name': 'sino_subs_v1.0.0.zip', 'browser_download_url': 'https://github.com/bilingual.zip'}, {'name': 'sino_subs_v1.0.0_mono.zip', 'browser_download_url': 'https://github.com/mono.zip'}]}))
    candidate, = provider.search(MediaQuery('鹿乃子乃子乃子虎视眈眈', media_type='series')).candidates
    assert candidate.download_url.endswith('/mono.zip')
    assert mapped_episode(candidate, 'ep2_tc.ass') == (1, 2)
    assert match_document(MediaQuery(candidate.title, media_type='series', season=1, episode=2), candidate, SubtitleDocument('ep2_tc.ass', 'ass', b'')).accepted


@pytest.mark.parametrize('filename', ['Example [02.5].ass', 'Example [SP01].ass', 'Example OP1.ass', 'Example.S03E01.ass', 'Example.S02E01-E03.ass', 'Example [14].ass'])
def test_mapping_rejects_specials_conflicts_ranges_out_of_scope(filename):
    candidate = SubtitleCandidate('fixture', '1', 'Example', metadata={'episode_mapping': {'season': 2, 'offset': -12, 'first': 13, 'last': 13}})
    assert mapped_episode(candidate, filename) is None
    assert 'episode_mapping_unresolved' in match_document(MediaQuery('Example', media_type='series', season=2, episode=1), candidate, SubtitleDocument(filename, 'ass', b'')).reasons


def test_absolute_to_aired_mapping_and_no_double_offset():
    candidate = SubtitleCandidate('fixture', '1', 'Example', metadata={'episode_mapping': {'season': 2, 'offset': -12}})
    assert mapped_episode(candidate, 'Example EP13.SC.ass') == (2, 1)
    assert mapped_episode(candidate, '[Group] Example - 13.Encore.SC.ass') == (2, 1)
    assert mapped_episode(candidate, 'Example.S02E01.ass') == (2, 1)
    assert mapped_episode(replace(candidate, metadata={}), 'Example [13].ass') is None
    assert match_document(MediaQuery('Example', media_type='series', season=2, episode=1), candidate, SubtitleDocument('Example EP13.SC.ass', 'ass', b'')).accepted


def test_local_reads_only_configured_root(tmp_path):
    root = tmp_path / 'archive'
    root.mkdir()
    path = root / 'Example.2020.chi.srt'
    path.write_bytes(b'local subtitle body')
    (tmp_path / 'Other.srt').write_text('outside')
    provider = LocalArchiveProvider({'roots': [str(root)]})
    candidate, = provider.search(MediaQuery('Example', year=2020)).candidates
    assert provider.download(candidate) == (path.read_bytes(), path.name)
    assert LocalArchiveProvider().search(MediaQuery('Example')).status == 'not_configured'


def test_local_tsv_maps_opaque_filename_and_episode(tmp_path):
    root = tmp_path / 'archive'
    root.mkdir()
    (root / '100.zip').write_bytes(b'archive')
    mapping = tmp_path / 'index.tsv'
    mapping.write_text('relative_path\ttitle\tyear\tmedia_type\tseason\tepisode\taliases\n100.zip\tExample\t2020\tseries\t2\t3\t示例\n')
    provider = LocalArchiveProvider({'roots': [str(root)], 'mapping_file': str(mapping)})
    candidate, = provider.search(MediaQuery('示例', media_type='series', year=2020, season=2, episode=3)).candidates
    assert (candidate.season, candidate.episode) == (2, 3)
    assert not provider.search(MediaQuery('示例', media_type='series', season=2, episode=4)).candidates


def test_local_symlinks_ignored_and_swap_rechecked(tmp_path):
    root, outside = tmp_path / 'archive', tmp_path / 'outside'
    root.mkdir()
    outside.mkdir()
    (outside / 'Example.2020.srt').write_bytes(b'private')
    (root / 'link').symlink_to(outside, target_is_directory=True)
    path = root / 'Example.2020.srt'
    path.symlink_to(outside / path.name)
    assert not LocalArchiveProvider({'roots': [str(root)]}).search(MediaQuery('Example', year=2020)).candidates
    path.unlink()
    path.write_bytes(b'allowed')
    provider = LocalArchiveProvider({'roots': [str(root)]})
    candidate, = provider.search(MediaQuery('Example', year=2020)).candidates
    path.unlink()
    path.symlink_to(outside / path.name)
    with pytest.raises(ProviderError) as exc:
        provider.download(candidate)
    assert exc.value.status == 'unsafe_path'


def test_local_scan_bound_and_path_forgery(tmp_path):
    (tmp_path / 'Example.2020.srt').write_text('one')
    (tmp_path / 'Other.2020.srt').write_text('two')
    assert LocalArchiveProvider({'roots': [str(tmp_path)], 'max_files': 1}).search(MediaQuery('Example')).status == 'catalog_incomplete'
    provider = LocalArchiveProvider({'roots': [str(tmp_path)]})
    candidate, = provider.search(MediaQuery('Example', year=2020)).candidates
    with pytest.raises(ProviderError):
        provider.download(replace(candidate, metadata={**candidate.metadata, 'local_relative_path': '../Other.srt'}))


def test_local_season_folder_retains_coordinates(tmp_path):
    folder = tmp_path / 'Example (2020)' / 'Season 2'
    folder.mkdir(parents=True)
    (folder / 'Example.S02E03.chi.srt').write_text('one')
    candidate, = LocalArchiveProvider({'roots': [str(tmp_path)]}).search(MediaQuery('Example', media_type='series', season=2, episode=3, year=2020)).candidates
    assert (candidate.season, candidate.episode) == (2, 3)


def test_local_absolute_candidate_is_mapped_before_candidate_filter(tmp_path):
    name = 'Example EP13.SC.ass'
    (tmp_path / name).write_text('subtitle')
    provider = LocalArchiveProvider({'roots': [str(tmp_path)], 'episode_mappings': {name: {'season': 2, 'offset': -12}}})
    candidate, = provider.search(MediaQuery('Example', media_type='series', season=2, episode=1)).candidates
    assert (candidate.season, candidate.episode) == (2, 1)


def test_local_mapping_never_leaks_partial_episode_when_unresolved(tmp_path):
    name = 'Example EP13.SC.ass'
    (tmp_path / name).write_text('subtitle')
    provider = LocalArchiveProvider({'roots': [str(tmp_path)], 'episode_mappings': {name: {'season': 2, 'offset': -12, 'last': 12}}})
    candidate, = provider.search(MediaQuery('Example', media_type='series', season=2, episode=13)).candidates
    assert 'episode_mapping_unresolved' in match_document(MediaQuery('Example', media_type='series', season=2, episode=13), candidate, SubtitleDocument(name, 'ass', b'')).reasons


def test_local_depth_limit_reports_incomplete_instead_of_empty_catalog(tmp_path):
    folder = tmp_path / 'one' / 'two'
    folder.mkdir(parents=True)
    (folder / 'Example.2020.srt').write_text('subtitle')
    provider = LocalArchiveProvider({'roots': [str(tmp_path)], 'max_depth': 1})
    assert provider.search(MediaQuery('Example', year=2020)).status == 'catalog_incomplete'


def test_single_episode_query_does_not_supply_unproven_catalog_season(tmp_path):
    name = 'Example EP03.SC.ass'
    (tmp_path / name).write_text('subtitle')
    query = MediaQuery('Example', media_type='series', season=2, episode=3)
    candidate, = LocalArchiveProvider({'roots': [str(tmp_path)]}).search(query).candidates
    assert 'episode_mapping_unresolved' in match_document(query, candidate, SubtitleDocument(name, 'ass', b'')).reasons
