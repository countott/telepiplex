# -*- coding: utf-8 -*-

import threading

import init
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.handlers.offline_task_handler import try_to_offline2115_again

scheduler = BlockingScheduler()
tasks = []


def clear_request_count():
    init.logger.info(f"昨日累计115 OpenAPI请求次数: [{init.openapi_115.request_count}]")
    cache_hit_rate = (
        init.openapi_115.cache_hit / init.openapi_115.request_count * 100
        if init.openapi_115.request_count > 0
        else 0
    )
    init.logger.info(f"昨日累计115 缓存命中率: [{cache_hit_rate:.2f}%]")
    init.logger.info("正在重置115请求计数...")
    init.openapi_115.clear_request_count()
    init.logger.info("115请求计数已重置！")


def init_tasks():
    global tasks
    tasks = [
        {"id": "retry_failed_downloads", "func": try_to_offline2115_again, "interval": 12 * 60 * 60, "task_type": "interval"},
        {"id": "clear_request_count_task", "func": clear_request_count, "hour": 0, "minute": 0, "task_type": "time"},
    ]


def subscribe_scheduler():
    init_tasks()
    for task in tasks:
        if scheduler.get_job(task["id"]):
            continue
        if task["task_type"] == "interval":
            scheduler.add_job(
                task["func"],
                IntervalTrigger(seconds=task["interval"]),
                id=task["id"],
            )
        if task["task_type"] == "time":
            scheduler.add_job(
                task["func"],
                CronTrigger(hour=task["hour"], minute=task["minute"]),
                id=task["id"],
            )
    if not scheduler.running:
        scheduler.start()


def stop_all_subscriptions():
    for task in tasks:
        job = scheduler.get_job(task["id"])
        if job:
            scheduler.remove_job(task["id"])
            init.logger.info(f"任务 {task['id']} 已停止")
        else:
            init.logger.info(f"任务 {task['id']} 不存在")


def start_scheduler_in_thread():
    thread = threading.Thread(target=subscribe_scheduler)
    thread.daemon = True
    thread.start()
