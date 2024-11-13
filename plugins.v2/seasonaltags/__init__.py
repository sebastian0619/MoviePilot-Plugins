"""
SeasonalTags插件
用于自动添加季度标签
"""
from typing import Any, Dict, List, Tuple, Optional
from datetime import datetime, timedelta
import pytz
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

class SeasonalTags(_PluginBase):
    # 插件基础信息
    plugin_name = "Emby季度番剧标签"
    plugin_desc = "自动为Emby的动漫库添加季度标签（例：2024年10月番）"

    plugin_version = "1.1"
    plugin_author = "Sebas0619"
    plugin_config_prefix = "seasonaltags_"
    plugin_icon = "emby.png"
    plugin_order = 21
    author_url = "https://github.com/sebastian0619"
    auth_level = 1

    # 退出事件
    _event = threading.Event()
    
    # 私有属性
    _enabled = False
    _onlyonce = False
    _cron = None
    _libraries = []  # 改为存储媒体库选择
    _scheduler = None
    _clean_enabled = False  # 添加清理开关状态
    _notify_enabled = False  # 添加通知开关状态
    
    # 链式调用
    tmdbchain = None
    mschain = None
    mediaserver_helper = None

    def __init__(self):
        super().__init__()
        self._EMBY_HOST = None
        self._EMBY_APIKEY = None
        self._EMBY_USER = None
        self._mediaserver = None
        self._clean_enabled = False  # 添加清理开关状态
        self._notify_enabled = False  # 添加通知开关初始状态
        # 初始化历史记录
        self.history_data = self.get_data('history') or {}

    def init_plugin(self, config: dict = None):
        """
        插件初始化
        """
        # 停止现有任务
        self.stop_service()
        
        # 初始化组件
        self.mediaserver_helper = MediaServerHelper()
        self.tmdbchain = TmdbChain()
        
        if config:
            self._enabled = config.get("enabled")
            self._clean_enabled = config.get("clean_enabled")  # 读取清理开关状态
            self._notify_enabled = config.get("notify_enabled")  # 读取通知开关状态
            self._onlyonce = config.get("onlyonce")
            self._cron = config.get("cron")
            self._mediaserver = config.get("mediaserver")
            self._target_libraries = config.get("target_libraries", "").split(",") if config.get("target_libraries") else []
            
            # 保存配置
            self.__update_config()
            
            # 初始化 Emby 连接信息
            if self._mediaserver:
                server_info = self.mediaserver_helper.get_service(self._mediaserver)
                if server_info:
                    self._EMBY_USER = server_info.instance.get_user()
                    self._EMBY_APIKEY = server_info.config.config.get("apikey")
                    self._EMBY_HOST = server_info.config.config.get("host")
                    if self._EMBY_HOST:
                        if not self._EMBY_HOST.endswith("/"):
                            self._EMBY_HOST += "/"
                        if not self._EMBY_HOST.startswith("http"):
                            self._EMBY_HOST = "http://" + self._EMBY_HOST
            
            # 立即运行
            if self._onlyonce:
                logger.info(f"季度标签服务启动，立即运行一次...")
                # 创建定时任务
                self._scheduler = BackgroundScheduler(timezone=settings.TZ)
                self._scheduler.add_job(func=self.process_seasonal_tags,
                                      trigger='date',
                                      run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3),
                                      name="季度标签")
                # 启动任务
                if self._scheduler.get_jobs():
                    self._scheduler.print_jobs()
                    self._scheduler.start()
                    logger.info("立即运行任务已启动")
                
                # 关闭一次性开关
                self._onlyonce = False
                # 保存配置
                self.__update_config()
                
            # 周期运行
            elif self._enabled and self._cron:
                try:
                    self._scheduler = BackgroundScheduler(timezone=settings.TZ)
                    self._scheduler.add_job(func=self.process_seasonal_tags,
                                          trigger=CronTrigger.from_crontab(self._cron),
                                          name="季度标签")
                    if self._scheduler.get_jobs():
                        self._scheduler.print_jobs()
                        self._scheduler.start()
                        logger.info(f"周期任务已启动，执行周期：{self._cron}")
                except Exception as err:
                    logger.error(f"周期任务启动失败：{str(err)}")

    def __update_config(self):
        """
        更新配置
        """
        self.update_config({
            "enabled": self._enabled,
            "clean_enabled": self._clean_enabled,  # 保存清理开关状态
            "notify_enabled": self._notify_enabled,  # 保存通知开关状态
            "onlyonce": self._onlyonce,
            "cron": self._cron,
            "mediaserver": self._mediaserver,
            "target_libraries": ",".join(self._target_libraries) if self._target_libraries else ""
        })

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        插件配置页面
        """
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
                                            'label': '启用插件',
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
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'clean_enabled',
                                            'label': '清理非目标库季度标签',
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
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'onlyonce',
                                            'label': '立即运行一次',
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
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'notify_enabled',
                                            'label': '开启通知',
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
                                            'placeholder': '5位cron表达式，留空自动'
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
                                            'model': 'mediaserver',
                                            'label': '媒体服务器',
                                            'items': [{"title": config.name, "value": config.name}
                                                     for config in self.mediaserver_helper.get_configs().values() 
                                                     if config.type == "emby"],
                                            'clearable': True
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
                                            'model': 'target_libraries',
                                            'label': '目标媒体库',
                                            'placeholder': '多个媒体库用英文逗号隔开',
                                            'rows': 2,
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
            "clean_enabled": False,  # 添加清理开关默认值
            "notify_enabled": False,  # 添加通知开关默认值
            "onlyonce": False,
            "cron": "5 1 * * *",
            "target_libraries": "",
            "mediaserver": None
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
        if not self._mediaserver:
            return
        
        try:
            # 获取媒体服务器实例
            server_info = self.mediaserver_helper.get_service(self._mediaserver)
            if not server_info:
                return
            
            # 获取所有媒体库
            libraries = server_info.instance.get_librarys()
            if not libraries:
                return
            
            # 记录处理数量
            processed_items = 0
            updated_items = 0
            
            # 只处理指定的媒体库
            for library in libraries:
                # 检查是否是目标媒体库
                if library.name not in self._target_libraries:
                    continue
                    
                logger.info(f"开始处理媒体库：{library.name}")
                
                # 获取媒体库中的项目
                items = server_info.instance.get_items(library.id)
                if not items:
                    continue
                    
                # 处理每个项目
                for item in items:
                    if not item:
                        continue
                        
                    processed_items += 1
                    logger.info(f"正在处理第 {processed_items} 个项目：{item.title}")
                    
                    try:
                        # 获取所有季信息
                        seasons = self.tmdbchain.tmdb_seasons(tmdbid=item.tmdbid)
                        if not seasons:
                            continue
                        
                        # 用于存储所有需要添加的季���标签
                        season_tags = set()
                        
                        # 处理每一季
                        for season in seasons:
                            # 跳过特别篇
                            if season.season_number == 0:
                                continue
                            
                            # 获取该的首播日期
                            if not season.air_date:
                                continue
                            
                            # 生成该季的标签
                            season_tag = self._get_season_tag(season.air_date)
                            if season_tag:
                                season_tags.add(season_tag)
                                logger.debug(f"{item.title} 第{season.season_number}季 标签：{season_tag}")
                        
                        # 获取当前标签
                        req_url = f"{self._EMBY_HOST}emby/Users/{self._EMBY_USER}/Items/{item.item_id}?api_key={self._EMBY_APIKEY}"
                        current_tags = []
                        with RequestUtils().get_res(req_url) as res:
                            if res and res.status_code == 200:
                                item_info = res.json()
                                current_tags = [tag.get('Name') for tag in item_info.get("TagItems", [])]
                        
                        # 添加新标签
                        for tag in season_tags:
                            if tag not in current_tags:
                                # 构造标签数据
                                tags = {"Tags": [{"Name": tag}]}
                                
                                # 通过 Emby API 添加标签
                                req_url = f"{self._EMBY_HOST}emby/Items/{item.item_id}/Tags/Add?api_key={self._EMBY_APIKEY}"
                                with RequestUtils(content_type="application/json").post_res(url=req_url, json=tags) as res:
                                    if res and res.status_code == 204:
                                        logger.info(f"为 {item.title} 添加标签：{tag}")
                                        updated_items += 1
                                    else:
                                        logger.error(f"为 {item.title} 添加标签 {tag} 失败")
                                    
                    except Exception as e:
                        logger.error(f"处理 {item.title} 时出错：{str(e)}")
                        continue
                    
            # 如果启用了清理,则清理非目标库的标签
            cleaned_items = 0
            if self._clean_enabled:
                logger.info("开始清理非目标媒体库的季度标签...")
                cleaned_items = self.clean_season_tags()
                
            # 处理完成后输出统计信息
            logger.info("="*50)
            logger.info("季度标签处理完成！")
            logger.info(f"处理项目数: {processed_items}")
            logger.info(f"更新标签数: {updated_items}")
            if self._clean_enabled:
                logger.info(f"清理标签数: {cleaned_items}")
            logger.info("="*50)
            
            # 添加处理完成通知
            if self._notify_enabled:
                message = (
                    f"季度标签处理完成！\n"
                    f"处理项目数: {processed_items}\n"
                    f"更新标签数: {updated_items}"
                )
                if self._clean_enabled:
                    message += f"\n清理标签数: {cleaned_items}"
                    
                self.post_message(
                    title="季度标签处理完成",
                    text=message
                )
                
        except Exception as e:
            logger.error(f"处理季度标签时出错：{str(e)}")
            # 添加错误通知
            if self._notify_enabled:
                self.post_message(
                    title="季度标签处理出错",
                    text=f"错误信息：{str(e)}"
                )

    def _get_item_tags(self, server, item_id: str) -> List[str]:
        """
        获取媒体的标签
        """
        try:
            req_url = f"{self._EMBY_HOST}emby/Users/{self._EMBY_USER}/Items/{item_id}?api_key={self._EMBY_APIKEY}"
            with RequestUtils().get_res(req_url) as res:
                if res and res.status_code == 200:
                    item = res.json()
                    return [tag.get('Name') for tag in item.get("TagItems", [])]
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
            logger.error(f"添加签失败：{str(e)}")
            return False

    def _calculate_season_tag(self, server, item) -> Optional[str]:
        """
        计算季度标签
        """
        try:
            # 获取TMDB ID
            tmdb_id = None
            if hasattr(item, 'provider_ids'):
                provider_ids = item.provider_ids or {}
                if provider_ids.get("Tmdb"):
                    tmdb_id = provider_ids.get("Tmdb")
            
            if not tmdb_id:
                logger.debug(f"{item.title} 未找到TMDB ID")
                return None
            
            # 获取首播日期
            air_date = None
            if hasattr(item, 'type') and item.type == "Series":
                # 获取剧集信息
                series_info = self.tmdbchain.get_series_detail(tmdbid=tmdb_id)
                if series_info:
                    air_date = series_info.first_air_date
            else:
                logger.debug(f"{item.title} 不是剧集")
                return None
                
            if not air_date:
                logger.debug(f"{item.title} 未找到首播日期")
                return None
            
            # 解析日期
            try:
                air_date = datetime.strptime(air_date, '%Y-%m-%d')
            except Exception as e:
                logger.error(f"日期解析失败: {str(e)}")
                return None
            
            # 计算季度
            month = air_date.month
            year = air_date.year
            
            if 1 <= month <= 3:
                season_month = 1
            elif 4 <= month <= 6:
                season_month = 4
            elif 7 <= month <= 9:
                season_month = 7
            else:
                season_month = 10
                
            # 生成标签
            season_tag = f"{year}年{season_month:02d}月番"
            logger.debug(f"{item.title} 计算得到标签：{season_tag}")
            return season_tag
            
        except Exception as e:
            logger.error(f"计算标签失败：{str(e)}")
            return None

    def service_infos(self, server_type: Optional[str] = None) -> Optional[Dict[str, ServiceInfo]]:
        """
        获取媒体服务器实例
        """
        if not self._mediaservers:
            logger.warning("尚未配置媒体服务器，请检查配置")
            return None

        services = self.mediaserver_helper.get_services(type_filter=server_type, name_filters=self._mediaservers)
        if not services:
            logger.warning("获取媒体服务器实例失败，请检查配置")
            return None

        active_services = {}
        for service_name, service_info in services.items():
            if service_info.instance.is_inactive():
                logger.warning(f"媒体服务器 {service_name} 未连接，请检查配置")
            else:
                active_services[service_name] = service_info

        if not active_services:
            logger.warning("没有已连接的媒体服务器，请检查配置")
            return None

        return active_services

    def get_state(self) -> bool:
        """
        获取插件态
        """
        return self._enabled
    
    def get_command(self) -> List[Dict[str, Any]]:
        """
        定义远程控制命令
        """
        return [
            {
                "cmd": "/seasonaltags",
                "event": EventType.PluginAction,
                "desc": "手动执行季度标签处理",
                "category": "媒体管理",
                "data": {
                    "action": "seasonaltags"
                }
            },
            {
                "cmd": "/clean_season_tags",
                "event": EventType.PluginAction,
                "desc": "清理非目标媒体库的季度标签",
                "category": "媒体管理",
                "data": {
                    "action": "clean_season_tags"
                }
            }
        ]

    def get_api(self) -> List[Dict[str, Any]]:
        """
        定义API接口
        """
        return [{
            "path": "/seasonaltags/libraries",
            "endpoint": self.get_libraries,
            "methods": ["GET"],
            "summary": "获取媒体库列表",
            "description": "获取定媒体服务器的媒体库列表"
        }]

    def get_page(self) -> List[dict]:
        """
        插件页面配置
        """
        return [
            {
                'component': 'div',
                'props': {
                    'class': 'pa-0'
                },
                'content': [
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
                                        'component': 'api',
                                        'props': {
                                            'url': '/seasonaltags/libraries?server={mediaserver}',
                                            'method': 'GET'
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ]

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
            logger.error(f"加剧集和季标签失败：{str(e)}")
            return False

    @eventmanager.register(EventType.PluginAction)
    def manual_process(self, event: Event):
        """
        手动处理
        """
        if event:
            event_data = event.event_data
            if not event_data or event_data.get("action") != "seasonaltags":
                return
            logger.info("收到手动处理请求")
            self.post_message(channel=event.event_data.get("channel"),
                             title="开始处理季度标签 ...",
                             userid=event.event_data.get("user"))
            # 执行处理
            self.process_seasonal_tags()
            self.post_message(channel=event.event_data.get("channel"),
                             title="季度标签处理完成！",
                             userid=event.event_data.get("user"))

    def get_libraries(self, server: str):
        """
        获取媒体库列表
        """
        if not server:
            return []
        
        try:
            # 获取媒体服务器实例
            server_info = self.mediaserver_helper.get_service(server)
            if not server_info:
                return []
            
            # 获取媒体库列表
            libraries = server_info.instance.get_librarys()
            if not libraries:
                return []
            
            # 转换为下拉菜单选项格式
            return [{
                "title": library.name,
                "value": f"{server}|{library.name}"
            } for library in libraries]
            
        except Exception as e:
            logger.error(f"获取媒体库列表失败：{str(e)}")
            return []

    def __update_item(self, server: str, item: MediaServerItem, server_type: str = None,
                      mediainfo: MediaInfo = None, season: int = None):
        """
        更新媒体服务器中的条目
        """
        # 识别媒体信息
        if not mediainfo:
            mtype = MediaType.TV if item.item_type in ['Series', 'show'] else MediaType.MOVIE
            
            # 优先使用 TMDB ID
            if item.tmdbid:
                mediainfo = self.chain.recognize_media(mtype=mtype, tmdbid=item.tmdbid)
            
            # 如果没有 TMDB ID 或识别失败，尝试通过名称搜索
            if not mediainfo:
                logger.info(f"{item.title} 未找到tmdbid或识别失败，尝试通过名称搜索...")
                # 通过标题搜索 TMDB
                mediainfo = self.chain.recognize_media(
                    mtype=mtype,
                    title=item.title,
                    year=item.year
                )
            
            # 如果仍然无法识别
            if not mediainfo:
                logger.warn(f"{item.title} 未识别到媒体信息")
                return

        # 获取媒体项
        iteminfo = self.get_iteminfo(server=server, server_type=server_type, itemid=item.item_id)
        if not iteminfo:
            logger.warn(f"{item.title} 未找到媒体项")
            return

        # ... 后续处理代码保持不变 ...

    def _get_season_tag(self, air_date: str) -> str:
        """
        根据首播日期生成季度标签，格式为 YYYY年X月番
        """
        try:
            # 解析日期
            date_obj = datetime.strptime(air_date, '%Y-%m-%d')
            year = date_obj.year
            month = date_obj.month
            
            # 获取季度月份
            if 1 <= month <= 3:
                season_month = 1
            elif 4 <= month <= 6:
                season_month = 4
            elif 7 <= month <= 9:
                season_month = 7
            else:
                season_month = 10
                
            # 返回标签
            return f"{year}年{season_month}月番"
        except Exception as e:
            logger.error(f"生成季度标签失败：{str(e)}")
            return None

    def _update_item_tags(self, server: str, server_type: str, item_id: str, current_tags: List[str], new_tag: str) -> bool:
        """
        更新媒体标签
        """
        try:
            # 构造标签数据
            tags = {"Tags": [{"Name": new_tag}]}
            
            # 添加标签
            req_url = f"{self._EMBY_HOST}emby/Items/{item_id}/Tags/Add?api_key={self._EMBY_APIKEY}"
            with RequestUtils(content_type="application/json").post_res(url=req_url, json=tags) as res:
                if res and res.status_code == 204:
                    return True
                else:
                    logger.error(f"添加标签失败，错误码：{res.status_code if res else 'None'}")
                    return False
        except Exception as e:
            logger.error(f"更新标签失败：{str(e)}")
            return False

    def clean_season_tags(self):
        """
        清理非目标媒体库的季度标签
        返回清理的项目数
        """
        cleaned_count = 0
        try:
            # 获取媒体服务器实例
            server_info = self.mediaserver_helper.get_service(self._mediaserver)
            if not server_info:
                return cleaned_count
            
            # 获取所有媒体库
            libraries = server_info.instance.get_librarys()
            if not libraries:
                return cleaned_count
            
            # 遍历所有媒体库
            for library in libraries:
                # 跳过目标媒体库
                if library.name in self._target_libraries:
                    continue
                    
                logger.info(f"正在清理媒体库：{library.name}")
                
                # 获取媒体库中的项目
                items = server_info.instance.get_items(library.id)
                if not items:
                    continue
                    
                # 处理每个项目
                for item in items:
                    if not item:
                        continue
                        
                    # 获取当前标签
                    req_url = f"{self._EMBY_HOST}emby/Users/{self._EMBY_USER}/Items/{item.item_id}?api_key={self._EMBY_APIKEY}"
                    current_tags = []
                    with RequestUtils().get_res(req_url) as res:
                        if res and res.status_code == 200:
                            item_info = res.json()
                            current_tags = [tag.get('Name') for tag in item_info.get("TagItems", [])]
                    
                    # 检查是否有季度标签
                    season_tags = [tag for tag in current_tags if self._is_season_tag(tag)]
                    if not season_tags:
                        continue
                        
                    # 移除季度标签
                    for tag in season_tags:
                        # 构造删除标签的请求体
                        remove_tags = {
                            "Tags": [
                                {"Name": tag}
                            ]
                        }
                        
                        # 使用 POST 请求移除标签
                        req_url = f"{self._EMBY_HOST}emby/Items/{item.item_id}/Tags/Delete?api_key={self._EMBY_APIKEY}"
                        logger.debug(f"尝试删除标签，请求URL: {req_url}")
                        logger.debug(f"请求体: {remove_tags}")
                        
                        req = RequestUtils(content_type="application/json")
                        try:
                            res = req.post_res(url=req_url, json=remove_tags)
                            logger.debug(f"删除请求响应: 状态码={res.status_code if res else 'None'}")
                            if res:
                                logger.debug(f"响应内容: {res.text}")
                            
                            if res and res.status_code == 204:
                                logger.info(f"从 {item.title} 移除标签：{tag}")
                                cleaned_count += 1
                            else:
                                logger.error(f"从 {item.title} 移除标签 {tag} 失败，状态码：{res.status_code if res else 'None'}")
                        except Exception as e:
                            logger.error(f"删除标签请求失败: {str(e)}")
                            logger.error(f"请求URL: {req_url}")
                            continue
                            
            return cleaned_count
                            
        except Exception as e:
            logger.error(f"清理标签失败：{str(e)}")
            return cleaned_count

    def _is_season_tag(self, tag: str) -> bool:
        """
        判断是否是季度标签
        """
        try:
            # 季度标签格式: YYYY年MM月番
            if not tag or len(tag) != 9:
                return False
            
            # 检查格式
            if not (tag.endswith("月番") and "年" in tag):
                return False
            
            # 分割年月
            year_str = tag.split("年")[0]
            month_str = tag.split("年")[1].split("月")[0]
            
            # 验证年份
            year = int(year_str)
            if year < 1900 or year > 2100:
                return False
            
            # 验证月份
            month = int(month_str)
            if month not in [1, 4, 7, 10]:
                return False
            
            return True
        except:
            return False

    @eventmanager.register(EventType.PluginAction)
    def plugin_action(self, event: Event):
        """
        插件动作
        """
        if event:
            event_data = event.event_data
            if not event_data:
                return
            
            # 处理清理动作
            if event_data.get("action") == "clean_season_tags":
                self.post_message(channel=event_data.get("channel"),
                                title="开始清理季度标签 ...",
                                userid=event_data.get("user"))
                
                if self.clean_season_tags():
                    self.post_message(channel=event_data.get("channel"),
                                    title="季度标签清理完成！",
                                    userid=event_data.get("user"))
                else:
                    self.post_message(channel=event_data.get("channel"),
                                    title="季度标签清理失败！",
                                    userid=event_data.get("user"))
                                
            # 处理其他动作
            elif event_data.get("action") == "seasonaltags":
                # ... 现有的手动处理代码 ...
                pass

    def get_dashboard(self, key: str, **kwargs) -> Optional[Tuple[Dict[str, Any], Dict[str, Any], List[dict]]]:
        """
        获取插件仪表盘页面
        """
        # 列配置
        cols = {
            "cols": 12
        }
        # 全局配置
        attrs = {
            "refresh": 60  # 60秒自动刷新
        }
        
        # 获取统计数据
        statistics = self.__get_statistics()
        
        # 拼装页面元素
        elements = [
            # 顶部统计卡片
            {
                'component': 'VRow',
                'content': [
                    # 总处理数量
                    {
                        'component': 'VCol',
                        'props': {
                            'cols': 12,
                            'md': 3,
                            'sm': 6
                        },
                        'content': [{
                            'component': 'VCard',
                            'props': {
                                'variant': 'tonal',
                            },
                            'content': [{
                                'component': 'VCardText',
                                'content': [
                                    {
                                        'component': 'div',
                                        'content': [
                                            {
                                                'component': 'span',
                                                'props': {'class': 'text-caption'},
                                                'text': '总处理数量'
                                            },
                                            {
                                                'component': 'div',
                                                'props': {'class': 'text-h6'},
                                                'text': f"{statistics['total_processed']}"
                                            }
                                        ]
                                    }
                                ]
                            }]
                        }]
                    },
                    # 成功处理数量
                    {
                        'component': 'VCol',
                        'props': {
                            'cols': 12,
                            'md': 3,
                            'sm': 6
                        },
                        'content': [{
                            'component': 'VCard',
                            'props': {
                                'variant': 'tonal',
                            },
                            'content': [{
                                'component': 'VCardText',
                                'content': [
                                    {
                                        'component': 'div',
                                        'content': [
                                            {
                                                'component': 'span',
                                                'props': {'class': 'text-caption'},
                                                'text': '成功处理数量'
                                            },
                                            {
                                                'component': 'div',
                                                'props': {'class': 'text-h6'},
                                                'text': f"{statistics['success_count']}"
                                            }
                                        ]
                                    }
                                ]
                            }]
                        }]
                    },
                    # 失败数量
                    {
                        'component': 'VCol',
                        'props': {
                            'cols': 12,
                            'md': 3,
                            'sm': 6
                        },
                        'content': [{
                            'component': 'VCard',
                            'props': {
                                'variant': 'tonal',
                            },
                            'content': [{
                                'component': 'VCardText',
                                'content': [
                                    {
                                        'component': 'div',
                                        'content': [
                                            {
                                                'component': 'span',
                                                'props': {'class': 'text-caption'},
                                                'text': '失败数量'
                                            },
                                            {
                                                'component': 'div',
                                                'props': {'class': 'text-h6'},
                                                'text': f"{statistics['failed_count']}"
                                            }
                                        ]
                                    }
                                ]
                            }]
                        }]
                    },
                    # 跳过数量
                    {
                        'component': 'VCol',
                        'props': {
                            'cols': 12,
                            'md': 3,
                            'sm': 6
                        },
                        'content': [{
                            'component': 'VCard',
                            'props': {
                                'variant': 'tonal',
                            },
                            'content': [{
                                'component': 'VCardText',
                                'content': [
                                    {
                                        'component': 'div',
                                        'content': [
                                            {
                                                'component': 'span',
                                                'props': {'class': 'text-caption'},
                                                'text': '跳过数量'
                                            },
                                            {
                                                'component': 'div',
                                                'props': {'class': 'text-h6'},
                                                'text': f"{statistics['skipped_count']}"
                                            }
                                        ]
                                    }
                                ]
                            }]
                        }]
                    }
                ]
            },
            # 处理历史记录表格
            {
                'component': 'VRow',
                'content': [{
                    'component': 'VCol',
                    'props': {
                        'cols': 12
                    },
                    'content': [{
                        'component': 'VCard',
                        'props': {
                            'variant': 'tonal',
                        },
                        'content': [{
                            'component': 'VCardTitle',
                            'content': '处理历史'
                        }, {
                            'component': 'VCardText',
                            'content': [{
                                'component': 'VTable',
                                'props': {
                                    'headers': [
                                        {'title': '时间', 'key': 'time'},
                                        {'title': '媒体标题', 'key': 'title'},
                                        {'title': '原标签', 'key': 'old_tag'},
                                        {'title': '新标签', 'key': 'new_tag'},
                                        {'title': '状态', 'key': 'status'}
                                    ],
                                    'items': self.__get_history_items()
                                }
                            }]
                        }]
                    }]
                }]
            }
        ]
        
        return cols, attrs, elements
    def __get_statistics(self) -> dict:
        """获取统计数据"""
        return {
            "total_processed": len(self.history_data),
            "success_count": len([x for x in self.history_data.values() if x.get('status') == 'success']),
            "failed_count": len([x for x in self.history_data.values() if x.get('status') == 'failed']),
            "skipped_count": len([x for x in self.history_data.values() if x.get('status') == 'skipped'])
        }
        
    def __get_history_items(self) -> List[dict]:
        """获取历史记录表格数据"""
        items = []
        for item_id, data in self.history_data.items():
            items.append({
                'time': data.get('time'),
                'title': data.get('title'),
                'old_tag': data.get('old_tag'),
                'new_tag': data.get('new_tag'), 
                'status': data.get('status')
            })
        return sorted(items, key=lambda x: x['time'], reverse=True)
        
    def __is_processed(self, item_id: str) -> bool:
        """检查是否已处理过"""
        return item_id in self.history_data
        
    def process_item(self, item_id: str, title: str, old_tag: str):
        """处理单个项目"""
        # 检查是否已处理
        if self.__is_processed(item_id):
            logger.info(f"项目 {title} 已处理过,跳过")
            return
            
        try:
            # 处理标签逻辑
            new_tag = self.__process_tag(old_tag)
            
            # 记录处理结果
            self.history_data[item_id] = {
                'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'title': title,
                'old_tag': old_tag,
                'new_tag': new_tag,
                'status': 'success'
            }
            
        except Exception as e:
            logger.error(f"处理失败: {str(e)}")
            # 记录失败信息
            self.history_data[item_id] = {
                'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'title': title,
                'old_tag': old_tag,
                'new_tag': None,
                'status': 'failed',
                'error': str(e)
            }
            
        # 保存历史记录
        self.save_data('history', self.history_data)

    def post_message(self, channel: Any = None, title: str = None, text: str = None, image: str = None, userid: str = None):
        """
        发送消息
        """
        if not self._notify_enabled:  # 检查通知开关状态
            return
            
        super().post_message(channel=channel, 
                           title=title, 
                           text=text, 
                           image=image, 
                           userid=userid)
