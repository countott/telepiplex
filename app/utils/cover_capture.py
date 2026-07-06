# -*- coding: utf-8 -*-
import os
import sys
current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
parent_dir = os.path.dirname(current_dir)
sys.path.append(parent_dir)
sys.path.append(current_dir)
import requests
from bs4 import BeautifulSoup
import init
import time


def get_movie_cover(query, page=1):
    """
    封面抓取
    :param query:
    :return:
    """
    base_url = "https://www.themoviedb.org"
    url = f"https://www.themoviedb.org/search/movie?query={query}&page={page}"
    headers = {
        "user-agent": init.USER_AGENT,
        "accept-language": "zh-CN"
    }
    response = requests.get(headers=headers, url=url)
    if response.status_code != 200:
        return ""
    soup = BeautifulSoup(response.text, features="html.parser")
    tags_p = soup.find_all('p')
    for tag in tags_p:
        if "找不到和您的查询相符的电影" in tag.text:
            init.logger.info(f"TMDB未找到匹配电影: {query}")
            return ""
    tags_img = soup.find_all('img')
    image_tag = is_movie_exist(query, tags_img)
    if image_tag is None:
        page += 1
        time.sleep(3)
        return get_movie_cover(query, page)
    tag_parent = image_tag.find_parent('a')
    if 'href' not in tag_parent.attrs:
        return ""
    main_page = tag_parent['href']
    url = base_url + main_page
    response = requests.get(headers=headers, url=url)
    if response.status_code != 200:
        return ""
    soup = BeautifulSoup(response.text, features="html.parser")
    tags_img = soup.find_all('img')
    if len(tags_img) > 1 and 'src' not in tags_img[1].attrs:
        return ""
    cover_url = tags_img[1]['src']
    return cover_url


def is_movie_exist(movie_name, name_list):
    """
    判断搜索结果是否存在
    :param url:
    :param name_list:
    :return:
    """
    img_tag = None
    for name in name_list:
        if 'alt' in name.attrs:
            if name['alt'] == movie_name:
                img_tag = name
                break
    return img_tag

if __name__ == '__main__':
    # init.create_logger()
    # tmdb_id = get_tmdb_id("死人", 20)
    # print(f"TMDB ID: {tmdb_id}")
    cover_url = get_movie_cover("死人", 20)
    print(f"封面URL: {cover_url}")
