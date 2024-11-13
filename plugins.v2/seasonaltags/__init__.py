from typing import Any, Dict, List, Tuple
from app.core.config import settings
from app.core.event import eventmanager, Event
from app.plugins import _PluginBase
from app.schemas.types import MediaType
from app.schemas import EventType
from app.log import logger
from app.helper.module import ModuleHelper
from app.helper.meta import MetaHelper
from datetime import datetime
import os
import re

class SeasonalTags(_PluginBase):
    # 插件基础信息
    plugin_name = "季度番剧标签"
    plugin_desc = "自动为动漫添加季度标签（例：2024年10月番）"
    plugin_version = "1.0"
    plugin_author = "your_name"
    plugin_config_prefix = "seasonaltags_"
    plugin_order = 21
    auth_level = 1

    # 私有属性
    _enabled = False
    _cron = None
    _paths = None
    _mediaservers = None
    _notify = False
    _test_mode = False
    
    # 媒体服务器相关
    mediaserver_helper = None
    _EMBY_HOST = None
    _EMBY_APIKEY = None
    _EMBY_USER = None

    def init_plugin(self, config: dict = None):
        self.mediaserver_helper = ModuleHelper()
        if config:
            self._enabled = config.get("enabled")
            self._cron = config.get("cron")
            self._paths = config.get("paths")
            self._mediaservers = config.get("mediaservers")
            self._notify = config.get("notify")
            self._test_mode = config.get("test_mode")

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """配置表单"""
        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 4,
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
                                    'cols': 4,
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'notify',
                                            'label': '发送通知'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 4,
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'test_mode',
                                            'label': '测试模式'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VTextField',
                        'props': {
                            'model': 'cron',
                            'label': '定时执行',
                            'placeholder': '0 0 * * *'
                        }
                    },
                    {
                        'component': 'VTextarea',
                        'props': {
                            'model': 'paths',
                            'label': '目录配置',
                            'placeholder': '每行一个动漫目录'
                        }
                    },
                    {
                        'component': 'VTextField',
                        'props': {
                            'model': 'mediaservers',
                            'label': 'Emby服务器',
                            'placeholder': '配置Emby服务器名称，多个用,分割'
                        }
                    }
                ]
            }
        ], {
            'enabled': False,
            'notify': False,
            'test_mode': False,
            'cron': '0 0 * * *',
            'paths': '',
            'mediaservers': ''
        }

    def __get_air_date(self, tmdb_id: int) -> str:
        """
        获取首播日期
        """
        try:
            meta = MetaInfo(tmdb_id=tmdb_id)
            if meta.first_air_date:
                air_date = datetime.strptime(meta.first_air_date, '%Y-%m-%d')
                return f"{air_date.year}年{air_date.month:02d}月番"
        except Exception as e:
            logger.error(f"获取首播日期失败: {str(e)}")
        return None

    def __add_tag(self, item_id: str, tag: str, emby_server) -> bool:
        """
        添加标签
        """
        try:
            current_tags = emby_server.instance.get_item_tags(item_id)
            if tag not in current_tags:
                if not self._test_mode:
                    emby_server.instance.add_tag(item_id, tag)
                logger.info(f"添加标签: {tag}")
                return True
            else:
                logger.info(f"标签已存在: {tag}")
        except Exception as e:
            logger.error(f"添加标签失败: {str(e)}")
        return False

    @eventmanager.register(EventType.PluginAction)
    def process_seasonal_tags(self, event: Event = None):
        """
        处理季度标签
        """
        if not self._enabled or not self._paths:
            return

        # 获取Emby服务器
        emby_servers = self.mediaserver_helper.get_services(
            name_filters=self._mediaservers, 
            type_filter="emby"
        )
        if not emby_servers:
            logger.error("未配置Emby媒体服务器")
            return

        for path in self._paths.splitlines():
            if not path.strip():
                continue

            logger.info(f"处理目录: {path}")
            
            try:
                # 获取目录下的所有剧集
                meta_info = MetaInfo(path)
                if not meta_info.tmdb_id:
                    logger.error(f"无法获取TMDB ID: {path}")
                    continue

                # 获取首播月份标签
                seasonal_tag = self.__get_air_date(meta_info.tmdb_id)
                if not seasonal_tag:
                    logger.error(f"无法获取首播月份: {path}")
                    continue

                # 为每个Emby服务器添加标签
                for emby_name, emby_server in emby_servers.items():
                    logger.info(f"处理媒体服务器: {emby_name}")
                    
                    # 查找对应的媒体项
                    items = emby_server.instance.get_items(
                        parent_id=None,
                        media_type="Series",
                        name=meta_info.title
                    )

                    if not items:
                        logger.error(f"未找到媒体: {meta_info.title}")
                        continue

                    # 添加标签
                    for item in items:
                        if self.__add_tag(item.id, seasonal_tag, emby_server):
                            if self._notify:
                                self.post_message(
                                    title=f"季度番剧标签{'(测试)' if self._test_mode else ''}",
                                    text=f"{meta_info.title} 添加标签: {seasonal_tag}"
                                )

            except Exception as e:
                logger.error(f"处理失败: {str(e)}")
                if self._notify:
                    self.post_message(
                        title="季度番剧标签 - 错误",
                        text=f"处理 {path} 时出错: {str(e)}"
                    ) 