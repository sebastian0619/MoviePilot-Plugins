from datetime import datetime, date
from typing import List, Dict, Any, Tuple
from pathlib import Path

from app.core.config import settings
from app.core.event import EventManager
from app.plugins import _PluginBase
from app.schemas.types import EventType, NotificationType, MediaType
from app.schemas import MessageChannel
from app.utils.string import StringUtils
from app.modules.themoviedb.category import CategoryHelper

class AnimeMonitor(_PluginBase):
    # 插件信息
    plugin_name = "动漫更新提醒"
    plugin_desc = "监控订阅的连载动漫是否有新剧集更新"
    plugin_version = "1.0"
    plugin_author = "Sebastian0619"
    plugin_author_url = "https://github.com/Sebastian0619"
    plugin_config_prefix = "anime_monitor_"
    plugin_order = 20
    auth_level = 1

    def __init__(self):
        super().__init__()
        self._enabled = False
        # 使用CategoryHelper获取分类配置
        self.category_helper = CategoryHelper()

    def init_plugin(self, config: dict = None):
        """
        插件初始化
        """
        if config:
            self._enabled = config.get("enabled", True)
            self._category_name = config.get("category_name", "连载动漫")  # 从配置中读取分类名称
            self._cron = config.get("cron", "0 0 * * *")  # 从配置中读取cron表达式
        else:
            self._enabled = False
            self._category_name = "连载动漫"
            self._cron = "0 0 * * *"
        
        # 确保CategoryHelper正确初始化
        self.category_helper = CategoryHelper()

    def get_state(self) -> bool:
        """
        插件运行状态
        """
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """
        注册命令
        """
        return [{
            "cmd": "/anime_check",
            "event": EventType.PluginAction,
            "desc": "检查今日动漫更新",
            "category": "订阅",
            "data": {
                "action": "check_anime"
            }
        }]

    def get_api(self) -> List[Dict[str, Any]]:
        return []

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'enabled',
                                            'label': '启用插件'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'category_name',
                                            'label': '分类名称',
                                            'placeholder': '输入要监控的分类名称'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'cron',
                                            'label': '执行周期',
                                            'placeholder': '5位cron表达式'
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], {
            "enabled": False,
            "category_name": "连载动漫",
            "cron": "0 0 * * *"
        }

    def get_page(self) -> List[dict]:
        return []

    def get_service(self) -> List[Dict[str, Any]]:
        """
        注册定时服务
        """
        return [{
            "id": "anime_monitor",
            "name": "动漫更新检查",
            "trigger": "cron",
            "func": self.check_anime_update,
            "kwargs": {
                "cron": self._cron
            }
        }]

    def check_anime_update(self):
        """
        检查动漫更新
        """
        if not self.get_state():
            return

        try:
            # 使用配置中的分类名称
            anime_category = self._category_name
            
            # 获取今天的日期
            today = date.today().strftime('%Y-%m-%d')
            
            # 获取所有订阅
            subscribes = self.chain.get_subscribes()
            if not subscribes:
                return

            update_list = []
            
            # 遍历订阅
            for subscribe in subscribes:
                # 只处理电视剧类型且分类为配置中的分类名称的订阅
                if subscribe.type != MediaType.TV or self.category_helper.get_tv_category(subscribe) != anime_category:
                    continue
                
                # 获取TMDB信息
                if not subscribe.tmdbid:
                    continue

                # 使用TheMovieDbModule获取季集信息
                tmdb_info = self.chain.tmdb_seasons(tmdbid=subscribe.tmdbid)
                if not tmdb_info:
                    continue

                # 获取最新季信息
                latest_season = max(tmdb_info, key=lambda x: x.season_number)
                
                # 获取该季的所有剧集
                episodes = self.chain.tmdb_episodes(tmdbid=subscribe.tmdbid, 
                                                    season=latest_season.season_number)
                
                # 检查今日更新
                for episode in episodes:
                    if episode.air_date == today:
                        update_list.append({
                            "name": subscribe.name,
                            "season": latest_season.season_number,
                            "episode": episode.episode_number,
                            "air_date": episode.air_date
                        })

            # 发送通知
            if update_list:
                message = "今日更新动漫:\n"
                for item in update_list:
                    message += (f"{item['name']} "
                              f"S{item['season']:02d}E{item['episode']:02d} "
                              f"({item['air_date']})\n")
                    
                self.post_message(
                    channel=MessageChannel.System,
                    mtype=NotificationType.Subscribe,
                    title="动漫更新提醒",
                    text=message
                )

        except Exception as e:
            self.systemmessage.put(title="动漫更新检查失败", 
                                   message=f"错误信息: {str(e)}")

    def stop_service(self):
        """
        停止插件
        """
        self._enabled = False 