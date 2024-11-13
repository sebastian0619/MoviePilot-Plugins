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
        
        # 初始化组件
        self.mediaserver_helper = MediaServerHelper()
        self.tmdbchain = TmdbChain()
        
        if config:
            self._enabled = config.get("enabled")
            self._onlyonce = config.get("onlyonce")
            self._cron = config.get("cron")
            self._mediaserver = config.get("mediaserver")
            # 从配置中获取媒体库名称
            self._target_libraries = config.get("target_libraries", "").split(",") if config.get("target_libraries") else []
            
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
            "onlyonce": self._onlyonce,
            "cron": self._cron,
            "mediaserver": self._mediaserver,
            "library_text": '\n'.join(self._libraries)
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
                    {
                        "component": "VRow",
                        "content": [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
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
                                            'multiple': True,
                                            'chips': True,
                                            'clearable': True,
                                            'model': 'mediaserver',
                                            'label': '媒体服务器',
                                            'items': [{"title": config.name, "value": config.name}
                                                      for config in self.mediaserver_helper.get_configs().values() if
                                                      config.type == "emby"]
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
                                            'rows': 3,
                                            'placeholder': '媒体库名称，多个用英文逗号分隔'
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
                                    'cols': 12,
                                },
                                'content': [
                                    {
                                        'component': 'VAlert',
                                        'props': {
                                            'type': 'info',
                                            'variant': 'tonal',
                                            'text': '选择媒体服务器后，输入需要添加季度标签的媒体库名称，每行一个，将根据剧集的首播时间自动添加对应季度标签。'
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
        
        # 获取媒体服务器实例
        server_info = self.mediaserver_helper.get_service(self._mediaserver)
        if not server_info:
            return
        
        # 设置 Emby 连接信息
        self._EMBY_USER = server_info.instance.get_user()
        self._EMBY_APIKEY = server_info.config.config.get("apikey")
        self._EMBY_HOST = server_info.config.config.get("host")
        if not self._EMBY_HOST.endswith("/"):
            self._EMBY_HOST += "/"
        if not self._EMBY_HOST.startswith("http"):
            self._EMBY_HOST = "http://" + self._EMBY_HOST
        
        # 获取所有媒体库
        libraries = server_info.instance.get_librarys()
        if not libraries:
            return
        
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
                    
                logger.debug(f"正在处理：{item.title}")
                
                # 获取当前标签
                current_tags = self._get_item_tags(server_info.instance, item.item_id)
                logger.debug(f"{item.title} 当前标签：{current_tags}")
                
                # 获取媒体信息
                mediainfo = None
                try:
                    if item.tmdbid:
                        logger.debug(f"{item.title} 使用TMDB ID：{item.tmdbid}")
                        mediainfo = self.chain.recognize_media(
                            mtype=MediaType.TV,  # 动漫库都是剧集类型
                            tmdbid=item.tmdbid
                        )
                    
                    if not mediainfo:
                        logger.debug(f"{item.title} 未找到TMDB ID")
                        continue
                    
                    # 获取首播日期
                    air_date = mediainfo.release_date or mediainfo.first_air_date
                    if not air_date:
                        logger.debug(f"{item.title} 未获取到首播日期")
                        continue
                    
                    # 生成季度标签
                    season_tag = self._get_season_tag(air_date)
                    if not season_tag:
                        logger.debug(f"{item.title} 无法计算季度标签")
                        continue
                    
                    # 更新标签
                    if season_tag not in current_tags:
                        if self._update_item_tags(self._mediaserver, server_info.type, 
                                                item.item_id, current_tags, season_tag):
                            logger.info(f"为 {item.title} 添加标签：{season_tag}")
                        else:
                            logger.error(f"为 {item.title} 添加标签 {season_tag} 失败")
                        
                except Exception as e:
                    logger.error(f"处理 {item.title} 失败：{str(e)}")
                    continue

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
        return [{
            "cmd": "/seasonaltags",
            "event": EventType.PluginAction,
            "desc": "手动执行季度标签处理",
            "category": "媒体管理",
            "data": {
                "action": "seasonaltags"
            }
        }]

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