# -*- coding: utf-8 -*-

import init


def create_offline_url(res_list):
    offline_tasks = ""
    offline_tasks_list = []
    for item in res_list:
        magnet = item.get("magnet") if isinstance(item, dict) else None
        title = item.get("title", "Unknown") if isinstance(item, dict) else "Unknown"
        if not magnet:
            init.logger.warn(f"跳过无效的离线任务，标题: {title}，下载链接为空")
            continue
        offline_tasks += magnet + "\n"
        if offline_tasks.count("\n") >= 100:
            offline_tasks_list.append(offline_tasks[:-1])
            offline_tasks = ""
    if offline_tasks:
        offline_tasks_list.append(offline_tasks[:-1])
    return offline_tasks_list
