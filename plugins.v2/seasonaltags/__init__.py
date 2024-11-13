"""
SeasonalTags插件
用于自动添加季度标签
"""
from typing import Any, Dict, List, Tuple, Optional
from datetime import datetime
from dataclasses import dataclass
import threading

from app.core.config import settings
from app.core.event import eventmanager, Event, EventType
from app.plugins import _PluginBase
from app.schemas import MediaInfo, MediaServerItem, ServiceInfo
from app.schemas.types import MediaType, SystemConfigKey, ModuleType, EventType
from app.log import logger
from app.helper.mediaserver import MediaServerHelper
from app.chain.tmdb import TmdbChain
from app.chain.mediaserver import MediaServerChain
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

@dataclass
class ServiceInfo:
    """
    封装服务相关信息的数据类
    """
    # 名称
    name: Optional[str] = None
    # 实例
    instance: Optional[Any] = None
    # 模块
    module: Optional[Any] = None
    # 类型
    type: Optional[str] = None
    # 配置
    config: Optional[Any] = None

class SeasonalTags(_PluginBase):
    # 插件基础信息
    plugin_name = "季度番剧标签"
    plugin_desc = "自动为动漫添加季度标签（例：2024年10月番）"
    plugin_version = "1.0"
    plugin_author = "Sebas0619"
    plugin_config_prefix = "seasonaltags_"
    plugin_order = 21
    auth_level = 1

    # 退出事件
    _event = threading.Event()
    
    # 私有属性
    _enabled = False
    _cron = None
    _paths = None
    _mediaservers = None
    _notify = False
    _test_mode = False
    _scheduler = None
    
    # 链式调用
    tmdbchain = None
    mschain = None
    mediaserver_helper = None

    def init_plugin(self, config: dict = None):
        """
        插件初始化
        """
        # 停止现有任务
        self.stop_service()
        
        # 初始化链式调用
        self.tmdbchain = TmdbChain()
        self.mschain = MediaServerChain()
        self.mediaserver_helper = MediaServerHelper()
        
        if config:
            self._enabled = config.get("enabled")
            self._cron = config.get("cron")
            self._paths = config.get("paths")
            self._mediaservers = config.get("mediaservers") or []
            self._notify = config.get("notify")
            self._test_mode = config.get("test_mode")
            
            # 启动定时任务
            if self._enabled and self._cron:
                self._scheduler = BackgroundScheduler(timezone=settings.TZ)
                self._scheduler.add_job(self.process_seasonal_tags,
                                      CronTrigger.from_crontab(self._cron))
                self._scheduler.start()
                logger.info(f"季度番剧标签服务启动，周期：{self._cron}")

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        拼装插件配置页面
        返回: (表单配置, 默认值)
        """
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
                                    'cols': 12,
                                    'md': 6
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
                                    'md': 6
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
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12
                                },
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'multiple': True,
                                            'chips': True,
                                            'clearable': True,
                                            'model': 'mediaservers',
                                            'label': '媒体服务器',
                                            'items': [
                                                {"title": config.name, "value": config.name}
                                                for config in self.mediaserver_helper.get_configs().values()
                                            ]
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'cron',
                                            'label': '执行周期',
                                            'placeholder': '5 4 * * *'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12
                                },
                                'content': [
                                    {
                                        'component': 'VTextarea',
                                        'props': {
                                            'model': 'paths',
                                            'label': '监控目录',
                                            'placeholder': '每行一个目录'
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], {
            'enabled': False,
            'test_mode': False,
            'notify': False,
            'cron': '5 4 * * *',
            'paths': '',
            'mediaservers': []
        }

    def __get_air_date(self, tmdb_id: int) -> str:
        """
        获取首播日期
        """
        try:
            # 使用 TmdbChain 获取剧集详情
            series_detail = self.tmdbchain.tv_detail(tmdb_id)
            if not series_detail:
                return None
            
            if series_detail.first_air_date:
                air_date = datetime.strptime(series_detail.first_air_date, '%Y-%m-%d')
                return f"{air_date.year}年{air_date.month:02d}月番"
        except Exception as e:
            logger.error(f"获取首播日期失败: {str(e)}")
        return None

    def __add_tag(self, server: str, item_id: str, tag: str) -> bool:
        """
        添加标签
        """
        try:
            # 获取当前标签
            current_tags = self.mschain.get_item_tags(server=server, item_id=item_id)
            if not current_tags:
                current_tags = []
                
            if tag not in current_tags:
                if not self._test_mode:
                    # 使用 MediaServerChain 添加标签
                    self.mschain.add_tag(server=server, item_id=item_id, tag=tag)
                logger.info(f"添加标签: {tag}")
                return True
            else:
                logger.info(f"标签已存在: {tag}")
        except Exception as e:
            logger.error(f"添加标签失败: {str(e)}")
        return False

    @eventmanager.register(EventType.PluginAction)
    def process_seasonal_tags(self):
        """
        处理季度标签
        """
        # 获取媒体服务器
        media_servers = self.mediaserver_helper.get_services(
            name_filters=self._mediaservers
        )
        if not media_servers:
            logger.error("未配置媒体服务器")
            return
            
        # 处理每个媒体服务器
        for server_name, server_info in media_servers.items():
            logger.info(f"开始处理媒体服务器：{server_name}")
            
            # 获取媒体库
            librarys = server_info.instance.get_librarys()
            if not librarys:
                logger.error(f"{server_name} 获取媒体库失败")
                continue
                
            # 处理每个媒体库
            for library in librarys:
                logger.info(f"开始处理媒体库：{library.name}")
                
                # 获取媒体库中的项目
                items = server_info.instance.get_items(library.id)
                if not items:
                    continue
                    
                # 处理每个项目
                for item in items:
                    if not item:
                        continue
                        
                    # 获取当前标签
                    current_tags = self._get_item_tags(
                        server=server_info.instance,
                        item_id=item.item_id
                    )
                    
                    # 计算季度标签
                    season_tag = self._calculate_season_tag(item)
                    if not season_tag:
                        continue
                        
                    # 添加标签
                    if season_tag not in current_tags:
                        self._add_tag(
                            server=server_info.instance,
                            item_id=item.item_id,
                            tag=season_tag
                        )
                        logger.info(f"为 {item.title} 添加标签：{season_tag}")

    def _get_item_tags(self, server, item_id: str) -> List[str]:
        """
        获取项目当前标签
        """
        try:
            item_info = server.get_item_info(item_id)
            return [tag.get('Name') for tag in item_info.get("TagItems", [])]
        except Exception as e:
            logger.error(f"获取标签失败：{str(e)}")
            return []

    def _add_tag(self, server, item_id: str, tag: str) -> bool:
        """
        添加标签
        """
        try:
            tags = {"Tags": [{"Name": tag}]}
            return server.add_tag(item_id, tags)
        except Exception as e:
            logger.error(f"添加标签失败：{str(e)}")
            return False

    def _calculate_season_tag(self, item) -> Optional[str]:
        """
        计算季度标签
        """
        # 根据项目添加时间或首播时间计算季度
        # 返回对应的标签名称
        pass

    def service_infos(self) -> Optional[Dict[str, ServiceInfo]]:
        """
        获取媒体服务器信息
        """
        if not self._mediaservers:
            logger.warning("尚未配置媒体服务器，请检查配置")
            return None

        services = self.mediaserver_helper.get_services(name_filters=self._mediaservers)
        if not services:
            logger.warning("获取媒体服务器实例失败，请检查配置")
            return None

        return services

    def get_state(self) -> bool:
        """
        获取插件状态
        """
        return self._enabled
    
    def get_command(self) -> List[Dict[str, Any]]:
        """
        定义远程控制命令
        """
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        """
        定义API接口
        """
        return []

    def get_page(self) -> List[dict]:
        """
        插件页面
        """
        return []

    def stop_service(self):
        """
        停止服务
        """
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._event.set()
                    self._scheduler.shutdown()
                    self._event.clear()
                self._scheduler = None
            logger.info(f"插件 {self.plugin_name} 服务已停止")
        except Exception as e:
            logger.error(f"插件 {self.plugin_name} 停止服务失败: {str(e)}")