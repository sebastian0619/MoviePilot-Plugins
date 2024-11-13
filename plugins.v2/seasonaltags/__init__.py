"""
SeasonalTags插件
用于自动添加季度标签
"""
from typing import Any, Dict, List, Tuple, Optional
from datetime import datetime
from dataclasses import dataclass

from app.core.config import settings
from app.core.event import eventmanager, Event, EventType
from app.plugins import _PluginBase
from app.schemas import MediaInfo, MediaServerItem, ServiceInfo
from app.schemas.types import MediaType, SystemConfigKey, ModuleType, EventType
from app.log import logger
from app.helper.mediaserver import MediaServerHelper
from app.chain.tmdb import TmdbChain
from app.chain.mediaserver import MediaServerChain

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

    # 私有属性
    _enabled = False
    _cron = None
    _paths = None
    _mediaservers = None
    _notify = False
    _test_mode = False
    
    # 链式调用
    tmdbchain = None
    mschain = None
    mediaserver_helper = None

    def init_plugin(self, config: dict = None):
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

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        拼装插件配置页面
        返回: (表单配置, 默认值)
        """
        # 获取媒体服务器列表
        mediaserver_list = []
        for item in self.mediaserver_helper.get_services():
            mediaserver_list.append({
                "title": item.name,
                "value": item.id
            })
        
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
                                            'model': 'mediaservers',
                                            'label': '媒体服务器',
                                            'items': mediaserver_list
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
    def process_seasonal_tags(self, event: Event = None):
        """
        处理季度标签
        """
        if not self._enabled or not self._paths:
            return

        # 获取媒体服务器
        service_infos = self.service_infos()
        if not service_infos:
            return

        for path in self._paths.splitlines():
            if not path.strip():
                continue

            logger.info(f"处理目录: {path}")
            
            try:
                # 获取媒体信息
                mediainfo = self.chain.recognize_media(path=path)
                if not mediainfo:
                    logger.error(f"无法识别媒体信息: {path}")
                    continue

                # 获取首播月份标签
                seasonal_tag = self.__get_air_date(mediainfo.tmdb_id)
                if not seasonal_tag:
                    logger.error(f"无法获取首播月份: {path}")
                    continue

                # 为每个媒体服务器添加标签
                for server_name, server_info in service_infos.items():
                    logger.info(f"处理媒体服务器: {server_name}")
                    
                    # 查找媒体库中的对应媒体
                    existsinfo = self.chain.media_exists(mediainfo=mediainfo)
                    if not existsinfo:
                        logger.error(f"未找到媒体: {mediainfo.title}")
                        continue

                    # 添加标签
                    if self.__add_tag(server=server_name, 
                                    item_id=existsinfo.itemid, 
                                    tag=seasonal_tag):
                        if self._notify:
                            self.post_message(
                                title=f"季度番剧标签{'(测试)' if self._test_mode else ''}",
                                text=f"{mediainfo.title} 添加标签: {seasonal_tag}"
                            )

            except Exception as e:
                logger.error(f"处理失败: {str(e)}")
                if self._notify:
                    self.post_message(
                        title="季度番剧标签 - 错误",
                        text=f"处理 {path} 时出错: {str(e)}"
                    )

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
            # 停止定时任务
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