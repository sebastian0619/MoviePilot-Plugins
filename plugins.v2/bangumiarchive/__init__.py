"""
BangumiArchive插件
用于自动归档完结/连载番剧
"""
from typing import Any, Dict, List, Tuple
from app.core.config import settings
from app.core.event import eventmanager, Event, EventType
from app.core.context import Context, MediaInfo
from app.plugins import _PluginBase
from app.schemas.types import MediaType
from app.log import logger
from app.helper.module import ModuleHelper
from datetime import datetime
import os
import shutil
from pathlib import Path
from app.helper.notification import NotificationHelper
from app.chain.media import MediaChain
from app.helper.nfo import NfoReader
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz
from datetime import timedelta
import time

class BangumiArchive(_PluginBase):
    # 插件基础信息
    plugin_name = "连载番剧归档"
    plugin_desc = "自动检测连载目录中的番剧，识别完结情况并归档到完结目录"
    plugin_version = "1.2"
    plugin_author = "Sebastian0619"
    author_url = "https://github.com/sebastian0619"
    plugin_config_prefix = "bangumiarchive_"
    plugin_order = 21
    auth_level = 1

    # 配置信息
    _enabled = False
    _onlyonce = False
    _cron = None
    _paths = None
    _test_mode = False
    _notify = False
    _bidirectional = False
    _end_after_days = 730  # 默认730天(2年)

    # 完结状态列表
    END_STATUS = [
        'ended',           # 正常完结
        'canceled',        # 被取消
        'completed',       # 完成
        'released',        # 已发布（适用于剧场版）
        'discontinued'     # 停播
    ]
    
    # 在类中初始化
    meta_helper = None
    mediachain = None
    
    def init_plugin(self, config: dict = None):
        """
        插件初始化
        """
        try:
            self.meta_helper = ModuleHelper()
            self.mediachain = MediaChain()
            
            if config:
                self._enabled = config.get("enabled")
                self._onlyonce = config.get("onlyonce")
                self._cron = config.get("cron")
                self._paths = config.get("paths")
                self._test_mode = config.get("test_mode")
                self._notify = config.get("notify")
                self._bidirectional = config.get("bidirectional")
                # 添加新配置项，如果未配置则使用默认值
                self._end_after_days = int(config.get("end_after_days", 730))
                
                # 如果开启立即运行
                if self._enabled and self._onlyonce:
                    logger.info(f"番剧归档服务启动，立即运行一次...")
                    # 行一次任务
                    self.check_and_move()
                    # 关闭一次性开关
                    self._onlyonce = False
                    self.__update_config()
                
                # 周期运行
                if self._enabled and self._cron:
                    try:
                        self._scheduler = BackgroundScheduler(timezone=settings.TZ)
                        self._scheduler.add_job(func=self.check_and_move,
                                              trigger=CronTrigger.from_crontab(self._cron),
                                              name="番剧归档")
                        if self._scheduler.get_jobs():
                            self._scheduler.print_jobs()
                            self._scheduler.start()
                            logger.info(f"周期任务已启动，执行周期：{self._cron}")
                    except Exception as err:
                        logger.error(f"周期任务启动失败：{str(err)}")
        except Exception as e:
            logger.error(f"插件初始化失败: {str(e)}")

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """配置表单"""
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
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'onlyonce',
                                            'label': '立即运行一次'
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
                                            'model': 'test_mode',
                                            'label': '测试模式'
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
                                            'model': 'notify',
                                            'label': '发送通知'
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
                                            'model': 'bidirectional',
                                            'label': '双向监控'
                                        }
                                    }
                                ]
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
                                    'model': 'days_to_complete',
                                    'label': '视为完结天数',
                                    'placeholder': '输入天数，例如：730'
                                }
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
                                        'component': 'VTextarea',
                                        'props': {
                                            'model': 'paths',
                                            'label': '目录配置',
                                            'placeholder': '连载目录:完结目录\n例如：/media/anime/airing:/media/anime/ended',
                                            'rows': 2
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
            'onlyonce': False,
            'test_mode': False,
            'notify': False,
            'bidirectional': False,
            'cron': '5 1 * * *',
            'paths': '',
            'end_after_days': 730
        }

    def __send_notification(self, title: str, text: str):
        """
        发送通知
        """
        if self._notify:
            NotificationHelper().send_message(
                title=title,
                text=text
            )

    def check_status(self, tmdb_id: int, max_retries: int = 3) -> tuple:
        """
        检查剧集状态，支持重试
        """
        for retry in range(max_retries):
            try:
                mediainfo = self.mediachain.recognize_media(tmdbid=tmdb_id, mtype=MediaType.TV)
                if mediainfo:
                    return self.__parse_status(mediainfo)
                if retry < max_retries - 1:
                    logger.warning(f"第 {retry + 1} 次获取媒体信息失败，准备重试...")
                    time.sleep(1)  # 重试前等待1秒
            except Exception as e:
                if retry < max_retries - 1:
                    logger.error(f"第 {retry + 1} 次获取媒体信息出错: {str(e)}，准备重试...")
                    time.sleep(1)
                else:
                    logger.error(f"最终获取媒体信息失败: {str(e)}")
        return False, "无法识别媒体信息"

    def __parse_status(self, mediainfo: MediaInfo) -> tuple:
        """
        解析媒体状态
        """
        # 打印基本信息
        logger.info(f"媒体信息: {mediainfo.title} ({mediainfo.year})")
        logger.info(f"当前状态: {mediainfo.status}")
        
        # 检查状态
        status = mediainfo.status.lower() if mediainfo.status else ''
        if status in self.END_STATUS:
            logger.info(f"媒体已完结，完结状态: {status}")
            return True, status
        
        # 检查最后播出日期
        if mediainfo.last_air_date:
            try:
                last_date = datetime.strptime(mediainfo.last_air_date, '%Y-%m-%d')
                days_diff = (datetime.now() - last_date).days
                
                # 计算具体时间
                years = days_diff // 365
                remaining_days = days_diff % 365
                months = remaining_days // 30
                days = remaining_days % 30
                
                time_desc = []
                if years > 0:
                    time_desc.append(f"{years}年")
                if months > 0:
                    time_desc.append(f"{months}个月")
                if days > 0:
                    time_desc.append(f"{days}天")
                    
                time_str = "".join(time_desc)
                
                logger.info(f"最后播出日期: {mediainfo.last_air_date}")
                logger.info(f"距今已过: {time_str}")
                logger.info(f"完结判定阈值: {self._end_after_days}天")
                
                # 使用配置的天数判断
                if days_diff > self._end_after_days:
                    logger.info(f"超过{self._end_after_days}天未更新，视为完结")
                    return True, f"最后播出超过{self._end_after_days}天 ({mediainfo.last_air_date})"
                else:
                    logger.info(f"未超过{self._end_after_days}天，视为连载中")
                    
            except ValueError as e:
                logger.error(f"日期解析错误: {str(e)}")
        else:
            logger.info("无最后播出日期信息")
                
        return False, status

    def __save_history(self, source: str, target: str, title: str, tmdb_id: int, status: str):
        """
        保存移动历史
        """
        try:
            TransferHistory(
                source_path=source,
                target_path=target,
                media_name=title,
                tmdb_id=tmdb_id,
                status=status,
                transfer_type="bangumiarchive",
                create_time=datetime.now()
            ).save()
        except Exception as e:
            logger.error(f"保存历记录失败: {str(e)}")

    def __process_directory(self, source_dir: str, target_dir: str, check_ended: bool = True):
        """
        处理目录
        :param source_dir: 源目录
        :param target_dir: 目标目录
        :param check_ended: True检查完结->连载，False检查连载->完结
        """
        if not os.path.exists(source_dir):
            logger.error(f"源目录不存在: {source_dir}")
            return

        # 遍历源目录
        for item in os.listdir(source_dir):
            item_path = os.path.join(source_dir, item)
            if not os.path.isdir(item_path):
                continue
            
            logger.debug(f"处理目录: {item_path}")
            
            # 获取TMDB ID
            tmdb_id = self.__get_tmdb_id(item_path)
            if not tmdb_id:
                logger.warning(f"无法获取TMDB ID: {item}")
                continue
            
            # 检查状态
            is_ended, status = self.check_status(tmdb_id)
            if check_ended != is_ended:
                # 状态不符合要求
                logger.debug(f"保持不变的剧集: {item} (状态: {status})")
                continue
            
            # 移动目录
            target_path = os.path.join(target_dir, item)
            if self._test_mode:
                logger.info(f"测试模式 - 需要移动: {item_path} -> {target_path}")
            else:
                try:
                    shutil.move(item_path, target_path)
                    logger.info(f"已移动: {item_path} -> {target_path}")
                except Exception as e:
                    logger.error(f"移动失败: {str(e)}")

    def __get_tmdb_id(self, dir_path: str) -> int:
        """
        获取目录的TMDB ID
        优先从NFO文件获取,其次从文件名解析
        """
        try:
            # 尝试从tvshow.nfo文件获取
            nfo_path = os.path.join(dir_path, "tvshow.nfo")
            if os.path.exists(nfo_path):
                tmdbid = self.__get_tmdbid_from_nfo(nfo_path)
                if tmdbid:
                    logger.debug(f"从tvshow.nfo获取到TMDB ID: {tmdbid}")
                    return tmdbid
            
            # 查找目录下的其他nfo文件
            for file in os.listdir(dir_path):
                if file.endswith(".nfo"):
                    nfo_path = os.path.join(dir_path, file)
                    try:
                        tmdbid = self.__get_tmdbid_from_nfo(nfo_path)
                        if tmdbid:
                            logger.debug(f"从{file}获取到TMDB ID: {tmdbid}")
                            return tmdbid
                    except Exception as e:
                        logger.debug(f"读取NFO文件 {file} 失败: {str(e)}")
                        continue
            
            # 从文件名解析
            meta_info = self.meta_helper.get_media_info(
                title=os.path.basename(dir_path),
                mtype=MediaType.TV
            )
            if meta_info and meta_info.tmdb_id:
                logger.debug(f"从文件名解析到TMDB ID: {meta_info.tmdb_id}")
                return meta_info.tmdb_id
            
        except Exception as e:
            logger.debug(f"获取TMDB ID失败: {str(e)}")
        
        return None

    @staticmethod
    def __get_tmdbid_from_nfo(file_path: Path):
        """
        从nfo文件中获取信息
        :param file_path:
        :return: tmdbid
        """
        if not file_path:
            return None
        xpaths = [
            "uniqueid[@type='Tmdb']",
            "uniqueid[@type='tmdb']",
            "uniqueid[@type='TMDB']",
            "tmdbid"
        ]
        try:
            reader = NfoReader(file_path)
            for xpath in xpaths:
                tmdbid = reader.get_element_value(xpath)
                if tmdbid:
                    return tmdbid
        except Exception as err:
            logger.warn(f"从nfo文件中获取tmdbid失败：{str(err)}")
        return None

    @eventmanager.register(EventType.PluginAction)
    def check_and_move(self, event: Event = None):
        """
        定时任务
        """
        if not self._enabled:
            return
        
        if not self._paths:
            return

        logger.info(f"开始归档任务 [{'测试模式' if self._test_mode else '正常模式'}]")
        
        # 处理每一对目录配置
        for path_pair in self._paths.splitlines():
            if not path_pair.strip():
                continue
            
            try:
                airing_dir, ended_dir = path_pair.split(':')
                airing_dir = airing_dir.strip()
                ended_dir = ended_dir.strip()
                
                # 连载 -> 完结
                logger.info(f"检查连载->完结: {airing_dir} -> {ended_dir}")
                self.__process_directory(airing_dir, ended_dir, check_ended=True)
                
                # 完结 -> 连载（如果启用双向监控）
                if self._bidirectional:
                    logger.info(f"检查完结->连载: {ended_dir} -> {airing_dir}")
                    self.__process_directory(ended_dir, airing_dir, check_ended=False)
                
            except Exception as e:
                logger.error(f"处理目录出错: {str(e)}")
                self.__send_notification(
                    title="番剧归档 - 错误",
                    text=f"理目录时出错: {str(e)}"
                )

    def get_state(self) -> bool:
        return self._enabled
    
    def get_command(self) -> List[Dict[str, Any]]:
        return []

    def stop_service(self):
        pass

    def __update_config(self):
        """
        更新配置
        """
        self.update_config({
            "enabled": self._enabled,
            "onlyonce": self._onlyonce,
            "test_mode": self._test_mode,
            "notify": self._notify,
            "bidirectional": self._bidirectional,
            "cron": self._cron,
            "paths": self._paths,
            "end_after_days": self._end_after_days  # 添加新配置项
        })

    def get_page(self) -> List[dict]:
        """
        插件页面
        """
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        """
        返回API接口配置
        """
        return []
