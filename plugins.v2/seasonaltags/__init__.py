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
from app.utils.http import RequestUtils

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
    _onlyonce = False
    _cron = None
    _libraries = []  # 改为存储媒体库选择
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
            self._onlyonce = config.get("onlyonce")
            self._cron = config.get("cron")
            self._libraries = config.get("libraries") or []  # 初始化媒体库选择
            
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
        """
        # 获取媒体服务器中的媒体库列表
        library_items = []
        emby_servers = self.mediaserver_helper.get_services(type_filter="emby")
        if emby_servers:
            for server_name, server_info in emby_servers.items():
                libraries = server_info.instance.get_librarys()
                if libraries:
                    for library in libraries:
                        library_items.append({
                            "title": f"{server_name}/{library.name}",
                            "value": f"{server_name}|{library.name}"
                        })

        return [
            {
                'component': 'VForm',
                'content': [
                    # 启用开关和立即运行开关
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
                                            'label': '启用插件',
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
                                            'model': 'onlyonce',
                                            'label': '立即运行一次',
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    # 执行周期
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
                    },
                    # 媒体库选择
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
                                            'model': 'libraries',
                                            'label': '媒体库',
                                            'items': library_items
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    # 说明
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                },
                                'content': [
                                    {
                                        'component': 'VAlert',
                                        'props': {
                                            'type': 'info',
                                            'variant': 'tonal',
                                            'text': '选择需要添加季度标签的媒体库，将根据剧集的首播时间自动添加对应季度标签。'
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
            "onlyonce": False,
            "cron": "5 1 * * *",
            "libraries": []  # 改为存储媒体库选择
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
        logger.info(f"开始执行季度标签任务 ...")
        
        # 获取媒体服务器
        media_servers = self.mediaserver_helper.get_services(
            name_filters=self._libraries
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
                # 检查是否是选中的媒体库
                library_key = f"{server_name}|{library.name}"
                if library_key not in self._libraries:
                    logger.debug(f"跳过未选择的媒体库：{library.name}")
                    continue
                    
                logger.info(f"开始处理媒体库：{library.name}")
                
                # 获取媒体库中的项目
                items = server_info.instance.get_items(library.id)
                if not items:
                    logger.warning(f"媒体库 {library.name} 未获取到媒体项目")
                    continue
                    
                # 处理每个项目
                processed_count = 0
                tagged_count = 0
                for item in items:
                    if not item:
                        continue
                        
                    if self._event.is_set():
                        logger.info(f"季度标签任务停止")
                        return
                        
                    processed_count += 1
                    logger.debug(f"正在处理：{item.title}")
                    
                    # 获取当前标签
                    current_tags = self._get_item_tags(
                        server=server_info.instance,
                        item_id=item.item_id
                    )
                    logger.debug(f"{item.title} 当前标签：{current_tags}")
                    
                    # 计算季度标签
                    season_tag = self._calculate_season_tag(item)
                    if not season_tag:
                        logger.debug(f"{item.title} 无法计算季度标签")
                        continue
                        
                    # 添加标签到剧集和季
                    if season_tag not in current_tags:
                        if self._add_tags_to_series_and_season(
                            server=server_info.instance,
                            item=item,
                            season_tag=season_tag
                        ):
                            tagged_count += 1
                            logger.info(f"为 {item.title} 及其季添加标签：{season_tag}")
                        else:
                            logger.error(f"为 {item.title} 添加标签 {season_tag} 失败")
                    else:
                        logger.debug(f"{item.title} 已存在标签：{season_tag}")
                        
                logger.info(f"媒体库 {library.name} 处理完成，共处理 {processed_count} 个项目，添加标签 {tagged_count} 个")
                
            logger.info(f"媒体服务器 {server_name} 处理完成")
            
        logger.info(f"季度标签任务执行完成")

    def _get_item_tags(self, server, item_id: str) -> List[str]:
        """
        获取项目当前标签
        """
        try:
            item_info = server.get_item_info(item_id)
            tags = [tag.get('Name') for tag in item_info.get("TagItems", [])]
            logger.debug(f"获取到标签：{tags}")
            return tags
        except Exception as e:
            logger.error(f"获取标签失败：{str(e)}")
            return []

    def _add_tag(self, server, item_id: str, tag: str) -> bool:
        """
        添加标签
        """
        try:
            tags = {"Tags": [{"Name": tag}]}
            logger.debug(f"添加标签：{tags}")
            ret = server.add_tag(item_id, tags)
            if ret:
                logger.debug(f"标签添加成功")
            else:
                logger.error(f"标签添加失败")
            return ret
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
        if not self._libraries:
            logger.warning("尚未配置媒体服务器，请检查配置")
            return None

        services = self.mediaserver_helper.get_services(name_filters=self._libraries)
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
        退出插件
        """
        try:
            if self._scheduler:
                logger.info(f"正在停止插件服务 ...")
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._event.set()
                    self._scheduler.shutdown()
                    self._event.clear()
                self._scheduler = None
                logger.info(f"插件服务已停止")
        except Exception as e:
            logger.error(f"停止插件服务失败：{str(e)}")

    def _get_season_items(self, server, series_id: str) -> List[dict]:
        """
        获取剧集下所有季的信息
        """
        try:
            # 通过Emby API获取季信息
            url = f"{self._EMBY_HOST}emby/Shows/{series_id}/Seasons?api_key={self._EMBY_APIKEY}"
            with RequestUtils().get_res(url) as res:
                if res and res.status_code == 200:
                    return res.json().get("Items", [])
        except Exception as e:
            logger.error(f"获取季信息失败：{str(e)}")
        return []

    def _add_tags_to_series_and_season(self, server, item, season_tag: str) -> bool:
        """
        同时给剧集和季添加标签
        """
        try:
            # 1. 给剧集添加标签
            series_success = self._add_tag(
                server=server,
                item_id=item.item_id,
                tag=season_tag
            )
            if series_success:
                logger.info(f"剧集 {item.title} 添加标签：{season_tag}")
            
            # 2. 获取该剧所有季
            seasons = self._get_season_items(server, item.item_id)
            if not seasons:
                return series_success
            
            # 3. 遍历季，找到对应的季添加标签
            for season in seasons:
                season_id = season.get("Id")
                season_name = season.get("Name", "")
                
                # 获取季的首播日期
                season_info = self._get_season_info(item.provider_ids.get("Tmdb"), 
                                                  season.get("IndexNumber", 1))
                if not season_info or not season_info.get("air_date"):
                    continue
                    
                # 计算该季的标签
                season_air_date = datetime.strptime(season_info["air_date"], '%Y-%m-%d')
                month = season_air_date.month
                year = season_air_date.year
                
                if 1 <= month <= 3:
                    season_month = 1
                elif 4 <= month <= 6:
                    season_month = 4
                elif 7 <= month <= 9:
                    season_month = 7
                else:
                    season_month = 10
                    
                this_season_tag = f"{year}年{season_month:02d}月番"
                
                # 如果是同一个标签，则添加
                if this_season_tag == season_tag:
                    # 给季添加标签
                    season_success = self._add_tag(
                        server=server,
                        item_id=season_id,
                        tag=season_tag
                    )
                    if season_success:
                        logger.info(f"季 {season_name} 添加标签：{season_tag}")
                    
            return True
                
        except Exception as e:
            logger.error(f"添加剧集和季标签失败：{str(e)}")
            return False